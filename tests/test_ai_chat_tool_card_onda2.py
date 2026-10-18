"""Onda 2, PR A: `compare_periods` mostra o próprio card `comparacao` (como `get_largest_expenses` e `forecast_month_end`
na onda 1, `tests/test_ai_chat_tool_card.py`, de onde vêm os apoios). Relógio congelado em 12/10/2026: o mês aberto é o
mês INTEIRO (P1-B), então o número do bloco é o do `resumo_do_mes`. O pytest não fala com o modelo: aqui se prova o que o
código empurra; se o modelo segue, só o staging diz."""
import json
from datetime import date, datetime

import pytest

import db
from _ia_stream_helpers import executar, openai_falso, rodada
from conftest import usuario_pagante
from core.services.ai_chat import cards, runner
from core.services.ai_chat.tools import get_tool, launches
from core.services import comparacao_periodos as cp
from db import resumo_mes
from test_ai_chat_tool_card import FIXTURES, _afirmacoes, _chama, _tool_msgs, _turno
from test_ai_chat_periodo import _conforme
from test_api_app_sessao import completo
from test_comparacao_periodos import _lanc, semeia
from utils_date import _tz

AGORA = datetime(2026, 10, 12, 10, 0, tzinfo=_tz())
DO_APP = cards.DA_TOOL
COMPARA = ("compare_periods", {"mes": "2026-10"})
CHAVES_DE_SEMPRE = ["modo", "escolha", "atual", "anterior", "mesmo_tamanho", "anterior_encurtado", "base", "despesa",
                    "receita", "resultado", "categorias", "categorias_omitidas", "motivo", "motivos", "nota", "dias_corridos", "dias_no_mes"]  # o app que não declara o card também as recebe: o servidor só acrescenta campo


@pytest.fixture(autouse=True)
def _relogio(monkeypatch):
    monkeypatch.setattr(cp, "now_tz", lambda: AGORA)
    monkeypatch.setattr(launches, "today_tz", lambda: AGORA.date())
    monkeypatch.setattr(launches, "now_tz", lambda: AGORA)


@pytest.fixture
def uid(pro_user_id):
    completo(pro_user_id)
    semeia(pro_user_id)  # outubro e setembro de 2026, fatura e parcelado incluídos
    return pro_user_id


# ── 1 a 3. o bloco, a visão e a fonte única ──────────────────────────────────────────────────────

def test_emite_o_bloco_com_a_forma_da_fixture_do_app(monkeypatch, uid):
    _, blocos, _ = _turno(monkeypatch, uid, COMPARA)
    (bloco,) = blocos
    fix = json.loads((FIXTURES / "ai_chat_bloco_comparacao.json").read_text())
    assert fix["card"] == bloco["card"] == "comparacao" and bloco["abas"][0]["mes"] is None
    rec = bloco["abas"][0]["recursos"]["comparacao"]
    _conforme(rec, fix["abas"][0]["recursos"]["comparacao"], "comparacao")
    assert rec["serie"]["atual"][-1] == rec["despesa"]["total_atual"] and rec["serie"]["anterior"][-1] == rec["despesa"]["total_anterior"]
    assert "escolha" not in rec and "nota" not in rec  # são do modelo, não da tela
    assert len(rec["categorias"]) == 6  # o bloco leva todas (até 10); só a visão do modelo corta


def test_o_modelo_recebe_a_visao_com_uma_categoria_e_o_escolha(monkeypatch, uid):
    _, (bloco,), _ = _turno(monkeypatch, uid, COMPARA)
    (visao,) = _tool_msgs(uid)
    rec = bloco["abas"][0]["recursos"]["comparacao"]
    assert visao["card_na_tela"] is True and visao["categorias"] == rec["categorias"][:1] and "serie" not in visao  # o modelo não vê a série
    assert visao["categorias"][0]["categoria"] == "Alimentação" and "Outubro até agora" in visao["escolha"]
    assert {k: v for k, v in visao.items() if k not in ("card_na_tela", "escolha", "nota", "categorias", "categorias_total")} == {
        k: v for k, v in rec.items() if k not in ("categorias", "categorias_omitidas", "serie")}


def test_a_visao_diz_quantas_categorias_ha_e_nao_repassa_o_omitidas_do_bloco(monkeypatch, uid):
    """Cortada para 1, a visão com `categorias_omitidas: 0` diria que só existe essa; `categorias_total` é a contagem real."""
    _, (bloco,), _ = _turno(monkeypatch, uid, COMPARA)
    (visao,) = _tool_msgs(uid)
    rec = bloco["abas"][0]["recursos"]["comparacao"]
    assert len(visao["categorias"]) == 1 and "categorias_omitidas" not in visao
    assert visao["categorias_total"] == len(rec["categorias"]) + rec["categorias_omitidas"] == 6


def test_o_bloco_e_o_numero_do_resumo_do_mes(monkeypatch, uid):
    _, (bloco,), _ = _turno(monkeypatch, uid, COMPARA)
    rec = bloco["abas"][0]["recursos"]["comparacao"]
    res = resumo_mes.resumo_do_mes(uid, 2026, 10, AGORA)
    assert (rec["despesa"]["total_atual"], rec["receita"]["total_atual"]) == (float(res["saiu"]), float(res["entrou"]))
    assert (rec["despesa"]["total_anterior"], rec["receita"]["total_anterior"]) == (
        float(res["anterior"]["saiu"]), float(res["anterior"]["entrou"]))
    assert rec["modo"] == "mes_aberto" and rec["atual"]["fim"] == "2026-10-31"


def test_mes_aberto_leva_o_dia_corrido_para_a_legenda_e_so_ele(monkeypatch, uid):
    """`atual.fim` é o último dia do mês (P1-B): o "dia 17 de 31" do app vem do servidor, não do relógio do aparelho."""
    dia17 = datetime(2026, 10, 17, 10, 0, tzinfo=_tz())
    monkeypatch.setattr(cp, "now_tz", lambda: dia17)
    _, (bloco,), _ = _turno(monkeypatch, uid, COMPARA)
    (visao,) = _tool_msgs(uid)
    rec = bloco["abas"][0]["recursos"]["comparacao"]
    assert (rec["dias_corridos"], rec["dias_no_mes"]) == (17, 31) == (visao["dias_corridos"], visao["dias_no_mes"])


def test_mes_fechado_nao_leva_dia_corrido(uid):
    for r in (cp.comparar_mes(uid, 2026, 9, AGORA), cp.comparar_intervalos(uid, (date(2026, 9, 1), date(2026, 9, 20)), (date(2026, 8, 1), date(2026, 8, 20)), AGORA)):
        assert r["modo"] in ("mes_fechado", "periodos_informados") and not {"dias_corridos", "dias_no_mes"} & r.keys()


# ── 4. controle negativo: sem o emissor o grupo acima fica vermelho ──────────────────────────────

def test_sem_mostrar_da_tool_nao_sai_bloco_e_o_modelo_recebe_o_dado_inteiro(monkeypatch, uid):
    monkeypatch.setattr(launches, "mostrar_da_tool", lambda *a: False)
    _, blocos, _ = _turno(monkeypatch, uid, COMPARA)
    (visao,) = _tool_msgs(uid)
    assert not blocos and "card_na_tela" not in visao and len(visao["categorias"]) == 6


# ── 5 e 6. sem emissor e build antigo: o resultado é o de sempre ─────────────────────────────────

def test_sem_emissor_nada_sai_e_o_resultado_e_o_de_sempre(monkeypatch, uid):
    from core.services.ai_chat._context import CARDS_DO_APP
    CARDS_DO_APP.set(DO_APP)  # mesmo com a capacidade no contexto: sem emissor não há app
    try:
        openai_falso(monkeypatch, rodada(None, COMPARA), rodada("Comentário."))
        assert runner.chat(uid, "pergunta", monthly_limit=100)
    finally:
        CARDS_DO_APP.set(())
    (visao,) = _tool_msgs(uid)
    assert list(visao) == CHAVES_DE_SEMPRE and len(visao["categorias"]) == 6
    assert get_tool("compare_periods").execute(uid, COMPARA[1]) == visao


@pytest.mark.parametrize("declarado", [(), ("maiores_gastos", "fim_do_mes")], ids=["nada", "build_33"])
def test_app_que_nao_declarou_o_card_recebe_o_dado_sem_bloco(monkeypatch, uid, declarado):
    _, blocos, _ = _turno(monkeypatch, uid, COMPARA, declarado)
    (visao,) = _tool_msgs(uid)
    assert not blocos and list(visao) == CHAVES_DE_SEMPRE and len(visao["categorias"]) == 6


# ── 7. onde não há card ──────────────────────────────────────────────────────────────────────────

def test_essencial_nao_ganha_card_pro_required(monkeypatch):
    uid = completo(usuario_pagante(plan="essencial"))
    _, blocos, _ = _turno(monkeypatch, uid, COMPARA)
    (visao,) = _tool_msgs(uid)
    assert visao["error"] == "pro_required" and not blocos


def test_so_o_mes_atual_com_dado_vira_sem_base_e_sem_card(monkeypatch, pro_user_id):
    completo(pro_user_id)
    _lanc(pro_user_id, "despesa", 80, 10, 3, "Mercado")  # setembro vazio
    _, blocos, _ = _turno(monkeypatch, pro_user_id, COMPARA)
    (visao,) = _tool_msgs(pro_user_id)
    assert visao["motivo"] == "sem_base" and "card_na_tela" not in visao and not blocos


def test_historico_cortado_pelo_plano_nao_tem_numero_nem_card(monkeypatch):
    uid = completo(usuario_pagante(plan="plus"))
    _lanc(uid, "despesa", 80, 10, 3, "Mercado", ano=2025)
    _, blocos, _ = _turno(monkeypatch, uid, ("compare_periods", {"mes": "2025-10"}))
    (visao,) = _tool_msgs(uid)
    assert visao["motivo"] == "inicio_do_historico" and visao["despesa"] is None and not blocos


def test_argumento_invalido_nao_tem_card(monkeypatch, uid):
    _, blocos, _ = _turno(monkeypatch, uid, ("compare_periods", {"mes": "2026-13"}))
    (visao,) = _tool_msgs(uid)
    assert "error" in visao and not blocos


def test_dois_meses_passados_seguem_para_o_card_categorias(monkeypatch, uid):
    _, blocos, _ = _turno(monkeypatch, uid, ("compare_periods", {"mes": "2026-09"}))  # setembro × agosto
    (visao,) = _tool_msgs(uid)
    assert visao["error"] == "comparar_no_card" and "show_card categorias" in visao["message"] and not blocos


# ── 8. teto de 3 vagas e repetição ───────────────────────────────────────────────────────────────

def test_vagas_gastas_o_card_nao_sai_e_a_tool_devolve_o_dado(uid):
    r, blocos, usados = _chama(uid, COMPARA, usados=["contas", "hero", "resumo"])
    assert not blocos and "card_na_tela" not in r and len(r["categorias"]) == 6 and len(usados) == 3


def test_vaga_livre_gasta_uma_vaga_e_a_repetida_nao_emite(uid):
    r, blocos, usados = _chama(uid, COMPARA, usados=["contas", "hero"])
    assert len(blocos) == 1 and r["card_na_tela"] and usados == ["contas", "hero", "comparacao"]
    r, blocos, _ = _chama(uid, COMPARA, usados=["comparacao"])
    assert not blocos and "card_na_tela" not in r


# ── 9. conversa: dois assuntos, o estado de um turno não vaza para o outro ───────────────────────

def test_conversa_so_o_turno_da_comparacao_tem_bloco(monkeypatch, uid):
    antes = db.count_launches(uid)
    openai_falso(monkeypatch, rodada(None, COMPARA), rodada("Gastou menos."),
                 rodada(None, ("add_launch", {"tipo": "despesa", "valor": 50, "alvo": "mercado"})))
    _, ev1, _ = executar(uid, "esse mês vs o passado", cards=DO_APP)
    _, ev2, _ = executar(uid, "gastei 50 no mercado", cards=DO_APP)
    assert [e["bloco"]["card"] for e in ev1 if e["tipo"] == "bloco"] == ["comparacao"]
    assert not [e for e in ev2 if e["tipo"] == "bloco"]
    assert db.count_launches(uid) == antes + 1  # o 2º assunto foi gravado


# ── 10. ai_guard: o delta da visão sustenta a resposta ───────────────────────────────────────────

def test_total_delta_da_visao_sustenta_a_resposta(monkeypatch, uid):
    """O guard lê o SINAL (`R$ -50,00` ≠ `R$ 50,00`, `ai_guard.money_cents`): o delta que cai é citado com o sinal."""
    _turno(monkeypatch, uid, COMPARA)
    ok = _afirmacoes(uid, "Saiu R$ -1.338,00 em relação a setembro e entrou R$ 500,00 a mais.")  # total_delta: -1338 e +500
    assert ok and all(ok.values()), ok
    errado = _afirmacoes(uid, "Saiu R$ -1.339,00 em relação a setembro.")
    assert errado and not any(errado.values()), errado
