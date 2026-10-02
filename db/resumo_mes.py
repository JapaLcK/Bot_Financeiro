"""
db/resumo_mes.py — Entrou e Saiu do mês: a regra única do mês-calendário (Q18).

`TOTAIS_SQL` é a fonte única (veio da consulta 5 do /app): lançamentos NÃO internos por
`criado_em` (a forma legada canonizada por `TIPO_CANON_SQL`) + compras no cartão sem
estorno pelo `period_end` da fatura (parcelado: uma parcela por mês). Leem daqui
`GET /api/v2/resumo-do-mes`, o "Gastos em <mês>" do WhatsApp, o relatório mensal, a
consulta 5 do /app e `compute_kpis` das Análises. `compute_evolution` é cópia em consulta
única, comparada mês a mês em `tests/test_resumo_mes_regra.py`.

Ficam na regra antiga (só `launches`, sem cartão: `get_summary_by_period`) de propósito,
e é divergência conhecida: relatório diário e semanal, ferramentas da IA de período
livre, projeção de fechamento e o Repórter (`piggy_agents._month_stats`). Ver
`docs/CLAUDE.md`, "API v2".

Limites declarados (não mudam número nenhum em relação às regras de antes): o mês corta
`criado_em` contra data ingênua (meia-noite no fuso da SESSÃO do Postgres), igual às
duas famílias antigas; conciliação pendente conta em dobro, como hoje (sai com motivo);
estorno não abate; transação PENDING que vira lançada, pagamento de fatura por
palavra-chave, cartão em duplicidade e moeda gravada como BRL ficam como estão.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .connection import TIPO_CANON_SQL, TIPO_DESPESA_SQL, TIPO_RECEITA_SQL, get_conn

# `n` é a contagem de linhas somadas (o `transactions_count` das Análises); `n_cartao`, só
# as do cartão (o relatório mensal as soma às suas linhas de `launches`). Cada perna
# filtra pelo usuário, inclusive a FATURA (`b.user_id`; NULL entra, porque a coluna
# aceita NULL sem backfill e o SQL antigo contava). Params: `totais_params`.
TOTAIS_SQL = f"""
    select coalesce(sum(valor) filter (where tipo = 'receita'), 0) as entrou,
           coalesce(sum(valor) filter (where tipo = 'despesa'), 0) as saiu,
           count(*) as n, count(*) filter (where cartao) as n_cartao
      from (select {TIPO_CANON_SQL} as tipo, valor, false as cartao
              from launches
             where user_id = %s
               and criado_em >= %s and criado_em < %s
               and is_internal_movement = false
               and ({TIPO_DESPESA_SQL} or {TIPO_RECEITA_SQL})
            union all
            select 'despesa', ct.valor, true
              from credit_transactions ct
              join credit_bills b on b.id = ct.bill_id
             where ct.user_id = %s and (b.user_id = %s or b.user_id is null)
               and ct.is_refund = false
               and b.period_end >= %s and b.period_end < %s) x
"""

# Do bloco de contas (`db/contas_hoje.py`), a mesma regra e o mesmo limite de 48 h, mais
# o corte da janela do plano. Ordem fixa: é a da resposta.
MOTIVOS = ("conciliacao_pendente", "movimentos_pendentes", "banco_desatualizado",
           "inicio_do_historico")


def totais_params(user_id: int, inicio: date, fim_excl: date) -> tuple:
    """Os de `TOTAIS_SQL`, na ordem do texto. O executor async do /app (`_q` da consulta
    5) e o síncrono abaixo leem daqui: um SQL só, dois executores (§0.7)."""
    return (user_id, inicio, fim_excl, user_id, user_id, inicio, fim_excl)


def totais(cur, user_id: int, inicio: date, fim_excl: date) -> dict:
    """`{"entrou", "saiu", "n", "n_cartao"}` em `[inicio, fim_excl)`, dinheiro em `Decimal`."""
    cur.execute(TOTAIS_SQL, totais_params(user_id, inicio, fim_excl))
    return dict(cur.fetchone())


def mes_de(dia: date) -> tuple[date, date]:
    """(dia 1 do mês de `dia`, dia 1 do mês seguinte)."""
    inicio = dia.replace(day=1)
    return inicio, (inicio + timedelta(days=32)).replace(day=1)


def totais_do_mes(user_id: int, dia: date) -> dict:
    """`totais` do mês-calendário INTEIRO de `dia` (o corrente também: a fatura que fecha
    depois de hoje, dentro do mês, entra). Para quem não tem cursor: WhatsApp e relatório."""
    with get_conn() as conn, conn.cursor() as cur:
        return totais(cur, user_id, *mes_de(dia))


def resumo_do_mes(user_id: int, ano: int, mes: int, agora: datetime | None = None) -> dict:
    """O `GET /api/v2/resumo-do-mes`. `agora` (no fuso do app) é um relógio só para quem
    chama validar o mês e para a janela do plano; `ate` é o último dia do mês pedido (o
    corrente também: a soma cobre o mês inteiro). Aplica a janela do plano como a consulta 5 do /app.
    `anterior` é o mês anterior inteiro (N4), ou `None` se a janela corta qualquer parte
    dele; quando ela corta o mês OU o anterior, sai `inicio_do_historico`. Os outros
    motivos vêm do bloco de contas e refletem a situação ATUAL das contas, não a do mês
    pedido (num mês passado também)."""
    from core.services.plan_service import history_earliest_date
    from utils_date import now_tz

    from .contas_hoje import listar

    agora = agora or now_tz()
    inicio, fim = mes_de(date(ano, mes, 1))
    ant_inicio = mes_de(inicio - timedelta(days=1))[0]
    corte = history_earliest_date(user_id, agora)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("set transaction isolation level repeatable read, read only")
        atual = totais(cur, user_id, max(inicio, corte) if corte else inicio, fim)
        anterior = None if corte and corte > ant_inicio else totais(cur, user_id, ant_inicio, inicio)
        vistos = set(listar(cur, user_id)["motivos"])
        conn.rollback()
    if corte and corte > ant_inicio:  # cortou o mês ou o `anterior` (que sai None)
        vistos.add("inicio_do_historico")
    return {
        "mes": f"{ano:04d}-{mes:02d}",
        "ate": (fim - timedelta(days=1)).isoformat(),  # a janela somada: o mês inteiro, sempre
        "entrou": atual["entrou"],
        "saiu": atual["saiu"],
        "anterior": anterior and {"mes": f"{ant_inicio.year:04d}-{ant_inicio.month:02d}",
                                  "entrou": anterior["entrou"], "saiu": anterior["saiu"]},
        "motivos": [m for m in MOTIVOS if m in vistos],
    }
