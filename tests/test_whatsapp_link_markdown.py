"""Link markdown `[rótulo](url)` da IA no WhatsApp: o cliente não renderiza e a URL saía duas vezes.
Controle negativo: tirar a linha `_LINK_MD.sub` de `format_for_platform` deixa vermelhos os testes de link.
Positivos: texto sem link, URL solta e `**x**` -> `*x*` seguem iguais."""
from __future__ import annotations

import time

import pytest

import core.intent_classifier as ic
from core.response_formatter import format_for_platform
from tests._fusao_of_helpers import uid_pro  # noqa: F401 (fixture)
from tests._ia_falsa_helpers import liga_flag, openai_falso, texto
from tests._pendencia_credito_helpers import diga

U = "https://pigbankai.com/painel"


def wa(t: str) -> str:
    return format_for_platform(t, "whatsapp")


@pytest.mark.parametrize("entrada", [
    f"[{U}]({U})", f"[pigbankai.com/painel]({U})", f"[pigbankai.com/painel/]({U})",
    f"[{U}/]({U})", f"[http://pigbankai.com/painel]({U})",
])
def test_rotulo_igual_a_url_vira_so_a_url(entrada):
    assert wa(entrada) == U


def test_rotulo_diferente_vira_rotulo_dois_pontos_url():
    assert wa(f"[o painel]({U})") == f"o painel: {U}"


def test_dois_links_na_mesma_frase():
    assert wa(f"[a](https://a.com) e [b.com](https://b.com/)") == "a: https://a.com e https://b.com/"


def test_link_dentro_de_negrito():
    assert wa(f"**[painel]({U})**") == f"*painel: {U}*"


def test_link_http_sem_s_tambem_converte():
    assert wa("[x](http://a.com)") == "x: http://a.com"


def test_esquema_em_maiuscula_converte_e_a_url_sai_como_veio():
    assert wa("[pigbankai.com/painel](HTTPS://pigbankai.com/painel)") == "HTTPS://pigbankai.com/painel"
    assert wa("[HTTPS://pigbankai.com/painel](https://pigbankai.com/painel)") == U
    assert wa("[x](Http://a.com)") == "x: Http://a.com"


def test_caminho_tem_caixa_exata():
    assert wa("[pigbankai.com/Painel](https://pigbankai.com/painel)") == f"pigbankai.com/Painel: {U}"


def test_colchete_aberto_antes_do_link_converte_o_link():
    assert wa("[a [b](https://u.com)") == "[a b: https://u.com"


# Sem o limite {1,2000} do grupo da URL, "[a](https://"*5000 levava 1,3 s (quadrático).
@pytest.mark.parametrize("entrada", ["[a](https://" * 5000, "[a](https://a.com" * 2500])
def test_entrada_patologica_nao_e_quadratica(entrada):
    t0 = time.perf_counter()
    wa(entrada)
    assert time.perf_counter() - t0 < 1.0


def test_pontuacao_e_parenteses_em_volta():
    assert wa("(veja [x](https://a.com/b).)") == "(veja x: https://a.com/b.)"


@pytest.mark.parametrize("entrada", [
    "veja [nota] aqui", "[a\nb](https://a.com)", "[a](javascript:alert(1))", "[a](ftp://x.com/f)", "[a] (https://a.com)", "[](https://a.com)",
])
def test_o_que_nao_e_link_http_fica_intacto(entrada):
    assert wa(entrada) == entrada


@pytest.mark.parametrize("entrada,saida", [
    ("sem link nenhum", "sem link nenhum"),
    (f"acesse {U} agora", f"acesse {U} agora"),
    ("**total** R$ 10", "*total* R$ 10"),
    ("use `x` aqui", "use x aqui"),
])
def test_positivo_o_que_ja_funcionava_segue(entrada, saida):
    assert wa(entrada) == saida


@pytest.mark.parametrize("plat", ["discord", "telegram"])
def test_outras_plataformas_nao_mudam_o_link(plat):
    t = f"[pigbankai.com/painel]({U})"
    assert format_for_platform(t, plat) == t


def test_conversa_resposta_da_ia_sai_com_a_url_uma_vez(uid_pro, monkeypatch):
    """A mensagem passa pelo `handle_incoming` e pelo runner real (modelo falso)."""
    liga_flag(monkeypatch)
    openai_falso(monkeypatch, texto(f"🐷 Veja pelo painel em [pigbankai.com/painel]({U}) na seção de Metas"))
    r = diga(uid_pro, "qto sobrou esse mes")
    assert "](" not in r
    assert r.count("pigbankai.com/painel") == 1
    assert U in r


FRASE = f"🐷 Veja pelo painel em [pigbankai.com/painel]({U}) na seção de Metas"


def _so_a_url_uma_vez(r):
    assert "](" not in r
    assert r.count("pigbankai.com/painel") == 1


def test_conversa_comando_da_ia_handle_incoming_1203(uid_pro, monkeypatch):
    """`piggy ...` -> handle_ai_chat_command (flag desligada)."""
    openai_falso(monkeypatch, texto(FRASE))
    _so_a_url_uma_vez(diga(uid_pro, "piggy qto sobrou esse mes"))


def test_conversa_fallback_da_ia_handle_incoming_380(uid_pro, monkeypatch):
    """Frase que o classificador não entende -> `_resposta_da_ia` (flag desligada)."""
    monkeypatch.setattr(ic, "_classify_with_ai", lambda text, user_id=None: ic.IntentResult(intent="out_of_scope", confidence=0.0))
    openai_falso(monkeypatch, texto(FRASE))
    _so_a_url_uma_vez(diga(uid_pro, "xyzzy blorp quantos fnord"))
