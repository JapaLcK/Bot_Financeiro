"""
db/bancos.py — `GET /api/v2/bancos`: o que cada banco conectado vale, saldo em conta + investido.

Nenhuma regra de dinheiro nova: o saldo é o de `db/contas_hoje.py` (`no_total`) e o investido é o de
`db/investido.py` (`por_conexao`), lidos no MESMO cursor (repeatable read, só leitura) para o topo
fechar com os bancos. Uma entrada por CONEXÃO (duas "Nubank" são duas), pelo id local. O app não soma.

`None` = não se sabe, nunca zero. Conexão sem conta, cartão nem posição não entra em `bancos` (nem no `motivos`
e no `total` do topo, que saem só dos bancos listados). Carteira manual, cartão e fatura ficam fora dos
valores; o cartão só entra como contagem. Só lê; toda consulta filtra por `user_id`.
"""
from __future__ import annotations

from decimal import Decimal

from core.services.cashflow_contract import somar

from . import contas_hoje, investido
from .connection import get_conn
from .patrimonio import ler_conexoes

MOTIVOS = (*contas_hoje.MOTIVOS_CONTA, "plano_sem_investimentos")

# Cartões, um por identidade do provedor (a da conexão mais nova): o molde de `CONTAS_BANCO_SQL`.
CARTOES_SQL = """
    select connection_id, count(*) as n from (
        select distinct on (a.provider_account_id) a.connection_id
        from open_finance_accounts a
        join open_finance_connections c on c.id = a.connection_id
        where c.user_id=%s and upper(a.type) = 'CREDIT'
        order by a.provider_account_id, c.id desc
    ) x group by connection_id
"""


def _soma(*partes: Decimal | None) -> Decimal | None:
    vistas = [p for p in partes if p is not None]
    return somar(vistas) if vistas else None


def _ordem(motivos: set) -> list[str]:
    return [m for m in MOTIVOS if m in motivos]


def montar(cur, user_id: int, investimentos: bool = True) -> dict:
    """`investimentos=False` (plano sem o recurso): `investido` e `investimentos` saem `None`, com o
    motivo `plano_sem_investimentos`, e o saldo segue (o precedente de `db/metas.py`)."""
    contas = contas_hoje.listar(cur, user_id)["contas"]
    inv = investido.calcular(cur, user_id, por_conexao=True)["por_conexao"] if investimentos else {}
    cur.execute(CARTOES_SQL, (user_id,))
    cartoes = {r["connection_id"]: r["n"] for r in cur.fetchall()}

    bancos = []
    for c in ler_conexoes(cur, user_id)[0]:
        cid = c["id"]
        dela = [x for x in contas if x["conexao_id"] == cid]
        entram = [x["saldo"] for x in dela if x["no_total"]]
        saldo = _soma(*entram)
        i = inv.get(cid)
        if not (dela or cartoes.get(cid) or (i and i["posicoes"])):
            continue  # sem nenhuma linha no espelho (DELETED, reconexão que migrou, recém-conectada) não é banco
        valor = i["valor"] if i else None
        motivos = {m for x in dela for m in x["motivos"]} | set(i["motivos"] if i else ())
        if not investimentos:
            motivos.add("plano_sem_investimentos")
        bancos.append({"conexao_id": cid, "saldo_conta": saldo, "investido": valor,
                       "total": _soma(saldo, valor), "contas": len(dela), "cartoes": cartoes.get(cid, 0),
                       "investimentos": i["posicoes"] if i else None, "motivos": _ordem(motivos)})
    return {"total": _soma(*(b["total"] for b in bancos)),
            "motivos": _ordem({m for b in bancos for m in b["motivos"]}), "bancos": bancos}


def ler(user_id: int, investimentos: bool = True) -> dict:
    """`montar` num snapshot só, como `GET /api/v2/contas`."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        dados = montar(cur, user_id, investimentos)
        conn.rollback()
    return dados
