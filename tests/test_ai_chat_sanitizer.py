"""
Cobre os helpers defensivos de `core/services/ai_chat/sanitizer.py`:

- `strip_markdown_headers`: rede de proteção contra o LLM escrever `###`
  literal no WhatsApp (Regra 0 do system prompt).
- `detect_trend_window`: detector usado pelo runner pra fazer override de
  `report_out_of_scope` → `get_spending_trend` quando o user pede tendência.

Puro Python, sem DB — testes rápidos.
"""
from core.services.ai_chat.sanitizer import (
    detect_trend_window,
    strip_markdown_headers,
)


# ─── strip_markdown_headers ─────────────────────────────────────────────────


def test_strip_converte_h3_em_bold():
    assert strip_markdown_headers("### Resumo") == "*Resumo*"


def test_strip_converte_h1_h2_h3_h6():
    assert strip_markdown_headers("# Foo") == "*Foo*"
    assert strip_markdown_headers("## Bar") == "*Bar*"
    assert strip_markdown_headers("### Baz") == "*Baz*"
    assert strip_markdown_headers("###### Qux") == "*Qux*"


def test_strip_funciona_em_multilinha():
    raw = "### Resumo\n· Média: R$ 100\n\n### Gastos por Mês\n· Abril: R$ 200"
    out = strip_markdown_headers(raw)
    assert out == "*Resumo*\n· Média: R$ 100\n\n*Gastos por Mês*\n· Abril: R$ 200"


def test_strip_ignora_hash_no_meio_da_frase():
    assert strip_markdown_headers("o gasto #123 foi caro") == "o gasto #123 foi caro"


def test_strip_ignora_hash_sem_espaco_apos():
    # `#tag` sem espaço não é heading markdown — deixa em paz
    assert strip_markdown_headers("#hashtag") == "#hashtag"


def test_strip_aceita_espacos_antes_do_hash():
    assert strip_markdown_headers("  ### Foo") == "*Foo*"


def test_strip_preserva_string_vazia_e_none():
    assert strip_markdown_headers("") == ""
    assert strip_markdown_headers(None) is None


def test_strip_nao_mexe_em_texto_sem_headers():
    txt = "🐷 Seu saldo é R$ 1.234,56.\n• Conta: Itaú\n• Última atualização: ontem"
    assert strip_markdown_headers(txt) == txt


def test_strip_nao_mexe_em_negrito_existente():
    assert strip_markdown_headers("*Resumo*\n· total: 100") == "*Resumo*\n· total: 100"


# ─── detect_trend_window ────────────────────────────────────────────────────


def test_trend_deste_ano_retorna_12():
    assert detect_trend_window("piggy minha tendência deste ano") == 12
    assert detect_trend_window("qual a tendência do ano?") == 12
    assert detect_trend_window("evolução anual dos meus gastos") == 12


def test_trend_trimestre_retorna_3():
    assert detect_trend_window("tendência do trimestre") == 3
    assert detect_trend_window("evolução trimestral") == 3


def test_trend_n_meses_retorna_n():
    assert detect_trend_window("tendência dos últimos 6 meses") == 6
    assert detect_trend_window("evolução nos 3 meses") == 3
    assert detect_trend_window("tendência últimos 9 meses") == 9


def test_trend_n_meses_respeita_clamp():
    assert detect_trend_window("tendência últimos 99 meses") == 24
    assert detect_trend_window("tendência últimos 0 meses") == 1


def test_trend_sem_janela_default_6():
    assert detect_trend_window("como tá a tendência?") == 6
    assert detect_trend_window("qual minha evolução de gastos?") == 6


def test_trend_mes_a_mes():
    assert detect_trend_window("mostra meus gastos mês a mês") == 6


def test_trend_nao_dispara_sem_palavra_chave():
    # "ano" sozinho não é tendência — sem palavra-chave, retorna None
    assert detect_trend_window("quanto gastei no ano?") is None
    assert detect_trend_window("saldo do trimestre") is None
    assert detect_trend_window("últimos 3 meses") is None


def test_trend_nao_dispara_em_texto_aleatorio():
    assert detect_trend_window("qual meu saldo?") is None
    assert detect_trend_window("oi piggy") is None
    assert detect_trend_window("") is None
    assert detect_trend_window(None) is None


def test_trend_case_insensitive():
    assert detect_trend_window("TENDÊNCIA DESTE ANO") == 12
    assert detect_trend_window("Tendência Trimestral") == 3
