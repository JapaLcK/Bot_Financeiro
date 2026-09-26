""""Sim" logo depois de uma oferta da IA vai para a IA, não para o "não entendi".

Relato do dono (WhatsApp, 2026-09-24): a IA respondeu a um desabafo com "Quer
que eu mostre suas maiores despesas ou top categorias do mês?", sem guardar
pendência. O "Sim" classifica como `confirm.yes` com confiança 1.0, então não
caía no fallback de IA do `handle_incoming`, e o `route()` sem pendência
devolvia `NOT_UNDERSTOOD_MSG`.
"""
import uuid

import pytest

import db
import core.handle_incoming as hi
from db.connection import get_conn
from core.intent_router import INVESTMENT_ACTION_REFUSAL_MSG, NOT_UNDERSTOOD_MSG
from core.types import IncomingMessage
from _pendencia_credito_helpers import arma_installment, diga, novo_uid
from core.services.ai_chat_commands import pergunta_aberta_da_ia

OFERTA = ("🐷 Eu entendo, às vezes os gastos podem surpreender. Quer que eu "
          "mostre suas maiores despesas ou top categorias do mês?")


def _com_ia(monkeypatch):
    uid = novo_uid()
    chamadas = []
    monkeypatch.setattr("core.services.plan_service.ai_chat_allowed", lambda _uid: True)
    monkeypatch.setattr("core.services.ai_chat.chat",
                        lambda *a, **k: chamadas.append(a[1]) or "resposta do agente")
    return uid, chamadas


def _ia_disse(uid, texto, minutos_atras=0):
    msg_id = db.ai_append_message(uid, "assistant", texto)
    if minutos_atras:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "update ai_messages set created_at = now() - %s * interval '1 minute' "
                "where id = %s and user_id = %s",
                (minutos_atras, msg_id, uid),
            )
            conn.commit()


def test_sim_depois_da_oferta_vai_para_a_ia(monkeypatch):
    for resposta in ("Sim", "sim", "não", "Nao"):
        uid, chamadas = _com_ia(monkeypatch)
        db.ai_append_message(uid, "user", "Pqp como que eu gastei tanto")
        _ia_disse(uid, OFERTA)

        assert diga(uid, resposta) == "resposta do agente", resposta
        assert chamadas == [resposta]


def test_pergunta_terminada_em_emoji_tambem_conta(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, "Quer ver seus top gastos do mês? 🐷")

    assert diga(uid, "sim") == "resposta do agente"


def test_sim_sem_pergunta_da_ia_continua_nao_entendido(monkeypatch):
    """Positivo do outro lado: "sim" solto não vira chamada à IA (cota)."""
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, "🐷 Tamo junto! Qualquer coisa, é só chamar.")

    assert diga(uid, "sim") == NOT_UNDERSTOOD_MSG
    assert chamadas == []


def test_pergunta_velha_da_ia_nao_captura_o_sim(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, OFERTA, minutos_atras=11)

    assert diga(uid, "sim") == NOT_UNDERSTOOD_MSG
    assert chamadas == []


def test_historico_de_outro_usuario_nao_conta(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(novo_uid(), OFERTA)

    assert diga(uid, "sim") == NOT_UNDERSTOOD_MSG
    assert chamadas == []


def test_comando_no_meio_encerra_a_pergunta_da_ia(monkeypatch):
    """Achado do Codex no #574: o `ai_messages` não vê o comando do meio, então
    sem a invalidação o "sim" voltava para a oferta abandonada. "conectar banco"
    responde antes do 5b (Open Finance só devolve o link, sem rede)."""
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, OFERTA)

    assert "Open Finance" in diga(uid, "conectar banco")
    assert diga(uid, "sim") == NOT_UNDERSTOOD_MSG
    assert chamadas == []


def test_comando_que_responde_antes_do_route_tambem_encerra(monkeypatch):
    """2º achado do Codex no #574: `plano` (cobrança) sai antes do `route()`."""
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, OFERTA)

    assert diga(uid, "plano") != "resposta do agente"
    assert diga(uid, "sim") == NOT_UNDERSTOOD_MSG
    assert chamadas == []


def test_a_oferta_continua_aberta_depois_de_o_sim_ir_para_a_ia(monkeypatch):
    """O `finally` não pode encerrar a pergunta no mesmo turno em que a IA a
    atendeu (com a IA de verdade, ela grava a própria resposta)."""
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, OFERTA)
    monkeypatch.setattr(
        "core.services.ai_chat.chat",
        lambda u, t, **k: (chamadas.append(t), db.ai_append_message(u, "user", t),
                           db.ai_append_message(u, "assistant", "Top 3: ..."))
        and "resposta do agente",
    )

    assert diga(uid, "sim") == "resposta do agente"
    assert db.ai_get_last_message(uid)["content"] == "Top 3: ..."


def test_encerrar_so_grava_se_a_pergunta_ainda_e_a_ultima():
    """3º achado do Codex no #574: conferência e gravação no mesmo statement.
    Uma resposta da IA gravada depois da pergunta lida não é encerrada."""
    uid = novo_uid()
    velha = db.ai_append_message(uid, "assistant", OFERTA)
    nova = db.ai_append_message(uid, "assistant", "Quer ver as categorias?")

    assert db.ai_append_message_if_last(uid, velha, "system", "x") is False
    assert db.ai_get_last_message(uid)["id"] == nova
    assert db.ai_append_message_if_last(uid, nova, "system", "x") is True


def test_encerrar_espera_a_resposta_da_ia_que_ainda_nao_commitou():
    """4º achado do Codex no #574: em READ COMMITTED, uma resposta da IA ainda
    não commitada escapa do snapshot da conferência. Com a trava do histórico, o
    encerramento espera o commit e então vê que a pergunta lida não é a última."""
    import threading
    from db.ai_chat import _TRAVA_DO_HISTORICO

    uid = novo_uid()
    velha = db.ai_append_message(uid, "assistant", OFERTA)
    resultado = {}

    with get_conn() as conn, conn.cursor() as cur:
        # A "outra requisição": gravou a pergunta nova e ainda não commitou.
        cur.execute(_TRAVA_DO_HISTORICO, (str(uid),))
        cur.execute(
            "insert into ai_messages (user_id, role, content) values (%s, 'assistant', %s)",
            (uid, "Quer ver as categorias?"),
        )
        t = threading.Thread(target=lambda: resultado.update(
            gravou=db.ai_append_message_if_last(uid, velha, "system", "x")))
        t.start()
        t.join(timeout=1.0)
        assert t.is_alive(), "o encerramento não esperou a trava"
        conn.commit()
    t.join(timeout=5.0)

    assert resultado == {"gravou": False}
    assert db.ai_get_last_message(uid)["content"] == "Quer ver as categorias?"


# ---------------------------------------------------------------------------
# Qualquer texto responde à pergunta da IA, não só "sim"/"não" (relato do dono,
# 2026-09-25): a IA pediu o orçamento e "300 reais transporte" virou despesa.
# ---------------------------------------------------------------------------

ORCAMENTO = ("Qual categoria você gostaria de focar primeiro? Alimentação, "
             "transporte, lazer... E quanto você gostaria de gastar por mês "
             "nessa categoria?")


def _lancamentos(uid):
    with get_conn() as conn, conn.cursor() as cur:
        return cur.execute("select count(*) as n from launches where user_id = %s",
                           (uid,)).fetchone()["n"]


@pytest.mark.parametrize("resposta", ["300 reais transporte", "gastei 50 no mercado", "saldo"])
def test_resposta_a_pergunta_da_ia_vai_para_a_ia(monkeypatch, resposta):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    assert diga(uid, resposta) == "resposta do agente"
    assert chamadas == [resposta]
    assert _lancamentos(uid) == 0


def test_sim_ao_desfazer_apaga_mesmo_com_pergunta_da_ia(monkeypatch):
    """Positivo: a pendência (delete_launch) armada depois da pergunta ganha."""
    from core.handlers.launches import propose_delete
    uid, chamadas = _com_ia(monkeypatch)
    launch_id, _, _ = db.add_launch_and_update_balance(uid, "despesa", 50, None, "mercado")
    _ia_disse(uid, ORCAMENTO)
    propose_delete(uid, launch_id)

    diga(uid, "Sim")
    assert chamadas == []
    assert _lancamentos(uid) == 0


def test_pendencia_deterministica_resolve_pelo_route(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)
    arma_installment(uid)

    assert "parcelamento registrado" in diga(uid, "comprei uma tv").lower()
    assert chamadas == []


class _Anexo:
    def __init__(self, filename, content_type, data=None):
        self.filename, self.content_type, self.data = filename, content_type, data


def _envia(uid, anexo, texto=""):
    out = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=uid, text=texto, message_id=uuid.uuid4().hex,
        attachments=[anexo], external_id="", raw={},
    ))
    return "\n".join(m.text for m in out)


def test_csv_sem_dados_encerra_a_pergunta(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    assert "Recebi o CSV, mas não consegui baixar" in _envia(
        uid, _Anexo("extrato.csv", "text/csv"))
    diga(uid, "300 reais transporte")
    assert chamadas == []
    assert _lancamentos(uid) == 1


def test_sem_ia_no_plano_grava_pelo_route(monkeypatch):
    """Sem a IA por outro motivo que não a cota (v1 sem Pro): route(), como antes."""
    uid, chamadas = _com_ia(monkeypatch)
    monkeypatch.setattr("core.services.plan_service.ai_chat_allowed", lambda _uid: False)
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    _ia_disse(uid, ORCAMENTO)

    diga(uid, "300 reais transporte")
    assert chamadas == []
    assert _lancamentos(uid) == 1


# Cota da IA esgotada (v2: todo tier tem IA, só a cota a tira) com a pergunta
# aberta: aviso de cota, sem gravar; a pergunta fecha (achado do Codex).
COTA = "Suas mensagens com a Piggy deste mês acabaram"


def _sem_cota(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    monkeypatch.setattr("core.services.plan_service.ai_chat_allowed", lambda _uid: False)
    monkeypatch.delenv("PLANS_V2_ENABLED", raising=False)
    return uid, chamadas


def test_cota_esgotada_com_pergunta_aberta_avisa_e_nao_grava(monkeypatch):
    uid, chamadas = _sem_cota(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    assert COTA in diga(uid, "300 reais transporte")
    assert chamadas == []
    assert _lancamentos(uid) == 0
    assert pergunta_aberta_da_ia(uid) is None

    diga(uid, "300 reais transporte")  # a pergunta fechou: volta ao route()
    assert _lancamentos(uid) == 1


def test_audio_cota_esgotada_com_pergunta_aberta_avisa_e_nao_grava(monkeypatch):
    uid, chamadas = _sem_cota(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    r = _audio(monkeypatch, uid, "300 reais transporte")
    assert 'Entendi: "300 reais transporte"' in r and COTA in r
    assert chamadas == []
    assert _lancamentos(uid) == 0

    _audio(monkeypatch, uid, "300 reais transporte")
    assert _lancamentos(uid) == 1


def test_cota_esgotada_sem_pergunta_aberta_grava_pelo_route(monkeypatch):
    uid, chamadas = _sem_cota(monkeypatch)

    assert COTA not in diga(uid, "300 reais transporte")
    assert chamadas == []
    assert _lancamentos(uid) == 1


@pytest.mark.parametrize("primeira", ["error_msg", "levanta"])
def test_ia_que_falha_sem_gravar_nao_encerra_a_pergunta(monkeypatch, primeira):
    """O runner devolve ERROR_MSG sem gravar nada (sem OPENAI_API_KEY, cliente
    que não sobe), ou levanta antes do `user`: o histórico fica igual, mas o
    turno foi da IA — a nova tentativa ainda responde a ela, não vira despesa."""
    from core.services.ai_chat.runner import ERROR_MSG
    uid, chamadas = _com_ia(monkeypatch)

    def chat(_uid, texto, **_k):
        chamadas.append(texto)
        if len(chamadas) > 1:
            return "resposta do agente"
        if primeira == "levanta":
            raise RuntimeError("cliente OpenAI")
        return ERROR_MSG

    monkeypatch.setattr("core.services.ai_chat.chat", chat)
    _ia_disse(uid, ORCAMENTO)

    assert diga(uid, "300 reais transporte") == ERROR_MSG
    assert diga(uid, "300 reais transporte") == "resposta do agente"
    assert chamadas == ["300 reais transporte"] * 2
    assert _lancamentos(uid) == 0


def test_prefixo_piggy_que_falha_sem_gravar_nao_encerra_a_pergunta(monkeypatch):
    """Mesma regra pela outra porta da IA (`handle_ai_chat_command`)."""
    from core.services.ai_chat.runner import ERROR_MSG
    uid, chamadas = _com_ia(monkeypatch)
    monkeypatch.setattr("core.services.ai_chat_commands.ai_chat_allowed", lambda _uid: True)
    monkeypatch.setattr("core.services.ai_chat.chat",
                        lambda _u, t, **_k: chamadas.append(t) or ERROR_MSG)
    _ia_disse(uid, ORCAMENTO)

    assert diga(uid, "piggy 300 reais transporte") == ERROR_MSG
    assert diga(uid, "300 reais transporte") == ERROR_MSG
    assert chamadas == ["300 reais transporte"] * 2
    assert _lancamentos(uid) == 0


def test_recusa_de_investimento_vem_antes_da_pergunta(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    assert INVESTMENT_ACTION_REFUSAL_MSG.splitlines()[0] in diga(uid, "devo comprar bitcoin?")
    assert chamadas == []


def test_pergunta_velha_ou_de_outro_usuario_nao_captura(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    outro = novo_uid()
    _ia_disse(uid, ORCAMENTO, minutos_atras=11)
    _ia_disse(outro, ORCAMENTO)

    diga(uid, "300 reais transporte")
    assert chamadas == []
    assert _lancamentos(uid) == 1
    assert _lancamentos(outro) == 0


def test_botao_nunca_responde_a_pergunta_da_ia(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=uid, text="desfazer", message_id=uuid.uuid4().hex,
        attachments=[], external_id="", raw={},
    ), de_botao=True)
    assert chamadas == []


@pytest.mark.parametrize("botao,texto", [("undo_launch", ""), ("confirm_yes", "Sim")])
def test_wa_runtime_marca_o_clique_como_botao(monkeypatch, botao, texto):
    """A fiação: o clique chega ao `handle_incoming` real com `de_botao`."""
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)
    _clica(monkeypatch, uid, botao, texto)
    assert chamadas == []


def _clica(monkeypatch, uid, botao, texto):
    """Um clique pelo `process_message` real."""
    from adapters.whatsapp.wa_parse import InboundMessage
    from adapters.whatsapp.wa_runtime import process_message
    wr = "adapters.whatsapp.wa_runtime."
    monkeypatch.setattr(wr + "get_or_create_canonical_user", lambda provider, external_id: uid)
    monkeypatch.setattr(wr + "attempt_whatsapp_phone_link",
                        lambda wa_id, current_user_id=None: {"status": "noop", "user_id": uid})
    monkeypatch.setattr(wr + "log_system_event_sync", lambda *a, **k: None)
    monkeypatch.setattr(wr + "send_typing_indicator", lambda *a, **k: None)
    monkeypatch.setattr(wr + "_seen_recent", lambda message_id: False)
    monkeypatch.setattr(wr + "_send_reply", lambda *a, **k: None)
    monkeypatch.setattr(wr + "_send_reply_with_optional_buttons", lambda *a, **k: None)

    process_message(InboundMessage(
        wa_id="5511999990000", text=texto, timestamp="1", attachments=[],
        raw={"id": f"wamid.{uuid.uuid4().hex[:10]}", "type": "interactive",
             "interactive": {"type": "button_reply",
                             "button_reply": {"id": botao, "title": texto or "x"}}},
    ))


def test_botao_que_retorna_cedo_tambem_encerra_a_pergunta(monkeypatch):
    """Achado do Codex no #598: a lista de categorias responde e dá `return`
    no `process_message`, sem passar pelo `finally` do `handle_incoming`."""
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    _clica(monkeypatch, uid, "recatpick:1:Alimentação", "Alimentação")
    diga(uid, "gastei 50 no mercado")
    assert chamadas == []
    assert _lancamentos(uid) == 1


def _pendencias_quebram_uma_vez(monkeypatch):
    real = db.get_pending_action
    falhou = []

    def _get(uid):
        if not falhou:
            falhou.append(uid)
            raise RuntimeError("banco caiu")
        return real(uid)
    monkeypatch.setattr(db, "get_pending_action", _get)
    return falhou


ERRO_INTERNO = "Ocorreu um erro interno"


def test_consulta_de_pendencia_falha_com_pergunta_aberta_nao_grava(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)
    falhou = _pendencias_quebram_uma_vez(monkeypatch)

    assert ERRO_INTERNO in diga(uid, "300 reais transporte")
    assert falhou == [uid]
    assert chamadas == []
    assert _lancamentos(uid) == 0

    # A pergunta continuou aberta: a repetição vai à IA.
    assert diga(uid, "300 reais transporte") == "resposta do agente"
    assert chamadas == ["300 reais transporte"]
    assert _lancamentos(uid) == 0


def test_consulta_de_pendencia_falha_sem_pergunta_segue_o_route(monkeypatch):
    """Positivo: sem pergunta aberta, a mesma falha segue o fluxo de hoje."""
    from core.services.ai_chat.runner import ERROR_MSG
    uid, chamadas = _com_ia(monkeypatch)
    falhou = _pendencias_quebram_uma_vez(monkeypatch)

    assert diga(uid, "300 reais transporte") != ERROR_MSG
    assert falhou == [uid]
    assert _lancamentos(uid) == 1


def _audio(monkeypatch, uid, transcricao):
    monkeypatch.setattr(hi, "transcribe_audio", lambda data, fn: transcricao)
    return _envia(uid, _Anexo("audio.ogg", "audio/ogg", b"x"))


def test_audio_com_pergunta_da_ia_vai_para_a_ia(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    r = _audio(monkeypatch, uid, "300 reais transporte e 200 lazer")
    assert 'Entendi: "300 reais transporte e 200 lazer"' in r
    assert "resposta do agente" in r
    assert chamadas == ["300 reais transporte e 200 lazer"]
    assert _lancamentos(uid) == 0


def test_audio_sem_pergunta_ou_com_pendencia_segue_o_route(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _audio(monkeypatch, uid, "gastei 50 no mercado")
    assert _lancamentos(uid) == 1

    uid2 = novo_uid()
    _ia_disse(uid2, ORCAMENTO)
    arma_installment(uid2)
    assert "parcelamento registrado" in _audio(monkeypatch, uid2, "comprei uma tv").lower()
    assert chamadas == []
    assert pergunta_aberta_da_ia(uid2) is None  # áudio roteado encerra


def test_audio_consulta_de_pendencia_falha_com_pergunta_aberta_nao_grava(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)
    falhou = _pendencias_quebram_uma_vez(monkeypatch)

    assert ERRO_INTERNO in _audio(monkeypatch, uid, "300 reais transporte")
    assert falhou == [uid]
    assert chamadas == []
    assert _lancamentos(uid) == 0

    # A pergunta continuou aberta: a repetição vai à IA.
    _audio(monkeypatch, uid, "300 reais transporte")
    assert chamadas == ["300 reais transporte"]
    assert _lancamentos(uid) == 0


# ---------------------------------------------------------------------------
# A IA falha (timeout, rate limit) com a pergunta aberta: a resposta a ela não
# pode cair no route() e virar despesa (achado do Tester).
# ---------------------------------------------------------------------------

def _ia_quebrada(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)

    def _falha(*a, **k):
        chamadas.append(a[1])
        raise RuntimeError("timeout")
    monkeypatch.setattr("core.services.ai_chat.chat", _falha)
    return uid, chamadas


def test_ia_falha_com_pergunta_aberta_nao_grava(monkeypatch):
    from core.services.ai_chat.runner import ERROR_MSG
    uid, chamadas = _ia_quebrada(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    assert diga(uid, "300 reais transporte") == ERROR_MSG
    assert chamadas == ["300 reais transporte"]
    assert _lancamentos(uid) == 0


def test_audio_ia_falha_com_pergunta_aberta_nao_grava(monkeypatch):
    from core.services.ai_chat.runner import ERROR_MSG
    uid, chamadas = _ia_quebrada(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    r = _audio(monkeypatch, uid, "300 reais transporte")
    assert ERROR_MSG in r
    assert chamadas == ["300 reais transporte"]
    assert _lancamentos(uid) == 0


def test_ia_falha_sem_pergunta_aberta_segue_o_fluxo_antigo(monkeypatch):
    """Positivo: out_of_scope sem pergunta aberta, IA falhando, continua no
    route() como antes — sem a mensagem de erro."""
    from core.services.ai_chat.runner import ERROR_MSG
    uid, chamadas = _ia_quebrada(monkeypatch)

    r = diga(uid, "qual a capital da frança")
    assert chamadas, "o texto nem chegou ao fallback de IA"
    assert ERROR_MSG not in r
    assert _lancamentos(uid) == 0


def test_audio_para_a_ia_nao_encerra_a_pergunta(monkeypatch):
    """Com a IA de verdade (grava user+assistant), o `finally` não grava o
    `system`: a última mensagem é a resposta da IA."""
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)
    monkeypatch.setattr(
        "core.services.ai_chat.chat",
        lambda u, t, **k: (chamadas.append(t), db.ai_append_message(u, "user", t),
                           db.ai_append_message(u, "assistant", "Anotado: R$ 300"))
        and "resposta do agente",
    )

    assert "resposta do agente" in _audio(monkeypatch, uid, "300 reais transporte")
    assert db.ai_get_last_message(uid)["content"] == "Anotado: R$ 300"


# ---------------------------------------------------------------------------
# A nova tentativa depois de uma falha da IA (achado do Tester): o runner real
# grava o turno falho no histórico, e ele não pode fechar a pergunta.
# ---------------------------------------------------------------------------

def _ia_falha_uma_vez(monkeypatch, *, levanta):
    """Imita o runner: 1º turno falha — `user` + `assistant(ERROR_MSG)` (caminho
    comum) ou só o `user` e exceção (erro que escapa do runner); depois responde."""
    from core.services.ai_chat.runner import ERROR_MSG
    uid, chamadas = _com_ia(monkeypatch)

    def _chat(u, t, **k):
        chamadas.append(t)
        if len(chamadas) > 1:
            return "resposta do agente"
        db.ai_append_message(u, "user", t)
        if levanta:
            raise RuntimeError("banco caiu no meio do turno")
        db.ai_append_message(u, "assistant", ERROR_MSG)
        return ERROR_MSG
    monkeypatch.setattr("core.services.ai_chat.chat", _chat)
    return uid, chamadas


@pytest.mark.parametrize("levanta", [False, True])
def test_nova_tentativa_depois_de_falha_da_ia_vai_para_a_ia(monkeypatch, levanta):
    from core.services.ai_chat.runner import ERROR_MSG
    uid, chamadas = _ia_falha_uma_vez(monkeypatch, levanta=levanta)
    _ia_disse(uid, ORCAMENTO)

    assert ERROR_MSG in diga(uid, "300 reais transporte")
    assert diga(uid, "300 reais transporte") == "resposta do agente"
    assert chamadas == ["300 reais transporte"] * 2
    assert _lancamentos(uid) == 0


def test_comando_depois_de_falha_da_ia_encerra_a_pergunta(monkeypatch):
    uid, chamadas = _ia_falha_uma_vez(monkeypatch, levanta=False)
    _ia_disse(uid, ORCAMENTO)

    diga(uid, "300 reais transporte")
    assert "Open Finance" in diga(uid, "conectar banco")
    diga(uid, "300 reais transporte")
    assert len(chamadas) == 1
    assert _lancamentos(uid) == 1


def test_janela_conta_da_pergunta_e_nao_da_falha(monkeypatch):
    from core.services.ai_chat.runner import ERROR_MSG
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO, minutos_atras=11)
    db.ai_append_message(uid, "user", "300 reais transporte")
    db.ai_append_message(uid, "assistant", ERROR_MSG)

    diga(uid, "300 reais transporte")
    assert chamadas == []
    assert _lancamentos(uid) == 1


# ---------------------------------------------------------------------------
# A pergunta só fecha quando o turno LEU a mensagem e a atendeu fora da IA.
# ---------------------------------------------------------------------------

def _transcricao_que_levanta(data, fn):
    raise RuntimeError("whisper caiu")


@pytest.mark.parametrize("caso", ["sem_dados", "transcricao_vazia", "transcricao_levanta"])
def test_audio_nao_lido_nao_encerra_a_pergunta(monkeypatch, caso):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    if caso == "sem_dados":
        _envia(uid, _Anexo("audio.ogg", "audio/ogg"))
    elif caso == "transcricao_vazia":
        _audio(monkeypatch, uid, None)
    else:
        monkeypatch.setattr(hi, "transcribe_audio", _transcricao_que_levanta)
        assert ERRO_INTERNO in _envia(uid, _Anexo("audio.ogg", "audio/ogg", b"x"))

    assert diga(uid, "300 reais transporte") == "resposta do agente"
    assert chamadas == ["300 reais transporte"]
    assert _lancamentos(uid) == 0


def test_excecao_no_turno_nao_encerra_a_pergunta(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)
    real, falhou = hi.classify, []

    def _classify(*a, **k):
        if not falhou:
            falhou.append(1)
            raise RuntimeError("classifier caiu")
        return real(*a, **k)
    monkeypatch.setattr(hi, "classify", _classify)

    assert ERRO_INTERNO in diga(uid, "300 reais transporte")
    assert diga(uid, "300 reais transporte") == "resposta do agente"
    assert chamadas == ["300 reais transporte"]
    assert _lancamentos(uid) == 0


def test_anexo_sem_texto_nao_encerra_a_pergunta(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    assert _envia(uid, _Anexo("contato.vcf", "text/vcard", b"x")) == ""
    assert diga(uid, "300 reais transporte") == "resposta do agente"
    assert _lancamentos(uid) == 0


def _arma_ai_pending(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    monkeypatch.setattr("core.services.ai_chat_commands.ai_chat_allowed", lambda _uid: True)
    db.ai_set_pending_action(uid, "delete_launch", {"launch_id": 5}, "apagar #5")
    _ia_disse(uid, "Posso apagar o lançamento #5?")
    return uid, chamadas


@pytest.mark.parametrize("comando", ["saldo", "Apagar id 5"])
def test_comando_que_larga_a_ai_pending_vai_ao_route(monkeypatch, comando):
    uid, chamadas = _arma_ai_pending(monkeypatch)

    diga(uid, comando)
    assert chamadas == []
    assert db.ai_get_pending_action(uid) is None
    assert pergunta_aberta_da_ia(uid) is None

    diga(uid, "300 reais transporte")
    assert _lancamentos(uid) == 1


def test_larga_a_ai_pending_com_consulta_de_pendencia_falhando(monkeypatch):
    uid, chamadas = _arma_ai_pending(monkeypatch)
    falhou = _pendencias_quebram_uma_vez(monkeypatch)

    assert ERRO_INTERNO not in diga(uid, "saldo")
    assert falhou == [uid]
    assert chamadas == []


def test_sim_com_ai_pending_e_pergunta_vai_para_a_ia(monkeypatch):
    uid, chamadas = _arma_ai_pending(monkeypatch)

    assert diga(uid, "sim") == "resposta do agente"
    assert chamadas == ["sim"]


def test_audio_nao_lido_nao_vaza_para_o_turno_seguinte(monkeypatch):
    uid, chamadas = _com_ia(monkeypatch)
    _ia_disse(uid, ORCAMENTO)

    _envia(uid, _Anexo("audio.ogg", "audio/ogg"))
    assert "Open Finance" in diga(uid, "conectar banco")
    diga(uid, "300 reais transporte")
    assert chamadas == []
    assert _lancamentos(uid) == 1
