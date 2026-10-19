"""F3 da IA: no app, a pergunta com card vira card (o `get_period_summary` e o `get_open_bill` apontam para
ele); as tools de data pedem as datas; a linha de data leva o mês atual; JSON cru nunca chega ao usuário.
O pytest NÃO fala com o modelo: aqui se prova que o código empurra; se o modelo segue, só o staging diz."""
import json
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

import db
from _cards_apoio import M, M0, mostrar, mundo  # noqa: F401  (mundo é fixture)
from conftest import usuario_pagante
from test_api_app_sessao import completo
from _ia_stream_helpers import executar, linhas, openai_falso, rodada
from core.services.ai_chat import runner
from core.services.ai_chat.cards import CardIndisponivel
from core.services.ai_chat import system_prompt as sp
from core.services.ai_chat._context import CARDS_DO_TURNO, FALHAS_DE_CARD_DO_TURNO
from core.services.ai_chat.tools import SCHEMAS, get_tool
from db.pockets import update_pocket_meta
from test_ai_chat_periodo import INICIO, PRIMEIRO_M0, ULTIMO, _mes
from test_ai_chat_uma_fonte_app import com_emissor
from utils_date import now_tz, today_tz
from tests._ia_falsa_helpers import chamada, com_tools, openai_falso as falso_whatsapp, texto
from tests._pendencia_credito_helpers import diga

AS_QUATRO = ("get_period_summary", "get_top_categories", "get_category_spend", "get_largest_expenses")


def _tool_msgs(uid):
    return [json.loads(l["content"]) for l in linhas(uid) if l["role"] == "tool"]


# ── conversa: resumo de meses inteiros no app vira card; no WhatsApp segue pela tool ─────────────

def test_conversa_resumo_de_meses_no_app_vai_ao_card_e_no_whatsapp_recebe_o_dado(mundo, monkeypatch):
    a, _ = mundo
    datas = {"start_date": PRIMEIRO_M0.isoformat(), "end_date": ULTIMO.isoformat()}
    fora = get_tool("get_period_summary").execute(a, datas)  # sem emissor: o número de sempre
    assert "error" not in fora and fora["despesa"] > 0

    reg = openai_falso(monkeypatch,
                       rodada(None, ("get_period_summary", datas)),
                       rodada(None, ("show_card", {"card": "periodo", "de": M0, "ate": M})),
                       rodada("Veja o card."))
    _, eventos, _ = executar(a, "quanto gastei nos dois últimos meses?")
    recusa, card = _tool_msgs(a)[:2]
    assert recusa["error"] == "periodo_no_card" and f"de={M0} e ate={M}" in recusa["message"]
    (bloco,) = [e["bloco"] for e in eventos if e["tipo"] == "bloco"]
    periodo = bloco["abas"][0]["recursos"]["periodo"]
    assert float(periodo["saiu"]) == pytest.approx(fora["despesa"])  # fonte única: o card dá o número da tool
    assert len(reg.creates) == 3

    # a MESMA conta, agora no WhatsApp (sem emissor): a tool entrega o dado, sem recusa
    vistas = []

    def modelo(messages):
        vistas.append([json.loads(m["content"]) for m in messages if m["role"] == "tool"][-1])
        return texto("🐷 Foi isso.")

    falso_whatsapp(monkeypatch, com_tools(chamada("get_period_summary", datas)), modelo)
    assert "Foi isso" in diga(a, "piggy quanto gastei nos dois últimos meses")
    assert "error" not in vistas[-1] and vistas[-1]["despesa"] == fora["despesa"]


# ── fatura: o total vem do card, e é a mesma fatura ──────────────────────────────────────────────

def test_conversa_fatura_no_app_chama_o_card_e_o_total_e_o_da_tool_sem_emissor(mundo, monkeypatch):
    a, _ = mundo
    fora = get_tool("get_open_bill").execute(a, {"card_name": "Nubank"})
    assert "error" not in fora and fora["total"] > 0

    openai_falso(monkeypatch,
                 rodada(None, ("get_open_bill", {"card_name": "Nubank"})),
                 rodada(None, ("show_card", {"card": "fatura"})),
                 rodada("Veja o card."))
    _, eventos, _ = executar(a, "quanto está minha fatura do Nubank?")
    app, _card = _tool_msgs(a)[:2]
    assert app["error"] == "total_no_card" and "total" not in app and app["items"]
    (bloco,) = [e["bloco"] for e in eventos if e["tipo"] == "bloco"]
    nubank = next(c for c in bloco["abas"][0]["recursos"]["cartoes"]["cards"] if c["name"] == "Nubank")
    assert nubank["open_bill"]["total"] == fora["total"]  # mesma fatura: fonte única


# ── JSON cru nunca é a resposta ──────────────────────────────────────────────────────────────────

JSON_CRU = '{"tipo":"despesa","valor":50,"alvo":"mercado"}'


@pytest.mark.parametrize("canal", ["app", "whatsapp"])
def test_json_cru_vira_erro_padrao_sem_gravar_e_com_a_cota_de_volta(monkeypatch, pro_user_id, canal):
    antes = db.count_launches(pro_user_id)
    openai_falso(monkeypatch, rodada(JSON_CRU))
    if canal == "app":
        r, _, _ = executar(pro_user_id, "gastei 50 no mercado")
        assert r.reply == runner.ERROR_MSG.removeprefix("🐷 ") and r.message_id is None and not r.sugerivel
    else:
        assert runner.chat(pro_user_id, "gastei 50 no mercado", monthly_limit=100) == runner.ERROR_MSG
    assert db.count_launches(pro_user_id) == antes
    assert db.ai_get_usage_this_month(pro_user_id) == 0  # a vaga voltou
    assert [l["content"] for l in linhas(pro_user_id) if l["role"] == "assistant"] == [runner.ERROR_MSG]


def test_json_cru_na_ultima_volta_sem_tool(monkeypatch, pro_user_id):
    n = runner.MAX_TOOL_LOOPS
    reg = openai_falso(monkeypatch, *[rodada(None, ("get_balance", {})) for _ in range(n - 1)], rodada(JSON_CRU))
    assert runner.chat(pro_user_id, "e o saldo?", monthly_limit=100) == runner.ERROR_MSG
    assert reg.creates[-1]["tool_choice"] == "none"


@pytest.mark.parametrize("canal", ["app", "whatsapp"])
def test_json_cru_cancela_a_pendencia_que_o_turno_armou(monkeypatch, pro_user_id, canal):
    """A rodada 1 arma `delete_all_launches`; a 2 devolve JSON: sem a pendência cancelada, o "sim" seguinte apagaria tudo."""
    db.add_launch_and_update_balance(pro_user_id, "despesa", 10, "mercado", "gastei 10", categoria="alimentação")
    openai_falso(monkeypatch, rodada(None, ("delete_all_launches", {})), rodada('{"x": 1}'))
    if canal == "app":
        reply = executar(pro_user_id, "apaga tudo")[0].reply
        assert reply == runner.ERROR_MSG.removeprefix("🐷 ")
    else:
        reply = runner.chat(pro_user_id, "apaga tudo", monthly_limit=100)
        assert reply == runner.ERROR_MSG
    assert db.ai_get_pending_action(pro_user_id) is None
    assert db.count_launches(pro_user_id) == 1


@pytest.mark.parametrize("resposta", ["50", "3.5", "-1"])  # número sozinho é texto, não argumento de tool
def test_numero_sozinho_passa_como_texto(monkeypatch, pro_user_id, resposta):
    openai_falso(monkeypatch, rodada(resposta))
    assert runner.chat(pro_user_id, "oi", monthly_limit=100) == resposta


@pytest.mark.parametrize("resposta", ["null", "true", "false", "  null\n", "[]", "{}"])  # JSON vazio ou literal: vazamento igual a objeto
def test_literal_json_sozinho_vira_erro_padrao_e_cancela_a_pendencia(monkeypatch, pro_user_id, resposta):
    db.add_launch_and_update_balance(pro_user_id, "despesa", 10, "mercado", "gastei 10", categoria="alimentação")
    openai_falso(monkeypatch, rodada(None, ("delete_all_launches", {})), rodada(resposta))
    assert runner.chat(pro_user_id, "apaga tudo", monthly_limit=100) == runner.ERROR_MSG
    assert db.ai_get_pending_action(pro_user_id) is None and db.count_launches(pro_user_id) == 1


@pytest.mark.parametrize("resposta", [
    "🐷 Gastou R$ 50 {mercado}",                      # chaves no meio do texto
    '🐷 Anotei. {"valor": 50} foi o que li',          # JSON dentro de prosa
    "[ok] 🐷 Beleza, anotei",                         # começa com colchete, não é JSON
    "{não é json}",                                   # começa com chave, não é JSON
])
def test_texto_com_chaves_que_nao_e_json_inteiro_passa(monkeypatch, pro_user_id, resposta):
    openai_falso(monkeypatch, rodada(resposta))
    assert runner.chat(pro_user_id, "oi", monthly_limit=100) == resposta


def test_json_cru_e_lista_tambem(monkeypatch, pro_user_id):
    openai_falso(monkeypatch, rodada('[{"valor": 50}]'))
    assert runner.chat(pro_user_id, "oi", monthly_limit=100) == runner.ERROR_MSG


# ── schema e linha de data ───────────────────────────────────────────────────────────────────────

def test_as_quatro_tools_de_data_pedem_as_duas_datas():
    por_nome = {s["function"]["name"]: s["function"]["parameters"] for s in SCHEMAS}
    for nome in AS_QUATRO:
        assert {"start_date", "end_date"} <= set(por_nome[nome]["required"]), nome
    assert "categoria" in por_nome["get_category_spend"]["required"]


@pytest.mark.parametrize("hoje,esperado", [
    (date(2026, 10, 14), 'Data de hoje: 14/10/2026. Mês atual: 2026-10; "últimos 3 meses" = 2026-08 a 2026-10; mês sem ano = o último que já começou ("novembro" = 2025-11).'),
    (date(2026, 1, 5), 'Data de hoje: 05/01/2026. Mês atual: 2026-01; "últimos 3 meses" = 2025-11 a 2026-01; mês sem ano = o último que já começou ("fevereiro" = 2025-02).'),
    (date(2026, 2, 28), 'Data de hoje: 28/02/2026. Mês atual: 2026-02; "últimos 3 meses" = 2025-12 a 2026-02; mês sem ano = o último que já começou ("março" = 2025-03).'),
])
def test_linha_de_data_leva_o_mes_atual_e_o_exemplo(hoje, esperado):
    assert runner._linha_de_data(hoje) == esperado


@pytest.mark.parametrize("canal", ["app", "whatsapp"])
def test_system_enviado_a_openai_tem_a_linha_de_data_do_mesmo_hoje(monkeypatch, pro_user_id, canal):
    reg = openai_falso(monkeypatch, rodada("Oi!"))
    if canal == "app":
        executar(pro_user_id, "oi")
    else:
        runner.chat(pro_user_id, "oi", monthly_limit=100)
    system = reg.creates[0]["messages"][0]["content"]
    assert system.endswith(runner._linha_de_data(today_tz())) and "Mês atual: " in system


# ── prompt ───────────────────────────────────────────────────────────────────────────────────────

LINHA_PEDIDOS = "Pedidos comuns → card"


@pytest.mark.parametrize("ano", [2026, 2027])
def test_exemplo_de_comparar_meses_leva_o_ano_do_hoje_do_turno(ano):
    esperado = f'categorias com meses=["{ano - 1}-03","{ano - 1}-08"]'  # em janeiro nenhum dos dois meses começou: ano anterior
    for metas in (False, True):
        p = sp.prompt_do_usuario(metas, True, date(ano, 1, 15))
        assert esperado in p and "@@ANO@@" not in p
        assert "@@ANO@@" not in sp.prompt_do_usuario(metas, False, date(ano, 1, 15))  # WhatsApp: sem card, sem marcador


def test_system_enviado_a_openai_leva_o_ano_do_relogio_do_app(monkeypatch, pro_user_id):
    """O MESMO `hoje` da linha de data: relógio do app fixo em 2027 (a virada de ano é o caso que um ano fixo erra)."""
    monkeypatch.setattr(runner, "today_tz", lambda: date(2027, 1, 5))
    reg = openai_falso(monkeypatch, rodada("Oi!"))
    executar(pro_user_id, "oi")
    system = reg.creates[0]["messages"][0]["content"]
    assert 'meses=["2026-03","2026-08"]' in system and "2027-03" not in system
    assert system.endswith(runner._linha_de_data(date(2027, 1, 5)))


def test_adendo_de_cards_so_no_app_e_a_linha_das_metas_se_separa():
    assert LINHA_PEDIDOS in sp.ADENDO_CARDS and '"minhas metas"/"minhas caixinhas" = metas' in sp.ADENDO_CARDS
    assert LINHA_PEDIDOS in sp.ADENDO_CARDS_METAS  # monta sem AssertionError (o `_troca` falha alto)
    assert '"minhas metas" = metas · "minhas caixinhas" = caixinhas' in sp.ADENDO_CARDS_METAS
    assert '"minhas metas"/"minhas caixinhas" = metas' not in sp.ADENDO_CARDS_METAS
    for metas in (False, True):
        assert LINHA_PEDIDOS in sp.prompt_do_usuario(metas, True)
        assert LINHA_PEDIDOS not in sp.prompt_do_usuario(metas, False)  # o WhatsApp não tem card
    assert "total_no_card" in sp.ADENDO_CARDS and "total_no_card" in sp.ADENDO_CARDS_METAS


def test_show_card_descreve_contas_como_o_saldo_total():
    from core.services.ai_chat.tools import mostrar_card as mc
    for schema in (mc.SCHEMA, mc.SCHEMA_METAS):
        assert "contas (saldo total e por conta: 'meu saldo')" in schema["function"]["description"]


# ── a recusa só vale quando o card ainda resolve: vaga e card-alvo ───────────────────────────────

def test_conversa_card_alvo_ja_usado_o_resumo_devolve_o_dado_e_o_repetido_aponta_um_caminho_que_existe(mundo, monkeypatch):
    """`show_card resumo [M0]` → `get_period_summary` de M: o `resumo` já está na tela, a recusa mandaria a ele de
    novo (`card_repetido`) e o número não sairia por caminho nenhum: a tool devolve o dado."""
    a, _ = mundo
    datas = {"start_date": M + "-01", "end_date": ULTIMO.isoformat()}
    openai_falso(monkeypatch,
                 rodada(None, ("show_card", {"card": "resumo", "meses": [M0]})),
                 rodada(None, ("get_period_summary", datas)),
                 rodada(None, ("show_card", {"card": "resumo", "meses": [M]})),
                 rodada("Pronto."))
    executar(a, "mês a mês")
    _, resumo, repetido = _tool_msgs(a)[:3]
    assert "error" not in resumo and resumo["despesa"] > 0
    assert repetido["error"] == "card_repetido" and "show_card periodo" in repetido["message"]


def test_conversa_com_3_cards_usados_o_resumo_de_mes_passado_devolve_o_dado(mundo, monkeypatch):
    """Sem vaga, o `show_card` seguinte dá `limite_de_cards`: a recusa trancaria o número."""
    a, _ = mundo
    datas = {"start_date": M + "-01", "end_date": ULTIMO.isoformat()}
    openai_falso(monkeypatch,
                 rodada(None, *[("show_card", {"card": c}) for c in ("contas", "fatura", "parcelas")]),
                 rodada(None, ("get_period_summary", datas), ("show_card", {"card": "resumo", "meses": [M]})),
                 rodada("Pronto."))
    executar(a, "tudo")
    *_, resumo, card = _tool_msgs(a)
    assert "error" not in resumo and resumo["despesa"] > 0 and card["error"] == "limite_de_cards"


TRES = ("get_period_summary", "get_top_categories", "get_category_spend")


def _chama(nome, a, periodo, usados):
    """A tool no app, com os cards do turno em `usados`. `um`: M0 inteiro; `varios`: M0 a M."""
    fim = INICIO - timedelta(days=1) if periodo == "um" else ULTIMO
    args = {"start_date": PRIMEIRO_M0.isoformat(), "end_date": fim.isoformat(),
            **({"categoria": "mercado"} if nome == "get_category_spend" else {})}
    t = CARDS_DO_TURNO.set(usados)
    try:
        return com_emissor(get_tool(nome).execute, a, args)
    finally:
        CARDS_DO_TURNO.reset(t)


@pytest.mark.parametrize("usados,periodo,dado", [
    (["contas", "fatura", "parcelas"], "um", set(TRES)),                # sem vaga: o show_card daria limite_de_cards
    (["contas", "fatura", "parcelas"], "varios", set(TRES)),
    (["resumo"], "um", {"get_period_summary"}),                         # o alvo do resumo já está na tela; o das irmãs não
    (["categorias"], "um", {"get_top_categories", "get_category_spend"}),
    (["periodo"], "varios", set(TRES)),
    (["resumo", "categorias"], "varios", set()),                        # card de 1 mês não libera o `periodo`
    (["periodo"], "um", set()),                                         # nem o `periodo` libera o de 1 mês
    (["contas"], "um", set()),                                          # positivo: ainda há vaga e o alvo está livre
    ([], "um", set()),
    ([], "varios", set()),
    (None, "um", set()),                                                # fora do runner (sem lista do turno)
])
def test_recusa_so_se_o_card_ainda_resolve(mundo, usados, periodo, dado):
    a, _ = mundo
    for nome in TRES:
        r = _chama(nome, a, periodo, None if usados is None else list(usados))
        assert ("error" not in r) == (nome in dado), (nome, r)


def test_recusa_de_1_mes_leva_o_aviso_do_fim_ajustado_como_a_de_varios_meses(mundo):
    """`end` no futuro é cortado no fim do mês atual (`_limita_periodo`): a mensagem diz isso, em 1 mês também."""
    a, _ = mundo
    hoje = date.today()
    args = {"start_date": hoje.replace(day=1).isoformat(), "end_date": (hoje + timedelta(days=100)).isoformat()}
    for nome in TRES:
        extra = {"categoria": "mercado"} if nome == "get_category_spend" else {}
        r = com_emissor(get_tool(nome).execute, a, {**args, **extra})
        assert r["error"] == "mes_inteiro_no_card" and "fim do mês atual" in r["message"], (nome, r)


# ── sem saída pelo card, o dado: conta sem acesso ao app e show_card que falhou no turno ────────────

def test_conversa_conta_sem_onboarding_o_resumo_devolve_o_dado(pro_user_id, monkeypatch):
    """Sem onboarding de OF o `show_card` responde `open_finance_onboarding_required` ("sem inventar números"):
    recusar a tool antes disso era beco sem saída."""
    datas = {"start_date": M + "-01", "end_date": ULTIMO.isoformat()}
    openai_falso(monkeypatch,
                 rodada(None, ("get_period_summary", datas)),
                 rodada(None, ("show_card", {"card": "resumo", "meses": [M]})),
                 rodada("Pronto."))
    executar(pro_user_id, "quanto gastei em setembro?")
    resumo, card = _tool_msgs(pro_user_id)[:2]
    assert card["error"] == "open_finance_onboarding_required"  # a premissa: o card não resolve para esta conta
    assert "error" not in resumo and "despesa" in resumo


def _montar_que_levanta(exc):
    def montar(*a, **k):
        raise exc
    return montar


# Falha do PRÓPRIO card-alvo (`resumo`) libera o dado; falha de outro card (`simulador`, `pro_required`) NÃO libera: o controle
# positivo. O `sem_plano_ativo` (402) fica de fora porque a tool já devolve o dado antes, pelo `exigir_acesso_do_app`.
@pytest.mark.parametrize("esperado,exc,libera", [
    ("pro_required", None, False),                               # real: `simulador` num Plus; o `resumo` segue recusado
    ("http_503", HTTPException(503), True),                      # HTTPException sem `error` no detalhe
    ("indisponivel", CardIndisponivel("ainda não"), True),
    ("falha_interna", RuntimeError("boom"), True),               # exceção genérica: o runner a vira `falha_interna`
    ("falha_interna", KeyError("campo"), True),
])
def test_conversa_show_card_que_falhou_no_turno_libera_so_a_tool_do_mesmo_card(monkeypatch, esperado, exc, libera):
    """Conta com acesso: a 1ª chamada é recusada (positivo); o `show_card` falha; a repetição devolve o dado só
    se o card que falhou é o da recusa (`resumo`, do `get_period_summary` de 1 mês)."""
    from core.services.ai_chat.tools import mostrar_card
    uid = completo(usuario_pagante(plan="plus"))
    if exc:
        monkeypatch.setattr(mostrar_card, "montar", _montar_que_levanta(exc))
    datas = {"start_date": M + "-01", "end_date": ULTIMO.isoformat()}
    card = {"card": "simulador", "meses": [M]} if exc is None else {"card": "resumo", "meses": [M]}
    openai_falso(monkeypatch,
                 rodada(None, ("get_period_summary", datas)),
                 rodada(None, ("show_card", card)),
                 rodada(None, ("get_period_summary", datas)),
                 rodada("Pronto."))
    executar(uid, "quanto gastei em setembro?")
    recusa, falha, depois = _tool_msgs(uid)[:3]
    assert recusa["error"] == "mes_inteiro_no_card" and falha["error"] == esperado
    if libera:
        assert "error" not in depois and "despesa" in depois
    else:
        assert depois["error"] == "mes_inteiro_no_card"


def test_falha_do_periodo_libera_o_resumo_de_varios_meses_e_nao_o_de_1_mes(mundo):
    """O card-alvo depende do período: 2 a 12 meses = `periodo`, 1 mês = `resumo`."""
    a, _ = mundo
    t = FALHAS_DE_CARD_DO_TURNO.set(["periodo"])
    try:
        assert "error" not in _chama("get_period_summary", a, "varios", [])
        assert _chama("get_period_summary", a, "um", [])["error"] == "mes_inteiro_no_card"
    finally:
        FALHAS_DE_CARD_DO_TURNO.reset(t)


def test_falha_do_renda_libera_o_dado_do_mes_em_texto_sem_abrir_o_resumo(mundo):
    """"Quanto entrou em setembro" num plano sem `renda` (Plus+): o `get_period_summary` devolve o dado, não manda ao `resumo`
    (que só mostra gasto). Controle: sem a falha do `renda`, o mesmo pedido segue indo ao card."""
    a, _ = mundo
    assert _chama("get_period_summary", a, "um", [])["error"] == "mes_inteiro_no_card"
    t = FALHAS_DE_CARD_DO_TURNO.set(["renda"])
    try:
        assert "error" not in _chama("get_period_summary", a, "um", [])
    finally:
        FALHAS_DE_CARD_DO_TURNO.reset(t)


# ── a fatura só perde o total quando o `show_card fatura` ainda pode aparecer ────────────────────

def _ids(*cards):
    return [("show_card", {"card": c}) for c in cards]


@pytest.mark.parametrize("antes,exc,onboarding,total", [
    ((), None, True, False),                                              # positivo: acesso, vaga e card livres = aponta o card
    ((), None, False, True),                                              # sem onboarding o card recusa
    (("fatura",), CardIndisponivel("ainda não"), True, True),             # o show_card fatura falhou no turno
    (("fatura",), RuntimeError("boom"), True, True),
    (("fatura",), None, True, True),                                      # o card fatura já está na tela
    (("contas", "parcelas", "patrimonio"), None, True, True),             # as 3 vagas gastas (e não pela fatura)
    (("contas",), None, True, False),                                     # outro card na tela não libera
])
def test_conversa_get_open_bill_so_tira_o_total_se_o_card_fatura_resolve(mundo, monkeypatch, antes, exc, onboarding, total):
    from core.services.ai_chat.tools import mostrar_card
    a, _ = mundo
    uid = a
    if not onboarding:
        uid = usuario_pagante(plan="pro_max")  # sem `completo`: o gate do app recusa
        db.add_credit_purchase(uid, db.create_card(uid, "Nubank", 10, 17), 100, "lazer", "show", today_tz())
    if exc:
        monkeypatch.setattr(mostrar_card, "montar", _montar_que_levanta(exc))
    openai_falso(monkeypatch,
                 *([rodada(None, *_ids(*antes))] if antes else []),
                 rodada(None, ("get_open_bill", {"card_name": "Nubank"})),
                 rodada("Pronto."))
    executar(uid, "quanto está minha fatura do Nubank?")
    fatura = _tool_msgs(uid)[-1]
    if total:
        assert "error" not in fatura and fatura["total"] > 0 and {"paid_amount", "remaining"} <= set(fatura)
    else:
        assert fatura["error"] == "total_no_card" and not {"total", "paid_amount", "remaining"} & set(fatura)


def test_get_open_bill_no_whatsapp_segue_devolvendo_o_total(mundo, monkeypatch):
    a, _ = mundo
    vistas = []

    def modelo(messages):
        vistas.append([json.loads(m["content"]) for m in messages if m["role"] == "tool"][-1])
        return texto("🐷 Foi isso.")

    falso_whatsapp(monkeypatch, com_tools(chamada("get_open_bill", {"card_name": "Nubank"})), modelo)
    diga(a, "piggy quanto está minha fatura do Nubank?")
    assert "error" not in vistas[-1] and vistas[-1]["total"] > 0


# ── as caixinhas só perdem o progresso quando o card (`metas` com a chave, senão `caixinhas`) ainda pode aparecer ──

def _conta_com_meta(monkeypatch, chave, onboarding=True):
    uid = usuario_pagante(plan="pro_max")
    uid = completo(uid) if onboarding else uid
    db.add_launch_and_update_balance(uid, "receita", 1000.0, None, "seed")
    db.create_pocket(uid, "viagem")
    update_pocket_meta(uid, db.list_pockets(uid)[0]["id"], target_amount=1000.0, target_date="2999-01-01")
    monkeypatch.setenv("OF_CASH_ENABLED", "1")
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(uid) if chave else "")  # depois de semear (a chave recusa escrita manual)
    return uid


@pytest.mark.parametrize("chave,antes,exc,onboarding,aparado", [
    (False, (), None, True, True),                                  # positivo: sem a chave, card `caixinhas` livre
    (True, (), None, True, True),                                   # positivo: com a chave, card `metas` livre
    (False, (), None, False, False),                                # sem onboarding o card recusa
    (False, ("caixinhas",), CardIndisponivel("ainda não"), True, False),   # o card falhou no turno
    (True, ("metas",), RuntimeError("boom"), True, False),
    (False, ("caixinhas",), None, True, False),                     # o card já está na tela
    (False, ("metas",), None, True, False),                         # sem a chave o `metas` vira `caixinhas`: o id remapeado é o que vale
    (True, ("metas",), None, True, False),
    (True, ("caixinhas",), None, True, True),                       # com a chave o card é `metas`: `caixinhas` na tela não libera
])
def test_conversa_list_pockets_so_tira_o_progresso_se_o_card_resolve(monkeypatch, chave, antes, exc, onboarding, aparado):
    from core.services.ai_chat.tools import mostrar_card
    uid = _conta_com_meta(monkeypatch, chave, onboarding)
    if exc:
        monkeypatch.setattr(mostrar_card, "montar", _montar_que_levanta(exc))
    openai_falso(monkeypatch,
                 *([rodada(None, *_ids(*antes))] if antes else []),
                 rodada(None, ("list_pockets", {})),
                 rodada("Pronto."))
    executar(uid, "como está minha caixinha da viagem?")
    (p,) = _tool_msgs(uid)[-1]["pockets"]
    assert p["target_amount"] == 1000.0
    assert ("progress_pct" not in p) == aparado and ("monthly_needed" not in p) == aparado


def test_list_pockets_no_whatsapp_segue_com_o_insight(monkeypatch):
    uid = _conta_com_meta(monkeypatch, chave=False)
    vistas = []

    def modelo(messages):
        vistas.append([json.loads(m["content"]) for m in messages if m["role"] == "tool"][-1])
        return texto("🐷 Foi isso.")

    falso_whatsapp(monkeypatch, com_tools(chamada("list_pockets", {})), modelo)
    diga(uid, "piggy como está minha caixinha da viagem?")
    assert "progress_pct" in vistas[-1]["pockets"][0]


# ── mensagem do card_repetido certa por card ─────────────────────────────────────────────────────

@pytest.mark.parametrize("card,args,deve,nao_deve", [
    ("resumo", {}, ("show_card periodo", "categorias com meses=["), ()),             # os de 1 mês
    ("calendario", {}, ("show_card periodo", "categorias com meses=["), ()),
    ("simulador", {"meses": [M]}, ("show_card periodo", "categorias com meses=["), ()),  # Pro: a conta do `mundo` é pro_max
    ("categorias", {}, ("get_top_categories",), ("show_card periodo", "meses=[...]")),  # o outro mês: tool de texto
    ("periodo", {"de": M0, "ate": M}, ("compare_periods",), ("meses=[",)),
    ("contas", {}, ("resultado anterior",), ("meses", "show_card", "periodo")),      # os demais
    ("fatura", {}, ("resultado anterior",), ("meses", "show_card", "periodo")),
    ("parcelas", {}, ("resultado anterior",), ("meses", "show_card", "periodo")),
    ("hero", {}, ("resultado anterior",), ("meses", "show_card", "periodo")),
])
def test_card_repetido_aponta_o_caminho_do_card(mundo, card, args, deve, nao_deve):
    a, _ = mundo
    t = CARDS_DO_TURNO.set([])
    try:
        assert "error" not in mostrar(a, card=card, **args)[0], card
        r, blocos, _ = mostrar(a, card=card, **args)
    finally:
        CARDS_DO_TURNO.reset(t)
    assert r["error"] == "card_repetido" and not blocos
    assert all(x in r["message"] for x in deve) and not any(x in r["message"] for x in nao_deve), r["message"]


# ── conta com corte de histórico: 1 mês cortado devolve o dado; vários meses vão ao card `periodo` ──

def _cortando(monkeypatch, corte):
    from core.services import plan_service
    from db import mes_detalhes
    for m in (plan_service, mes_detalhes):
        monkeypatch.setattr(m, "history_earliest_date", lambda *a, **k: corte)


@pytest.mark.parametrize("nome", TRES)
def test_corte_do_plano_em_1_mes_devolve_o_dado_com_historico_desde_e_em_varios_vai_ao_card(mundo, monkeypatch, nome):
    a, _ = mundo
    corte = PRIMEIRO_M0 + timedelta(days=1)
    _cortando(monkeypatch, corte)
    r = _chama(nome, a, "um", [])
    assert "error" not in r and r["historico_desde"] == corte.isoformat(), r  # o card `resumo` não traz; o dado sim
    assert _chama(nome, a, "varios", [])["error"] == "periodo_no_card"        # o `periodo` traz `historico_desde`
    # positivo: sem corte, o mesmo mês vai ao card
    _cortando(monkeypatch, None)
    assert _chama(nome, a, "um", [])["error"] == "mes_inteiro_no_card"


@pytest.mark.parametrize("nome", TRES)
def test_corte_exatamente_no_dia_1_do_mes_nao_corta_nada_e_o_mes_vai_ao_card(mundo, monkeypatch, nome):
    """`corte > start`, não `>=`: com o corte no dia 1 o card mostra o mês inteiro."""
    a, _ = mundo
    _cortando(monkeypatch, PRIMEIRO_M0)
    assert _chama(nome, a, "um", [])["error"] == "mes_inteiro_no_card"


def test_resumo_no_app_com_plano_essencial_real_segue_a_mesma_regra():
    """Conta Essencial de verdade (90 dias): o mês que contém o corte devolve o dado; vários meses vão ao card."""
    from core.services.plan_service import history_earliest_date
    ex = get_tool("get_period_summary").execute
    uid = completo(usuario_pagante(plan="essencial"))
    corte = history_earliest_date(uid, now_tz())
    ini = corte.replace(day=1)
    fim = (ini + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    r = com_emissor(ex, uid, {"start_date": ini.isoformat(), "end_date": fim.isoformat()})
    if corte > ini:
        assert "error" not in r and r["historico_desde"] == corte.isoformat() and "Limite do plano" in r["nota"]
        assert r == ex(uid, {"start_date": ini.isoformat(), "end_date": fim.isoformat()})  # o mesmo dado do WhatsApp
    else:
        assert r["error"] == "mes_inteiro_no_card"
    varios = {"start_date": _mes(-5) + "-01", "end_date": now_tz().date().isoformat()}
    assert com_emissor(ex, uid, varios)["error"] == "periodo_no_card"
