"""`core/services/asaas.py` — o cliente HTTP do Asaas (§10, §10.1).

## Como se testa cliente HTTP com a rede BLOQUEADA

O kill switch do `tests/conftest.py` (`_block_outbound_network`) bloqueia por
**TRANSPORTE, não por classe**: só `httpx.HTTPTransport`/`AsyncHTTPTransport`
abrem socket, e o `_TestClientTransport` do Starlette não. `httpx.MockTransport`
também não é `HTTPTransport` — então ele **passa pelo bloqueio e roda
in-process**, que é exatamente o que se quer.

O jeito de injetá-lo é trocar `httpx.Client` **no namespace do módulo** por uma
fábrica que devolve um `Client(transport=MockTransport(...))`. Não se toca no
`conftest`: mexer no kill switch para testar um cliente novo é como o furo do
PR #133 nasceu (nada falha, a chamada só sai).

A asserção não é "chamou": é **método, path, body e headers**. Um teste que só
verificasse "não estourou" passaria com o verbo errado, com o valor em centavos
onde o Asaas espera reais, e com a `access_token` na query string.

## O erro SEM corpo se testa sem transporte nenhum

`_raise_for_asaas_response` recebe uma `httpx.Response` FABRICADA. A ausência do
corpo é propriedade da FUNÇÃO — testá-la através de uma chamada mockada mediria
o mock, e um refactor que passasse a montar a mensagem noutro lugar sairia
verde.

Isso importa porque `str(exc)` desta exceção vai para `log_system_event`
(persistido em `system_event_logs`, lido pelo painel admin) e para
`pix_webhook_events.last_error`, que SOBREVIVE à purga do payload (§13.3) e à
exclusão da conta.

CONTROLES NEGATIVOS MEDIDOS:

  * faça `_raise_for_asaas_response` incluir `resp.text` na mensagem →
    `test_erro_nao_carrega_o_corpo_da_resposta` vermelho;
  * tire o `status_code=` do `AsaasApiError` →
    `test_erro_preserva_o_status_code` vermelho (e a reconciliação do §10.1
    perde como separar 404 de 5xx);
  * mande a `access_token` em `params` em vez de `headers` →
    `test_token_vai_no_header_nunca_na_url` vermelho;
  * troque `value` por centavos (`valor_cents` cru) →
    `test_criar_pagamento_manda_reais_e_nao_centavos` vermelho.

POSITIVO do grupo: `test_busca_por_external_reference_devolve_a_lista` — sem
ele, um código que levantasse em toda resposta passaria nos negativos.

CEGUEIRA DECLARADA: nada aqui prova o contrato REAL do Asaas (nome dos campos,
formato do `dueDate`, o header que o provedor de fato aceita). Isso é §18 do
plano — só contra o Sandbox, e não neste ambiente.
"""

from types import SimpleNamespace

import httpx
import pytest

import core.services.asaas as asaas
from core.services.asaas import (
    AsaasApiError,
    AsaasConfigError,
    _raise_for_asaas_response,
    buscar_por_external_reference,
    criar_pagamento_pix,
    deletar_pagamento,
)


@pytest.fixture()
def chamadas(monkeypatch):
    """Troca `httpx.Client` NO NAMESPACE DO MÓDULO por uma fábrica que injeta um
    `MockTransport`. Devolve a lista de requisições vistas.

    `MockTransport` não é `HTTPTransport`, então o kill switch do conftest o
    deixa passar e a chamada roda in-process — sem socket, sem `.invalid`, sem
    tocar o `conftest`.
    """
    espiao = SimpleNamespace(vistas=[], json={"data": [], "id": "pay_x"}, status=200)

    def handler(request: httpx.Request) -> httpx.Response:
        espiao.vistas.append(request)
        return httpx.Response(espiao.status, json=espiao.json)

    def fabrica(*a, **kw):
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    # Só `Client` é substituído: é o único atributo de `httpx` que o módulo usa
    # em RUNTIME (`httpx.Response` do type hint não é avaliado, por causa do
    # `from __future__ import annotations`). Trocar o módulo inteiro esconderia
    # um uso novo atrás de um `AttributeError` confuso.
    monkeypatch.setattr(asaas, "httpx", SimpleNamespace(Client=fabrica))
    monkeypatch.setenv("ASAAS_API_KEY", "chave_de_teste")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://sandbox.asaas.test")
    return espiao


# ── método, path, body e headers ─────────────────────────────────────────────

def test_criar_pagamento_manda_reais_e_nao_centavos(chamadas):
    """O resto do sistema fala em CENTAVOS inteiros (§3.2, "sem float"); o Asaas
    fala em reais. A divisão por 100 acontece num lugar só, o mais tarde
    possível — mandar 29900 aqui cobraria R$ 29.900,00."""
    import json

    criar_pagamento_pix(customer_id="cus_1", valor_cents=29900,
                        due_date="2026-09-14", external_reference="pix:42")
    req = chamadas.vistas[0]
    assert req.method == "POST"
    assert req.url.path == "/v3/payments"
    corpo = json.loads(req.content)
    assert corpo["value"] == 299.0
    assert corpo["billingType"] == "PIX"
    assert corpo["customer"] == "cus_1"
    assert corpo["dueDate"] == "2026-09-14"
    assert corpo["externalReference"] == "pix:42"


def test_token_vai_no_header_nunca_na_url(chamadas):
    """URL vai para log de proxy, para `Referer` e para relatório de erro. A
    `access_token` do Asaas é credencial de MOVER DINHEIRO."""
    criar_pagamento_pix(customer_id="cus_1", valor_cents=500,
                        due_date="2026-09-14", external_reference="pix:1")
    req = chamadas.vistas[0]
    assert req.headers["access_token"] == "chave_de_teste"
    assert "chave_de_teste" not in str(req.url)


def test_busca_por_external_reference_devolve_a_lista(chamadas):
    """POSITIVO do grupo. E a lista VAZIA é uma resposta legítima (§10.1): é a
    única prova que autoriza apagar a linha local."""
    chamadas.json = {"data": [{"id": "pay_7", "status": "PENDING"}]}
    achados = buscar_por_external_reference("pix:42")
    req = chamadas.vistas[0]
    assert req.method == "GET"
    assert req.url.path == "/v3/payments"
    assert req.url.params["externalReference"] == "pix:42"
    assert achados == [{"id": "pay_7", "status": "PENDING"}]

    chamadas.json = {"data": []}
    assert buscar_por_external_reference("pix:99") == []


def test_resposta_sem_data_nao_vira_lista_falsa(chamadas):
    """Resposta com `data` ausente ou de outro tipo devolve `[]` — e **lista
    vazia autoriza APAGAR a cobrança** (§10.1, regra (b)). Ou seja: aqui o lado
    "defensivo" cai no lado perigoso.

    O teste fixa o comportamento e nomeia o teto em vez de escondê-lo. O que
    impede o apagamento errado NÃO é esta função: é a segunda condição da regra
    (b), `asaas_payment_id is null`, que mora no 1b-B. Uma cobrança que já ganhou
    id remoto nunca é apagada, mesmo com a lista vazia.

    ponytail: se o Asaas mudar a forma da resposta, o conserto é distinguir
    "consultei e não achou" de "não entendi a resposta" — um sentinela, não um
    `[]`. Fazer isso agora seria escrever a regra (b) sem o chamador dela.
    """
    chamadas.json = {"erro": "forma inesperada"}
    assert buscar_por_external_reference("pix:1") == []


def test_deletar_usa_o_verbo_delete_e_o_id_no_path(chamadas):
    deletar_pagamento("pay_7")
    req = chamadas.vistas[0]
    assert req.method == "DELETE"
    assert req.url.path == "/v3/payments/pay_7"


def test_sem_api_key_levanta_config_error(monkeypatch):
    """Falta de configuração é 503 do nosso lado, não erro do provedor — e tem
    de estourar ANTES de qualquer socket."""
    monkeypatch.delenv("ASAAS_API_KEY", raising=False)
    with pytest.raises(AsaasConfigError):
        deletar_pagamento("pay_1")


# ── o erro, sem transporte nenhum ────────────────────────────────────────────

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
    `log_system_event` (PERSISTIDO, lido pelo painel admin) e
    `pix_webhook_events.last_error` (sobrevive à purga do payload E à exclusão
    da conta, §13.3).

    Fabricada, sem transporte: a ausência do corpo é propriedade da FUNÇÃO.
    """
    with pytest.raises(AsaasApiError) as exc:
        _raise_for_asaas_response(_resp(400, CORPO_COM_PII), "Falha ao criar")
    msg = str(exc.value)
    for pii in ("12345678901", "Fulano", "fulano@example.com", "inválido"):
        assert pii not in msg, f"{pii!r} vazou para a mensagem persistida: {msg}"
    # POSITIVO: sobra o que serve para depurar.
    assert "400" in msg and "invalid_cpfCnpj" in msg


def test_erro_preserva_o_status_code():
    """404 é resposta de NEGÓCIO na reconciliação (§10.1: a cobrança não existe
    lá); 5xx é indisponibilidade, e indisponibilidade NÃO é evidência de
    inexistência. Sem o `status_code`, o único jeito de separar as duas seria
    regex na mensagem — e a decisão que depende disso APAGA cobrança."""
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


# ── falha de transporte e corpo ilegível (o contrato que o 1b-B consome) ─────

def _cliente_que_levanta(monkeypatch, exc):
    def fabrica(*a, **kw):
        def handler(request):
            raise exc
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(asaas, "httpx", SimpleNamespace(Client=fabrica,
                                                        HTTPError=httpx.HTTPError))
    monkeypatch.setenv("ASAAS_API_KEY", "k")
    monkeypatch.setenv("ASAAS_BASE_URL", "https://sandbox.asaas.test")


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("sem rota"),
    httpx.ReadTimeout("demorou"),
    httpx.RemoteProtocolError("servidor fechou"),
])
def test_falha_de_transporte_vira_asaas_api_error(monkeypatch, exc):
    """A docstring de `buscar_por_external_reference` promete que
    indisponibilidade NÃO se confunde com inexistência. Sem este tratamento a
    promessa era só texto: `ConnectError` e `ReadTimeout` escapavam CRUS.

    Importa porque o 1b-B decide **apagar cobrança** com esse contrato (§10.1,
    regra (b)): um timeout que suba como outro tipo pode ser engolido por um
    `except Exception` do chamador e virar "consultei e não achou" — que é
    dinheiro sem linha.

    `status_code is None` distingue "não houve resposta" de 404.
    """
    _cliente_que_levanta(monkeypatch, exc)
    with pytest.raises(AsaasApiError) as capturado:
        buscar_por_external_reference("pix:42")
    assert capturado.value.status_code is None
    assert type(exc).__name__ in str(capturado.value)


def test_falha_de_transporte_nao_vaza_a_url(monkeypatch):
    """A mensagem do httpx carrega a URL, e a URL carrega o `externalReference`.
    Esta string é persistida em `pix_webhook_events.last_error`."""
    _cliente_que_levanta(monkeypatch, httpx.ConnectError("erro em https://x/y?ref=pix:42"))
    with pytest.raises(AsaasApiError) as capturado:
        buscar_por_external_reference("pix:42")
    assert "pix:42" not in str(capturado.value)


@pytest.mark.parametrize("status,corpo", [(200, "<html>proxy</html>"), (204, "")])
def test_sucesso_sem_json_valido_vira_asaas_api_error(chamadas, monkeypatch, status, corpo):
    """`is_success` passa e o `resp.json()` estoura: 200 com HTML de proxy, 204
    sem corpo. Sem tratamento era um `ValueError` solto no meio da venda —
    exceção de tipo inesperado no caminho que decide apagar cobrança."""
    def handler(request):
        return httpx.Response(status, text=corpo)

    monkeypatch.setattr(asaas, "httpx", SimpleNamespace(
        Client=lambda *a, **kw: httpx.Client(transport=httpx.MockTransport(handler), **kw),
        HTTPError=httpx.HTTPError))
    with pytest.raises(AsaasApiError) as capturado:
        buscar_por_external_reference("pix:42")
    assert capturado.value.status_code == status


def test_timeout_invalido_nao_derruba_a_venda(monkeypatch):
    """Erro de digitação na env é problema de operação, não motivo para recusar
    cobrança. `float("vinte")` estourava dentro do `_request`, ou seja na hora
    da venda."""
    monkeypatch.setenv("ASAAS_TIMEOUT", "vinte")
    assert asaas._timeout() == 20.0
    monkeypatch.setenv("ASAAS_TIMEOUT", "5.5")
    assert asaas._timeout() == 5.5


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
