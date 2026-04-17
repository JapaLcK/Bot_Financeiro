from utils_text import fmt_rate


def test_fmt_rate_cdi_mostra_percentual_correto():
    assert fmt_rate(1.16, "cdi") == "116% CDI"
    assert fmt_rate(1.0, "cdi") == "100% CDI"


def test_fmt_rate_taxa_anual_nao_exibe_periodo():
    assert fmt_rate(0.14, "yearly") == "14%"
