"""`/api/v2/cartoes`, `/parcelas`, `/renda`, `/insights` e `/rendimento` (dados reais dos cards do Resumo do /painel) pelo
monólito real: a MESMA função da rota legada e do chat (número igual, dinheiro em texto), isolamento entre usuários, gate
de plano antes de ler ou gerar, LLM nunca real, dois namespaces e não-finito. Banco real."""
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from api.nativo.app import app as app_nativo
from conftest import usuario_pagante
from core import ai_patterns
from db.analytics import compute_evolution
from db.cards import list_installment_groups_detailed
from db.cartoes_resumo import resumo_cartoes
from test_api_app_sessao import completo
from tests._patrimonio_helpers import conexao, posicao
from utils_date import today_tz

ROTAS = ("/cartoes", "/parcelas", "/renda", "/insights", "/rendimento")
V2, APP = "/api/v2", "/api/app"
ITENS_B = [{"title": "Dica so do B", "message": "mensagem do B", "severity": "info"}]


@pytest.fixture
def a_e_b(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    a, b = usuario_pagante("pro"), usuario_pagante("pro")  # ids pequenos: o lançamento cai no usuário certo
    hoje = today_tz()
    for uid, nome, valor, receita in ((a, "CartaoA", 80, 1000), (b, "CartaoB", 4321, 7777)):
        cartao = db.create_card(uid, nome, closing_day=10, due_day=17, credit_limit=9000)
        db.add_credit_purchase(uid, cartao, valor, "mercado", f"compra {nome}", hoje)
        db.add_credit_purchase_installments(uid, cartao, 300, "casa", f"parcelado {nome}", hoje, 3)
        db.add_launch_and_update_balance(uid, "receita", receita, None, "salario")
    ai_patterns._save_cache(b, ai_patterns._cache_kind("insights"), ITENS_B)
    # Depois de semear: com a chave ligada, a trava Q36 recusa as escritas manuais da semente.
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{b}")
    return a, b


@pytest.fixture(autouse=True)
def llm_nunca_real(monkeypatch):
    chamadas = []
    monkeypatch.setattr(ai_patterns, "_call_llm", lambda *a, **k: chamadas.append(a) or [{"title": "Gerada", "message": "m"}])
    return chamadas


def _get(rota, quem, prefixo=V2, **q):
    c = TestClient(dashboard.app, raise_server_exceptions=False)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(quem)["dashboard"])
    return c.get(prefixo + rota, params=q)


def _ok(rota, quem, **q):
    r = _get(rota, quem, **q)
    assert r.status_code == 200, r.text
    return r.json()


def _txt(x):
    """O que a rota deve devolver para um número do módulo: texto de 2 casas, nunca `0` nem `5000.0`."""
    return None if x is None else str(Decimal(str(x)).quantize(Decimal("0.01")))


# ── o mesmo número da função de origem ───────────────────────────────────────

def test_cartoes_e_o_resumo_do_cartao_campo_a_campo(a_e_b):
    a, _ = a_e_b
    j = _ok("/cartoes", a)
    esperado = resumo_cartoes(a)
    assert len(j["cards"]) == len(esperado) == 1
    c, e = j["cards"][0], esperado[0]
    assert set(c) == {"name", "last4", "due_day", "closing_day", "credit_limit", "credit_used", "open_bill"}
    assert set(c["open_bill"]) == {"paid_amount", "due_amount"}
    assert (c["name"], c["last4"], c["due_day"], c["closing_day"]) == (e["name"], e["last4"], e["due_day"], e["closing_day"])
    assert c["credit_limit"] == "9000.00" and c["credit_used"] == _txt(e["credit_used"]) and c["credit_used"] != "0.00"
    assert c["open_bill"] == {"paid_amount": _txt(e["open_bill"]["paid_amount"]), "due_amount": _txt(e["open_bill"]["due_amount"])}
    assert c["open_bill"]["paid_amount"] == "0.00"  # nunca `0` nem `0.0`


def test_parcelas_e_a_lista_detalhada_campo_a_campo(a_e_b):
    a, _ = a_e_b
    j = _ok("/parcelas", a)
    esperado = list_installment_groups_detailed(a, "urgency")
    assert len(j["installments"]) == len(esperado) == 1
    g, e = j["installments"][0], esperado[0]
    assert set(g) == {"name", "card_name", "installments_total", "manual", "parcelas"} and g["installments_total"] == 3
    assert (g["name"], g["card_name"], g["manual"]) == (e["name"], e["card_name"], e["manual"]) and g["manual"] is True
    assert g["parcelas"] == [{"installment_no": p["installment_no"], "valor": _txt(p["valor"]), "is_paid": p["is_paid"],
                              "is_next": p["is_next"], "due_date": p["due_date"]} for p in e["parcelas"]]
    assert [p["valor"] for p in g["parcelas"]] == ["100.00"] * 3 and [p["is_next"] for p in g["parcelas"]].count(True) == 1
    # grupo com uma linha não manual (Open Finance): manual=False, igual ao da lista detalhada
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update credit_transactions set source='open_finance' where id=(select max(id) from credit_transactions where user_id=%s and installments_total=3)", (a,))
        conn.commit()
    assert _ok("/parcelas", a)["installments"][0]["manual"] is False is list_installment_groups_detailed(a, "urgency")[0]["manual"]


def test_renda_e_a_evolucao_de_6_meses(a_e_b):
    a, _ = a_e_b
    j = _ok("/renda", a)
    esperado = compute_evolution(a, 6)
    assert j["months"] == 6 and len(j["evolution"]) == 6
    assert j["evolution"] == [{"month": m["month"], "income": _txt(m["income"]), "expense": _txt(m["expense"])} for m in esperado]
    assert Decimal(j["evolution"][-1]["income"]) == 1000 and j["evolution"][0]["income"] == "0.00"


def test_insights_cacheado_nao_chama_o_llm_e_sem_cache_gera_uma_vez(a_e_b, llm_nunca_real):
    a, b = a_e_b
    assert _ok("/insights", b) == {"insights": [{"title": "Dica so do B", "message": "mensagem do B"}]}  # cache: sem LLM
    assert llm_nunca_real == []
    assert _ok("/insights", a) == {"insights": [{"title": "Gerada", "message": "m"}]}
    _ok("/insights", a)
    assert len(llm_nunca_real) == 1  # a 2ª leitura vem do cache de 6 h


def _rajada(quem, n=6):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(n * len(quem)) as ex:
        return list(ex.map(lambda u: _get("/insights", u), quem * n))


@pytest.fixture
def llm_lento(monkeypatch):
    import time
    chamadas = []
    monkeypatch.setattr(ai_patterns, "_call_llm", lambda *a, **k: chamadas.append(a) or time.sleep(0.4) or [{"title": "Gerada", "message": "m"}])
    return chamadas


def test_rajada_do_mesmo_usuario_sem_cache_gera_uma_vez_so(a_e_b, llm_lento):
    a, _ = a_e_b
    rs = _rajada([a])
    assert {r.status_code for r in rs} == {200} and len({r.text for r in rs}) == 1
    assert len(llm_lento) == 1


def test_controle_negativo_sem_a_trava_a_rajada_paga_varias_vezes(a_e_b, llm_lento, monkeypatch):
    import threading

    class Solta(dict):
        def setdefault(self, k, v):
            return threading.Lock()  # trava nova a cada pedido = sem trava
    monkeypatch.setattr(ai_patterns, "_INSIGHTS_LOCKS", Solta())
    _rajada([a_e_b[0]])
    assert len(llm_lento) > 1


def test_controle_positivo_dois_usuarios_geram_um_cada(monkeypatch, a_e_b, llm_lento):
    a, _ = a_e_b
    c = usuario_pagante("pro")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{c}")
    rs = _rajada([a, c], n=3)
    assert {r.status_code for r in rs} == {200} and len(llm_lento) == 2


# ── isolamento ───────────────────────────────────────────────────────────────

def test_a_nao_ve_nada_do_b_nem_com_uid_na_query(a_e_b, llm_nunca_real):
    a, b = a_e_b
    for extra in ({}, {"uid": b, "user_id": b}):
        j = {r: _ok(r, a, **extra) for r in ROTAS[:4]}
        assert [c["name"] for c in j["/cartoes"]["cards"]] == ["CartaoA"]
        assert j["/cartoes"]["cards"][0]["credit_used"] == "380.00"
        assert [g["card_name"] for g in j["/parcelas"]["installments"]] == ["CartaoA"]
        assert Decimal(j["/renda"]["evolution"][-1]["income"]) == 1000
        assert "Dica so do B" not in str(j["/insights"])
    assert "7777" not in str(_ok("/renda", a)) and "4321" not in str(_ok("/cartoes", a))


def test_controle_positivo_b_ve_os_proprios(a_e_b):
    _, b = a_e_b
    assert [c["name"] for c in _ok("/cartoes", b)["cards"]] == ["CartaoB"]
    assert _ok("/parcelas", b)["installments"][0]["card_name"] == "CartaoB"
    assert Decimal(_ok("/renda", b)["evolution"][-1]["income"]) == 7777
    assert _ok("/insights", b)["insights"][0]["title"] == "Dica so do B"


def test_rendimento_de_a_nao_traz_o_de_b_e_responde_nos_dois_namespaces(a_e_b):
    a, b = a_e_b
    posicao(conexao(b, f"i-{b}", banco="BancoDoB"), "pB", "4321")  # só o B tem investimento
    v2 = _ok("/rendimento", a, uid=b, user_id=b)
    assert v2["itens"] == [] and v2["rendimentos"]["quantidade"] == 0 and "BancoDoB" not in str(v2)
    assert "4321" not in str(v2)
    proprio = _ok("/rendimento", b)  # controle positivo: o dado do B existe e a rota o enxerga
    assert proprio["rendimentos"]["quantidade"] == 1 and proprio["rendimentos"]["saldo"] == "4321.00"
    completo(a)
    r = TestClient(dashboard.app)
    r.headers["Authorization"] = f"Bearer {sessao_de(a)['access']}"
    app = r.get(APP + "/rendimento", params={"user_id": b})
    assert app.status_code == 200, app.text
    assert app.json() == v2
    caminhos = [x.path for r in app_nativo.routes for x in (r.original_router.routes if hasattr(r, "original_router") else [r])]
    assert caminhos.count("/rendimento") == 1  # uma só: a da criar_app, não a duplicada do laço do app


# ── gate de plano ────────────────────────────────────────────────────────────

def _essencial(monkeypatch):
    uid = usuario_pagante("essencial")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(uid))
    return uid


def _pro_required(r):
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "pro_required"
    assert "evolution" not in r.text and "title" not in r.text


def test_essencial_403_em_renda_e_insights_e_nada_e_lido(monkeypatch, llm_nunca_real):
    uid = _essencial(monkeypatch)
    lido, gerado = [], []
    monkeypatch.setattr("api.v2.renda.compute_evolution", lambda *a, **k: lido.append(a) or [])
    monkeypatch.setattr("api.v2.insights.generate_ai_insights", lambda *a, **k: gerado.append(a) or [])
    _pro_required(_get("/renda", uid))
    _pro_required(_get("/insights", uid))
    assert lido == [] and gerado == [] and llm_nunca_real == []


def test_controle_negativo_sem_o_gate_a_leitura_acontece(monkeypatch, llm_nunca_real):
    uid = _essencial(monkeypatch)
    lido, gerado = [], []
    monkeypatch.setattr("api.v2.renda.compute_evolution", lambda *a, **k: lido.append(a) or [])
    monkeypatch.setattr("api.v2.insights.generate_ai_insights", lambda *a, **k: gerado.append(a) or [])
    monkeypatch.setattr("api.v2.comparacao.require_plan_feature", lambda *a: None)
    monkeypatch.setattr("api.v2.insights.require_plan_feature", lambda *a: None)
    assert _get("/renda", uid).status_code == 200 and _get("/insights", uid).status_code == 200
    assert len(lido) == 1 and len(gerado) == 1  # o registrador mede: sem o gate, é chamado


def test_plus_200_e_cartoes_parcelas_sem_gate_para_o_essencial(monkeypatch, a_e_b):
    a, _ = a_e_b
    assert _get("/renda", a).status_code == _get("/insights", a).status_code == 200
    uid = _essencial(monkeypatch)
    assert _ok("/cartoes", uid) == {"cards": []} and _ok("/parcelas", uid) == {"installments": []}


# ── sessão e não-finito ──────────────────────────────────────────────────────

@pytest.mark.parametrize("rota", ROTAS)
def test_sem_sessao_401(rota):
    r = TestClient(dashboard.app, raise_server_exceptions=False).get(V2 + rota)
    assert r.status_code == 401 and "error" in r.json()


def test_nao_finito_vira_500_no_envelope_nunca_nan_no_json(monkeypatch, a_e_b):
    a, _ = a_e_b
    monkeypatch.setattr("api.v2.renda.compute_evolution",
                        lambda *x: [{"month": "2026-10", "income": float("nan"), "expense": float("inf")}])
    r = _get("/renda", a)
    assert r.status_code == 500 and "error" in r.json() and "NaN" not in r.text and "Infinity" not in r.text
    monkeypatch.setattr("api.v2.cartoes.resumo_cartoes", lambda uid: [{
        "name": "x", "last4": None, "due_day": 1, "closing_day": 1, "credit_limit": None, "credit_used": float("nan"),
        "open_bill": {"paid_amount": 0.0, "due_amount": 0.0}}])
    r = _get("/cartoes", a)
    assert r.status_code == 500 and "error" in r.json() and "NaN" not in r.text
