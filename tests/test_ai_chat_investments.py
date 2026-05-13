"""
Cobre as tools de investimentos da IA conversacional
(`core/services/ai_chat/tools/investments.py`):

Read:
  - list_investments
  - get_investment_summary
  - get_investment_contributions

Write (retornam string final pronta, não passam por confirmação aqui):
  - create_investment
  - investment_deposit
  - investment_withdraw
  - delete_investment

Os helpers da DB (db.create_investment, db.investment_deposit_from_account,
db.investment_withdraw_to_account) já têm cobertura própria em
test_db_investments. Esses testes garantem o contrato das funções
`_*_execute` / `_list_*` da camada AI: parsing dos args, validação de
input vinda da IA, e formato do retorno.
"""
from datetime import date, timedelta

import db
from core.services.ai_chat.tools.investments import (
    _create_investment_execute,
    _delete_investment_execute,
    _get_investment_contributions,
    _get_investment_summary,
    _investment_deposit_execute,
    _investment_withdraw_execute,
    _list_investments,
)


# ─── list_investments ───────────────────────────────────────────────────────


def test_list_investments_vazio_sem_ativos(user_id):
    out = _list_investments(user_id, {})
    assert out == {"investments": []}


def test_list_investments_retorna_ativos_com_saldo_e_taxa(user_id):
    db.create_investment(user_id, "CDB Nubank", 0.14, "yearly")
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.investment_deposit_from_account(user_id, "CDB Nubank", 500)

    out = _list_investments(user_id, {})
    assert len(out["investments"]) == 1
    inv = out["investments"][0]
    assert inv["name"] == "CDB Nubank"
    assert inv["balance"] >= 500.0  # >= por causa do accrual
    assert "rate_display" in inv
    assert inv["rate_display"]  # não vazio


# ─── get_investment_summary ─────────────────────────────────────────────────


def test_summary_zero_sem_ativos(user_id):
    out = _get_investment_summary(user_id, {})
    assert out == {
        "total_invested": 0,
        "investment_count": 0,
        "investments_with_balance": 0,
    }


def test_summary_agrega_multiplos_ativos(user_id):
    db.create_investment(user_id, "CDB A", 0.13, "yearly")
    db.create_investment(user_id, "CDB B", 0.14, "yearly")
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.investment_deposit_from_account(user_id, "CDB A", 300)
    db.investment_deposit_from_account(user_id, "CDB B", 200)

    out = _get_investment_summary(user_id, {})
    assert out["investment_count"] == 2
    assert out["investments_with_balance"] == 2
    assert out["total_invested"] >= 500.0


def test_summary_ignora_investimento_sem_saldo(user_id):
    db.create_investment(user_id, "CDB Vazio", 0.10, "yearly")

    out = _get_investment_summary(user_id, {})
    assert out["investment_count"] == 1
    assert out["investments_with_balance"] == 0
    assert out["total_invested"] == 0.0


# ─── get_investment_contributions ───────────────────────────────────────────


def test_contributions_zero_sem_aportes(user_id):
    out = _get_investment_contributions(user_id, {})
    assert out["total_contributed"] == 0.0
    assert out["contribution_count"] == 0
    assert out["by_investment"] == []


def test_contributions_soma_aportes_do_periodo(user_id):
    db.create_investment(user_id, "CDB", 0.13, "yearly")
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.investment_deposit_from_account(user_id, "CDB", 300)
    db.investment_deposit_from_account(user_id, "CDB", 100)

    out = _get_investment_contributions(user_id, {})
    assert out["total_contributed"] == 400.0
    assert out["contribution_count"] == 2
    assert out["by_investment"] == [{"name": "CDB", "total": 400.0}]


def test_contributions_breakdown_por_investimento(user_id):
    db.create_investment(user_id, "CDB A", 0.13, "yearly")
    db.create_investment(user_id, "CDB B", 0.14, "yearly")
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.investment_deposit_from_account(user_id, "CDB A", 200)
    db.investment_deposit_from_account(user_id, "CDB B", 150)

    out = _get_investment_contributions(user_id, {})
    names = {item["name"] for item in out["by_investment"]}
    assert names == {"CDB A", "CDB B"}


def test_contributions_end_anterior_a_start_retorna_erro(user_id):
    today = date.today()
    out = _get_investment_contributions(user_id, {
        "start_date": today.isoformat(),
        "end_date": (today - timedelta(days=10)).isoformat(),
    })
    assert "error" in out


# ─── create_investment (execute) ────────────────────────────────────────────


def test_create_investment_cria_com_args_validos(user_id):
    out = _create_investment_execute(user_id, {
        "name": "Tesouro Selic",
        "rate": 13.75,
        "period": "yearly",
    })
    assert "✅" in out
    assert "Tesouro Selic" in out

    invs = db.list_investments(user_id)
    assert any(i["name"] == "Tesouro Selic" for i in invs)


def test_create_investment_recusa_period_invalido(user_id):
    out = _create_investment_execute(user_id, {
        "name": "Foo",
        "rate": 10,
        "period": "weekly",  # inválido
    })
    assert "🐷" in out
    assert "daily" in out or "period" in out.lower()


def test_create_investment_recusa_rate_zero_ou_negativa(user_id):
    out = _create_investment_execute(user_id, {
        "name": "Foo",
        "rate": 0,
        "period": "yearly",
    })
    assert "🐷" in out

    out2 = _create_investment_execute(user_id, {
        "name": "Bar",
        "rate": -1,
        "period": "yearly",
    })
    assert "🐷" in out2


def test_create_investment_recusa_nome_vazio(user_id):
    out = _create_investment_execute(user_id, {
        "name": "  ",
        "rate": 10,
        "period": "yearly",
    })
    assert "🐷" in out


def test_create_investment_rate_nao_numerica(user_id):
    out = _create_investment_execute(user_id, {
        "name": "X",
        "rate": "abc",
        "period": "yearly",
    })
    assert "🐷" in out


# ─── investment_deposit (execute) ───────────────────────────────────────────


def test_deposit_aporta_com_args_validos(user_id):
    db.create_investment(user_id, "CDB", 0.14, "yearly")
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")

    out = _investment_deposit_execute(user_id, {
        "name": "CDB",
        "amount": 500,
    })
    assert "✅" in out
    assert "500" in out

    invs = db.list_investments(user_id)
    cdb = next(i for i in invs if i["name"] == "CDB")
    assert float(cdb["balance"]) >= 500.0


def test_deposit_recusa_amount_zero(user_id):
    out = _investment_deposit_execute(user_id, {"name": "X", "amount": 0})
    assert "🐷" in out


def test_deposit_recusa_amount_invalido(user_id):
    out = _investment_deposit_execute(user_id, {"name": "X", "amount": "abc"})
    assert "🐷" in out


def test_deposit_recusa_investimento_inexistente(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    out = _investment_deposit_execute(user_id, {
        "name": "Não Existe",
        "amount": 50,
    })
    assert "🐷" in out


# ─── investment_withdraw (execute) ──────────────────────────────────────────


def test_withdraw_resgata_com_args_validos(user_id):
    db.create_investment(user_id, "CDB", 0.14, "yearly")
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    db.investment_deposit_from_account(user_id, "CDB", 500)

    out = _investment_withdraw_execute(user_id, {
        "name": "CDB",
        "amount": 200,
    })
    assert "✅" in out
    assert "200" in out


def test_withdraw_recusa_amount_invalido(user_id):
    out = _investment_withdraw_execute(user_id, {"name": "X", "amount": 0})
    assert "🐷" in out


def test_withdraw_recusa_saldo_insuficiente(user_id):
    db.create_investment(user_id, "CDB", 0.14, "yearly")
    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    db.investment_deposit_from_account(user_id, "CDB", 50)

    out = _investment_withdraw_execute(user_id, {
        "name": "CDB",
        "amount": 9999,
    })
    assert "🐷" in out


# ─── delete_investment (execute) ────────────────────────────────────────────


def test_delete_investment_apaga_quando_zerado(user_id):
    db.create_investment(user_id, "CDB Vazio", 0.14, "yearly")

    out = _delete_investment_execute(user_id, {"name": "CDB Vazio"})
    assert "✅" in out

    invs = db.list_investments(user_id)
    assert not any(i["name"] == "CDB Vazio" for i in invs)


def test_delete_investment_recusa_quando_tem_saldo(user_id):
    db.create_investment(user_id, "CDB Com Saldo", 0.14, "yearly")
    db.add_launch_and_update_balance(user_id, "receita", 100, None, "seed")
    db.investment_deposit_from_account(user_id, "CDB Com Saldo", 50)

    out = _delete_investment_execute(user_id, {"name": "CDB Com Saldo"})
    assert "🐷" in out  # falhou; não pode apagar com saldo


def test_delete_investment_recusa_nome_vazio(user_id):
    out = _delete_investment_execute(user_id, {"name": "   "})
    assert "🐷" in out
