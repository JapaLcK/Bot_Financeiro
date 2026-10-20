"""Contrato da /api/v2 com o frontend: tipos TS gerados e envelope de erro.

- o `api-v2.gen.ts` commitado é a saída do gerador sobre o `openapi()` de hoje;
- o gerador traduz cada construção aceita e recusa as outras;
- o `ErroV2` descreve as respostas de erro REAIS do monólito, e as fixtures que os
  testes de navegador servem (`tests/frontend/api_v2_respostas.json`) seguem os modelos.
"""
import calendar
import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import frontend.finance_bot_websocket_custom as dashboard
from api.v2 import app as app_v2
from api.v2.assinaturas import Assinaturas
from api.v2.bancos import Bancos
from api.v2.caixinhas import Caixinhas
from api.v2.categorias import Categorias
from api.v2.contas import Contas
from api.v2.contas_a_pagar import ContasAPagar
from api.v2.erros import ErroV2
from api.v2.gastos import Gastos, GastosDaCategoria
from api.v2.guia import DICAS, PASSOS, Guia
from api.v2.investido import Investido
from api.v2.lancamentos import Lancamentos
from api.v2.me import Me
from api.v2.metas import Metas
from api.v2.orcamento_domestico import OrcamentoDomestico
from api.v2.orcamentos_categoria import OrcamentosCategoria
from api.v2.progresso_milhao import ProgressoMilhao
from api.v2.perfil import Perfil
from api.v2.previsao import Previsao
from api.v2.resumo_mes import ResumoDoMes
from db import investido
from db.gastos import MAIORES_POR_DIA
from db.lancamentos import ler_cursor
from scripts.gerar_tipos_api_v2 import CABECALHO, SAIDA, gerar
from test_api_v2_erros import Login, _corpo_do_422, rota_temporaria  # noqa: F401 (fixture)

FIXTURES = json.loads((Path(__file__).parent / "frontend" / "api_v2_respostas.json").read_text(encoding="utf-8"))


def test_fixtures_nao_tem_chave_repetida():
    """`json.loads` guarda só a ÚLTIMA chave repetida: duas `"bancos"` no arquivo escondiam metade das fixtures (e dos testes)."""
    def sem_repeticao(pares):
        assert len({k for k, _ in pares}) == len(pares), [k for k, _ in pares]
        return dict(pares)

    json.loads((Path(__file__).parent / "frontend" / "api_v2_respostas.json").read_text(encoding="utf-8"), object_pairs_hook=sem_repeticao)


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


@pytest.mark.parametrize("nome", sorted(FIXTURES["investido"]))
def test_fixture_de_investido_segue_o_modelo(nome):
    """Dinheiro é texto com 2 casas (ou null); o rótulo é o de `investido.TIPOS` (§0.7:
    a fixture repete o rótulo, este teste os compara); Σ partes == total (null = 0)."""
    import re
    from decimal import Decimal

    f = FIXTURES["investido"][nome]
    Investido.model_validate(f)
    partes = [*f["por_tipo"], *f["por_banco"]]
    assert all(v is None or re.fullmatch(r"-?\d+\.\d{2}", v) for v in [f["total"], *(p["valor"] for p in partes)])
    assert all(p["rotulo"] == investido.TIPOS[p["tipo"]] for p in f["por_tipo"])
    for lado in ("por_tipo", "por_banco"):
        assert sum((Decimal(p["valor"] or 0) for p in f[lado]), Decimal(0)) == Decimal(f["total"] or 0)
        assert all(set(p["motivos"]) <= set(f["motivos"]) for p in f[lado])


@pytest.mark.parametrize("nome", sorted(FIXTURES["bancos"]))
def test_fixture_de_bancos_segue_o_modelo(nome):
    """`total` do banco = Σ das partes não nulas (ou null se as duas são); o topo = Σ dos totais dos bancos listados
    (ou null) e seus motivos = a união dos motivos dos bancos (db/bancos.py). A `padrao` é o JSON do app (§0.7)."""
    f = FIXTURES["bancos"][nome]
    b = Bancos.model_validate(f)
    soma = lambda xs: sum(xs, Decimal(0)) if xs else None  # noqa: E731
    for x in b.bancos:
        assert x.total == soma([v for v in (x.saldo_conta, x.investido) if v is not None])
    assert b.total == soma([x.total for x in b.bancos if x.total is not None])
    assert set(b.motivos) == set().union(*(x.motivos for x in b.bancos))
    assert len({x.conexao_id for x in b.bancos}) == len(b.bancos)


def test_fixture_padrao_de_bancos_e_a_do_app():
    assert FIXTURES["bancos"]["padrao"] == json.loads((Path(__file__).parent / "fixtures" / "app_bancos.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("nome", sorted(FIXTURES["metas"]))
def test_fixture_de_metas_segue_o_modelo(nome):
    """Dinheiro é texto com 2 casas (ou null); `falta`/`percentual` seguem de `atual` e `alvo`;
    `atual` null leva tudo junto (nunca R$ 0); no máximo uma reserva, com `alvo` = meses × custo."""
    import re
    from decimal import Decimal

    f = FIXTURES["metas"][nome]
    Metas.model_validate(f)
    din = r"-?\d+\.\d{2}"
    assert all(v is None or re.fullmatch(din, v) for v in [f["custo_mensal"]["valor"]])
    assert sum(m["tipo"] == "reserva" for m in f["metas"]) <= 1
    for m in f["metas"]:
        assert all(m[k] is None or re.fullmatch(din, m[k]) for k in ("atual", "alvo", "falta", "por_mes"))
        assert m["ritmo"]["valor"] is None or re.fullmatch(din, m["ritmo"]["valor"])
        if m["atual"] is None or m["alvo"] is None:
            assert m["falta"] is None and m["percentual"] is None and m["por_mes"] is None
        else:
            assert Decimal(m["falta"]) == max(Decimal(m["alvo"]) - Decimal(m["atual"]), 0)
            assert m["percentual"] == min(int(Decimal(m["atual"]) * 100 // Decimal(m["alvo"])), 100)
        if m["tipo"] == "reserva" and m["alvo"] and f["custo_mensal"]["valor"]:
            assert Decimal(m["alvo"]) == Decimal(f["custo_mensal"]["valor"]) * m["meses"]
        assert m["prazo_passou"] <= bool(m["data_alvo"]) and not (m["prazo_passou"] and m["por_mes"])


@pytest.mark.parametrize("nome", sorted(FIXTURES["orcamento_domestico"]))
def test_fixture_do_orcamento_domestico_segue_o_modelo(nome):
    f = FIXTURES["orcamento_domestico"][nome]
    assert OrcamentoDomestico.model_validate(f).model_dump(mode="json") == f  # dinheiro e % são texto, sem número solto
    assert [p["chave"] for p in f["potes"]] == ["custos_fixos", "conforto", "metas", "prazeres", "liberdade_financeira", "conhecimento"]


@pytest.mark.parametrize("nome", sorted(FIXTURES["progresso_milhao"]))
def test_fixture_do_progresso_milhao_segue_o_modelo(nome):
    f = FIXTURES["progresso_milhao"][nome]
    assert ProgressoMilhao.model_validate(f).model_dump(mode="json") == f  # patrimônio é texto de 2 casas


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


def _dias_fecham(dias, chaves):
    """O card do dia não soma dinheiro no cliente: `categorias` fecha com `total`, e `quantidade` com `maiores`."""
    for d in dias:
        cats = d["categorias"]
        assert sum(Decimal(c["valor"]) for c in cats) == Decimal(d["total"])
        assert {c["chave"] for c in cats} <= chaves and len({c["chave"] for c in cats}) == len(cats)
        assert cats == sorted(cats, key=lambda c: (-Decimal(c["valor"]), c["chave"]))
        assert len(d["maiores"]) == min(MAIORES_POR_DIA, d["quantidade"]) and d["quantidade"] >= len(cats)


@pytest.mark.parametrize("nome", sorted(FIXTURES["gastos"]))
def test_fixture_de_gastos_segue_o_modelo_e_fecha_a_conta(nome):
    f = FIXTURES["gastos"][nome]
    Gastos.model_validate(f)
    if not f["disponivel"]:  # fora do histórico: nunca "0"
        assert f["total"] is None and not f["categorias"] and not f["dias"]
        return
    total = Decimal(f["total"])
    assert sum(Decimal(c["valor"]) for c in f["categorias"]) == total
    assert sum(Decimal(d["total"]) for d in f["dias"]) + Decimal(f["fora_da_grade"]["valor"]) == total
    chaves = {c["chave"] for c in f["categorias"]}
    assert all(m["categoria"] in chaves for d in f["dias"] for m in d["maiores"])
    _dias_fecham(f["dias"], chaves)
    assert sum(d["quantidade"] for d in f["dias"]) + f["fora_da_grade"]["quantidade"] == sum(
        c["quantidade"] for c in f["categorias"])
    if f["anterior_total_ate_corte"] is None:  # mês anterior indisponível: nenhuma comparação sai
        assert all(c["anterior_ate_corte"] is None for c in f["categorias"])
    else:  # o total do anterior inclui categoria que não está na lista: nunca menos que Σ
        assert Decimal(f["anterior_total_ate_corte"]) >= sum(Decimal(c["anterior_ate_corte"]) for c in f["categorias"])


@pytest.mark.parametrize("nome", sorted(FIXTURES["gastos_categoria"]))
def test_fixture_de_gastos_categoria_segue_o_modelo_e_fecha_a_conta(nome):
    f = FIXTURES["gastos_categoria"][nome]
    GastosDaCategoria.model_validate(f)
    assert len(f["serie"]) == 3 and f["serie"][-1]["mes"] == f["mes"]
    if not f["disponivel"]:  # fora do histórico: nunca "0"
        assert f["total"] is f["compras"] is f["valor_medio"] is None
        assert not f["ultimas"] and not f["dias"] and not f["estabelecimentos"] and all(s["valor"] is None for s in f["serie"])
        return
    total = Decimal(f["total"])
    assert sum(Decimal(d["total"]) for d in f["dias"]) + Decimal(f["fora_da_grade"]["valor"]) == total
    assert f["serie"][-1]["valor"] is None or Decimal(f["serie"][-1]["valor"]) == total
    assert sum(Decimal(e["valor"]) for e in f["estabelecimentos"]) <= total
    assert sum(e["quantidade"] for e in f["estabelecimentos"]) <= f["compras"] and len(f["ultimas"]) <= min(6, f["compras"])
    if f["compras"] == 0:  # sem compras não há média: null, nunca 0
        assert f["valor_medio"] is None and total == 0
    else:
        assert Decimal(f["valor_medio"]) == (total / f["compras"]).quantize(Decimal("0.01"), ROUND_HALF_UP)
    assert all(m["categoria"] == f["categoria"]["chave"] for d in f["dias"] for m in d["maiores"])
    _dias_fecham(f["dias"], {f["categoria"]["chave"]})
    assert all([c["chave"] for c in d["categorias"]] == [f["categoria"]["chave"]] for d in f["dias"])  # uma só


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


@pytest.mark.parametrize("nome", sorted(FIXTURES["previsao"]))
def test_fixture_de_previsao_segue_modelo_e_precisao(nome):
    f = FIXTURES["previsao"][nome]
    p = Previsao.model_validate(f)
    assert p.model_dump(mode="json") == f
    assert p.cabe_nas_premissas is False
    assert p.cobertura.inclui_estimativa_variavel is (p.estimativa_variavel is not None)
    assert len(p.marcos) <= 3
    if p.trajetoria is not None:
        assert len(p.trajetoria) == p.dias <= 90
        for m in p.marcos:
            assert m.saldo == p.trajetoria[m.dias - 1].saldo
    else:
        assert p.capacidades == ["marcos"]
        assert p.periodo is p.ancora is p.pior_dia is p.compromissos is None


def test_fixture_do_simulador_segue_modelo_e_dinheiro_em_texto():
    import re
    from datetime import date, timedelta
    from api.v2.simulador import CODIGO_GENERICO, CODIGOS_ERRO, PREMISSAS_V2, Simulador
    f = FIXTURES["simulador"]["compra"]
    s = Simulador.model_validate(f)
    assert s.model_dump(mode="json") == f
    # a regra de tamanho da API: hoje..hoje+dias, uma data por dia, em `atual` e em cada cenário
    esperado = [(date.fromisoformat(f["hoje"]) + timedelta(days=i)).isoformat() for i in range(f["dias"] + 1)]
    for t in (s.atual.trajetoria, *(c.resumo.trajetoria for c in s.cenarios)):
        assert [p.data for p in t] == esperado
    assert f["premissas"][-len(PREMISSAS_V2):] == PREMISSAS_V2 and len(f["premissas"]) == 4 + len(PREMISSAS_V2)  # as 4 da Previsão + as da pessoa
    assert not [t for t in f["premissas"] if re.search(r"\b[a-z]+_[a-z_]+\b", t)]
    # os códigos de 422 que a tela cobre (`dashboard_v2_simulador_compra.test.mjs`) são os do servidor
    assert FIXTURES["simulador"]["codigos_422"] == sorted({c for c, _ in CODIGOS_ERRO.values()} | {CODIGO_GENERICO})
    assert s.cabe_nas_premissas is s.cobertura.inclui_estimativa_variavel is False and s.orientacao == "abster"
    ts = (Path(__file__).parent.parent / "webapp/src/dashboard/lib/api-v2.gen.ts").read_text(encoding="utf-8")
    bloco = "\n".join(l for l in ts.splitlines() if l.startswith(("export type Simulador", "export type ResumoCenario", "export type ContratoSimulador", "export type PontoSimulador")))
    assert bloco and "saldo: number" not in bloco and "total_pago: number" not in bloco


def test_fixture_do_simulador_de_habitos_segue_modelo_e_dinheiro_em_texto():
    from datetime import date, timedelta
    from api.v2.simulador import PREMISSAS_HABITOS_V2, SimuladorHabitos
    f = FIXTURES["simulador"]["habitos"]
    s = SimuladorHabitos.model_validate(f)
    assert s.model_dump(mode="json") == f
    esperado = [(date.fromisoformat(f["hoje"]) + timedelta(days=i)).isoformat() for i in range(f["dias"] + 1)]
    for t in (s.atual.trajetoria, s.com_corte.trajetoria):
        assert [p.data for p in t] == esperado
    assert len(s.cortes) == 2 and s.economia_mensal == sum(c.economia_mensal for c in s.cortes)
    assert s.com_corte.delta_vs_atual == 3 * s.economia_mensal and s.atual.pior_dia.saldo < 0
    assert f["premissas"][-len(PREMISSAS_HABITOS_V2):] == PREMISSAS_HABITOS_V2
    from api.v2.simulador import CODIGOS_HABITOS
    assert FIXTURES["simulador"]["codigos_422_habitos"] == sorted(CODIGOS_HABITOS)  # os que `SimuladorHabitos.tsx` cobre


@pytest.mark.parametrize("nome", sorted(FIXTURES["orcamentos_categoria"]))
def test_fixture_dos_orcamentos_por_categoria_segue_o_modelo(nome):
    f = FIXTURES["orcamentos_categoria"][nome]
    assert OrcamentosCategoria.model_validate(f).model_dump(mode="json") == f  # dinheiro e % são texto, sem número solto


@pytest.mark.parametrize("nome", sorted(FIXTURES["contas_a_pagar"]))
def test_fixture_de_contas_a_pagar_segue_o_modelo(nome):
    """Dinheiro é texto; o total soma os valores das pendentes (a regra da consulta do Carteiro); vencida = dias < 0."""
    f = FIXTURES["contas_a_pagar"][nome]
    c = ContasAPagar.model_validate(f)
    assert c.model_dump(mode="json") == f
    assert c.pendentes == len(c.contas) and c.vencidas == sum(x.dias < 0 for x in c.contas) == sum(x.vencida for x in c.contas)
    assert c.total_estimado == sum((x.valor for x in c.contas), Decimal(0)) and c.tem_variavel == any(x.variavel for x in c.contas)


@pytest.mark.parametrize("nome", sorted(FIXTURES["caixinhas"]))
def test_fixture_de_caixinhas_segue_o_modelo(nome):
    """Dinheiro e % são texto; o total soma os saldos; o progresso é saldo/meta limitado a 100; sem meta, sem progresso."""
    f = FIXTURES["caixinhas"][nome]
    c = Caixinhas.model_validate(f)
    assert c.model_dump(mode="json") == f
    assert c.total == sum((x.saldo for x in c.caixinhas), Decimal(0))
    for x in c.caixinhas:
        assert (x.meta is None) == (x.progresso_pct is None) == (x.atingida is None)
        if x.meta:
            assert x.progresso_pct == min(x.saldo / x.meta * 100, 100).quantize(Decimal("0.01")) and x.atingida == (x.saldo >= x.meta)
