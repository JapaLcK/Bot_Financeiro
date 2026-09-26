"""Saque e depósito em espécie vindos do Open Finance movem a Carteira (Q41).

- Kill switch `OF_CASH_ENABLED`, desligado por padrão e lido a cada chamada.
- Escrita sob `_lock_user`, na transação de quem chama, com SQL inline: nunca
  `add_launch_and_update_balance`/`delete_launch_and_rollback` (conexão própria
  esperando a linha de `accounts` que o sync já travou = autodeadlock).
- Chave (conta, providerId). Quem impede crédito em dobro depois de reconectar
  é a janela de cobertura, não a chave.
- `ativo` com `launch_id` nulo = desfeito (apagar #N zera pelo `set null`).
"""
import hashlib
import os
import re
from datetime import datetime, time
from decimal import Decimal

from psycopg.types.json import Jsonb

from utils_date import _tz
from .connection import get_conn

PERGUNTAS = ("perguntar_manual", "perguntar_novo", "perguntar_fraco")
# Nestes estados o lado do banco fica fora dos relatórios (sombra interna) e
# fora das declarações bancárias (db/bank_movements.py).
INTERNOS = ("ativo", "desfeito") + PERGUNTAS
RESPOSTAS = {"perguntar_manual": {"same", "different"}, "perguntar_novo": {"already", "credit"},
             "perguntar_fraco": {"cash", "not_cash"}, "ativo": {"seen"}}


def enabled() -> bool:
    return (os.getenv("OF_CASH_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def cash_kind(account_type, amount, raw, description) -> str | None:
    """'saque' credita sozinho; 'deposito' e 'fraco' (sinal fraco de saque) só perguntam."""
    from .open_finance import _normalize_merchant
    op = str((raw or {}).get("operationType") or "").upper()
    v, tipo = Decimal(str(amount or 0)), (account_type or "").upper()
    saque = "saque" in _normalize_merchant(description)
    if op == "TARIFA_SERVICOS_AVULSOS":  # tarifa de saque continua gasto
        return None
    if tipo == "BANK" and v < 0 and op == "SAQUE":
        return "saque"
    if tipo == "BANK" and v > 0 and op == "DEPOSITO":
        return "deposito"
    if saque and (tipo == "BANK" and v < 0 and op == "PIX" or tipo == "CREDIT" and v != 0):
        return "fraco"
    return None


def _sha(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def account_key(account_type, account_raw, institution_name) -> str:
    """Número da conta; sem ele, o nome da instituição. Nunca id de conexão."""
    from .open_finance import _normalize_merchant
    tipo = (account_type or "").upper()
    numero = re.sub(r"\D", "", str((account_raw or {}).get("number") or ""))
    return _sha(tipo, "n", numero) if numero else _sha(tipo, "i", _normalize_merchant(institution_name))


def tx_key(acc_key, raw, provider_transaction_id) -> tuple[str, bool]:
    """(chave, durável). Sem providerId cai no id da Pluggy, que não sobrevive a reconectar."""
    pid = str((raw or {}).get("providerId") or "").strip()
    return (_sha(acc_key, "p", pid), True) if pid else (_sha(acc_key, "id", provider_transaction_id), False)


def _activation(cur):
    """Gravada na 1ª rodada com o switch ligado: tudo antes dela é 'historico'."""
    cur.execute("insert into of_cash_activation default values on conflict do nothing")
    cur.execute("select activated_at from of_cash_activation")
    return cur.fetchone()["activated_at"]


def _quando(tx_date, transacted_at=None):
    return ((transacted_at, True) if transacted_at is not None
            else (datetime.combine(tx_date, time(12, 0), tzinfo=_tz()), False))


def _credita(cur, user_id, link, transacted_at=None) -> int:
    v = Decimal(str(link["amount"]))
    deposito = link["kind"] == "deposito"
    delta = -v if deposito else v
    criado_em, known = _quando(link["tx_date"], transacted_at)
    cur.execute("update accounts set balance = balance + %s where user_id=%s", (delta, user_id))
    cur.execute("""insert into launches(user_id, tipo, valor, alvo, categoria, criado_em, efeitos,
                   is_internal_movement) values (%s,%s,%s,%s,'transferencia_interna',%s,%s,true)
                   returning id""",
                (user_id, "despesa" if deposito else "receita", v,
                 "Depósito em dinheiro" if deposito else "Saque em dinheiro", criado_em,
                 Jsonb({"delta_conta": float(delta), "time_known": known})))
    launch_id = cur.fetchone()["id"]
    cur.execute("""update of_cash_links set status='ativo', origem='auto', launch_id=%s,
                   updated_at=now() where id=%s and user_id=%s""", (launch_id, link["id"], user_id))
    return launch_id


def _solta(cur, user_id, link) -> None:
    """Devolve a Carteira: apaga o automático (revertendo o delta) ou devolve o
    manual casado a lançamento comum. Chamador já gravou o status novo."""
    lid = link["launch_id"]
    if not lid:
        return
    if link["origem"] == "manual":
        cur.execute("update launches set is_internal_movement=false where id=%s and user_id=%s", (lid, user_id))
        return
    from .accounts import _validar_efeitos
    cur.execute("select efeitos from launches where id=%s and user_id=%s for update", (lid, user_id))
    row = cur.fetchone()
    if row:
        delta = _validar_efeitos(row["efeitos"] or {}, escopo_conta_corrente=False)
        cur.execute("update accounts set balance = balance - %s where user_id=%s", (delta, user_id))
        cur.execute("delete from launches where id=%s and user_id=%s", (lid, user_id))


def _muda_status(cur, user_id, link, status) -> None:
    """Grava o status ANTES de soltar o lançamento (o `set null` do delete não
    pode ser o que decide o estado final)."""
    cur.execute("update of_cash_links set status=%s, launch_id=null, updated_at=now() "
                "where id=%s and user_id=%s", (status, link["id"], user_id))
    if link["status"] == "ativo":
        _solta(cur, user_id, link)
    link.update(status=status, launch_id=None)


def _corrige(cur, user_id, link, t) -> int:
    """O banco corrigiu valor/data de um saque já creditado: segue o banco."""
    v = abs(Decimal(str(t["amount"])))
    if v == Decimal(str(link["amount"])) and t["transaction_date"] == link["tx_date"]:
        return 0
    novo = -v if link["kind"] == "deposito" else v
    criado_em, known = _quando(t["transaction_date"], t["transacted_at"])
    # O delta velho vem do PRÓPRIO lançamento (o `returning` lê a linha antes do update).
    cur.execute("""update launches l set valor=%s, criado_em=%s, efeitos = l.efeitos || %s
                     from launches o where o.id = l.id and l.id=%s and l.user_id=%s
                   returning (o.efeitos->>'delta_conta')::numeric as velho""",
                (v, criado_em, Jsonb({"delta_conta": float(novo), "time_known": known}),
                 link["launch_id"], user_id))
    if not (row := cur.fetchone()):
        return 0
    cur.execute("update accounts set balance = balance + %s where user_id=%s", (novo - row["velho"], user_id))
    cur.execute("update of_cash_links set amount=%s, tx_date=%s, updated_at=now() where id=%s and user_id=%s",
                (v, t["transaction_date"], link["id"], user_id))
    return 1


def _revisa(cur, user_id, link, t, kind) -> int:
    """Transação que já tem vínculo pela chave: religa, corrige ou estorna."""
    if link["of_transaction_id"] is None:  # reconexão: mesma transação, espelho novo
        link["of_transaction_id"] = t["id"]
        cur.execute("update of_cash_links set of_transaction_id=%s, updated_at=now() where id=%s "
                    "and user_id=%s", (t["id"], link["id"], user_id))
    elif link["of_transaction_id"] != t["id"]:
        return 0  # a mesma transação vista por outra conexão viva
    if link["status"] not in INTERNOS:
        return 0
    if kind != link["kind"]:
        _muda_status(cur, user_id, link, "estornado")
        return 1
    if link["status"] == "ativo" and link["launch_id"] and link["origem"] == "auto":
        return _corrige(cur, user_id, link, t)
    return 0


def _candidato_manual(cur, user_id, links, kind, t) -> int | None:
    """Receita (saque) / despesa (depósito) manual da Carteira, mesmo valor, ±3 dias."""
    from .open_finance import _find_manual_candidates, pick_reconciliation_match
    tipo = "despesa" if kind == "deposito" else "receita"
    valor = abs(Decimal(str(t["amount"])))
    usados = {i for k in links.values() for i in (k["launch_id"], k["manual_launch_id"]) if i}
    cands = [c for c in _find_manual_candidates(cur, user_id, tipo, valor, t["transaction_date"])
             if c["source"] == "manual" and c["id"] not in usados and c["delta_conta"] is not None
             and (c["delta_conta"] < 0 if tipo == "despesa" else c["delta_conta"] > 0)]
    return pick_reconciliation_match(valor, t["transaction_date"], t["description"], cands)["launch_id"]


def reconcile_cash_transfers(cur, user_id) -> int:
    """Roda dentro da transação do sync. Devolve quantos vínculos mudaram."""
    if not enabled():
        return 0
    from .bank_movements import _lock_user
    _lock_user(cur, user_id)
    ativacao = _activation(cur)
    # ponytail: varre as transações do usuário todo sync (≤1837/conexão); filtrar no SQL se pesar.
    cur.execute("""select t.*, a.type as account_type, a.raw as account_raw, c.id as connection_id,
                          c.institution_name, c.created_at as connected_at
                     from open_finance_transactions t
                     join open_finance_accounts a on a.id = t.account_id
                     join open_finance_connections c on c.id = a.connection_id
                    where c.user_id = %s order by t.transaction_date, t.id""", (user_id,))
    txs = [dict(r) for r in cur.fetchall()]
    cur.execute("select * from of_cash_links where user_id=%s for update", (user_id,))
    links = {r["tx_key"]: dict(r) for r in cur.fetchall()}
    cur.execute("select * from of_cash_coverage where user_id=%s", (user_id,))
    primeira, janelas = {}, []
    for c in cur.fetchall():
        primeira[c["account_key"]] = min(primeira.get(c["account_key"], c["connected_at"]), c["connected_at"])
        janelas.append((c["account_key"], None, c["covered_from"], c["covered_until"]))
    vivas = {}
    for t in txs:
        k = t["akey"] = account_key(t["account_type"], t["account_raw"], t["institution_name"])
        primeira[k] = min(primeira.get(k, t["connected_at"]), t["connected_at"])
        lo, hi = vivas.get((k, t["connection_id"]), (t["transaction_date"],) * 2)
        vivas[(k, t["connection_id"])] = (min(lo, t["transaction_date"]), max(hi, t["transaction_date"]))
    janelas += [(k, cid, lo, hi) for (k, cid), (lo, hi) in vivas.items()]
    mudou = 0
    for t in txs:
        kind = (None if t["reconciliation_status"] == "bank_movement_confirmed"
                else cash_kind(t["account_type"], t["amount"], t["raw"], t["description"]))
        chave, duravel = tx_key(t["akey"], t["raw"], t["provider_transaction_id"])
        if chave in links:
            mudou += _revisa(cur, user_id, links[chave], t, kind)
            continue
        if kind is None:
            continue
        dia, manual = t["transaction_date"], None
        corte = max(ativacao, primeira[t["akey"]]).astimezone(_tz()).date()
        if dia < corte:
            status = "historico"
        elif (manual := _candidato_manual(cur, user_id, links, kind, t)) is not None:
            status = "perguntar_manual"
        elif not duravel or any(k == t["akey"] and cid != t["connection_id"] and lo and hi and lo <= dia <= hi
                                for k, cid, lo, hi in janelas):
            status = "perguntar_novo"
        elif kind != "saque":
            status = "perguntar_fraco"
        else:
            status = "ativo"
        cur.execute("""insert into of_cash_links(user_id, tx_key, key_durable, account_key, kind,
                         status, manual_launch_id, of_transaction_id, amount, tx_date)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       on conflict (user_id, tx_key) do nothing returning *""",
                    (user_id, chave, duravel, t["akey"], kind, status, manual, t["id"],
                     abs(Decimal(str(t["amount"]))), dia))
        if not (novo := cur.fetchone()):
            continue
        links[chave] = dict(novo)
        if status == "ativo":  # só quem inseriu credita
            links[chave]["launch_id"] = _credita(cur, user_id, novo, t["transacted_at"])
        mudou += 1
    return mudou


def cash_internal_tx_ids(cur, user_id) -> set:
    """Transações OF cujo lado do banco fica fora dos relatórios. Os três sites
    que classificam a sombra (import, correção, desfazer fusão) consultam isto."""
    if not enabled():
        return set()
    cur.execute("select of_transaction_id from of_cash_links where user_id=%s "
                "and of_transaction_id is not null and status = any(%s)", (user_id, list(INTERNOS)))
    return {r["of_transaction_id"] for r in cur.fetchall()}


def estorna_links(cur, user_id, of_tx_ids) -> int:
    """O banco apagou a transação: estorna na mesma transação do delete. Sem o
    switch de propósito — crédito já feito tem de voltar mesmo com ele desligado."""
    cur.execute("select * from of_cash_links where user_id=%s and of_transaction_id = any(%s) "
                "and status = any(%s) for update", (user_id, list(of_tx_ids), list(INTERNOS)))
    rows = [dict(r) for r in cur.fetchall()]
    for link in rows:
        _muda_status(cur, user_id, link, "estornado")
    return len(rows)


def record_coverage(cur, user_id, connection_id=None) -> None:
    """Antes do delete da conexão: a janela que cada conta dela já cobriu."""
    if not enabled():
        return
    cur.execute("""select a.type, a.raw, c.institution_name, c.created_at,
                          min(t.transaction_date) as lo, max(t.transaction_date) as hi
                     from open_finance_accounts a
                     join open_finance_connections c on c.id = a.connection_id
                     left join open_finance_transactions t on t.account_id = a.id
                    where c.user_id=%s and (%s::bigint is null or c.id=%s) group by a.id, c.id""",
                (user_id, connection_id, connection_id))
    for r in cur.fetchall():
        cur.execute("insert into of_cash_coverage(user_id, account_key, connected_at, covered_from, "
                    "covered_until) values (%s,%s,%s,%s,%s)",
                    (user_id, account_key(r["type"], r["raw"], r["institution_name"]),
                     r["created_at"], r["lo"], r["hi"]))


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
            cur.execute("update launches set is_internal_movement=true where id=%s and user_id=%s "
                        "and not is_internal_movement", (link["manual_launch_id"], user_id))
            if cur.rowcount != 1:
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
