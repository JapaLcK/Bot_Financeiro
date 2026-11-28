"""PR 3d: o leitor de conta de CONDOMÍNIO não vê nome de pessoa (funcionário, condômino). Banco real.

Mundo (`_apoio_mascara.condominio`): "Pix para João da Silva CPF 123..." em `salários` (grupo pessoal), "Mensalidade apto 101
Maria" em `mensalidades` (receitas) e "Sabesp" em `água` (funcionais), em todas as pernas: conta, cartão, parcelado,
gasto fixo, receita fixa, boleto. Para CADA rota classificada "mascara" em `tests/test_leitura_guard.py`:
o JSON serializado INTEIRO do leitor não tem nenhum `PROIBIDO`; vê valor/data/categoria e o "Sabesp"; o dono vê tudo
(controle positivo); com o helper desligado a rota fica vermelha (controle negativo).
"""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import api.nativo.app as nativo
import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from _apoio_leitura import leitor_de, url_de
from _apoio_mascara import PROIBIDO, VISIVEL, condominio
from conftest import promote_to_pro
from core.services import leitura_mascara
from test_leitura_guard import _gets, classe_da_rota

HOJE = date.today().isoformat()
SALARIOS = "salários"

# (rota como o FastAPI a escreve) -> (query, o dono vê nome?, o leitor vê "Sabesp"?)
# `dono_ve=False`: a rota não carrega nome neste mundo (sem Open Finance nem LLM) mas está coberta pelo helper/negação.
_V2 = {
    "assinaturas": ("", False, False), "contas-a-pagar": ("", True, True), "gastos": ("", True, True),
    "insights": ("", True, False), "lancamentos": ("", True, True), "orcamento-domestico": ("", True, True),
    "parcelas": ("", True, False), "previsao": ("", True, False),
    "gastos/categoria": (f"?categoria={SALARIOS}", True, False),
}
ROTAS = {
    **{f"/api/{ns}/{r}": q for ns in ("v2", "app") for r, q in _V2.items()},
    "/data/{user_id}": ("", True, True),
    "/history/{user_id}/list": ("", True, True),
    "/forecast/{user_id}": ("", True, False),
    "/recurring-bills/{user_id}/projection": ("?date=2027-01-15", False, False),
    "/recurring-expenses/{user_id}": ("", True, True),
    "/recurring-incomes/{user_id}": ("", True, False),
    "/household-budget/{user_id}/status": ("", True, True),
    "/categories/{user_id}/launches": (f"?categoria={SALARIOS}", True, False),
    "/analytics/{user_id}/kpis": ("", True, False),
    "/analytics/{user_id}/patterns": ("", False, False),
    "/insights/{user_id}/current": ("", True, False),
    "/bills/{user_id}/{bill_id}": ("", True, False),
    "/installments/{user_id}/list": ("", True, False),
}


@pytest.fixture
def mundo(monkeypatch, user_id):
    promote_to_pro(user_id, plan="pro_max")
    condominio(user_id)  # ANTES da chave v2: com ela o cartão manual é recusado (fonte única do Open Finance)
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    monkeypatch.setattr(nativo, "exigir_onboarding", lambda uid: None)  # o /api/app pede o onboarding do OF: não é o assunto
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from credit_bills where user_id=%s order by id limit 1", (user_id,))
        bill = cur.fetchone()["id"]
    return {"uid": user_id, "bill": bill, "leitor": leitor_de(user_id)["bearer"],
            "dono": {"Authorization": f"Bearer {sessao_de(user_id)['access']}"}, "c": TestClient(dashboard.app)}


def _url(m, rota):
    return url_de(rota.replace("{user_id}", str(m["uid"])).replace("{bill_id}", str(m["bill"]))) + ROTAS[rota][0]


def _texto(r) -> str:
    assert r.status_code == 200, (r.request.url, r.status_code, r.text[:200])
    return json.dumps(r.json(), ensure_ascii=False)  # o JSON inteiro: chave, valor, aninhado


def _vazou(texto: str) -> list[str]:
    return [p for p in PROIBIDO if p in texto]


# ── 1. enumeração: toda rota "mascara" da tabela de classificação tem caso aqui, e vice-versa ──

def test_toda_rota_mascara_classificada_tem_caso_e_vice_versa():
    classificadas = {p for p, u in _gets() if classe_da_rota(u) == "mascara"}
    assert classificadas == set(ROTAS), (classificadas ^ set(ROTAS))
    assert len(classificadas) >= 20  # a descida de rotas não quebrou


# ── 2. o leitor não vê nome em lugar nenhum; o dono vê (controle positivo) ──

@pytest.mark.parametrize("rota", sorted(ROTAS))
def test_leitor_nao_ve_nome_e_dono_ve(mundo, rota):
    m = mundo
    _, dono_ve, sabesp = ROTAS[rota]
    url = _url(m, rota)
    leitor = _texto(m["c"].get(url, headers=m["leitor"]))
    assert _vazou(leitor) == [], (rota, _vazou(leitor))
    dono = _texto(m["c"].get(url, headers=m["dono"]))
    if dono_ve:
        assert _vazou(dono), f"{rota}: o dono deveria ver o nome (a rota não prova nada sem isto)"
    if sabesp:
        assert VISIVEL in leitor and VISIVEL in dono, rota  # fora dos grupos pessoal/receitas, o nome fica


def test_leitor_ve_valor_data_e_categoria_do_lancamento_oculto(mundo):
    m = mundo
    itens = m["c"].get("/api/v2/lancamentos", headers=m["leitor"]).json()["itens"]
    sal = next(i for i in itens if i["categoria"] == "salarios")
    mens = next(i for i in itens if i["categoria"] == "mensalidades")
    assert (sal["valor"], sal["data"], sal["tipo"]) == ("1500.0", HOJE, "saida")
    assert (mens["valor"], mens["data"], mens["tipo"]) == ("450.0", HOJE, "entrada")
    assert sal["descricao"] == sal["mensagem"] == "salarios"  # o rótulo neutro é o nome da categoria
    agua = next(i for i in itens if i["categoria"] == "agua")
    assert agua["descricao"] == "Sabesp" and agua["mensagem"] == "Sabesp conta de água"


def test_busca_por_texto_nao_confirma_nome_ao_leitor(mundo):
    m = mundo
    leitor = m["c"].get("/api/v2/lancamentos?q=Joao", headers=m["leitor"]).json()["itens"]
    sem_q = m["c"].get("/api/v2/lancamentos", headers=m["leitor"]).json()["itens"]
    assert len(leitor) == len(sem_q) > 1  # a busca foi ignorada: acertar ou errar o nome dá a mesma resposta
    dono = m["c"].get("/api/v2/lancamentos?q=Joao", headers=m["dono"]).json()["itens"]
    assert 0 < len(dono) < len(sem_q)  # positivo: para o dono a busca segue filtrando
    for rota in ("/history/{u}/list?q=Joao", "/data/{u}?q=Joao"):
        r = m["c"].get(rota.replace("{u}", str(m["uid"])), headers=m["leitor"])
        assert _vazou(_texto(r)) == []


# ── 3. conta pessoal: nada muda para o leitor ──

def test_leitor_de_conta_pessoal_ve_tudo_como_antes(monkeypatch, user_id):
    promote_to_pro(user_id, plan="pro_max")
    db.add_launch_and_update_balance(user_id, "despesa", 80.0, "João da Silva", "Pix para João da Silva CPF 123.456.789-00", "salários")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    c, leitor = TestClient(dashboard.app), leitor_de(user_id)["bearer"]
    for url in ("/api/v2/lancamentos", f"/data/{user_id}", f"/history/{user_id}/list"):
        assert "João da Silva" in _texto(c.get(url, headers=leitor)), url
    assert leitura_mascara.categorias_ocultas(user_id, _Req(True)) == frozenset()


class _Req:  # só o que o helper lê de um Request
    def __init__(self, ro): self.state = type("S", (), {"somente_leitura": ro})(); self.headers = {}; self.cookies = {}


# ── 4. WS: a conversa real no socket ──

def test_ws_do_leitor_snapshot_get_month_get_history_sem_nome(mundo):
    m = mundo
    leitor = leitor_de(m["uid"])
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, leitor["dashboard"])
    quadros = {}
    with c.websocket_connect(f"/ws/{m['uid']}") as ws:
        quadros["snapshot"] = ws.receive_json()
        ws.send_json({"type": "refresh", "q": "Joao"})
        quadros["update"] = ws.receive_json()
        hoje = date.today()
        ws.send_json({"type": "get_month", "year": hoje.year, "month": hoje.month, "q": "Maria"})
        quadros["month_data"] = ws.receive_json()
        ws.send_json({"type": "get_history", "months": 3})
        quadros["history_data"] = ws.receive_json()
    assert set(quadros) == {q["type"] for q in quadros.values()}
    for tipo, q in quadros.items():
        assert _vazou(json.dumps(q, ensure_ascii=False)) == [], tipo
    for tipo in ("snapshot", "update", "month_data"):
        linhas = quadros[tipo]["data"]["recent_launches"]
        assert {l["categoria"] for l in linhas} >= {SALARIOS, "mensalidades", "água"}, tipo
        sal = next(l for l in linhas if l["categoria"] == SALARIOS)
        assert float(sal["valor"]) == 1500.0 and sal["alvo"] == sal["nota"] == SALARIOS, tipo
        assert any(l["alvo"] == VISIVEL for l in linhas), tipo
    # a busca é ignorada: "Joao"/"Maria" não filtram nem confirmam (todas as linhas voltam)
    assert quadros["update"]["data"]["launches_pagination"]["total"] == quadros["snapshot"]["data"]["launches_pagination"]["total"]
    # positivo: o dono, no mesmo socket, vê o nome
    d = TestClient(dashboard.app)
    d.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(m["uid"])["dashboard"])
    with d.websocket_connect(f"/ws/{m['uid']}") as ws:
        assert _vazou(json.dumps(ws.receive_json(), ensure_ascii=False))


def test_alerta_do_snapshot_nao_carrega_nome_de_gasto_fixo(mundo):
    """`alerts[].name` (gasto fixo cobrado / receita creditada) não tem categoria na linha: o servidor a põe."""
    m = mundo
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from recurring_expenses where user_id=%s and name like 'Maria%%'", (m["uid"],))
        rid = cur.fetchone()["id"]
        cur.execute("select id from recurring_incomes where user_id=%s", (m["uid"],))
        iid = cur.fetchone()["id"]
        cur.execute("insert into recurring_charges (recurring_id, user_id, amount, ym) values (%s,%s,900,'2026-11')", (rid, m["uid"]))
        cur.execute("insert into recurring_income_credits (income_id, user_id, amount, ym) values (%s,%s,450,'2026-11')", (iid, m["uid"]))
        conn.commit()
    r = m["c"].get(f"/data/{m['uid']}", headers=m["leitor"])
    tipos = {a["type"] for a in r.json()["alerts"]}
    assert {"recurring_charged", "recurring_credited"} <= tipos
    assert _vazou(_texto(r)) == []
    assert _vazou(_texto(m["c"].get(f"/data/{m['uid']}", headers=m["dono"])))  # positivo


# ── 5. controle negativo: sem o helper as rotas ficam vermelhas ──

def test_controle_negativo_helper_desligado_deixa_as_rotas_vermelhas(mundo, monkeypatch):
    m = mundo
    monkeypatch.setattr(leitura_mascara, "plano_da_conta", lambda uid: None)  # todo chamador lê o plano por aqui
    vermelhas = [rota for rota, (_, dono_ve, _) in ROTAS.items()
                 if dono_ve and _vazou(_texto(m["c"].get(_url(m, rota), headers=m["leitor"])))]
    assert set(vermelhas) == {r for r, (_, dono_ve, _) in ROTAS.items() if dono_ve}, set(ROTAS) - set(vermelhas)


# ── 6. rotas negadas (sem categoria para mascarar) e o helper em si ──

@pytest.mark.parametrize("url", ["/analytics/{u}/top-merchants", "/open-finance/{u}", "/open-finance/{u}/movements",
                                 "/open-finance/{u}/reconciliations", "/open-finance/{u}/cash-transfers",
                                 "/pockets/{u}/cx/history", "/agents/{u}/feed", "/agents/{u}/xerife/esperados"])
def test_rota_negada_ao_leitor_e_aberta_ao_dono(mundo, url):
    m = mundo
    url = url.replace("{u}", str(m["uid"]))
    r = m["c"].get(url, headers=m["leitor"])
    assert r.status_code == 403 and r.json() == {"error": "somente_leitura"}
    assert m["c"].get(url, headers=m["dono"]).status_code != 403 or url.startswith("/agents")  # agentes: o 403 do dono é o beta


def test_helper_nao_altera_a_entrada_e_so_age_no_leitor(mundo):
    uid = mundo["uid"]
    dado = {"itens": [{"categoria": "Salários", "descricao": "João", "valor": 1}, {"categoria": "água", "descricao": "Sabesp"}]}
    copia = json.loads(json.dumps(dado))
    out = leitura_mascara.mascarar_para_leitor(uid, _Req(True), dado)
    assert dado == copia  # pode vir de cache
    assert [i["descricao"] for i in out["itens"]] == ["Salários", "Sabesp"] and out["itens"][0]["valor"] == 1
    assert leitura_mascara.mascarar_para_leitor(uid, _Req(False), dado) is dado  # dono: o próprio objeto
    assert leitura_mascara.mascarar_para_leitor(uid, True, dado)["itens"][0]["descricao"] == "Salários"  # flag do /ws
