"""`/api/v2/contas-a-pagar` e `/api/v2/caixinhas` (os dados dos cards do Carteiro e do Banqueiro) pelo monólito real:
contrato, dinheiro em texto, a MESMA regra da consulta do agente, leitura sem sync nem juros, gate e isolamento. Banco real."""
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import db
import db.bills as B
import db.pockets
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de as sessao
from conftest import em_carencia, promote_to_pro
from core.services import agent_chat
from core.services.ai_chat.tools.bills import _get_bills_to_pay
from db import ensure_user
from db.connection import get_conn
from utils_date import today_tz

CONTAS, CAIXINHAS = "/api/v2/contas-a-pagar", "/api/v2/caixinhas"


@pytest.fixture
def a_e_b(monkeypatch, user_id):
    a = user_id
    b = int(uuid.uuid4().int % 10_000_000_000)  # o autouse do conftest apaga depois
    ensure_user(b)
    promote_to_pro(a)
    promote_to_pro(b)
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{b}")
    return a, b


def _get(rota, quem, **q):  # `quem` e não `uid`: o teste de isolamento manda `?uid=` na query
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao(quem)["dashboard"])
    return client.get(rota, params=q)


def _caixinha(uid, nome, saldo, meta=None):
    _, pid, nome = db.create_pocket(uid, nome)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pockets set balance=%s where id=%s and user_id=%s", (saldo, pid, uid))
        conn.commit()
    if meta is not None:
        db.pockets.update_pocket_meta(uid, pid, target_amount=meta)
    return pid


def _semeia_contas(uid):
    hoje = today_tz()
    luz = B.create_boleto(uid, "Luz", 120.5, hoje - timedelta(days=2), category="moradia")  # vencida
    B.create_boleto(uid, "Internet", 99.9, hoje + timedelta(days=3))
    paga = B.create_boleto(uid, "Paga", 777, hoje + timedelta(days=1))
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update bill_instances set status='paid' where id=%s and user_id=%s", (paga["id"], uid))
        conn.commit()
    return luz


# ── contas a pagar ───────────────────────────────────────────────────────────

def test_contas_contrato_dinheiro_em_texto_e_so_pendentes(a_e_b):
    a, _ = a_e_b
    luz = _semeia_contas(a)
    r = _get(CONTAS, a)
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) == {"hoje", "pendentes", "vencidas", "total_estimado", "tem_variavel", "contas"}
    assert (j["pendentes"], j["vencidas"], j["total_estimado"], j["tem_variavel"]) == (2, 1, "220.40", False)
    assert [c["nome"] for c in j["contas"]] == ["Luz", "Internet"]  # a paga fica de fora; a vencida vem antes
    assert j["contas"][0] == {"id": luz["id"], "nome": "Luz", "categoria": "moradia", "vence_em": (today_tz() - timedelta(days=2)).isoformat(),
                              "dias": -2, "valor": "120.50", "variavel": False, "vencida": True}
    assert (j["contas"][1]["valor"], j["contas"][1]["dias"], j["contas"][1]["vencida"]) == ("99.90", 3, False)
    assert "provider" not in r.text and "raw" not in r.text


def test_contas_mesmo_numero_da_consulta_do_carteiro(a_e_b):
    a, _ = a_e_b
    _semeia_contas(a)
    lido = agent_chat.execute_read(a, "carteiro", "get_bills_to_pay", {})
    j = _get(CONTAS, a).json()
    assert j["total_estimado"] == f"{lido['total_estimated_value']:.2f}"
    assert (j["pendentes"], j["vencidas"], j["hoje"]) == (lido["count_pending"], lido["overdue_count"], lido["today"])
    assert [(c["id"], c["dias"]) for c in j["contas"]] == [(b["id"], b["days_until"]) for b in lido["bills"]]


def test_contas_ler_nao_gera_instancia_do_proximo_ciclo(a_e_b, monkeypatch):
    a, _ = a_e_b
    chamadas = []
    monkeypatch.setattr("core.services.recurring_charger.sync_manual_bills_once", lambda *args: chamadas.append(args))
    assert _get(CONTAS, a).status_code == 200
    assert chamadas == []
    _get_bills_to_pay(a, {})  # controle negativo: o padrão da consulta (sync ligado) chama o sync, então o recorder mede
    assert len(chamadas) == 1


def test_contas_isolamento_entre_usuarios(a_e_b):
    a, b = a_e_b
    _semeia_contas(b)
    for r in (_get(CONTAS, a), _get(CONTAS, a, uid=b), _get(CONTAS, a, user_id=b)):
        assert r.status_code == 200 and r.json()["contas"] == [] and r.json()["total_estimado"] == "0.00", r.text
    assert len(_get(CONTAS, b).json()["contas"]) == 2  # controle positivo: os dados de B existem


def test_contas_gate_da_agenda_de_boletos(user_id, monkeypatch):
    em_carencia(user_id)  # o Grátis que entra no painel: sem `recurring_expenses`
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    r = _get(CONTAS, user_id)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "pro_required"), r.text
    promote_to_pro(user_id, plan="essencial")  # controle positivo: o Essencial entra
    assert _get(CONTAS, user_id).status_code == 200


def test_contas_sem_sessao_401():
    assert TestClient(dashboard.app).get(CONTAS).status_code == 401


# ── caixinhas ────────────────────────────────────────────────────────────────

def test_caixinhas_contrato_dinheiro_em_texto_e_ordem(a_e_b):
    a, _ = a_e_b
    viagem = _caixinha(a, "Viagem", 250, meta=1000)
    reserva = _caixinha(a, "Reserva", 1500.5)
    batida = _caixinha(a, "Moto", 300, meta=300)
    r = _get(CAIXINHAS, a)
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) == {"total", "caixinhas"} and j["total"] == "2050.50"
    assert [(c["id"], c["saldo"]) for c in j["caixinhas"]] == [(reserva, "1500.50"), (batida, "300.00"), (viagem, "250.00")]
    assert j["caixinhas"][0] == {"id": reserva, "nome": "Reserva", "saldo": "1500.50", "meta": None, "progresso_pct": None, "atingida": None}
    assert j["caixinhas"][2]["meta"] == "1000.00" and j["caixinhas"][2]["progresso_pct"] == "25.00" and j["caixinhas"][2]["atingida"] is False
    assert (j["caixinhas"][1]["progresso_pct"], j["caixinhas"][1]["atingida"]) == ("100.00", True)
    assert "provider" not in r.text and "raw" not in r.text


def test_caixinhas_mesmo_numero_da_consulta_do_banqueiro(a_e_b):
    a, _ = a_e_b
    _caixinha(a, "Viagem", 250.3, meta=1000)
    _caixinha(a, "Reserva", 1500.5)
    lido = agent_chat.execute_read(a, "cofre", "list_pockets", {})["pockets"]
    j = _get(CAIXINHAS, a).json()
    assert [(c["id"], c["saldo"], c["progresso_pct"]) for c in j["caixinhas"]] == [(p["id"], f"{p['balance']:.2f}", None if p["progress_pct"] is None else f"{p['progress_pct']:.2f}") for p in lido]
    assert j["total"] == f"{sum(p['balance'] for p in lido):.2f}"


def test_caixinhas_total_soma_os_saldos_crus_como_o_whatsapp(a_e_b):
    """pockets.balance é numeric sem escala: juros legados deixam fração de centavo. O total é a soma crua arredondada uma vez,
    a do "Total nas caixinhas" do WhatsApp, não a soma das linhas já arredondadas (Codex no #1200)."""
    from core.handlers.pockets import list_pockets as whatsapp
    a, _ = a_e_b
    _caixinha(a, "A", "0.006")
    _caixinha(a, "B", "0.006")
    j = _get(CAIXINHAS, a).json()
    assert [c["saldo"] for c in j["caixinhas"]] == ["0.01", "0.01"]
    assert j["total"] == "0.01"  # 0,012; somar as linhas daria 0,02
    assert "Total nas caixinhas: **R$ 0,01**" in whatsapp(a)


def test_caixinhas_ler_nao_aplica_juros(a_e_b, monkeypatch):
    a, _ = a_e_b
    _caixinha(a, "Reserva", 100)
    chamadas = []
    monkeypatch.setattr(db.pockets, "accrue_all_pockets", lambda *args, **kw: chamadas.append(args) or [])
    assert _get(CAIXINHAS, a).status_code == 200
    assert chamadas == []
    db.list_pockets(a)  # controle negativo: o padrão (accrue ligado) chama o accrue, então o recorder mede
    assert len(chamadas) == 1


def test_caixinhas_isolamento_entre_usuarios(a_e_b):
    a, b = a_e_b
    _caixinha(b, "Do B", 999)
    for r in (_get(CAIXINHAS, a), _get(CAIXINHAS, a, uid=b), _get(CAIXINHAS, a, user_id=b)):
        assert r.status_code == 200 and r.json() == {"total": "0.00", "caixinhas": []}, r.text
    assert _get(CAIXINHAS, b).json()["total"] == "999.00"  # controle positivo


def test_caixinhas_sem_gate_de_plano_e_sem_sessao_401(user_id, monkeypatch):
    em_carencia(user_id)
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    assert _get(CAIXINHAS, user_id).status_code == 200  # o Grátis tem caixinha
    assert TestClient(dashboard.app).get(CAIXINHAS).status_code == 401
