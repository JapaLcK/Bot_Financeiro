"""Vínculo de saque/depósito em espécie (db/open_finance_cash.py) cuja transação
o banco mudou: corrige valor/data, desfaz o casamento com o manual que deixou de
casar, estorna o que deixou de ser dinheiro e reabre o que o próprio banco
encerrou (estornado, historico). Decisão do usuário não reabre (`_do_usuario`)."""
from decimal import Decimal

from psycopg.types.json import Jsonb

from .open_finance_cash import INTERNOS, _do_usuario, _muda_status, _quando


def _corrige(cur, user_id, link, t) -> int:
    """O banco corrigiu valor/data: o vínculo segue o banco (a pergunta pendente
    credita o valor corrente) e o automático já creditado também."""
    v = abs(Decimal(str(t["amount"])))
    if v == Decimal(str(link["amount"])) and t["transaction_date"] == link["tx_date"]:
        return 0
    if link["status"] == "ativo" and link["launch_id"] and link["origem"] == "auto":
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
    link.update(amount=v, tx_date=t["transaction_date"])
    return 1


def casa_manual(cur, user_id, manual_id, valor, dia) -> bool:
    """O manual ainda casa com o banco? Mesma tolerância do casamento
    (`pick_reconciliation_match`: valor e ±dias). Manual apagado não casa."""
    from .open_finance import pick_reconciliation_match
    cur.execute("select id, valor, coalesce(posted_at, criado_em::date) as ref_date from launches "
                "where id=%s and user_id=%s", (manual_id, user_id))
    row = cur.fetchone()
    return bool(row) and pick_reconciliation_match(valor, dia, "", [dict(row)])["launch_id"] is not None


def _descasa(cur, user_id, link, corrigiu) -> bool:
    """Casamento com o manual que deixou de casar (o banco corrigiu, ou o manual
    mudou/sumiu): desfaz — senão o "é o mesmo" some com a diferença. A pergunta
    é revalidada toda rodada; o 'ativo' manual já respondido, só quando o banco
    corrige (editar o manual depois do "é o mesmo" é escolha do usuário)."""
    if link["status"] == "perguntar_manual":
        manual = link["manual_launch_id"]
    elif corrigiu and link["status"] == "ativo" and link["origem"] == "manual" and link["launch_id"]:
        manual = link["launch_id"]
    else:
        return False
    if casa_manual(cur, user_id, manual, link["amount"], link["tx_date"]):
        return False
    if link["status"] == "ativo":
        _muda_status(cur, user_id, link, "desfeito")  # devolve o manual a lançamento comum
    return True


def _revisa(cur, user_id, link, t, kind) -> tuple[int, bool]:
    """Transação que já tem vínculo: religa, corrige ou estorna. Devolve
    (mudou, reavaliar) — reavaliar = volta à decisão (o manual não casa mais, ou
    o banco mudou de novo um estado que ele mesmo encerrou)."""
    if link["of_transaction_id"] is None:  # reconexão: mesma transação, espelho novo
        link["of_transaction_id"] = t["id"]
        cur.execute("update of_cash_links set of_transaction_id=%s, updated_at=now() where id=%s "
                    "and user_id=%s", (t["id"], link["id"], user_id))
    elif link["of_transaction_id"] != t["id"]:
        return 0, False  # a mesma transação vista por outra conexão viva
    if _do_usuario(link):  # só acompanha valor/data; o status não muda
        return _corrige(cur, user_id, link, t), False
    mudou = 0
    if link["status"] in INTERNOS and kind != link["kind"]:
        _muda_status(cur, user_id, link, "estornado")
        mudou = 1
    if link["status"] in ("estornado", "historico"):
        # Estornado reabre quando o banco volta a dizer dinheiro; histórico, quando
        # muda data ou tipo (a decisão diz se ainda é antes do corte).
        mesma = (kind, t["transaction_date"]) == (link["kind"], link["tx_date"])
        if kind is None or link["status"] == "historico" and mesma:
            return mudou, False
        link.update(kind=kind, manual_launch_id=None)  # o manual solto pelo estorno volta a ser candidato
        return 1, True
    corrigiu = _corrige(cur, user_id, link, t)
    return corrigiu, _descasa(cur, user_id, link, corrigiu)
