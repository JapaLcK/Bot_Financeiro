"""`core/services/asaas.py` — a FORMA do erro e a leitura da configuração.

Arquivo próprio, separado de `tests/test_asaas_client.py` (que pina método,
path, body e headers), porque nada aqui precisa de transporte: são propriedades
de FUNÇÃO, e testá-las através de uma chamada mockada mediria o mock.

## Por que a forma do erro é caminho de dinheiro

`str(exc)` desta exceção vai para `log_system_event` (persistido em
`system_event_logs`, lido pelo painel admin) e para
`pix_webhook_events.last_error`, que **sobrevive à purga do payload** (§13.3) e
à exclusão da conta. É o pior lugar do schema para PII, e o corpo de erro do
Asaas carrega nome, CPF e e-mail do titular.

E o `status_code` decide o que a reconciliação faz: **404** é resposta de
negócio (§10.1, a cobrança não existe lá) e **5xx** é indisponibilidade, que
NÃO é evidência de inexistência. Sem ele, separar as duas exigiria regex na
mensagem — e a decisão que depende disso APAGA cobrança.

## E por que a configuração entra aqui

`_timeout` é lido antes de qualquer socket, e um valor que **parseia mas não
serve** (`-1`, `nan`, `inf`) levanta `ValueError`/`OverflowError` dentro do
`httpx` — **nenhum dos dois é `httpx.HTTPError`**, então escaparia do `except`
do `_request` e a venda falharia FORA do contrato `AsaasApiError`. Medido.

CEGUEIRA DECLARADA: nada aqui prova o contrato REAL do Asaas (nome dos campos,
o `code` que ele de fato manda). É o §18 — só contra o Sandbox.
"""

import httpx
import pytest

import core.services.asaas as asaas
from core.services.asaas import AsaasApiError, _raise_for_asaas_response


CORPO_COM_PII = {
    "errors": [{
        "code": "invalid_cpfCnpj",
        "description": "O CPF 12345678901 de Fulano de Tal (fulano@example.com) é inválido",
    }]
}


def _resp(status: int, json_corpo) -> httpx.Response:
    return httpx.Response(status, json=json_corpo,
                          request=httpx.Request("POST", "https://asaas.test/v3/payments"))


def test_erro_nao_carrega_o_corpo_da_resposta():
    """A propriedade que mais custa se quebrar: `str(exc)` vira `details` de
    `log_system_event` (persistido, lido pelo painel admin) e
    `pix_webhook_events.last_error`, que sobrevive à purga do payload E à
    exclusão da conta (§13.3). Fabricada, sem transporte: a ausência do corpo é
    propriedade da FUNÇÃO."""
    with pytest.raises(AsaasApiError) as exc:
        _raise_for_asaas_response(_resp(400, CORPO_COM_PII), "Falha ao criar")
    msg = str(exc.value)
    for pii in ("12345678901", "Fulano", "fulano@example.com", "inválido"):
        assert pii not in msg, f"{pii!r} vazou para a mensagem persistida: {msg}"
    # POSITIVO: sobra o que serve para depurar.
    assert "400" in msg and "invalid_cpfCnpj" in msg


def test_erro_preserva_o_status_code():
    """404 é resposta de NEGÓCIO na reconciliação (§10.1); 5xx é
    indisponibilidade, que NÃO é evidência de inexistência. Sem `status_code`, o
    único jeito de separar seria regex na mensagem — e a decisão que depende
    disso APAGA cobrança."""
    for status in (404, 429, 500, 503):
        with pytest.raises(AsaasApiError) as exc:
            _raise_for_asaas_response(_resp(status, {"errors": []}), "ctx")
        assert exc.value.status_code == status


def test_code_so_passa_se_tiver_forma_de_codigo():
    """A descrição do erro vem no MESMO objeto que o `code`. Filtrar por FORMA
    (alfanumérico curto) é o que impede alguém de ampliar isto para "só a
    description, que é curtinha"."""
    longo = {"errors": [{"code": "x" * 61}]}
    with pytest.raises(AsaasApiError) as exc:
        _raise_for_asaas_response(_resp(400, longo), "ctx")
    assert exc.value.code is None

    com_espaco = {"errors": [{"code": "CPF do Fulano invalido"}]}
    with pytest.raises(AsaasApiError) as exc:
        _raise_for_asaas_response(_resp(400, com_espaco), "ctx")
    assert exc.value.code is None
    assert "Fulano" not in str(exc.value)


def test_corpo_nao_json_nao_derruba_o_tratamento():
    """O provedor fora do ar devolve HTML de gateway, não JSON. O `.json()`
    estourando ali transformaria uma indisponibilidade tratável num `ValueError`
    solto no meio da venda."""
    resp = httpx.Response(502, text="<html>bad gateway</html>",
                          request=httpx.Request("GET", "https://asaas.test/x"))
    with pytest.raises(AsaasApiError) as exc:
        _raise_for_asaas_response(resp, "ctx")
    assert exc.value.status_code == 502
    assert "html" not in str(exc.value).lower()


def test_resposta_de_sucesso_nao_levanta():
    """POSITIVO: sem ele o grupo passaria num código que levanta sempre."""
    assert _raise_for_asaas_response(_resp(200, {"id": "pay_1"}), "ctx") is None


def test_code_com_forma_de_cpf_e_recusado():
    """A forma de um CPF é a de um código curto: alfanumérico, sem espaço, 11
    chars. O `code` do Asaas é sempre nominal (`invalid_cpfCnpj`), então recusar
    só-dígitos custa nada e fecha o caminho por onde um documento entraria numa
    string que é PERSISTIDA e sobrevive à exclusão da conta."""
    for documento in ("12345678901", "123.456.789-01", "12345678000199"):
        with pytest.raises(AsaasApiError) as capturado:
            _raise_for_asaas_response(_resp(400, {"errors": [{"code": documento}]}), "ctx")
        assert capturado.value.code is None
        assert documento not in str(capturado.value)
    # POSITIVO: o código nominal continua passando.
    with pytest.raises(AsaasApiError) as capturado:
        _raise_for_asaas_response(_resp(400, {"errors": [{"code": "invalid_cpfCnpj"}]}), "ctx")
    assert capturado.value.code == "invalid_cpfCnpj"


@pytest.mark.parametrize("bruto", ["-1", "0", "nan", "inf", "-inf", "vinte", ""])
def test_timeout_que_parseia_mas_nao_SERVE_cai_no_padrao(monkeypatch, bruto):
    """Parsear não é servir. Medido contra o httpx real: `-1` e `nan` levantam
    `ValueError`, `inf` levanta `OverflowError` — e **nenhum é
    `httpx.HTTPError`**, então escapavam do `except` do `_request` e a venda
    falhava FORA do contrato `AsaasApiError` que este módulo promete.

    Timeout zero entra na lista porque "desista imediatamente" é, na prática,
    não cobrar. `"vinte"` e `""` também: este teste ABSORVEU o antigo
    `test_timeout_invalido_nao_derruba_a_venda`, que virou subconjunto estrito
    dele — duas versões da mesma regra é o que o §0.7 proíbe.

    *Negativo: tire o `math.isfinite(valor) and valor > 0` → as cinco primeiras
    linhas ficam vermelhas.*
    """
    monkeypatch.setenv("ASAAS_TIMEOUT", bruto)
    assert asaas._timeout() == 20.0


def test_timeout_valido_continua_valendo(monkeypatch):
    """POSITIVO: sem ele, um `_timeout` que devolvesse sempre 20 passaria acima
    e a env deixaria de ter efeito nenhum."""
    monkeypatch.setenv("ASAAS_TIMEOUT", "7.5")
    assert asaas._timeout() == 7.5
