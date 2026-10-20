"""`POST /api/v2/simulador` e `/api/app/simulador` pelo monólito real: sessão, CSRF e DB pytest_UUID."""
import ast
import inspect
import re
from datetime import date, datetime, timedelta
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

import db
import db_support
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf, sessao_de
from api.v2 import simulador as rota
from api.v2.simulador import CODIGO_GENERICO, CODIGOS_ERRO, PREMISSAS_V2, Simulador, _detalhe
from conftest import promote_to_pro, usuario_pagante
from core.services import decision_simulator as ds, previsao_recorrencias, previsao_v2
from core.services.decision_simulator import Simulacao, simulate
from tests.test_api_v2_previsao import recorrente
from tests.test_cashflow_snapshot import q
from utils_date import now_tz

ROTA = "/api/v2/simulador"
CORPO = {"reserva_minima": "50", "cenarios": [
    {"nome": "À vista", "preco": "100"},
    {"nome": "Parcelado", "preco": "1200.50", "entrada": "200", "parcelas": 12, "juros_mensal_pct": "1.99",
     "custos_unicos": "30", "despesa_mensal_nova": "15.50"}]}
CONTAGENS = {"dias", "parcelas", "quantidade", "contas_bancarias", "dias_abaixo_da_reserva"}


@pytest.fixture
def uid(monkeypatch):
    u = usuario_pagante("pro_max")
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(u))
    monkeypatch.setenv("OF_CONSOLIDATED_BALANCE_ENABLED", "0")
    db.set_balance(u, D("1000"))
    # 5 dias à frente no relógio da rota (`previsao_v2.now_tz`); `recorrente` usa o relógio real, que difere dele
    # quando o dia é congelado por fora (a rota e a fixture precisam concordar sobre "hoje").
    recorrente(u, "Aluguel", 300, offset=5 + (previsao_v2.now_tz().date() - now_tz().date()).days)
    return u


@pytest.fixture
def dia_fixo(monkeypatch):
    """Hoje = 2026-10-06 (hoje+90 = 2027-01-04): as bordas do dia 90 não dependem do dia em que o teste roda.
    Com o dia solto, `_add_months(c, 1) == hoje+90` não tem solução quando hoje+90 é dia 29..31 sem par no mês anterior."""
    fixo = date(2026, 10, 6)
    agora = datetime.combine(fixo, now_tz().time()).replace(tzinfo=now_tz().tzinfo)
    monkeypatch.setattr(previsao_v2, "now_tz", lambda: agora)

    class Dia(date):
        @classmethod
        def today(cls):
            return fixo
    monkeypatch.setattr(ds, "date", Dia)
    return fixo


def cliente(quem, com_csrf=True):
    c = TestClient(dashboard.app)
    if quem is not None:
        c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(quem)["dashboard"])
    return c, (csrf(c) if com_csrf else {})


def post(quem, corpo=CORPO, rota_=ROTA, **kw):
    c, h = cliente(quem, **kw)
    return c.post(rota_, json=corpo, headers=h)


def ok(quem, corpo=CORPO, **kw):
    r = post(quem, corpo, **kw)
    assert r.status_code == 200, r.text
    assert Simulador.model_validate(r.json()).model_dump(mode="json") == r.json()
    return r.json()


def sem_numero_monetario(v, chave=""):
    if isinstance(v, dict):
        for k, x in v.items():
            sem_numero_monetario(x, k)
    elif isinstance(v, list):
        for x in v:
            sem_numero_monetario(x, chave)
    elif isinstance(v, (int, float)) and not isinstance(v, bool):
        assert chave in CONTAGENS, f"número em {chave}: {v}"


# ── 1. gate antes da leitura ────────────────────────────────────────────────

@pytest.fixture
def espiao(monkeypatch):
    monkeypatch.setattr(rota, "ler_snapshot", lambda *a: pytest.fail("leitura financeira"))


def test_sem_sessao_401(uid, espiao):
    r = post(None)
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"


@pytest.mark.parametrize("plano", ["essencial", "pro"])  # 'pro' gravado = tier Plus
def test_abaixo_do_pro_403_antes_da_leitura_mesmo_com_corpo_invalido(uid, espiao, plano):
    promote_to_pro(uid, plano)
    for corpo in (CORPO, {"cenarios": []}, {"user_id": 1}):
        r = post(uid, corpo)
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "pro_required"


def test_free_e_barrado_pela_sessao_antes_da_leitura(uid, espiao):
    q("update auth_accounts set plan='free' where user_id=%s", (uid,))
    db.mark_plan_selected(uid)
    db_support.invalidate_auth_user_cache(uid)
    r = post(uid)
    assert r.status_code == 402 and r.json()["error"]["code"] == "subscription_required"


# ── 2. dinheiro em texto ────────────────────────────────────────────────────

def test_resposta_sem_numero_monetario_e_calculo_decimal(uid):
    c = ok(uid)
    sem_numero_monetario(c)
    assert c["dias"] == 90 and c["orientacao"] == "abster" and c["cabe_nas_premissas"] is False
    assert len(c["atual"]["trajetoria"]) == 91 and c["atual"]["trajetoria"][0]["data"] == c["hoje"]
    assert all(set(m) == {"codigo", "direcao_do_erro"} for m in c["motivos"])
    assert "aviso_saldo" not in c


@pytest.mark.parametrize("campo,valor", [("preco", 100), ("preco", "0.001"), ("preco", "1e3"), ("preco", "NaN"),
                                          ("preco", "-1"), ("entrada", 5), ("custos_unicos", "1,5"),
                                          ("juros_mensal_pct", 1.5), ("juros_mensal_pct", "1e1")])
def test_valor_fora_do_padrao_texto_422(uid, campo, valor):
    cen = {"nome": "x", "preco": "100", "parcelas": 3, "entrada": "10"}
    cen[campo] = valor
    r = post(uid, {"cenarios": [cen]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


def test_reserva_em_numero_json_422(uid):
    assert post(uid, {"reserva_minima": 50, "cenarios": CORPO["cenarios"][:1]}).status_code == 422


def test_fronteira_do_teto_de_dinheiro(uid):
    c = ok(uid, {"cenarios": [{"nome": "Máximo", "preco": "999999999.99"}]})
    assert D(c["cenarios"][0]["resumo"]["delta_vs_atual"]) == D("-999999999.99")


def test_fim_da_janela_e_inclusivo_compra_e_parcela_no_dia_90(uid, dia_fixo):
    """Controle do `until = hoje + 90`: o evento exatamente no dia 90 entra no último ponto."""
    hoje = date.fromisoformat(ok(uid)["hoje"])
    assert hoje == dia_fixo
    d90 = hoje + timedelta(days=90)  # 2027-01-04
    compra_parcela = date(2026, 12, 4)  # +1 mês = d90
    c = ok(uid, {"cenarios": [
        {"nome": "Compra no 90", "preco": "100", "data_compra": d90.isoformat()},
        {"nome": "Parcela no 90", "preco": "200", "parcelas": 2, "data_compra": compra_parcela.isoformat()}]})
    a, b = c["cenarios"]
    ultimo = lambda x: x["resumo"]["trajetoria"][-1]
    assert c["atual"]["trajetoria"][-1]["data"] == ultimo(a)["data"] == d90.isoformat()
    assert D(a["resumo"]["delta_vs_atual"]) == D("-100") and not a["resumo"]["compra_fora_da_janela"]
    assert D(ultimo(a)["saldo"]) == D(c["atual"]["trajetoria"][-1]["saldo"]) - 100
    assert D(b["resumo"]["delta_vs_atual"]) == D("-100")  # só a 1ª parcela (dia 90); a 2ª cai depois
    assert D(ultimo(b)["saldo"]) == D(c["atual"]["trajetoria"][-1]["saldo"]) - 100
    assert b["contrato"]["parcelas_fora_do_horizonte"]["quantidade"] == 1


def test_dia_91_fica_fora_da_janela_e_nao_mexe_no_saldo(uid):
    hoje = date.fromisoformat(ok(uid)["hoje"])
    c = ok(uid, {"cenarios": [{"nome": "Dia 91", "preco": "100", "data_compra": (hoje + timedelta(days=91)).isoformat()},
                              {"nome": "Dia 90", "preco": "100", "data_compra": (hoje + timedelta(days=90)).isoformat()}]})
    fora, dentro = c["cenarios"]
    assert fora["resumo"]["compra_fora_da_janela"] is True and not dentro["resumo"]["compra_fora_da_janela"]
    assert D(fora["resumo"]["delta_vs_atual"]) == 0 and fora["resumo"]["saldo_final"] == c["atual"]["saldo_final"]
    assert D(dentro["resumo"]["delta_vs_atual"]) == D("-100")


def test_despesa_mensal_no_dia_90_entra_e_no_dia_91_nao(uid, dia_fixo):
    """Borda do `while` das mensais: a 1ª ocorrência em hoje+90 conta; em hoje+91 não."""
    assert date.fromisoformat(ok(uid)["hoje"]) == dia_fixo
    no90, no91 = date(2026, 12, 4), date(2026, 12, 5)  # +1 mês = 2027-01-04 (dia 90) e 2027-01-05 (dia 91)
    c = ok(uid, {"cenarios": [
        {"nome": "Mensal no 90", "preco": "100", "despesa_mensal_nova": "10", "data_compra": no90.isoformat()},
        {"nome": "Mensal no 91", "preco": "100", "despesa_mensal_nova": "10", "data_compra": no91.isoformat()}]})
    a, b = c["cenarios"]
    assert D(a["resumo"]["delta_vs_atual"]) == D("-110")  # compra + a mensal que cai exatamente no dia 90
    assert D(b["resumo"]["delta_vs_atual"]) == D("-100")  # a mensal cai no dia 91: fora


# ── 3 e 4. paridade com a Previsão e com o legado ──────────────────────────

def test_atual_bate_com_a_previsao_e_a_vista_hoje_custa_o_preco(uid):
    c = ok(uid)
    cli, _ = cliente(uid)
    p = cli.get("/api/v2/previsao", params={"dias": 90}).json()
    assert c["atual"]["saldo_final"] == p["trajetoria"][-1]["saldo"]
    # a Previsão parte de hoje (dia 0 fica na âncora); o simulador acrescenta o ponto de hoje
    assert [(x["data"], x["saldo"]) for x in c["atual"]["trajetoria"][1:]] == [(x["data"], x["saldo"]) for x in p["trajetoria"]]
    assert D(c["cenarios"][0]["resumo"]["delta_vs_atual"]) == D("-100")
    assert D(c["atual"]["saldo_final"]) == D("700")


def test_v2_igual_ao_legado_no_mesmo_fixture(uid):
    c = ok(uid)
    leg = simulate(uid, Simulacao.model_validate({"reserva_minima": 50.0, "cenarios": [
        {"nome": "À vista", "preco": 100.0},
        {"nome": "Parcelado", "preco": 1200.5, "entrada": 200.0, "parcelas": 12, "juros_mensal_pct": 1.99,
         "custos_unicos": 30.0, "despesa_mensal_nova": 15.5}]}))
    assert float(c["atual"]["saldo_final"]) == leg["atual"]["saldo_final_90"]
    assert float(c["reserva_minima"]) == leg["reserva_minima"]
    for novo, velho in zip(c["cenarios"], leg["cenarios"]):
        assert float(novo["resumo"]["saldo_final"]) == velho["resumo"]["saldo_final_90"]
        assert float(novo["resumo"]["delta_vs_atual"]) == velho["resumo"]["delta_vs_atual"]
        for k, v in velho["contrato"].items():
            if isinstance(v, float):
                assert float(novo["contrato"][k]) == v, k
    assert "trajetoria" not in str(leg)  # a saída legada não ganha a curva


# ── 5. validação → 422, nunca 500 ───────────────────────────────────────────

def _cen(**kw):
    return {"nome": "x", "preco": "100", **kw}


@pytest.mark.parametrize("corpo", [
    {"cenarios": [_cen(preco="100", entrada="150", parcelas=3)]},
    {"cenarios": [_cen(nome="A"), _cen(nome=" a ")]},
    {"cenarios": [_cen(data_compra="2020-01-01")]},
    {"cenarios": [_cen(data_compra="2026-13-45")]},
    {"cenarios": [_cen(nome=str(i)) for i in range(4)]},
    {"cenarios": [_cen(juros_mensal_pct="2")]},
    {"cenarios": [_cen(parcelas=421)]},
    {"cenarios": [_cen(juros_mensal_pct="20.5", parcelas=3)]},
    {"cenarios": [_cen(entrada="50")]},
])
def test_entrada_invalida_422_sem_eco_do_corpo(uid, corpo):
    r = post(uid, corpo)
    assert r.status_code == 422, r.text
    e = r.json()["error"]
    assert e["code"] == "validation_error" and e["details"]
    assert all(set(d) == {"loc", "msg", "type"} for d in e["details"])
    assert "input" not in r.text


# Um caso por `value_error` do `Simulacao` alcançável pela rota: código estável e o campo certo no `loc`.
# (`dinheiro_casas` é barrado antes, pelo `_VALOR` do corpo; vai no teste de `_detalhe` abaixo.)
_NOME = ["body", "cenarios", 0]
CASOS_CODIGO = [
    ("entrada_maior_que_preco", [_cen(entrada="150", parcelas=3)], [*_NOME, "entrada"]),
    ("entrada_parcial_sem_parcelas", [_cen(entrada="50")], [*_NOME, "parcelas"]),
    ("entrada_igual_ao_preco", [_cen(entrada="100", parcelas=3)], [*_NOME, "entrada"]),
    ("juros_sem_parcelas", [_cen(juros_mensal_pct="2")], [*_NOME, "parcelas"]),
    ("data_no_passado", [_cen(data_compra="2020-01-01")], [*_NOME, "data_compra"]),
    ("data_fora_do_calendario", [_cen(parcelas=2, data_compra="9999-12-31")], [*_NOME, "data_compra"]),
    ("financiado_pequeno_demais", [_cen(preco="0.05", parcelas=12)], [*_NOME, "parcelas"]),
    ("nomes_repetidos", [_cen(nome="A"), _cen(nome=" a ")], ["body"]),
]


@pytest.mark.parametrize("codigo,cenarios,loc", CASOS_CODIGO, ids=[c[0] for c in CASOS_CODIGO])
def test_value_error_vira_codigo_estavel_e_campo(uid, codigo, cenarios, loc):
    d = post(uid, {"cenarios": cenarios}).json()["error"]["details"]
    assert d == [{"loc": loc, "msg": "Valor inválido.", "type": codigo}]


def test_todo_value_error_do_modelo_tem_codigo_mapeado():
    """Enumera os `raise ValueError(...)` das classes de validação: mensagem nova sem código quebra aqui."""
    arvore = ast.parse(inspect.getsource(ds))
    msgs = set()
    for cls in (n for n in arvore.body if isinstance(n, ast.ClassDef) and n.name in {"_Corpo", "Cenario", "Simulacao"}):
        for r in (n for n in ast.walk(cls) if isinstance(n, ast.Raise)):
            assert isinstance(r.exc, ast.Call) and r.exc.func.id == "ValueError" and len(r.exc.args) == 1, ast.dump(r)
            a = r.exc.args[0]
            msgs.add(getattr(ds, a.id) if isinstance(a, ast.Name) else a.value)  # literal solto também precisa de código
    assert msgs and msgs == set(CODIGOS_ERRO), msgs ^ set(CODIGOS_ERRO)
    assert len({c for c, _ in CODIGOS_ERRO.values()}) == len(CODIGOS_ERRO)
    assert CODIGO_GENERICO not in {c for c, _ in CODIGOS_ERRO.values()}
    assert {c for c, *_ in CASOS_CODIGO} | {"dinheiro_casas"} == {c for c, _ in CODIGOS_ERRO.values()}


def test_detalhe_casas_e_value_error_desconhecido_sem_msg_original():
    with pytest.raises(Exception) as exc:
        Simulacao.model_validate({"cenarios": [{"nome": "x", "preco": 0.001}]})
    e = exc.value.errors(include_input=False)[0]
    assert _detalhe(e) == {"loc": ["body", "cenarios", 0, "preco"], "msg": "Valor inválido.", "type": "dinheiro_casas"}
    desconhecido = {"loc": ("cenarios", 0), "type": "value_error", "msg": "Value error, segredo", "ctx": {"error": ValueError("segredo")}}
    r = _detalhe(desconhecido)
    assert r == {"loc": ["body", "cenarios", 0], "msg": "Valor inválido.", "type": CODIGO_GENERICO} and "segredo" not in str(r)


def test_premissas_sao_as_da_pessoa_sem_identificador_de_codigo(uid):
    p = ok(uid)["premissas"]
    assert p[-len(PREMISSAS_V2):] == PREMISSAS_V2
    assert not [t for t in p if re.search(r"\b[a-z]+_[a-z_]+\b", t)], "identificador de campo na premissa"


@pytest.mark.parametrize("com_banco", [False, True])
def test_premissas_da_previsao_chegam_ao_simulador(uid, com_banco):
    """O simulador usa a mesma snapshot da Previsão: nenhuma premissa dela some (nem a do banco, dinâmica)."""
    if com_banco:
        from tests.test_previsao_recorrencias_of import netflix
        netflix(uid)
    cli, _ = cliente(uid)
    prev = cli.get("/api/v2/previsao", params={"dias": 90}).json()["premissas"]
    sim = ok(uid)["premissas"]
    assert len(prev) >= 4 + com_banco
    assert (previsao_recorrencias.PREMISSA in prev) is com_banco
    assert set(prev) <= set(sim) and sim[-len(PREMISSAS_V2):] == PREMISSAS_V2


# Cada regra que a tool (`ds.PREMISSAS`) e a pessoa (`PREMISSAS_V2`) devem dizer; §0.7: dois textos, um teste que os liga.
# Os números são literais de propósito: mudar `DIAS` ou as casas só num lado (ou nos dois) deixa vermelho.
REGRAS = {
    "janela": ["saldo de 90 dias", "até o dia 90 inclusive", "(91 datas)", "depois do dia 90"],
    "casas": ["duas casas decimais"],
    "price": ["tabela price", "última parcela"],
    "receitas": ["só entram quando mensais ou anuais"],
    "estimativa": ["estimativa dos gastos do dia a dia"],
    "total_pago_sem_despesa_mensal": ["não inclui a despesa mensal nova"],
}


@pytest.mark.parametrize("regra", REGRAS)
def test_toda_regra_esta_nas_premissas_da_tool_e_da_pessoa(regra):
    for nome, texto in (("ds.PREMISSAS", ds.PREMISSAS), ("PREMISSAS_V2", " ".join(PREMISSAS_V2))):
        for t in REGRAS[regra]:
            assert t in texto.lower(), f"{nome} sem a regra {regra}: {t!r}"


def test_casas_dos_juros_da_regra_sao_as_do_campo():
    assert "no máximo quatro" in PREMISSAS_V2[-1] and rota.CASAS_JUROS == 4
    assert re.match(rota._JUROS, "1.9999") and not re.match(rota._JUROS, "1.99999")


def test_loc_aponta_o_campo_do_cenario(uid):
    r = post(uid, {"cenarios": [_cen(), _cen(nome="y", data_compra="2020-01-01")]})
    assert any(d["loc"][:3] == ["body", "cenarios", 1] for d in r.json()["error"]["details"])


def test_user_id_no_corpo_422(uid):
    assert post(uid, {**CORPO, "user_id": 1}).status_code == 422


# ── 7. isolamento ───────────────────────────────────────────────────────────

def test_b_nao_ve_o_saldo_de_a_nem_troca_de_usuario(uid, monkeypatch):
    b = usuario_pagante("pro_max")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{uid},{b}")
    db.set_balance(b, D("7"))
    c, h = cliente(b)
    r = c.post(ROTA, json=CORPO, params={"user_id": uid, "uid": uid}, headers=h)
    assert r.status_code == 200
    assert D(r.json()["base"]["saldo"]) == D("7")
    assert D(ok(uid)["base"]["saldo"]) == D("1000")


# ── 8. as duas portas ───────────────────────────────────────────────────────

def test_app_mesmo_contrato_e_onboarding_obrigatorio(uid):
    sem = usuario_pagante("pro_max")
    c = TestClient(dashboard.app)
    c.headers["Authorization"] = f"Bearer {sessao_de(sem)['access']}"
    r = c.post("/api/app/simulador", json=CORPO)
    assert r.status_code == 403 and r.json()["error"]["code"] == "open_finance_onboarding_required"
    q("update auth_accounts set open_finance_onboarding_completed_at=now() where user_id=%s", (uid,))
    c = TestClient(dashboard.app)
    c.headers["Authorization"] = f"Bearer {sessao_de(uid)['access']}"
    r = c.post("/api/app/simulador", json=CORPO)
    assert r.status_code == 200, r.text
    sem_hora = lambda d: {k: v for k, v in d.items() if k not in ("calculado_em", "valido_ate")}
    assert sem_hora(r.json()) == sem_hora(ok(uid))


# ── 9. CSRF ─────────────────────────────────────────────────────────────────

def test_post_sem_token_csrf_403(uid):
    r = post(uid, com_csrf=False)
    assert r.status_code == 403


# ── 10. sem escrita ─────────────────────────────────────────────────────────

def _contagens(uid):
    tabelas = [t["table_name"] for t in q("""select table_name from information_schema.columns
                where table_schema='public' and column_name='user_id' order by 1""")]
    return {t: q(f'select count(*) n from "{t}" where user_id=%s', (uid,))[0]["n"] for t in tabelas}


def test_nao_grava_nada(uid):
    c, h = cliente(uid)  # a sessão do teste grava auth_sessions: fica fora da medição
    antes = _contagens(uid)
    assert c.post(ROTA, json=CORPO, headers=h).status_code == 200
    assert _contagens(uid) == antes
    recorrente(uid, "Novo", 1)  # controle: a medição enxerga escrita
    assert _contagens(uid) != antes
