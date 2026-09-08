from utils_text import fmt_rate


def test_fmt_rate_cdi_mostra_percentual_correto():
    assert fmt_rate(1.16, "cdi") == "116% CDI"
    assert fmt_rate(1.0, "cdi") == "100% CDI"


def test_fmt_rate_taxa_anual_nao_exibe_periodo():
    assert fmt_rate(0.14, "yearly") == "14%"


def test_fmt_rate_spread_mostra_percentual_humano():
    assert fmt_rate(0.0008, "selic_spread") == "SELIC + 0,08% a.a."
    assert fmt_rate(0.025, "cdi_spread") == "CDI + 2,5% a.a."
    assert fmt_rate(0.0743, "ipca_spread") == "IPCA + 7,43% a.a."


def test_contains_word_palavra_vazia_nao_casa_nada():
    """`\\b\\b` casa QUALQUER texto com uma palavra: sem esta guarda, um nome
    que normaliza para "" (caixinha "💰", keyword de regra só-emoji) sequestra
    todo casamento por palavra do repositório."""
    from utils_text import contains_word, normalize_text

    assert contains_word("tira 100 da poupanca", "") is False
    assert contains_word("tira 100 da poupanca", normalize_text("\U0001f4b0")) is False
    assert contains_word("", "") is False
    # controle POSITIVO: palavra de verdade continua casando
    assert contains_word("tira 100 da poupanca", "poupanca") is True
