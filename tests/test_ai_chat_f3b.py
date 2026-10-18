"""F3b da IA: `compare_periods` de dois meses inteiros vira o card `categorias`; a janela que a recusa mandou vale quando
o modelo diverge e ela foi cortada por 12 meses. O resumo dos cards de gasto e a fatura: `test_ai_chat_f3b_resumo.py`.
Conversa pelo runner (OpenAI falsa, banco real). O pytest NÃO fala com o modelo: aqui se prova que o código empurra."""
import json
from datetime import date, timedelta

import pytest

from _cards_apoio import M, M0, mundo  # noqa: F401  (mundo é fixture)
from _ia_stream_helpers import executar, openai_falso, rodada
from conftest import usuario_pagante
from core.services.ai_chat._context import CARDS_DO_TURNO, FALHAS_DE_CARD_DO_TURNO
from core.services.ai_chat.tools import get_tool
from test_ai_chat_f3 import _cortando, _tool_msgs
from test_ai_chat_periodo import PRIMEIRO_M0, _mes
from test_ai_chat_uma_fonte_app import com_emissor
from test_api_app_sessao import completo
from tests._ia_falsa_helpers import chamada, com_tools, openai_falso as falso_whatsapp, texto
from tests._pendencia_credito_helpers import diga
from utils_date import today_tz


def _dias(ym):
    """(primeiro, último) dia do mês AAAA-MM, em ISO."""
    ini = date(int(ym[:4]), int(ym[5:]), 1)
    return ini.isoformat(), ((ini + timedelta(days=32)).replace(day=1) - timedelta(days=1)).isoformat()


def _quatro(a, b):
    """Os argumentos de `compare_periods`: o lado A é o mês `a`, o lado B o mês `b` (cada um, o mês inteiro)."""
    (a0, a1), (b0, b1) = _dias(a), _dias(b)
    return {"period_a_start": a0, "period_a_end": a1, "period_b_start": b0, "period_b_end": b1}


def _compara(uid, args, usados=(), falhas=(), emissor=True):
    t1, t2 = CARDS_DO_TURNO.set(list(usados)), FALHAS_DE_CARD_DO_TURNO.set(list(falhas))
    try:
        ex = get_tool("compare_periods").execute
        return com_emissor(ex, uid, args) if emissor else ex(uid, args)
    finally:
        CARDS_DO_TURNO.reset(t1)
        FALHAS_DE_CARD_DO_TURNO.reset(t2)


# ── mudança 1: comparar dois meses inteiros no app = o card `categorias` ─────────────────────────

def test_conversa_comparar_dois_meses_no_app_vai_ao_card_e_no_whatsapp_recebe_o_dado(mundo, monkeypatch):
    a, _ = mundo
    args = _quatro(M0, M)
    fora = get_tool("compare_periods").execute(a, args)  # sem emissor: o dado de sempre
    assert "error" not in fora
    openai_falso(monkeypatch,
                 rodada(None, ("compare_periods", args)),
                 rodada(None, ("show_card", {"card": "categorias", "meses": [M0, M]})),
                 rodada("Veja o card."))
    _, eventos, _ = executar(a, f"compara {M0} com {M}")
    recusa, card = _tool_msgs(a)[:2]
    assert recusa == {"error": "comparar_no_card", "message": f'use show_card categorias com meses=["{M0}","{M}"]'}
    (bloco,) = [e["bloco"] for e in eventos if e["tipo"] == "bloco"]
    assert [x["mes"] for x in card["abas"]] == [M0, M]
    # fonte única: cada aba do card dá a despesa de cada lado da tool (A = anterior, B = atual)
    assert [x["saiu"] for x in card["abas"]] == [fora["despesa"]["total_anterior"], fora["despesa"]["total_atual"]]
    assert len(bloco["abas"]) == 2

    # a MESMA comparação no WhatsApp (sem emissor): o dado, sem recusa
    vistas = []

    def modelo(messages):
        vistas.append([json.loads(m["content"]) for m in messages if m["role"] == "tool"][-1])
        return texto("🐷 Foi isso.")

    falso_whatsapp(monkeypatch, com_tools(chamada("compare_periods", args)), modelo)
    assert "Foi isso" in diga(a, "piggy compara os dois meses")
    assert "error" not in vistas[-1] and vistas[-1]["despesa"] == fora["despesa"]


@pytest.mark.parametrize("nome,recusa", [
    ("passados_A_B", True),                  # positivo: o caso do card
    ("passados_B_A", True),                  # ordem trocada: a lista sai cronológica
    ("mes=M", True),                         # `mes=` passado = [M-1, M]
    ("mes=corrente", False),                 # o mês corrente: mês inteiro × anterior inteiro, sem a recusa (no app com `comparacao` vira o card)
    ("corrente_x_anterior", False),
    ("corrente_em_um_lado", False),
    ("lado_de_2_meses", False),
    ("futuro", False),
    ("A_igual_B", False),
    ("categorias_na_tela", False),           # o card-alvo já está na tela
    ("categorias_falhou", False),
    ("sem_vagas", False),
    ("corte_do_plano_depois_do_dia_1", False),
    ("corte_do_plano_no_dia_1", True),       # `>` e não `>=`: o card mostra o mês inteiro
    ("sem_emissor", False),
])
def test_comparar_so_recusa_dois_meses_passados_inteiros_e_diferentes(mundo, monkeypatch, nome, recusa):
    a, _ = mundo
    usados, falhas, emissor = (), (), True
    args = _quatro(M0, M)
    if nome == "passados_B_A":
        args = _quatro(M, M0)
    elif nome == "mes=M":
        args = {"mes": M}
    elif nome == "mes=corrente":
        args = {"mes": _mes(0)}
    elif nome == "corrente_x_anterior":
        args = _quatro(_mes(-1), _mes(0))
    elif nome == "corrente_em_um_lado":
        args = _quatro(M0, _mes(0))
    elif nome == "lado_de_2_meses":
        args = {**_quatro(M0, M), "period_b_start": _dias(_mes(-4))[0], "period_b_end": _dias(_mes(-3))[1]}
    elif nome == "futuro":
        args = _quatro(M, _mes(1))
    elif nome == "A_igual_B":
        args = _quatro(M, M)
    elif nome == "categorias_na_tela":
        usados = ["categorias"]
    elif nome == "categorias_falhou":
        falhas = ["categorias"]
    elif nome == "sem_vagas":
        usados = ["contas", "fatura", "parcelas"]
    elif nome == "corte_do_plano_depois_do_dia_1":
        _cortando(monkeypatch, PRIMEIRO_M0 + timedelta(days=1))
    elif nome == "corte_do_plano_no_dia_1":
        _cortando(monkeypatch, PRIMEIRO_M0)
    elif nome == "sem_emissor":
        emissor = False
    r = _compara(a, args, usados, falhas, emissor)
    assert (r.get("error") == "comparar_no_card") == recusa, r
    if recusa:
        assert r["message"] == f'use show_card categorias com meses=["{M0}","{M}"]'
    else:
        assert "error" not in r  # o dado, nunca um beco sem saída


def test_comparar_no_essencial_continua_pro_required_no_app():
    uid = completo(usuario_pagante(plan="essencial"))
    r = _compara(uid, _quatro(M0, M))
    assert r["error"] == "pro_required"  # quem não tem a comparação não a ganha pelo card


# ── mudança 2: a janela que a recusa mandou vale quando o modelo diverge e ela foi cortada ───────

def _pede_24_meses():
    """`get_period_summary` de 24 meses atrás até hoje: a recusa manda os 12 mais recentes."""
    return {"start_date": _dias(_mes(-23))[0], "end_date": today_tz().isoformat()}


def test_conversa_pedido_de_24_meses_o_card_usa_a_janela_da_recusa_e_nao_a_do_modelo(mundo, monkeypatch):
    a, _ = mundo
    openai_falso(monkeypatch,
                 rodada(None, ("get_period_summary", _pede_24_meses())),
                 rodada(None, ("show_card", {"card": "periodo", "de": _mes(-23), "ate": _mes(-12)})),  # o modelo diverge
                 rodada("Veja o card."))
    _, eventos, _ = executar(a, "quanto gastei desde jan/2024?")
    recusa, card = _tool_msgs(a)[:2]
    assert recusa["error"] == "periodo_no_card" and f"de={_mes(-11)} e ate={_mes(0)}" in recusa["message"]
    (bloco,) = [e["bloco"] for e in eventos if e["tipo"] == "bloco"]
    meses = [x["mes"] for x in bloco["abas"][0]["recursos"]["periodo"]["meses"]]
    assert meses[0] == _mes(-11) and meses[-1] == _mes(0)  # a janela da recusa, não a do modelo
    assert card["limitado_a_12_meses"] is True and card["aviso_limite"]
    assert card["pedido"] == {"de": _mes(-23), "ate": _mes(0)}  # o pedido ORIGINAL da tool
    assert card["abas"][0]["saiu"] > 0  # a janela errada daria 0,00


def test_conversa_modelo_repete_a_janela_da_recusa_e_leva_os_avisos(mundo, monkeypatch):
    a, _ = mundo
    openai_falso(monkeypatch,
                 rodada(None, ("get_period_summary", _pede_24_meses())),
                 rodada(None, ("show_card", {"card": "periodo", "de": _mes(-11), "ate": _mes(0)})),
                 rodada("Veja o card."))
    executar(a, "quanto gastei desde jan/2024?")
    card = _tool_msgs(a)[1]
    assert card["limitado_a_12_meses"] is True and card["pedido"]["de"] == _mes(-23) and card["aviso_limite"]


def test_conversa_pedido_legitimo_no_passado_continua_como_pedido(mundo, monkeypatch):
    """Positivo: recusa SEM corte (3 meses) e o modelo pede outra janela legítima: nunca é trocada."""
    a, _ = mundo
    tres = {"start_date": _dias(_mes(-3))[0], "end_date": _dias(_mes(-1))[1]}
    openai_falso(monkeypatch,
                 rodada(None, ("get_period_summary", tres)),
                 rodada(None, ("show_card", {"card": "periodo", "de": _mes(-23), "ate": _mes(-20)})),
                 rodada("Veja o card."))
    _, eventos, _ = executar(a, "e lá em 2024?")
    recusa, card = _tool_msgs(a)[:2]
    assert recusa["error"] == "periodo_no_card"
    (bloco,) = [e["bloco"] for e in eventos if e["tipo"] == "bloco"]
    assert [x["mes"] for x in bloco["abas"][0]["recursos"]["periodo"]["meses"]][0] == _mes(-23)
    assert "limitado_a_12_meses" not in card and "pedido" not in card


def test_conversa_com_duas_recusas_o_modelo_que_repete_a_janela_sem_corte_leva_a_dela(mundo, monkeypatch):
    """A cortada (24 meses) e a legítima (3 meses) no mesmo turno: o `show_card` igual à legítima não vira a cortada."""
    a, _ = mundo
    tres = {"start_date": _dias(_mes(-3))[0], "end_date": _dias(_mes(-1))[1]}
    openai_falso(monkeypatch,
                 rodada(None, ("get_period_summary", _pede_24_meses()), ("get_top_categories", tres)),
                 rodada(None, ("show_card", {"card": "periodo", "de": _mes(-3), "ate": _mes(-1)})),
                 rodada("Veja o card."))
    _, eventos, _ = executar(a, "e os últimos 3 meses?")
    (bloco,) = [e["bloco"] for e in eventos if e["tipo"] == "bloco"]
    assert [x["mes"] for x in bloco["abas"][0]["recursos"]["periodo"]["meses"]] == [_mes(-3), _mes(-2), _mes(-1)]
    assert "limitado_a_12_meses" not in _tool_msgs(a)[-1]


def test_conversa_com_duas_janelas_cortadas_vence_a_ultima(mundo, monkeypatch):
    a, _ = mundo
    antiga = {"start_date": _dias(_mes(-35))[0], "end_date": _dias(_mes(-3))[1]}  # cortada em _mes(-14) a _mes(-3)
    openai_falso(monkeypatch,
                 rodada(None, ("get_period_summary", _pede_24_meses()), ("get_top_categories", antiga)),
                 rodada(None, ("show_card", {"card": "periodo", "de": _mes(-40), "ate": _mes(-39)})),
                 rodada("Veja o card."))
    _, eventos, _ = executar(a, "e desde 2023?")
    (bloco,) = [e["bloco"] for e in eventos if e["tipo"] == "bloco"]
    meses = [x["mes"] for x in bloco["abas"][0]["recursos"]["periodo"]["meses"]]
    assert (meses[0], meses[-1]) == (_mes(-14), _mes(-3)) and _tool_msgs(a)[-1]["pedido"]["ate"] == _mes(-3)


@pytest.mark.parametrize("de,cortada", [(_mes(-23), True), (_mes(-2), False)])
def test_conversa_janela_imposta_com_fim_ajustado_leva_o_aviso_do_fim(mundo, monkeypatch, de, cortada):
    """Período que entra no futuro: a recusa o termina no fim do mês atual. Cortada, o modelo diverge (e a janela é
    imposta); sem corte, ele repete a janela. Nos dois, a visão leva `fim_ajustado`; `limitado_a_12_meses` só na cortada."""
    a, _ = mundo
    futuro = {"start_date": _dias(de)[0], "end_date": (today_tz() + timedelta(days=100)).isoformat()}
    card = {"card": "periodo", "de": _mes(-40), "ate": _mes(-39)} if cortada else {"card": "periodo", "de": de, "ate": _mes(0)}
    openai_falso(monkeypatch, rodada(None, ("get_period_summary", futuro)), rodada(None, ("show_card", card)), rodada("Pronto."))
    executar(a, "quanto gastei daqui pra frente?")
    visao = _tool_msgs(a)[-1]
    assert visao["fim_ajustado"] is True and visao["aviso_fim"] and ("limitado_a_12_meses" in visao) == cortada


def test_conversa_segundo_turno_nao_herda_a_janela_do_primeiro(mundo, monkeypatch):
    a, _ = mundo
    openai_falso(monkeypatch,
                 rodada(None, ("get_period_summary", _pede_24_meses())),
                 rodada(None, ("show_card", {"card": "periodo", "de": _mes(-11), "ate": _mes(0)})),
                 rodada("Veja o card."),
                 rodada(None, ("show_card", {"card": "periodo", "de": _mes(-23), "ate": _mes(-12)})),
                 rodada("Veja o card."))
    executar(a, "quanto gastei desde jan/2024?")
    _, eventos, _ = executar(a, "e em 2024?")
    (bloco,) = [e["bloco"] for e in eventos if e["tipo"] == "bloco"]
    assert [x["mes"] for x in bloco["abas"][0]["recursos"]["periodo"]["meses"]][0] == _mes(-23)  # como pedido
    assert "limitado_a_12_meses" not in _tool_msgs(a)[-1]


# ── `get_category_spend` com categoria: o dado só se o card deste turno disse `encontrada=false` para ela ───────────

def _gasto(categoria):
    return rodada(None, ("get_category_spend", {"start_date": _dias(M0)[0], "end_date": _dias(M)[1], "categoria": categoria}))


@pytest.mark.parametrize("card_cat,pedida,erro", [
    ("viagem", "VIAGEM", None),                             # o card não a achou: o dado, sem 2º card (a caixa não importa: cat_key)
    ("Sem Categoria", "sem categoria", "periodo_no_card"),  # positivo: o card a achou, vale a regra normal (recusa e card)
    ("viagem", "mercado", "periodo_no_card"),               # positivo: o `encontrada=false` foi de OUTRA categoria
    (None, "viagem", "periodo_no_card"),                    # positivo: card de gastos aberto, mas sem categoria pedida
])
def test_conversa_get_category_spend_so_devolve_o_dado_se_o_card_nao_achou_a_categoria(mundo, monkeypatch, card_cat, pedida, erro):
    a, _ = mundo
    card = {"card": "categorias", "meses": [M], **({"categoria": card_cat} if card_cat else {})}
    openai_falso(monkeypatch, rodada(None, ("show_card", card)), _gasto(pedida), rodada("Pronto."))
    executar(a, "quanto gastei com isso?")
    assert _tool_msgs(a)[-1].get("error") == erro  # a janela de 2 meses iria ao `periodo`, que ainda resolve: só o `encontrada=false` o impede


def test_conversa_o_encontrada_false_nao_passa_para_o_proximo_turno(mundo, monkeypatch):
    a, _ = mundo
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "categorias", "meses": [M], "categoria": "viagem"})),
                 _gasto("viagem"), rodada("Pronto."), _gasto("viagem"), rodada("Pronto."))
    executar(a, "quanto gastei com viagem?")
    assert "error" not in _tool_msgs(a)[-1]
    executar(a, "e a viagem de novo?")
    assert _tool_msgs(a)[-1]["error"] == "periodo_no_card"
