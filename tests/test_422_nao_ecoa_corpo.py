"""O 422 não devolve o corpo da requisição — #369, a última categoria aberta.

MEDIDO nesta árvore ANTES do conserto (handler delegando direto ao FastAPI):
`POST /auth/login {"password": "senhaforte123"}`, sem `email`, respondia
`422 {"detail":[{"type":"missing","loc":["body","email"],"msg":"Field required",
"input":{"password":"senhaforte123"}}]}` — a SENHA em claro de volta para quem
a mandou. O `input` do erro `missing` é o objeto PAI, ou seja, o corpo inteiro,
e isso vale para TODA rota com modelo Pydantic: não é defeito das rotas de auth
nem do validador de veneno do #369. Por isso o conserto é no handler único de
422 (`validation_exception_page_handler`) e por isso um dos testes abaixo mede
uma rota que NÃO é de auth — categoria, não instância (§2).

Arquivo separado de `test_auth_corpo_venenoso.py` de propósito (§0.5): lá o
assunto é "corpo venenoso não pode virar 500 nas rotas de auth"; aqui é "o 422
de QUALQUER rota não pode ecoar o corpo". O outro arquivo está em 349 linhas,
com teto de 350 (`tests/test_max_lines_python.py`).

CONTROLE NEGATIVO (desligar o conserto é editar o handler; medido à mão):
tirando o `sem_input` de `validation_exception_page_handler`,
`test_senha_nao_volta_no_422` e `test_rota_que_nao_e_de_auth_tambem_nao_ecoa`
ficam VERMELHOS — os dois pela senha/marca no corpo e pelo `input` presente.
CONTROLE POSITIVO: `test_422_continua_dizendo_qual_campo_falta` — um handler
que respondesse `{"detail": []}` esconderia o corpo e seria PIOR que o bug,
porque o cliente deixaria de saber qual campo está errado.
"""
import asyncio
import json
import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError

import frontend.finance_bot_websocket_custom as dashboard
from _corpo_json_helpers import _client, _post_bruto

SENHA = "senhaforte123"
# Marca que só existe aqui: se voltar no corpo, veio do eco e de mais nada.
MARCA = "NAO-PODE-VOLTAR-9f3"
SURR_ALTO = "\ud800"


def _detalhe(resposta):
    return resposta.json()["detail"]


def test_senha_nao_volta_no_422():
    """O caso que o usuário dispara sem querer: errou/esqueceu um campo do
    login e a senha voltava no JSON de erro (console do navegador, log de
    proxy, APM). Não passa pelo `admin_error_logging_middleware`, então nunca
    esteve em `system_event_logs` — o vazamento é para o cliente e para quem
    estiver no caminho."""
    r = _post_bruto(_client(), "/auth/login", {"password": SENHA})  # falta `email`
    assert r.status_code == 422, r.text
    assert SENHA not in r.text, r.text
    assert all("input" not in erro for erro in _detalhe(r)), r.text


def test_422_continua_dizendo_qual_campo_falta():
    """Sem este, um handler que devolvesse `{"detail": []}` passaria no teste
    de cima. O formato continua sendo o do FastAPI MENOS o `input`."""
    erro = _detalhe(_post_bruto(_client(), "/auth/login", {"password": SENHA}))[0]
    assert erro["type"] == "missing", erro
    assert erro["loc"] == ["body", "email"], erro
    assert erro["msg"], erro


def test_rota_que_nao_e_de_auth_tambem_nao_ecoa():
    """A categoria: `/investments/{id}` não herda `_CorpoSemVeneno` (é a #321)
    e é autenticada — o 422 sai ANTES do 401, porque a validação de corpo roda
    antes do handler. `missing` de propósito: é o erro cujo `input` é o corpo
    inteiro (num erro de TIPO o `input` é só o campo, e o teste mediria menos)."""
    r = _post_bruto(_client(), "/investments/1",
                    {"name": MARCA, "initial_amount": 10.0})  # faltam `rate`/`period`
    assert r.status_code == 422, r.text
    assert MARCA not in r.text, r.text
    assert _detalhe(r)[0]["type"] == "missing", r.text


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/x", "headers": [],
                    "query_string": b"", "scheme": "https", "server": ("testserver", 443)})


def test_cinto_responde_422_quando_o_erro_nao_serializa(caplog):
    """O ramo `except` do handler, agora que o `input` saiu de TODO 422.

    MEDIDO depois da supressão: nenhum dos três venenos do #369 (surrogate,
    não finito, aninhamento fundo) entra mais no `except` pela rota — eles
    viajavam todos no `input`. O ramo fica como cinto para o que sobra no erro
    e NÃO é nosso: o `ctx` (que continua indo ao cliente — medido,
    `"ctx":{"error":{}}`) e a `msg` de um validador futuro que ecoe o valor
    recebido em vez do nome do campo. Como pela rota é inalcançável hoje, o
    teste chama o handler direto com um erro assim; um `except` removido
    levanta `UnicodeEncodeError` aqui e o teste fica vermelho."""
    exc = RequestValidationError([{
        "type": "value_error", "loc": ("body", "x"),
        "msg": f"Value error, AAA{SURR_ALTO}BBB",
        "input": {"password": SENHA}, "ctx": {"error": {}},
    }])
    with caplog.at_level(logging.INFO, logger=dashboard.__name__):
        resposta = asyncio.run(dashboard.validation_exception_page_handler(_request(), exc))
    assert resposta.status_code == 422
    erro = json.loads(resposta.body)["detail"][0]
    assert erro["loc"] == ["body", "x"], erro
    assert "�" in erro["msg"], erro          # saneado pelo `limpa_para_pg`
    assert "input" not in erro and "ctx" not in erro, erro
    assert SENHA not in resposta.body.decode(), resposta.body
    assert any("422 sem input" in m for m in caplog.messages), caplog.messages
