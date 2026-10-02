"""Contrato da /api/v2 com o frontend: tipos TS gerados e envelope de erro.

- o `api-v2.gen.ts` commitado é a saída do gerador sobre o `openapi()` de hoje;
- o gerador traduz cada construção aceita e recusa as outras;
- o `ErroV2` descreve as respostas de erro REAIS do monólito, e as fixtures que os
  testes de navegador servem (`tests/frontend/api_v2_respostas.json`) seguem os modelos.
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import frontend.finance_bot_websocket_custom as dashboard
from api.v2 import app as app_v2
from api.v2.erros import ErroV2
from api.v2.me import Me
from scripts.gerar_tipos_api_v2 import CABECALHO, SAIDA, gerar
from test_api_v2_erros import Login, _corpo_do_422, rota_temporaria  # noqa: F401 (fixture)

FIXTURES = json.loads((Path(__file__).parent / "frontend" / "api_v2_respostas.json").read_text(encoding="utf-8"))


def test_tipos_gerados_em_dia():
    assert gerar(app_v2.openapi()) == SAIDA.read_text(encoding="utf-8"), (
        "webapp/src/dashboard/lib/api-v2.gen.ts está velho: rode "
        "`python scripts/gerar_tipos_api_v2.py` e depois `npm --prefix webapp run build`.")


def _spec(schemas, paths=None):
    return {"components": {"schemas": schemas}, "paths": paths or {}}


def _get(ref):
    return {"get": {"summary": "x", "operationId": "x", "responses": {
        "200": {"description": "ok", "content": {"application/json": {"schema": {"$ref": ref}}}}}}}


def test_gerador_traduz_cada_construcao():
    schemas = {
        "Tudo": {
            "title": "Tudo", "description": "meta ignorada", "type": "object",
            "required": ["s", "i", "n", "b", "z", "e", "a", "r", "u"],
            "properties": {
                "s": {"type": "string", "title": "S"},
                "i": {"type": "integer"},
                "n": {"type": "number"},
                "b": {"type": "boolean"},
                "z": {"type": "null"},
                "e": {"type": "string", "enum": ["a", "b"]},
                "a": {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
                "r": {"$ref": "#/components/schemas/Aa"},
                "u": {"anyOf": [{"$ref": "#/components/schemas/Aa"}, {"type": "null"}]},
                "opcional": {"type": "string", "default": "x"},
                "com-hifen": {"type": "boolean"},
            },
        },
        "Aa": {"type": "object", "properties": {"x": {"type": "string"}}},
    }
    esperado = CABECALHO + (
        "export type Aa = { x?: string };\n"
        "export type Tudo = { s: string; i: number; n: number; b: boolean; z: null; "
        'e: "a" | "b"; a: Array<string | number>; r: Aa; u: Aa | null; opcional?: string; '
        '"com-hifen"?: boolean };\n'
        'export type RotasGet = { "/a": Aa; "/b": Tudo };\n'
    )
    paths = {"/b": _get("#/components/schemas/Tudo"), "/a": _get("#/components/schemas/Aa")}
    assert gerar(_spec(schemas, paths)) == esperado


def _sse(content_schema=None):
    """Rota SSE na forma que o FastAPI emite; sem `content_schema`, o `data` sem tipo."""
    data = {"type": "string"}
    if content_schema is not None:
        data |= {"contentMediaType": "application/json", "contentSchema": content_schema}
    item = {"type": "object", "required": ["data"], "properties": {
        "data": data, "event": {"type": "string"}, "id": {"type": "string"},
        "retry": {"type": "integer", "minimum": 0}}}
    return {"get": {"summary": "x", "operationId": "x", "responses": {
        "200": {"description": "ok", "content": {"text/event-stream": {"itemSchema": item}}}}}}


def test_gerador_traduz_rota_sse():
    schemas = {"Aa": {"type": "object", "properties": {"x": {"type": "string"}}}}
    paths = {"/a": _get("#/components/schemas/Aa"), "/s": _sse({"$ref": "#/components/schemas/Aa"})}
    assert gerar(_spec(schemas, paths)) == CABECALHO + (
        "export type Aa = { x?: string };\n"
        'export type RotasGet = { "/a": Aa };\n'
        'export type RotasSSE = { "/s": Aa };\n'
    )


def _post(ref, required=True):
    op = dict(_get(ref)["get"], requestBody={
        "content": {"application/json": {"schema": {"$ref": ref}}}, "required": required})
    return {"post": op}


def test_gerador_traduz_rota_post():
    schemas = {"Aa": {"type": "object", "properties": {"x": {"type": "string"}}}}
    paths = {"/a": _get("#/components/schemas/Aa"), "/p": _post("#/components/schemas/Aa")}
    assert gerar(_spec(schemas, paths)) == CABECALHO + (
        "export type Aa = { x?: string };\n"
        'export type RotasGet = { "/a": Aa };\n'
        'export type RotasPost = { "/p": { corpo: Aa; resposta: Aa } };\n'
    )


@pytest.mark.parametrize("spec", [
    _spec({"X": {"type": "object", "properties": {}, "additionalProperties": True}}),
    _spec({"X": {"oneOf": [{"type": "string"}, {"type": "integer"}]}}),
    _spec({"X": {"allOf": [{"$ref": "#/components/schemas/Y"}]}}),
    _spec({"X": {"const": "a"}}),
    _spec({"X": {"type": "object", "properties": {"a": {"type": "string"}}}},
          {"/x": {"post": _get("#/components/schemas/X")["get"]}}),
    _spec({"X": {"type": "object", "properties": {"a": {"type": "string"}}}},
          {"/x": _post("#/components/schemas/X", required=False)}),
    _spec({}, {"/s": _sse({"type": "string"})}),
    _spec({}, {"/s": _sse()}),
], ids=["additionalProperties", "oneOf", "allOf", "const", "post", "post_corpo_opcional",
        "sse_sem_ref", "sse_sem_contentSchema"])
def test_gerador_recusa_o_que_nao_traduz(spec):
    with pytest.raises(ValueError, match="construção não suportada"):
        gerar(spec)


def _envelope_real(r, status):
    assert r.status_code == status, r.text
    return ErroV2.model_validate(r.json())


def test_erro_v2_aceita_401_404_405_reais():
    client = TestClient(dashboard.app)
    assert _envelope_real(client.get("/api/v2/me"), 401).error.code == "unauthenticated"
    assert _envelope_real(client.get("/api/v2/nao-existe"), 404).error.code == "not_found"
    assert _envelope_real(TestClient(dashboard.app).post("/api/v2/me", json={}), 405).error.code == "method_not_allowed"


def test_erro_v2_aceita_422_real_com_details(rota_temporaria):  # noqa: F811
    def login(corpo: Login):  # pragma: no cover - a validação recusa antes
        return {}

    rota_temporaria("/_teste_contrato_422", login, "POST")
    erro = ErroV2.model_validate(_corpo_do_422(TestClient(dashboard.app), "/api/v2/_teste_contrato_422").json())
    assert erro.error.details and erro.error.details[0].loc == ["body", "email"]


def test_erro_v2_recusa_o_detail_fora_do_envelope():
    with pytest.raises(ValidationError):
        ErroV2.model_validate({"detail": "x"})


@pytest.mark.parametrize("plano", sorted(FIXTURES["me"]))
def test_fixture_do_me_segue_o_modelo(plano):
    assert Me.model_validate(FIXTURES["me"][plano]).plan_tier == plano


@pytest.mark.parametrize("nome", sorted(FIXTURES["erros"]))
def test_fixture_de_erro_segue_o_envelope(nome):
    f = FIXTURES["erros"][nome]
    if f.get("fora_do_envelope"):
        with pytest.raises(ValidationError):
            ErroV2.model_validate(f["body"])
    else:
        assert ErroV2.model_validate(f["body"]).error.code
