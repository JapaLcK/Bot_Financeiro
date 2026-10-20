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
BILLS = {"bills": [], "count_pending": 0, "today": "2026-10-20"}
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
    ("carteiro", [("get_bills_to_pay", {}, BILLS)], {"tipo": "contas", "hoje": "2026-10-20"}),
    ("carteiro", [("get_bills_to_pay", {"include_paid": False}, BILLS)], {"tipo": "contas", "hoje": "2026-10-20"}),
    ("carteiro", [("get_bills_to_pay", {}, {**BILLS, "today": None})], None),  # sem o dia da consulta
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


# --- compare_periods → card `comparacao_meses`: o resultado REAL da tool (sem mock), relógio congelado em 12/10/2026 ---
from datetime import datetime  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from core.services import agent_chat_card, comparacao_periodos as cp  # noqa: E402
from core.services.ai_chat.tools import launches as tool  # noqa: E402
from db import resumo_mes  # noqa: E402
from tests._patrimonio_helpers import q  # noqa: E402
from tests.conftest import usuario_pagante  # noqa: E402

execute_read_real = chat.execute_read  # o fixture `conversa` troca o `chat.execute_read`; o teste do Repórter devolve o real
from utils_date import _tz  # noqa: E402

AGORA = datetime(2026, 10, 12, 10, 0, tzinfo=_tz())
CENT = Decimal("0.01")


@pytest.fixture
def relogio(monkeypatch):
    monkeypatch.setattr(cp, "now_tz", lambda: AGORA)
    monkeypatch.setattr(tool, "now_tz", lambda: AGORA)
    monkeypatch.setattr(tool, "today_tz", lambda: AGORA.date())
    monkeypatch.setattr(agent_chat_card, "now_tz", lambda: AGORA)


@pytest.fixture
def conta(relogio):
    """Mar, ago, set e out de 2026 com dinheiro e uma compra de cartão cuja fatura (fecha dia 20) cai no próprio mês."""
    uid = usuario_pagante("pro")
    for mes_, rec, desp in ((3, 700, 70), (8, 1000, 100), (9, 1500, 250), (10, 900, 40)):
        for tipo, v, dia in (("receita", rec, 3), ("despesa", desp, 4)):
            q("insert into launches (user_id, tipo, valor, criado_em) values (%s, %s, %s, %s)", (uid, tipo, v, datetime(2026, mes_, dia, 12, tzinfo=_tz())))
    cartao = db.create_card(uid, "Nubank", closing_day=20, due_day=27)
    for mes_ in (8, 9, 10):
        db.add_credit_purchase(uid, cartao, 80, "Mercado", f"c{mes_}", date(2026, mes_, 5))
    # depois do fechamento (dia 20): a fatura cai no mês seguinte ao da compra, então "por fatura" ≠ "por data da compra"
    for dia, valor in ((20, 7), (21, 11), (25, 13)):
        db.add_credit_purchase(uid, cartao, valor, "Padaria", f"p8{dia}", date(2026, 8, dia))
    db.add_credit_purchase(uid, cartao, 17, "Padaria", "p921", date(2026, 9, 21))
    return uid


def _comparar(uid, **args):
    r = tool._compare_periods(uid, args)
    return r, card_da_resposta(uid, "reporter", [("compare_periods", args, r)])


def datas(a, b):
    """Os 4 argumentos de dois meses inteiros ("2026-08" → 01..último dia)."""
    import calendar
    dia = lambda m: (f"{m}-01", f"{m}-{calendar.monthrange(int(m[:4]), int(m[5:]))[1]:02d}")  # noqa: E731
    return dict(zip(("period_a_start", "period_a_end", "period_b_start", "period_b_end"), (*dia(a), *dia(b))))


def _compra_por_data(uid, ano, mes_):
    """O que o cálculo por data da compra somaria no mês (a regra que a tool NÃO usa)."""
    return q("select coalesce(sum(valor), 0) as s from credit_transactions where user_id=%s and extract(year from purchased_at)=%s "
             "and extract(month from purchased_at)=%s", (uid, ano, mes_))["s"]


def _contrato(meses, r):
    return {"tipo": "comparacao_meses", "meses": meses, "motivos": r["motivos"]}


@pytest.mark.parametrize("args,meses", [
    ({"mes": "2026-09"}, ["2026-08", "2026-09"]),  # mês passado
    ({"mes": "2026-10"}, ["2026-09", "2026-10"]),  # o corrente, inteiro
    (datas("2026-08", "2026-09"), ["2026-08", "2026-09"]),  # 4 datas consecutivas
    (datas("2026-03", "2026-08"), ["2026-03", "2026-08"]),  # 4 datas não consecutivas
    (datas("2026-09", "2026-10"), ["2026-09", "2026-10"]),  # corrente contra o anterior pelas datas
])
def test_comparacao_vira_card_e_os_numeros_batem_com_o_resumo_do_mes(conta, args, meses):
    r, card = _comparar(conta, **args)
    assert card == _contrato(meses, r) and r["base"]["cartao"] == "fatura"
    for lado, m in zip(("anterior", "atual"), meses):
        rota = resumo_mes.resumo_do_mes(conta, int(m[:4]), int(m[5:]), AGORA)
        assert r["receita"]["total_" + lado] == Decimal(str(rota["entrou"])).quantize(CENT)
        assert r["despesa"]["total_" + lado] == Decimal(str(rota["saiu"])).quantize(CENT)
        assert r["despesa"]["total_" + lado] > 0  # a fatura do cartão entrou: o número não é de lançamento só
    # por fatura ≠ por data da compra (as compras pós-fechamento de ago e set mudam de mês): o assert acima separa as duas regras
    for lado, m in zip(("anterior", "atual"), meses):
        if m in ("2026-08", "2026-09"):
            avulso = Decimal(q("select coalesce(sum(valor), 0) as s from launches where user_id=%s and tipo='despesa' "
                               "and to_char(criado_em, 'YYYY-MM')=%s", (conta, m))["s"])
            assert r["despesa"]["total_" + lado] != avulso + _compra_por_data(conta, int(m[:4]), int(m[5:])), m


def test_card_leva_os_motivos_da_tool_so_os_str(conta, monkeypatch):
    from db import contas_hoje
    monkeypatch.setattr(contas_hoje, "listar", lambda cur, uid: {"motivos": ["conciliacao_pendente", "banco_desatualizado"]})
    r, card = _comparar(conta, mes="2026-09")
    assert r["motivos"] == ["conciliacao_pendente", "banco_desatualizado"] and card == _contrato(["2026-08", "2026-09"], r)
    assert card["motivos"] == ["conciliacao_pendente", "banco_desatualizado"]
    sujo = {**r, "motivos": ["conciliacao_pendente", {"obj": 1}, None, "banco_desatualizado"]}  # só `str` chega ao card
    assert card_da_resposta(conta, "reporter", [("compare_periods", {}, sujo)])["motivos"] == ["conciliacao_pendente", "banco_desatualizado"]


def test_corte_do_plano_no_dia_1_do_mais_antigo_ainda_e_card(conta, monkeypatch):
    monkeypatch.setattr(cp, "history_earliest_date", lambda uid, agora: date(2026, 8, 1))
    r, card = _comparar(conta, mes="2026-09")
    assert card == _contrato(["2026-08", "2026-09"], r)


@pytest.mark.parametrize("nome,args,corte", [
    ("período livre (data da compra)", {"period_a_start": "2026-08-05", "period_a_end": "2026-08-20", "period_b_start": "2026-09-05", "period_b_end": "2026-09-20"}, None),
    ("lado com dois meses", {**datas("2026-08", "2026-09"), "period_a_start": "2026-07-01"}, None),
    ("mês igual", datas("2026-08", "2026-08"), None),
    ("anterior depois do atual", datas("2026-09", "2026-08"), None),
    ("mês futuro", datas("2026-10", "2026-11"), None),
    ("corte do plano no mais antigo", {"mes": "2026-09"}, date(2026, 8, 15)),
    ("sem base no mês anterior", {"mes": "2026-06"}, None),
    ("lado com fim ajustado (pedido)", {**datas("2026-09", "2026-10"), "period_b_end": "2026-12-31"}, None),
    ("erro da tool", {}, None),
])
def test_sem_card(conta, monkeypatch, nome, args, corte):
    if corte:
        monkeypatch.setattr(cp, "history_earliest_date", lambda uid, agora: corte)
    r, card = _comparar(conta, **args)
    assert card is None, nome
    if nome == "lado com dois meses":
        assert r["anterior"]["inicio"] == "2026-07-01" and r["base"]["cartao"] == "fatura"  # chegou ao card por fatura: só o lado o barrou
    if nome == "lado com fim ajustado (pedido)":
        assert "pedido" in r["atual"] and r["atual"]["inicio"] == "2026-10-01"  # as duas pontas são mês inteiro: só o `pedido` barra
    if nome == "mês futuro":
        assert "error" not in r  # o futuro chegou ao card: só a regra do mês corrente o barra
    if nome == "sem base no mês anterior":
        assert r["motivo"] == "sem_base"
    if nome == "corte do plano no mais antigo":
        assert r["motivo"] == "inicio_do_historico"


def test_sem_card_formas_sinteticas_e_consultas_misturadas(conta):
    r, _ = _comparar(conta, mes="2026-09")
    soma = tool._get_period_summary(conta, {"start_date": "2026-09-01", "end_date": "2026-09-30"})
    r2, _ = _comparar(conta, mes="2026-10")
    for res in ({}, None, {"base": None}, {**r, "base": {**r["base"], "cartao": "data_da_compra"}}, {**r, "despesa": None}, {**r, "atual": None}):
        assert card_da_resposta(1, "reporter", [("compare_periods", {}, res)]) is None
    c = lambda res: ("compare_periods", {}, res)  # noqa: E731
    assert card_da_resposta(1, "reporter", [c(r), ("get_period_summary", {}, soma)]) is None  # cards diferentes: o texto fala dos dois
    assert card_da_resposta(1, "reporter", [c(r), c(r2)]) is None
    assert card_da_resposta(1, "reporter", [c(r), c(r)]) == _contrato(["2026-08", "2026-09"], r)
    assert card_da_resposta(1, "xerife", [c(r)]) is None  # outro agente


@pytest.mark.parametrize("troca", [
    {"atual": {"inicio": "2026-09-01", "fim": "2026-09-15"}},  # mês incompleto
    {"atual": {"inicio": "2026-09-01", "fim": "2026-09-99"}},  # dia impossível
    {"atual": {"inicio": "2026-09-01", "fim": "2026-09-30T00:00"}},  # lixo depois do dia
    {"anterior": {"inicio": "2026-08-02", "fim": "2026-08-31"}},  # não começa no dia 1
    {"anterior": {"inicio": "2026-13-01", "fim": "2026-13-31"}},  # mês que não existe
    {"receita": None},
    {"despesa": None},
])
def test_sem_card_guardas_do_mes_inteiro(conta, troca):
    r, card = _comparar(conta, mes="2026-09")
    assert card and card_da_resposta(1, "reporter", [("compare_periods", {}, {**r, **troca})]) is None


class _Call:
    def __init__(self, nome, args, n):
        self.id = f"call_{n}"
        self.function = SimpleNamespace(name=nome, arguments=json.dumps(args))

    def model_dump(self):
        return {"id": self.id, "type": "function", "function": {"name": self.function.name, "arguments": self.function.arguments}}


def test_conversa_do_reporter_compara_dois_meses_e_leva_o_card(conversa, monkeypatch, conta):
    monkeypatch.setattr(plan_service, "plan_gate_ok", lambda uid, feature: True)  # o fixture `gate` nega financial_comparison; aqui o plano é o da conta
    r = tool._compare_periods(conta, {"mes": "2026-09"})
    monkeypatch.setattr(chat, "execute_read", execute_read_real)  # a conversa passa pelo `execute_read → tool.execute` de verdade
    monkeypatch.setattr(chat, "_route", lambda *a: own_route(needs_data=True))
    conversa([response(calls=[_Call("compare_periods", {"mes": "2026-09"}, 1)]), response("Setembro foi diferente."), OK])
    assert chat.chat(conta, "reporter", "Compare agosto com setembro")["card"] == _contrato(["2026-08", "2026-09"], r)
    # as duas consultas na mesma resposta: o texto fala de mais que um card mostra
    conversa([response(calls=[_Call("compare_periods", {"mes": "2026-09"}, 1), _Call("get_period_summary", {}, 2)]), response("Setembro foi diferente."), OK])
    assert chat.chat(conta, "reporter", "Compare agosto com setembro e resuma setembro")["card"] is None
