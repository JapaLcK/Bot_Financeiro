"""PR 3A: o card sai junto da própria tool (`get_largest_expenses` -> `maiores_gastos`, `forecast_month_end` ->
`fim_do_mes`), sem o `show_card`, e só para o app que declarou saber mostrá-lo (`cards` no corpo do stream).
Sem emissor (WhatsApp, `/ai/chat`) e com o build antigo (sem `cards`) o resultado é o de sempre, byte a byte.
O pytest NÃO fala com o modelo: aqui se prova o que o código empurra; se o modelo segue, só o staging diz."""
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

import db
from _cards_apoio import mundo  # noqa: F401  (fixture)
import _ia_stream_helpers
from _ia_stream_helpers import _chunk, conta, eventos_sse, executar, linhas, openai_falso, rodada
from conftest import usuario_pagante
from core.services import ai_guard
from core.services.ai_chat import cards, runner, streaming
from core.services.ai_chat._context import CARDS_DO_APP, CARDS_DO_TURNO
from core.services.ai_chat.system_prompt import prompt_do_usuario
from core.services.ai_chat.tools import OCULTAS_NO_APP, SCHEMAS_APP, get_tool
from core.services.ai_chat.tools.metas import schemas_com_metas
from test_ai_chat_periodo import _conforme
from test_api_app_sessao import completo
from test_senha_obrigatoria import H

DO_APP = cards.DA_TOOL
MAIORES = ("get_largest_expenses", {})
FIM = ("forecast_month_end", {})
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def uid(pro_user_id):
    completo(pro_user_id)
    db.add_launch_and_update_balance(pro_user_id, "receita", 1000, None, "salário")
    for valor, alvo, cat in ((300, "aluguel", "moradia"), (120, "mercado", "alimentação"), (80, "uber", "transporte")):
        db.add_launch_and_update_balance(pro_user_id, "despesa", valor, alvo, alvo, categoria=cat)
    return pro_user_id


def _tool_msgs(uid):
    return [json.loads(l["content"]) for l in linhas(uid) if l["role"] == "tool"]


def _turno(monkeypatch, uid, tool, cards_do_app=DO_APP):
    openai_falso(monkeypatch, rodada(None, tool), rodada("Comentário."))
    r, eventos, _ = executar(uid, "pergunta", cards=cards_do_app)
    return r, [e["bloco"] for e in eventos if e["tipo"] == "bloco"], eventos


# ── 1. o app declarou o card: bloco antes do fim, e o modelo recebe a visão resumida ────────────

def test_maiores_gastos_emite_o_bloco_e_o_modelo_recebe_o_resumo(monkeypatch, uid):
    _, blocos, _ = _turno(monkeypatch, uid, MAIORES)
    (bloco,) = blocos
    assert bloco["card"] == "maiores_gastos" and bloco["abas"][0]["mes"] is None
    lista = bloco["abas"][0]["recursos"]["maiores_gastos"]["expenses"]
    assert [x["valor"] for x in lista] == [300.0, 120.0, 80.0]
    (visao,) = _tool_msgs(uid)
    assert visao["card_na_tela"] is True and "expenses" not in visao  # o modelo não recebe a lista que a tela mostra
    assert visao["count"] == 3 and visao["total_dos_itens"] == 500.0 and "valor_total" not in visao
    assert visao["maior"]["valor"] == 300.0 and visao["categorias"] == ["moradia", "alimentação", "transporte"]


def test_fim_do_mes_emite_o_bloco_sem_a_nota_e_o_modelo_recebe_o_resultado_inteiro(monkeypatch, uid):
    _, blocos, _ = _turno(monkeypatch, uid, FIM)
    (bloco,) = blocos
    rec = bloco["abas"][0]["recursos"]["fim_do_mes"]
    assert bloco["card"] == "fim_do_mes" and "nota_metodologia" not in rec and "cartao_incluido" not in rec
    (visao,) = _tool_msgs(uid)
    assert visao["card_na_tela"] is True and "nota_metodologia" in visao
    assert {k: v for k, v in visao.items() if k not in ("card_na_tela", "nota_metodologia", "cartao_incluido")} == rec
    assert rec["dias_corridos"] >= 1 and rec["dias_no_mes"] >= 28  # o app desenha "dia 17 de 31"


def test_endpoint_manda_o_bloco_antes_do_fim_e_aceita_cards_no_corpo(monkeypatch):
    uid, c = conta()
    completo(uid)
    db.add_launch_and_update_balance(uid, "despesa", 90, "mercado", "x", categoria="alimentação")
    openai_falso(monkeypatch, rodada(None, MAIORES), rodada("Comentário."))
    ev = eventos_sse(c.post("/ai/chat/stream", json={"message": "meus maiores gastos", "cards": list(DO_APP)}, headers=H))
    tipos = [e["tipo"] for e in ev]
    assert tipos.count("bloco") == 1 and tipos.index("bloco") < tipos.index("fim") == len(tipos) - 1


@pytest.mark.parametrize("cards_do_corpo", [["x" * 41], ["a"] * 51, "maiores_gastos"])
def test_endpoint_recusa_cards_malformado(cards_do_corpo):
    _, c = conta()
    r = c.post("/ai/chat/stream", json={"message": "oi", "cards": cards_do_corpo}, headers=H)
    assert r.status_code == 422


# ── 3. sem emissor (WhatsApp, /ai/chat): o resultado é o de sempre ───────────────────────────────

CHAVES_DE_SEMPRE = {
    "get_largest_expenses": ["start_date", "end_date", "categoria", "expenses", "count", "cartao_por"],
    "forecast_month_end": ["mes", "hoje", "dias_corridos", "dias_no_mes", "receita", "despesa", "saldo_ate_agora",
                           "despesa_cartao", "despesa_agendada", "despesa_no_ritmo", "despesa_projetada",
                           "saldo_projetado", "vai_fechar_negativo", "cartao_incluido", "cartao_por", "motivos",
                           "nota_metodologia"],
}


@pytest.mark.parametrize("tool", [MAIORES, FIM], ids=["maiores", "fim"])
def test_sem_emissor_nada_sai_e_o_resultado_e_o_de_sempre(monkeypatch, uid, tool):
    CARDS_DO_APP.set(DO_APP)  # mesmo com a capacidade no contexto: sem emissor não há app
    try:
        openai_falso(monkeypatch, rodada(None, tool), rodada("Comentário."))
        assert runner.chat(uid, "pergunta", monthly_limit=100)
    finally:
        CARDS_DO_APP.set(())
    (visao,) = _tool_msgs(uid)
    assert list(visao) == CHAVES_DE_SEMPRE[tool[0]]  # nenhuma chave nova, na mesma ordem
    assert get_tool(tool[0]).execute(uid, {}) == visao


# ── 4. o build antigo não manda `cards`: o dado vai inteiro ──────────────────────────────────────

@pytest.mark.parametrize("tool,outro", [(MAIORES, "fim_do_mes"), (FIM, "maiores_gastos")], ids=["maiores", "fim"])
@pytest.mark.parametrize("declarado", ["nada", "so_o_outro"])
def test_app_que_nao_declarou_o_card_recebe_o_dado_sem_bloco(monkeypatch, uid, tool, outro, declarado):
    _, blocos, _ = _turno(monkeypatch, uid, tool, () if declarado == "nada" else (outro,))
    (visao,) = _tool_msgs(uid)
    assert not blocos and "card_na_tela" not in visao
    assert list(visao) == CHAVES_DE_SEMPRE[tool[0]]


# ── 5. teto e repetição ──────────────────────────────────────────────────────────────────────────

def _chama(uid, tool, usados=None, declarado=DO_APP):
    ev, t1, t2, t3 = [], streaming.EMISSOR.set(lambda e: ev.append(e)), CARDS_DO_APP.set(declarado), CARDS_DO_TURNO.set(usados)
    try:
        return get_tool(tool[0]).execute(uid, tool[1]), [e for e in ev if e["tipo"] == "bloco"], usados
    finally:
        CARDS_DO_TURNO.reset(t3)
        CARDS_DO_APP.reset(t2)
        streaming.EMISSOR.reset(t1)


def test_vagas_gastas_o_card_nao_sai_e_a_tool_devolve_o_dado(uid):
    r, blocos, usados = _chama(uid, MAIORES, usados=["contas", "hero", "resumo"])
    assert not blocos and "card_na_tela" not in r and len(r["expenses"]) == 3 and len(usados) == 3


def test_vaga_livre_o_card_gasta_uma_vaga_e_o_quarto_nao_sai(uid):
    r, blocos, usados = _chama(uid, MAIORES, usados=["contas", "hero"])
    assert len(blocos) == 1 and r["card_na_tela"] and usados == ["contas", "hero", "maiores_gastos"]
    r, blocos, _ = _chama(uid, FIM, usados=usados)  # a 4ª
    assert not blocos and "card_na_tela" not in r and "nota_metodologia" in r


def test_repetida_no_turno_so_a_primeira_emite(monkeypatch, uid):
    openai_falso(monkeypatch, rodada(None, MAIORES), rodada(None, MAIORES), rodada("Comentário."))
    _, eventos, _ = executar(uid, "pergunta", cards=DO_APP)
    assert [e["bloco"]["card"] for e in eventos if e["tipo"] == "bloco"] == ["maiores_gastos"]
    primeira, segunda = _tool_msgs(uid)
    assert primeira["card_na_tela"] and "expenses" not in primeira
    assert "card_na_tela" not in segunda and len(segunda["expenses"]) == 3  # a 2ª: o dado, nada se perde


# ── 6. quando o card não resolve, o dado sai e a tool não vira falha_interna ─────────────────────

def test_sem_acesso_ao_app_o_dado_sai_sem_bloco(monkeypatch, uid):
    def nega(_uid):
        raise HTTPException(402, "sem plano")
    monkeypatch.setattr(cards, "exigir_acesso_do_app", nega)
    _, blocos, _ = _turno(monkeypatch, uid, MAIORES)
    (visao,) = _tool_msgs(uid)
    assert not blocos and "error" not in visao and len(visao["expenses"]) == 3


def test_forecast_sem_o_plano_nao_emite_card(monkeypatch):
    uid = completo(usuario_pagante(plan="essencial"))
    _, blocos, _ = _turno(monkeypatch, uid, FIM)
    (visao,) = _tool_msgs(uid)
    assert visao["error"] == "pro_required" and not blocos


def test_lista_vazia_nao_emite_card(monkeypatch, pro_user_id):
    completo(pro_user_id)
    _, blocos, _ = _turno(monkeypatch, pro_user_id, MAIORES)
    (visao,) = _tool_msgs(pro_user_id)
    assert visao["count"] == 0 and visao["expenses"] == [] and not blocos


def test_forecast_do_mes_zerado_nao_emite_card_e_devolve_o_resultado_normal(monkeypatch, pro_user_id):
    completo(pro_user_id)  # nenhum lançamento: receita e despesa 0 não viram card com cara de sucesso
    _, blocos, _ = _turno(monkeypatch, pro_user_id, FIM)
    (visao,) = _tool_msgs(pro_user_id)
    assert visao["receita"] == 0 and visao["despesa"] == 0 and "card_na_tela" not in visao and not blocos
    assert "nota_metodologia" in visao


def test_maiores_gastos_resume_so_as_3_primeiras_categorias(monkeypatch, uid):
    for valor, cat in ((60, "lazer"), (50, "saúde"), (40, "educação")):  # 6 categorias; o limite padrão traz 5 itens
        db.add_launch_and_update_balance(uid, "despesa", valor, cat, cat, categoria=cat)
    _turno(monkeypatch, uid, MAIORES)
    (visao,) = _tool_msgs(uid)
    assert visao["categorias"] == ["moradia", "alimentação", "transporte"] and visao["count"] == 5  # o limite padrão


# ── 7. conversa: dois assuntos, o estado de um turno não vaza para o outro ───────────────────────

def test_conversa_so_o_turno_dos_maiores_gastos_tem_bloco(monkeypatch, uid):
    openai_falso(monkeypatch, rodada(None, MAIORES), rodada("Esses pesam."),
                 rodada(None, ("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado"})))
    _, ev1, _ = executar(uid, "meus maiores gastos", cards=DO_APP)
    _, ev2, _ = executar(uid, "gastei 50 no mercado", cards=DO_APP)
    assert [e["bloco"]["card"] for e in ev1 if e["tipo"] == "bloco"] == ["maiores_gastos"]
    assert not [e for e in ev2 if e["tipo"] == "bloco"]
    assert db.count_launches(uid) == 5  # 4 do cenário + o gasto de 50


# ── 8. o 🐷 só some no stream do app ─────────────────────────────────────────────────────────────

def test_stream_tira_o_porco_do_delta_e_do_fim_mas_o_historico_guarda(monkeypatch, uid):
    openai_falso(monkeypatch, rodada("🐷 Tudo certo por aqui."))  # o 1º pedaço é só "🐷"
    r, eventos, _ = executar(uid, "oi")
    deltas = [e["texto"] for e in eventos if e["tipo"] == "delta"]
    assert "".join(deltas) == "Tudo certo por aqui." and "🐷" not in "".join(deltas)
    assert r.reply == "Tudo certo por aqui."
    assert [l["content"] for l in linhas(uid) if l["role"] == "assistant"] == ["🐷 Tudo certo por aqui."]


def test_stream_porco_sozinho_no_1o_pedaco_e_o_espaco_no_2o_nao_vaza(monkeypatch, uid):
    openai_falso(monkeypatch, rodada("🐷 Oi."))
    monkeypatch.setattr(_ia_stream_helpers, "_chunks", lambda r: [_chunk("🐷"), _chunk(" Oi.")])  # a divisão real dá "🐷 "
    _, eventos, _ = executar(uid, "oi")
    assert [e["texto"] for e in eventos if e["tipo"] == "delta"] == ["Oi."]


def test_stream_sem_porco_nao_mexe_no_texto(monkeypatch, uid):
    openai_falso(monkeypatch, rodada("Tudo certo por aqui."))
    r, eventos, _ = executar(uid, "oi")
    assert "".join(e["texto"] for e in eventos if e["tipo"] == "delta") == r.reply == "Tudo certo por aqui."


def test_whatsapp_mantem_o_porco(monkeypatch, uid):
    openai_falso(monkeypatch, rodada("🐷 Tudo certo por aqui."))
    assert runner.chat(uid, "oi", monthly_limit=100) == "🐷 Tudo certo por aqui."


# ── 9. prompt ────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("metas", [False, True])
def test_prompt_do_app_sem_porco_sem_force_e_sem_lado_a_lado(metas):
    p = prompt_do_usuario(metas, True)
    assert "🐷" not in p and "nunca force card" not in p and "lado a lado" not in p
    assert "Sem emoji." in p and "card_na_tela" in p and "🎉" not in p
    assert "`nota_metodologia`" in p  # o aviso de dados incompletos do Fim do mês só vive nessa chave


@pytest.mark.parametrize("metas", [False, True])
def test_prompt_do_whatsapp_segue_com_porco_e_sem_o_adendo(metas):
    p = prompt_do_usuario(metas, False)
    assert "1 emoji só (🐷 no início)" in p and "🐷 [Título curto]" in p
    assert "card_na_tela" not in p and "Sem emoji." not in p


# ── visão para o modelo x ai_guard ───────────────────────────────────────────────────────────────

def _afirmacoes(uid, resposta):
    return {c.token: c.supported for c in ai_guard.check(resposta, ai_guard.tool_results(
        [{"role": "tool", "content": l["content"]} for l in linhas(uid) if l["role"] == "tool"]))}


def test_numeros_da_visao_resumida_sustentam_a_resposta(monkeypatch, uid):
    _turno(monkeypatch, uid, MAIORES)
    ok = _afirmacoes(uid, "O maior foi R$ 300,00 e os 3 somam R$ 500,00.")
    assert ok and all(ok.values()), ok
    solto = _afirmacoes(uid, "O segundo foi R$ 120,00.")  # está só na tela: o guard (modo log) o marca
    assert solto and not any(solto.values()), solto


def test_numeros_do_fim_do_mes_sustentam_a_resposta(monkeypatch, uid):
    _turno(monkeypatch, uid, FIM)
    (visao,) = _tool_msgs(uid)
    brl = lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")  # noqa: E731
    ok = _afirmacoes(uid, f"Vai fechar em {brl(visao['saldo_projetado'])}, com gasto de {brl(visao['despesa_projetada'])}.")
    assert ok and all(ok.values()), ok


# ── 10. auditoria tool x card: tool de leitura nova sem classificação deixa isto vermelho ────────

DA_TOOL_DE = {"get_largest_expenses": "maiores_gastos", "forecast_month_end": "fim_do_mes", "compare_periods": "comparacao",
              "list_recent_launches": "ultimos_lancamentos", "get_uncategorized_launches": "sem_categoria"}
R = "recusa→card"
AUDITORIA = {
    "get_largest_expenses": "da_tool", "forecast_month_end": "da_tool", "compare_periods": "da_tool",
    "list_recent_launches": "da_tool", "get_uncategorized_launches": "da_tool",
    # o app só tem o card de meses inteiros: o período livre segue em texto (onda 2)
    "get_period_summary": R, "get_top_categories": R, "get_category_spend": R,
    "get_open_bill": R, "list_pockets": "prompt→card",
    "forecast_balance": R, "forecast_next_bill": R, "get_balance": R, "get_card_limit_usage": R,
    "get_spending_trend": R, "get_total_debt": R, "list_installments": R, "list_metas": R,
    # onda 2 (os cards parecidos leem de outro leitor: plugá-los daria número diferente)
    "get_budget_status": "sem card: orçamento (onda 2)", "get_bills_to_pay": "sem card: contas a pagar (onda 2)",
    "check_cashflow": "sem card: prazo de boleto (onda 2)", "get_investment_summary": "sem card: investimentos (onda 2)",
    "get_investment_contributions": "sem card: aportes (onda 2)", "simulate_purchase": "sem card: simulação (onda 2)",
    "list_cards": "sem card: o card fatura mostra os cartões",
    "list_categories": "sem card: configuração", "list_user_rules": "sem card: configuração",
    "get_daily_report_prefs": "sem card: configuração",
}


def test_toda_tool_de_leitura_do_app_esta_classificada():
    do_app = {s["function"]["name"] for s in (*SCHEMAS_APP, *schemas_com_metas(SCHEMAS_APP, True))}
    leitura = {n for n in do_app if not get_tool(n).is_write} - {"show_card"} | OCULTAS_NO_APP
    assert set(AUDITORIA) == leitura, (set(AUDITORIA) ^ leitura)
    assert {n: c for n, c in DA_TOOL_DE.items() if AUDITORIA[n] == "da_tool"} == DA_TOOL_DE
    assert set(DA_TOOL_DE.values()) == set(cards.DA_TOOL) and not set(cards.DA_TOOL) & set(cards.IDS)
    assert all(v in ("da_tool", R, "prompt→card") or v.startswith("sem card: ") for v in AUDITORIA.values())


# ── 11. paridade com a fixture do app ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool,card", [(MAIORES, "maiores_gastos"), (FIM, "fim_do_mes")])
def test_bloco_real_tem_a_forma_da_fixture_do_app(monkeypatch, uid, tool, card):
    fix = json.loads((FIXTURES / f"ai_chat_bloco_{card}.json").read_text())
    _, (bloco,), _ = _turno(monkeypatch, uid, tool)
    _conforme(bloco["abas"][0]["recursos"][card], fix["abas"][0]["recursos"][card], card)
    assert fix["card"] == card
