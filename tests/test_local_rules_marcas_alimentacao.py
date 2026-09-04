"""
Issue #138: marcas de fast food/restaurante nas LOCAL_RULES.

Antes deste bloco, 14 das 16 marcas medidas na issue caíam em "outros" para
quem não tem IA (plano Grátis) — e `learn_from_inference` ignora
reason="default", então caíam em "outros" para sempre.

Controle negativo: apague o bloco de marcas de `utils_text.LOCAL_RULES` e
`test_marca_vai_para_alimentacao` fica vermelho em todos os termos novos.
"""
from __future__ import annotations

import pytest

from db.categories import create_user_category
from core.services.category_service import infer_category
from ofx_import import resolve_category
from utils_text import normalize_text


# Todo termo acrescentado às LOCAL_RULES aparece aqui — inclusive na forma que
# o usuário digita (com apóstrofo, com acento, em CAIXA ALTA).
MARCAS = [
    "mcdonalds", "mcdonald's", "McDonalds", "mc donalds", "mequi",
    "bk", "subway", "habibs", "habib's", "bobs",
    "kfc", "KFC", "giraffas", "ragazzo", "china in box", "dominos",
    "divino fogao", "divino fogão",
    "starbucks", "the coffee", "kopenhagen", "cacau show",
    "outback", "madero", "coco bambu", "spoleto", "applebees", "applebee's",
]


@pytest.mark.parametrize("marca", MARCAS)
def test_marca_vai_para_alimentacao(user_id, marca):
    res = infer_category(user_id, f"gastei 39,90 no {marca}", allow_ai=False)
    assert (res.category, res.reason) == ("alimentação", "local_rule"), marca


@pytest.mark.parametrize("marca", ["burger king", "pizza hut"])
def test_marca_ja_coberta_pelo_termo_generico(user_id, marca):
    """As 2 das 16 que já funcionavam — por "burger"/"pizza", não por marca.
    Ficaram FORA do bloco novo de propósito (§0.2: sem termo redundante)."""
    res = infer_category(user_id, f"gastei 39,90 no {marca}", allow_ai=False)
    assert res.category == "alimentação"


# --- anti-falso-positivo -------------------------------------------------
# "bk" (2 letras) e "kfc" (3) casam só como palavra inteira em
# local_rule_category; os demais casam por substring, então cada um precisa de
# uma frase vizinha que NÃO pode virar alimentação.
@pytest.mark.parametrize("frase,esperado", [
    ("comprei um bkzinho", "outros"),
    ("paguei o bkp do servidor", "outros"),
    ("paguei o kfcloud", "outros"),
    # o próprio eletrodoméstico continua moradia — o blocker de "fogao" só
    # dispara com "divino" na frente
    ("comprei um fogao novo", "moradia"),
    ("conserto do fogao", "moradia"),
    # termos genéricos que sobrevivem ao bloco novo
    ("gastei 50 no mercado", "mercado"),
    ("curso de japones", "educação"),
    ("paguei o aluguel", "moradia"),
])
def test_nao_vira_alimentacao(user_id, frase, esperado):
    assert infer_category(user_id, frase, allow_ai=False).category == esperado


# --- categoria custom do usuário continua ganhando -----------------------
def test_categoria_custom_vence_marca_nova(user_id):
    """B2 (custom_category_match) roda ANTES do passo C (LOCAL_RULES) em
    infer_category — quem criou uma categoria chamada como a marca não perde
    o lançamento para a regra local nova."""
    create_user_category(user_id, "outback")
    res = infer_category(user_id, "gastei 39,90 no outback", allow_ai=False)
    assert (res.category, res.reason) == ("outback", "user_category")


# --- caminho do importador (OFX/extrato) ---------------------------------
def test_marca_no_importador_de_extrato():
    """`resolve_category` do OFX lê as mesmas LOCAL_RULES — a marca vale lá
    também. Ceiling conhecido: esse laço casa por substring mesmo em keyword
    curta (não usa EXACT_WORD_KEYWORDS nem o corte de <=3 letras), então "bk"
    dentro de outra palavra casa ali. Comportamento pré-existente da função
    para "pet"/"casa"/"cao"; documentado, não corrigido nesta issue."""
    assert resolve_category(normalize_text("COMPRA CARTAO MCDONALDS 1234"), []) == "alimentacao"
    assert resolve_category(normalize_text("PAG BKZINHO"), []) == "alimentacao"  # ceiling
