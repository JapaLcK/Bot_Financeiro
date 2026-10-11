"""PR B2: no app (com emissor) as tools que calculam por outra função o número de um card
somem, são recusadas ou aparadas; sem emissor (WhatsApp, /ai/chat) o resultado é o de hoje."""
import json
from datetime import date

import pytest

import db
from conftest import usuario_pagante
from test_api_app_sessao import completo
from _cards_apoio import mostrar, mundo  # noqa: F401  (mundo é fixture)
from _ia_stream_helpers import executar, linhas, openai_falso, rodada
from core.services.ai_chat import _context, runner, streaming
from core.services.ai_chat import tools as ai_tools
from core.services.ai_chat.tools import OCULTAS_NO_APP, SCHEMAS, SCHEMAS_APP, get_tool
from core.services.ai_chat.tools.launches import _get_spending_trend, _tendencia
from db.pockets import update_pocket_meta
from tests._fusao_of_helpers import uid_pro  # noqa: F401  (fixture)
from tests._ia_falsa_helpers import chamada, com_tools, openai_falso as falso_whatsapp, texto
from tests._pendencia_credito_helpers import diga

SETE = {"get_balance", "forecast_balance", "get_spending_trend", "get_card_limit_usage",
        "get_total_debt", "forecast_next_bill", "list_installments"}


def _nomes(schemas):
    return [s["function"]["name"] for s in schemas]


def com_emissor(f, *a):
    t = streaming.EMISSOR.set(lambda e: None)
    try:
        return f(*a)
    finally:
        streaming.EMISSOR.reset(t)


# ── o mecanismo: schemas e despacho ─────────────────────────────────────────

def test_schemas_app_sem_as_sete_e_com_show_card_e_schemas_intacto():
    assert OCULTAS_NO_APP == SETE
    assert SETE <= set(_nomes(SCHEMAS)) and "show_card" not in _nomes(SCHEMAS)
    assert not SETE & set(_nomes(SCHEMAS_APP)) and "show_card" in _nomes(SCHEMAS_APP)
    assert _nomes(SCHEMAS_APP) == [n for n in _nomes(SCHEMAS) if n not in SETE] + ["show_card"]


@pytest.mark.parametrize("nome", sorted(SETE))
def test_despacho_recusa_a_oculta_no_app_e_executa_sem_emissor(pro_user_id, nome):
    app, _ = com_emissor(runner._dispatch_tool, pro_user_id, nome, {})
    assert json.loads(app)["error"] == "indisponivel_no_app"
    fora, _ = runner._dispatch_tool(pro_user_id, nome, {})
    assert "indisponivel_no_app" not in fora


def test_controle_negativo_sem_a_recusa_o_modelo_que_imita_o_historico_recebe_dado(pro_user_id, monkeypatch):
    monkeypatch.setattr(runner, "OCULTAS_NO_APP", frozenset())
    r, _ = com_emissor(runner._dispatch_tool, pro_user_id, "get_balance", {})
    assert "error" not in json.loads(r)


def test_conversa_no_app_modelo_chama_get_balance_do_historico_e_recebe_a_recusa(pro_user_id, monkeypatch):
    openai_falso(monkeypatch, rodada(None, ("get_balance", {})), rodada("Veja o card."))
    _, eventos, _ = executar(pro_user_id, "qual meu saldo?")
    assert not [e for e in eventos if e["tipo"] == "passo"]  # a recusada não vira "Conferindo seu saldo"
    (tool,) = [json.loads(l["content"]) for l in linhas(pro_user_id) if l["role"] == "tool"]
    assert tool["error"] == "indisponivel_no_app"


def test_passo_sai_para_tool_que_executa(pro_user_id, monkeypatch):
    openai_falso(monkeypatch, rodada(None, ("list_pockets", {})), rodada("Pronto."))
    _, eventos, _ = executar(pro_user_id, "minhas caixinhas")
    assert [e["texto"] for e in eventos if e["tipo"] == "passo"] == ["Vendo suas caixinhas"]


# ── override de tendência ───────────────────────────────────────────────────

def _override(texto_user):
    tc = [{"id": "x", "function": {"name": "report_out_of_scope", "arguments": "{}"}}]
    tok = _context.CURRENT_USER_MESSAGE.set(texto_user)
    try:
        runner._maybe_override_trend(tc, 1)
    finally:
        _context.CURRENT_USER_MESSAGE.reset(tok)
    return tc[0]["function"]


def test_override_de_tendencia_vira_show_card_renda_no_app_e_get_spending_trend_fora():
    f = com_emissor(_override, "qual a tendência dos meus gastos nos últimos 4 meses?")
    assert f["name"] == "show_card" and json.loads(f["arguments"]) == {"card": "renda", "quantidade_meses": 4}
    f = _override("qual a tendência dos meus gastos nos últimos 4 meses?")
    assert f["name"] == "get_spending_trend" and json.loads(f["arguments"]) == {"months": 4}


# ── tendência: a mesma conta nos dois lugares ───────────────────────────────

@pytest.mark.parametrize("despesas,esperado", [
    ([], ("estavel", 0.0)), ([100.0], ("estavel", 0.0)), ([0.0, 50.0], ("estavel", 0.0)),
    ([100.0, 100.0, 111.0], ("subindo", 11.0)), ([100.0, 100.0, 89.0], ("descendo", -11.0)),
    ([100.0, 100.0, 110.0], ("estavel", 10.0)),
    ([100.0, 100.0, 133.33], ("subindo", 33.3)),  # 1 casa: round(var, 3) daria 33.33
])
def test_tendencia_pura(despesas, esperado):
    assert tuple(_tendencia(despesas).values()) == esperado


def test_visao_do_renda_tem_a_tendencia_do_get_spending_trend(mundo, monkeypatch):
    a, _ = mundo
    r, (bloco,), _ = mostrar(a, card="renda", quantidade_meses=4)
    linhas_ = bloco["abas"][0]["recursos"]["renda"]["evolution"]
    despesas = [m["expense"] for m in linhas_ if m["income"] or m["expense"]]  # sem os meses zerados
    assert r["abas"][0]["tendencia"] == _tendencia(despesas)
    assert "tendencia" not in bloco["abas"][0]["recursos"]["renda"]  # só na visão do modelo
    monkeypatch.setattr(db, "get_spending_trend", lambda uid, n: [
        {"year": 2026, "month": 1, "despesa": d, "receita": 0.0} for d in despesas])
    resumo = _get_spending_trend(a, {"months": 4})["summary"]
    assert {k: resumo[k] for k in ("tendencia_despesa", "variacao_pct")} == r["abas"][0]["tendencia"]


@pytest.mark.parametrize("atras", [(2, 1, 0), (3, 1, 0)], ids=["seguidos", "zero_no_meio"])
@pytest.mark.parametrize("n", [3, 6, 12])
def test_tendencia_do_card_com_poucos_meses_e_a_do_get_spending_trend(n, atras):
    """Sem monkeypatch: o `compute_evolution` preenche N meses com zero e isso diluía a média."""
    from datetime import datetime
    from utils_date import now_tz
    u = completo(usuario_pagante(plan="pro_max"))
    agora = now_tz()
    for a_, valor in zip(atras, (100.0, 100.0, 133.33)):
        m = agora.month - a_
        ano, m = (agora.year, m) if m >= 1 else (agora.year - 1, m + 12)
        db.add_launch_and_update_balance(u, "despesa", valor, "mercado", "x", categoria="alimentação",
                                         criado_em=datetime(ano, m, 1, 12, tzinfo=agora.tzinfo))
    tool = _get_spending_trend(u, {"months": n})["summary"]
    r, _, _ = mostrar(u, card="renda", quantidade_meses=n)
    assert r["abas"][0]["tendencia"] == {k: tool[k] for k in ("tendencia_despesa", "variacao_pct")}
    assert r["abas"][0]["tendencia"] == {"tendencia_despesa": "subindo", "variacao_pct": 33.3}


# ── mês inteiro × período livre ─────────────────────────────────────────────

@pytest.fixture
def gastos(pro_user_id):
    db.add_launch_and_update_balance(pro_user_id, "despesa", 50.0, "mercado", "gastei 50", categoria="alimentação")
    return pro_user_id


class _Data(date):
    hoje = date(2026, 10, 2)

    @classmethod
    def today(cls):
        return cls.hoje


@pytest.fixture
def dia_2(monkeypatch):
    """Relógio congelado no dia 2: o teste não pode depender do dia do mês em que roda."""
    from core.services.ai_chat.tools import launches
    monkeypatch.setattr(launches, "date", _Data)
    monkeypatch.setattr(launches, "today_tz", lambda: _Data.hoje)
    return _Data.hoje


FERRAMENTAS = [("get_top_categories", {}), ("get_category_spend", {"categoria": "alimentação"})]


@pytest.mark.parametrize("nome,args", FERRAMENTAS)
@pytest.mark.parametrize("periodo,recusa", [
    (("2026-09-01", "2026-09-30"), True),    # mês passado inteiro: o card categorias o mostra
    (("2026-10-01", "2026-10-02"), True),    # o corrente, do dia 1 até hoje
    (("2026-09-01", "2026-09-15"), False),   # período livre passado
    (("2026-11-01", "2026-11-30"), False),   # mês futuro: o show_card recusa, a tool segue
    (("2026-10-01", "2026-10-01"), False),   # 1 dia no mês corrente, antes de hoje
])
def test_mes_inteiro_recusado_so_se_o_card_o_mostra(gastos, dia_2, nome, args, periodo, recusa):
    ex = get_tool(nome).execute
    a = {**args, "start_date": periodo[0], "end_date": periodo[1]}
    fora, app = ex(gastos, a), com_emissor(ex, gastos, a)
    assert "error" not in fora
    if recusa:
        assert app == {"error": "mes_inteiro_no_card", "message": "use show_card categorias"}
    else:
        assert app == fora


@pytest.mark.parametrize("nome,args", FERRAMENTAS)
def test_sem_datas_no_dia_2_e_mes_corrente_inteiro(gastos, dia_2, nome, args):
    assert com_emissor(get_tool(nome).execute, gastos, args)["error"] == "mes_inteiro_no_card"
    assert "error" not in get_tool(nome).execute(gastos, args)


def test_whatsapp_por_categoria_esse_mes_igual_antes_e_depois(uid_pro, monkeypatch):
    """A conversa inteira pelo handle_incoming: a tool devolve o dado de sempre."""
    db.add_launch_and_update_balance(uid_pro, "despesa", 50.0, "mercado", "gastei 50", categoria="alimentação")
    vistas = []

    def modelo(messages):
        vistas.append([m["content"] for m in messages if m["role"] == "tool"])
        return texto("🐷 Alimentação lidera.")

    falso_whatsapp(monkeypatch, com_tools(chamada("get_top_categories", {})), modelo)
    r = diga(uid_pro, "piggy quanto gastei por categoria esse mês")
    assert "Alimentação lidera" in r
    (conteudo,) = vistas[-1]
    esperado = get_tool("get_top_categories").execute(uid_pro, {})
    assert json.loads(conteudo) == json.loads(json.dumps(esperado, default=str))
    assert "error" not in esperado and esperado["categories"]


# ── aparas ──────────────────────────────────────────────────────────────────

def test_list_pockets_apara_o_insight_so_no_app(pro_user_id):
    db.add_launch_and_update_balance(pro_user_id, "receita", 1000.0, None, "seed")
    db.create_pocket(pro_user_id, "viagem")
    update_pocket_meta(pro_user_id, db.list_pockets(pro_user_id)[0]["id"],
                       target_amount=1000.0, target_date="2999-01-01")
    ex = get_tool("list_pockets").execute
    fora, app = ex(pro_user_id, {}), com_emissor(ex, pro_user_id, {})
    p_fora, p_app = fora["pockets"][0], app["pockets"][0]
    extras = set(p_fora) - set(p_app)
    assert {"monthly_needed", "progress_pct", "remaining_to_goal", "days_to_target"} <= extras  # o do card metas
    assert p_app["target_amount"] == 1000.0 and p_app["target_date"] == "2999-01-01"  # a meta existe
    assert p_app == {k: v for k, v in p_fora.items() if k in p_app}
    assert {"id", "name", "balance", "status"} <= set(p_app)
    assert {k: v for k, v in fora.items() if k != "pockets"} == {k: v for k, v in app.items() if k != "pockets"}


def test_get_open_bill_apara_total_e_deixa_periodo_e_itens(pro_user_id):
    cid = db.create_card(pro_user_id, "Nubank", 10, 17)
    db.add_credit_purchase(pro_user_id, cid, 100, "lazer", "show", date.today())
    ex, args = get_tool("get_open_bill").execute, {"card_name": "Nubank"}
    fora, app = ex(pro_user_id, args), com_emissor(ex, pro_user_id, args)
    assert {"total", "paid_amount", "remaining"} <= set(fora) and fora["total"] == 100.0
    assert set(fora) - set(app) == {"total", "paid_amount", "remaining"}
    assert app == {k: v for k, v in fora.items() if k in app}
    assert app["items"] and app["period_start"] and app["period_end"]
