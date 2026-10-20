"""Card abaixo da resposta do agente (core/services/agent_chat_card.py) e a regra dos números:
o snapshot do agente e o card leem `db.investido.ler`, a mesma do /api/v2/investido."""
import json
from pathlib import Path
from decimal import Decimal

import pytest

import db
import db.ai_chat as quota
from core.services import agent_chat as chat, plan_service
from core.services.agent_chat_card import card_da_resposta
from core.services.agent_chat_data import _snapshot, total_renda_fixa
from tests._patrimonio_helpers import conexao, posicao
from tests.test_agent_chat_conversation import ScriptedClient, SnapshotCall, own_route, response

SNAP = ("consultar_dados_do_agente", {}, {})


@pytest.fixture
def gate(monkeypatch):
    estado = {"ok": True}
    monkeypatch.setattr(plan_service, "plan_gate_ok", lambda uid, feature: estado["ok"] if feature == "investments" else False)
    return estado


@pytest.mark.parametrize("kind,consultas,esperado", [
    ("faria_limer", [SNAP], {"tipo": "investido"}),
    ("barao", [SNAP], {"tipo": "renda_fixa"}),
    ("faria_limer", [], None),
    ("barao", [("get_balance", {}, {})], None),
    ("barao", [("get_balance", {}, {}), SNAP], {"tipo": "renda_fixa"}),
    ("reporter", [SNAP], None),
    ("xerife", [SNAP], None),
])
def test_decisao_do_card(gate, kind, consultas, esperado):
    assert card_da_resposta(1, kind, consultas) == esperado


def test_sem_plano_de_investimentos_nao_tem_card(gate):
    gate["ok"] = False
    assert card_da_resposta(1, "faria_limer", [SNAP]) is None


# --- ponta a ponta: chat() com o cliente roteirizado, sem mockar _answer ---
@pytest.fixture
def conversa(monkeypatch, gate):
    import openai
    charged = []
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(chat, "require_access", lambda *a: None)
    monkeypatch.setattr(chat, "access_state", lambda *a: "ready")
    monkeypatch.setattr(plan_service, "ai_monthly_limit_for", lambda uid: 100)
    monkeypatch.setattr(db, "ai_get_usage_this_month", lambda uid: len(charged))
    monkeypatch.setattr(quota, "try_consume_usage", lambda *a: charged.append(a) or len(charged))
    monkeypatch.setattr(chat, "execute_read", lambda *a: {"investido": {"total_investido": "100.00"}})
    monkeypatch.setattr(chat, "_route", lambda *a: own_route(needs_data=True))

    def roteiro(respostas):
        client = ScriptedClient(respostas)
        monkeypatch.setattr(openai, "OpenAI", lambda **kw: client)
    return roteiro


OK = response('{"valid":true,"reason":"ok"}')


@pytest.mark.parametrize("kind,tipo", [("faria_limer", "investido"), ("barao", "renda_fixa")])
def test_resposta_com_dados_do_tema_leva_o_card(conversa, kind, tipo):
    conversa([response(calls=[SnapshotCall()]), response("Você tem R$ 100."), OK])
    assert chat.chat(42, kind, "Quanto tenho?")["card"] == {"tipo": tipo}


def test_resposta_conceitual_nao_leva_card(conversa, monkeypatch):
    monkeypatch.setattr(chat, "_route", lambda *a: own_route())
    conversa([response("P/L é preço sobre lucro."), OK])
    assert chat.chat(42, "faria_limer", "O que é P/L?")["card"] is None


def test_gate_que_levanta_nao_derruba_a_resposta(conversa, monkeypatch):
    def quebra(uid, feature):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(plan_service, "plan_gate_ok", quebra)
    conversa([response(calls=[SnapshotCall()]), response("Você tem R$ 100."), OK])
    r = chat.chat(42, "faria_limer", "Quanto tenho?")
    assert (r["reply"], r["card"]) == ("Você tem R$ 100.", None)


# --- os números: snapshot == regra do painel ---
def test_snapshot_do_faria_limer_traz_o_total_do_painel(user_id):
    from db import investido
    c = conexao(user_id, f"item-{user_id}", banco="Nubank")
    posicao(c, "a", "1000.00", tipo="EQUITY", subtipo="STOCK")
    posicao(c, "b", "2000.00", tipo="FIXED_INCOME", subtipo="CDB")
    posicao(c, "c", "4000.00", tipo="MUTUAL_FUND")
    snap = _snapshot(user_id, "faria_limer")
    total = investido.ler(user_id)["total"]
    assert snap["investido"]["total_investido"] == str(total) == "7000.00"
    # discriminante: os recortes antigos NÃO dão o total (fundo fica fora dos dois)
    recortes = Decimal(str(snap["resumo_brl"]["market_value"])) + Decimal(str(snap["renda_fixa_brl"]["balance"]))
    assert recortes != total
    assert {p["tipo"]: p["valor"] for p in snap["investido"]["por_tipo"]} == {
        "fundos": "4000.00", "renda_fixa": "2000.00", "acoes": "1000.00"}
    assert "investido" in snap["nota"] and "não os some como total" in snap["nota"]


def test_posicao_de_outro_usuario_nao_entra(user_id):
    from tests.conftest import ensure_user, _cleanup_user
    outro = user_id + 7
    ensure_user(outro)
    try:
        posicao(conexao(user_id, f"i-{user_id}"), "a", "100.00")
        posicao(conexao(outro, f"i-{outro}"), "b", "999999.00")
        for kind in ("faria_limer", "barao"):
            assert _snapshot(user_id, kind)["investido"]["total_investido"] == "100.00"
    finally:
        _cleanup_user(outro)


def test_barao_total_renda_fixa_soma_renda_fixa_e_tesouro_do_banco(user_id):
    c = conexao(user_id, f"item-{user_id}")
    posicao(c, "a", "100.10", tipo="FIXED_INCOME", subtipo="CDB")
    posicao(c, "b", "50.05", tipo="FIXED_INCOME", subtipo="TREASURY")
    posicao(c, "c", "10.00", tipo="EQUITY", subtipo="STOCK")
    snap = _snapshot(user_id, "barao")
    assert snap["total_renda_fixa"] == "150.15" and snap["investido"]["total_investido"] == "160.15"
    assert "total_renda_fixa" in snap["nota"]


# A MESMA tabela está em tests/frontend/dashboard_v2_chat_agentes_card.test.mjs (§0.7): o servidor e o card têm de concordar.
def _inv(total, partes):
    return {"total_investido": total, "por_tipo": [{"tipo": t, "valor": v} for t, v in partes]}


# A tabela é a mesma do teste do card (tests/frontend/dashboard_v2_chat_agentes_card.test.mjs): a regra existe em Python e em TS (§0.7).
with open(Path(__file__).parent / "frontend" / "total_renda_fixa.json", encoding="utf-8") as _f:
    TABELA = [(_inv(c["total"], c["partes"]), c["esperado"]) for c in json.load(_f)]


@pytest.mark.parametrize("investido,esperado", TABELA)
def test_total_renda_fixa_tabela(investido, esperado):
    assert total_renda_fixa(investido) == esperado


@pytest.mark.parametrize("kind", ["faria_limer", "barao"])
def test_snapshot_degrada_se_a_leitura_do_total_falha(monkeypatch, kind):
    import db.investido
    def quebra(uid):
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db.investido, "ler", quebra)
    for nome in ("list_rv_positions", "list_investments", "list_of_fixed_income"):
        monkeypatch.setattr(db, nome, lambda *a, **k: [])
    monkeypatch.setattr(db, "rv_portfolio_summary", lambda *a, **k: {})
    monkeypatch.setattr(db, "of_fixed_income_summary", lambda *a, **k: {})
    snap = _snapshot(1, kind)
    assert snap["investido"] == {"indisponivel": True}
    assert snap.get("total_renda_fixa") is None
    assert "indisponivel" in snap["nota"]
