"""Card abaixo da resposta do agente (core/services/agent_chat_card.py) e a regra dos números:
o snapshot do agente e o card leem `db.investido.ler`, a mesma do /api/v2/investido."""
import json
from datetime import date
import re
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
BUDGETS = {"budgets": [{"categoria": "mercado"}], "count": 1, "mes": "2026-10"}
BILLS = {"bills": [], "count_pending": 0}
POCKETS = {"pockets": []}


def mes(ini, fim, por="fatura", motivos=()):
    """O resultado de get_period_summary (core/services/ai_chat/tools/launches.py::_resumo_periodo) que o card lê."""
    return {"start_date": ini, "end_date": fim, "cartao_por": por, "motivos": list(motivos), "receita": 1.0, "despesa": 1.0}


@pytest.fixture
def gate(monkeypatch):
    estado = {"ok": True}
    # só as features que o card consulta; `consultadas` prova que resumo_mes e orcamentos (rotas sem gate) não consultam nenhuma
    estado["consultadas"] = []
    monkeypatch.setattr(plan_service, "plan_gate_ok",
                        lambda uid, feature: estado["consultadas"].append(feature) or (estado["ok"] if feature in ("investments", "subscriptions", "recurring_expenses") else False))
    return estado


@pytest.mark.parametrize("kind,consultas,esperado", [
    ("faria_limer", [SNAP], {"tipo": "investido"}),
    ("barao", [SNAP], {"tipo": "renda_fixa"}),
    ("faria_limer", [], None),
    ("barao", [("get_balance", {}, {})], None),
    ("barao", [("get_balance", {}, {}), SNAP], {"tipo": "renda_fixa"}),
    ("reporter", [SNAP], None),
    ("xerife", [SNAP], None),
    ("detetive", [SNAP], {"tipo": "assinaturas"}),
    ("detetive", [("get_balance", {}, {})], None),
    ("faria_limer", [("get_budget_status", {}, BUDGETS)], None),
    ("xerife", [("get_budget_status", {}, BUDGETS)], {"tipo": "orcamentos", "mes": "2026-10"}),
    ("xerife", [("get_budget_status", {}, {**BUDGETS, "mes": None})], None),  # sem o mês da consulta
    ("xerife", [("get_budget_status", {}, {**BUDGETS, "mes": "2026-13"})], None),
    ("xerife", [("get_budget_status", {}, {"budgets": [], "count": 0})], None),  # nada a mostrar
    ("xerife", [("get_budget_status", {"categoria": "mercado"}, {"found": True, "categoria": "mercado"})], None),  # o texto fala de uma só
    ("carteiro", [("get_bills_to_pay", {}, BILLS)], {"tipo": "contas"}),
    ("carteiro", [("get_bills_to_pay", {"include_paid": False}, BILLS)], {"tipo": "contas"}),
    ("carteiro", [("get_bills_to_pay", {"include_paid": True}, BILLS)], None),  # o texto inclui as pagas, a rota só as pendentes
    ("carteiro", [("get_bills_to_pay", {}, {"error": "x"})], None),
    ("carteiro", [SNAP], None),
    ("reporter", [("get_bills_to_pay", {}, BILLS)], None),  # fora do tema do agente
    ("cofre", [("list_pockets", {}, POCKETS)], {"tipo": "caixinhas"}),
    ("cofre", [("list_pockets", {}, {"error": "x"})], None),
    ("cofre", [("get_bills_to_pay", {}, BILLS)], None),
    ("carteiro", [("list_pockets", {}, POCKETS)], None),
    ("xerife", [("get_period_summary", {}, mes("2026-10-01", "2026-10-31"))], None),
    ("reporter", [("get_period_summary", {}, mes("2026-10-01", "2026-10-31"))], {"tipo": "resumo_mes", "mes": "2026-10", "motivos": []}),
    ("reporter", [("get_period_summary", {}, mes("2024-02-01", "2024-02-29"))], {"tipo": "resumo_mes", "mes": "2024-02", "motivos": []}),
    ("reporter", [("get_period_summary", {}, mes("2026-03-01", "2026-08-31"))], None),  # vários meses
    ("reporter", [("get_period_summary", {}, mes("2026-10-05", "2026-10-12", por="data_da_compra"))], None),  # período livre
    ("reporter", [("get_period_summary", {}, mes("2099-01-01", "2099-01-31", motivos=["mes_futuro"]))], None),  # a rota dá 422
    ("reporter", [("get_period_summary", {}, {"saldo_periodo": 1})], None),
    ("reporter", [("get_period_summary", {}, mes("2026-09-01", "2026-09-30")), ("get_period_summary", {}, mes("2026-10-01", "2026-10-31"))], None),
    ("reporter", [("get_period_summary", {}, mes("2026-10-01", "2026-10-31")), ("get_period_summary", {}, mes("2026-10-01", "2026-10-31"))], {"tipo": "resumo_mes", "mes": "2026-10", "motivos": []}),
    # outra consulta de período que o card não mostra: o texto fala dela também (Tester no PR 3b)
    ("reporter", [("get_period_summary", {}, mes("2026-10-01", "2026-10-31")), ("compare_periods", {}, {})], None),
    ("reporter", [("get_period_summary", {}, mes("2026-10-01", "2026-10-31")), ("get_spending_trend", {}, {})], None),
    ("xerife", [("get_budget_status", {}, BUDGETS), ("get_category_spend", {}, {})], None),
    ("reporter", [("get_balance", {}, {}), ("get_period_summary", {}, mes("2026-10-01", "2026-10-31"))], {"tipo": "resumo_mes", "mes": "2026-10", "motivos": []}),  # saldo não tem período
    ("reporter", [("get_period_summary", {}, mes("0999-01-01", "0999-01-31"))], None),  # o padrão da rota não aceita
])
def test_decisao_do_card(gate, kind, consultas, esperado):
    assert card_da_resposta(1, kind, consultas) == esperado


def test_sem_plano_de_investimentos_nao_tem_card(gate):
    gate["ok"] = False
    assert card_da_resposta(1, "faria_limer", [SNAP]) is None


def test_gate_de_cada_tipo_e_o_da_rota_v2(gate):
    # assinaturas = `subscriptions` (api/v2/assinaturas.py); resumo-do-mes e orcamentos-categoria não têm gate: o plano não corta o card
    gate["ok"] = False
    assert card_da_resposta(1, "detetive", [SNAP]) is None and gate["consultadas"] == ["subscriptions"]
    gate["consultadas"].clear()
    assert card_da_resposta(1, "reporter", [("get_period_summary", {}, mes("2026-10-01", "2026-10-31"))]) == {"tipo": "resumo_mes", "mes": "2026-10", "motivos": []}
    assert card_da_resposta(1, "xerife", [("get_budget_status", {}, BUDGETS)]) == {"tipo": "orcamentos", "mes": "2026-10"}
    assert gate["consultadas"] == []
    # contas = `recurring_expenses` (api/v2/contas_a_pagar.py, o gate da agenda de boletos); caixinhas não tem gate
    gate["consultadas"].clear()
    assert card_da_resposta(1, "carteiro", [("get_bills_to_pay", {}, BILLS)]) is None and gate["consultadas"] == ["recurring_expenses"]
    gate["consultadas"].clear()
    assert card_da_resposta(1, "cofre", [("list_pockets", {}, POCKETS)]) == {"tipo": "caixinhas"} and gate["consultadas"] == []


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


# --- o card lê o resultado REAL do get_period_summary (sem mock): mês inteiro vira card; o resto, não ---
@pytest.mark.parametrize("args,esperado", [
    ({"start_date": "2025-03-01", "end_date": "2025-03-31"}, {"tipo": "resumo_mes", "mes": "2025-03"}),
    ({"start_date": "2025-03-01", "end_date": "2025-03-15"}, None),  # mês passado cortado no meio = período livre
    ({"start_date": "2025-03-01", "end_date": "2025-05-31"}, None),
    ({"start_date": "2025-03-10", "end_date": "2025-03-12"}, None),
])
def test_resultado_real_do_get_period_summary(gate, user_id, args, esperado):
    from core.services.ai_chat.tools.launches import _get_period_summary
    r = _get_period_summary(user_id, args)
    # os avisos do card são os do resultado (a janela do plano da conta de teste decide se março está cortado)
    assert card_da_resposta(user_id, "reporter", [("get_period_summary", args, r)]) == (esperado and esperado | {"motivos": r["motivos"]})


@pytest.mark.parametrize("corte,no_card", [
    (date(2025, 2, 15), False),  # cortou só o mês anterior: março está inteiro, o agente não avisa, o card também não
    (date(2025, 3, 10), True),  # cortou o mês pedido: os dois avisam
])
def test_card_do_resumo_leva_os_avisos_do_agente_nao_os_da_rota(gate, monkeypatch, user_id, corte, no_card):
    from core.services.ai_chat.tools.launches import _get_period_summary
    from db import resumo_mes
    monkeypatch.setattr(plan_service, "history_earliest_date", lambda uid, agora: corte)
    texto = _get_period_summary(user_id, {"start_date": "2025-03-01", "end_date": "2025-03-31"})
    card = card_da_resposta(user_id, "reporter", [("get_period_summary", {}, texto)])
    assert ("inicio_do_historico" in texto["motivos"]) is no_card
    assert ("inicio_do_historico" in card["motivos"]) is no_card
    assert "inicio_do_historico" in resumo_mes.resumo_do_mes(user_id, 2025, 3)["motivos"]  # a rota avisa nos dois: o card não pode lê-la


def test_padrao_do_mes_no_cliente_e_o_da_rota():
    """§0.7: o TS não importa Python; a cópia de lib/agentes.ts tem de ser a mesma regex."""
    from api.v2.resumo_mes import PADRAO_MES
    ts = (Path(__file__).parent.parent / "webapp/src/dashboard/lib/agentes.ts").read_text(encoding="utf-8")
    assert re.search(r"export const MES = /(.+?)/;", ts).group(1) == PADRAO_MES


def test_consulta_de_orcamentos_carimba_o_mes_da_consulta(user_id, monkeypatch):
    """O card mostra o mês da consulta: execute_read carimba o mês em que o get_budget_status rodou (Codex no #1199)."""
    from datetime import date as _date
    class Hoje(_date):
        @classmethod
        def today(cls):
            return _date(2026, 1, 31)
    monkeypatch.setattr(chat, "date", Hoje)
    r = chat.execute_read(user_id, "xerife", "get_budget_status", {})
    assert r["mes"] == "2026-01"
