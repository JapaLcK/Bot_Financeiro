"""
tests/test_household_budget.py — Orçamento Doméstico (método dos potes).

Cobre `db/household_budget.py` e as rotas `/household-budget/...`: seed lazy
dos defaults (soma 100); save_config (validações + tudo-ou-nada); renda
computada × override × limpeza e RENDA_INVALIDA; gasto por pote (launch +
cartão, interno excluído EXCETO aporte, fallback 'conforto'); budget_amount e
used_pct (None com renda 0); validação de month; caixinha/cripto/aporte nos potes + lista por linha (soma == spent, sem vazar
entre usuários); rotas 403 Free / 200 Pro /
PUT income com amount null pelo HTTP de verdade.
"""
from __future__ import annotations

from datetime import date

import uuid

import pytest

from conftest import promote_to_pro
from db.accounts import add_launch_and_update_balance
from db.connection import get_conn
from db.investments import create_investment, investment_deposit_from_account
from db.pockets import create_pocket, pocket_deposit_from_account, pocket_withdraw_to_account
from db.users import ensure_user
from db.household_budget import (
    BUCKETS,
    clear_income_override,
    get_config,
    get_household_budget_status,
    get_monthly_income,
    save_config,
    set_income_override,
)


def _mes_atual() -> str:
    hoje = date.today()
    return f"{hoje.year:04d}-{hoje.month:02d}"


def _gasto(user_id: int, categoria: str, valor: float, interno: bool = False):
    add_launch_and_update_balance(
        user_id, "despesa", valor, "teste", "teste",
        categoria=categoria, is_internal_movement=interno,
    )


def _receita(user_id: int, valor: float):
    add_launch_and_update_balance(user_id, "receita", valor, "teste", "teste")


def _gasto_cartao(user_id: int, categoria: str, valor: float, estorno: float = 0):
    """Compra no cartão atribuída ao mês pela fatura (period_end = hoje)."""
    hoje = date.today()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into credit_cards (user_id, name, closing_day, due_day) "
                "values (%s, 'Cartão', 1, 10) returning id",
                (user_id,),
            )
            card = cur.fetchone()["id"]
            cur.execute(
                "insert into credit_bills (user_id, card_id, period_start, period_end) "
                "values (%s, %s, %s, %s) returning id",
                (user_id, card, hoje, hoje),
            )
            bill = cur.fetchone()["id"]
            cur.execute(
                "insert into credit_transactions "
                "(bill_id, user_id, card_id, valor, purchased_at, categoria, is_refund) "
                "values (%s, %s, %s, %s, %s, %s, %s)",
                (bill, user_id, card, valor, hoje, categoria, False),
            )
            if estorno:  # estorno na mesma fatura
                cur.execute(
                    "insert into credit_transactions "
                    "(bill_id, user_id, card_id, valor, purchased_at, categoria, is_refund) "
                    "values (%s, %s, %s, %s, %s, %s, true)",
                    (bill, user_id, card, estorno, hoje, categoria),
                )
        conn.commit()


def _config_valida(**overrides) -> dict[str, float]:
    cfg = {b["key"]: float(b["default_pct"]) for b in BUCKETS}
    cfg.update(overrides)
    return cfg


def _bucket(status: dict, key: str) -> dict:
    return next(b for b in status["buckets"] if b["key"] == key)


# ── config: seed lazy e validações ───────────────────────────────────────────

def test_primeira_leitura_semeia_defaults_somando_100(user_id):
    cfg = get_config(user_id)
    assert set(cfg) == {b["key"] for b in BUCKETS}
    assert sum(cfg.values()) == 100.0
    # defaults inspirados no método dos potes: custos fixos é o maior
    assert cfg["custos_fixos"] == 55.0


def test_save_config_rejeita_soma_diferente_de_100(user_id):
    with pytest.raises(ValueError, match="PCT_SOMA_INVALIDA"):
        save_config(user_id, _config_valida(custos_fixos=60.0))  # soma 105


def test_save_config_rejeita_bucket_desconhecido(user_id):
    cfg = _config_valida()
    cfg["viagem"] = 5.0
    cfg["conforto"] = 0.0  # soma continua 100: só a chave está errada
    with pytest.raises(ValueError, match="BUCKET_DESCONHECIDO"):
        save_config(user_id, cfg)
    # conjunto incompleto também é BUCKET_DESCONHECIDO
    with pytest.raises(ValueError, match="BUCKET_DESCONHECIDO"):
        save_config(user_id, {"custos_fixos": 100.0})


def test_save_config_rejeita_pct_fora_da_faixa(user_id):
    with pytest.raises(ValueError, match="PCT_INVALIDO"):
        save_config(user_id, _config_valida(conforto=-5.0, custos_fixos=60.0))
    with pytest.raises(ValueError, match="PCT_INVALIDO"):
        save_config(user_id, _config_valida(conforto=105.0, custos_fixos=-100.0))
    with pytest.raises(ValueError, match="PCT_INVALIDO"):
        save_config(user_id, _config_valida(conforto="cinco"))


def test_save_config_persiste_e_get_config_reflete(user_id):
    nova = _config_valida(custos_fixos=50.0, prazeres=15.0)
    save_config(user_id, nova)
    assert get_config(user_id) == nova


def test_save_config_invalida_nao_suja_a_anterior(user_id):
    """Tudo-ou-nada: payload inválido deixa os 6 percentuais intactos."""
    anterior = get_config(user_id)
    with pytest.raises(ValueError, match="PCT_SOMA_INVALIDA"):
        save_config(user_id, _config_valida(custos_fixos=99.0))
    with pytest.raises(ValueError, match="PCT_INVALIDO"):
        save_config(user_id, _config_valida(metas="dez"))
    assert get_config(user_id) == anterior


# ── renda: computada × override × limpeza ────────────────────────────────────

def test_renda_sem_override_e_a_computada(user_id):
    _receita(user_id, 5000.0)
    _receita(user_id, 500.0)
    assert get_monthly_income(user_id, _mes_atual()) == (5500.0, "computed")


def test_receita_interna_nao_entra_na_renda_computada(user_id):
    _receita(user_id, 5000.0)
    add_launch_and_update_balance(
        user_id, "receita", 999.0, "teste", "teste", is_internal_movement=True
    )
    assert get_monthly_income(user_id, _mes_atual()) == (5000.0, "computed")


def test_override_vence_computada_e_limpeza_restaura(user_id):
    _receita(user_id, 5000.0)
    set_income_override(user_id, _mes_atual(), 9000.0)
    assert get_monthly_income(user_id, _mes_atual()) == (9000.0, "override")
    clear_income_override(user_id, _mes_atual())
    assert get_monthly_income(user_id, _mes_atual()) == (5000.0, "computed")


def test_override_negativo_ou_invalido_e_renda_invalida(user_id):
    with pytest.raises(ValueError, match="RENDA_INVALIDA"):
        set_income_override(user_id, _mes_atual(), -1.0)
    with pytest.raises(ValueError, match="RENDA_INVALIDA"):
        set_income_override(user_id, _mes_atual(), "muito")
    # nada foi gravado: a renda continua a computada (0)
    assert get_monthly_income(user_id, _mes_atual()) == (0.0, "computed")


# ── status: agregação por pote ───────────────────────────────────────────────

def test_gastos_caem_no_pote_mapeado_launch_e_cartao(user_id):
    set_income_override(user_id, _mes_atual(), 10000.0)
    _gasto(user_id, "moradia", 2000.0)          # custos_fixos
    _gasto(user_id, "lazer", 300.0)             # prazeres
    _gasto(user_id, "educação", 400.0)          # conhecimento
    _gasto_cartao(user_id, "mercado", 800.0)    # custos_fixos (cartão)

    status = get_household_budget_status(user_id, _mes_atual())
    assert _bucket(status, "custos_fixos")["spent"] == 2800.0
    assert _bucket(status, "prazeres")["spent"] == 300.0
    assert _bucket(status, "conhecimento")["spent"] == 400.0
    assert _bucket(status, "metas")["spent"] == 0.0


def test_categoria_sem_mapeamento_cai_no_fallback_conforto(user_id):
    _gasto(user_id, "faculdade do filho", 250.0)  # custom, fora do mapa
    status = get_household_budget_status(user_id, _mes_atual())
    assert _bucket(status, "conforto")["spent"] == 250.0


def test_movimento_interno_e_excluido_mas_aporte_conta(user_id):
    """A exceção deliberada: aporte É a alocação do pote Liberdade financeira,
    mesmo gravado como is_internal_movement=true. Transferência interna comum
    continua fora (senão cairia no fallback 'conforto')."""
    _gasto(user_id, "transferencia_interna", 700.0, interno=True)
    _gasto(user_id, "investimento_aporte", 1000.0, interno=True)

    status = get_household_budget_status(user_id, _mes_atual())
    assert _bucket(status, "liberdade_financeira")["spent"] == 1000.0
    assert _bucket(status, "conforto")["spent"] == 0.0
    assert status["totals"]["spent"] == 1000.0


def test_budget_amount_e_used_pct_seguem_a_renda(user_id):
    set_income_override(user_id, _mes_atual(), 10000.0)
    _gasto(user_id, "lazer", 500.0)  # prazeres = 10% → 1000

    b = _bucket(get_household_budget_status(user_id, _mes_atual()), "prazeres")
    assert b["budget_amount"] == 1000.0
    assert b["spent"] == 500.0
    assert b["remaining"] == 500.0
    assert b["used_pct"] == 50.0


def test_renda_zero_used_pct_none_sem_divisao_por_zero(user_id):
    _gasto(user_id, "lazer", 100.0)
    status = get_household_budget_status(user_id, _mes_atual())
    for b in status["buckets"]:
        assert b["budget_amount"] == 0.0
        assert b["used_pct"] is None
    assert status["totals"]["used_pct"] is None
    assert status["totals"]["spent"] == 100.0


def test_totals_tem_o_shape_fechado(user_id):
    set_income_override(user_id, _mes_atual(), 10000.0)
    _gasto(user_id, "moradia", 1000.0)
    status = get_household_budget_status(user_id, _mes_atual())
    assert set(status["totals"]) == {"spent", "budget_amount", "remaining", "used_pct"}
    assert status["totals"]["budget_amount"] == 10000.0  # soma dos % = 100
    assert status["totals"]["spent"] == 1000.0
    assert status["totals"]["used_pct"] == 10.0
    assert status["income"] == {"amount": 10000.0, "source": "override"}


# ── caixinha, cripto, aporte e a lista por linha ─────────────────────────────

def test_deposito_de_caixinha_cai_em_metas_e_saque_nao(user_id):
    add_launch_and_update_balance(user_id, "receita", 1000.0, "teste", "teste")
    create_pocket(user_id, "Viagem")
    pocket_deposit_from_account(user_id, "Viagem", 300.0)
    pocket_withdraw_to_account(user_id, "Viagem", 100.0)

    metas = _bucket(get_household_budget_status(user_id, _mes_atual()), "metas")
    assert metas["spent"] == 300.0  # o saque de 100 não abate nem soma
    assert [l["valor"] for l in metas["launches"]] == [300.0]
    assert metas["categories"] == ["Depósitos em caixinhas"]


def test_cripto_e_aporte_de_investimento_caem_em_liberdade(user_id):
    add_launch_and_update_balance(user_id, "receita", 5000.0, "teste", "teste")
    _gasto(user_id, "criptomoedas", 200.0, interno=True)
    create_investment(user_id, "CDB X", 1.0, "daily")
    investment_deposit_from_account(user_id, "CDB X", 400.0)

    lib = _bucket(get_household_budget_status(user_id, _mes_atual()), "liberdade_financeira")
    assert lib["spent"] == 600.0
    assert _bucket(get_household_budget_status(user_id, _mes_atual()), "conforto")["spent"] == 0.0


def test_categorias_de_investimento_cripto_e_aliases_caem_em_liberdade(user_id):
    _receita(user_id, 5000.0)
    for cat in ("cripto", "Bitcoin", "investimentos"):
        _gasto(user_id, cat, 100.0, interno=True)
    _gasto_cartao(user_id, "btc", 50.0)
    _gasto(user_id, "rendimentos", 70.0, interno=True)  # segue fora

    st = get_household_budget_status(user_id, _mes_atual())
    assert _bucket(st, "liberdade_financeira")["spent"] == 350.0
    assert _bucket(st, "conforto")["spent"] == 0.0


def test_tabela_de_decisao_do_pote_por_linha(user_id):
    """Cada linha da tabela do docstring de db/household_budget.py."""
    _receita(user_id, 5000.0)
    _gasto(user_id, "Tesouro Selic", 10.0, interno=True)      # 2: composta interna → liberdade
    _gasto(user_id, "Ações Brasil", 20.0, interno=True)       # 2
    _gasto_cartao(user_id, "Tesouro Direto", 40.0)            # 2: cartão
    _gasto(user_id, "ajuste_saldo", 999.0, interno=True)      # 3: interna não-investimento → fora
    _gasto(user_id, "lazer", 100.0)                           # 4 → prazeres
    _gasto(user_id, "Presentes", 200.0)                       # 5 → conforto

    st = get_household_budget_status(user_id, _mes_atual())
    assert _bucket(st, "liberdade_financeira")["spent"] == 70.0
    assert _bucket(st, "prazeres")["spent"] == 100.0
    assert _bucket(st, "conforto")["spent"] == 200.0
    assert st["totals"]["spent"] == 370.0


def test_launches_somam_o_spent_de_cada_pote_e_nao_vazam_entre_usuarios(user_id):
    outro = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(outro)
    try:
        _gasto(user_id, "moradia", 2000.0)
        _gasto_cartao(user_id, "mercado", 800.0)
        _gasto(outro, "moradia", 777.0)
        _gasto_cartao(outro, "mercado", 555.0)

        status = get_household_budget_status(user_id, _mes_atual())
        for b in status["buckets"]:
            assert round(sum(l["valor"] for l in b["launches"]), 2) == b["spent"]
        cf = _bucket(status, "custos_fixos")
        assert cf["spent"] == 2800.0
        assert {l["origem"] for l in cf["launches"]} == {"conta", "cartao"}
        assert sum(c["total"] for c in cf["by_category"]) == 2800.0
    finally:
        from conftest import _cleanup_user
        _cleanup_user(outro)


def test_estorno_no_cartao_nao_entra_no_spent_nem_nos_launches(user_id):
    _gasto_cartao(user_id, "mercado", 800.0, estorno=100.0)
    cf = _bucket(get_household_budget_status(user_id, _mes_atual()), "custos_fixos")
    assert cf["spent"] == 800.0
    assert [l["valor"] for l in cf["launches"]] == [800.0]


def test_rotulo_de_categoria_usa_o_nome_do_mapa_mesmo_sem_acento_na_origem(user_id):
    _gasto(user_id, "Alimentacao", 10.0)
    _gasto(user_id, "ALIMENTAÇÃO ", 20.0)
    cf = _bucket(get_household_budget_status(user_id, _mes_atual()), "custos_fixos")
    assert [c["categoria"] for c in cf["by_category"]] == ["Alimentação"]
    assert {l["categoria"] for l in cf["launches"]} == {"Alimentação"}


# ── validação de month ───────────────────────────────────────────────────────

@pytest.mark.parametrize("mes", ["2026-13", "abc", "09-2026", "2026", "2026-00"])
def test_mes_invalido_rejeitado(user_id, mes):
    with pytest.raises(ValueError, match="MES_INVALIDO"):
        get_household_budget_status(user_id, mes)
    with pytest.raises(ValueError, match="MES_INVALIDO"):
        set_income_override(user_id, mes, 100.0)
    with pytest.raises(ValueError, match="MES_INVALIDO"):
        clear_income_override(user_id, mes)


def test_mes_valido_aceito(user_id):
    status = get_household_budget_status(user_id, "2026-09")
    assert status["month"] == "2026-09"


# ── rotas pelo HTTP de verdade ───────────────────────────────────────────────

def _cliente(user_id: int, email: str = "hb@t.com"):
    from fastapi.testclient import TestClient

    import frontend.finance_bot_websocket_custom as dashboard

    client = TestClient(dashboard.app, raise_server_exceptions=False)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, email))
    client.cookies.set(
        dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1)
    )
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "t")
    headers = {dashboard.CSRF_HEADER_NAME: "t", "Content-Type": "application/json"}
    return client, headers


def test_rota_status_403_abaixo_do_plus(user_id):
    promote_to_pro(user_id, "essencial")  # abaixo do Plus
    client, _ = _cliente(user_id)
    r = client.get(f"/household-budget/{user_id}/status")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["error"] == "pro_required"
    assert r.json()["detail"]["feature"] == "household_budget"


def test_rota_status_200_para_pro(user_id):
    promote_to_pro(user_id)
    client, _ = _cliente(user_id)
    r = client.get(f"/household-budget/{user_id}/status?month={_mes_atual()}")
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["ok"] is True
    assert len(corpo["buckets"]) == 6
    assert set(corpo["totals"]) == {"spent", "budget_amount", "remaining", "used_pct"}


def test_rota_income_override_e_amount_null_limpa(user_id):
    promote_to_pro(user_id)
    _receita(user_id, 5000.0)
    client, headers = _cliente(user_id)
    mes = _mes_atual()
    url = f"/household-budget/{user_id}/income"

    r = client.put(url, json={"month": mes, "amount": 9000.0}, headers=headers)
    assert r.status_code == 200, r.text
    assert client.get(f"/household-budget/{user_id}/status?month={mes}").json()[
        "income"
    ] == {"amount": 9000.0, "source": "override"}

    r = client.put(url, json={"month": mes, "amount": None}, headers=headers)
    assert r.status_code == 200, r.text
    assert client.get(f"/household-budget/{user_id}/status?month={mes}").json()[
        "income"
    ] == {"amount": 5000.0, "source": "computed"}


def test_rota_income_rejeita_amount_negativo(user_id):
    promote_to_pro(user_id)
    client, headers = _cliente(user_id)
    r = client.put(
        f"/household-budget/{user_id}/income",
        json={"month": _mes_atual(), "amount": -5.0}, headers=headers,
    )
    assert r.status_code == 400, r.text


def test_rota_mes_invalido_400(user_id):
    promote_to_pro(user_id)
    client, headers = _cliente(user_id)
    assert client.get(
        f"/household-budget/{user_id}/status?month=2026-13"
    ).status_code == 400
    r = client.put(
        f"/household-budget/{user_id}/income",
        json={"month": "abc", "amount": 100.0}, headers=headers,
    )
    assert r.status_code == 400, r.text


def test_rota_config_valida_e_soma_invalida(user_id):
    promote_to_pro(user_id)
    client, headers = _cliente(user_id)
    url = f"/household-budget/{user_id}/config"

    boa = {b["key"]: float(b["default_pct"]) for b in BUCKETS}
    r = client.put(url, json={"buckets": boa}, headers=headers)
    assert r.status_code == 200, r.text

    ruim = dict(boa, custos_fixos=60.0)  # soma 105
    r = client.put(url, json={"buckets": ruim}, headers=headers)
    assert r.status_code == 400, r.text
    # a config anterior continua intacta
    r = client.get(f"/household-budget/{user_id}/status")
    assert r.status_code == 200, r.text
    assert r.json()["buckets"][0]["pct"] == boa["custos_fixos"]
