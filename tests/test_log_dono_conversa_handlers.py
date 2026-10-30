"""#657 C1b (PR-6b do grupo D): as falhas dos handlers, da IA, dos serviços e de `db/categories`
gravam o dono na coluna `user_id` de `system_event_logs`, com só tipo + sqlstate (D6) e sem o
texto que o usuário digitou (D4).

T7: a conversa de verdade (`handle_incoming` real, Postgres, o `_DashboardHandler` do root),
um caso por gancho pós-commit de `add_from_entities`. T8: um turno de IA que esgota as voltas.
T9: chamada direta dos sites que a conversa não alcança barato, a rede contra `NameError` nos
`except` reescritos (o retorno de fail-open continua o da base). Os helpers são os do PR-6a.

Sem teste de comportamento (cobertos só pelo ratchet de `test_log_dono_na_coluna.py` e pelo
`pyflakes` comparado à main): intent_router `route`, agent_chat `chat`, as voltas de prazo e
de JSON cru de `_run_tool_loop`, db/categories `user_category_display_map`.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date

import pytest

import core.handlers.launches as launches
import core.handlers.investments as investments
import db
from core.services import category_service, funding, plan_service
from core.services.ai_chat import runner
import db.categories as cats
import db.privacy as privacy
from conftest import usuario_pagante
from test_log_dono_conversa_whatsapp import (  # noqa: F401 (banco_real é fixture autouse)
    AMIGAVEL, GASTO, _conversa, _exportados, _levanta, _linhas, _marca, _sem_vazamento, _uid, _uma,
    _violacao, _vazou, banco_real)

CAUSA = "causa=UniqueViolation sqlstate=23505"
LAUNCHES = "core.handlers.launches"


def _so_na_acao(nome: str, acao: str, marcador: str):
    """`db.<nome>` levanta só para a pendência `acao`; as outras seguem para o banco."""
    real = getattr(db, nome)

    def f(uid, action_type, *a, **k):
        if action_type == acao:
            raise _violacao(marcador)
        return real(uid, action_type, *a, **k)
    return f


def _oferta(*_a, **_k):
    return {"name": "Fixo", "amount": 50.9, "category": "alimentação", "due_day": 1, "merchant_key": "fixo"}


def _gancho_learn(m, marcador):
    m.setattr(launches, "learn_from_inference", _levanta(marcador))


def _gancho_oferta(m, marcador):
    m.setattr(launches, "_maybe_recurring_offer", _levanta(marcador))


def _gancho_pending_oferta(m, marcador):
    m.setattr(launches, "_maybe_recurring_offer", _oferta)
    for nome in ("set_pending_action", "create_pending_action_if_absent"):
        m.setattr(db, nome, _so_na_acao(nome, "confirm_recurring_offer", marcador))


def _gancho_pending_recat(m, marcador):
    for nome in ("set_pending_action", "create_pending_action_if_absent"):
        m.setattr(db, nome, _so_na_acao(nome, "recategorize_launch_offer", marcador))


def _gancho_saldo(m, marcador):
    m.setattr(db, "get_consolidated_balance", _levanta(marcador))


def _gancho_orcamento(m, marcador):
    import core.budget_alerts as ba
    m.setattr(ba, "evaluate_after_expense", _levanta(marcador))


def _gancho_nudge(m, marcador):
    import core.observability as obs
    real = obs.recent_event_exists

    def f(evento, *a, **k):
        if evento == "onboarding_first_launch_nudge":
            raise _violacao(marcador)
        return real(evento, *a, **k)
    m.setattr(obs, "recent_event_exists", f)


# (gancho, event_type, prefixo da message): um por `except` pós-commit de `add_from_entities`.
_GANCHOS = [
    (_gancho_learn, "logger.error", "learn_from_inference falhou depois do commit"),
    (_gancho_oferta, "logger.error", "_maybe_recurring_offer falhou depois do commit"),
    (_gancho_pending_oferta, "logger.warning", "falha ao salvar pending confirm_recurring_offer"),
    (_gancho_pending_recat, "logger.warning", "falha ao salvar pending recategorize_launch_offer"),
    (_gancho_saldo, "logger.error", "saldo consolidado falhou depois do commit"),
    (_gancho_orcamento, "logger.warning", "avaliação de alerta de orçamento falhou"),
    (_gancho_nudge, "logger.warning", "nudge de primeiro lançamento falhou"),
]


# ── T7. "gastei" com falha pós-commit, pela conversa ────────────────────────

@pytest.mark.parametrize("gancho,event_type,prefixo", _GANCHOS, ids=[g[0].__name__[8:] for g in _GANCHOS])
def test_falha_pos_commit_do_gasto_tem_dono_e_o_lancamento_fica(gancho, event_type, prefixo):
    a, b = _uid(), _uid()
    mark_a, mark_b = f"MARCADOR-{uuid.uuid4().hex}", f"MARCADOR-{uuid.uuid4().hex}"
    marca = _marca()
    with pytest.MonkeyPatch.context() as m:
        gancho(m, mark_b)
        assert "registrada" in _conversa(b, "gastei 12,30 no pão de queijo")
    with pytest.MonkeyPatch.context() as m:
        gancho(m, mark_a)
        resposta = _conversa(a, GASTO)
    assert "registrada" in resposta and AMIGAVEL not in resposta, resposta
    (lanc,) = db.list_launches(a, 5)  # um só: a falha pós-commit não desfaz nem duplica

    linha = _uma(_linhas(marca, "user_id = %s", (a,)), event_type, LAUNCHES, prefixo)
    _sem_vazamento(linha, mark_a, "açaí", "Zélia", "Traceback", lanc["categoria"])
    assert CAUSA in linha["message"], linha
    da_b = _uma(_linhas(marca, "user_id = %s", (b,)), event_type, LAUNCHES, prefixo)

    bruto_a, ids_a = _exportados(a)
    assert linha["id"] in ids_a and da_b["id"] not in ids_a and mark_b not in bruto_a
    assert linha["id"] not in _exportados(b)[1], "linha de A na exportação de B"

    # Rode a conversa: outro assunto, sem a falha, é respondido.
    outra = _conversa(a, "saldo")
    assert outra.strip() and AMIGAVEL not in outra, outra

    privacy.delete_user_data(a)
    assert _linhas(marca, "user_id = %s", (a,)) == [], "log de A sobreviveu à exclusão"
    assert _linhas(marca, "id = %s", (da_b["id"],)), "a exclusão de A levou a linha de B"


# ── T8. turno de IA que esgota as voltas: site sem exceção ──────────────────

def test_max_tool_loops_grava_o_dono(monkeypatch):
    from _ia_stream_helpers import openai_falso, rodada
    a = usuario_pagante()
    openai_falso(monkeypatch, *[rodada(None, ("get_balance", {})) for _ in range(runner.MAX_TOOL_LOOPS)])
    marca = _marca()
    assert runner.chat(a, "quanto tenho na conta do açaí?", monthly_limit=100) == runner.ERROR_MSG

    linha = _uma(_linhas(marca, "user_id = %s", (a,)), "logger.warning", runner.__name__,
                 "MAX_TOOL_LOOPS atingido")
    assert linha["id"] in _exportados(a)[1]
    privacy.delete_user_data(a)
    assert _linhas(marca, "id = %s", (linha["id"],)) == []


# ── T9. chamada direta: o fail-open de sempre, e o record com o dono ─────────

def _caso_bills(m, uid, mk):
    from core.handlers.bills import conta_paga
    m.setattr(db, "get_consolidated_balance", _levanta(mk))
    r = conta_paga(uid, {"name": "Luz"}, 120.0)
    assert r.endswith("lançado e categorizado."), r
    return "core.handlers.bills", "aviso a conferir falhou", ()


def _caso_forma(m, uid, mk):
    from core.handlers.forma_pagamento import regra_ativa
    m.setattr(db, "has_open_finance_connections", _levanta(mk))
    assert regra_ativa(uid) is True
    return "core.handlers.forma_pagamento", "regra da forma falhou", ()


def _caso_greeting(m, uid, mk):
    import openai
    from core.handlers.greeting import _greeting_with_ai
    chamou = []
    m.setattr(plan_service, "is_pro", _levanta(mk))
    m.setenv("OPENAI_API_KEY", "sk-teste")
    m.setattr(openai, "OpenAI", lambda **k: chamou.append(1) or _levanta(mk)())
    assert _greeting_with_ai("bom dia", "bom_dia", user_id=uid) is None
    assert chamou, "o gate falhando deixou de seguir para a IA (fail-open)"
    return "core.handlers.greeting", "gate Pro do greeting IA falhou", ()


def _caso_maybe_recurring(m, uid, mk):
    import db.recurring as rec
    m.setattr(rec, "find_recurring_candidate", _levanta(mk))
    assert launches._maybe_recurring_offer(uid, "açaí da Dona Zélia", 50.9, "moradia", date.today(), None) is None
    return LAUNCHES, "detecção de gasto fixo falhou", ("açaí", "Zélia")


def _caso_classifier(m, uid, mk):
    from core.intent_classifier import _classify_llm_call
    m.setattr(plan_service, "plan_gate_ok", _levanta(mk))
    assert _classify_llm_call("gastei no açaí da Dona Zélia", uid).intent == "out_of_scope"
    return "core.intent_classifier", "gate pago do tier-3 falhou", ("açaí", "Zélia")


def _caso_alvos(m, uid, mk):
    from core.intent_router import _alvos_existentes
    m.setattr(db, "list_pockets", _levanta(mk))
    assert _alvos_existentes(uid, "pockets.deposit") == []
    return "core.intent_router", "falha ao listar alvos", ()


def _caso_investido(m, uid, mk):
    from core.services.agent_chat_data import _investido
    from core.services.ai_chat.tools import investments
    m.setattr(investments, "_get_investment_summary", _levanta(mk))
    assert _investido(uid) == {"indisponivel": True}
    return "core.services.agent_chat_data", "agent_chat: investido.ler falhou", ()


def _caso_cancela(m, uid, mk):
    m.setattr(db, "ai_consume_pending_action", _levanta(mk))
    tok = runner._PENDENCIA_DO_TURNO.set({"id": 1})
    try:
        assert runner._cancela_pendencia_do_turno(uid) is None
    finally:
        runner._PENDENCIA_DO_TURNO.reset(tok)
    return "core.services.ai_chat.runner", "falha ao cancelar a pendência do turno", ()


def _caso_desiste(m, uid, mk):
    m.setattr(db, "ai_append_message", _levanta(mk))
    assert runner._desiste(uid) is None
    return "core.services.ai_chat.runner", "falha ao fechar o turno da IA", ()


def _caso_encerra(m, uid, mk):
    from core.services.ai_chat_commands import encerra_pergunta_da_ia
    m.setattr(db, "ai_append_message_if_last", _levanta(mk))
    assert encerra_pergunta_da_ia(uid, 1) is None
    return "core.services.ai_chat_commands", "encerra_pergunta_da_ia falhou", ()


def _caso_ai_command(m, uid, mk):
    import core.services.ai_chat as ai_chat
    import core.services.ai_chat_commands as cmds
    m.setattr(cmds, "ai_chat_allowed", lambda _u: True)
    m.setattr(cmds, "ai_monthly_limit_for", lambda _u: 100)
    m.setattr(ai_chat, "chat", _levanta(mk))
    assert cmds.handle_ai_chat_command(uid, "piggy quanto gastei no açaí da Dona Zélia?", "whatsapp") == cmds.ERROR_MSG
    return "core.services.ai_chat_commands", "ai_chat_run falhou", ("açaí", "Zélia")


def _caso_fonte_unica(m, uid, mk):
    from core.services import fonte_unica
    m.setattr(plan_service, "dashboard_v2_beta", _levanta(mk))
    assert fonte_unica.recusa(uid, next(iter(fonte_unica.MENSAGENS))) is None
    return "core.services.fonte_unica", "fonte_unica: chave do v2 falhou", ()


def _caso_metas(m, uid, mk):
    import core.services.metas_conversa as mc
    m.setattr(mc, "ler", _levanta(mk))
    assert mc.texto_whatsapp(uid).startswith("Não consegui ler suas metas agora."), mc.texto_whatsapp
    return "core.services.metas_conversa", "minhas metas falhou", ()


def _caso_cria_categoria(m, uid, mk):
    m.setattr(category_service, "reconciliar_regras_com_categoria", _levanta(mk))
    cat = cats.create_user_category(uid, "Açaí da Zelinha")
    assert cat and cat["id"], cat
    return "db.categories", "reconciliacao de regras falhou", ("Zelinha",)


def _caso_renomeia_categoria(m, uid, mk):
    cat = cats.create_user_category(uid, "Lanches")
    m.setattr(category_service, "reconciliar_regras_com_categoria", _levanta(mk))
    novo = cats.update_user_category(uid, cat["id"], new_name="Açaí da Zelinha")
    assert novo and novo["id"] == cat["id"], novo
    return "db.categories", "reconciliacao pos-rename falhou", ("Zelinha",)


def _caso_ensure_categoria(m, uid, mk):
    m.setattr(cats, "create_user_category", _levanta(mk))
    assert cats.ensure_user_category(uid, "Açaí da Zelinha", exigir_plano=False) is None
    return "db.categories", "ensure_user_category falhou", ("Zelinha",)


def _caso_credito(m, uid, mk):
    from core.handlers import credit
    db.create_card(uid, "Cartao Manual", closing_day=10, due_day=17)
    m.setattr(credit, "set_pending_action", _levanta(mk))
    r = credit.add_credit_from_entities(uid, valor=30, card_name="Cartao Manual", descricao="açaí da Dona Zélia")
    assert "Cartao Manual" in r, r
    return "core.handlers.credit", "falha ao salvar pending delete_credit_purchase", ("açaí", "Zélia")


def _inv_x(*_a, **_k):
    raise ValueError("INV_X")  # código que o handler não conhece


def _aporta(uid, mk):
    return investments._aporta(uid, f"CDB da Zelinha {mk}", 100.0, "aportar 100", {"kind": funding.CARTEIRA})


def _resgata(m, uid, mk, falha):
    nome = f"CDB da Zelinha {mk}"
    m.setattr(db, "list_investments", lambda *a, **k: [{"name": nome}])
    m.setattr(db, "investment_withdraw_to_account", falha)
    return investments.withdraw(uid, "resgatar 100", {"investment_name": nome, "amount": 100})


def _caso_aporte_codigo(m, uid, mk):
    m.setattr(db, "investment_deposit_from_account", _inv_x)
    assert _aporta(uid, mk) == "Não consegui registrar esse aporte. Confira o valor e tente de novo."
    return investments.__name__, "deposit: código inesperado", ("Zelinha",)


def _caso_aporte_sobe(m, uid, mk):  # :426, antes do commit: a exceção SOBE (re-arma a pergunta)
    m.setattr(db, "investment_deposit_from_account", _levanta(mk))
    with pytest.raises(type(_violacao(mk))):
        _aporta(uid, mk)
    return investments.__name__, "deposit falhou", ("Zelinha",)


def _caso_aporte_pos_commit(m, uid, mk):  # :440, o dinheiro já andou: não sobe
    m.setattr(db, "investment_deposit_from_account", lambda *a, **k: (777, 0, 0, "CDB"))
    m.setattr(db, "display_id_for", _levanta(mk))
    assert "registrado (ID interno #777)" in _aporta(uid, mk)
    return investments.__name__, "aporte 777 registrado, mas a mensagem falhou", ("Zelinha",)


def _caso_resgate_codigo(m, uid, mk):
    assert _resgata(m, uid, mk, _inv_x) == "Não consegui registrar esse resgate. Confira o valor e tente de novo."
    return investments.__name__, "withdraw: código inesperado", ("Zelinha",)


def _caso_resgate(m, uid, mk):
    r = _resgata(m, uid, mk, _levanta(mk))
    assert r == "Não consegui registrar esse resgate agora. Tente de novo em instantes.", r
    return investments.__name__, "withdraw falhou", ("Zelinha",)


def _caso_caixinha_pos_commit(m, uid, mk):  # pockets:151, o dinheiro já andou: não sobe
    from core.handlers.pockets import deposita_com_origem
    m.setattr(db, "pocket_deposit_from_account", lambda *a, **k: (888, 10, 20, "Viagem"))
    m.setattr(db, "display_id_for", _levanta(mk))
    r = deposita_com_origem(uid, "Viagem", 50.0, "guardar 50", {"kind": funding.CARTEIRA})
    assert "registrado (ID interno #888)" in r, r
    return "core.handlers.pockets", "depósito 888 registrado, mas a mensagem falhou", ()


def _caso_carteira(m, uid, mk):  # `carteira.lancar` é HTTP (R2): pela rota POST /api/v2/lancamentos/carteira
    from tests.test_api_v2_lancamentos_escrita import cria
    m.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    m.setenv("DASHBOARD_V2_BETA_USER_IDS", str(uid))
    m.setattr(category_service, "learn_from_inference", _levanta(mk))
    cria(uid, descricao="açaí da Dona Zélia")
    assert db.count_launches(uid) == 1
    return "core.services.carteira", "learn_from_inference falhou depois do commit", ("açaí", "Zélia")


_DIRETOS = [_caso_bills, _caso_forma, _caso_greeting, _caso_maybe_recurring, _caso_classifier, _caso_alvos,
            _caso_investido, _caso_cancela, _caso_desiste, _caso_encerra, _caso_ai_command, _caso_fonte_unica,
            _caso_metas, _caso_cria_categoria, _caso_renomeia_categoria, _caso_ensure_categoria,
            _caso_credito, _caso_aporte_codigo, _caso_aporte_sobe, _caso_aporte_pos_commit, _caso_resgate_codigo,
            _caso_resgate, _caso_caixinha_pos_commit, _caso_carteira]


@pytest.mark.parametrize("caso", _DIRETOS, ids=[c.__name__[6:] for c in _DIRETOS])
def test_falha_direta_grava_o_dono_sem_o_texto_cru(caso, caplog):
    uid, mk = usuario_pagante(), f"MARCADOR-{uuid.uuid4().hex}"
    with pytest.MonkeyPatch.context() as m, caplog.at_level(logging.WARNING):
        logger, prefixo, do_usuario = caso(m, uid, mk)
    (r,) = [r for r in caplog.records if r.name == logger and r.getMessage().startswith(prefixo)]
    msg = r.getMessage()
    assert getattr(r, "user_id", None) == uid and r.exc_info is None, r.__dict__
    assert CAUSA in msg or "code=INV_X" in msg, msg  # os "código inesperado" não têm exceção
    assert not (p := _vazou(msg, mk, *do_usuario)), f"{p!r} vazou: {msg}"


# runner:429 (`_chat_inner`): fora do ia_primeiro a exceção sobe sem log; nele, loga com o dono.
def test_falha_do_turno_de_ia_sobe_sem_log_ou_loga_com_dono_no_ia_primeiro(caplog):
    from _ia_stream_helpers import openai_falso
    a, mk = usuario_pagante(), f"MARCADOR-{uuid.uuid4().hex}"
    with pytest.MonkeyPatch.context() as m, caplog.at_level(logging.WARNING):
        openai_falso(m)
        m.setattr(runner, "_run_tool_loop", _levanta(mk))
        with pytest.raises(type(_violacao(mk))):
            runner.chat(a, "quanto gastei no açaí?", monthly_limit=100)
        assert not [r for r in caplog.records if r.name == runner.__name__], "logou antes do raise: log em dobro"
        assert runner.chat(a, "quanto gastei no açaí?", monthly_limit=100, ia_primeiro=True) is None
    (r,) = [r for r in caplog.records if r.getMessage().startswith("ai_chat ia_primeiro falhou")]
    assert getattr(r, "user_id", None) == a and r.exc_info is None and CAUSA in r.getMessage(), r.__dict__
    assert not _vazou(r.getMessage(), mk, "açaí")
