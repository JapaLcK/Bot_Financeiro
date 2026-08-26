"""
tests/test_budget_category_accent.py — orçamento e gasto casam mesmo com
grafias diferentes da MESMA categoria (acento e caixa).

Caso real (medido pelo Manager no Postgres local): orçamento cadastrado como
'cafe da manha', lançamento gravado como 'café da manhã' (grafia do catálogo,
que a leitura de categoria passou a devolver). O orçamento enxergava R$ 0,00,
o gasto real era R$ 50,00, e o alerta de 80% nunca disparava — silencioso.

A comparação passou a usar `cat_norm_sql` (`db/connection.py:93`), que é
insensível a caixa E a acento. Controle positivo obrigatório: categorias
genuinamente diferentes ('cafe' × 'carne') continuam separadas.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import db
from core.budget_alerts import evaluate_after_expense
from db.accounts import add_launch_and_update_balance
from db.analytics import compute_categories
from db.budgets import (
    get_budgets_status_for_month,
    sum_spent_in_category_this_month,
    upsert_budget,
)
import frontend.finance_bot_websocket_custom as dashboard


def _gasto(user_id: int, categoria: str, valor: float):
    add_launch_and_update_balance(
        user_id, "despesa", valor, "teste", "teste", categoria=categoria
    )


# ── o cenário do Manager ────────────────────────────────────────────────────

def test_orcamento_sem_acento_conta_gasto_com_acento(user_id):
    """Controle negativo: revertendo `_CAT_EQ` para `lower(categoria)=lower(%s)`
    em `db/budgets.py`, `spent` volta a 0.0 e as três asserções caem."""
    upsert_budget(user_id, "cafe da manha", 200.0)
    _gasto(user_id, "café da manhã", 50.0)

    assert sum_spent_in_category_this_month(user_id, "cafe da manha") == 50.0

    status = get_budgets_status_for_month(user_id)
    linha = [b for b in status["budgets"] if b["categoria"] == "cafe da manha"]
    assert len(linha) == 1, status["budgets"]
    assert linha[0]["spent"] == 50.0
    assert status["totals"]["spent"] == 50.0


def test_alerta_dispara_com_as_duas_grafias(user_id):
    """Threshold de 80% (`core/budget_alerts.THRESHOLDS`) cruzado quando o gasto
    entra na grafia acentuada e o orçamento está na grafia sem acento — e
    vice-versa, na segunda metade."""
    upsert_budget(user_id, "cafe da manha", 100.0)
    _gasto(user_id, "café da manhã", 90.0)
    alerta = evaluate_after_expense(
        user_id, "café da manhã", 90.0, datetime.now(timezone.utc)
    )
    assert alerta is not None and alerta.threshold == 80

    # grafias trocadas de lado: orçamento COM acento, gasto SEM
    upsert_budget(user_id, "pizzaría", 100.0)
    _gasto(user_id, "pizzaria", 90.0)
    alerta2 = evaluate_after_expense(
        user_id, "pizzaria", 90.0, datetime.now(timezone.utc)
    )
    assert alerta2 is not None and alerta2.threshold == 80


def test_donut_nao_duplica_a_mesma_categoria(user_id):
    """As duas grafias viram UMA fatia, com o total somado, e o orçamento
    cadastrado na terceira grafia (caixa alta) gruda nessa fatia."""
    upsert_budget(user_id, "CAFE DA MANHA", 100.0)
    _gasto(user_id, "cafe da manha", 30.0)
    _gasto(user_id, "café da manhã", 60.0)

    data = asyncio.run(dashboard.get_financial_data(user_id))
    fatias = [
        c for c in data["expense_categories"]
        if c["categoria"].lower().startswith(("cafe", "café"))
    ]
    assert len(fatias) == 1, data["expense_categories"]
    assert fatias[0]["total"] == 90.0
    # rótulo = grafia do lançamento MAIS RECENTE
    assert fatias[0]["categoria"] == "café da manhã"
    assert fatias[0]["budget"] == 100.0
    assert fatias[0]["budget_pct"] == 90.0
    assert "cat_key" not in fatias[0]
    # o alerta de 85% da dashboard (mesmo laço) só aparece se o orçamento grudou
    assert [a["type"] for a in data["alerts"]] == ["budget_warning"]

    # o outro donut (aba Análises, `db/analytics.py`) tem que agrupar igual
    hoje = date.today()
    analytics = compute_categories(user_id, hoje.replace(day=1), hoje + timedelta(days=1))
    cafes = [c for c in analytics if c["name"].lower().startswith(("cafe", "café"))]
    assert len(cafes) == 1, analytics
    assert cafes[0]["total"] == 90.0 and cafes[0]["name"] == "café da manhã"


# ── controle positivo: o normalizador não pode colapsar o que não é gêmeo ────

def test_categorias_diferentes_continuam_separadas(user_id):
    upsert_budget(user_id, "cafe", 100.0)
    upsert_budget(user_id, "carne", 100.0)
    _gasto(user_id, "cafe", 10.0)
    _gasto(user_id, "carne", 70.0)

    por_cat = {b["categoria"]: b["spent"] for b in get_budgets_status_for_month(user_id)["budgets"]}
    assert por_cat == {"cafe": 10.0, "carne": 70.0}
    assert sum_spent_in_category_this_month(user_id, "cafe") == 10.0

    data = asyncio.run(dashboard.get_financial_data(user_id))
    fatias = {c["categoria"]: c["total"] for c in data["expense_categories"]}
    assert fatias == {"cafe": 10.0, "carne": 70.0}
    hoje = date.today()
    analytics = compute_categories(user_id, hoje.replace(day=1), hoje + timedelta(days=1))
    assert {c["name"]: c["total"] for c in analytics} == {"cafe": 10.0, "carne": 70.0}

    # e o alerta de uma não pode ser disparado pelo gasto da outra
    assert evaluate_after_expense(
        user_id, "cafe", 10.0, datetime.now(timezone.utc)
    ) is None


def test_crud_de_orcamento_acha_a_linha_com_a_outra_grafia(user_id):
    """`upsert_budget` não pode criar orçamento gêmeo: 'café' cai na linha
    'cafe' que já existe, mantendo a grafia original."""
    upsert_budget(user_id, "cafe", 100.0)
    canon, created = upsert_budget(user_id, "Café", 300.0)
    assert (canon, created) == ("cafe", False)
    assert db.budgets.get_budget(user_id, "CAFÉ") == {"categoria": "cafe", "budget": 300.0}
    assert len(db.budgets.list_budgets(user_id)) == 1
    assert db.budgets.delete_budget(user_id, "café") is True
    assert db.budgets.list_budgets(user_id) == []


# ── catálogo com gêmeas de acento: join normalizado não pode multiplicar ─────
# `user_categories` é única só no par EXATO (user_id, name), então 'cafe' e
# 'café' coexistem em dado legado. Com o join de emoji/cor feito por valor
# NORMALIZADO, as duas casavam com o mesmo orçamento/fatia e dobravam
# `total_budget`, `total_spent` e `at_risk` (P1 do Codex no PR #143).

def _catalogo(user_id: int, *linhas: tuple[str, str, bool]):
    """(name, emoji, is_system) direto no catálogo.

    Direto em SQL de propósito: `ensure_user_category` não cria mais gêmea —
    quem tem duas grafias é dado gravado antes da normalização.
    """
    from db.connection import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            for name, emoji, is_system in linhas:
                cur.execute(
                    "insert into user_categories (user_id, name, emoji, color, is_system) "
                    "values (%s, %s, %s, '#123456', %s)",
                    (user_id, name, emoji, is_system),
                )
        conn.commit()


def test_catalogo_com_gemeas_nao_dobra_orcamento(user_id):
    """Controle negativo: trocando a CTE `cat_meta` de volta pelo
    `left join user_categories uc on uc.user_id=%s and <norm> = <norm>`
    (db/budgets.py, db/analytics.py), medido no Postgres local:
    2 linhas, budget 80.0, spent 74.2, at_risk 2, e o donut com duas fatias
    de 37.1 (pct 50.0 cada)."""
    _catalogo(user_id, ("cafe da manha", "🅰️", False), ("café da manhã", "🅱️", False))
    upsert_budget(user_id, "cafe da manha", 40.0)
    _gasto(user_id, "café da manhã", 37.10)

    status = get_budgets_status_for_month(user_id)
    assert len(status["budgets"]) == 1, status["budgets"]
    linha = status["budgets"][0]
    assert (linha["budget"], linha["spent"]) == (40.0, 37.1)
    assert linha["status"] == "amarelo"
    assert linha["emoji"] == "🅰️"  # metadado veio, não sumiu com a dedup
    assert status["totals"]["budget"] == 40.0
    assert status["totals"]["spent"] == 37.1
    assert status["totals"]["at_risk"] == 1

    hoje = date.today()
    cats = compute_categories(user_id, hoje.replace(day=1), hoje + timedelta(days=1))
    assert len(cats) == 1, cats
    assert (cats[0]["total"], cats[0]["pct"], cats[0]["emoji"]) == (37.1, 100.0, "🅰️")


def test_desempate_do_catalogo_e_o_mesmo_do_display_map(user_id):
    """Uma fonte só pro desempate: vence a do seed (is_system) e, entre iguais,
    o menor nome — igual a `user_category_display_map` (db/categories.py)."""
    _catalogo(user_id, ("cafe", "🅰️", False), ("café", "🅱️", True))
    upsert_budget(user_id, "CAFE", 40.0)
    _gasto(user_id, "Café", 10.0)

    assert db.categories.user_category_display_map(user_id)["cafe"] == "café"
    linhas = get_budgets_status_for_month(user_id)["budgets"]
    assert len(linhas) == 1 and linhas[0]["emoji"] == "🅱️"

    hoje = date.today()
    cats = compute_categories(user_id, hoje.replace(day=1), hoje + timedelta(days=1))
    assert len(cats) == 1 and cats[0]["emoji"] == "🅱️"


def test_catalogo_normal_continua_trazendo_emoji_de_cada_categoria(user_id):
    """Controle positivo: sem gêmeas, cada categoria mantém a linha e o
    metadado dela — a dedup não pode colapsar catálogo legítimo."""
    _catalogo(user_id, ("cafe", "☕", False), ("carne", "🥩", False))
    upsert_budget(user_id, "cafe", 100.0)
    upsert_budget(user_id, "carne", 100.0)
    _gasto(user_id, "cafe", 11.0)
    _gasto(user_id, "carne", 73.0)

    linhas = get_budgets_status_for_month(user_id)["budgets"]
    assert {b["categoria"]: (b["spent"], b["emoji"]) for b in linhas} == {
        "cafe": (11.0, "☕"), "carne": (73.0, "🥩"),
    }
    assert get_budgets_status_for_month(user_id)["totals"]["spent"] == 84.0

    hoje = date.today()
    cats = compute_categories(user_id, hoje.replace(day=1), hoje + timedelta(days=1))
    assert {c["name"]: (c["total"], c["emoji"]) for c in cats} == {
        "cafe": (11.0, "☕"), "carne": (73.0, "🥩"),
    }


# ── orçamentos gêmeos legados: 2 linhas, mas o gasto conta UMA vez no total ──
# `category_budgets` também é única só no par EXATO (user_id, categoria), então
# 'cafe' e 'café' coexistem em dado gravado antes da normalização. Com o gasto
# casado por valor normalizado (c48f554), o MESMO gasto entra nas duas linhas e
# dobrava `totals.spent`/`at_risk`. Decisão do dono: as duas linhas continuam,
# cada uma com o limite que o usuário criou; só o TOTAL conta o gasto uma vez.

def _orcamento_bruto(user_id: int, *linhas: tuple[str, float]):
    """Insere direto: `upsert_budget` não cria mais gêmea (cai na existente)."""
    from db.connection import get_conn

    with get_conn() as conn:
        with conn.cursor() as cur:
            for cat, val in linhas:
                cur.execute(
                    "insert into category_budgets (user_id, categoria, budget) "
                    "values (%s, %s, %s)",
                    (user_id, cat, val),
                )
        conn.commit()


def test_orcamentos_gemeos_contam_o_gasto_uma_vez_no_total(user_id):
    """Controle negativo: trocando o `if r["cat_key"] not in spent_counted`
    por `total_spent += spent` incondicional (db/budgets.py), medido no
    Postgres local: totals.spent volta a 74.2 e remaining a 275.8."""
    _orcamento_bruto(user_id, ("cafe", 100.0), ("café", 250.0))
    _gasto(user_id, "café", 37.10)

    status = get_budgets_status_for_month(user_id)
    # nada é fundido: as duas linhas aparecem com o limite que o user criou
    assert [(b["categoria"], b["budget"], b["spent"]) for b in status["budgets"]] == [
        ("cafe", 100.0, 37.1), ("café", 250.0, 37.1),
    ]
    # e o total soma o gasto UMA vez (limites NÃO deduplicam: 100 + 250)
    assert status["totals"]["budget"] == 350.0
    assert status["totals"]["spent"] == 37.1
    assert status["totals"]["remaining"] == 312.9


def test_at_risk_conta_uma_vez_por_categoria_normalizada(user_id):
    """Controle negativo: voltando `at_risk` para o contador `+= 1` por linha,
    medido: at_risk 2 com uma categoria só em risco."""
    _orcamento_bruto(user_id, ("cafe", 40.0), ("café", 45.0))
    _gasto(user_id, "café", 37.10)

    status = get_budgets_status_for_month(user_id)
    assert [b["status"] for b in status["budgets"]] == ["amarelo", "amarelo"]
    assert status["totals"]["at_risk"] == 1
    assert status["totals"]["spent"] == 37.1


def test_sem_gemeas_os_totais_continuam_somando_tudo(user_id):
    """Controle positivo: a dedup não pode subtrair gasto de categorias
    genuinamente diferentes, nem esconder a segunda em risco."""
    _orcamento_bruto(user_id, ("cafe", 40.0), ("carne", 80.0))
    _gasto(user_id, "cafe", 37.10)
    _gasto(user_id, "carne", 75.0)

    totals = get_budgets_status_for_month(user_id)["totals"]
    assert totals["budget"] == 120.0
    assert totals["spent"] == 112.1
    assert totals["at_risk"] == 2


def test_get_budget_e_upsert_deterministicos_com_gemeas(user_id):
    """Com gêmeas, `fetchone()` sem `order by` devolvia a linha da ORDEM DE
    INSERÇÃO (medido: 'café' quando ela é inserida primeiro) e o UPDATE casava
    as DUAS, apagando o limite da outra (medido: 777.0 nas duas).

    Desempate = o mesmo do catálogo: menor nome alfabético → 'cafe'.
    """
    _orcamento_bruto(user_id, ("café", 250.0), ("cafe", 100.0))  # acentuada 1º

    assert db.budgets.get_budget(user_id, "CAFÉ") == {"categoria": "cafe", "budget": 100.0}

    canon, created = db.budgets.upsert_budget(user_id, "Café", 777.0)
    assert (canon, created) == ("cafe", False)
    assert {b["categoria"]: b["budget"] for b in db.budgets.list_budgets(user_id)} == {
        "cafe": 777.0, "café": 250.0,  # o limite da gêmea sobrevive
    }


# ── donut × get_budget: com gêmeas, os dois têm que mostrar o MESMO limite ───
# O SELECT de orçamentos do donut (`finance_bot_websocket_custom.py:675`) vira
# `budget_by_key = {cat_key: budget}` — dict, última linha vence. Sem `order
# by`, quem vencia era a ordem de inserção: a tela mostrava 250 e o bot
# respondia 100 pela MESMA categoria. Daí o `ORDER BY categoria DESC` lá (o
# menor nome vem por último e ganha o dict) = `CAT_CANON_ORDER` do `get_budget`.

def _donut(user_id: int) -> tuple[dict[str, float | None], list[str]]:
    """({categoria da fatia: budget exibido}, [tipos de alerta])."""
    data = asyncio.run(dashboard.get_financial_data(user_id))
    return (
        {c["categoria"]: c.get("budget") for c in data["expense_categories"]},
        [a["type"] for a in data["alerts"]],
    )


def _cenario_gemeas(user_id: int, *orcamentos: tuple[str, float]) -> None:
    """Par gêmeo ('cafe' 40 × 'café' 250) + uma categoria sem gêmea (controle
    positivo). Gasto de 37,10 cruza 80% de 40 e NÃO cruza 80% de 250."""
    _orcamento_bruto(user_id, *orcamentos)
    _orcamento_bruto(user_id, ("carne", 80.0))
    _gasto(user_id, "café", 37.10)
    _gasto(user_id, "carne", 20.0)


def _assert_gemeas_coerentes(user_id: int) -> None:
    assert db.budgets.get_budget(user_id, "CAFÉ") == {"categoria": "cafe", "budget": 40.0}
    fatias, alertas = _donut(user_id)
    # a fatia gêmea mostra o limite canônico (40), não o da outra grafia (250);
    # 'carne' é o controle positivo: sem gêmea, o limite não muda
    assert fatias == {"café": 40.0, "carne": 80.0}
    # e o alerta de 85% do donut só sai porque grudou o limite certo (92.8%)
    assert alertas == ["budget_warning"]
    # o alerta do bot (core/budget_alerts) usa o mesmo desempate
    alerta = evaluate_after_expense(user_id, "café", 37.10, datetime.now(timezone.utc))
    assert alerta is not None and (alerta.categoria, alerta.budget) == ("cafe", 40.0)


def test_donut_com_gemeas_sem_acento_inserida_primeiro(user_id):
    """Controle negativo: tirando o `ORDER BY categoria DESC` do SELECT 10 do
    donut, medido no Postgres local — 'café' vence o dict e a fatia sai com
    budget 250.0, sem alerta nenhum."""
    _cenario_gemeas(user_id, ("cafe", 40.0), ("café", 250.0))
    _assert_gemeas_coerentes(user_id)


def test_donut_com_gemeas_com_acento_inserida_primeiro(user_id):
    """Mesma asserção com a ordem de inserção invertida — é a ordem que decidia
    o vencedor antes do `ORDER BY`."""
    _cenario_gemeas(user_id, ("café", 250.0), ("cafe", 40.0))
    _assert_gemeas_coerentes(user_id)
