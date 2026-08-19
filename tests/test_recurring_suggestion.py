"""
Detecção "despesa que se repete → sugere gasto fixo".
Cobre find_recurring_candidate (repetição/guards), o gate de categoria em
_maybe_recurring_offer e o resolve do sim/não em resolve_delete.
"""
from datetime import datetime

from utils_date import _tz
from db.accounts import add_launch_and_update_balance
from db.recurring import (
    find_recurring_candidate,
    dismiss_recurring_suggestion,
    create_recurring_expense,
    list_recurring_expenses,
)


def _add(user_id, valor, alvo, categoria, ano, mes, dia=10):
    return add_launch_and_update_balance(
        user_id, "despesa", valor, alvo, alvo, categoria,
        criado_em=datetime(ano, mes, dia, 12, 0, tzinfo=_tz()),
    )


def test_repeticao_em_meses_distintos_vira_candidata(user_id):
    _add(user_id, 39.90, "Netflix", "assinaturas", 2026, 6)
    _add(user_id, 39.90, "Netflix", "assinaturas", 2026, 7)
    # nenhum lançamento no mês "atual" (agosto) → conta 2 meses anteriores
    n = find_recurring_candidate(
        user_id, "netflix", 39.90, current_year=2026, current_month=8,
    )
    assert n >= 1


def test_mesmo_mes_nao_e_candidata(user_id):
    _add(user_id, 25.00, "Cafe", "alimentação", 2026, 8, dia=1)
    _add(user_id, 25.00, "Cafe", "alimentação", 2026, 8, dia=2)
    n = find_recurring_candidate(
        user_id, "cafe", 25.00, current_year=2026, current_month=8,
    )
    assert n == 0


def test_valor_diferente_nao_casa(user_id):
    _add(user_id, 44.90, "Netflix", "assinaturas", 2026, 6)          # valor antigo
    r_now = _add(user_id, 39.90, "Netflix", "assinaturas", 2026, 8, dia=4)  # atual
    # o valor atual (39.90) não tem ocorrência anterior → não repete
    n = find_recurring_candidate(
        user_id, "netflix", 39.90, current_year=2026, current_month=8,
        exclude_launch_id=r_now[0],
    )
    assert n == 0


def test_recusado_nao_reaparece(user_id):
    _add(user_id, 99.00, "Academia", "saúde", 2026, 5)
    _add(user_id, 99.00, "Academia", "saúde", 2026, 6)
    assert find_recurring_candidate(user_id, "academia", 99.00, current_year=2026, current_month=8) >= 1
    dismiss_recurring_suggestion(user_id, "academia", 99.00)
    assert find_recurring_candidate(user_id, "academia", 99.00, current_year=2026, current_month=8) == 0


def test_ja_e_gasto_fixo_nao_sugere(user_id):
    _add(user_id, 120.00, "Escola", "educação", 2026, 5)
    _add(user_id, 120.00, "Escola", "educação", 2026, 6)
    create_recurring_expense(user_id, "Escola", 120.00, "educação", 10, "account")
    assert find_recurring_candidate(user_id, "escola", 120.00, current_year=2026, current_month=8) == 0


def test_gate_categoria_blocklist(user_id):
    from core.handlers.launches import _maybe_recurring_offer
    # mercado está na blocklist → nunca oferece, mesmo repetindo
    _add(user_id, 300.00, "Supermercado X", "mercado", 2026, 6)
    _add(user_id, 300.00, "Supermercado X", "mercado", 2026, 7)
    offer = _maybe_recurring_offer(user_id, "Supermercado X", 300.00, "mercado", None, None)
    assert offer is None


def test_gate_categoria_elegivel_gera_oferta(user_id):
    from core.handlers.launches import _maybe_recurring_offer
    _add(user_id, 89.90, "Internet Vivo", "moradia", 2026, 6)
    _add(user_id, 89.90, "Internet Vivo", "moradia", 2026, 7)
    offer = _maybe_recurring_offer(user_id, "Internet Vivo", 89.90, "moradia", None, None)
    assert offer is not None
    assert offer["merchant_key"] == "internet vivo"
    assert offer["amount"] == 89.90
    assert offer["category"] == "moradia"


def test_resolve_sim_cria_gasto_fixo(user_id):
    from core.handlers.pending import resolve_delete
    import db
    db.set_pending_action(user_id, "confirm_recurring_offer", {
        "name": "Spotify", "amount": 21.90, "category": "assinaturas",
        "due_day": 15, "merchant_key": "spotify",
    })
    resp = resolve_delete(user_id, confirmed=True)
    assert resp and "gasto fixo" in resp.lower()
    fixos = list_recurring_expenses(user_id)
    assert any(f["name"] == "Spotify" and abs(f["amount"] - 21.90) < 0.001 for f in fixos)


def test_resolve_nao_registra_recusa(user_id):
    from core.handlers.pending import resolve_delete
    import db
    _add(user_id, 55.00, "Revista", "assinaturas", 2026, 5)
    _add(user_id, 55.00, "Revista", "assinaturas", 2026, 6)
    db.set_pending_action(user_id, "confirm_recurring_offer", {
        "name": "Revista", "amount": 55.00, "category": "assinaturas",
        "due_day": 10, "merchant_key": "revista",
    })
    resp = resolve_delete(user_id, confirmed=False)
    assert resp is not None
    # a recusa foi persistida → não sugere de novo
    assert find_recurring_candidate(user_id, "revista", 55.00, current_year=2026, current_month=8) == 0


def test_resolve_sim_nao_dobra_cobranca_no_mesmo_mes(user_id):
    """A despesa deste mês que dispara a oferta já foi lançada; ao aceitar, o
    gasto fixo autopay (due_day=hoje) NÃO pode debitar de novo no mesmo dia.
    Deve nascer com o mês corrente já marcado como cobrado."""
    from core.handlers.pending import resolve_delete
    from core.services.recurring_charger import charge_due_recurring_expenses_once
    from utils_date import today_tz
    import db

    today = today_tz()
    db.set_pending_action(user_id, "confirm_recurring_offer", {
        "name": "Aluguel", "amount": 1500.00, "category": "moradia",
        "due_day": today.day, "merchant_key": "aluguel",
    })
    resolve_delete(user_id, confirmed=True)

    fixos = list_recurring_expenses(user_id)
    assert fixos and fixos[0]["last_charged_ym"] == today.strftime("%Y-%m")

    before = len(db.list_launches(user_id, limit=50))
    charge_due_recurring_expenses_once(today)      # cron de hoje
    after = len(db.list_launches(user_id, limit=50))
    assert after == before                          # não debitou de novo
