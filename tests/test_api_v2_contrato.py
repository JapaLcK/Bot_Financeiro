"""Contrato da /api/v2 com o frontend: tipos TS gerados e envelope de erro.

- o `api-v2.gen.ts` commitado é a saída do gerador sobre o `openapi()` de hoje;
- o gerador traduz cada construção aceita e recusa as outras;
- o `ErroV2` descreve as respostas de erro REAIS do monólito, e as fixtures que os
  testes de navegador servem (`tests/frontend/api_v2_respostas.json`) seguem os modelos.
"""
import calendar
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import frontend.finance_bot_websocket_custom as dashboard
from api.v2 import app as app_v2
from api.v2.assinaturas import Assinaturas
from api.v2.categorias import Categorias
from api.v2.contas import Contas
from api.v2.erros import ErroV2
from api.v2.guia import DICAS, PASSOS, Guia
from api.v2.lancamentos import Lancamentos
from api.v2.me import Me
from api.v2.perfil import Perfil
from api.v2.resumo_mes import ResumoDoMes
from db.lancamentos import ler_cursor
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
            "required": ["s", "i", "b", "z", "e", "a", "r", "u"],
            "properties": {
                "s": {"type": "string", "title": "S"},
                "i": {"type": "integer"},
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
        "export type Tudo = { s: string; i: number; b: boolean; z: null; "
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


def _put(ref_corpo="#/components/schemas/Aa", **corpo):
    op = dict(_get("#/components/schemas/Bb")["get"])
    op["requestBody"] = {"content": {"application/json": {"schema": {"$ref": ref_corpo}}},
                         "required": True, **corpo}
    return op


_DINHEIRO = {"type": "string", "pattern": "^(?!^[-+.]*$)[+-]?0*\\d*\\.?\\d*$"}


def test_gerador_traduz_dinheiro_data_e_put():
    schemas = {"Aa": {"type": "object", "properties": {"x": {"type": "string"}}},
               "Bb": {"type": "object", "required": ["v", "em"], "properties": {
                   "v": _DINHEIRO, "em": {"type": "string", "format": "date-time"}}}}
    paths = {"/b": {"get": _get("#/components/schemas/Bb")["get"], "put": _put()}}
    assert gerar(_spec(schemas, paths)) == CABECALHO + (
        "export type Aa = { x?: string };\n"
        "export type Bb = { v: string; em: string };\n"
        'export type RotasGet = { "/b": Bb };\n'
        'export type RotasPut = { "/b": { corpo: Aa; resposta: Bb } };\n'
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


def _com_query(*params):
    op = dict(_get("#/components/schemas/Aa")["get"], parameters=list(params))
    return {"get": op}


_MES = {"name": "mes", "in": "query", "required": False,
        "schema": {"anyOf": [{"type": "string", "pattern": "^x$"}, {"type": "null"}], "title": "Mes"}}


def test_gerador_traduz_query_do_get():
    schemas = {"Aa": {"type": "object", "properties": {"x": {"type": "string"}}}}
    obrigatorio = {"name": "n", "in": "query", "required": True, "schema": {"type": "integer"}}
    teto = {"name": "q", "in": "query", "required": False,
            "schema": {"anyOf": [{"type": "string", "maxLength": 200, "minLength": 2}, {"type": "null"}]}}
    paths = {"/a": _get("#/components/schemas/Aa"), "/q": _com_query(_MES, obrigatorio, teto)}
    assert gerar(_spec(schemas, paths)) == CABECALHO + (
        "export type Aa = { x?: string };\n"
        'export type RotasGet = { "/a": Aa; "/q": Aa };\n'
        'export type QueryGet = { "/q": { mes?: string | null; n: number; q?: string | null } };\n'
    )


_AA = {"Aa": {"type": "object", "properties": {"x": {"type": "string"}}}}


@pytest.mark.parametrize("param", [
    dict(_MES, **{"in": "path"}), dict(_MES, **{"in": "header"}), dict(_MES, name="com-hifen"),
    dict(_MES, required="nao"), dict(_MES, description="x"), {k: v for k, v in _MES.items() if k != "required"},
    dict(_MES, schema={"type": "string", "format": "date"}), "mes",
], ids=["path", "header", "nome_invalido", "required_nao_bool", "chave_a_mais", "sem_required",
        "schema_fora_da_lista", "nao_e_objeto"])
def test_gerador_recusa_query_fora_da_lista(param):
    with pytest.raises(ValueError, match="construção não suportada"):
        gerar(_spec(_AA, {"/q": _com_query(param)}))


def test_gerador_recusa_parametro_em_escrita():
    op = dict(_post("#/components/schemas/Aa")["post"], parameters=[_MES])
    with pytest.raises(ValueError, match="construção não suportada"):
        gerar(_spec(_AA, {"/p": {"post": op}}))


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
    _spec({"X": {"type": "string", "format": "date"}}),
    _spec({"X": {"type": "integer", "pattern": "1"}}),
    _spec({"X": {"type": "integer", "maxLength": 1}}),
    _spec({"X": {"type": "string", "maxLength": 1, "format": "date"}}),
    _spec({"X": {"type": "number"}}),
    _spec({"Bb": {"type": "string"}}, {"/b": {"put": _put()}}),
    _spec({"Bb": {"type": "string"}}, {"/b": {"get": _get("#/components/schemas/Bb")["get"],
                                              "put": _put(required=False)}}),
    _spec({"Bb": {"type": "string"}}, {"/b": {"get": _get("#/components/schemas/Bb")["get"],
                                              "put": _get("#/components/schemas/Bb")["get"]}}),
], ids=["additionalProperties", "oneOf", "allOf", "const", "post", "post_corpo_opcional",
        "sse_sem_ref", "sse_sem_contentSchema", "format_date", "pattern_fora_de_string",
        "maxLength_fora_de_string", "maxLength_com_format", "number_float",
        "put_sem_get", "put_corpo_opcional", "put_sem_corpo"])
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


@pytest.mark.parametrize("nome", sorted(FIXTURES["assinaturas"]))
def test_fixture_de_assinaturas_segue_o_modelo(nome):
    from decimal import Decimal

    f = FIXTURES["assinaturas"][nome]
    a = Assinaturas.model_validate(f)
    assert a.model_dump(mode="json") == f  # dinheiro é texto: um 39.9 numérico volta "39.9"
    assert str(a.total_mensal) == str(sum((x.valor for x in a.servicos if x.status == "ativa"), Decimal(0)))
    assert a.total_anual == a.total_mensal * 12


@pytest.mark.parametrize("nome", sorted(FIXTURES["perfil"]))
def test_fixture_do_perfil_segue_o_modelo(nome):
    assert Perfil.model_validate(FIXTURES["perfil"][nome]).perfil == (None if nome == "nunca_escolheu" else nome)


@pytest.mark.parametrize("nome", sorted(FIXTURES["contas"]))
def test_fixture_de_contas_segue_o_modelo_e_fecha_a_conta(nome):
    from decimal import Decimal

    c = Contas.model_validate(FIXTURES["contas"][nome])
    assert c.total == c.carteira.saldo + sum((x.saldo for x in c.contas if x.no_total), Decimal(0))
    assert c.fora_do_total == sum(not x.no_total for x in c.contas)
    assert set(c.motivos) == set(c.carteira.motivos).union(*(x.motivos for x in c.contas))


@pytest.mark.parametrize("nome", sorted(FIXTURES["resumo_do_mes"]))
def test_fixture_do_resumo_do_mes_segue_o_modelo(nome):
    f = FIXTURES["resumo_do_mes"][nome]
    ResumoDoMes.model_validate(f)
    assert isinstance(f["entrou"], str) and isinstance(f["saiu"], str)  # dinheiro é texto
    ano, mes = map(int, f["mes"].split("-"))  # `ate` = último dia do mês, o corrente também
    assert f["ate"] == f"{f['mes']}-{calendar.monthrange(ano, mes)[1]:02d}"


@pytest.mark.parametrize("nome", sorted(FIXTURES["lancamentos"]))
def test_fixture_de_lancamentos_segue_o_modelo(nome):
    f = FIXTURES["lancamentos"][nome]
    Lancamentos.model_validate(f)
    for i in f["itens"]:
        assert isinstance(i["valor"], str)  # dinheiro é texto
        assert i["id"][0] == ("c" if i["cartao_id"] else "l") and (i["fatura"] is None) == (i["id"][0] == "l")
        assert i["origem"] != "registro_antigo" or i["pode"] == []  # antigo é só leitura (P2)
    if f["proximo"]:  # o cursor é o da última linha, e o servidor o aceita
        assert ler_cursor(f["proximo"])[2:] == (f["itens"][-1]["id"][0], int(f["itens"][-1]["id"][1:]))


@pytest.mark.parametrize("nome", sorted(FIXTURES["categorias"]))
def test_fixture_de_categorias_segue_o_modelo(nome):
    Categorias.model_validate(FIXTURES["categorias"][nome])


@pytest.mark.parametrize("nome", sorted(FIXTURES["guia"]))
def test_fixture_do_guia_segue_o_modelo_e_o_roteiro(nome):
    """Fonte única: o roteiro servido ao navegador é o `PASSOS` do servidor, campo a campo."""
    g = Guia.model_validate(FIXTURES["guia"][nome])
    assert g.estado == nome
    assert [{k: p[k] for k in PASSOS[0]} for p in FIXTURES["guia"][nome]["passos"]] == list(PASSOS)
    assert [{k: d[k] for k in DICAS[0]} for d in FIXTURES["guia"][nome]["dicas"]] == list(DICAS)


@pytest.mark.parametrize("nome", sorted(FIXTURES["erros"]))
def test_fixture_de_erro_segue_o_envelope(nome):
    f = FIXTURES["erros"][nome]
    if f.get("fora_do_envelope"):
        with pytest.raises(ValidationError):
            ErroV2.model_validate(f["body"])
    else:
        assert ErroV2.model_validate(f["body"]).error.code


def test_gerador_traduz_literal_de_um_valor_e_get_com_post():
    schemas = {"Aa": {"type": "object", "required": ["k"], "properties": {"k": {"type": "string", "const": "x"}}}}
    paths = {"/a": {"get": _get("#/components/schemas/Aa")["get"], **_post("#/components/schemas/Aa")}}
    assert gerar(_spec(schemas, paths)) == CABECALHO + (
        'export type Aa = { k: "x" };\n'
        'export type RotasGet = { "/a": Aa };\n'
        'export type RotasPost = { "/a": { corpo: Aa; resposta: Aa } };\n'
    )
