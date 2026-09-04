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
from utils_text import guess_category, normalize_text


# Todo termo acrescentado às LOCAL_RULES aparece aqui — inclusive na forma que
# o usuário digita (com apóstrofo, com acento, em CAIXA ALTA).
MARCAS = [
    # As 4 grafias de McDonald's: "mc donald's" é a mais provável no WhatsApp e
    # era a que faltava — a lista antiga só tinha "mc donalds"/"mcdonald's" e a
    # keyword "mc donalds" não casa normalize_text("mc donald's") == "mc donald s".
    "mcdonalds", "mcdonald's", "McDonalds",
    "mc donalds", "mc donald's", "mc donald",
    "mequi",
    # Bob's se escreve COM apóstrofo; as duas grafias precisam casar.
    "bk", "subway", "habibs", "habib's", "bobs", "bob's",
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
    # "bobs"/"bob s" entram explícitos justamente pra NÃO virar a keyword "bob"
    # (3 letras, palavra inteira), que roubaria o cachorro chamado Bob.
    ("racao do bob", "pets"),
    ("levei o bob no veterinario", "pets"),
    ("consulta do bob", "saúde"),
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
    """`resolve_category` do OFX lê as mesmas LOCAL_RULES — a marca vale lá também."""
    assert resolve_category(normalize_text("COMPRA CARTAO MCDONALDS 1234"), []) == "alimentacao"


@pytest.mark.xfail(
    strict=True,
    reason="issue #272: resolve_category/_categorize casam keyword <=3 letras por "
           "substring; quando o corte de palavra inteira entrar, este teste fica "
           "XPASS(strict) e é só remover o marcador",
)
def test_importador_nao_deveria_casar_keyword_curta_dentro_de_palavra():
    """Teto conhecido, NÃO conserto desta issue — e não é um assert positivo, senão
    a #272 chegaria como falsa regressão.

    Os importadores (`ofx_import.resolve_category` e `ofx_credit_import._categorize`)
    casam por substring mesmo em keyword de <=3 letras, sem `EXACT_WORD_KEYWORDS`
    nem o corte que `local_rule_category` faz. A classe é pré-existente e maior que
    esta issue: são 39 keywords <=3 letras já na `main` (bar, sol, gas, luz, pet,
    cao, ada, nft…). Medido num corpus de 32 memos realistas de fatura:
    27 falsos positivos na `main`, 32 aqui — "bk"/"kfc" acrescentam 5 a uma classe
    que já valia 27, e tirá-las corrigiria a instância, não a classe (CLAUDE.md §2).
    """
    assert resolve_category(normalize_text("PAG BKZINHO"), []) == "outros"


def test_divino_fogao_continua_categorizado_no_motor_de_recorrente():
    """KEYWORD_BLOCKERS["fogao"] tem DOIS consumidores: `local_rule_category` e
    `guess_category` (utils_text), este último vivo em `core/handlers/recurring.py:171`
    pra classificar gasto fixo quando a IA não classificou.

    As 22 marcas entraram só em LOCAL_RULES, então o blocker sozinho tirava
    "divino fogão" de moradia (errado) e deixava em "outros" (nada) no recorrente.
    A marca na lista de alimentação de CATEGORY_KEYWORDS fecha o par — e alimentação
    é avaliada antes de moradia, então a marca vence o eletrodoméstico.
    """
    assert guess_category("divino fogão") == "alimentação"
    assert guess_category("divino fogao") == "alimentação"
    # controle positivo: o eletrodoméstico continua moradia nos DOIS motores
    assert guess_category("comprei um fogão novo") == "moradia"
