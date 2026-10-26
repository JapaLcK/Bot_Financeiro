"""PR-A2c (ADR 0006): PARA QUEM o lembrete sai e o que acontece quando o envio falha. G4 janela de 24 h, G5
consentimento e acesso, G6 falha de envio, e o passo ligado no loop. Postgres real, `send_text` real.

Controles NEGATIVOS (numa CÓPIA; `core/services/declaracao_lembrete.py` e `db/bank_lembrete.py`):
- G4. tirar o `exists (select 1 from auth_accounts ...)` do `list_declaracao_lembretes_devidos` -> VERMELHO
  `test_fora_da_janela_nao_envia_nem_reserva[25h]` e `[nula]`; `_JANELA_WA` virar `hours=24` -> VERMELHO
  `test_borda_da_janela[23h01]`; tirar `a.user_id = dec.user_id and` do `exists` -> VERMELHO
  `test_isolamento_so_quem_esta_na_janela_recebe`; checar a janela DEPOIS do claim -> VERMELHO os mesmos de fora da
  janela (reservou e não enviou). Positivos: `[22h59]` e o segundo passo de `test_a_mensagem_do_usuario_abre_a_janela`.
- G5. trocar `not get_whatsapp_updates_opt_out(uid)` por `True` -> VERMELHO `test_barrado_nao_recebe_nao_reserva[opt_out]`;
  trocar `bool(filtrar_por_acesso([uid]))` por `True` -> VERMELHO `[sem_acesso]` (e o portão); mover o `claim` para
  antes do `try` de leitura ou do `if not targets` -> VERMELHO os três `[...]` e `test_leitura_que_falha_nao_reserva`.
  Positivo: o segundo passo do mesmo teste (religou/voltou: sai UMA vez).
  D10. tirar `is_account_scheduled_for_deletion(uid)` do `ok` -> VERMELHO `[exclusao_agendada]` e `[exclusao_processando]`.
  Positivo: o mesmo teste com a exclusão cancelada (sai UMA vez).
- B1. tirar o `try/except` por usuário -> VERMELHO `test_falha_no_meio_de_um_usuario_nao_derruba_o_outro[claim|montagem|release]`.
- G6. tirar o `release_declaracao_lembretes` -> VERMELHO `test_recusa_em_todos_os_destinos_solta_a_reserva[...]`
  (o segundo tick não reenvia); soltar sem `launch_id = any(...)` -> VERMELHO `test_soltar_nao_apaga_o_que_ja_foi_enviado`;
  contar o 401 (`None`) como aceito -> VERMELHO `[401]`; `if reservadas and not aceitos:` -> `if reservadas:` VERMELHO `test_erro_depois_do_envio_aceito_nao_solta_a_reserva`. Positivo:
  `test_um_destino_aceita_mantem_a_reserva` e o segundo passo das recusas (a hora seguinte envia).
- Loop. tirar o passo novo de `run_recurring_charger_loop` -> VERMELHO `test_loop_real_manda_o_lembrete`; tirar o
  `try/except` próprio do passo -> VERMELHO `test_falha_de_um_passo_nao_derruba_o_outro[lembrete_quebra]`.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
import requests

import core.services.declaracao_lembrete as dl
import core.services.recurring_charger as rc
import db
from conftest import promote_to_pro, usuario_pagante
from core.observability import recent_event_exists
from db_support import invalidate_auth_user_cache
from tests._lembrete_declaracao import (  # noqa: F401 (_event_logs é fixture autouse)
    EVENTO, FONE_A, FONE_B, _event_logs, agora, armar, corpos, declara_ha, dono, lembretes, para, reservadas)
from tests._patrimonio_helpers import q
from tests.test_bill_amount_pending import _manda_texto_no_wa
from tests.test_recorrente_so_preve import _um_tick


def _roda(ag, posts, fone=FONE_A):
    return dl.notify_declaracoes_whatsapp_once(now=ag), len(para(posts, fone))


# --- G4: janela de 24 h --------------------------------------------------------------------------------------

@pytest.mark.parametrize("atras", [timedelta(hours=25), None], ids=["25h", "nula"])
def test_fora_da_janela_nao_envia_nem_reserva(user_id, monkeypatch, atras):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag, janela=atras)
    declara_ha(user_id, ag, 8)

    assert _roda(ag, posts) == (0, 0) and reservadas(user_id) == 0


@pytest.mark.parametrize("atras, envia", [(timedelta(hours=22, minutes=59), 1), (timedelta(hours=23, minutes=1), 0)],
                         ids=["22h59", "23h01"])
def test_borda_da_janela(user_id, monkeypatch, atras, envia):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag, janela=atras)
    declara_ha(user_id, ag, 8)

    assert _roda(ag, posts) == (envia, envia) and reservadas(user_id) == envia


def test_a_mensagem_do_usuario_abre_a_janela(monkeypatch):
    """Sem conversa nada sai nem é reservado; um texto real pelo `process_message` abre a janela (escritor real,
    `update_last_activity`) e a volta seguinte manda UMA vez."""
    import core.services.ai_chat as AC
    ag = agora()
    posts = armar(monkeypatch)
    monkeypatch.setattr(AC, "chat", lambda uid, text, **kw: "[IA]")  # nada de LLM
    uid = usuario_pagante()
    db.bind_identity("whatsapp", FONE_A, uid)
    declara_ha(uid, ag, 8)

    assert _roda(ag, posts) == (0, 0) and reservadas(uid) == 0
    _manda_texto_no_wa(monkeypatch, uid, "oi piggy")
    assert _roda(ag, posts) == (1, 1) and reservadas(uid) == 1
    assert _roda(ag, posts) == (0, 1)


def test_isolamento_so_quem_esta_na_janela_recebe(user_id, monkeypatch):
    from conftest import _cleanup_user
    ag = agora()
    posts = armar(monkeypatch)
    outro = user_id + 1
    try:
        db.ensure_user(outro)
        dono(user_id, FONE_A, ag)
        dono(outro, FONE_B, ag, janela=timedelta(hours=30))
        declara_ha(user_id, ag, 8, alvo="do A")
        declara_ha(outro, ag, 8, alvo="do B")

        assert dl.notify_declaracoes_whatsapp_once(now=ag) == 1
        [corpo] = corpos(posts, FONE_A)
        assert "gasto com do A," in corpo and "do B" not in corpo
        assert para(posts, FONE_B) == [] and reservadas(outro) == 0
    finally:
        _cleanup_user(outro)


# --- G5: consentimento e acesso (barrado NÃO reserva; volta a valer, sai UMA vez) -----------------------------------

def _barra(uid, motivo):
    if motivo == "opt_out":
        db.set_whatsapp_updates_opt_out(uid, True)
    elif motivo == "sem_acesso":
        q("update auth_accounts set plan='free', plan_expires_at=null where user_id=%s", (uid,))
        invalidate_auth_user_cache(uid)
    elif motivo == "exclusao_agendada":
        q("""update auth_accounts set deletion_status='scheduled', deletion_requested_at=now(),
                    deletion_scheduled_for=now() + interval '7 days' where user_id=%s""", (uid,))
    elif motivo == "exclusao_processando":  # como `schedule_account_deletion` marca: scheduled_for = now()
        q("""update auth_accounts set deletion_status='processing', deletion_requested_at=now(),
                    deletion_scheduled_for=now(), deletion_processing_started_at=now() where user_id=%s""", (uid,))
    else:
        q("delete from user_identities where user_id=%s and provider='whatsapp'", (uid,))


def _libera(uid, motivo):
    if motivo == "opt_out":
        db.set_whatsapp_updates_opt_out(uid, False)
    elif motivo == "sem_acesso":
        promote_to_pro(uid)
    elif motivo.startswith("exclusao"):  # a exclusão foi cancelada
        q("""update auth_accounts set deletion_status=null, deletion_requested_at=null, deletion_scheduled_for=null,
                    deletion_processing_started_at=null where user_id=%s""", (uid,))
    else:
        db.bind_identity("whatsapp", FONE_A, uid)


@pytest.mark.parametrize("motivo", ["opt_out", "sem_acesso", "sem_telefone", "exclusao_agendada", "exclusao_processando"])
def test_barrado_nao_recebe_nao_reserva(user_id, monkeypatch, motivo):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, 20)
    _barra(user_id, motivo)

    assert _roda(ag, posts) == (0, 0) and reservadas(user_id) == 0
    assert _roda(agora(15), posts) == (0, 0) and reservadas(user_id) == 0

    _libera(user_id, motivo)
    q("update auth_accounts set last_activity_at=%s where user_id=%s", (ag - timedelta(hours=1), user_id))
    assert _roda(agora(16), posts) == (1, 1) and reservadas(user_id) == 1
    assert _roda(agora(17), posts) == (0, 1)


def test_leitura_que_falha_nao_reserva_e_a_proxima_hora_envia(user_id, monkeypatch):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, 8)

    def _quebra(*a, **k):
        raise RuntimeError("banco caiu")

    with monkeypatch.context() as m:
        m.setattr("db.get_whatsapp_updates_opt_out", _quebra)
        assert _roda(ag, posts) == (0, 0) and reservadas(user_id) == 0
    assert _roda(agora(13), posts) == (1, 1)


@pytest.mark.parametrize("falha", ["claim", "montagem", "release"])
def test_falha_no_meio_de_um_usuario_nao_derruba_o_outro(user_id, monkeypatch, falha):
    """B1: claim, montagem do texto e soltura ficam dentro do `try` por usuário. A falha depois da reserva solta a
    reserva (best effort); no `release` que falha, a reserva fica (a mensagem se perde, declarado na ADR)."""
    from conftest import _cleanup_user
    import db.bank_lembrete as bl
    ag = agora()
    posts = armar(monkeypatch)
    outro = user_id + 1
    try:
        db.ensure_user(outro)
        dono(user_id, FONE_A, ag)
        dono(outro, FONE_B, ag)
        declara_ha(user_id, ag, 9, alvo="do A")  # A é o primeiro da listagem (declared_at mais antigo)
        declara_ha(outro, ag, 8, alvo="do B")
        real_claim = bl.claim_declaracao_lembretes

        def _quebra(*a, **k):
            raise RuntimeError("banco caiu")

        if falha == "claim":
            monkeypatch.setattr(bl, "claim_declaracao_lembretes",
                                lambda uid, *a: _quebra() if uid == user_id else real_claim(uid, *a))
        elif falha == "montagem":
            real = dl._mensagem_declaracoes
            monkeypatch.setattr(dl, "_mensagem_declaracoes",
                                lambda rows: _quebra() if "do A" in str(rows) else real(rows))
        else:
            boa = requests.post  # o `_post` do `armar`

            def _so_o_b_aceita(url, **kw):
                if kw["json"]["to"] == FONE_A:
                    raise ConnectionError("meta fora do ar")  # ninguém de A aceita: a soltura roda e quebra
                return boa(url, **kw)

            monkeypatch.setattr("requests.post", _so_o_b_aceita)
            monkeypatch.setattr(bl, "release_declaracao_lembretes", _quebra)

        n = dl.notify_declaracoes_whatsapp_once(now=ag)

        assert n == 1 and len(para(posts, FONE_B)) == 1 and para(posts, FONE_A) == []  # o tick não abortou
        assert reservadas(outro) == 1
        # claim/montagem: a reserva de A foi devolvida (ou nem existiu); release: a soltura quebrou, a reserva fica
        assert reservadas(user_id) == (1 if falha == "release" else 0)
    finally:
        _cleanup_user(outro)


# --- G6: envio que falha --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("status", [400, 401])
def test_recusa_em_todos_os_destinos_solta_a_reserva(user_id, monkeypatch, status):
    """400 levanta dentro do `send_text`, 401 devolve None: nenhum destino aceitou, a reserva volta e a hora
    seguinte tenta de novo (a Meta normalizou)."""
    ag = agora()
    posts = armar(monkeypatch, status=status)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, 8)

    assert _roda(ag, posts) == (0, 1)  # tentou uma vez
    assert reservadas(user_id) == 0 and recent_event_exists(EVENTO, user_id, 1) is False

    posts = armar(monkeypatch)
    assert _roda(agora(13), posts) == (1, 1)
    assert reservadas(user_id) == 1 and recent_event_exists(EVENTO, user_id, 1) is True


def test_um_destino_aceita_mantem_a_reserva(user_id, monkeypatch):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    db.bind_identity("whatsapp", FONE_B, user_id)
    declara_ha(user_id, ag, 8)
    boa = requests.post  # o `_post` do `armar`

    def _so_o_a_aceita(url, **kw):
        if kw["json"]["to"] == FONE_B:
            raise ConnectionError("meta fora do ar")
        return boa(url, **kw)

    monkeypatch.setattr("requests.post", _so_o_a_aceita)
    assert dl.notify_declaracoes_whatsapp_once(now=ag) == 1
    assert len(para(posts, FONE_A)) == 1 and reservadas(user_id) == 1
    assert recent_event_exists(EVENTO, user_id, 1) is True
    assert dl.notify_declaracoes_whatsapp_once(now=agora(13)) == 0  # mantém: não reenvia


def test_erro_depois_do_envio_aceito_nao_solta_a_reserva(user_id, monkeypatch):
    """O `except` externo só solta a reserva se NADA saiu: soltar depois de aceito reenviaria a mesma mensagem. A falha
    entra no log do envio (o real engole os erros; aqui só o de `source="declaracao_lembrete"` levanta)."""
    import core.observability as obs
    ag, real, posts = agora(), obs.log_system_event_sync, armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, 8)

    def _quebra_so_o_do_lembrete(*a, **k):  # o `send_text` também loga por aqui, e esse tem de seguir inteiro
        if k.get("source") == "declaracao_lembrete":
            raise RuntimeError("log fora")
        return real(*a, **k)
    monkeypatch.setattr(obs, "log_system_event_sync", _quebra_so_o_do_lembrete)
    assert dl.notify_declaracoes_whatsapp_once(now=ag) == 1  # o envio contou
    assert len(para(posts, FONE_A)) == 1 and reservadas(user_id) == 1  # a reserva fica
    assert dl.notify_declaracoes_whatsapp_once(now=agora(13)) == 0 and len(para(posts, FONE_A)) == 1  # sem reenvio


def test_soltar_nao_apaga_o_que_ja_foi_enviado(user_id, monkeypatch):
    """A soltura é só das linhas que ESTE tick reservou (`launch_id = any(...)`): a enviada em outra hora fica."""
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    velha = declara_ha(user_id, ag, 9, alvo="velha")
    assert _roda(ag, posts) == (1, 1)  # enviada e reservada
    nova = declara_ha(user_id, ag, 8, alvo="nova")
    armar(monkeypatch, status=401)  # a nova falha em todos os destinos e é solta

    assert dl.notify_declaracoes_whatsapp_once(now=agora(13)) == 0
    assert lembretes(user_id)[velha] is not None and lembretes(user_id)[nova] is None


def test_dois_telefones_do_mesmo_usuario_recebem(user_id, monkeypatch):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    db.bind_identity("whatsapp", FONE_B, user_id)
    declara_ha(user_id, ag, 8)

    assert dl.notify_declaracoes_whatsapp_once(now=ag) == 2
    assert len(para(posts, FONE_A)) == 1 and len(para(posts, FONE_B)) == 1 and reservadas(user_id) == 1


def test_o_log_nao_leva_alvo_valor_nem_telefone(user_id, monkeypatch):
    ag = agora()
    armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    lid = declara_ha(user_id, ag, 8, alvo="segredo", valor=77)
    dl.notify_declaracoes_whatsapp_once(now=ag)

    ev = q("select details, message from system_event_logs where user_id=%s and event_type=%s", (user_id, EVENTO))
    assert ev["details"] == {"launch_ids": [lid]}
    assert "segredo" not in str(ev) and FONE_A not in str(ev) and "77" not in str(ev["message"])


# --- o passo ligado no loop ---------------------------------------------------------------------------------------

def test_loop_real_manda_o_lembrete(user_id, monkeypatch):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, 8)
    monkeypatch.setattr(rc, "now_tz", lambda: ag)

    _um_tick(monkeypatch)

    assert len(para(posts, FONE_A)) == 1 and reservadas(user_id) == 1


@pytest.mark.parametrize("quebra", ["lembrete_quebra", "autopay_quebra"])
def test_falha_de_um_passo_nao_derruba_o_outro(user_id, monkeypatch, quebra):
    ag = agora()
    posts = armar(monkeypatch)
    dono(user_id, FONE_A, ag)
    declara_ha(user_id, ag, 8)
    monkeypatch.setattr(rc, "now_tz", lambda: ag)
    chamadas = []

    def _quebra(*a, **k):
        raise RuntimeError("quebrou")

    if quebra == "lembrete_quebra":
        monkeypatch.setattr(dl, "notify_declaracoes_whatsapp_once", _quebra)
        monkeypatch.setattr(rc, "notify_autopay_notices_whatsapp_once", lambda now: chamadas.append(now))
    else:
        monkeypatch.setattr(rc, "notify_autopay_notices_whatsapp_once", _quebra)

    _um_tick(monkeypatch)

    if quebra == "lembrete_quebra":
        assert chamadas == [ag]  # o autopay rodou ANTES e o laço seguiu para a próxima volta
        assert para(posts, FONE_A) == []
    else:
        assert len(para(posts, FONE_A)) == 1
