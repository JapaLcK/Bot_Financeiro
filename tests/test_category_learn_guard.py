"""
Guard do auto-aprendizado de categoria: não deixar um keyword com categoria
canônica (LOCAL_RULES) ser reaprendido com outra categoria.

Regressão: "gastei no mercado" classificado errado como "alimentação" aprendia
mercado→alimentação e travava pra sempre (regra do usuário vence a LOCAL_RULE,
e o passo B nunca reaprende).
"""
from __future__ import annotations

import pytest

import db
from core.services.category_service import (
    local_rule_category,
    learn_from_inference,
    learn_from_explicit_category,
    infer_category,
)
from utils_text import normalize_text


# --- unit puro do helper --------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("mercado", "mercado"),
    ("gastei 800 no mercado", "mercado"),
    ("ifood", "alimentação"),
    ("uber", "transporte"),
    ("amazonia", None),          # palavra-inteira: não casa "amazon"
    ("presente da mae", None),   # sem regra canônica → aprendizado livre
])
def test_local_rule_category(text, expected):
    assert local_rule_category(normalize_text(text)) == expected


# --- guard no auto-aprendizado -------------------------------------------

def test_nao_reaprende_mercado_como_alimentacao(user_id):
    # simula a inferência errada (IA) tentando aprender mercado→alimentação
    learn_from_inference(user_id, "gastei 800 no mercado", "alimentacao", reason="ai")
    # o guard bloqueou: nenhuma regra de usuário pra "mercado"
    assert db.get_memorized_category(user_id, "gastei 800 no mercado") is None
    # e a inferência continua caindo na LOCAL_RULE certa
    assert infer_category(user_id, "gastei 800 no mercado", allow_ai=False).category == "mercado"


def test_aprende_categoria_consistente_com_local(user_id):
    # ifood é canonicamente alimentação → aprender é consistente, não bloqueia
    learn_from_inference(user_id, "gastei 50 no ifood", "alimentacao", reason="ai")
    assert db.get_memorized_category(user_id, "ifood") == "alimentacao"


def test_aprende_keyword_sem_regra_canonica(user_id):
    # "padoca do zé" não tem regra local → aprendizado livre
    learn_from_inference(user_id, "gastei 20 na padoca do ze", "alimentacao", reason="ai")
    assert db.get_memorized_category(user_id, "padoca do ze") == "alimentacao"


def test_escolha_explicita_ainda_sobrescreve_local(user_id):
    # o guard NÃO se aplica à escolha explícita do usuário (botão/#cat):
    # se o user REALMENTE quer mercado→lazer, respeita.
    learn_from_explicit_category(user_id, "comprei no mercado", "lazer")
    assert db.get_memorized_category(user_id, "mercado") == "lazer"
