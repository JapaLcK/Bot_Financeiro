"""
Regressão: categoria CUSTOM criada na tela (user_categories) sem regra de
keyword deve ser reconhecida na inferência de um lançamento.

Bug relatado: usuário cria a categoria "gastos com minha namorada" e lança
"gastei 400 com minha namorada" — o gasto ia parar em "outros" porque
infer_category só olhava user_category_rules / LOCAL_RULES / IA, nunca as
categorias custom que o usuário cadastrou visualmente.
"""
from __future__ import annotations

import pytest

import db
from db.categories import (
    create_user_category,
    list_custom_category_names,
    list_user_category_rules,
    set_user_category_archived,
)
from core.handlers.credit import try_handle_natural_credit_purchase
from core.services.category_service import (
    custom_category_match,
    infer_category,
    learn_from_inference,
    _distinctive_tokens,
)
from utils_text import normalize_text


# --- unit do helper de tokens distintivos --------------------------------

@pytest.mark.parametrize("name,expected", [
    ("gastos com minha namorada", ["namorada"]),
    ("Gastos com Pet",            ["pet"]),          # 3 letras, palavra inteira → ok
    ("gastos com minha mãe",      ["mae"]),          # acento cai na normalização
    ("cachorro do vizinho",       ["cachorro", "vizinho"]),
    ("gastos gerais",             []),               # só tokens genéricos
    ("gastos do dia",             []),               # "dia" é genérico curto
    ("faculdade",                 ["faculdade"]),
])
def test_distinctive_tokens(name, expected):
    assert _distinctive_tokens(name) == expected


# --- match contra categorias custom do usuário ---------------------------

def test_custom_category_match_namorada(pro_user_id):
    create_user_category(pro_user_id, "gastos com minha namorada")
    hit = custom_category_match(pro_user_id, normalize_text("gastei 400 com minha namorada"))
    assert hit == "gastos com minha namorada"


def test_custom_category_no_false_positive(pro_user_id):
    create_user_category(pro_user_id, "gastos com minha namorada")
    # nenhum token distintivo ("namorada") aparece → não casa
    assert custom_category_match(pro_user_id, normalize_text("gastei 50 no mercado")) is None


def test_infer_usa_categoria_custom(pro_user_id):
    create_user_category(pro_user_id, "gastos com minha namorada")
    res = infer_category(pro_user_id, "gastei 400 com minha namorada", allow_ai=False)
    assert res.category == "gastos com minha namorada"
    assert res.reason == "user_category"


def test_infer_cai_em_outros_sem_categoria_custom(pro_user_id):
    # sem categoria custom cadastrada e sem regra local, "namorada" não existe
    res = infer_category(pro_user_id, "gastei 400 com minha namorada", allow_ai=False)
    assert res.category == "outros"
    assert res.reason == "default"


def test_regra_de_keyword_vence_categoria_custom(pro_user_id):
    # user_rule (passo B) tem prioridade sobre a categoria custom (passo B2)
    create_user_category(pro_user_id, "gastos com minha namorada")
    db.upsert_category_rule(pro_user_id, "namorada", "lazer")
    res = infer_category(pro_user_id, "gastei 400 com minha namorada", allow_ai=False)
    assert res.category == "lazer"
    assert res.reason == "user_rule"


def test_preserva_acento_do_nome_cadastrado(pro_user_id):
    # o nome retornado deve bater EXATAMENTE com o cadastrado (com acento),
    # senão o lançamento não agrupa com a categoria no dashboard.
    create_user_category(pro_user_id, "saúde da família")
    res = infer_category(pro_user_id, "gastei 200 com a saúde da família", allow_ai=False)
    assert res.category == "saúde da família"
    assert res.reason == "user_category"


def test_categoria_arquivada_nao_casa(pro_user_id):
    cat = create_user_category(pro_user_id, "gastos com minha namorada")
    set_user_category_archived(pro_user_id, cat["id"], True)
    assert custom_category_match(pro_user_id, normalize_text("gastei 400 com minha namorada")) is None


def test_user_category_nao_cria_regra_aprendida(pro_user_id):
    create_user_category(pro_user_id, "gastos com minha namorada")

    learn_from_inference(
        pro_user_id,
        "gastei 400 com minha namorada",
        "gastos com minha namorada",
        target_hint="minha namorada",
        reason="user_category",
    )

    assert list_user_category_rules(pro_user_id) == []


def test_categoria_arquivada_nao_volta_por_regra_aprendida(pro_user_id):
    cat = create_user_category(pro_user_id, "gastos com minha namorada")
    learn_from_inference(
        pro_user_id,
        "gastei 400 com minha namorada",
        "gastos com minha namorada",
        target_hint="minha namorada",
        reason="user_category",
    )

    set_user_category_archived(pro_user_id, cat["id"], True)
    res = infer_category(pro_user_id, "gastei 400 com minha namorada", allow_ai=False)

    assert res.category == "outros"
    assert res.reason == "default"


def test_list_custom_category_names_nao_roda_seed(monkeypatch, pro_user_id):
    def _fail_seed(_user_id):
        raise AssertionError("inferência não deve semear categorias nesse caminho")

    monkeypatch.setattr("db.categories.ensure_user_categories_seeded", _fail_seed)

    assert list_custom_category_names(pro_user_id) == []


def test_compra_natural_credito_usa_categoria_custom(pro_user_id):
    card_id = db.create_card(pro_user_id, "Nubank", closing_day=10, due_day=17)
    db.set_default_card(pro_user_id, card_id)
    create_user_category(pro_user_id, "gastos com minha namorada")

    msg = try_handle_natural_credit_purchase(
        pro_user_id,
        "gastei 400 no crédito com minha namorada",
    )

    assert msg is not None
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select categoria, nota from credit_transactions "
                "where user_id=%s order by id desc limit 1",
                (pro_user_id,),
            )
            row = cur.fetchone()

    assert row["categoria"] == "gastos com minha namorada"
    assert row["nota"] == "minha namorada"
