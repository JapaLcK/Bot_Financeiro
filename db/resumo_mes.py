"""
db/resumo_mes.py — Entrou e Saiu do mês: a regra única do mês-calendário (Q18).

`TOTAIS_SQL` é a fonte única (veio da consulta 5 do /app): lançamentos NÃO internos por
`criado_em` (a forma legada canonizada por `TIPO_CANON_SQL`) + compras no cartão sem
estorno pelo `period_end` da fatura (parcelado: uma parcela por mês). Leem daqui
`GET /api/v2/resumo-do-mes`, o "Gastos em <mês>" do WhatsApp, o relatório mensal, a
consulta 5 do /app, `compute_kpis` das Análises e as tools da IA de meses inteiros
(`get_period_summary`, que para 2 a 12 meses lê `mes_detalhes.ler_periodo`, o mesmo do
card `periodo` do chat; `forecast_month_end`, que lê o mês por `mes_detalhes.ler` e extrapola só o gasto até hoje fora do cartão do Open Finance; `compare_periods`, que lê `totais`/`categorias` daqui, PL-07, e `lancamentos.listar` para a série diária do gasto; `get_top_categories`, `get_largest_expenses` e
`get_category_spend` pela fatura: `core/services/ai_chat/tools/launches.py`); as pernas dela (`MES_*_SQL`), a
lista de `GET /api/v2/lancamentos` (`db/lancamentos.py`). `compute_evolution` é cópia em
consulta única, comparada mês a mês em `tests/test_resumo_mes_regra.py`.

`categorias` abre a despesa de `TOTAIS_SQL` por categoria (a soma é o `saiu`); lê dela
`core/services/comparacao_periodos.py` (`compare_periods` da IA, PL-07, com `mes=`; com datas que não são meses
inteiros dos dois lados ele usa a regra de período livre das outras tools, cartão pela data da compra:
`n_compras_cartao` conta essas compras). O mês aberto da comparação é o mês inteiro, como o `resumo_do_mes`.

Ficam fora da regra, de propósito, e é divergência conhecida: relatório diário e semanal
(só `launches`, sem cartão: `get_summary_by_period`), o Repórter
(`piggy_agents._month_stats`) e as ferramentas da IA de período livre: `get_period_summary`
soma `launches` + cartão pela data da compra (`cartao_por: data_da_compra`), como
`get_top_categories` e `get_largest_expenses`; `get_category_spend` pela fatura
e `get_spending_trend` pela data da compra. Ver
`docs/CLAUDE.md`, "API v2".

Limites declarados (não mudam número nenhum em relação às regras de antes): o mês corta
`criado_em` contra data ingênua (meia-noite no fuso da SESSÃO do Postgres), igual às
duas famílias antigas; conciliação pendente conta em dobro, como hoje (sai com motivo);
estorno não abate; transação PENDING que vira lançada, pagamento de fatura por
palavra-chave, cartão em duplicidade e moeda gravada como BRL ficam como estão.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .connection import CAT_VAZIA_LABEL, TIPO_CANON_SQL, TIPO_DESPESA_SQL, TIPO_RECEITA_SQL, cat_key_sql, get_conn

# As duas pernas do mês. A lista de `db/lancamentos.py` lê a mesma janela (`MES_JANELA_SQL`) e
# a perna do cartão (§0.7); só os tipos ela alarga (guardado, nunca em total). Params:
# (user_id, início, fim) e (user_id, user_id, início, fim). A de `launches` é sem alias e
# SEM o filtro de interno: a lista mostra o interno marcado; a soma o tira abaixo. Janela e tipos
# separados porque a lista alarga só os tipos (guardado, P8); a soma não muda.
MES_JANELA_SQL = "user_id = %s and criado_em >= %s and criado_em < %s"
MES_TIPOS_SQL = f"({TIPO_DESPESA_SQL} or {TIPO_RECEITA_SQL})"
MES_LANCAMENTOS_SQL = f"{MES_JANELA_SQL} and {MES_TIPOS_SQL}"
# O FROM da perna do cartão, com o join da fatura e a barreira no MESMO texto (o portão de
# `tests/test_barreira_fatura.py` lê o literal). Cada perna filtra pelo usuário, inclusive a
# FATURA (`b.user_id`; a NULL, a coluna aceita sem backfill, vale pelo dono do cartão dela).
MES_CARTAO_SQL = """credit_transactions ct
              join credit_bills b on b.id = ct.bill_id
               and ct.user_id = %s and b.user_id = %s
               and ct.is_refund = false
               and b.period_end >= %s and b.period_end < %s"""

# `n` é a contagem de linhas somadas (o `transactions_count` das Análises); `n_cartao`, só
# as do cartão (o relatório mensal as soma às suas linhas de `launches`). Params: `totais_params`.
TOTAIS_SQL = f"""
    select coalesce(sum(valor) filter (where tipo = 'receita'), 0) as entrou,
           coalesce(sum(valor) filter (where tipo = 'despesa'), 0) as saiu,
           count(*) as n, count(*) filter (where cartao) as n_cartao
      from (select {TIPO_CANON_SQL} as tipo, valor, false as cartao
              from launches
             where {MES_LANCAMENTOS_SQL}
               and is_internal_movement = false
            union all
            select 'despesa', ct.valor, true
              from {MES_CARTAO_SQL}) x
"""

# A despesa de `TOTAIS_SQL` por categoria: as mesmas duas pernas (mesmos params), agrupada pela
# chave de casamento (`cat_key_sql`); rótulo = grafia do lançamento mais recente (como `compute_categories`).
CATEGORIAS_SQL = f"""
    select {cat_key_sql("categoria")} as chave,
           (array_agg(categoria order by dt desc))[1] as categoria,
           sum(valor) as total, count(*) as n
      from (select {TIPO_CANON_SQL} as tipo, valor, criado_em as dt,
                   coalesce(nullif(categoria, ''), '{CAT_VAZIA_LABEL}') as categoria
              from launches
             where {MES_LANCAMENTOS_SQL}
               and is_internal_movement = false
            union all
            select 'despesa', ct.valor, b.period_end::timestamptz,
                   coalesce(nullif(ct.categoria, ''), '{CAT_VAZIA_LABEL}')
              from {MES_CARTAO_SQL}) x
     where tipo = 'despesa'
     group by {cat_key_sql("categoria")}
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


def categorias(cur, user_id: int, inicio: date, fim_excl: date) -> list[dict]:
    """`[{"chave", "categoria", "total", "n"}]` da despesa em `[inicio, fim_excl)`; a soma é o `saiu` de `totais`."""
    cur.execute(CATEGORIAS_SQL, totais_params(user_id, inicio, fim_excl))
    return [dict(r) for r in cur.fetchall()]


def n_compras_cartao(user_id: int, inicio: date, fim: date) -> int:
    """Quantas compras de cartão (sem estorno) têm `purchased_at` em `[inicio, fim]` (fim INCLUSIVO): a
    contagem da perna de cartão do período livre de `db.get_top_expense_categories`, que usa o mesmo filtro.
    Abre a própria conexão, como aquela: quem chama não pode estar dentro de outro `get_conn()` (pool pequeno)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from credit_transactions where user_id = %s and is_refund = false "
                    "and purchased_at >= %s and purchased_at <= %s", (user_id, inicio, fim))
        return int(cur.fetchone()["n"])


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
