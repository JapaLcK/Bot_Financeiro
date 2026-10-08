"""
db/patrimonio.py — o patrimônio em reais, numa conta só (docs/plano-dashboard-v2.md §4).

`calcular` é a função que a foto diária (`gravar_foto`, pelo job de
`core/services/patrimonio_foto.py`) e a tela Patrimônio da etapa 6 usam. Só lê,
pelo cursor recebido: quem chama decide a transação.

Soma: Carteira (com a fusão devolvida, `merged_wallet_delta`) + contas do banco
(`CONTAS_BANCO_SQL`, o recorte do saldo consolidado) + posições de
investimento do banco em reais + caixinhas manuais (a espelhada já vem pela
posição) + investimentos manuais. Cartão fica de fora. `motivos` lista por que o
número não é exato; vazio só quando nada o põe em dúvida.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
from psycopg.types.json import Jsonb

from core.services.cashflow_contract import somar

from .connection import get_conn
from .open_finance import (PENDING_RECONCILIATION_SQL, actionable_pending_params,
                           merged_wallet_delta)
from .open_finance_state import _TERMINAL, SQL_COLETA_ESTOURADA, aplica_teto_por_health

# Posições do banco, uma por identidade do provedor (reconectar cria outra linha
# para a mesma posição): fica a da conexão mais nova, como em `CONTAS_BANCO_SQL`.
# O que sai da soma (conexão pausada/apagada, outra moeda, resgatada) é decidido
# em `calcular`, para ser contado em `base.fora`.
POSICOES_BANCO_SQL = """
    select distinct on (i.provider_investment_id)
        i.provider_investment_id, i.balance,
        upper(coalesce(i.currency, 'BRL')) as currency,
        upper(coalesce(i.raw->>'status', '')) as status,
        i.raw->>'currencyCode' as currency_code,
        i.raw->>'balance' as raw_balance, i.connection_id, i.updated_at,
        upper(coalesce(c.status, '')) as connection_status, i.type, i.subtype
    from open_finance_investments i
    join open_finance_connections c on c.id = i.connection_id
    where c.user_id=%s
    order by i.provider_investment_id, c.id desc
"""

# Contas BANK, uma por identidade do provedor, a da conexão mais nova; a moeda e a
# pausa são decididas DEPOIS do `distinct on`, em `calcular`, como nas posições (e
# em `db/contas_hoje.listar`, o bloco de contas, que lê o mesmo recorte).
# O `BANK_ACCOUNTS_SQL` filtra a moeda antes: com a mesma conta em USD na conexão
# nova e em BRL numa velha ainda viva, ele soma o BRL velho. Nesse caso (e só
# nele) a foto diverge do saldo consolidado de propósito. Limite: a fusão
# (`merged_wallet_delta`) e a conciliação seguem o `BANK_ACCOUNTS_SQL` e ainda
# contam a conta BRL velha.
CONTAS_BANCO_SQL = """
    select distinct on (a.provider_account_id)
        a.id, a.name, a.provider_account_id, a.balance,
        upper(coalesce(a.currency, 'BRL')) as currency,
        a.raw->>'currencyCode' as currency_code,
        a.raw->>'balance' as raw_balance, a.connection_id, a.updated_at,
        upper(coalesce(c.status, '')) as connection_status
    from open_finance_accounts a
    join open_finance_connections c on c.id = a.connection_id
    where c.user_id=%s and upper(a.type) = 'BANK'
    order by a.provider_account_id, c.id desc
"""

# Sincronização mais velha que isto é banco desatualizado (decisão do dono, P3).
BANCO_VELHO = timedelta(hours=48)

# `updated_at` mais novo de cada conexão do usuário, numa tabela do espelho. Os
# saves (`save_open_finance_sync`, `save_open_finance_investments`) carimbam todas
# as linhas de uma chamada com o MESMO `now`: a última geração tem esse máximo.
_ULTIMA_GERACAO_SQL = """
    select x.connection_id, max(x.updated_at) as m
      from {} x join open_finance_connections c on c.id = x.connection_id
     where c.user_id=%s group by x.connection_id
"""


# Os critérios abaixo são de `calcular` e do bloco de contas (`db/contas_hoje.py`):
# uma regra só para a foto e a tela.

def ler_conexoes(cur, user_id: int) -> tuple[list, dict]:
    """As conexões do usuário e o estado de cada uma (`connection_ui_state`), por id em texto."""
    from core.services.pluggy_health import connection_ui_state

    # O teto do "Atualizando…" muda o ESTADO (vira `error_recoverable`): sem as duas
    # metades dele (o derivado do SQL e o do `health`), a foto gravaria `updating`
    # onde a tela mostra erro. As mesmas que `get_open_finance_snapshot` aplica.
    cur.execute(f"select *, {SQL_COLETA_ESTOURADA} from open_finance_connections"
                " where user_id=%s order by id", (user_id,))
    conexoes = [aplica_teto_por_health(dict(c)) for c in cur.fetchall()]
    return conexoes, {str(c["id"]): connection_ui_state(c)["state"] for c in conexoes}


def desatualizada(c, estado: str, limite: datetime) -> bool:
    ultimo, tentativa = c["last_sync_at"], c["last_attempt_at"]
    return (estado != "updated" or ultimo is None or ultimo < limite
            or (tentativa is not None and tentativa > ultimo))


# `_to_decimal` deixa "NaN"/"Infinity" passar e a coluna numeric aceita: um só
# zeraria o total (a foto é permanente). Saldo não finito soma 0 e vira motivo.
def finito(v) -> bool:
    return v is not None and v.is_finite()


# O sync grava 0 na coluna quando a Pluggy omite ou estraga o saldo
# (`pluggy_sync._to_decimal`, e `save_open_finance_sync` no `or 0` das contas);
# só o `raw` diz que o 0 não veio do banco. Vale para conta e posição.
def sem_saldo(p) -> bool:
    try:
        return not finito(p["balance"]) or not Decimal(p["raw_balance"]).is_finite()
    except (TypeError, ArithmeticError):  # None ou texto que não é número
        return True


# Conta ou posição que deixou de vir no /accounts ou /investments fica no
# espelho com o saldo velho (`save_open_finance_sync` não poda conta; a posição
# só é podada com `leitura_completa`), e a conexão continua fresca. Ela fica
# com `updated_at` abaixo do máximo da sua conexão na mesma tabela (todas as
# linhas, de qualquer tipo/moeda: o save grava todas). O saldo fica na soma; o
# motivo marca a dúvida. Limites: cego quando o último sync omitiu TODAS as
# linhas da conexão; um caminho que carimbe `updated_at` de só uma linha faz as
# outras parecerem velhas (falso positivo: só o motivo, o total não muda).
def ultima_geracao(cur, user_id: int, tabela: str) -> dict:
    """{connection_id: maior updated_at} da tabela, para `fora_do_sync`."""
    cur.execute(_ULTIMA_GERACAO_SQL.format(tabela), (user_id,))
    return {r["connection_id"]: r["m"] for r in cur.fetchall()}


def fora_do_sync(p, ultima: dict) -> bool:
    m = ultima.get(p["connection_id"])
    return m is not None and p["updated_at"] is not None and p["updated_at"] < m


def separar_posicoes(rows) -> tuple[list, dict]:
    """(posições que entram, fora={'moeda','resgatada','pausada'}) — a regra da foto e do total investido."""
    fora = {"moeda": 0, "resgatada": 0, "pausada": 0}
    posicoes = []
    for p in rows:
        if p["connection_status"] in _TERMINAL:
            fora["pausada"] += 1
        elif p["currency"] != "BRL":
            fora["moeda"] += 1
        elif p["status"] == "TOTAL_WITHDRAWAL":
            fora["resgatada"] += 1
        else:
            posicoes.append(p)
    return posicoes, fora


def calcular(cur, user_id: int) -> dict:
    from .bank_movements import _declarations
    from .carteira_qualidade import nao_confirmada
    from .open_finance_cash import enabled as especie_ligada

    cur.execute("select balance from accounts where user_id=%s", (user_id,))
    row = cur.fetchone()
    carteira = somar((row["balance"] if row else Decimal(0), merged_wallet_delta(cur, user_id)))

    cur.execute(POSICOES_BANCO_SQL, (user_id,))
    posicoes, fora = separar_posicoes(cur.fetchall())
    # Conta de conexão pausada fica fora sem contar, como no saldo consolidado.
    cur.execute(CONTAS_BANCO_SQL, (user_id,))
    contas = []
    for r in cur.fetchall():
        if r["connection_status"] in _TERMINAL:
            continue
        if r["currency"] != "BRL":
            fora["moeda"] += 1
        else:
            contas.append(r)

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

    conexoes, estados = ler_conexoes(cur, user_id)
    vivas = [c for c in conexoes if (c["status"] or "").upper() not in _TERMINAL]
    limite = datetime.now(timezone.utc) - BANCO_VELHO
    ultima_contas = ultima_geracao(cur, user_id, "open_finance_accounts")
    ultima_posicoes = ultima_geracao(cur, user_id, "open_finance_investments")

    cur.execute(PENDING_RECONCILIATION_SQL, actionable_pending_params(cur, user_id))
    conciliacao = cur.fetchone()["pending_count"]

    motivos = [m for m, sim in (
        ("carteira_nao_confirmada", nao_confirmada(cur, user_id, row["balance"] if row else None)),
        ("especie_incompleta", not especie_ligada() and bool(vivas)),
        ("banco_desatualizado",
         any(desatualizada(c, estados[str(c["id"])], limite) for c in vivas)),
        ("movimentos_pendentes", any(not d["matched_transaction_id"]
                                     for d in _declarations(cur, user_id))),
        ("conciliacao_pendente", conciliacao > 0),
        ("manual_e_banco", (caixinhas["pos"] or manuais["pos"]) and bool(posicoes)),
        ("caixinha_espelhada_fora", fora["caixinha_espelhada"] > 0),
        ("moeda_presumida", any(not r["currency_code"] for r in [*contas, *posicoes])),
        ("saldo_ausente", any(sem_saldo(p) for p in [*contas, *posicoes])),
        ("conta_fora_do_ultimo_sync",
         any(fora_do_sync(r, ultima_contas) for r in contas)
         or any(fora_do_sync(p, ultima_posicoes) for p in posicoes)),
    ) if sim]

    partes = {
        "carteira": carteira,
        "bancos": somar(r["balance"] for r in contas if finito(r["balance"])),
        "investimentos_banco": sum((p["balance"] for p in posicoes if finito(p["balance"])),
                                   Decimal(0)),
        "caixinhas": caixinhas["s"],
        "investimentos_manuais": manuais["s"],
    }
    return {
        "total": somar(partes.values()),
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
