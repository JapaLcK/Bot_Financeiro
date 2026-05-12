"""
Cobre as 3 budget tools do Sprint 2 Bloco B:
- set_budget (com anti-typo + confirma em UPDATE)
- get_budget_status (todos ou um)
- delete_budget

Inclui os helpers DB de `db/budgets.py` indiretamente, e o fuzzy match
em `_resolve_category` (chave da feature anti-typo).
"""
from datetime import date

import db
from core.services.ai_chat.tools.budgets import (
    _delete_budget_execute,
    _delete_budget_validate,
    _get_budget_status,
    _resolve_category,
    _set_budget_execute,
)


# ─── _resolve_category (anti-typo) ──────────────────────────────────────────


def test_resolve_exact_match_devolve_canonica(user_id):
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "mercado", "compra", categoria="alimentação",
    )
    canon, action = _resolve_category(user_id, "Alimentação")
    assert action == "ok"
    assert canon == "alimentação"  # canônica do launch


def test_resolve_fuzzy_bloqueia_e_sugere(user_id):
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "x", "x", categoria="alimentação",
    )
    msg, action = _resolve_category(user_id, "alimemtacao")
    assert action == "block"
    assert "alimentação" in msg
    assert "force_new" in msg


def test_resolve_force_new_pula_fuzzy(user_id):
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "x", "x", categoria="alimentação",
    )
    canon, action = _resolve_category(user_id, "alimemtacao", force_new=True)
    assert action == "ok"
    assert canon == "alimemtacao"


def test_resolve_categoria_nova_sem_similar_aceita(user_id):
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "x", "x", categoria="alimentação",
    )
    canon, action = _resolve_category(user_id, "viagem")
    assert action == "ok"
    assert canon == "viagem"


def test_resolve_match_com_budget_existente(user_id):
    """Se já tem orçamento em X mas o user nunca registrou gasto em X,
    o catálogo deve incluir X — não pode bloquear como typo."""
    db.upsert_budget(user_id, "viagem", 1000)
    canon, action = _resolve_category(user_id, "viagem")
    assert action == "ok"
    assert canon == "viagem"


def test_resolve_categoria_vazia_bloqueia(user_id):
    msg, action = _resolve_category(user_id, "")
    assert action == "block"
    assert "categoria" in msg.lower()


# ─── set_budget ─────────────────────────────────────────────────────────────


def test_set_budget_create_categoria_existente_auto_executa(user_id):
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "x", "x", categoria="alimentação",
    )
    msg = _set_budget_execute(user_id, {"categoria": "alimentação", "budget": 500})
    assert "criado" in msg.lower() or "criar" in msg.lower() or "✅" in msg
    assert db.get_budget(user_id, "alimentação")["budget"] == 500.0


def test_set_budget_create_categoria_nova_legitima(user_id):
    """Categoria sem nada parecido no catálogo é aceita direto."""
    msg = _set_budget_execute(user_id, {"categoria": "viagem", "budget": 1000})
    assert "✅" in msg
    assert db.get_budget(user_id, "viagem")["budget"] == 1000.0


def test_set_budget_typo_bloqueia(user_id):
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "x", "x", categoria="alimentação",
    )
    msg = _set_budget_execute(user_id, {"categoria": "alimemtacao", "budget": 500})
    assert "Você quis dizer" in msg
    # Nada foi salvo
    assert db.get_budget(user_id, "alimemtacao") is None


def test_set_budget_force_new_pula_check(user_id):
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "x", "x", categoria="alimentação",
    )
    msg = _set_budget_execute(
        user_id, {"categoria": "alimemtacao", "budget": 500, "force_new": True},
    )
    assert "✅" in msg
    assert db.get_budget(user_id, "alimemtacao")["budget"] == 500.0


def test_set_budget_update_pede_confirmacao(user_id):
    db.upsert_budget(user_id, "alimentação", 500)
    msg = _set_budget_execute(user_id, {"categoria": "alimentação", "budget": 800})
    assert "Confirma" in msg
    # NÃO atualizou ainda — virou pending
    assert db.get_budget(user_id, "alimentação")["budget"] == 500.0
    pending = db.ai_get_pending_action(user_id)
    assert pending is not None
    assert pending["tool_name"] == "set_budget"
    assert pending["tool_args"]["_confirmed"] is True


def test_set_budget_confirmed_executa(user_id):
    db.upsert_budget(user_id, "alimentação", 500)
    msg = _set_budget_execute(
        user_id, {"categoria": "alimentação", "budget": 800, "_confirmed": True},
    )
    assert "✅" in msg
    assert db.get_budget(user_id, "alimentação")["budget"] == 800.0


def test_set_budget_budget_zero_rejeita(user_id):
    msg = _set_budget_execute(user_id, {"categoria": "x", "budget": 0})
    assert "maior que zero" in msg


# ─── get_budget_status ──────────────────────────────────────────────────────


def test_get_budget_status_vazio(user_id):
    out = _get_budget_status(user_id, {})
    assert out == {"budgets": [], "count": 0}


def test_get_budget_status_todos_com_gasto(user_id):
    db.upsert_budget(user_id, "alimentação", 500)
    db.upsert_budget(user_id, "lazer", 300)
    db.add_launch_and_update_balance(
        user_id, "despesa", 100, "x", "x", categoria="alimentação",
    )

    out = _get_budget_status(user_id, {})
    assert out["count"] == 2
    by_cat = {b["categoria"]: b for b in out["budgets"]}
    assert by_cat["alimentação"]["spent"] == 100.0
    assert by_cat["alimentação"]["pct"] == 20.0
    assert by_cat["alimentação"]["status"] == "ok"
    assert by_cat["lazer"]["spent"] == 0.0


def test_get_budget_status_alerta_e_estourado(user_id):
    db.upsert_budget(user_id, "lazer", 100)
    # gasto = 95 → pct = 95 → alerta
    db.add_launch_and_update_balance(
        user_id, "despesa", 95, "x", "x", categoria="lazer",
    )
    out = _get_budget_status(user_id, {"categoria": "lazer"})
    assert out["status"] == "alerta"

    # gasto +20 → pct 115 → estourado
    db.add_launch_and_update_balance(
        user_id, "despesa", 20, "y", "y", categoria="lazer",
    )
    out = _get_budget_status(user_id, {"categoria": "lazer"})
    assert out["status"] == "estourado"


def test_get_budget_status_categoria_sem_budget(user_id):
    out = _get_budget_status(user_id, {"categoria": "transporte"})
    assert out["found"] is False
    assert "hint" in out


def test_get_budget_status_categoria_com_typo_da_hint(user_id):
    db.add_launch_and_update_balance(
        user_id, "despesa", 10, "x", "x", categoria="alimentação",
    )
    out = _get_budget_status(user_id, {"categoria": "alimemtacao"})
    assert out["found"] is False
    assert "alimentação" in out.get("hint", "")


# ─── delete_budget ──────────────────────────────────────────────────────────


def test_delete_budget_validate_bloqueia_sem_orcamento(user_id):
    err = _delete_budget_validate(user_id, {"categoria": "lazer"})
    assert err is not None
    assert "não tem orçamento" in err.lower() or "não tem orçamento" in err


def test_delete_budget_executa(user_id):
    db.upsert_budget(user_id, "lazer", 300)
    args = {"categoria": "lazer"}
    err = _delete_budget_validate(user_id, args)
    assert err is None
    msg = _delete_budget_execute(user_id, args)
    assert "✅" in msg
    assert db.get_budget(user_id, "lazer") is None


def test_delete_budget_normaliza_case(user_id):
    """User digita 'LAZER', validate normaliza pra 'lazer'."""
    db.upsert_budget(user_id, "lazer", 300)
    args = {"categoria": "LAZER"}
    err = _delete_budget_validate(user_id, args)
    assert err is None
    assert args["categoria"] == "lazer"  # mutado in-place


# ─── db.sum_spent_in_category_this_month ────────────────────────────────────


def test_sum_spent_soma_launches_e_credito(user_id):
    """Gasto na conta + compra no cartão na mesma categoria somam pro orçamento."""
    db.add_launch_and_update_balance(
        user_id, "despesa", 50, "x", "x", categoria="alimentação",
    )
    card_id = db.create_card(user_id, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(user_id, card_id, 30, "alimentação", "ifood", date.today())

    total = db.sum_spent_in_category_this_month(user_id, "alimentação")
    assert total == 80.0


def test_sum_spent_filtra_internal_movement(user_id):
    """Aporte de investimento não conta como gasto pro orçamento."""
    db.add_launch_and_update_balance(
        user_id, "despesa", 100, "aporte", "aporte",
        categoria="investimento_aporte", is_internal_movement=True,
    )
    total = db.sum_spent_in_category_this_month(user_id, "investimento_aporte")
    assert total == 0.0
