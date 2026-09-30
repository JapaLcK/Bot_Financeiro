"""
db/patrimonio.py — o patrimônio em reais, numa conta só (docs/plano-dashboard-v2.md §4).

`calcular` é a função que a foto diária (`gravar_foto`, pelo job de
`core/services/patrimonio_foto.py`) e a tela Patrimônio da etapa 6 usam. Só lê,
pelo cursor recebido: quem chama decide a transação.

Soma: Carteira (com a fusão devolvida, `merged_wallet_delta`) + contas do banco
(`BANK_ACCOUNTS_SQL`, o mesmo recorte do saldo consolidado) + posições de
investimento do banco em reais + caixinhas manuais (a espelhada já vem pela
posição) + investimentos manuais. Cartão fica de fora. `motivos` lista por que o
número não é exato; vazio só quando nada o põe em dúvida.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from .connection import get_conn
from .open_finance import (BANK_ACCOUNTS_SQL, PENDING_RECONCILIATION_SQL,
                           actionable_pending_params, merged_wallet_delta)
from .open_finance_state import _TERMINAL

# Posições do banco, uma por identidade do provedor (reconectar cria outra linha
# para a mesma posição): fica a da conexão mais nova, como em `BANK_ACCOUNTS_SQL`.
# O que sai da soma (conexão pausada/apagada, outra moeda, resgatada) é decidido
# em `calcular`, para ser contado em `base.fora`.
POSICOES_BANCO_SQL = """
    select distinct on (i.provider_investment_id)
        i.provider_investment_id, i.balance,
        upper(coalesce(i.currency, 'BRL')) as currency,
        upper(coalesce(i.raw->>'status', '')) as status,
        i.raw->>'currencyCode' as currency_code,
        upper(coalesce(c.status, '')) as connection_status
    from open_finance_investments i
    join open_finance_connections c on c.id = i.connection_id
    where c.user_id=%s
    order by i.provider_investment_id, c.id desc
"""

_CONTAS_SQL = f"""
    select a.balance, ra.provider_account_id, ra.raw->>'currencyCode' as currency_code
      from ({BANK_ACCOUNTS_SQL}) a
      join open_finance_accounts ra on ra.id = a.id
"""

# Sincronização mais velha que isto é banco desatualizado (decisão do dono, P3).
BANCO_VELHO = timedelta(hours=48)


def calcular(cur, user_id: int) -> dict:
    from core.services.pluggy_health import connection_ui_state
    from .bank_movements import _declarations
    from .open_finance_cash import enabled as especie_ligada

    cur.execute("select balance from accounts where user_id=%s", (user_id,))
    row = cur.fetchone()
    carteira = (row["balance"] if row else Decimal(0)) + merged_wallet_delta(cur, user_id)

    cur.execute(_CONTAS_SQL, (user_id,))
    contas = cur.fetchall()

    cur.execute(POSICOES_BANCO_SQL, (user_id,))
    fora = {"moeda": 0, "resgatada": 0, "pausada": 0}
    posicoes = []
    for p in cur.fetchall():
        if p["connection_status"] in _TERMINAL:
            fora["pausada"] += 1
        elif p["currency"] != "BRL":
            fora["moeda"] += 1
        elif p["status"] == "TOTAL_WITHDRAWAL":
            fora["resgatada"] += 1
        else:
            posicoes.append(p)

    cur.execute("select coalesce(sum(balance), 0) as s, coalesce(bool_or(balance > 0), false) as pos"
                " from pockets where user_id=%s and of_investment_id is null", (user_id,))
    caixinhas = cur.fetchone()
    # A espelhada não entra em `caixinhas`; se a posição dela ficou fora, o dinheiro
    # some do total. Não se inventa número: conta aqui e vira motivo.
    cur.execute("""select (select i.provider_investment_id from open_finance_investments i
                             join open_finance_connections c on c.id = i.connection_id
                            where i.id = p.of_investment_id and c.user_id = p.user_id) as pid
                     from pockets p
                    where p.user_id=%s and p.of_investment_id is not null and p.balance > 0""",
                (user_id,))
    contadas = {p["provider_investment_id"] for p in posicoes}
    fora["caixinha_espelhada"] = sum(r["pid"] not in contadas for r in cur.fetchall())
    cur.execute("select coalesce(sum(balance), 0) as s, coalesce(bool_or(balance > 0), false) as pos"
                " from investments where user_id=%s", (user_id,))
    manuais = cur.fetchone()

    cur.execute("select * from open_finance_connections where user_id=%s order by id", (user_id,))
    conexoes = cur.fetchall()
    estados = {str(c["id"]): connection_ui_state(c)["state"] for c in conexoes}
    vivas = [c for c in conexoes if (c["status"] or "").upper() not in _TERMINAL]
    limite = datetime.now(timezone.utc) - BANCO_VELHO

    def desatualizada(c) -> bool:
        ultimo, tentativa = c["last_sync_at"], c["last_attempt_at"]
        return (estados[str(c["id"])] != "updated" or ultimo is None or ultimo < limite
                or (tentativa is not None and tentativa > ultimo))

    cur.execute(PENDING_RECONCILIATION_SQL, actionable_pending_params(cur, user_id))
    conciliacao = cur.fetchone()["pending_count"]

    motivos = [m for m, sim in (
        ("carteira_nao_confirmada", True),  # até existir a confirmação da Q37
        ("especie_incompleta", not especie_ligada() and bool(vivas)),
        ("banco_desatualizado", any(desatualizada(c) for c in vivas)),
        ("movimentos_pendentes", any(not d["matched_transaction_id"]
                                     for d in _declarations(cur, user_id))),
        ("conciliacao_pendente", conciliacao > 0),
        ("manual_e_banco", (caixinhas["pos"] or manuais["pos"]) and bool(posicoes)),
        ("caixinha_espelhada_fora", fora["caixinha_espelhada"] > 0),
        ("moeda_presumida", any(not r["currency_code"] for r in [*contas, *posicoes])),
        ("saldo_ausente", any(p["balance"] is None for p in posicoes)),
    ) if sim]

    partes = {
        "carteira": carteira,
        "bancos": sum((r["balance"] for r in contas), Decimal(0)),
        "investimentos_banco": sum((p["balance"] or Decimal(0) for p in posicoes), Decimal(0)),
        "caixinhas": caixinhas["s"],
        "investimentos_manuais": manuais["s"],
    }
    return {
        "total": sum(partes.values(), Decimal(0)),
        **partes,
        "base": {
            "v": 1,
            "contas": sorted(r["provider_account_id"] for r in contas),
            "posicoes": sorted(p["provider_investment_id"] for p in posicoes),
            "conexoes": estados,
            "fora": fora,
        },
        "motivos": motivos,
    }


def gravar_foto(user_id: int, dia: date) -> bool:
    """Grava a foto do dia, se ainda não houver. False = não gravou.

    `for share` em `accounts` antes de ler: o "Recomeçar do zero" começa pelo
    `update accounts` (db/privacy.py, `reset_user_data`) e segura a linha até o
    commit, então os dois se serializam — nunca uma foto com metade do reset. Só
    esta linha é travada; o resto é leitura no mesmo snapshot (repeatable read),
    sem lock que feche ciclo com os escritores. Reset que commitou depois do
    snapshot faz o `for share` levantar SerializationFailure: não grava, e a
    volta seguinte do job tenta de novo."""
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("set transaction isolation level repeatable read")
            cur.execute("select 1 from accounts where user_id=%s for share", (user_id,))
            if cur.fetchone() is None:
                conn.rollback()
                return False
            f = calcular(cur, user_id)
            cur.execute(
                """insert into patrimonio_fotos (user_id, dia, gravada_em, total, carteira, bancos,
                       investimentos_banco, caixinhas, investimentos_manuais, base, motivos)
                   values (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (user_id, dia) do nothing""",
                (user_id, dia, f["total"], f["carteira"], f["bancos"], f["investimentos_banco"],
                 f["caixinhas"], f["investimentos_manuais"], Jsonb(f["base"]), f["motivos"]))
            gravou = cur.rowcount == 1
            conn.commit()
        return gravou
    except psycopg.errors.SerializationFailure:
        return False


def candidatos(dia: date) -> list[int]:
    """Usuários (com linha em `accounts`) ainda sem foto naquele dia."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """select a.user_id from accounts a
                where not exists (select 1 from patrimonio_fotos f
                                   where f.user_id = a.user_id and f.dia = %s)
                order by a.user_id""", (dia,))
        return [r["user_id"] for r in cur.fetchall()]
