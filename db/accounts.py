"""
db/accounts.py — Saldo, lançamentos e importação OFX.
"""
import json
import logging
from datetime import datetime, date, timedelta
from decimal import Decimal

from psycopg.types.json import Json, Jsonb

import db_support as _db_support
from utils_date import _tz, day_tz, launch_day

from .connection import (
    get_conn, cat_key_sql, LAUNCH_HAS_TIME_SQL,
    TIPO_CANON_SQL, TIPO_DESPESA_SQL, TIPO_RECEITA_SQL,
)
from .users import ensure_user, ensure_user_tx

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Saldo
# ──────────────────────────────────────────────────────────────────────────────

def get_balance(user_id: int) -> Decimal:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select balance from accounts where user_id=%s", (user_id,))
            row = cur.fetchone()
            return row["balance"] if row else Decimal("0")


def set_balance(user_id: int, new_balance: Decimal) -> Decimal:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE accounts SET balance=%s WHERE user_id=%s RETURNING balance",
                (new_balance, user_id),
            )
            bal = cur.fetchone()["balance"]
        conn.commit()
    return bal


# ──────────────────────────────────────────────────────────────────────────────
# Lançamentos
# ──────────────────────────────────────────────────────────────────────────────

def add_launch_and_update_balance(
    user_id: int,
    tipo: str,
    valor: float,
    alvo: str | None,
    nota: str | None,
    categoria: str | None = None,
    criado_em: datetime | None = None,
    is_internal_movement: bool = False,
    extra_efeitos: dict | None = None,
):
    """
    Lança em launches e atualiza saldo em accounts na mesma transação.
    Regra: despesa → saldo -= valor; receita → saldo += valor.

    `extra_efeitos` é mesclado dentro de `efeitos` jsonb. Use pra que
    `delete_launch_and_rollback` consiga reverter side-effects além do
    saldo (ex: `bill_id` pra pagamento de fatura).
    """
    ensure_user(user_id)

    v = Decimal(str(valor))
    if tipo == "despesa":
        delta = -v
    elif tipo == "receita":
        delta = +v
    else:
        raise ValueError(f"tipo inválido: {tipo}")

    if criado_em is None:
        criado_em = datetime.now(_tz())

    cat = (categoria or "").strip() or "outros"

    efeitos = {"delta_conta": float(delta)}
    if extra_efeitos:
        efeitos.update(extra_efeitos)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update accounts set balance = balance + %s where user_id=%s returning balance",
                (delta, user_id),
            )
            new_bal = cur.fetchone()["balance"]

            cur.execute(
                """
                insert into launches(user_id, tipo, valor, alvo, nota, categoria, criado_em, efeitos, is_internal_movement)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                returning id, user_seq
                """,
                (user_id, tipo, v, alvo, nota, cat, criado_em,
                 Json(efeitos), is_internal_movement),
            )
            row = cur.fetchone()
            launch_id = row["id"]
            user_seq = row["user_seq"]

        conn.commit()

    return launch_id, user_seq, new_bal


def list_launches(user_id: int, limit: int = 10):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                -- `posted_at` + `has_time`: MESMO par (e mesmo CASE) da lista de
                -- uma categoria. Sem eles as duas portas divergiam em um dia nas
                -- linhas sem hora confiável — aqui elas são exatamente as que o
                -- CASE de `LAUNCH_HAS_TIME_SQL` marca como falsas. Compra no
                -- crédito não é uma delas: ela não grava linha nenhuma em
                -- `launches` (`add_credit_purchase`, db/cards.py), e esta query
                -- lê só `launches`. A divergência é a que `launch_day`
                -- (utils_date) fecha.
                select id, user_seq, tipo, valor, alvo, nota, categoria, source, criado_em,
                       posted_at, {LAUNCH_HAS_TIME_SQL} as has_time
                from launches
                where user_id=%s
                order by criado_em desc, id desc
                limit %s
                """,
                (user_id, limit),
            )
            return cur.fetchall()


def latest_launch_id(user_id: int) -> int | None:
    """Maior id de lançamento do usuário (ou None se não há nenhum). Usa max(id),
    não a ordem por data — assim detecta uma inserção mesmo de lançamento
    retroativo (criado_em no passado), cujo id é o maior mas não é o mais recente
    por data."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select max(id) as mx from launches where user_id=%s", (user_id,))
            row = cur.fetchone()
            return int(row["mx"]) if row and row["mx"] is not None else None


def get_last_inserted_launch(user_id: int):
    """Lançamento inserido por ÚLTIMO (maior id), com os campos que o desfazer
    usa. Diferente de `list_launches(limit=1)`, NÃO ordena por data — "desfazer"
    deve remover o último lançamento CRIADO, mesmo que seja retroativo (criado_em
    no passado). Retorna a row ou None."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, user_seq, tipo, valor from launches "
                "where user_id=%s order by id desc limit 1",
                (user_id,),
            )
            return cur.fetchone()


def list_launches_by_tipo(user_id: int, tipo: str, limit: int = 200):
    """Lançamentos recentes de um tipo (despesa/receita) com só os campos que
    a detecção de valor recorrente precisa (valor + descrição). Ordenado do mais
    recente pro mais antigo."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select valor, alvo, nota
                from launches
                where user_id=%s and tipo=%s
                order by criado_em desc, id desc
                limit %s
                """,
                (user_id, tipo, int(limit)),
            )
            return cur.fetchall()


def resolve_user_seq_to_id(user_id: int, user_seq: int) -> int | None:
    """Converte o `#N` que o usuário digita (user_seq) no id interno do lançamento.

    Retorna None se não houver lançamento com esse user_seq pra esse usuário.
    """
    if not user_seq or user_seq <= 0:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from launches where user_id=%s and user_seq=%s",
                (user_id, int(user_seq)),
            )
            row = cur.fetchone()
            return int(row["id"]) if row else None


def get_launch_user_seq(user_id: int, launch_id: int) -> int | None:
    """Inverso de resolve_user_seq_to_id: pega o user_seq de um id interno."""
    if not launch_id:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select user_seq from launches where id=%s and user_id=%s",
                (int(launch_id), user_id),
            )
            row = cur.fetchone()
            seq = row.get("user_seq") if row else None
            return int(seq) if seq else None


def display_id_for(user_id: int, launch_id: int) -> int:
    """Retorna o user_seq pra exibir; cai no id interno se não encontrar."""
    seq = get_launch_user_seq(user_id, launch_id)
    return seq if seq is not None else int(launch_id)


def update_launch_category(user_id: int, launch_id: int, categoria: str | None) -> bool:
    from utils_text import is_internal_category

    ensure_user(user_id)
    cat = (categoria or "").strip() or None
    is_internal = is_internal_category(cat)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update launches set categoria=%s, is_internal_movement=%s where user_id=%s and id=%s",
                (cat, is_internal, user_id, launch_id),
            )
            changed = (cur.rowcount or 0) == 1
        conn.commit()
    return changed


class LaunchDateLockedError(ValueError):
    """Tentou editar a data de um lançamento cuja data é do provedor (Open Finance)."""


# As condições PERMANENTES de `delete_launch_and_rollback` que o usuário precisa
# distinguir. São `ValueError` por tipo, não por código, porque quem discrimina
# é o TIPO em cada porta (WhatsApp, /ai/chat, dashboard) — e cada porta escreve
# a própria frase. O `str(exc)` NÃO é mais user-facing: o dashboard mandava
# `HTTPException(400, detail=str(exc))` e o modal do app mostrava "lançamento
# sem 'efeitos'" cru; hoje ele mapeia tipo → frase (`_MSG_DELETE_LAUNCH`,
# `frontend/finance_bot_websocket_custom.py`).
#
# Os outros `ValueError` da função (delta_pocket/delta_invest sem nome, dado
# corrompido) seguem crus DE PROPÓSITO: o destino deles é o mesmo de uma causa
# inesperada — ramo técnico, com log e retry — então nomeá-los seria classe sem
# chamador.

class LaunchNoEffects(ValueError):
    """O lançamento não guarda `efeitos`, então não dá pra reverter o saldo."""


class InvestmentLotHasWithdrawal(ValueError):
    """O lote do aporte já teve resgate. TEMPORÁRIA: apagar o resgate destrava."""

    motivo = "lote_com_resgate"


class LaunchUnsafeRollback(ValueError):
    """`efeitos` existe mas esta função não sabe revertê-lo POR INTEIRO: chave
    fora da allowlist, `efeitos` degenerado (sem `delta_conta`), chave presente
    mas sem o campo que a torna reversível, ou — só no "apagar tudo" — efeito
    de caixinha/investimento, que está fora do escopo daquele comando. Falha
    FECHADA: mantém a linha em vez de apagar dinheiro em silêncio.

    `motivo` é um CÓDIGO CURTO ENUMERADO, escolhido no `raise` — nunca
    derivado da mensagem, que é texto livre e pode passar a carregar dado do
    cliente. Sem ele o log de `delete_all_launches` colapsava as recusas num
    `causa=LaunchUnsafeRollback` só, e a comum (`lote_ausente`, lote gravado
    antes de `79bd52f`, que dispara em todo depósito de caixinha antigo) ficava
    indistinguível da rara e grave (`chave_desconhecida`, escritor novo
    gravando efeito que ninguém sabe reverter). Os cinco valores:

      - `sem_delta_conta`     — `efeitos` sem a chave (degenerado, ex.: `{}`)
      - `chave_desconhecida`  — chave fora de `_EFEITOS_REVERSIVEIS`
      - `lote_ausente`        — `delta_pocket`/`delta_invest` sem a chave do lote
      - `efeito_incompleto`   — chave PRESENTE sem o campo que a torna
                                reversível (`_EFEITOS_CAMPOS_EXIGIDOS`); o
                                irmão do `lote_ausente`, que é a chave ausente
      - `fora_do_escopo`      — caixinha/investimento no "apagar tudo"

    Obrigatório no construtor de propósito: `raise` novo tem de escolher um
    código, em vez de herdar um genérico em silêncio."""

    def __init__(self, mensagem: str, motivo: str):
        super().__init__(mensagem)
        self.motivo = motivo


def update_launch_fields(
    user_id: int,
    launch_id: int,
    *,
    categoria: str | None = None,
    alvo: str | None = None,
    nota: str | None = None,
    criado_em: datetime | None = None,
) -> bool:
    """Atualiza campos editáveis (categoria, alvo, nota, criado_em) de um lançamento.

    Argumentos None são ignorados (mantém valor atual). Strings vazias viram
    NULL no banco. Retorna False se não encontrou lançamento do usuário.
    """
    from utils_text import is_internal_category

    ensure_user(user_id)

    sets: list[str] = []
    params: list = []
    if categoria is not None:
        cat_clean = categoria.strip() or None
        sets.append("categoria=%s")
        params.append(cat_clean)
        sets.append("is_internal_movement=%s")
        params.append(is_internal_category(cat_clean))
    if alvo is not None:
        sets.append("alvo=%s")
        params.append((alvo.strip() or None))
    if nota is not None:
        sets.append("nota=%s")
        params.append((nota.strip() or None))
    if criado_em is not None:
        sets.append("criado_em=%s")
        params.append(criado_em)
        # `posted_at` anda JUNTO com `criado_em`. Onde não há hora confiável é
        # ELE quem manda no dia exibido — no back (`launch_day`, utils_date) e no
        # front (`fmtLaunchWhen`: dashboard.js:485, home.html:776). Sem isto,
        # editar a data de um extrato devolvia 200, mudava o banco e a tela
        # seguia mostrando a data VELHA, sem caminho de conserto.
        # Depois da recusa abaixo sobra só o extrato: `posted_at` não-nulo é
        # gravado por dois escritores, `import_ofx_launches_bulk` (source='ofx',
        # nesta mesma pasta) e o importador do Open Finance
        # (db/open_finance.py:1247) — e a linha do OF nem chega aqui.
        # Não é chave de idempotência de importador nenhum (OFX/extrato dedupam
        # por `external_id`, montado a partir do ARQUIVO; o Open Finance por
        # `provider_transaction_id`). NULL continua NULL: lançamento manual não
        # tem data de postagem.
        sets.append("posted_at = case when posted_at is null then null else %s end")
        params.append(day_tz(criado_em))
    if not sets:
        return False

    params.extend([user_id, launch_id])
    sql = f"update launches set {', '.join(sets)} where user_id=%s and id=%s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            if criado_em is not None:
                # DONO DA DATA numa linha do Open Finance é o PROVEDOR, não o
                # usuário. `sync_imported_open_finance_updates`
                # (db/open_finance.py:1559-1588) compara
                # `coalesce(posted_at, criado_em::date)` com o
                # `transaction_date` do espelho e, quando diferem, REESCREVE
                # `posted_at` com a data do banco — e isso roda em TODA
                # sincronização Pluggy (core/services/pluggy_sync.py:218).
                # Medido: editar 10/03 → 15/04 devolvia 200 e a sync seguinte
                # voltava pra 10/03. Aceitar seria fingir sucesso; recusar é o
                # que a tela consegue explicar. (Nota/descrição continuam
                # editáveis: a sync não toca em `nota`/`alvo`.)
                cur.execute(
                    "select coalesce(source,'') as source from launches where user_id=%s and id=%s",
                    (user_id, launch_id),
                )
                row = cur.fetchone()
                if row and row["source"] == "open_finance":
                    raise LaunchDateLockedError(
                        "A data deste lançamento vem do banco conectado e é "
                        "atualizada por ele. Dá pra editar a descrição e a categoria."
                    )
            cur.execute(sql, tuple(params))
            changed = (cur.rowcount or 0) == 1
        conn.commit()
    return changed


def update_launch_categories_bulk(user_id: int, items: list[tuple[int, str]]) -> int:
    from utils_text import is_internal_category

    ensure_user(user_id)
    if not items:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "update launches set categoria=%s, is_internal_movement=%s where user_id=%s and id=%s",
                [(cat, is_internal_category(cat), user_id, lid) for (lid, cat) in items],
            )
            n = cur.rowcount or 0
        conn.commit()
    return n


def export_launches(user_id: int, start_date: date | None = None, end_date: date | None = None):
    ensure_user(user_id)

    params = [user_id]
    where = ["user_id=%s"]

    if start_date:
        where.append("criado_em >= %s")
        params.append(datetime.combine(start_date, datetime.min.time()))
    if end_date:
        where.append("criado_em < %s")
        params.append(datetime.combine(end_date + timedelta(days=1), datetime.min.time()))

    sql = f"""
        select id, tipo, valor, alvo, nota, criado_em, efeitos
        from launches
        where {' and '.join(where)}
        order by criado_em asc, id asc
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            return cur.fetchall()


def get_launches_by_period(user_id: int, start_date: date, end_date: date):
    return _db_support.get_launches_by_period_impl(get_conn, ensure_user, user_id, start_date, end_date)


def get_summary_by_period(user_id: int, start_date: date, end_date: date):
    return _db_support.get_summary_by_period_impl(get_conn, ensure_user, user_id, start_date, end_date)


def get_internal_movement_total(user_id: int, start_date: date, end_date: date) -> float:
    """Soma de saídas internas (aportes, transferências pra caixinha) no período.

    `is_internal_movement=true` marca alocação que sai do caixa corrente mas
    não é gasto. Pra projeção de saldo (`forecast_month_end`), conta junto
    com despesa porque debita a conta corrente igual.
    """
    ensure_user(user_id)
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select coalesce(sum(valor), 0) as total
                from launches
                where user_id = %s
                  and tipo = 'despesa'
                  and is_internal_movement = true
                  and criado_em >= %s and criado_em < %s
                """,
                (user_id, start_dt, end_excl),
            )
            row = cur.fetchone()
    return float(row["total"] or 0) if row else 0.0


def get_spending_trend(user_id: int, months: int = 6) -> list[dict]:
    """Tendência de gastos dos últimos N meses (default 6, contando o atual).

    Cada item: {year, month, despesa, receita}. Despesas incluem launches
    (não-internos) + compras no cartão. Receita só de launches.
    """
    ensure_user(user_id)
    months = max(1, min(int(months), 24))
    today = date.today()

    # Calcula primeiro dia do mês mais antigo a incluir
    y, m = today.year, today.month
    for _ in range(months - 1):
        if m == 1:
            m = 12
            y -= 1
        else:
            m -= 1
    range_start = datetime(y, m, 1)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select y::int as year, m::int as month,
                       sum(case when tipo = 'despesa' then valor else 0 end) as despesa,
                       sum(case when tipo = 'receita' then valor else 0 end) as receita
                from (
                    -- `TIPO_CANON_SQL` colapsa a forma legada na moderna AQUI, na
                    -- perna de launches, para o `case when tipo = 'despesa'` de
                    -- fora continuar valendo sem virar uma segunda lista de
                    -- aliases. Sem isto, 'saida'/'entrada' eram filtradas fora e
                    -- a tendência da IA divergia do dashboard no mesmo mês.
                    select extract(year from criado_em at time zone %s)::int as y,
                           extract(month from criado_em at time zone %s)::int as m,
                           {TIPO_CANON_SQL} as tipo, valor
                    from launches
                    where user_id = %s
                      and criado_em >= %s
                      and is_internal_movement = false
                      and ({TIPO_DESPESA_SQL} or {TIPO_RECEITA_SQL})
                    union all
                    select extract(year from purchased_at)::int as y,
                           extract(month from purchased_at)::int as m,
                           'despesa' as tipo, valor
                    from credit_transactions
                    where user_id = %s
                      and purchased_at >= %s::date
                      and is_refund = false
                ) agg
                group by y, m
                order by y, m
                """,
                (
                    "America/Sao_Paulo", "America/Sao_Paulo",
                    user_id, range_start,
                    user_id, range_start.date(),
                ),
            )
            rows = cur.fetchall()

    return [
        {
            "year": int(r["year"]),
            "month": int(r["month"]),
            "despesa": float(r["despesa"] or 0),
            "receita": float(r["receita"] or 0),
        }
        for r in rows
    ]


def get_largest_expenses(
    user_id: int,
    start_date: date,
    end_date: date,
    limit: int = 5,
    categoria: str | None = None,
    by_bill_month: bool = False,
):
    """Top N maiores gastos INDIVIDUAIS no período (não agregados por categoria).

    Difere de `get_top_expense_categories` que soma por categoria. Esta
    retorna os lançamentos/compras de maior valor, um por um.

    Fontes:
      - launches: `TIPO_DESPESA_SQL` (a moderna e a legada 'saida', mesma
        forma do total e da lista) AND is_internal_movement=false (por criado_em)
      - credit_transactions onde is_refund=false

    `by_bill_month`:
      - False (padrão): compra de cartão entra pelo período da DATA DA COMPRA
        (purchased_at). Usado pelas tools de análise da IA.
      - True: compra entra pelo MÊS DA FATURA (credit_bills.period_end) — mesma
        regra do dashboard. Um gasto parcelado conta uma parcela por mês. Usado
        pela resposta "quanto gastei" do bot, pra bater com o dashboard.

    Se `categoria` for informada, filtra pelos gastos daquela categoria
    (match case- e acento-insensível, mesmo critério de
    `sum_spent_in_category_period` — a lista é sempre subconjunto do total).

    Retorna lista [{valor, categoria, descricao, data, fonte}].
    `fonte` = 'launches' | 'credito' (frontend pode renderizar tag).
    `descricao` = alvo (se launches) ou nota (se credito).
    """
    ensure_user(user_id)

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
    end_date_excl = end_date + timedelta(days=1)  # janela meio-aberta em period_end

    # `cat_key_sql` e não `cat_norm_sql`: mesma chave da lista e do total da
    # categoria (`list_launches_by_category`; `sum_spent_in_category_period`,
    # db/budgets.py). Os "5 maiores" saem ao lado daquele total na resposta do
    # bot — com a chave crua, a categoria SEM nome trazia total e lista com
    # valor e nenhum "maior gasto".
    cat_filter = (
        f"and {cat_key_sql('categoria')} = {cat_key_sql('%s')}" if categoria else ""
    )
    cat_filter_ct = (
        f"and {cat_key_sql('ct.categoria')} = {cat_key_sql('%s')}" if categoria else ""
    )

    if by_bill_month:
        credit_from = "from credit_transactions ct join credit_bills b on b.id = ct.bill_id"
        credit_date = "and b.period_end >= %s and b.period_end < %s"
        credit_date_params = [start_date, end_date_excl]
    else:
        credit_from = "from credit_transactions ct"
        credit_date = "and ct.purchased_at >= %s::date and ct.purchased_at <= %s::date"
        credit_date_params = [start_date, end_date]

    launches_params = [user_id]
    if categoria:
        launches_params.append(categoria)
    launches_params += [start_dt, end_excl]

    credit_params = [user_id]
    if categoria:
        credit_params.append(categoria)
    credit_params += credit_date_params

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select valor, categoria, descricao, dt, fonte
                from (
                    select valor,
                           coalesce(nullif(categoria, ''), 'outros') as categoria,
                           coalesce(nullif(alvo, ''), nullif(nota, ''), '—') as descricao,
                           criado_em::date as dt,
                           'launches' as fonte
                    from launches
                    where user_id = %s
                      and {TIPO_DESPESA_SQL}
                      and is_internal_movement = false
                      {cat_filter}
                      and criado_em >= %s and criado_em < %s
                    union all
                    select ct.valor,
                           coalesce(nullif(ct.categoria, ''), 'outros') as categoria,
                           coalesce(nullif(ct.nota, ''), 'compra no crédito') as descricao,
                           ct.purchased_at as dt,
                           'credito' as fonte
                    {credit_from}
                    where ct.user_id = %s
                      and ct.is_refund = false
                      {cat_filter_ct}
                      {credit_date}
                ) agg
                order by valor desc
                limit %s
                """,
                (
                    *launches_params,
                    *credit_params,
                    int(limit),
                ),
            )
            rows = cur.fetchall()

    return [
        {
            "valor": float(r["valor"] or 0),
            "categoria": r["categoria"],
            "descricao": r["descricao"],
            "data": r["dt"].isoformat() if r.get("dt") else None,
            "fonte": r["fonte"],
        }
        for r in rows
    ]


# Nenhum writer atual escreve 'entrada'/'saida', mas MUITO read path de produção
# ainda trata esses valores como tipo, espalhado por core/, db/ e frontend/ —
# `evaluate_after_expense`, `_xerife_detect_for_user`, `_month_stats`,
# `compute_evolution`, `get_budgets_status_for_month`, o resumo de
# `list_launches`, o bloco inteiro de db/analytics.py e o SQL de
# `_fetch_admin_overview_inner`, entre outros. A contagem exata fica de fora de
# propósito: não há comando que a produza sem falso positivo, e número de
# comentário que ninguém consegue reproduzir envelhece errado. Para ver a lista
# de hoje:
#   grep -rn "'entrada'\|'saida'" --include="*.py" core/ db/ frontend/
# Filtrar só o valor moderno deixaria a linha legada invisível na lista E fora
# do total.
_TIPO_ALIASES = {
    "despesa": ("despesa", "saida"),
    "saida": ("despesa", "saida"),
    "receita": ("receita", "entrada"),
    "entrada": ("receita", "entrada"),
}


def list_launches_by_category(
    user_id: int,
    categoria: str,
    start_date: date | None = None,
    end_date: date | None = None,
    tipo: str | None = None,
    limit: int = 20,
    include_internal: bool = True,
    after: tuple | None = None,
) -> tuple[list[dict], dict]:
    """Lançamentos de UMA categoria, mais recente primeiro (launches + cartão).

    Difere de `get_largest_expenses` (que ordena por valor e é expense-only):
    aqui a lista é cronológica e inclui RECEITA — é o que responde "liste as
    receitas em rendimentos".

    - `tipo`: 'despesa' | 'receita' | None (ambos), case-insensitive. Aceita os
      valores LEGADOS 'saida'/'entrada' junto (mesmo `tipo in (...)` de
      `evaluate_after_expense` e `_xerife_detect_for_user`): uma linha antiga com
      tipo='entrada' ficaria invisível E fora do total, que é a pior classe de
      erro num caminho de dinheiro. Valor DESCONHECIDO ('xyz') não filtra nada e
      devolve os dois tipos: degradar pra lista VAZIA num caminho de dinheiro
      esconde dinheiro, degradar pra "os dois" só mostra a mais. A perna do cartão
      só entra quando tipo pede despesa ou nada — compra no crédito nunca é
      receita.
    - `start_date`/`end_date`: opcionais. None → sem janela (últimos N da
      categoria, sem filtrar por mês).
    - A compra de cartão entra pelo MÊS DA FATURA (credit_bills.period_end),
      mesma regra do dashboard e do `sum_spent_in_category_period`. Sem flag: o
      `by_bill_month` que `get_largest_expenses` expõe nunca foi chamado com
      False aqui, e uma janela alternativa que ninguém pede é só mais um SQL pra
      manter em sincronia.
    - Match de categoria pela `cat_key_sql` — case- e acento-insensível E com o
      vazio colapsado em 'sem categoria', a MESMA expressão que o donut do
      dashboard agrupa. Contra a coluna crua, `norm(NULL)` é NULL e nunca casa
      com nada: a barra "sem categoria" dizia R$ 100,00 e esta lista abria
      vazia (medido). Chega em produção pela importação de cartão do Open
      Finance, que grava `credit_transactions.categoria` NULO.
    - Tipos internos de gerenciamento (criar_caixinha & cia) ficam de fora;
      `is_internal_movement` é filtrado só quando `include_internal=False`. O
      default é True (comportamento de sempre): senão "liste os lançamentos em
      investimento_aporte" voltaria vazio. Consequência do default: numa
      categoria de movimento interno (pagamento_fatura, aporte) o total daqui é
      MAIOR que o de `sum_spent_in_category_period`, que filtra
      `is_internal_movement = false` (`sum_spent_in_category_period`, db/budgets.py).
      `include_internal=False` existe pro dashboard: quando a lista é aberta
      CLICANDO numa linha da Distribuição do mês, ela tem que mostrar o mesmo
      conjunto que aquela linha somou — e o donut (query 6 de
      finance_bot_websocket_custom.py e `get_top_expense_categories`) filtra
      `is_internal_movement = false`. Sem isso a linha dizia R$ 50 e o rodapé da
      lista dizia R$ 750 pela mesma categoria e o mesmo mês.

    Retorna `(rows, resumo)`:
    - rows: [{tipo, valor, categoria, descricao, nota, alvo, data, criado_em,
      posted_at, has_time, fonte, user_seq, id, is_internal_movement}], no
      máximo `limit`.
      `fonte` = 'launches' | 'credito'; `user_seq` é None no crédito (não existe
      "#N" pra apagar). `id` pareia com `user_seq`: é o `launches.id` na perna de
      launches e NULO na de crédito — de propósito. As duas tabelas têm
      sequências próprias, então o id COLIDE, e a perna de crédito fixa
      `tipo='despesa'` (ver acima): um `credit_transactions.id` saindo daqui com
      tipo='despesa' faria o chamador rotear o delete pro endpoint de launches e
      apagar OUTRO registro. Nulo na origem = colisão impossível. `data` é o dia
      por `launch_day` (utils_date): o `posted_at` gravado quando `has_time` é
      falso, senão o dia de PAREDE de `criado_em` (`day_tz`); nunca um `::date`
      em SQL, que responderia a pergunta errada onde a linha não tem hora
      (mesmo agora que a sessão do Postgres roda no fuso do app, por
      `utils_date.align_process_tz`). `criado_em` é o instante cheio (o editor do dashboard
      preenche "Data e hora" com ele — `data` sozinho abria o campo vazio).
      Na perna de `launches`, `posted_at` + `has_time` são o mesmo par (e o mesmo
      `LAUNCH_HAS_TIME_SQL`) que a Visão Geral usa na query 4 do dashboard: sem
      eles o front caía sempre no galho "só data" do `fmtLaunchWhen` e a mesma
      despesa saía "10/03, 00:30" numa tela e "09/03" na outra. Na perna de
      CRÉDITO as duas queries DIVERGEM: aqui `has_time=false` e o dia é o da
      COMPRA (`posted_at` = `purchased_at`, que é `date`); lá
      (finance_bot_websocket_custom.py:461) é `true` com `posted_at` nulo e
      `criado_em` = `credit_transactions.created_at`, o instante em que a LINHA
      foi gravada (`created_at timestamptz default now()`, db/schema.py:604).
      Quando os dois caem em dias diferentes — compra lançada depois, importação
      de cartão do Open Finance — a MESMA compra sai com dia diferente nas duas
      telas (medido: compra em 25/08 gravada em 28/08 → "25/08" aqui, "28/08,
      HH:MM" na Visão Geral). Alinhar as duas é mudança de COMPORTAMENTO, não
      deste comentário.
    - `categoria` também vem CRUA (NULL/'' saem como estão), pelo mesmo motivo
      de `nota`/`alvo` abaixo: o `coalesce(..., 'outros')` era um RÓTULO de
      mensagem de WhatsApp, e desde que o dashboard abre o editor por esta lista
      ele virava DADO — quem editasse só a nota ou a data de um lançamento sem
      categoria mandava 'outros' de volta no PATCH e categorizava a transação
      sem pedir. A tela decide como mostrar o vazio (dashboard.js), o SELECT não
      decide o que fica gravado.
    - `nota` e `alvo` vêm CRUS, cada um na sua chave. `descricao` continua sendo
      o rótulo pronto (`coalesce(alvo, nota, '—')`) que o WhatsApp imprime, mas
      ele NÃO serve pra pré-preencher um formulário de edição: numa linha com os
      dois preenchidos (recurring_charger.py, db/bills.py, db/cards.py) ele é o
      ALVO, e salvar o formulário gravava o alvo por cima da nota real.
    `after` (default None, aditivo) é o "carregar mais" do dashboard: a tupla
    `(dt, fonte, ord_id)` da ÚLTIMA linha da página anterior, e a próxima página
    é `where (dt, fonte, ord_id) < after` na mesma ordem total do `order by`.
    `after` é seguro porque entrou no FIM da assinatura: nenhum parâmetro que já
    existia mudou de posição, então nenhum dos 4 chamadores muda de
    comportamento. São eles `_listar_categoria`, `_total_despesa` e
    `_total_categoria` (core/handlers/launches.py), que passam
    `user_id, categoria, start, end` posicionais e `tipo`/`limit` por keyword, e
    `category_launches_route` (frontend/routes/categories.py), que passa TUDO
    por keyword.

    Era `offset`, e OFFSET não fecha a corrida que este produto tem TODO DIA: o
    bot escreve no banco enquanto o dashboard está aberto. Uma linha nova entra
    ACIMA do corte (a ordem é por data desc) e empurra a fronteira — a página 2
    repete a última linha da 1 e come outra, com o total dizendo que está tudo
    lá. Ordem total resolve EMPATE, não deslocamento; só o keyset resolve os
    dois. Deduplicar no cliente também não serve: ele veria a repetida, nunca a
    que sumiu.

    O filtro do keyset é aplicado FORA da subquery dos window aggregates, senão
    `n_total`/`tot_*` passariam a contar só o que sobrou depois do corte e o
    rodapé "N de M" mentiria a partir da página 2.

    - resumo: {"n_total", "despesa", "receita", "next_after"} sobre TODAS as
      linhas que casam ("next_after" é a tupla da última linha DESTA página, ou
      None quando a página veio vazia — é o que o chamador devolve como `after`),
      não só as `limit` devolvidas — os totais vêm de window aggregates, que o
      Postgres calcula ANTES do LIMIT. Sem isso o chamador somaria só as linhas
      exibidas e imprimiria um total errado com cara de total certo.
    """
    ensure_user(user_id)

    _cat = cat_key_sql("categoria")
    _cat_ct = cat_key_sql("ct.categoria")
    _arg = cat_key_sql("%s")

    params: list = [user_id, categoria]
    launch_filters = ""
    # tipo fora do dicionário ('DESPESA', 'xyz') → NENHUM filtro. Ver docstring:
    # `_TIPO_ALIASES.get(tipo, (tipo,))` filtrava por um valor que não existe na
    # coluna e devolvia lista vazia com n_total=0 — dinheiro sumido, sem erro.
    aliases = _TIPO_ALIASES.get(str(tipo).strip().lower()) if tipo else None
    if aliases:
        launch_filters += " and tipo = any(%s)"
        params.append(list(aliases))
    if not include_internal:
        launch_filters += " and is_internal_movement = false"
    if start_date:
        launch_filters += " and criado_em >= %s"
        params.append(datetime.combine(start_date, datetime.min.time()))
    if end_date:
        launch_filters += " and criado_em < %s"
        params.append(datetime.combine(end_date + timedelta(days=1), datetime.min.time()))

    credit_sql = ""
    if aliases is None or "despesa" in aliases:
        credit_from = "from credit_transactions ct join credit_bills b on b.id = ct.bill_id"
        credit_date_col = "b.period_end"
        credit_filters = ""
        params_credit: list = [user_id, categoria]
        if start_date:
            credit_filters += f" and {credit_date_col} >= %s"
            params_credit.append(start_date)
        if end_date:
            credit_filters += f" and {credit_date_col} < %s"
            params_credit.append(end_date + timedelta(days=1))
        credit_sql = f"""
                    union all
                    select 'despesa' as tipo,
                           ct.valor,
                           ct.categoria,
                           coalesce(nullif(ct.nota, ''), 'compra no crédito') as descricao,
                           -- Mesmo texto do `descricao` de propósito: a compra
                           -- no crédito não tem `alvo` aqui e a linha nunca é
                           -- editável (id nulo), então `nota` só precisa ser o
                           -- rótulo que a tela já mostra.
                           coalesce(nullif(ct.nota, ''), 'compra no crédito') as nota,
                           null::text as alvo,
                           ct.purchased_at::timestamp as dt,
                           -- Compra no crédito não tem hora: `purchased_at` é
                           -- `date` (db/schema.py). has_time=false + posted_at
                           -- fazem o front imprimir "dd/mm" sem passar por
                           -- conversão de fuso nenhuma.
                           ct.purchased_at as posted_at,
                           false as has_time,
                           'credito' as fonte,
                           null::int as user_seq,
                           null::int as id,
                           ct.id as ord_id,
                           false as is_internal_movement
                    {credit_from}
                    where ct.user_id = %s
                      and {_cat_ct} = {_arg}
                      and ct.is_refund = false
                      {credit_filters}
        """
        params += params_credit

    # Keyset. Fica FORA da subquery dos window aggregates (ver docstring) e usa a
    # comparação de LINHA do Postgres, que é exatamente o `order by` de baixo.
    after_sql = ""
    after_params: list = []
    if after:
        after_sql = "where (dt, fonte, ord_id) < (%s, %s, %s)"
        dt_after, fonte_after, ord_after = after
        after_params = [dt_after, str(fonte_after), int(ord_after)]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select * from (
                select tipo, valor, categoria, descricao, nota, alvo, dt,
                       posted_at, has_time, fonte,
                       user_seq, id, ord_id, is_internal_movement,
                       count(*) over () as n_total,
                       coalesce(sum(valor) filter (
                           where tipo in ('despesa', 'saida')) over (), 0) as tot_despesa,
                       coalesce(sum(valor) filter (
                           where tipo in ('receita', 'entrada')) over (), 0) as tot_receita
                from (
                    select tipo,
                           valor,
                           categoria,
                           coalesce(nullif(alvo, ''), nullif(nota, ''), '—') as descricao,
                           nota,
                           alvo,
                           criado_em as dt,
                           posted_at,
                           {LAUNCH_HAS_TIME_SQL} as has_time,
                           'launches' as fonte,
                           user_seq,
                           id,
                           id as ord_id,
                           is_internal_movement
                    from launches
                    where user_id = %s
                      and {_cat} = {_arg}
                      and tipo not in ('criar_caixinha', 'delete_pocket',
                                       'create_investment', 'delete_investment')
                      {launch_filters}
                    {credit_sql}
                ) agg
                ) w
                {after_sql}
                -- Ordem TOTAL, e é o que faz o keyset funcionar: `dt desc,
                -- user_seq desc` empatava todas as linhas de crédito do mesmo dia
                -- (user_seq é nulo nelas), e sem desempate a comparação de linha
                -- pula ou repete. `fonte` separa as duas pernas (id colide entre
                -- as tabelas) e `ord_id` é a PK dentro de cada uma. A ordem
                -- VISÍVEL não muda: 'launches' > 'credito' no desc, e user_seq
                -- cresce junto com o id.
                order by dt desc, fonte desc, ord_id desc
                limit %s
                """,
                (*params, *after_params, int(limit)),
            )
            rows = cur.fetchall()

    resumo = {
        "n_total": int(rows[0]["n_total"]) if rows else 0,
        "despesa": float(rows[0]["tot_despesa"] or 0) if rows else 0.0,
        "receita": float(rows[0]["tot_receita"] or 0) if rows else 0.0,
        # A tupla de ordenação da ÚLTIMA linha desta página = o `after` da
        # próxima. Sai daqui e não da LINHA porque `ord_id` é o id CRU da tabela
        # (o do crédito inclusive): o `id` nulo na perna de crédito existe pra um
        # `credit_transactions.id` não virar handle de delete no dashboard, e uma
        # chave `ord_id` na linha desfaria isso. O cursor CARREGA esse id em
        # texto claro (`_fmt_cursor`, frontend/routes/categories.py) — ele é
        # marcador de página, não handle de linha, e as rotas de crédito são
        # escopadas por `user_id`, então cursor de terceiro não devolve linha
        # alheia. Segredo ele não é; sigilo aqui seria criptografia caseira.
        "next_after": (
            (rows[-1]["dt"], rows[-1]["fonte"], rows[-1]["ord_id"]) if rows else None
        ),
    }
    return [
        {
            "tipo": r["tipo"],
            "valor": float(r["valor"] or 0),
            "categoria": r["categoria"],
            "descricao": r["descricao"],
            "nota": r["nota"],
            "alvo": r["alvo"],
            # `launch_day` (utils_date), não `.date()` nem `day_tz` seco: o `.date()`
            # cru devolve o dia no fuso da SESSÃO do Postgres, que ERA UTC na
            # produção, e um gasto das 21:30 em São Paulo saía com o dia SEGUINTE
            # — o MESMO lançamento com dia diferente aqui e em "meus últimos
            # lançamentos" (`list_launches`, core/handlers/launches.py). E onde `has_time` é
            # falso quem manda é `posted_at` — QUAIS linhas são essas, e por que
            # os importadores de HOJE ficam de fora, estão no docstring de
            # `launch_day` (utils_date), que é o dono dessa enumeração.
            "data": launch_day(r["dt"], r["posted_at"], r["has_time"]),
            "criado_em": r["dt"],
            "posted_at": r["posted_at"],
            "has_time": bool(r["has_time"]),
            "fonte": r["fonte"],
            "user_seq": r["user_seq"],
            "id": r["id"],
            "is_internal_movement": bool(r["is_internal_movement"]),
        }
        for r in rows
    ], resumo


def get_top_expense_categories(
    user_id: int,
    start_date: date,
    end_date: date,
    limit: int = 5,
    by_bill_month: bool = False,
):
    """Top N categorias de gasto no período.

    Agrega:
      - despesas reais em launches (tipo='despesa', is_internal_movement=false),
        pela data do lançamento (criado_em)
      - compras no cartão (credit_transactions, is_refund=false)

    `by_bill_month`:
      - False (padrão): compra de cartão entra pela DATA DA COMPRA (purchased_at).
        Usado pelas tools de análise da IA.
      - True: compra entra pelo MÊS DA FATURA (credit_bills.period_end) — igual
        ao dashboard. Um gasto parcelado conta uma parcela por mês. Usado pela
        resposta "quanto gastei" do bot.

    NÃO inclui movimentações internas (aporte, resgate, transfer caixinha)
    nem reembolsos de cartão.

    Retorna lista [{categoria, total}] ordenada desc por total.
    """
    ensure_user(user_id)

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_excl = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
    end_date_excl = end_date + timedelta(days=1)  # janela meio-aberta em period_end

    if by_bill_month:
        credit_from = "from credit_transactions ct join credit_bills b on b.id = ct.bill_id"
        credit_date = "and b.period_end >= %s and b.period_end < %s"
        credit_date_params = (start_date, end_date_excl)
    else:
        credit_from = "from credit_transactions ct"
        credit_date = "and ct.purchased_at >= %s::date and ct.purchased_at <= %s::date"
        credit_date_params = (start_date, end_date)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select coalesce(nullif(categoria, ''), 'outros') as categoria,
                       sum(valor) as total
                from (
                    select categoria, valor
                    from launches
                    where user_id = %s
                      and tipo = 'despesa'
                      and is_internal_movement = false
                      and criado_em >= %s and criado_em < %s
                    union all
                    select ct.categoria, ct.valor
                    {credit_from}
                    where ct.user_id = %s
                      and ct.is_refund = false
                      {credit_date}
                ) agg
                group by coalesce(nullif(categoria, ''), 'outros')
                order by total desc
                limit %s
                """,
                (
                    user_id, start_dt, end_excl,
                    user_id, *credit_date_params,
                    int(limit),
                ),
            )
            rows = cur.fetchall()

    return [
        {"categoria": r["categoria"], "total": float(r["total"] or 0)}
        for r in rows
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Desfazer lançamento
# ──────────────────────────────────────────────────────────────────────────────

# Chaves de `efeitos` que `delete_launch_and_rollback` sabe tratar: as que ela
# reverte + as informativas (nada a reverter). ALLOWLIST, não denylist: chave
# nova de escritor novo faz o delete falhar FECHADO (mantém a linha) em vez de
# apagar dinheiro em silêncio. `tests/test_efeitos_allowlist.py` varre os
# escritores com `ast` e reprova chave não classificada aqui — o ALCANCE da
# varredura e as cegueiras dela estão na docstring do teste, não repetidos aqui.
_EFEITOS_REVERSIVEIS = frozenset({
    # revertidas por esta função
    "delta_conta", "bill_id", "paid_amount_added",
    "create_pocket", "create_investment", "delete_pocket", "delete_investment",
    "delta_pocket", "delta_invest",
    "investment_lot_create", "investment_lot_withdrawals",
    # informativas: ficam só no histórico, não há efeito a desfazer
    "funding_source", "tax_summary", "investment_meta",
    "ofx", "open_finance", "time_known",
})

# Classificadas e DE FORA da allowlist de propósito: são gravadas
# (`db/pockets.py`, depósito e saque de caixinha) e NUNCA revertidas. Apagar um
# `deposito_caixinha` reverte `pockets.balance` pelo `delta_pocket`, mas a linha
# em `pocket_lots` fica — e `_sync_pocket_from_lots` (`db/pockets.py`) recalcula
# `balance = sum(pocket_lots.balance)` no próximo depósito/saque/accrual, então
# o dinheiro desfeito VOLTA. Enquanto a reversão do lote não existir, apagar
# depósito/saque de caixinha RECUSA em todas as portas — mudança de
# comportamento visível e desejada.
#
# Lida só pelo teste da allowlist (`tests/test_efeitos_allowlist.py`): a recusa
# em produção vem do `desconhecidas` (não estão em `_EFEITOS_REVERSIVEIS`), não
# desta constante. Ela existe pra separar "fora da allowlist DE PROPÓSITO" de
# "chave nova que ninguém classificou" — sem isso a varredura reprovaria as duas.
_EFEITOS_SEM_REVERSAO = frozenset({"pocket_lot_create", "pocket_lot_withdrawals"})

# `delta_pocket`/`delta_invest` mexem em saldo de LOTE, e a reversão do saldo
# agregado (`pockets.balance`/`investments.balance`) só está completa quando a
# função também sabe QUAL lote desfazer. Cada delta é pareado com as chaves que
# nomeiam o lote; delta != 0 sem nenhuma delas é linha LEGADA (gravada antes de
# `79bd52f`, 16/05/2026, quando os lotes passaram a ser registrados no `efeitos`).
# Legada não quer dizer sem lote: `_ensure_pocket_lots` (`db/pockets.py`) e
# `_ensure_investment_lots` (`db/investments.py`) fazem backfill preguiçoso e
# criam um lote com o saldo inteiro no primeiro movimento posterior. Reverter só
# o agregado deixa o lote de pé, e o `_sync_*_from_lots` traz o dinheiro
# desfeito de volta no movimento seguinte — o mesmo estrago do `pocket_lot_*`,
# sem a chave que o denunciava.
_DELTA_EXIGE_LOTE = (
    ("delta_pocket", ("pocket_lot_create", "pocket_lot_withdrawals")),
    ("delta_invest", ("investment_lot_create", "investment_lot_withdrawals")),
)

# Chave PRESENTE mas sem o campo que a torna reversível. É outra falha que
# `_DELTA_EXIGE_LOTE` não pega: lá a chave do lote está AUSENTE (linha legada);
# aqui ela está lá e vazia por dentro, e cada `if <campo>:` a jusante vira um
# no-op silencioso — o efeito não é desfeito e o `delete` acontece assim mesmo.
#
# O inventário dos 8 sites, medido no `pigbank_ci_test` com `efeitos` forjado e
# `delete_launch_and_rollback` real (o motivo de cada linha estar aqui):
#   investment_lot_create sem `lot_id`     -> :1348 pula o delete do lote, mas
#       :1373 reverte o agregado: conta 700->1000, inv 300->0, lote de 300 DE PÉ;
#       o `_sync_*_from_lots` traz os 300 de volta no movimento seguinte.
#   investment_lot_withdrawals sem `lot_id`-> :1394 `continue`, lote não restaurado.
#   investment_lot_withdrawals sem `before`-> PIOR: :1404 escreve o default 0 e
#       ZERA o lote. O dinheiro some do lote E do agregado, sem volta.
#   bill_id sem `paid_amount_added`        -> :1248 pula a reversão, a fatura
#       fica `paid` com o pagamento apagado.
#   create_pocket/create_investment sem `nome`  -> :1447/:1274 não deletam.
#   delete_pocket/delete_investment sem `nome`  -> :1317/:1283 não recriam.
#
# `InvestmentLotHasWithdrawal` (:1363) NÃO cobre nada disso: mora DENTRO do
# `if lot_id:`. Inalcançável pelos escritores de hoje — os cinco gravam os
# campos no mesmo insert atômico, e nenhum código reescreve `efeitos` depois —,
# mas a tese deste PR é recusar o que não sabe reverter, e um dos oito destrói
# dinheiro. `bill_id` fica de fora desta tabela: é PAR com `paid_amount_added`,
# tratado no laço. `delta_invest`/`delta_pocket` PRESENTES sem `nome` são da
# MESMA classe (chave presente e oca) e também ficam de fora: seguem crus, como
# `ValueError` sem `motivo`, pela decisão documentada em `:258-261`.
_EFEITOS_CAMPOS_EXIGIDOS = (
    ("investment_lot_create", ("lot_id",)),
    ("investment_lot_withdrawals", ("lot_id", "before")),
    ("create_pocket", ("nome",)),
    ("create_investment", ("nome",)),
    ("delete_pocket", ("nome",)),
    ("delete_investment", ("nome",)),
)

# `before` sem estes dois faz o `.get(campo, 0)` de :1404-1405 escrever ZERO no
# lote. Exigir a chave `before` sozinha deixaria o pior dos oito aberto.
_BEFORE_CAMPOS = ("balance", "principal_remaining")

# Efeitos que o "apagar tudo" não pode tocar: a mensagem promete "suas caixinhas
# e investimentos NÃO são afetados". Guarda de ESCOPO, não de segurança — o
# delete de UM lançamento continua podendo desfazer um "criar caixinha", que é
# reversível. É a guarda EXPLÍCITA que faltava: o filtro por `tipo`
# (`_CONTA_CORRENTE_LAUNCH_FILTER`) protege isso só por tabela, e um `tipo`
# reescrito o fura.
_EFEITOS_FORA_DO_APAGAR_TUDO = (
    "create_pocket", "create_investment", "delete_pocket", "delete_investment",
    "delta_pocket", "delta_invest",
)


def delete_launch_and_rollback(user_id: int, launch_id: int, *,
                              escopo_conta_corrente: bool = False):
    """
    Deleta um lançamento e reverte seus efeitos no banco atomicamente.
    Usa o campo efeitos (jsonb) para saber o que reverter.

    Recusa (sem tocar em saldo) o que não sabe reverter por inteiro:
    `LaunchNoEffects` sem `efeitos`, `LaunchUnsafeRollback` com `efeitos`
    degenerado, com chave fora de `_EFEITOS_REVERSIVEIS`, com delta de lote
    sem a chave que nomeia o lote (`_DELTA_EXIGE_LOTE`), ou com a chave
    PRESENTE e oca — sem o campo que a torna reversível
    (`_EFEITOS_CAMPOS_EXIGIDOS`), no container errado, com `before` sem
    `_BEFORE_CAMPOS`, ou com `bill_id`/`paid_amount_added` desemparelhados.

    `escopo_conta_corrente=True` — usado SÓ pelo "apagar tudo" — recusa também
    o que mexe em caixinha/investimento (`_EFEITOS_FORA_DO_APAGAR_TUDO`).

    QUEM CHAMA — são OITO pontos, não as 4 portas de usuário. A recusa chega ao
    usuário como frase de produto em cinco deles e como SILÊNCIO em três:
      - `core/handlers/pending.py:170` (WhatsApp, singular) e `:230` (bulk);
      - `core/services/ai_chat/tools/launches.py:433` (/ai/chat);
      - `frontend/finance_bot_websocket_custom.py:5470` (DELETE /launches);
      - `delete_all_launches_and_rollback` (abaixo), que classifica em baldes;
      - `db/open_finance.py:43`, `:1329` e `:1418` — os três dentro de
        `except Exception: pass`. Ali uma recusa não vira mensagem nem log: o
        lançamento duplicado do Open Finance sobrevive à reconciliação e o saldo
        conta duas vezes, calado. HOJE inalcançável (as chaves que o importador
        do OF grava estão todas em `_EFEITOS_REVERSIVEIS`, e ele não grava delta
        de lote), mas qualquer chave nova de OF vira perda silenciosa antes de
        virar recusa visível. Os `except` de lá são o próximo conserto, não este.
    (`adapters/discord/` também chama; o adaptador está morto e fora do escopo.)
    """
    ensure_user(user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # `for update`: serializa duas reversões do MESMO lançamento. Sem
            # ele as duas leem o mesmo `efeitos`, as duas revertem o saldo e o
            # `delete` da segunda casa zero linhas sem levantar — o dinheiro
            # sai em dobro. Com ele, quem perde relê depois do commit do
            # vencedor, não acha a linha e levanta LookupError("NOT_FOUND").
            # Os chamadores de UM lançamento tratam isso como 404/"não achei";
            # o `delete_all_launches_and_rollback` (abaixo) NÃO — a linha que
            # sumiu por outra porta cai no `except Exception` dele e o usuário
            # lê "erro técnico, continua aí" sobre algo que já não existe. É o
            # comportamento da `main` também; o balde que falta é outro PR.
            cur.execute(
                "select id, tipo, valor, alvo, efeitos from launches "
                "where id=%s and user_id=%s for update",
                (launch_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                raise LookupError("NOT_FOUND")

            efeitos = row.get("efeitos")
            if isinstance(efeitos, str):
                try:
                    efeitos = json.loads(efeitos)
                except ValueError:
                    efeitos = None
            # jsonb aceita lista, escalar e string: `not isinstance(dict)` cobre
            # o `null` de hoje e os degenerados no mesmo ramo.
            if not isinstance(efeitos, dict):
                raise LaunchNoEffects("lançamento sem 'efeitos' (não dá pra desfazer com segurança).")

            # Daqui pra baixo NENHUM `update` ainda rodou — as recusas são antes
            # de mexer em saldo, de propósito.
            #
            # Presença de chave, não valor: os escritores gravam `delta_conta`
            # explicitamente, inclusive os que legitimamente gravam 0
            # (open_finance, criar_caixinha). `efeitos = '{}'::jsonb` NÃO é
            # `null`, então não cai em LaunchNoEffects, e `.get("delta_conta", 0)`
            # devolvia 0: a linha era apagada e o dinheiro não voltava.
            if "delta_conta" not in efeitos:
                raise LaunchUnsafeRollback(
                    "lançamento com 'efeitos' incompleto (sem delta_conta).",
                    "sem_delta_conta",
                )
            desconhecidas = set(efeitos) - _EFEITOS_REVERSIVEIS
            if desconhecidas:
                raise LaunchUnsafeRollback(
                    f"efeitos que não sei reverter: {sorted(desconhecidas)}",
                    "chave_desconhecida",
                )
            # FORMA antes de regra de negócio, e antes do `_DELTA_EXIGE_LOTE` de
            # propósito: a linha legada de verdade (chave AUSENTE) tem de
            # continuar saindo com `lote_ausente`, que é o motivo comum e o que
            # os testes já prendem. Aqui a chave está PRESENTE e oca.
            # `None` é ignorado: os escritores gravam `"delta_pocket": None`
            # explicitamente, e isso não é efeito nenhum a reverter.
            for chave, campos in _EFEITOS_CAMPOS_EXIGIDOS:
                valor = efeitos.get(chave)
                if valor is None:
                    continue
                # Container POR CHAVE: só `investment_lot_withdrawals` é lido
                # como LISTA (o `for effect in …` abaixo); as outras cinco levam
                # `.get` direto no dict. Aceitar os dois deixava o swap passar
                # aqui e estourar `AttributeError` cru lá embaixo — balde
                # `errors` e SILÊNCIO nos três `except Exception: pass` do OF.
                lista = chave == "investment_lot_withdrawals"
                if isinstance(valor, list) != lista:
                    raise LaunchUnsafeRollback(
                        f"'{chave}' com forma inesperada ({type(valor).__name__}).",
                        "efeito_incompleto",
                    )
                for item in (valor if lista else [valor]):
                    if not isinstance(item, dict):
                        raise LaunchUnsafeRollback(
                            f"'{chave}' com forma inesperada ({type(item).__name__}).",
                            "efeito_incompleto",
                        )
                    faltando = [c for c in campos if not item.get(c)]
                    # `before` presente mas oco escreve 0 no lote (:1404) — é o
                    # único dos oito que DESTRÓI dinheiro, então checa por dentro.
                    if not faltando and "before" in campos:
                        antes = item.get("before")
                        # VALOR, não presença: `{"balance": null}` passava pelo
                        # `c not in antes` e virava `Decimal(str(None))` —
                        # `InvalidOperation` cru, mesmo balde do swap acima.
                        if not isinstance(antes, dict) or any(
                            antes.get(c) is None for c in _BEFORE_CAMPOS
                        ):
                            faltando = ["before.%s" % "/".join(_BEFORE_CAMPOS)]
                    if faltando:
                        raise LaunchUnsafeRollback(
                            f"'{chave}' sem {faltando}: a reversão não desfaz o efeito.",
                            "efeito_incompleto",
                        )
            # `bill_id` e `paid_amount_added` são PAR (:1248 exige os dois): um
            # sem o outro pula a reversão e a fatura fica `paid` sem pagamento.
            if (efeitos.get("bill_id") is None) != (efeitos.get("paid_amount_added") is None):
                raise LaunchUnsafeRollback(
                    "'bill_id'/'paid_amount_added' incompletos: a reversão não "
                    "desfaz o pagamento da fatura.",
                    "efeito_incompleto",
                )
            delta_conta = Decimal(str(efeitos.get("delta_conta", 0)))
            # `delta == 0` NÃO entra: `create_investment` (`db/investments.py`)
            # grava `delta_invest` com delta 0 num investimento que ainda não
            # tem lote nenhum, e continua apagável. Mas só quando o lançamento
            # não move a conta TAMBÉM: `delta` 0 com `delta_conta` -300 é
            # dinheiro saindo da conta pra um lote que a reversão não desfaz —
            # o saldo volta e o lote fica, criando 300. Não alcançável hoje (o
            # único escritor de delta 0 grava `delta_conta` 0), fechado de
            # graça porque ele continua apagável.
            for delta_key, lot_keys in _DELTA_EXIGE_LOTE:
                delta_val = efeitos.get(delta_key)
                if not isinstance(delta_val, dict):
                    continue
                if Decimal(str(delta_val.get("delta") or 0)) == 0 and delta_conta == 0:
                    continue
                if not any(efeitos.get(k) for k in lot_keys):
                    raise LaunchUnsafeRollback(
                        f"'{delta_key}' sem chave de lote (lançamento legado): "
                        f"a reversão não desfaz o lote.",
                        "lote_ausente",
                    )
            if escopo_conta_corrente and any(
                efeitos.get(k) is not None for k in _EFEITOS_FORA_DO_APAGAR_TUDO
            ):
                raise LaunchUnsafeRollback(
                    "lançamento mexe em caixinha/investimento — fora do 'apagar tudo'.",
                    "fora_do_escopo",
                )

            delta_pocket = efeitos.get("delta_pocket")
            delta_invest = efeitos.get("delta_invest")
            create_pocket = efeitos.get("create_pocket")
            create_invest = efeitos.get("create_investment")
            delete_pocket = efeitos.get("delete_pocket")
            delete_investment = efeitos.get("delete_investment")
            investment_lot_create = efeitos.get("investment_lot_create")
            investment_lot_withdrawals = efeitos.get("investment_lot_withdrawals") or []
            investment_lots_handled = False
            # Pagamento de fatura: bill_id + paid_amount_added permitem
            # reverter o `paid_amount` da credit_bill correspondente.
            paid_bill_id = efeitos.get("bill_id")
            paid_amount_added = efeitos.get("paid_amount_added")

            # desfazer pagamento de fatura — reverte paid_amount e reabre se
            # necessário (paid não cobre mais o total).
            if paid_bill_id and paid_amount_added is not None:
                cur.execute(
                    """
                    update credit_bills
                    set paid_amount = greatest(0, coalesce(paid_amount, 0) - %s),
                        status = case
                            when (coalesce(paid_amount, 0) - %s) < total then 'open'
                            else status
                        end,
                        paid_at = case
                            when (coalesce(paid_amount, 0) - %s) <= 0 then null
                            else paid_at
                        end
                    where id = %s and user_id = %s
                    """,
                    (
                        Decimal(str(paid_amount_added)),
                        Decimal(str(paid_amount_added)),
                        Decimal(str(paid_amount_added)),
                        int(paid_bill_id),
                        user_id,
                    ),
                )

            # desfazer criação de investimento (zera e deleta)
            if create_invest:
                nome = create_invest.get("nome")
                if nome:
                    cur.execute(
                        "delete from investments where user_id=%s and lower(name)=lower(%s) and balance=0",
                        (user_id, nome),
                    )

            # desfazer deleção de investimento (recria)
            if delete_investment:
                nome = delete_investment.get("nome")
                bal0 = Decimal(str(delete_investment.get("balance", 0)))
                rate = Decimal(str(delete_investment.get("rate", 0)))
                period = delete_investment.get("period", "monthly")
                last_date_str = delete_investment.get("last_date")
                asset_type = delete_investment.get("asset_type") or "CDB"
                indexer = delete_investment.get("indexer")
                issuer = delete_investment.get("issuer")
                purchase_date = delete_investment.get("purchase_date")
                maturity_date = delete_investment.get("maturity_date")
                interest_payment_frequency = delete_investment.get("interest_payment_frequency") or "maturity"
                tax_profile = delete_investment.get("tax_profile") or "regressive_ir_iof"
                if nome:
                    from datetime import date as _date
                    ld = _date.fromisoformat(last_date_str) if last_date_str else datetime.now(_tz()).date()
                    cur.execute(
                        """
                        insert into investments(
                            user_id, name, balance, rate, period, last_date,
                            asset_type, indexer, issuer, purchase_date, maturity_date,
                            interest_payment_frequency, tax_profile
                        )
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        on conflict (user_id, name) do nothing
                        """,
                        (
                            user_id, nome, bal0, rate, period, ld,
                            asset_type, indexer, issuer, purchase_date, maturity_date,
                            interest_payment_frequency, tax_profile,
                        ),
                    )

            # desfazer deleção de caixinha (recria)
            if delete_pocket:
                nome = delete_pocket.get("nome")
                bal0 = Decimal(str(delete_pocket.get("balance", 0)))
                if nome:
                    cur.execute(
                        "insert into pockets(user_id, name, balance) values (%s,%s,%s) "
                        "on conflict (user_id, name) do nothing",
                        (user_id, nome, bal0),
                    )

            # reverte conta
            if delta_conta != 0:
                cur.execute(
                    "update accounts set balance = balance - %s where user_id=%s",
                    (delta_conta, user_id),
                )

            # reverte caixinha
            if delta_pocket:
                nome = delta_pocket.get("nome")
                dp = Decimal(str(delta_pocket.get("delta", 0)))
                if not nome:
                    raise ValueError("delta_pocket inválido (sem nome).")
                cur.execute(
                    "update pockets set balance = balance - %s where user_id=%s and lower(name)=lower(%s)",
                    (dp, user_id, nome),
                )

            # reverte lotes de investimento antes do saldo agregado.
            if investment_lot_create:
                lot_id = investment_lot_create.get("lot_id")
                investment_id = investment_lot_create.get("investment_id")
                if lot_id:
                    cur.execute(
                        """
                        select investment_id, principal_initial, principal_remaining, status
                        from investment_lots
                        where id=%s and user_id=%s
                        for update
                        """,
                        (lot_id, user_id),
                    )
                    lot = cur.fetchone()
                    if lot and (
                        lot["status"] != "open"
                        or Decimal(str(lot["principal_remaining"])) != Decimal(str(lot["principal_initial"]))
                    ):
                        raise InvestmentLotHasWithdrawal(
                            "Não é possível desfazer este aporte: o lote já teve resgate."
                        )
                    if lot and not investment_id:
                        investment_id = lot["investment_id"]
                    cur.execute(
                        "delete from investment_lots where id=%s and user_id=%s",
                        (lot_id, user_id),
                    )
                    investment_lots_handled = True
                if investment_id:
                    cur.execute(
                        """
                        update investments i
                        set balance = coalesce(l.total_balance, 0),
                            last_date = coalesce(l.max_last_date, i.last_date)
                        from (
                            select coalesce(sum(balance), 0) as total_balance, max(last_date) as max_last_date
                            from investment_lots
                            where user_id=%s and investment_id=%s and status='open'
                        ) l
                        where i.user_id=%s and i.id=%s
                        """,
                        (user_id, investment_id, user_id, investment_id),
                    )

            if investment_lot_withdrawals:
                restored_investment_ids = set()
                for effect in investment_lot_withdrawals:
                    lot_id = effect.get("lot_id")
                    before = effect.get("before") or {}
                    if not lot_id:
                        continue
                    cur.execute(
                        """
                        update investment_lots
                        set balance=%s, principal_remaining=%s, status=%s, closed_at=%s
                        where id=%s and user_id=%s
                        returning investment_id
                        """,
                        (
                            Decimal(str(before.get("balance", 0))),
                            Decimal(str(before.get("principal_remaining", 0))),
                            before.get("status") or "open",
                            before.get("closed_at"),
                            lot_id,
                            user_id,
                        ),
                    )
                    restored = cur.fetchone()
                    if restored:
                        restored_investment_ids.add(restored["investment_id"])
                        investment_lots_handled = True

                for investment_id in restored_investment_ids:
                    cur.execute(
                        """
                        update investments i
                        set balance = coalesce(l.total_balance, 0),
                            last_date = coalesce(l.max_last_date, i.last_date)
                        from (
                            select coalesce(sum(balance), 0) as total_balance, max(last_date) as max_last_date
                            from investment_lots
                            where user_id=%s and investment_id=%s and status='open'
                        ) l
                        where i.user_id=%s and i.id=%s
                        """,
                        (user_id, investment_id, user_id, investment_id),
                    )

            # reverte investimento
            if delta_invest:
                nome = delta_invest.get("nome")
                di = Decimal(str(delta_invest.get("delta", 0)))
                if not nome:
                    raise ValueError("delta_invest inválido (sem nome).")
                if not investment_lots_handled:
                    cur.execute(
                        "update investments set balance = balance - %s where user_id=%s and lower(name)=lower(%s)",
                        (di, user_id, nome),
                    )

            # desfazer criação de caixinha (deleta)
            if create_pocket:
                nome = create_pocket.get("nome")
                if nome:
                    cur.execute(
                        "delete from pockets where user_id=%s and lower(name)=lower(%s)",
                        (user_id, nome),
                    )

            # apaga o lançamento
            cur.execute("delete from launches where id=%s and user_id=%s", (launch_id, user_id))

        conn.commit()


# Lançamentos "da conta corrente" no sentido do produto: SÓ `despesa` e `receita`.
# Isso já cobre os pagamentos de fatura (gravados como tipo='despesa') e exclui
# TODO o ciclo de vida de caixinha/investimento, que usa tipos próprios
# (deposito_caixinha, aporte_investimento, criar_caixinha, create_investment,
# saque_caixinha, resgate_investimento...).
#
# NÃO use `is_internal_movement = false`: a CRIAÇÃO de caixinha/investimento gera
# um launch com is_internal_movement=false, e apagá-lo deleta a caixinha/o
# investimento junto (efeitos.create_pocket → delete from pockets). O filtro por
# tipo evita essa armadilha. Validado no staging: 0 launches despesa/receita
# carregam efeitos de caixinha/investimento.
#
# Usado por count_launches e delete_all_launches_and_rollback pra ficarem
# consistentes (o que se conta é o que se apaga).
_CONTA_CORRENTE_LAUNCH_FILTER = "tipo in ('despesa', 'receita')"


def count_launches(user_id: int) -> int:
    """Conta os lançamentos da conta corrente (despesas/receitas + pagamentos de
    fatura) — o conjunto que `delete_all_launches_and_rollback` apaga. NÃO conta
    movimentação interna de caixinha/investimento."""
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from launches "
                f"where user_id=%s and {_CONTA_CORRENTE_LAUNCH_FILTER}",
                (user_id,),
            )
            row = cur.fetchone()
            return int(row["n"]) if row else 0


def delete_all_launches_and_rollback(user_id: int) -> dict:
    """Apaga os lançamentos da CONTA CORRENTE (despesas/receitas) e desfaz
    pagamentos de fatura, revertendo os efeitos de cada um no saldo da conta.

    NÃO toca em caixinhas nem investimentos: o depósito/saque de caixinha e o
    aporte/resgate de investimento são `is_internal_movement=true` sem `bill_id`,
    então ficam de fora do filtro — seus saldos e registros permanecem intactos.
    (Sem esse filtro, "apagar tudo" zerava caixinhas/investimentos junto e dava
    a sensação de resetar o usuário do zero.)

    Reusa `delete_launch_and_rollback` linha a linha (em vez de um `delete`
    em massa) porque cada lançamento guarda seus efeitos colaterais no jsonb
    `efeitos` — saldo da conta e reabertura de fatura. Apagar em massa sem
    reverter deixaria esses saldos inconsistentes.

    Ordena por `id desc` (mais novo primeiro) por segurança em reversões
    encadeadas (ex.: múltiplos pagamentos da mesma fatura).

    Retorna {"deleted": N, "kept_no_effects": [...], "kept_unsafe": [...],
    "errors": [...], "remaining": M | None}, com os ids em `user_seq` (o "#N"
    que o usuário vê).

    As três listas são causas DIFERENTES e a mensagem ao usuário precisa
    distingui-las: `kept_no_effects` são lançamentos antigos sem `efeitos`;
    `kept_unsafe` tem `efeitos`, mas não dá pra revertê-lo por inteiro
    (degenerado, chave desconhecida, lote ausente, ou fora do escopo do
    "apagar tudo") — a sub-causa vai pro LOG como um CÓDIGO ENUMERADO
    (`motivo=`, atributo da exceção; nunca a mensagem dela), não pro usuário,
    que não pode agir sobre ela;
    `errors` é falha técnica inesperada (conexão, deadlock, permissão, bug). O
    `failed` único de antes dizia "lançamento antigo" para um banco caído.

    A classificação é do `delete_launch_and_rollback`, não daqui: separar
    `efeitos is null` ANTES do loop mantinha a MESMA regra em dois lugares que
    podiam divergir — e divergiram (o `{}` moderno passava aqui e era apagado
    lá dentro com delta 0). Custo de colapsar: um lançamento sem `efeitos` abre
    e aborta uma transação. Com exceções tipadas, a mensagem ao usuário não
    depende de string-sniffing de jeito nenhum.

    `remaining` é uma RECONTAGEM depois do loop: é a única checagem que pega o
    caso em que o delete casou zero linhas e ninguém levantou. É uma
    CONFERÊNCIA, não um fato do trabalho — se ela mesma falhar (blip de
    conexão, timeout de pool), vem `None` ("não conferi") e o que já foi
    apagado continua sendo relatado. Deixar a exceção subir daqui fazia o
    usuário ler "não consegui apagar" DEPOIS de tudo apagado.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, user_seq from launches "
                f"where user_id=%s and {_CONTA_CORRENTE_LAUNCH_FILTER} "
                "order by id desc",
                (user_id,),
            )
            rows = cur.fetchall()

    candidatos = [(r["id"], r["user_seq"] or r["id"]) for r in rows]

    deleted = 0
    kept_no_effects: list = []
    kept_unsafe: list = []
    errors: list = []
    for lid, seq in candidatos:
        try:
            delete_launch_and_rollback(user_id, lid, escopo_conta_corrente=True)
            deleted += 1
        except LaunchNoEffects:
            kept_no_effects.append(seq)
        except (LaunchUnsafeRollback, InvestmentLotHasWithdrawal) as e:
            # Condição de DOMÍNIO, não incidente: warning (o `_DashboardHandler`
            # espelha ERROR em `backend_errors_24h`). O usuário lê uma frase só,
            # porque não pode agir na diferença entre "chave desconhecida" e
            # "efeitos degenerado".
            # Sem `str(e)`, igual aos outros logs deste arquivo: hoje as
            # mensagens destas exceções são texto nosso, mas nada prende essa
            # invariante — um `raise LaunchUnsafeRollback(f"... {row[...]}")`
            # amanhã persistiria dado do cliente em `system_event_logs`, e a
            # guarda por `ast` só olha `com_traceback`. Qual das 5 recusas
            # disparou vem no `motivo=`: CÓDIGO CURTO ENUMERADO que nasce no
            # `raise` (atributo da exceção), nunca inferido da mensagem. Sem
            # ele as cinco colapsavam num `causa=LaunchUnsafeRollback` só e a
            # comum (`lote_ausente`) ficava igual à rara e grave
            # (`chave_desconhecida`). Os valores estão na docstring de
            # `LaunchUnsafeRollback`; `InvestmentLotHasWithdrawal` traz o dela
            # como atributo de classe.
            kept_unsafe.append(seq)
            logger.warning(
                "delete_all_launches: mantido sem reverter user_id=%s launch_id=%s user_seq=%s causa=%s motivo=%s",
                user_id, lid, seq, type(e).__name__, e.motivo,
                extra={"user_id": user_id},
            )
        except Exception as e:
            errors.append(seq)
            # Sem str(e) e sem exc_info: o texto do psycopg pode trazer o valor
            # da linha que violou a constraint. Nome do tipo + sqlstate já
            # separam conexão (08006), deadlock (40P01), permissão (42501) e
            # bug de código (TypeError/AttributeError).
            # launch_id (interno) E user_seq: a queixa do usuário cita "#2",
            # o log cita 19616 — sem os dois, suporte não correlaciona.
            logger.error(
                "delete_all_launches: falha inesperada user_id=%s launch_id=%s user_seq=%s causa=%s sqlstate=%s",
                user_id, lid, seq, type(e).__name__, getattr(e, "sqlstate", None),
                extra={"user_id": user_id},
            )

    try:
        remaining = count_launches(user_id)
    except Exception as e:
        remaining = None
        logger.error(
            "delete_all_launches: recontagem falhou user_id=%s causa=%s sqlstate=%s",
            user_id, type(e).__name__, getattr(e, "sqlstate", None),
            extra={"user_id": user_id},
        )

    return {
        "deleted": deleted,
        "kept_no_effects": kept_no_effects,
        "kept_unsafe": kept_unsafe,
        "errors": errors,
        "remaining": remaining,
    }


# ──────────────────────────────────────────────────────────────────────────────
# OFX import (idempotente)
# ──────────────────────────────────────────────────────────────────────────────

def get_ofx_import_by_hash(user_id: int, file_hash: str):
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select file_hash, dt_start, dt_end, total_transactions,
                       inserted_count, duplicate_count, imported_at
                from ofx_imports
                where user_id=%s and file_hash=%s
                """,
                (user_id, file_hash),
            )
            return cur.fetchone()


def import_ofx_launches_bulk(
    user_id: int,
    launches_rows: list[dict],
    *,
    file_hash: str,
    bank_id: str | None,
    acct_id: str | None,
    acct_type: str | None,
    dt_start: date | None,
    dt_end: date | None,
):
    """
    Importa transações OFX de forma IDEMPOTENTE (ON CONFLICT DO NOTHING).
    Saldo só é ajustado pelas transações efetivamente inseridas.
    """
    ensure_user(user_id)
    total = len(launches_rows)

    prev = get_ofx_import_by_hash(user_id, file_hash)
    if prev:
        bal = get_balance(user_id)
        return {
            "skipped_same_file": True,
            "total": prev["total_transactions"],
            "inserted": prev["inserted_count"],
            "duplicates": prev["duplicate_count"],
            "dt_start": prev["dt_start"],
            "dt_end": prev["dt_end"],
            "new_balance": bal,
            "imported_at": prev["imported_at"],
        }

    inserted = 0
    duplicates = 0
    delta_total = Decimal("0")

    with get_conn() as conn:
        with conn.cursor() as cur:
            for r in launches_rows:
                cur.execute(
                    """
                    insert into launches(
                        user_id, tipo, valor, categoria, alvo, nota, criado_em, efeitos,
                        source, external_id, posted_at, currency, imported_at, is_internal_movement
                    )
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),%s)
                    on conflict (user_id, source, external_id) do nothing
                    """,
                    (
                        user_id, r["tipo"], r["valor"], r.get("categoria"), r.get("alvo"), r.get("nota"),
                        r["criado_em"],
                        Json({"delta_conta": float(r["delta"]), "ofx": r.get("ofx_meta", {})}),
                        "ofx", r["external_id"], r.get("posted_at"), r.get("currency", "BRL"),
                        r.get("is_internal_movement", False),
                    ),
                )
                if (cur.rowcount or 0) == 1:
                    inserted += 1
                    delta_total += r["delta"]
                else:
                    duplicates += 1

            if inserted:
                cur.execute(
                    "update accounts set balance = balance + %s where user_id=%s returning balance",
                    (delta_total, user_id),
                )
                new_bal = cur.fetchone()["balance"]
            else:
                cur.execute("select balance from accounts where user_id=%s", (user_id,))
                new_bal = cur.fetchone()["balance"]

            cur.execute(
                """
                insert into ofx_imports(
                    user_id, file_hash, bank_id, acct_id, acct_type,
                    dt_start, dt_end, total_transactions, inserted_count, duplicate_count
                )
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing
                """,
                (user_id, file_hash, bank_id, acct_id, acct_type,
                 dt_start, dt_end, total, inserted, duplicates),
            )

        conn.commit()

    return {
        "skipped_same_file": False,
        "total": total,
        "inserted": inserted,
        "duplicates": duplicates,
        "dt_start": dt_start,
        "dt_end": dt_end,
        "new_balance": new_bal,
    }
