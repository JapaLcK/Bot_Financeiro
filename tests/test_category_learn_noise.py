"""
Anti-poluição do auto-aprendizado de categoria.

O `learn_from_signals` recebia o `text_base` inteiro (mensagem/transcrição crua)
e gerava n-gramas dele, enchendo `user_category_rules` de ruído real visto em
prod: "pig eu", "pig reais ifood", "reais centavos almoco",
"pao doce mano acredita", "registrada pelo dashboard", "conserto meu carro pig".

A correção ataca a raiz em três frentes:
  1. `learn_from_inference` aprende do ALVO limpo (target_hint), não do text_base.
  2. stoplist de fala (wake-word, pronomes, valor por extenso, conectores,
     números por extenso) filtrada em `extract_memory_candidates`.
  3. candidatos limitados a no máx 2 palavras (sem frases longas).
"""
from __future__ import annotations

import pytest

import db
from core.services.category_service import learn_from_inference
from utils_text import (
    extract_memory_candidates,
    is_useful_memory_keyword,
)


# Frases-ruído reais colhidas em prod (via texto e transcrição de áudio).
NOISE_PHRASES = [
    "pig eu",
    "pig eu mercado mais",
    "pig reais ifood",
    "reais centavos almoco",
    "pao doce mano acredita",
    "mais focinheira",
    "registrada pelo dashboard",
    "valor recebido investimentos",
    "conserto meu carro pig",
]


# --- unit puro: extract_memory_candidates ---------------------------------

@pytest.mark.parametrize("phrase", NOISE_PHRASES)
def test_frase_ruido_nunca_vira_keyword_inteira(phrase):
    cands = extract_memory_candidates(phrase)
    # a frase crua completa nunca pode ser memorizada como está
    assert phrase not in cands
    # e nenhum candidato pode passar de 2 palavras
    assert all(len(c.split()) <= 2 for c in cands)


@pytest.mark.parametrize("phrase,expected", [
    # o alvo limpo sobrevive; wake-word / valor por extenso / conectores caem
    ("pig eu mercado mais", "mercado"),
    ("pig reais ifood", "ifood"),
    ("reais centavos almoco", "almoco"),
    ("mais focinheira", "focinheira"),
])
def test_alvo_limpo_sobrevive_ao_filtro(phrase, expected):
    cands = extract_memory_candidates(phrase)
    assert expected in cands


@pytest.mark.parametrize("phrase", [
    "pig eu",                    # só wake-word + pronome
    "registrada pelo dashboard",  # nota default do dashboard (sem alvo)
    "reais centavos",             # só valor por extenso
    "pig piggy mais",             # só ruído de fala
])
def test_ruido_puro_nao_gera_candidato(phrase):
    assert extract_memory_candidates(phrase) == []


# --- unit puro: is_useful_memory_keyword ----------------------------------

@pytest.mark.parametrize("kw", [
    "pig eu mercado mais",   # 4 palavras — frase longa
    "pao doce mano acredita",
    "reais centavos",        # só ruído
    "pig",                   # wake-word sozinha
    "eu",                    # pronome
    "mil reais",             # número por extenso + moeda
])
def test_is_useful_rejeita_ruido(kw):
    assert is_useful_memory_keyword(kw) is False


@pytest.mark.parametrize("kw", ["ifood", "mercado", "pao doce", "padoca ze"])
def test_is_useful_aceita_alvo_curto(kw):
    assert is_useful_memory_keyword(kw) is True


# --- integração: learn_from_inference aprende do alvo, não do text_base ----

def test_aprende_do_target_hint_e_ignora_text_base_ruidoso(user_id):
    # text_base cru e barulhento, mas o parser extraiu o alvo limpo "ifood"
    learn_from_inference(
        user_id,
        "pig gastei uns reais no ifood mais ou menos",
        "alimentacao",
        target_hint="ifood",
        reason="ai",
    )
    rules = db.list_user_category_rules(user_id)
    keywords = [kw for kw, _cat in rules]

    # aprendeu o alvo limpo
    assert "ifood" in keywords
    # e NENHUM keyword carrega ruído de fala
    ruido = {"pig", "reais", "mais", "eu", "menos", "gastei"}
    for kw in keywords:
        assert not (set(kw.split()) & ruido), f"keyword poluída: {kw!r}"
        assert len(kw.split()) <= 2


def test_sem_target_hint_cai_no_text_base_mas_filtrado(user_id):
    # sem alvo extraído: usa o text_base, mas o filtro de ruído limpa o resto
    learn_from_inference(
        user_id,
        "pig eu paguei reais no cinema mais",
        "lazer",
        target_hint=None,
        reason="ai",
    )
    keywords = [kw for kw, _cat in db.list_user_category_rules(user_id)]
    assert "cinema" in keywords
    ruido = {"pig", "reais", "mais", "eu"}
    for kw in keywords:
        assert not (set(kw.split()) & ruido), f"keyword poluída: {kw!r}"


def test_nota_default_do_dashboard_nao_polui(user_id):
    # receita pelo dashboard sem alvo → text_base é a nota default, puro ruído
    learn_from_inference(
        user_id,
        "receita registrada pelo dashboard",
        "salario",
        target_hint=None,
        reason="local_rule",
    )
    assert db.list_user_category_rules(user_id) == []
