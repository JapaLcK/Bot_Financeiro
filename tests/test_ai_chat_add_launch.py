"""
Cobre a tool `add_launch` da IA conversacional:
  - summary: monta texto pra template 3 de confirmação
  - execute: registra o lançamento via add_launch_and_update_balance,
    atualiza o saldo e respeita categoria explícita / inferência local.
"""
from decimal import Decimal

import db
from core.services.ai_chat.tools.launches import (
    _add_launch_execute,
    _add_launch_summary,
)


# ─── Summary (pure, sem DB) ────────────────────────────────────────────────

def test_summary_despesa_basica():
    out = _add_launch_summary({"tipo": "despesa", "valor": 50, "alvo": "mercado"})
    assert out == "registrar despesa de R$ 50,00 em mercado"


def test_summary_receita_sem_alvo():
    out = _add_launch_summary({"tipo": "receita", "valor": 1234.56})
    assert out == "registrar receita de R$ 1.234,56"


def test_summary_com_categoria_explicita_e_data():
    out = _add_launch_summary({
        "tipo": "despesa",
        "valor": 80,
        "alvo": "luz",
        "categoria": "moradia",
        "data": "2026-04-15",
    })
    assert out == "registrar despesa de R$ 80,00 em luz (moradia) em 15/04/2026"


def test_summary_tipo_invalido_fallback():
    out = _add_launch_summary({"tipo": "transferencia", "valor": 100})
    assert out == "registrar um lançamento"


# ─── Execute (DB-backed) ───────────────────────────────────────────────────

def test_execute_despesa_basica(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")

    msg = _add_launch_execute(user_id, {
        "tipo": "despesa",
        "valor": 50,
        "alvo": "mercado",
    })

    assert "Despesa de R$ 50,00" in msg
    assert "mercado" in msg
    # "mercado" cai em alimentação pela LOCAL_RULES (utils_text.py)
    assert "alimentação" in msg
    assert db.get_balance(user_id) == Decimal("950")


def test_execute_receita_atualiza_saldo(user_id):
    msg = _add_launch_execute(user_id, {
        "tipo": "receita",
        "valor": 200,
        "alvo": "freela",
    })

    assert "Receita de R$ 200,00" in msg
    assert db.get_balance(user_id) == Decimal("200")


def test_execute_categoria_explicita_respeitada(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")

    msg = _add_launch_execute(user_id, {
        "tipo": "despesa",
        "valor": 30,
        "alvo": "presente da mãe",
        "categoria": "lazer",
    })

    assert "(lazer)" in msg
    rows = db.list_launches(user_id, limit=2)
    # Primeira row é a mais recente (despesa que acabamos de criar)
    assert rows[0]["categoria"] == "lazer"


def test_execute_tipo_invalido_nao_escreve(user_id):
    msg = _add_launch_execute(user_id, {"tipo": "transferencia", "valor": 50})

    assert "Tipo inválido" in msg
    assert db.get_balance(user_id) == Decimal("0")
    assert db.list_launches(user_id, limit=1) == []


def test_execute_valor_zero_nao_escreve(user_id):
    msg = _add_launch_execute(user_id, {"tipo": "despesa", "valor": 0})

    assert "maior que zero" in msg
    assert db.get_balance(user_id) == Decimal("0")


def test_execute_data_iso_define_criado_em(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 500, None, "seed")

    _add_launch_execute(user_id, {
        "tipo": "despesa",
        "valor": 10,
        "alvo": "padaria",
        "data": "2026-04-15",
    })

    # list_launches ordena por criado_em desc, e a data passada é mais antiga
    # que o seed (now), então filtra por alvo em vez de presumir ordem.
    rows = db.list_launches(user_id, limit=10)
    padaria = next(r for r in rows if r.get("alvo") == "padaria")
    assert padaria["criado_em"].date().isoformat() == "2026-04-15"
