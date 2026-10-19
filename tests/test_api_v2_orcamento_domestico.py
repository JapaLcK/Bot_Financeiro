"""`/api/v2/orcamento-domestico` pelo monólito real: sessão, CSRF do pai, gate Plus/Pro,
isolamento entre usuários, soma dos potes, renda informada x calculada e mês. Banco real."""
import uuid

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf, sessao_de as sessao
from conftest import promote_to_pro
from db import ensure_user
from db.accounts import add_launch_and_update_balance
from db.connection import get_conn
from db.household_budget import get_config
from utils_date import now_tz

GET = "/api/v2/orcamento-domestico"
POTES = GET + "/potes"
RENDA = GET + "/renda"
_agora = now_tz()
MES = f"{_agora.year:04d}-{_agora.month:02d}"
FUTURO = f"{_agora.year + 1:04d}-{_agora.month:02d}"
CHAVES = ["custos_fixos", "conforto", "metas", "prazeres", "liberdade_financeira", "conhecimento"]


def _corpo(*pcts, mes=MES):
    return {"mes": mes, "potes": [{"chave": c, "pct": p} for c, p in zip(CHAVES, pcts)]}


@pytest.fixture
def a_e_b(monkeypatch, user_id):
    a = user_id
    b = int(uuid.uuid4().int % 10_000_000_000)  # o autouse do conftest apaga depois
    ensure_user(b)
    promote_to_pro(a)  # 'pro' gravado = Plus no v2
    promote_to_pro(b)
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{b}")
    return a, b


def _cliente(uid, com_csrf=True):
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao(uid)["dashboard"])
    return client, (csrf(client) if com_csrf else {})


def _get(quem, **q):  # `quem` e não `uid`: o teste de isolamento manda `?uid=` na query
    client, _ = _cliente(quem)
    return client.get(GET, params=q)


def _post(uid, rota, corpo):
    client, h = _cliente(uid)
    return client.post(rota, json=corpo, headers=h)


def _linhas(uid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select (select count(*) from household_budget_config where user_id=%s) c,"
                    " (select count(*) from household_budget_income where user_id=%s) i", (uid, uid))
        r = cur.fetchone()
    return r["c"], r["i"]


def _pote(r, chave):
    return next(p for p in r.json()["potes"] if p["chave"] == chave)


# ── isolamento ───────────────────────────────────────────────────────────────

def test_b_nao_ve_nem_muda_o_orcamento_de_a(a_e_b):
    a, b = a_e_b
    add_launch_and_update_balance(a, "receita", 1000.0, "t", "t")
    add_launch_and_update_balance(a, "despesa", 100.0, "t", "t", categoria="Mercado")
    assert _post(a, POTES, _corpo("40", "10", "10", "10", "20", "10")).status_code == 200
    assert _post(a, RENDA, {"mes": MES, "valor": "5000"}).status_code == 200
    ra = _get(a)  # controle positivo: A vê o que gravou
    assert (ra.json()["renda"], _pote(ra, "custos_fixos")["pct"], _pote(ra, "custos_fixos")["gasto"]) == \
        ({"valor": "5000.00", "origem": "informada"}, "40.00", "100.00")
    rb = _get(b, user_id=a, uid=a)  # o uid vindo do cliente não conta
    assert rb.status_code == 200, rb.text
    assert rb.json()["renda"] == {"valor": "0.00", "origem": "calculada"}
    assert [_pote(rb, "custos_fixos")["pct"], _pote(rb, "custos_fixos")["gasto"]] == ["55.00", "0.00"]
    assert _post(b, POTES, _corpo("55", "5", "10", "10", "10", "10")).status_code == 200
    assert get_config(a)["custos_fixos"] == 40.0  # POST de B não mexeu em A


def test_campos_de_dono_no_corpo_sao_ignorados(a_e_b):
    a, b = a_e_b
    assert _post(a, POTES, _corpo("40", "10", "10", "10", "20", "10")).status_code == 200
    assert _post(a, RENDA, {"mes": MES, "valor": "5000"}).status_code == 200
    dono = {"user_id": a, "uid": a}
    r = _post(b, RENDA, {"mes": MES, "valor": "777", **dono})
    assert r.json()["renda"] == {"valor": "777.00", "origem": "informada"}, r.text  # controle positivo: caiu em B
    r = _post(b, POTES, {**_corpo("30", "10", "10", "10", "30", "10"), **dono})
    assert _pote(r, "custos_fixos")["pct"] == "30.00", r.text
    ra = _get(a)
    assert ra.json()["renda"] == {"valor": "5000.00", "origem": "informada"}
    assert _pote(ra, "custos_fixos")["pct"] == "40.00"


# ── gate ─────────────────────────────────────────────────────────────────────

def test_essencial_leva_403_pro_required_e_nada_e_gravado(monkeypatch, user_id):
    promote_to_pro(user_id, plan="essencial")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    for r in (_get(user_id), _post(user_id, POTES, _corpo("55", "5", "10", "10", "10", "10")),
              _post(user_id, RENDA, {"mes": MES, "valor": "10"})):
        assert (r.status_code, r.json()["error"]["code"]) == (403, "pro_required"), r.text
    assert _linhas(user_id) == (0, 0)


def test_plus_entra(a_e_b):
    assert _get(a_e_b[0]).status_code == 200


# ── soma dos potes ───────────────────────────────────────────────────────────

def test_soma_errada_422_e_config_intacta(a_e_b):
    a, _ = a_e_b
    antes = _get(a).json()["potes"]
    r = _post(a, POTES, _corpo("55", "5", "10", "10", "10", "9.99"))
    assert r.status_code == 422, r.text
    assert _get(a).json()["potes"] == antes


def test_soma_em_centesimos_fecha(a_e_b):
    a, _ = a_e_b
    r = _post(a, POTES, _corpo("33.33", "33.33", "33.34", "0", "0", "0"))
    assert r.status_code == 200, r.text
    assert [p["pct"] for p in r.json()["potes"]] == ["33.33", "33.33", "33.34", "0.00", "0.00", "0.00"]


@pytest.mark.parametrize("corpo", [
    _corpo("101", "0", "0", "0", "0", "0"),
    _corpo("1e2", "0", "0", "0", "0", "0"),
    _corpo("-5", "105", "0", "0", "0", "0"),
    _corpo("55", "5", "10", "10", "10"),                       # pote faltando
    _corpo("55", "5", "10", "10", "10", "10", mes=FUTURO),     # mês futuro
    # 7 itens: os 6 certos (soma 100) + `custos_fixos` de novo. Sem a guarda do dict, o 7º sobrescreve o 1º e o `save_config` aceita.
    {"mes": MES, "potes": [{"chave": c, "pct": p} for c, p in zip(CHAVES, "55 5 10 10 10 10".split())] + [{"chave": "custos_fixos", "pct": "55"}]},
    _corpo("٥٥", "5", "10", "10", "10", "10"),                 # dígitos árabe-índicos: `\d` passaria, `[0-9]` não
    {"mes": MES, "potes": [{"chave": c, "pct": p} for c, p in zip(CHAVES[:5] + ["outro"], "55 5 10 10 10 10".split())]},
])
def test_potes_invalidos_422(a_e_b, corpo):
    a, _ = a_e_b
    r = _post(a, POTES, corpo)
    assert r.status_code == 422, r.text
    assert _linhas(a) == (0, 0)


# ── renda ────────────────────────────────────────────────────────────────────

def test_renda_informada_volta_a_calculada_e_zero_vale(a_e_b):
    a, _ = a_e_b
    add_launch_and_update_balance(a, "receita", 300.0, "t", "t")
    r = _post(a, RENDA, {"mes": MES, "valor": "1234.56"})
    assert r.json()["renda"] == {"valor": "1234.56", "origem": "informada"}
    assert _pote(r, "metas")["teto"] == "123.46"  # 10% de 1234.56
    assert _post(a, RENDA, {"mes": MES, "valor": None}).json()["renda"] == {"valor": "300.00", "origem": "calculada"}
    r = _post(a, RENDA, {"mes": MES, "valor": "0"})
    assert r.json()["renda"] == {"valor": "0.00", "origem": "informada"}
    assert _pote(r, "metas")["usado_pct"] is None


@pytest.mark.parametrize("valor", ["1,5", "-1", 1234.5, "", "abc", "1234567890", "٥٥"])
def test_renda_invalida_422(a_e_b, valor):
    a, _ = a_e_b
    assert _post(a, RENDA, {"mes": MES, "valor": valor}).status_code == 422
    assert _linhas(a)[1] == 0


def test_renda_sem_o_campo_valor_422(a_e_b):
    assert _post(a_e_b[0], RENDA, {"mes": MES}).status_code == 422


def test_renda_de_um_mes_nao_afeta_outro(a_e_b):
    a, _ = a_e_b
    ano, m = (_agora.year - 1, 12) if _agora.month == 1 else (_agora.year, _agora.month - 1)
    antes = f"{ano:04d}-{m:02d}"
    _post(a, RENDA, {"mes": MES, "valor": "777"})
    assert _get(a, mes=antes).json()["renda"]["origem"] == "calculada"


# ── mês ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("mes", ["2026-13", "2026-1", FUTURO, "abc"])
def test_get_mes_invalido_ou_futuro_422(a_e_b, mes):
    assert _get(a_e_b[0], mes=mes).status_code == 422


def test_post_mes_futuro_422_no_corpo(a_e_b):
    r = _post(a_e_b[0], RENDA, {"mes": FUTURO, "valor": "10"})
    assert r.status_code == 422
    assert ["body", "mes"] in [d["loc"] for d in r.json()["error"]["details"]]


def test_sem_mes_e_o_corrente(a_e_b):
    assert _get(a_e_b[0]).json()["mes"] == MES


# ── CSRF e formato ───────────────────────────────────────────────────────────

def test_post_sem_token_csrf_403_e_com_token_200(a_e_b):
    a, _ = a_e_b
    client, _ = _cliente(a, com_csrf=False)
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "tok")
    r = client.post(RENDA, json={"mes": MES, "valor": "10"})
    assert r.status_code == 403 and "detail" in r.json(), r.text
    assert _linhas(a)[1] == 0
    r = client.post(RENDA, json={"mes": MES, "valor": "10"}, headers={dashboard.CSRF_HEADER_NAME: "tok"})
    assert r.status_code == 200, r.text


def test_dinheiro_e_pct_saem_como_texto(a_e_b):
    j = _get(a_e_b[0]).json()
    for p in j["potes"]:
        assert all(isinstance(p[k], str) for k in ("pct", "teto", "gasto", "restante"))
    assert isinstance(j["renda"]["valor"], str) and isinstance(j["total"]["teto"], str)
    assert isinstance(j["total"]["restante_livre"], str)
    assert {p["chave"] for p in j["potes"] if p["guardado"]} == {"metas", "liberdade_financeira"}


def test_lancamentos_do_pote_em_texto_somam_o_gasto_e_nao_vazam(a_e_b):
    a, b = a_e_b
    add_launch_and_update_balance(a, "receita", 1000.0, "t", "t")
    add_launch_and_update_balance(a, "despesa", 100.5, "Aluguel", "t", categoria="Moradia")
    add_launch_and_update_balance(a, "despesa", 30.0, "Pão de Açúcar", "t", categoria="Mercado")
    add_launch_and_update_balance(b, "despesa", 999.0, "do B", "t", categoria="Moradia")
    p = _pote(_get(a), "custos_fixos")
    assert p["gasto"] == "130.50"
    assert sum(float(x["valor"]) for x in p["lancamentos"]) == 130.5  # controle positivo: não vem vazio
    assert sum(float(x["total"]) for x in p["por_categoria"]) == 130.5
    assert {x["descricao"] for x in p["lancamentos"]} == {"Aluguel", "Pão de Açúcar"}
    assert all(isinstance(x["valor"], str) and x["origem"] == "conta" for x in p["lancamentos"])
    assert all(isinstance(x["total"], str) for x in p["por_categoria"])
    assert p["categorias"] and all(isinstance(c, str) for c in p["categorias"])
    assert "do B" not in {x["descricao"] for q in _get(a).json()["potes"] for x in q["lancamentos"]}
    assert _pote(_get(b), "custos_fixos")["gasto"] == "999.00"
