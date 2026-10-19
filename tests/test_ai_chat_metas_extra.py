"""PR C1 (passada 1 do Tester): o que a IA vê é POR USUÁRIO, o texto do ritmo, o dinheiro negativo e
os mutantes que sobreviveram. Texto e tool: `test_ai_chat_metas.py`.

Quem NÃO tem a chave `dashboard_v2_enabled` (N) recebe schemas e prompt idênticos aos de antes das
metas; só quem a tem (K) vê `list_metas`, o card `metas` novo e o prompt de metas (P1-b). O pytest NÃO
prova o roteamento do modelo (isso é o harness real, no relato do PR): prova quem recebe o quê.
"""
import json
import re
from decimal import Decimal

import pytest

from _cards_apoio import mostrar  # noqa: F401
from _ia_stream_helpers import executar, openai_falso, rodada
from conftest import usuario_pagante
from core.services import ai_guard, metas_conversa as mc
from core.services import plan_service
from core.services.ai_chat import cards, runner, streaming, system_prompt as sp
from core.services.ai_chat._context import CARDS_DO_TURNO
from core.services.ai_chat.tools import SCHEMAS, SCHEMAS_APP, get_tool, schemas_para
from core.services.ai_chat.tools.metas import schemas_com_metas
from test_ai_chat_metas import conversa, dois, lista, meta_de  # noqa: F401  (dois é fixture)
from test_api_app_sessao import completo
from utils_date import today_tz


def txt(**campos):
    return conversa(meta_de("Viagem pro Japão", **campos))["metas"][0]["resumo"]


# ── o que cada usuário recebe ──────────────────────────────────────────────────────────────────

@pytest.fixture
def kn(monkeypatch):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    k, n = (completo(usuario_pagante(plan="pro_max")) for _ in range(2))
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(k))
    return k, n


def nomes(s):
    return [x["function"]["name"] for x in s]


def card_de(s):
    (sc,) = [x["function"] for x in s if x["function"]["name"] == "show_card"]
    return sc["parameters"]["properties"]["card"]["enum"], sc["description"]


def descricao(s, nome):
    return [x["function"]["description"] for x in s if x["function"]["name"] == nome][0]


def confere_quem_recebe_o_que(k, n):
    assert schemas_para(n, False) is SCHEMAS and schemas_para(n, True) is SCHEMAS_APP  # N: os de antes
    assert "list_metas" not in nomes(SCHEMAS) and "list_metas" not in nomes(SCHEMAS_APP)
    enum, desc = card_de(SCHEMAS_APP)
    assert "caixinhas" not in enum and "metas (caixinhas com meta)" in desc
    assert "tô no ritmo?" in descricao(SCHEMAS, "list_pockets")
    # K: wa e /ai/chat ganham `list_metas`; o app não (o número vem do card). Criar/editar/apagar (C4): os dois canais
    escrita = ["create_meta", "edit_meta", "delete_meta"]
    assert nomes(schemas_para(k, False)) == nomes(SCHEMAS) + ["list_metas", *escrita]
    assert nomes(schemas_para(k, True)) == nomes(SCHEMAS_APP) + escrita
    enum, desc = card_de(schemas_para(k, True))
    assert enum == list(cards.IDS) and "caixinhas (caixinhas e cofrinhos)" in desc
    assert "tô no ritmo?" not in descricao(schemas_para(k, False), "list_pockets")
    assert "list_metas" in descricao(schemas_para(k, False), "list_pockets")


def test_schemas_por_usuario(kn):
    confere_quem_recebe_o_que(*kn)


def test_controle_negativo_chave_sempre_ligada_deixa_o_teste_vermelho(kn, monkeypatch):
    monkeypatch.setattr(mc, "metas_ligadas", lambda uid: True)
    with pytest.raises(AssertionError):
        confere_quem_recebe_o_que(*kn)


def test_prompt_por_usuario():
    for p in (sp.SYSTEM_PROMPT, sp.SYSTEM_PROMPT_METAS, sp.SYSTEM_PROMPT_METAS_APP, sp.ADENDO_CARDS, sp.ADENDO_CARDS_METAS):
        assert "@@" not in p.replace("@@ANO@@", "")  # marcador de troca que vazou (o do ano só se troca em `prompt_do_usuario`)
    assert all("@@" not in sp.prompt_do_usuario(m, a) for m in (False, True) for a in (False, True))
    sem = sp.prompt_do_usuario(False, True)
    assert "list_metas" not in sem and "metas (caixinhas com meta)" in sem  # N: o de antes das metas
    assert sp.prompt_do_usuario(False, False) == sp.SYSTEM_PROMPT
    com = sp.prompt_do_usuario(True, False)  # K, WhatsApp e /ai/chat: a tool
    assert "→ `list_metas`" in com and "CARDS NO APP" not in com
    app = sp.prompt_do_usuario(True, True)  # K, app: o prompt nem cita a tool oculta como destino
    assert '→ `show_card` com `card="metas"`' in app and "→ `list_metas`" not in app and "CARDS NO APP" in app


@pytest.mark.parametrize("app", [False, True])
def test_o_runner_monta_o_turno_por_usuario(kn, monkeypatch, app):
    k, n = kn
    vistos = {}
    for quem, uid in (("K", k), ("N", n)):
        reg = openai_falso(monkeypatch, rodada("Oi!"))
        if app:
            executar(uid, "oi")
        else:
            assert runner.chat(uid, "oi", monthly_limit=100) == "Oi!"
        (kw,) = reg.creates
        vistos[quem] = (nomes(kw["tools"]), kw["messages"][0]["content"])
    assert ("list_metas" in vistos["K"][0]) is (not app) and "list_metas" not in vistos["N"][0]
    assert vistos["N"][1].startswith(sp.prompt_do_usuario(False, app)) and vistos["K"][1].startswith(sp.prompt_do_usuario(True, app))
    assert ("show_card" in vistos["K"][0]) is app


def test_na_tool_da_historia_e_no_card_metas_n_cai_nas_caixinhas(kn):
    k, n = kn
    assert "use_caixinhas" in lista(n) and "metas" in lista(k)
    _, (bloco,), _ = mostrar(n, card="metas")
    assert bloco["card"] == "caixinhas"
    _, (bloco,), _ = mostrar(k, card="metas")
    assert bloco["card"] == "metas"


# ── runner: a mensagem da tool oculta ──────────────────────────────────────────────────────────

BASE_OCULTA = "Não existe no app. Use show_card (contas, hero, fatura, parcelas, renda)."


def oculta(uid, nome="list_installments"):
    t = streaming.EMISSOR.set(lambda e: None)
    try:
        return json.loads(runner._dispatch_tool(uid, nome, {})[0])
    finally:
        streaming.EMISSOR.reset(t)


def test_oculta_no_app_cita_so_os_cards_que_o_usuario_tem(kn):
    """N recebe EXATAMENTE a mensagem de antes das metas (`caixinhas` nem está no enum dele); K, os cards de metas."""
    k, n = kn
    assert oculta(n) == {"error": "indisponivel_no_app", "message": BASE_OCULTA}
    assert oculta(k, "list_metas")["message"] == BASE_OCULTA[:-2] + ", metas, caixinhas)."


# ── K no app: nenhum texto manda para `list_metas` (oculta lá) ────────────────────────────────────

def test_list_pockets_de_k_no_app_manda_para_o_card_e_no_whatsapp_para_a_tool():
    app = descricao(schemas_com_metas(SCHEMAS_APP, True), "list_pockets")
    assert "list_metas" not in app and "show_card" in app
    assert "list_metas" in descricao(schemas_com_metas(SCHEMAS, False), "list_pockets")  # positivo: wa e /ai/chat


def test_nada_do_que_k_le_no_app_cita_list_metas_fora_da_lista_das_ocultas():
    prompt = sp.prompt_do_usuario(True, True)
    linha = next(l for l in prompt.splitlines() if l.startswith("- No app NÃO existem"))
    assert "list_metas" in linha  # controle: a única citação é "ignore-as"
    resto = prompt.replace(linha, "") + json.dumps(schemas_com_metas(SCHEMAS_APP, True), ensure_ascii=False)
    assert "list_metas" not in resto


def test_o_texto_de_n_e_a_base_com_o_marcador_trocado_pela_caixinha():
    """N: nenhum byte de metas. Sem hash de texto: o prompt é o `_BASE` com o marcador trocado, e só."""
    base = sp._BASE.replace("@@CAIXINHAS@@\n", sp._CAIXINHAS)
    assert sp.prompt_do_usuario(False, False) == base
    app = re.sub(r"@@ANO@@-(\d\d)", lambda m: f"{sp.ano_do_mes(today_tz(), int(m[1]))}-{m[1]}", base + sp.ADENDO_CARDS)
    assert sp.prompt_do_usuario(False, True) == app.replace("1 emoji só (🐷 no início). Não abuse.", "Sem emoji.").replace("🐷 ", "").replace(" 🎉", "")  # o app não mostra o 🐷 nem o 🎉
    assert "list_metas" not in base + sp.ADENDO_CARDS


# ── dinheiro negativo, zero e o ritmo como saldo ────────────────────────────────────────────────

def test_brl_negativo_e_zero():
    assert mc._brl(Decimal("-250")) == "R$ -250,00"  # a forma do fmt_brl e do ai_guard
    assert mc._brl(Decimal("-0.004")) == "R$ 0,00" and mc._brl(Decimal("0.005")) == "R$ 0,01"
    assert mc._brl(Decimal("1234.5")) == "R$ 1.234,50"


def test_ritmo_fala_do_saldo_da_meta_com_o_sentido_em_palavra():
    cresce = txt(ritmo={"valor": "300.00", "parcial": False}, chega_em=None)
    assert "O saldo da meta está crescendo R$ 300,00 por mês (contando rendimento e variação)" in cresce
    cai = txt(ritmo={"valor": "-300.00", "parcial": False}, chega_em=None)
    assert "O saldo da meta está diminuindo R$ 300,00 por mês" in cai and cai.endswith("a meta não anda.")
    quase = txt(ritmo={"valor": "-0.004", "parcial": False}, chega_em=None)  # arredonda para 0,00
    assert "O saldo da meta está parado (" in quase and "R$ -0,00" not in quase and quase.endswith("a meta não anda.")
    for t in (cresce, cai, quase):  # o modelo não pode ler o ritmo como poupança
        assert "Seu ritmo" not in t and "guard" not in t.lower() and "poup" not in t.lower()


def test_o_guard_aceita_a_copia_do_resumo_com_ritmo_negativo(pro_user_id):
    m = meta_de("Notebook novo")
    r = conversa(m)
    ev = ai_guard.tool_results([{"role": "tool", "content": json.dumps(r, ensure_ascii=False)}])
    assert all(c.supported for c in ai_guard.check("O saldo está diminuindo R$ 250,00 por mês", ev))
    assert not all(c.supported for c in ai_guard.check("O saldo está diminuindo R$ 350,00 por mês", ev))
    # limite declarado (docs): o guard é cego a percentual, mês e sinal; a palavra (crescendo x diminuindo) é do prompt


# ── os mutantes que sobreviveram ───────────────────────────────────────────────────────────────

def test_mata_decimal_str_no_caminho_do_card():  # `Decimal(x)` no lugar de `Decimal(str(x))`
    assert txt(atual=1.005, falta=98.995, alvo=100.0, percentual=1, por_mes=None).startswith("R$ 1,01 de R$ 100,00")


def test_mata_chega_no_mes_do_prazo_e_dentro():  # `<=` virando `<`
    assert "chega em junho de 2027, dentro do prazo" in txt(chega_em="2027-06")


def test_mata_ritmo_zero_a_meta_nao_anda():  # `v <= 0` virando `v < 0`
    assert txt(ritmo={"valor": "0.00", "parcial": False}, chega_em=None).endswith("Nesse ritmo, a meta não anda.")


def test_mata_um_mes_no_singular():
    assert "(1 mês de contas fixas)" in txt(tipo="reserva", meses=1)


def test_mata_passo_da_tool():
    assert streaming._PASSOS["list_metas"] == "Vendo suas metas"


def test_mata_sufixo_do_piggy_nao_vaza_para_outros_cards(kn, monkeypatch):
    k, _ = kn
    r, blocos, _ = mostrar(k, card="piggy")  # sem o cache: a dica "ainda não está pronta" leva o sufixo
    assert r["error"] == "indisponivel" and "Diga que ainda não estão prontas" in r["message"] and not blocos
    real = cards.registro()

    def recusa(uid, a):
        raise cards.CardIndisponivel("so isto")

    monkeypatch.setattr(cards, "registro", lambda: {**real, "contas": cards.Card(real["contas"].ler, real["contas"].visao, recusa)})
    assert mostrar(k, card="contas")[0]["message"] == "so isto"  # a mensagem é crua: o sufixo é do piggy


def test_mata_m10_alias_antes_do_card_repetido(kn):
    _, n = kn
    t = streaming.EMISSOR.set(lambda e: None)
    tk = CARDS_DO_TURNO.set([])
    try:
        a = get_tool("show_card").execute(n, {"card": "metas"})
        b = get_tool("show_card").execute(n, {"card": "metas"})
    finally:
        CARDS_DO_TURNO.reset(tk)
        streaming.EMISSOR.reset(t)
    assert a["card"] == "caixinhas" and b["error"] == "card_repetido"


def test_mata_m04_m24_adendo_do_app_de_K_troca_o_que_a_base_diz_de_metas():
    k_app = sp.prompt_do_usuario(True, True)
    assert "metas (caixinhas com meta)" not in k_app and "progresso, ritmo e quanto falta = card `metas`." not in k_app
    assert "metas (metas e reserva de emergência) · caixinhas (caixinhas e cofrinhos)" in k_app
    assert "Criar meta: `create_meta` grava na hora" in k_app and "nem `list_metas`" in k_app
    assert sp.ADENDO_CARDS_METAS != sp.ADENDO_CARDS


# ── a chave das metas é lida UMA vez por turno (pii_access_log a cada leitura sem cache) ─────────
@pytest.mark.parametrize("quem", ["K", "N"])
def test_turno_de_tres_voltas_le_a_chave_uma_vez(kn, monkeypatch, quem):
    k, n = kn
    real, lidas = plan_service.dashboard_v2_enabled, []
    monkeypatch.setattr(plan_service, "dashboard_v2_enabled", lambda uid: lidas.append(uid) or real(uid))
    usa = ("get_period_summary", {"period": "month"})
    reg = openai_falso(monkeypatch, rodada(None, usa), rodada(None, usa), rodada("Pronto."))
    assert runner.chat(k if quem == "K" else n, "oi", monthly_limit=100) == "Pronto."
    assert len(reg.creates) == 3 and len(lidas) == 1  # prompt, tools= e as 3 voltas com a mesma leitura
    for kw in reg.creates:  # positivo: K recebe prompt e tools de metas em toda volta; N a base
        assert ("list_metas" in nomes(kw["tools"])) is (quem == "K")
        assert kw["messages"][0]["content"].startswith(sp.prompt_do_usuario(quem == "K", False))


@pytest.mark.parametrize("quem", ["K", "N"])
def test_turno_do_app_le_a_chave_uma_vez_mesmo_com_card_e_tool_oculta(kn, monkeypatch, quem):
    """`show_card` metas, `list_metas` oculta e `list_pockets` no mesmo turno: prompt, tools, gate do card e
    mensagem da oculta usam a leitura do turno (nenhum relê a chave, nem pelo cache de 10 s)."""
    k, n = kn
    real, lidas = plan_service.dashboard_v2_enabled, []
    monkeypatch.setattr(plan_service, "dashboard_v2_enabled", lambda uid: lidas.append(uid) or real(uid))
    reg = openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "metas"}), ("list_metas", {}),
                                          ("get_balance", {})), rodada("Pronto."))
    r, eventos, _ = executar(k if quem == "K" else n, "como estão minhas metas")
    assert len(reg.creates) == 2 and len(lidas) == 1
    (bloco,) = [e["bloco"] for e in eventos if e.get("tipo") == "bloco"]
    assert bloco["card"] == ("metas" if quem == "K" else "caixinhas")  # positivo: o card certo por usuário


def test_turno_do_chat_le_a_chave_uma_vez_com_list_metas(kn, monkeypatch):
    k, _ = kn
    real, lidas = plan_service.dashboard_v2_enabled, []
    monkeypatch.setattr(plan_service, "dashboard_v2_enabled", lambda uid: lidas.append(uid) or real(uid))
    reg = openai_falso(monkeypatch, rodada(None, ("list_metas", {})), rodada("Pronto."))
    assert runner.chat(k, "como estão minhas metas", monthly_limit=100) == "Pronto."
    assert len(reg.creates) == 2 and len(lidas) == 1
    assert "sem_metas" in reg.creates[1]["messages"][-1]["content"]  # positivo: a tool rodou como K


# ── custo com leitura velha: o alvo da reserva não sai como exato (espelha `fraseCusto` do .ts) ──

VELHO = " Não consegui ler as cobranças do banco agora. O valor pode estar velho."
CUSTO_VELHO = {"valor": "3000.00", "recorrencias": 5, "motivos": ["recorrencias_banco_nao_lidas"]}


def _resumo_e_avisos(nome, custo):
    (i,) = conversa(meta_de(nome), custo=custo)["metas"]
    return i["resumo"], i["avisos"]


def test_reserva_com_custo_velho_avisa_no_resumo_sem_duplicar_nos_avisos():
    r, avisos = _resumo_e_avisos("Reserva de emergência", CUSTO_VELHO)
    assert "faltam R$ 13.500,00." in r and r.endswith("." + VELHO) and r.count("velho") == 1 and avisos == []


def test_reserva_com_custo_em_dia_e_meta_comum_nao_avisam():  # positivos: o aviso não vaza
    assert "velho" not in _resumo_e_avisos("Reserva de emergência", {**CUSTO_VELHO, "motivos": []})[0]
    assert "velho" not in _resumo_e_avisos("Viagem pro Japão", CUSTO_VELHO)[0]


def test_reserva_sem_alvo_segue_com_a_frase_do_custo():
    custo = {"valor": None, "recorrencias": 0, "motivos": ["recorrencias_banco_nao_lidas"]}
    m = meta_de("Reserva de emergência", alvo=None, falta=None, por_mes=None, percentual=None)
    (i,) = conversa(m, custo=custo)["metas"]
    assert i["resumo"].endswith("Ainda não sei o tamanho da reserva: " + mc.FRASE_CUSTO["recorrencias_banco_nao_lidas"])


# ── reserva sem saldo E sem tamanho: o resumo explica os dois (a tela mostra a frase do ritmo e a do custo) ──

E_TAMANHO = " E ainda não sei o tamanho da reserva: "


def _sem_saldo(nome="Reserva de emergência", custo=None, ritmo="sem_banco_conectado", **campos):
    custo = {"valor": None, "recorrencias": 0, "motivos": [custo] if custo else []}
    m = meta_de(nome, atual=None, ritmo={"motivos": [ritmo], "valor": None}, **campos)
    return conversa(m, custo=custo)["metas"][0]["resumo"]


def test_reserva_sem_saldo_e_sem_alvo_explica_o_custo():
    sem = dict(alvo=None, falta=None, por_mes=None, percentual=None)
    r = _sem_saldo(custo="plano_sem_custo_mensal", ritmo="fonte_indisponivel", **sem)
    assert r.endswith(mc.FRASE_RITMO["fonte_indisponivel"] + E_TAMANHO + mc.FRASE_CUSTO["plano_sem_custo_mensal"])
    r = _sem_saldo(custo="sem_banco_conectado", **sem)  # os dois motivos iguais, frases diferentes: as duas saem
    assert mc.FRASE_RITMO["sem_banco_conectado"] in r and r.endswith(E_TAMANHO + mc.FRASE_CUSTO["sem_banco_conectado"])
    assert E_TAMANHO.strip() in _sem_saldo(custo=None, **sem)  # sem motivo de custo: o fallback


def test_reserva_sem_saldo_nao_repete_frase_igual(monkeypatch):
    monkeypatch.setitem(mc.FRASE_CUSTO, "sem_banco_conectado", mc.FRASE_RITMO["sem_banco_conectado"])
    r = _sem_saldo(custo="sem_banco_conectado", alvo=None, falta=None, por_mes=None, percentual=None)
    assert r.count(mc.FRASE_RITMO["sem_banco_conectado"]) == 1 and "tamanho da reserva" not in r


def test_sem_saldo_sem_frase_do_tamanho_quando_o_alvo_existe_ou_e_meta_comum():  # positivos
    assert "tamanho da reserva" not in _sem_saldo(custo="plano_sem_custo_mensal")  # reserva com alvo
    assert "tamanho da reserva" not in _sem_saldo("Viagem pro Japão", custo="plano_sem_custo_mensal")
