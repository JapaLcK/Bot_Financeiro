"""
Cobre `core.services.commands_intent.is_commands_intent`:

- Triggers exatos batem (comandos, exemplos, etc).
- Variações do screenshot do Lucas batem ("do que é capaz", "quais
  suas funções", "o que que eu posso te pedir").
- Frases com mesma palavra mas contexto diferente NÃO batem
  ("gastei na função X").
- Texto vazio/None retorna False.
"""
import pytest

from core.services.commands_intent import is_commands_intent


# ─── Triggers exatos ────────────────────────────────────────────────────────


@pytest.mark.parametrize("txt", [
    "comandos",
    "/comandos",
    "exemplos",
    "explorar",
    "o que pedir",
    "que pedir",
    "o que voce faz",
    "o que vc faz",
    "o que pode fazer",
    "o que voce pode fazer",
    "o que vc pode fazer",
    "lista de comandos",
])
def test_triggers_exatos(txt):
    assert is_commands_intent(txt) is True


# ─── Variações do screenshot ────────────────────────────────────────────────


@pytest.mark.parametrize("txt", [
    "Do que você é capaz?",
    "Do que voce eh capaz?",
    "Do que vc é capaz",
    "Quais são suas funções?",
    "Quais suas funções",
    "quais sao suas funcoes",
    "Quais são suas capacidades?",
    "O que que eu posso te pedir?",
    "O que eu posso te pedir?",
    "O que posso te pedir",
    "O que você sabe?",
    "O que vc sabe fazer?",
    "O que você consegue?",
    "Suas funções?",
    "Me ajuda com o que?",
])
def test_variacoes_do_screenshot(txt):
    assert is_commands_intent(txt) is True, f"deveria ter batido: {txt!r}"


# ─── Casos negativos (não devem disparar) ───────────────────────────────────


@pytest.mark.parametrize("txt", [
    "saldo",
    "gastei 50 no mercado",
    "qual a função de uma fatura?",  # "função" mas contexto diferente
    "preciso de ajuda com o cartão",  # NÃO é intent meta
    "como funciona o parcelamento",  # NÃO — pergunta sobre conceito
    "quanto eu devo",
    "minha fatura do Nubank",
    "",
])
def test_negativos(txt):
    assert is_commands_intent(txt) is False, f"NÃO deveria ter batido: {txt!r}"


def test_none_retorna_false():
    assert is_commands_intent(None) is False


# ─── Normalização (case, acentos, pontuação) ────────────────────────────────


def test_case_insensitive():
    assert is_commands_intent("COMANDOS") is True
    assert is_commands_intent("Comandos") is True


def test_ignora_pontuacao_final():
    assert is_commands_intent("comandos?") is True
    assert is_commands_intent("o que voce faz!") is True
    assert is_commands_intent("do que é capaz...") is True


def test_remove_acentos():
    assert is_commands_intent("comandos") is True
    # Versão com acento também passa
    assert is_commands_intent("Do que você é capaz") is True
