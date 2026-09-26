"""Saque e depósito em espécie vindos do Open Finance movem a Carteira (Q41).

- Kill switch `OF_CASH_ENABLED`, desligado por padrão e lido a cada chamada. Ele
  só impede vínculo NOVO: o que já existe segue o banco e segue interno.
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

PERGUNTAS = ("perguntar_manual", "perguntar_novo", "perguntar_fraco")
# Nestes estados o lado do banco fica fora dos relatórios (sombra interna) e
# fora das declarações bancárias (db/bank_movements.py).
INTERNOS = ("ativo", "desfeito") + PERGUNTAS
RESPOSTAS = {"perguntar_manual": {"same", "different"}, "perguntar_novo": {"already", "credit"},
             "perguntar_fraco": {"cash", "not_cash"}, "ativo": {"seen"}}
# Fonte única para db/accounts.py: o par ativo segue interno com qualquer categoria;
# lançamento com vínculo trava `accounts` antes de apagar (a ordem do reconciliador).
PAR_ATIVO_SQL = ("exists (select 1 from of_cash_links k where k.launch_id = launches.id "
                 "and k.user_id = launches.user_id and k.status = 'ativo')")
VINCULADO_SQL = ("exists (select 1 from of_cash_links k where k.user_id = launches.user_id "
                 "and launches.id in (k.launch_id, k.manual_launch_id))")


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


def kind_atual(t) -> str | None:
    """O tipo que o banco diz AGORA. Declaração bancária confirmada nunca é dinheiro."""
    if t["reconciliation_status"] == "bank_movement_confirmed":
        return None
    return cash_kind(t["account_type"], t["amount"], t["raw"], t["description"])


def _sha(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def account_key(account_type, account_raw, institution_name) -> str:
    """Instituição (nome normalizado) + número da conta. Nunca id de conexão nem de
    connector: o mesmo banco vem por connectors diferentes com o mesmo nome."""
    from .open_finance import _normalize_merchant
    tipo, nome = (account_type or "").upper(), _normalize_merchant(institution_name)
    numero = re.sub(r"\D", "", str((account_raw or {}).get("number") or ""))
    return _sha(tipo, nome, "n", numero) if numero else _sha(tipo, "i", nome)


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


def _do_usuario(link) -> bool:
    """Desfazer, "apagar #N", "já anotei" e "não era dinheiro": o banco não reabre."""
    return link["status"] in ("desfeito", "nao_dinheiro") or (link["status"] == "ativo" and not link["launch_id"])


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
    ligado = enabled()
    if not ligado:
        cur.execute("select 1 from of_cash_links where user_id=%s limit 1", (user_id,))
        if not cur.fetchone():
            return 0
    from .bank_movements import _lock_user
    from .open_finance_cash_revisao import _revisa
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
    por_tx = {k["of_transaction_id"]: k for k in links.values() if k["of_transaction_id"]}
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

    def decide(t, kind, duravel):
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
        return status, manual

    mudou = 0
    for t in txs:
        kind = kind_atual(t)
        chave, duravel = tx_key(t["akey"], t["raw"], t["provider_transaction_id"])
        # Pela transação local primeiro: nome da instituição e número da conta mudam
        # na conexão viva (upsert do item) e, pela chave só, virariam crédito em dobro.
        if link := por_tx.get(t["id"]) or links.get(chave):
            if link["tx_key"] != chave and chave not in links:  # a chave segue o banco (religa ao reconectar)
                cur.execute("update of_cash_links set tx_key=%s, account_key=%s, key_durable=%s, "
                            "updated_at=now() where id=%s and user_id=%s",
                            (chave, t["akey"], duravel, link["id"], user_id))
                links[chave] = links.pop(link["tx_key"])
                link.update(tx_key=chave, account_key=t["akey"], key_durable=duravel)
            n, reavaliar = _revisa(cur, user_id, link, t, kind)
            mudou += n
            if reavaliar:
                status, manual = decide(t, kind, link["key_durable"])
                link.update(status=status, origem="auto", manual_launch_id=manual,
                            amount=abs(Decimal(str(t["amount"]))), tx_date=t["transaction_date"])
                cur.execute("update of_cash_links set status=%s, origem='auto', manual_launch_id=%s, kind=%s, "
                            "amount=%s, tx_date=%s, updated_at=now() where id=%s and user_id=%s",
                            (status, manual, kind, link["amount"], link["tx_date"], link["id"], user_id))
                if status == "ativo":
                    link["launch_id"] = _credita(cur, user_id, link, t["transacted_at"])
                mudou += 1
            continue
        if kind is None or not ligado:
            continue
        status, manual = decide(t, kind, duravel)
        dia = t["transaction_date"]
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
    que classificam a sombra (import, correção, desfazer fusão) consultam isto.
    Sem o switch de propósito: desligar não reinterpreta o que já foi gravado.
    Decisão do usuário (desfeito) só esconde o lado do banco enquanto o banco
    disser que é dinheiro: virou compra, conta como compra — sem recreditar."""
    cur.execute("""select k.of_transaction_id, k.status, k.launch_id, t.amount, t.raw, t.description,
                          t.reconciliation_status, a.type as account_type
                     from of_cash_links k
                     join open_finance_transactions t on t.id = k.of_transaction_id
                     join open_finance_accounts a on a.id = t.account_id
                     join open_finance_connections c on c.id = a.connection_id and c.user_id = k.user_id
                    where k.user_id=%s and k.status = any(%s)""", (user_id, list(INTERNOS)))
    return {r["of_transaction_id"] for r in cur.fetchall() if not _do_usuario(r) or kind_atual(r)}


def estorna_links(cur, user_id, of_tx_ids) -> int:
    """O banco apagou a transação: estorna na mesma transação do delete. Sem o
    switch de propósito — crédito já feito tem de voltar mesmo com ele desligado."""
    cur.execute("select * from of_cash_links where user_id=%s and of_transaction_id = any(%s) "
                "and status = any(%s) for update", (user_id, list(of_tx_ids), list(INTERNOS)))
    rows = [dict(r) for r in cur.fetchall() if not _do_usuario(r)]
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
