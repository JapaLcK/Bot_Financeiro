"""F3c da IA: (1) o `show_card` que a volta seguinte oferece só leva no enum os cards que ainda cabem (sem os já mostrados nem os que
falharam) e sai dos `tools` sem vaga; (2) a linha de data traz o mês sem ano; (3) a visão do `categorias` que compara meses leva só a
maior categoria por mês. Conversa pelo runner (OpenAI falsa, banco real). A falsa IGNORA `tools` e `tool_choice`: a obediência do modelo
não é provada aqui, só o que o código ENVIA. O pytest NÃO fala com o modelo."""
import json
import re
from datetime import date, datetime, timedelta

import pytest

import db
from _cards_apoio import M, M0, mostrar, mundo  # noqa: F401  (mundo é fixture)
from _ia_stream_helpers import executar, openai_falso, rodada
from core.services.ai_chat import cards, runner, system_prompt as sp
from core.services.ai_chat.tools import SCHEMAS_APP, mostrar_card
from test_ai_chat_f3 import _tool_msgs
from utils_date import _tz

MESES_SEM_ACENTO = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
                    "novembro", "dezembro"]
RENDA = {"card": "renda", "quantidade_meses": 4}
FATURA = ("get_open_bill", {"card_name": "Nubank"})
TODOS = [i for i in cards.IDS if i != "caixinhas"]  # o enum de quem não tem a chave das metas


def _enums(reg):
    """Por volta, o enum de `card` do `show_card` ENVIADO à OpenAI; None = o `show_card` nem foi oferecido."""
    def enum(c):
        return next((t["function"]["parameters"]["properties"]["card"]["enum"] for t in c["tools"]
                     if t["function"]["name"] == "show_card"), None)
    return [enum(c) for c in reg.creates]


def _pede(**args):
    return rodada(None, ("show_card", args))


def _blocos(eventos):
    return [e["bloco"]["card"] for e in eventos if e["tipo"] == "bloco"]


def _sem_chave(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "")  # `metas` vira `caixinhas`; o enum é o de `TODOS`


# ── 1. o enum do show_card só tem o que ainda cabe ───────────────────────────────────────────────

def test_caso_11_depois_do_renda_o_enum_da_volta_seguinte_nao_tem_renda_e_tem_os_outros(mundo, monkeypatch):
    a, _ = mundo
    _sem_chave(monkeypatch)
    reg = openai_falso(monkeypatch, _pede(**RENDA), _pede(**RENDA), rodada("Veja o card."))
    r, eventos, _ = executar(a, "evolução de 4 meses")
    assert [t.get("error") for t in _tool_msgs(a)] == [None, "card_repetido"]  # o falso ignora o enum: a recusa segue de pé
    assert _enums(reg) == [TODOS, [i for i in TODOS if i != "renda"], [i for i in TODOS if i != "renda"]]  # controle: o resto fica
    assert _blocos(eventos) == ["renda"] and r.reply == "Veja o card."
    assert db.ai_get_usage_this_month(a) == 1  # a cota é do turno, não da volta


def test_saldo_depois_do_card_repetido_o_show_card_contas_ainda_sai(mundo, monkeypatch):
    """A sonda do Manager: sem tool de texto para o saldo, `contas` tem de seguir alcançável depois do `card_repetido`."""
    a, _ = mundo
    reg = openai_falso(monkeypatch, _pede(**RENDA), _pede(**RENDA), _pede(card="contas"), rodada("Saldo: tanto."))
    _, eventos, _ = executar(a, "evolução e meu saldo")
    assert [t.get("error") for t in _tool_msgs(a)] == [None, "card_repetido", None]
    assert _blocos(eventos) == ["renda", "contas"] and "contas" in _enums(reg)[2]


def test_card_que_falhou_sai_do_enum_e_a_tool_de_texto_do_alvo_devolve_o_dado(mundo, monkeypatch):
    a, _ = mundo
    _sem_chave(monkeypatch)
    montar = mostrar_card.montar

    def falha_a_fatura(card, uid, args):
        if card == "fatura":
            raise cards.CardIndisponivel("ainda não")
        return montar(card, uid, args)
    monkeypatch.setattr(mostrar_card, "montar", falha_a_fatura)
    reg = openai_falso(monkeypatch, _pede(card="fatura"), rodada(None, FATURA), rodada("A fatura é tanto."))
    executar(a, "minha fatura")
    falha, fatura = _tool_msgs(a)
    assert falha["error"] == "indisponivel" and "error" not in fatura and fatura["total"] > 0
    assert _enums(reg)[1] == [i for i in TODOS if i != "fatura"]  # controle: só o que falhou sai


def test_sem_vaga_o_show_card_sai_dos_tools_e_o_get_open_bill_devolve_o_total(mundo, monkeypatch):
    """Cenário do Manager: contas, parcelas e patrimonio gastam as 3 vagas na mesma rodada. Com 2 gastas (positivo) o
    `show_card` ainda é oferecido, sem os 2."""
    a, _ = mundo
    _sem_chave(monkeypatch)
    reg = openai_falso(monkeypatch, _pede(card="contas"), rodada(None, ("show_card", {"card": "parcelas"}), ("show_card", {"card": "patrimonio"})),
                       rodada(None, FATURA), rodada("A fatura é tanto."))
    r, _, _ = executar(a, "quanto está minha fatura do Nubank?")
    assert _enums(reg)[1] == [i for i in TODOS if i != "contas"] and _enums(reg)[2:] == [None, None]
    fatura = _tool_msgs(a)[-1]
    assert "error" not in fatura and fatura["total"] > 0 and r.reply == "A fatura é tanto."


def test_tres_resumos_numa_rodada_um_bloco_dois_repetidos_e_o_enum_leva_o_categorias(mundo, monkeypatch):
    """"Compara jan, fev e mar": o falso chama os 3 antes do enum mudar. Na volta seguinte o `resumo` já saiu e o `categorias`
    (a saída que a recusa aponta) fica e emite o bloco."""
    a, _ = mundo
    pede = lambda m: ("show_card", {"card": "resumo", "meses": [m]})  # noqa: E731
    reg = openai_falso(monkeypatch, rodada(None, pede(M), pede(M0), pede("2020-01")), _pede(card="categorias", meses=[M0, M]),
                       rodada("Comparei."))
    _, eventos, _ = executar(a, "compara os 3 meses")
    assert [t.get("error") for t in _tool_msgs(a)][:3] == [None, "card_repetido", "card_repetido"]
    assert "resumo" not in _enums(reg)[1] and "categorias" in _enums(reg)[1] and _blocos(eventos) == ["resumo", "categorias"]


@pytest.mark.parametrize("chave,primeiro,fora,fica", [
    (False, "caixinhas", {"metas"}, {"contas"}),            # sem a chave `metas` É `caixinhas`: o alias não volta pelo enum
    (False, "metas", {"metas"}, {"contas"}),                # o inverso: o `metas` mostrado gastou o efetivo
    (True, "caixinhas", {"caixinhas"}, {"metas", "contas"}),  # com a chave são cards distintos: o `metas` fica
    (True, "metas", {"metas"}, {"caixinhas", "contas"}),
])
def test_alias_metas_sem_chave_e_caixinhas_saem_juntos_e_com_chave_nao(mundo, monkeypatch, chave, primeiro, fora, fica):
    a, _ = mundo
    if not chave:
        _sem_chave(monkeypatch)
    reg = openai_falso(monkeypatch, _pede(card=primeiro), rodada("Veja."))
    executar(a, "minhas metas")
    enum = set(_enums(reg)[1])
    assert not fora & enum and fica <= enum


@pytest.mark.parametrize("antes,esperado", [
    ([], "Use show_card (contas, hero, fatura, parcelas, renda, metas, caixinhas)."),  # controle: nada usado, a mensagem de sempre
    (["contas", "renda"], "Use show_card (hero, fatura, parcelas, metas, caixinhas)."),
    (["contas", "hero", "fatura"], f"O dado vem das tools de texto ({cards.TOOLS_DE_TEXTO}...)."),  # sem vaga
])
def test_indisponivel_no_app_cita_so_os_cards_que_ainda_cabem(mundo, monkeypatch, antes, esperado):
    a, _ = mundo
    openai_falso(monkeypatch, *[_pede(card=c) for c in antes], rodada(None, ("get_balance", {})), rodada("Pronto."))
    executar(a, "meu saldo")
    assert _tool_msgs(a)[-1] == {"error": "indisponivel_no_app", "message": f"Não existe no app. {esperado}"}


def test_sem_filtro_os_tools_sao_a_lista_global_e_com_filtro_o_global_nao_muda(mundo, monkeypatch):
    a, _ = mundo
    _sem_chave(monkeypatch)
    antes = json.dumps(SCHEMAS_APP, sort_keys=True)
    reg = openai_falso(monkeypatch, _pede(card="contas"), rodada("Veja."))
    executar(a, "saldo")
    assert reg.creates[0]["tools"] is SCHEMAS_APP  # nada filtrado: a mesma lista (identidade)
    assert _enums(reg)[1] == [i for i in TODOS if i != "contas"] and reg.creates[1]["tools"] is not SCHEMAS_APP
    assert json.dumps(SCHEMAS_APP, sort_keys=True) == antes and mostrar_card.SCHEMA["function"]["parameters"]["properties"]["card"]["enum"] == TODOS


def test_whatsapp_nao_oferece_o_show_card_e_o_que_o_modelo_imita_recebe_o_dado_e_depois_o_card_repetido(mundo, monkeypatch):
    a, _ = mundo
    reg = openai_falso(monkeypatch, _pede(**RENDA), _pede(**RENDA), rodada("Pronto."))
    runner.chat(a, "evolução", monthly_limit=100)
    assert [t.get("error") for t in _tool_msgs(a)] == [None, "card_repetido"] and _enums(reg) == [None] * 3


def test_na_ultima_volta_do_teto_o_tool_choice_none_continua(mundo, monkeypatch):
    a, _ = mundo
    monkeypatch.setattr(runner, "MAX_TOOL_LOOPS", 3)
    reg = openai_falso(monkeypatch, _pede(card="contas"), _pede(card="hero", dias="30"), rodada("Fim."))
    executar(a, "mostra")
    assert [c.get("tool_choice") for c in reg.creates] == [None, None, "none"]


def test_comparar_meses_no_categorias_o_outro_mes_vem_do_get_top_categories(mundo, monkeypatch):
    """O `card_repetido` do `categorias` aponta a tool de texto, que devolve o dado."""
    a, _ = mundo
    _semeia_categorias(a)
    fim = (date(int(M0[:4]), int(M0[5:]), 1) + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    openai_falso(monkeypatch, _pede(card="categorias", meses=[M]), _pede(card="categorias", meses=[M0]),
                 rodada(None, ("get_top_categories", {"start_date": f"{M0}-01", "end_date": fim.isoformat()})), rodada("Comparei."))
    executar(a, "compara os gastos de dois meses")
    repetido, dado = _tool_msgs(a)[1:]
    assert repetido["error"] == "card_repetido" and "get_top_categories" in repetido["message"]
    assert "meses=[...]" not in repetido["message"] and "error" not in dado and dado["categories"]


# ── 2. mês sem ano = o último que já começou ─────────────────────────────────────────────────────

@pytest.mark.parametrize("hoje,esperado", [
    (date(2026, 1, 15), '("fevereiro" = 2025-02)'),
    (date(2026, 6, 15), '("julho" = 2025-07)'),
    (date(2026, 10, 15), '("novembro" = 2025-11)'),
    (date(2026, 12, 15), '("janeiro" = 2026-01)'),  # em dezembro nenhum mês falta: o exemplo é janeiro deste ano
])
def test_linha_de_data_traz_o_exemplo_do_mes_sem_ano(hoje, esperado):
    assert f"mês sem ano = o último que já começou {esperado}" in runner._linha_de_data(hoje)


@pytest.mark.parametrize("hoje", [date(ano, mes, 1) for ano in (2026, 2027) for mes in range(1, 13)]
                         + [date(2026, 12, 31), date(2027, 1, 31)])
def test_o_exemplo_do_mes_sem_ano_e_sempre_o_ultimo_que_ja_comecou(hoje):
    ano, m = map(int, re.search(r'\("\w+" = (\d{4})-(\d{2})\)', runner._linha_de_data(hoje)).groups())
    assert (ano, m) <= (hoje.year, hoje.month) < (ano + 1, m)  # já começou, e o do ano seguinte ainda não


@pytest.mark.parametrize("hoje", [date(2026, m, 15) for m in range(1, 13)])
def test_o_parser_de_periodo_e_a_linha_de_data_leem_o_mes_sem_ano_pela_mesma_regra(hoje, monkeypatch):
    """§0.7: `parse_period_from_text` e o exemplo da IA usam `ano_do_mes`; 12 meses × um hoje em cada mês."""
    import utils_date
    from utils_date import ano_do_mes, parse_period_from_text
    monkeypatch.setattr(utils_date, "today_tz", lambda: hoje)
    for num, nome in enumerate(MESES_SEM_ACENTO, 1):
        inicio, _, _ = parse_period_from_text(f"gastos em {nome}")
        assert (inicio.year, inicio.month) == (ano_do_mes(hoje, num), num)


def _exemplo_do_prompt(hoje, metas=False):
    return re.search(r'compara março com agosto" \(meses inteiros\) = categorias com meses=(\[[^\]]*\])',
                     sp.prompt_do_usuario(metas, True, hoje))[1]


@pytest.mark.parametrize("hoje,esperado", [
    (date(2027, 2, 10), '["2026-03","2026-08"]'),   # fev/2027: nenhum dos dois começou em 2027 (era 2027-03: futuro)
    (date(2027, 9, 10), '["2027-03","2027-08"]'),   # set/2027: os dois já começaram
    (date(2026, 10, 15), '["2026-03","2026-08"]'),
    (date(2026, 6, 15), '["2026-03","2025-08"]'),   # só março já começou
    (date(2027, 3, 10), '["2027-03","2026-08"]'),   # em março, "março" já começou (mês == atual) e agosto não
    (date(2027, 8, 10), '["2027-03","2027-08"]'),   # em agosto, idem para "agosto"
])
@pytest.mark.parametrize("metas", [False, True])
def test_exemplo_de_comparar_meses_do_prompt_segue_a_regra_da_linha_de_data(hoje, esperado, metas):
    assert _exemplo_do_prompt(hoje, metas) == esperado


@pytest.mark.parametrize("hoje", [date(2026, mes, 1) for mes in range(1, 13)])
def test_nenhum_mes_do_exemplo_do_prompt_e_futuro(hoje):
    assert all(tuple(map(int, m.split("-"))) <= (hoje.year, hoje.month) for m in json.loads(_exemplo_do_prompt(hoje)))


# ── 3. comparando meses, o categorias leva só a maior categoria por mês ──────────────────────────

def _semeia_categorias(uid):
    for mes in (M0, M):
        for dia, (cat, v) in enumerate((("lazer", 90), ("transporte", 60), ("saude", 30)), 10):
            db.add_launch_and_update_balance(uid, "despesa", v, cat, None, categoria=cat,
                                             criado_em=datetime(int(mes[:4]), int(mes[5:]), dia, 12, tzinfo=_tz()))


def test_categorias_com_2_abas_traz_a_maior_de_cada_e_com_1_aba_continuam_3(mundo):
    a, _ = mundo
    _semeia_categorias(a)
    r, (bloco,), _ = mostrar(a, card="categorias", meses=[M0, M])
    cats = [x["recursos"]["detalhes"]["categorias"] for x in bloco["abas"]]
    assert all(len(c) >= 3 for c in cats), "a fixture precisa de 3+ categorias por mês, senão 1 × 3 não se distingue"
    for aba, c in zip(r["abas"], cats):
        assert [x["categoria"] for x in aba["categorias"]] == [c[0]["categoria"]]
        assert aba["categorias_total"] == len(c) and "saldo" in aba
    r1, _, _ = mostrar(a, card="categorias", meses=[M])
    assert len(r1["abas"][0]["categorias"]) == 3


def test_categorias_com_2_abas_mantem_a_categoria_pedida(mundo):
    a, _ = mundo
    _semeia_categorias(a)
    _, (bloco,), _ = mostrar(a, card="categorias", meses=[M])
    terceira = bloco["abas"][0]["recursos"]["detalhes"]["categorias"][2]["categoria"]  # fora da maior
    r, _, _ = mostrar(a, card="categorias", meses=[M0, M], categoria=terceira)
    assert all(len(aba["categorias"]) == 1 and aba["categoria_pedida"]["categoria"] for aba in r["abas"])
    assert r["abas"][1]["categoria_pedida"]["valor"] > 0
