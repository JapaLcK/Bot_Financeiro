"""Respostas do usuário aos saques/depósitos em espécie (db/open_finance_cash.py):
desfazer, responder pergunta, listar pendências. Cada uma trava `accounts` antes
(`_lock_user`), na mesma ordem do reconciliador."""
from .connection import get_conn
from .open_finance_cash import PERGUNTAS, RESPOSTAS, _credita, _muda_status
from .open_finance_cash_revisao import casa_manual


def _link_travado(cur, user_id, link_id) -> dict:
    from .bank_movements import _lock_user
    _lock_user(cur, user_id)
    cur.execute("select * from of_cash_links where id=%s and user_id=%s for update", (link_id, user_id))
    if not (link := cur.fetchone()):
        raise LookupError("CASH_LINK_NOT_FOUND")
    return dict(link)


def undo_link(user_id, link_id) -> dict:
    """Desfazer: só a Carteira volta; a saída do banco segue fora dos relatórios."""
    with get_conn() as conn, conn.cursor() as cur:
        link = _link_travado(cur, user_id, link_id)
        changed = link["status"] == "ativo" and bool(link["launch_id"])
        if changed:
            _muda_status(cur, user_id, link, "desfeito")
        conn.commit()
    return {"ok": True, "changed": changed}


def answer_link(user_id, link_id, resposta) -> dict:
    """Resposta a uma pergunta (ou 'seen' num aviso). Fora do estado atual é
    no-op idempotente (botão tocado duas vezes)."""
    if not any(resposta in r for r in RESPOSTAS.values()):
        raise ValueError("CASH_ANSWER_INVALID")
    with get_conn() as conn, conn.cursor() as cur:
        link = _link_travado(cur, user_id, link_id)
        result = {"ok": True, "changed": resposta in RESPOSTAS.get(link["status"], ())}
        if result["changed"] and resposta == "seen":
            cur.execute("update of_cash_links set seen_at=now() where id=%s and user_id=%s", (link_id, user_id))
        elif result["changed"] and resposta == "same":
            # Revalida na hora: o manual editado depois da pergunta não casa mais, e
            # "é o mesmo" sumiria com a diferença. Fica pendente; o sync reavalia.
            casa = casa_manual(cur, user_id, link["manual_launch_id"], link["amount"], link["tx_date"])
            if casa:
                cur.execute("update launches set is_internal_movement=true where id=%s and user_id=%s "
                            "and not is_internal_movement", (link["manual_launch_id"], user_id))
            if not casa or cur.rowcount != 1:
                result = {"ok": False, "changed": False, "reason": "MANUAL_NOT_AVAILABLE"}
            else:
                cur.execute("update of_cash_links set status='ativo', origem='manual', launch_id=%s, "
                            "updated_at=now() where id=%s and user_id=%s",
                            (link["manual_launch_id"], link_id, user_id))
        elif result["changed"] and resposta in ("already", "not_cash"):
            _muda_status(cur, user_id, link, "desfeito" if resposta == "already" else "nao_dinheiro")
        elif result["changed"]:  # different, credit, cash
            _credita(cur, user_id, link)
        conn.commit()
    return result


def list_pending(user_id) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""select k.id, k.kind, k.status, k.amount, k.tx_date, k.manual_launch_id,
                              m.alvo as manual_alvo, m.valor as manual_valor
                         from of_cash_links k
                         left join launches m on m.id = k.manual_launch_id and m.user_id = k.user_id
                        where k.user_id=%s and k.status = any(%s)
                        order by k.tx_date desc, k.id desc""", (user_id, list(PERGUNTAS)))
        return [dict(r) for r in cur.fetchall()]


def cash_transfer_summary(user_id) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""select count(*) filter (where status = any(%s)) as pending_count, count(*) filter
                         (where status='ativo' and launch_id is not null and seen_at is null) as unseen_count
                         from of_cash_links where user_id=%s""", (list(PERGUNTAS), user_id))
        row = cur.fetchone()
    return {"pending_count": int(row["pending_count"]), "unseen_count": int(row["unseen_count"])}
