"""Respostas do usuário aos saques/depósitos em espécie (db/open_finance_cash.py):
desfazer, responder pergunta, listar pendências. Cada uma trava `accounts` antes
(`_lock_user`), na mesma ordem do reconciliador."""
from utils_date import _tz

from .connection import get_conn
from .open_finance_cash import PERGUNTAS, RESPOSTAS, _credita, _muda_status
from .open_finance_cash_revisao import casa_manual

# Aviso = crédito/débito na Carteira que o usuário ainda não viu. Fonte única da
# lista (`list_pending`) e do contador (`cash_transfer_summary`).
AVISO_SQL = "k.status='ativo' and k.launch_id is not null and k.seen_at is null"


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
        result = {"ok": True, "changed": resposta in respostas(link)}
        if result["changed"] and resposta == "seen":
            cur.execute("update of_cash_links set seen_at=now() where id=%s and user_id=%s", (link_id, user_id))
        elif result["changed"] and resposta == "same":
            # Revalida na hora: o manual editado depois da pergunta não casa mais, e
            # "é o mesmo" sumiria com a diferença. Fica pendente; o sync reavalia.
            casa = casa_manual(cur, user_id, link, link["manual_launch_id"])
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
        if result["changed"] and resposta != "seen":  # responder já é ver: o crédito da resposta não vira aviso
            cur.execute("update of_cash_links set seen_at=now() where id=%s and user_id=%s", (link_id, user_id))
        conn.commit()
    return result


def respostas(link) -> set:
    """"Não era dinheiro vivo" vale em toda pergunta de depósito e de Pix Saque
    (decisão do dono); o saque (`operationType=SAQUE`) é sempre dinheiro. O Ok
    só vale no aviso (visto uma vez: o 2º toque é no-op)."""
    if link["status"] == "ativo" and (link["seen_at"] or not link["launch_id"]):
        return set()
    validas = RESPOSTAS.get(link["status"], set())
    if link["status"] in PERGUNTAS and link["kind"] != "saque":
        return validas | {"not_cash"}
    return validas


def list_pending(user_id) -> list[dict]:
    """Perguntas abertas e avisos não vistos. `manual_date` é a data no fuso do app."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"""select k.id, k.kind, k.status, k.amount, k.tx_date, c.institution_name as institution,
                               m.alvo as manual_alvo, m.valor as manual_valor, m.criado_em as manual_date
                          from of_cash_links k
                          left join launches m on m.id = k.manual_launch_id and m.user_id = k.user_id
                          left join open_finance_transactions t on t.id = k.of_transaction_id
                          left join open_finance_accounts a on a.id = t.account_id
                          left join open_finance_connections c on c.id = a.connection_id and c.user_id = k.user_id
                         where k.user_id=%s and (k.status = any(%s) or {AVISO_SQL})
                         order by k.tx_date desc, k.id desc""", (user_id, list(PERGUNTAS)))
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        if r["manual_date"]:
            r["manual_date"] = r["manual_date"].astimezone(_tz()).date()
    return rows


def cash_transfer_summary(user_id) -> dict:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"""select count(*) filter (where k.status = any(%s)) as pending_count,
                               count(*) filter (where {AVISO_SQL}) as unseen_count
                          from of_cash_links k where k.user_id=%s""", (list(PERGUNTAS), user_id))
        row = cur.fetchone()
    return {"pending_count": int(row["pending_count"]), "unseen_count": int(row["unseen_count"])}
