"""`core/services/asaas.py` — o cliente HTTP do Asaas (§10, §10.1).

## Como se testa cliente HTTP com a rede BLOQUEADA

O kill switch do `tests/conftest.py` (`_block_outbound_network`) bloqueia por
**TRANSPORTE, não por classe**: só `httpx.HTTPTransport`/`AsyncHTTPTransport`
abrem socket, e o `_TestClientTransport` do Starlette não. `httpx.MockTransport`
também não é `HTTPTransport` — então ele **passa pelo bloqueio e roda
in-process**, que é exatamente o que se quer.

Injeta-se trocando `httpx.Client` **no namespace do módulo** por uma fábrica que
devolve `Client(transport=MockTransport(...))`. Não se toca no `conftest`: mexer
no kill switch para testar cliente novo é como o furo do #133 nasceu.

A asserção não é "chamou": é **método, path, body e headers**. Um teste que só
verificasse "não estourou" passaria com o verbo errado, com o valor em centavos
onde o Asaas espera reais, e com a `access_token` na query string.

## O erro SEM corpo se testa sem transporte nenhum

`_raise_for_asaas_response` recebe uma `httpx.Response` FABRICADA: a ausência do
corpo é propriedade da FUNÇÃO, e testá-la por uma chamada mockada mediria o
mock. Importa porque `str(exc)` vai para `log_system_event` (persistido, lido
pelo painel admin) e para `pix_webhook_events.last_error`, que SOBREVIVE à purga
do payload (§13.3) e à exclusão da conta.

CONTROLES NEGATIVOS MEDIDOS:

  * faça `_raise_for_asaas_response` incluir `resp.text` na mensagem →
    `test_erro_nao_carrega_o_corpo_da_resposta` vermelho;
  * tire o `status_code=` do `AsaasApiError` →
    `test_erro_preserva_o_status_code` vermelho (e a reconciliação do §10.1
    perde como separar 404 de 5xx);
  * `access_token` em `params` → `test_token_vai_no_header_nunca_na_url`;
  * `value` em centavos crus → `test_criar_pagamento_manda_reais_e_nao_centavos`;
  * `else []` na busca → as 5 linhas de `test_forma_inesperada_NAO_vira_lista_vazia`.

POSITIVOS: `test_busca_por_external_reference_devolve_a_lista` e
`test_lista_vazia_BEM_FORMADA_continua_sendo_resposta` — sem eles, um código que
levantasse em toda resposta passaria nos negativos.

CEGUEIRA DECLARADA: nada aqui prova o contrato REAL do Asaas (nome dos campos,
formato do `dueDate`, o header aceito). É o §18 — só contra o Sandbox.
"""

from types import SimpleNamespace

import httpx
import pytest

import core.services.asaas as asaas
from core.services.asaas import (
    AsaasApiError,
    AsaasConfigError,
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


@pytest.mark.parametrize("corpo", [
    {"erro": "forma inesperada"},          # 2xx sem `data`
    {"data": {"id": "pay_7"}},             # `data` que não é lista
    {"data": None},
    ["pay_7"],                             # o corpo inteiro noutro formato
    "ok",
])
def test_forma_inesperada_NAO_vira_lista_vazia(chamadas, corpo):
    """P1-2 do Codex, e a correção de uma justificativa minha que estava errada.

    Lista vazia é a metade que, com `asaas_payment_id is null`, AUTORIZA apagar
    a linha (§10.1, regra (b)); converter resposta malformada em `[]` fabricava
    essa prova. Cenário: o POST efetiva no Asaas, a resposta se perde, a linha
    fica sem `asaas_payment_id`, o GET de reconciliação volta 2xx com forma
    inesperada — e a limpeza apaga a linha **com uma cobrança pagável viva no
    Asaas**.

    Eu marquei isso como teto dizendo que "quem segura é a segunda condição da
    regra (b)". Errado: as metades são um `and` e o cenário satisfaz as DUAS —
    a guarda que invoquei era a outra metade da condição que autoriza apagar.

    *Negativo: volte o `else []` → todas estas linhas ficam vermelhas.*
    """
    chamadas.json = corpo
    with pytest.raises(AsaasApiError) as capturado:
        buscar_por_external_reference("pix:1")
    assert capturado.value.status_code is None, "'não sei' não pode virar 404"


def test_lista_vazia_BEM_FORMADA_continua_sendo_resposta(chamadas):
    """POSITIVO do par, e ele não é cerimônia: sem esta linha o grupo passaria
    num código que levanta em TODA consulta — e aí a reconciliação do §10.1
    nunca conseguiria apagar `draft` órfão nenhum, que é o outro lado do erro."""
    chamadas.json = {"data": []}
    assert buscar_por_external_reference("pix:99") == []


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
