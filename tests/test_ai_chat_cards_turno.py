"""PR B1: `show_card`, gates, entrada, teto por turno e eventos do stream.
Apoio e paridade: `_cards_apoio.py`, `test_ai_chat_cards.py`."""
import json
import time

import pytest

from _cards_apoio import CASOS, M, M0, mostrar, mundo  # noqa: F401  (mundo é fixture)
from _ia_stream_helpers import conta, eventos_sse, executar, linhas, openai_falso, rodada
from conftest import usuario_pagante
from core.services.ai_chat import cards, runner, streaming
from core.services.ai_chat.system_prompt import ADENDO_CARDS
from core.services.ai_chat.tools import SCHEMAS, SCHEMAS_APP, get_tool
from core.services.ai_chat.tools.mostrar_card import SCHEMA
from test_api_app_sessao import completo
from test_senha_obrigatoria import H
from utils_date import now_tz

# ── gates e entrada ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("card,plano", [("assinaturas", "essencial"), ("hero", "essencial"), ("compromissos", "essencial"),
                                         ("renda", "essencial"), ("piggy", "essencial"), ("simulador", "plus")])
def test_gate_de_plano_vira_erro_sem_bloco(card, plano):
    uid = completo(usuario_pagante(plan=plano))
    args = {"meses": [M]} if card == "simulador" else {}
    r, blocos, ev = mostrar(uid, card=card, **args)
    assert r["error"] == "pro_required" and not blocos and not ev


def test_gate_positivo_o_plano_certo_recebe_o_bloco(mundo):
    a, _ = mundo
    for card in ("assinaturas", "hero", "simulador", "renda", "piggy"):
        assert mostrar(a, card=card, **CASOS[card][0])[1], card


FUTURO = f"{now_tz().year + 1}-01"


@pytest.mark.parametrize("args,erro", [
    ({"card": "resumo", "meses": [FUTURO]}, "mes_invalido"),
    ({"card": "resumo", "meses": ["2026-13"]}, "mes_invalido"),
    ({"card": "resumo", "meses": ["outubro"]}, "mes_invalido"),
    ({"card": "resumo", "meses": "2026-09"}, "mes_invalido"),
    ({"card": "categorias", "meses": [M, M, M0]}, "mes_invalido"),
    ({"card": "categorias", "meses": [M, M0, "2026-01", "2025-12"]}, "meses_demais"),
    ({"card": "resumo", "meses": [M, M0]}, "meses_demais"),
    ({"card": "calendario", "meses": [M, M0]}, "meses_demais"),
    ({"card": "hero", "dias": "45"}, "dias_invalido"),
    ({"card": "renda", "quantidade_meses": 0}, "quantidade_invalida"),
    ({"card": "renda", "quantidade_meses": 25}, "quantidade_invalida"),
    ({"card": "inexistente"}, "card_desconhecido"),
    ({}, "card_desconhecido"),
])
def test_entrada_invalida_vira_erro_sem_bloco(mundo, args, erro):
    a, _ = mundo
    ev = []
    t = streaming.EMISSOR.set(ev.append)
    try:
        r = get_tool("show_card").execute(a, args)
    finally:
        streaming.EMISSOR.reset(t)
    assert r["error"] == erro and not ev


def test_mes_valido_e_tres_meses_em_categorias_passam(mundo):
    a, _ = mundo
    assert len(mostrar(a, card="categorias", meses=[M, M0, "2020-01"])[1][0]["abas"]) == 3
    assert mostrar(a, card="resumo")[1][0]["abas"][0]["mes"] == f"{now_tz():%Y-%m}"


def test_o_user_id_nunca_vem_dos_args(mundo):
    a, b = mundo
    ev = []
    t = streaming.EMISSOR.set(ev.append)
    try:
        r = get_tool("show_card").execute(a, {"card": "metas", "user_id": b, "uid": b})
    finally:
        streaming.EMISSOR.reset(t)
    assert "SEGREDO-B" not in json.dumps(ev) and "viagem" in json.dumps(ev)


# ── no runner: schemas, teto, ordem dos eventos ────────────────────────────────

def _nomes(kw):
    return {t["function"]["name"] for t in kw["tools"]}


def test_sem_emissor_nao_oferece_show_card_nem_o_adendo(monkeypatch, pro_user_id):
    reg = openai_falso(monkeypatch, rodada("Oi!"))
    assert runner.chat(pro_user_id, "oi", monthly_limit=100) == "Oi!"
    (kw,) = reg.creates
    assert kw["tools"] is SCHEMAS and "show_card" not in _nomes(kw)
    assert "show_card" not in kw["messages"][0]["content"]
    assert "show_card" not in {s["function"]["name"] for s in SCHEMAS}


def test_com_emissor_oferece_show_card_e_o_adendo(monkeypatch, pro_user_id):
    reg = openai_falso(monkeypatch, rodada("Oi!"))
    executar(pro_user_id, "oi")
    (kw,) = reg.creates
    assert kw["tools"] is SCHEMAS_APP and "show_card" in _nomes(kw)
    assert "show_card" in kw["messages"][0]["content"]
    assert [s for s in SCHEMAS_APP if s not in SCHEMAS][0]["function"]["parameters"]["properties"]["card"]["enum"] == list(cards.IDS)


def test_modelo_que_imita_show_card_no_whatsapp_recebe_dados_sem_bloco(monkeypatch, pro_user_id):
    completo(pro_user_id)
    reg = openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "contas"})), rodada("Pronto."))
    assert runner.chat(pro_user_id, "saldo", monthly_limit=100) == "Pronto."
    tool = [l for l in linhas(pro_user_id) if l["role"] == "tool"][0]
    assert json.loads(tool["content"])["card"] == "contas"  # só dados; nenhum emissor para o bloco


def test_teto_de_3_por_turno_e_ordem_previa_bloco(monkeypatch, mundo):
    a, _ = mundo
    chamadas = [("show_card", {"card": c}) for c in ("contas", "metas", "fatura", "parcelas")]
    openai_falso(monkeypatch, rodada(None, *chamadas), rodada("Aqui está."))
    r, ev, _ = executar(a, "me mostra tudo")
    tipos = [(e["tipo"], e.get("previa")) for e in ev if e["tipo"] in ("passo", "bloco")]
    assert tipos == [("passo", "contas"), ("bloco", None), ("passo", "metas"), ("bloco", None),
                     ("passo", "fatura"), ("bloco", None)]  # o 4º é recusado: nem prévia
    ferramentas = [json.loads(l["content"]) for l in linhas(a) if l["role"] == "tool"]
    assert [f.get("error") for f in ferramentas] == [None, None, None, "limite_de_cards"]
    # o teto é por turno: o turno seguinte volta a ter 3
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "contas"})), rodada("Ok."))
    _, ev2, _ = executar(a, "e o saldo?")
    assert [e["tipo"] for e in ev2 if e["tipo"] == "bloco"] == ["bloco"]


def test_endpoint_previa_bloco_fim_nessa_ordem(monkeypatch):
    uid, c = conta()
    completo(uid)
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "contas"})), rodada("Seu saldo está aí."))
    ev = eventos_sse(c.post("/ai/chat/stream", json={"message": "meu saldo"}, headers=H))
    tipos = [e["tipo"] for e in ev]
    assert tipos.index("passo") < tipos.index("bloco") < tipos.index("fim") and tipos[-1] == "fim"
    assert next(e for e in ev if e["tipo"] == "passo")["previa"] == "contas"
    bloco = next(e for e in ev if e["tipo"] == "bloco")["bloco"]
    assert bloco["card"] == "contas" and bloco["abas"][0]["recursos"]["contas"]["total"] in ("0", "0.00")


def test_card_inexistente_nao_manda_previa_ao_app(monkeypatch, pro_user_id):
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "<script>"})), rodada("Não deu."))
    _, ev, _ = executar(pro_user_id, "mostra")
    assert not [e for e in ev if e["tipo"] in ("passo", "bloco")]


def _previas_e_blocos(uid, **args):
    r, blocos, ev = mostrar(uid, **args)
    return r, [e["previa"] for e in ev if e["tipo"] == "passo"], blocos


def test_card_recusado_nao_manda_previa(mundo):
    """Plano, Pro, mês malformado, mês futuro, dias fora do plano e card sem dado pronto: nenhum
    recusado manda a prévia (o app ficaria com o esqueleto e nunca receberia o bloco)."""
    a, _ = mundo
    livre = completo(usuario_pagante(plan="essencial"))
    plus = completo(usuario_pagante(plan="plus"))
    recusas = [(livre, {"card": "hero"}), (livre, {"card": "assinaturas"}), (plus, {"card": "simulador"}),
               (plus, {"card": "hero", "dias": "60"}), (a, {"card": "resumo", "meses": ["2026-13"]}),
               (a, {"card": "resumo", "meses": [f"{now_tz().year + 1}-01"]}),
               (a, {"card": "simulador", "meses": [f"{now_tz().year + 1}-01"]})]
    for uid, args in recusas:
        r, previas, blocos = _previas_e_blocos(uid, **args)
        assert "error" in r and not previas and not blocos, (args, r)
    # positivo: o mesmo card, aceito, manda a prévia e depois o bloco
    r, previas, blocos = _previas_e_blocos(a, card="resumo", meses=[M])
    assert previas == ["resumo"] and len(blocos) == 1


def test_previa_vem_antes_do_bloco(mundo):
    a, _ = mundo
    _, _, ev = mostrar(a, card="metas")
    assert [e["tipo"] for e in ev] == ["passo", "bloco"] and ev[0]["previa"] == "metas"


def test_so_card_com_bloco_gasta_vaga_do_teto(mundo):
    a, _ = mundo
    runner.CARDS_DO_TURNO.set([])
    try:
        for _ in range(5):  # chamadas recusadas não gastam vaga
            assert mostrar(a, card="resumo", meses=["2026-13"])[0]["error"] == "mes_invalido"
        for card in ("contas", "metas", "fatura"):
            assert "error" not in mostrar(a, card=card)[0], card
        assert mostrar(a, card="parcelas")[0]["error"] == "limite_de_cards"
    finally:
        runner.CARDS_DO_TURNO.set(None)


def test_card_repetido_no_turno_e_recusado_sem_gastar_vaga(mundo):
    a, _ = mundo
    runner.CARDS_DO_TURNO.set([])
    try:
        assert "error" not in mostrar(a, card="contas")[0]
        r, blocos, ev = mostrar(a, card="contas")
        assert r["error"] == "card_repetido" and "meses" in r["message"] and not blocos and not ev
        # não gastou vaga: ainda cabem 2 cards diferentes (total 3) e só então o teto
        for card in ("metas", "fatura"):
            assert "error" not in mostrar(a, card=card)[0], card
        assert mostrar(a, card="parcelas")[0]["error"] == "limite_de_cards"
    finally:
        runner.CARDS_DO_TURNO.set(None)


def test_o_maximo_do_prompt_e_da_tool_vem_da_constante():
    frase = f"no máximo {cards.MAX_POR_TURNO} "
    assert frase in ADENDO_CARDS and frase in SCHEMA["function"]["description"]


def test_piggy_sem_cache_nao_gera_dentro_do_turno(monkeypatch, pro_user_id):
    completo(pro_user_id)
    """Sem o cache de 6 h, gerar seria uma chamada ao LLM (sem timeout) com a trava do turno na mão."""
    chamadas = []
    monkeypatch.setattr("core.ai_patterns._call_llm", lambda *a, **k: chamadas.append(1) or time.sleep(3) or [])
    openai_falso(monkeypatch, rodada(None, ("show_card", {"card": "piggy"})), rodada("As dicas ainda não estão prontas."))
    t = time.monotonic()
    r, ev, _ = executar(pro_user_id, "o que você notou?")
    assert time.monotonic() - t < 2 and not chamadas
    assert r.reply == "As dicas ainda não estão prontas." and not [e for e in ev if e["tipo"] in ("passo", "bloco")]
    (tool,) = [json.loads(l["content"]) for l in linhas(pro_user_id) if l["role"] == "tool"]
    assert tool["error"] == "indisponivel"
    openai_falso(monkeypatch, rodada("Oi de novo."))
    r2, _, _ = executar(pro_user_id, "e agora?")  # o 2º turno não espera a trava
    assert r2.reply == "Oi de novo."


# ── conta que o app recusa não recebe card ─────────────────────────────────────

def _conta_inativa():
    """Plano `free` (inativo) com escolha feita: passa no gate `ai_chat`, e o app responde 402."""
    from _apoio_auth_app import sql
    uid = completo(usuario_pagante(plan="pro_max"))
    sql("update auth_accounts set plan='free', plan_selected_at=now() where user_id=%s", uid)
    import db_support
    db_support.invalidate_auth_user_cache(uid)
    return uid


def _recusado_sem_prevista(uid, card):
    r, blocos, ev = mostrar(uid, card=card, **CASOS[card][0])
    assert r.get("error") == "sem_plano_ativo", (card, r)
    assert not blocos and not [e for e in ev if e["tipo"] in ("passo", "bloco")], card
    return r


@pytest.mark.parametrize("card", list(CASOS))
def test_conta_inativa_nao_recebe_card(card):
    r = _recusado_sem_prevista(_conta_inativa(), card)
    assert "escolher um plano" in r["message"]


def test_conta_inativa_controle_negativo(monkeypatch):
    """Sem a checagem de acesso do app, a mesma conta recebe bloco: o teste acima fica vermelho."""
    uid = _conta_inativa()
    monkeypatch.setattr(cards, "exigir_acesso_do_app", lambda uid: None)
    with pytest.raises(AssertionError):
        _recusado_sem_prevista(uid, "contas")


def test_conta_sem_onboarding_do_open_finance_tambem_e_recusada():
    from _apoio_auth_app import sql
    uid = usuario_pagante(plan="pro_max")
    sql("update auth_accounts set open_finance_onboarding_completed_at=null where user_id=%s", uid)
    r, blocos, ev = mostrar(uid, card="contas")
    assert r["error"] == "open_finance_onboarding_required" and not blocos and not ev


# ── o veredito do show_card é o do app, por estado de conta ───────────────────
# `exigir_acesso_do_app` repete as chamadas de `usuario_assinante` (que precisa do Request): este
# teste é o que o mantém igual. Se o app passar a recusar um estado novo e o card não, fica vermelho.

_UM_DIA = "now() + interval '1 day'"
ESTADOS = {
    "ativo": "",
    "trial": f"update auth_accounts set plan='pro', plan_expires_at={_UM_DIA} + interval '2 days' where user_id=%s",
    "so_google_sem_senha": "update auth_accounts set password_hash=null where user_id=%s",
    "inativo": "update auth_accounts set plan='free', plan_selected_at=now() where user_id=%s",
    "nunca_escolheu": "update auth_accounts set plan='free', plan_selected_at=null where user_id=%s",
    "expirado": "update auth_accounts set plan_expires_at=now() - interval '30 days', past_due_since=null,"
                " plan_selected_at=now() where user_id=%s",
    "sem_credencial": "update auth_accounts set password_hash=null where user_id=%s",
    "exclusao_agendada": f"update auth_accounts set deletion_status='scheduled',"
                         f" deletion_scheduled_for={_UM_DIA} + interval '7 days' where user_id=%s",
    "onboarding_pendente": "update auth_accounts set open_finance_onboarding_completed_at=null where user_id=%s",
}


def _conta_no_estado(estado):
    from _apoio_auth_app import sql
    uid = completo(usuario_pagante(plan="pro_max"))
    if ESTADOS[estado]:
        sql(ESTADOS[estado], uid)
    if estado == "so_google_sem_senha":  # sem senha MAS com identidade Google: a conta entra
        sql("insert into auth_identities (user_id, provider, provider_sub) values (%s, 'google', %s)",
            uid, f"sub-{uid}")
    return uid


@pytest.mark.parametrize("estado", list(ESTADOS))
def test_veredito_do_card_e_o_da_rota_do_app(estado):
    from test_api_app_sessao import cliente
    uid = _conta_no_estado(estado)
    status = cliente(uid).get("/api/app/contas").status_code
    r, blocos, ev = mostrar(uid, card="contas")
    if status == 200:
        assert "error" not in r and len(blocos) == 1, (estado, r)
    else:
        assert status in (402, 403), (estado, status)
        assert "error" in r and not blocos and not [e for e in ev if e["tipo"] in ("passo", "bloco")], (estado, r)


def test_a_matriz_de_estados_tem_os_dois_lados():
    """A matriz mede de verdade: há estado liberado (ativo, trial, só-Google) e estado recusado."""
    from test_api_app_sessao import cliente
    codigos = {e: cliente(_conta_no_estado(e)).get("/api/app/contas").status_code for e in ESTADOS}
    assert codigos["ativo"] == codigos["trial"] == codigos["so_google_sem_senha"] == 200
    assert {codigos[e] for e in ESTADOS if e not in ("ativo", "trial", "so_google_sem_senha")} <= {402, 403}
    assert sum(c != 200 for c in codigos.values()) == 6
