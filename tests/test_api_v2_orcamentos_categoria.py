"""`/api/v2/orcamentos-categoria` pelo monólito real: sessão, CSRF do pai, isolamento entre usuários,
teto do plano (a mesma fonte na rota antiga `/budgets/{uid}`), valor, categoria e mês. Banco real."""
import threading
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf, sessao_de as sessao
from conftest import em_carencia, promote_to_pro
from core.services import plan_limits
from db import ensure_user
from db.accounts import add_launch_and_update_balance
from db.connection import get_conn
from utils_date import now_tz

GET = "/api/v2/orcamentos-categoria"
APAGAR = GET + "/apagar"
MES = f"{now_tz().year:04d}-{now_tz().month:02d}"


@pytest.fixture
def a_e_b(monkeypatch, user_id):
    a = user_id
    b = int(uuid.uuid4().int % 10_000_000_000)  # o autouse do conftest apaga depois
    ensure_user(b)
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    # `sessao_de` precisa de `auth_accounts` e o painel só deixa entrar quem tem direito: o único Grátis
    # com acesso é a carência (tier free, `budgets_max` 3). O Essencial do teste promove por cima.
    em_carencia(a)
    em_carencia(b)
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


def _post(quem, corpo, rota=GET, **q):  # `quem` e não `uid`: o teste de isolamento manda `?uid=` na query
    client, h = _cliente(quem)
    return client.post(rota, json=corpo, headers=h, params=q)


def _legado(uid, categoria, budget):
    client, h = _cliente(uid)
    return client.post(f"/budgets/{uid}", json={"categoria": categoria, "budget": budget}, headers=h)


def _linhas(uid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select categoria, budget from category_budgets where user_id=%s order by categoria", (uid,))
        return [(r["categoria"], float(r["budget"])) for r in cur.fetchall()]


def _item(r, categoria):
    return next((o for o in r.json()["orcamentos"] if o["categoria"] == categoria), None)


def _loc(r):
    return r.json()["error"]["details"][0]["loc"]


# ── isolamento ───────────────────────────────────────────────────────────────

def test_b_nao_ve_nem_muda_o_orcamento_de_a(a_e_b):
    a, b = a_e_b
    assert _post(a, {"categoria": "lazer", "valor": "500"}).status_code == 200
    assert _item(_get(a), "lazer")["teto"] == "500.00"  # controle positivo
    assert _get(b, user_id=a, uid=a).json()["orcamentos"] == []
    for q in ({}, {"user_id": a, "uid": a}):
        r = _post(b, {"categoria": "lazer", "valor": "1", "user_id": a, "uid": a}, **q)
        assert r.status_code == 200, r.text
    assert _linhas(a) == [("lazer", 500.0)] and _linhas(b) == [("lazer", 1.0)]
    assert _post(b, {"categoria": "lazer"}, APAGAR).status_code == 200
    assert _post(b, {"categoria": "lazer"}, APAGAR).status_code == 404  # só A tem agora
    assert _linhas(a) == [("lazer", 500.0)]


def test_gasto_de_a_nao_entra_no_de_b(a_e_b):
    a, b = a_e_b
    add_launch_and_update_balance(a, "despesa", 80.0, "t", "t", categoria="lazer")
    for u in (a, b):
        _post(u, {"categoria": "lazer", "valor": "100"})
    assert (_item(_get(a), "lazer")["gasto"], _item(_get(b), "lazer")["gasto"]) == ("80.00", "0.00")


# ── teto do plano ────────────────────────────────────────────────────────────

def _tres(uid):
    for c in ("lazer", "mercado", "saúde"):
        assert _post(uid, {"categoria": c, "valor": "200"}).status_code == 200


def test_gratis_para_na_quarta_mas_atualiza_a_existente_em_outra_grafia(a_e_b):
    a, _ = a_e_b
    _tres(a)
    r = _post(a, {"categoria": "transporte", "valor": "200"})
    assert (r.status_code, r.json()["error"]["code"]) == (403, "plan_limit"), r.text
    assert len(_linhas(a)) == 3
    assert _get(a).json()["limite"] == 3
    # controle positivo: no teto, "LAZER" e "saude" atualizam a existente, sem gêmea
    for c in ("LAZER", "saude"):
        assert _post(a, {"categoria": c, "valor": "321"}).status_code == 200
    assert sorted(_linhas(a)) == [("lazer", 321.0), ("mercado", 200.0), ("saúde", 321.0)]
    assert _post(a, {"categoria": "lazer"}, APAGAR).status_code == 200
    assert _post(a, {"categoria": "transporte", "valor": "200"}).status_code == 200


def test_essencial_e_pago_nao_tem_teto(a_e_b, monkeypatch):
    a, _ = a_e_b
    promote_to_pro(a, plan="essencial")
    for c in ("lazer", "mercado", "saúde", "transporte", "alimentação"):
        assert _post(a, {"categoria": c, "valor": "10"}).status_code == 200
    assert _get(a).json()["limite"] is None


def test_mesma_regra_na_rota_antiga_sem_gemea(a_e_b):
    a, _ = a_e_b
    _tres(a)
    r = _legado(a, "transporte", 10)
    assert (r.status_code, r.json()["detail"]) == (403, {"error": "pro_required", "feature": "budgets_limit", "limit": 3})
    assert _legado(a, "lazer", 77).status_code == 200
    r = _legado(a, "Mercado", 88)  # a regressão do furo: outra caixa não vira 4ª linha
    assert r.status_code == 200 and r.json()["categoria"] == "mercado", r.text
    assert sorted(_linhas(a)) == [("lazer", 77.0), ("mercado", 88.0), ("saúde", 200.0)]


def test_fonte_unica_do_teto(a_e_b, monkeypatch):
    a, _ = a_e_b
    monkeypatch.setitem(plan_limits.FREE_LIMITS, "budgets_max", 2)
    assert _post(a, {"categoria": "lazer", "valor": "1"}).status_code == 200
    assert _legado(a, "mercado", 1).status_code == 200
    assert _post(a, {"categoria": "saúde", "valor": "1"}).status_code == 403
    assert _legado(a, "saúde", 1).status_code == 403


# ── valor e categoria ────────────────────────────────────────────────────────

@pytest.mark.parametrize("valor", ["0", "-1", "1,5", 1.5, "1e3", "1234567890", "12.345", "", "١٢", "0.00"])
def test_valor_invalido_422_sem_linha(a_e_b, valor):
    a, _ = a_e_b
    r = _post(a, {"categoria": "lazer", "valor": valor})
    assert r.status_code == 422 and _loc(r) == ["body", "valor"], r.text
    assert _linhas(a) == []


def test_valor_nos_extremos_sai_exato(a_e_b):
    a, _ = a_e_b
    assert _item(_post(a, {"categoria": "lazer", "valor": "0.01"}), "lazer")["teto"] == "0.01"
    assert _item(_post(a, {"categoria": "lazer", "valor": "999999999.99"}), "lazer")["teto"] == "999999999.99"


@pytest.mark.parametrize("cat", ["xyz-nao-existe", "", "   ", "a\x00", "x" * 81])
def test_categoria_invalida_422_sem_linha(a_e_b, cat):
    a, _ = a_e_b
    r = _post(a, {"categoria": cat, "valor": "10"})
    assert r.status_code == 422 and _loc(r) == ["body", "categoria"], r.text
    assert _linhas(a) == []


def test_categoria_sem_acento_grava_a_do_catalogo(a_e_b):
    a, _ = a_e_b
    assert _item(_post(a, {"categoria": "alimentacao", "valor": "10"}), "alimentação")
    assert _linhas(a) == [("alimentação", 10.0)]


def test_orcamento_fora_do_catalogo_pode_ser_editado(a_e_b):
    a, _ = a_e_b
    db.upsert_budget(a, "viagem antiga", 100)
    r = _post(a, {"categoria": "viagem antiga", "valor": "200"})
    assert r.status_code == 200 and _item(r, "viagem antiga")["teto"] == "200.00", r.text
    assert _post(a, {"categoria": "outra inventada", "valor": "200"}).status_code == 422


def test_mesma_categoria_nova_em_paralelo_nao_estoura(a_e_b):
    a, _ = a_e_b
    n, erros, saidas = 6, [], []
    barreira = threading.Barrier(n)

    def corre(i):
        try:
            barreira.wait()
            saidas.append(db.upsert_budget(a, "lazer", 10 + i, limite=None))
        except Exception as e:  # noqa: BLE001 - o teste quer ver qualquer uma
            erros.append(e)

    ts = [threading.Thread(target=corre, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert erros == []
    assert len(_linhas(a)) == 1 and [c for _, c in saidas].count(True) == 1


# ── mês, formato, apagar ─────────────────────────────────────────────────────

def test_so_o_mes_corrente_e_ignora_mes_da_query(a_e_b):
    a, _ = a_e_b
    _post(a, {"categoria": "lazer", "valor": "100"})
    add_launch_and_update_balance(a, "despesa", 30.0, "t", "t", categoria="lazer")
    add_launch_and_update_balance(a, "despesa", 999.0, "t", "t", categoria="lazer", criado_em=now_tz().replace(day=1) - timedelta(days=3))
    r = _get(a, mes="2020-01")
    assert (r.json()["mes"], _item(r, "lazer")["gasto"]) == (MES, "30.00")


def test_formato_status_e_total(a_e_b):
    a, _ = a_e_b
    for c, teto, gasto in (("lazer", "100", 79.99), ("mercado", "100", 80.0), ("saúde", "100", 100.0)):
        _post(a, {"categoria": c, "valor": teto})
        add_launch_and_update_balance(a, "despesa", gasto, "t", "t", categoria=c)
    r = _get(a)
    assert [_item(r, c)["status"] for c in ("lazer", "mercado", "saúde")] == ["verde", "amarelo", "vermelho"]
    add_launch_and_update_balance(a, "despesa", 20.0, "t", "t", categoria="saúde")
    r = _get(a)
    assert _item(r, "saúde")["restante"] == "-20.00"
    assert r.json()["total"]["em_risco"] == 2
    for o in r.json()["orcamentos"]:
        assert o["chave"] and all(isinstance(o[k], str) for k in ("teto", "gasto", "restante", "usado_pct"))
    assert all(isinstance(v, str) for k, v in r.json()["total"].items() if k != "em_risco")


def test_apagar_tira_da_resposta_e_do_get(a_e_b):
    a, _ = a_e_b
    _post(a, {"categoria": "lazer", "valor": "100"})
    r = _post(a, {"categoria": "lazer"}, APAGAR)
    assert r.status_code == 200 and r.json()["orcamentos"] == []
    assert _get(a).json()["orcamentos"] == []


# ── CSRF e sessão ────────────────────────────────────────────────────────────

def test_sem_csrf_403_e_nada_gravado(a_e_b):
    a, _ = a_e_b
    _post(a, {"categoria": "lazer", "valor": "100"})
    client, h = _cliente(a, com_csrf=False)
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "x")  # cookie sem o header
    for rota, corpo in ((GET, {"categoria": "mercado", "valor": "5"}), (APAGAR, {"categoria": "lazer"})):
        r = client.post(rota, json=corpo)
        assert r.status_code == 403 and "detail" in r.json(), r.text
    r = client.post(f"/budgets/{a}", json={"categoria": "mercado", "budget": 5})
    assert r.status_code == 403 and "detail" in r.json()
    assert _linhas(a) == [("lazer", 100.0)]


def test_sem_sessao_401_e_fora_do_beta_404(a_e_b, monkeypatch):
    a, _ = a_e_b
    assert TestClient(dashboard.app).get(GET).status_code == 401
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")
    r = _get(a)
    assert (r.status_code, r.json()["error"]["code"]) == (404, "dashboard_v2_disabled")
