"""
tests/test_dunning_warning.py — T8: o aviso da véspera do corte por inadimplência.

`core.services.dunning_warning.check_dunning_warning`, no mesmo tick de
24 h que já hospeda `_check_trial_ending` e `_check_free_upgrade_nudge`. Quatro
perguntas, cada uma com um controle:

  • janela — 5 dias de atraso não avisa, 6,5 dias avisa, 8 dias já passou;
  • dedupe — segundo tick no mesmo ciclo não reenvia (decisão 6);
  • inerte com a flag off — avisar "amanhã você é cortado" sem corte ligado
    é mentir (o controle POSITIVO deste par é o teste da janela, que prova
    que com a flag ligada o e-mail SAI);
  • o e-mail sai mesmo sem o template de WhatsApp aprovado na Meta —
    `WA_TEMPLATE_DUNNING_WARNING` é vazio por padrão e o caminho é dormente.

`recent_event_exists` só dedupe de verdade se `system_event_logs` existir no
banco de teste — por isso o `garantir_system_event_logs` (ver o docstring dele
em tests/_billing_grants_helpers.py: sem ele um teste de reentrega já acusou 3
e-mails onde devia haver 1, e a causa era a tabela ausente).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from _billing_grants_helpers import conta as _conta, garantir_system_event_logs
from core.services import dunning_warning, plan_service
from db.connection import get_conn
from db.plan_grants import upsert_grant


@pytest.fixture(autouse=True)
def _liga_flag(monkeypatch):
    monkeypatch.setenv("DUNNING_BLOCK_ENABLED", "1")
    monkeypatch.delenv("WA_TEMPLATE_DUNNING_WARNING", raising=False)
    garantir_system_event_logs()


def _inadimplente(uid: int, *, dias: float, status: str = "past_due",
                  email: str | None = None) -> None:
    """Conta com cartão em atraso há `dias`, com e-mail e sem opt-out."""
    _conta(uid, "pro", datetime.now(timezone.utc) + timedelta(days=20), status)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts"
                "   set past_due_since = now() - make_interval(secs => %s),"
                "       email = coalesce(%s, email), email_enc = null,"
                "       engagement_opt_out = false"
                " where user_id = %s",
                (dias * 86400, email or f"dun-{uid}@t.local", uid),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)
    # Grant só de cartão: nada resgata a conta.
    agora = datetime.now(timezone.utc)
    upsert_grant(uid, "stripe", f"stripe:{uid}", "pro",
                 agora - timedelta(days=40), agora + timedelta(days=1), 1, "evt_dun")


def _espia(monkeypatch, uid: int) -> list:
    """Destinos do e-mail de véspera, SÓ desta conta.

    Filtra por e-mail em vez de contar tudo: o funil é uma varredura da base
    inteira, e um resíduo de outro teste no mesmo banco viraria falha
    intermitente com cara de bug.
    """
    from core.services import email_service
    todos, meus = [], []
    alvo = f"dun-{uid}@t.local"

    def _fake(to, dash=""):
        todos.append(to)
        if to == alvo:
            meus.append(to)
        return True

    monkeypatch.setattr(email_service, "send_dunning_warning_email", _fake)
    return meus


def _tick():
    asyncio.run(dunning_warning.check_dunning_warning())


def _limpar_eventos(uid: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from system_event_logs where user_id = %s", (uid,))
        conn.commit()


# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dias,avisa", [(5.0, False), (6.5, True), (8.0, False)])
def test_janela_de_um_dia(user_id, monkeypatch, dias, avisa):
    """A véspera é UMA janela de 1 dia: [6d, 7d). Antes é cedo, depois já
    cortou. Com o tick de 24 h, cada conta cai nela uma vez por ciclo."""
    _inadimplente(user_id, dias=dias)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    _tick()
    assert bool(enviados) is avisa, (dias, enviados)


def test_dedupe_nao_reenvia_no_mesmo_ciclo(user_id, monkeypatch):
    """Decisão 6: um aviso por ciclo. Dois ticks dentro da mesma janela
    mandam UM e-mail — dedupe por system_event_logs, não por coluna."""
    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    _tick()
    assert len(enviados) == 1, enviados
    _tick()
    assert len(enviados) == 1, enviados
    # E o evento de dedupe ficou registrado com o user_id certo (isolamento).
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from system_event_logs"
                " where event_type = 'dunning_warning_sent' and user_id = %s",
                (user_id,))
            assert cur.fetchone()["n"] == 1


def test_inerte_com_a_flag_desligada(user_id, monkeypatch):
    """Sem DUNNING_BLOCK_ENABLED o tick não manda nada — a MESMA conta que o
    teste da janela avisa. É o controle negativo da flag."""
    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    monkeypatch.setenv("DUNNING_BLOCK_ENABLED", "0")
    _tick()
    assert enviados == []
    # controle positivo, mesma conta e mesmo tick: religou → avisa.
    monkeypatch.setenv("DUNNING_BLOCK_ENABLED", "1")
    _tick()
    assert len(enviados) == 1


def test_email_sai_sem_template_de_whatsapp(user_id, monkeypatch):
    """O e-mail é o caminho GARANTIDO; o WhatsApp é melhoria que liga quando o
    template existir na Meta. Sem a env, `_wa_dunning_warning` devolve False
    sem sequer importar o wa_client — e o e-mail sai igual."""
    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    assert dunning_warning._wa_dunning_warning(user_id) is False
    _tick()
    assert len(enviados) == 1


def test_grant_pix_vigente_nao_recebe_aviso(user_id, monkeypatch):
    """Quem não vai ser bloqueado não recebe véspera de bloqueio: grant Pix
    vigente resgata (decisão 3), então o aviso não sai — mesma guarda do gate."""
    _inadimplente(user_id, dias=6.5)
    agora = datetime.now(timezone.utc)
    upsert_grant(user_id, "pix", f"pix:{user_id}", "pro",
                 agora - timedelta(days=10), agora + timedelta(days=355), 1, "evt_pix")
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    _tick()
    assert enviados == []


def test_allowlist_nao_recebe_aviso(user_id, monkeypatch):
    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    monkeypatch.setattr(plan_service, "_ACCESS_ALLOWLIST", {int(user_id)})
    _tick()
    assert enviados == []


def test_status_fora_da_lista_de_tres_nao_recebe_aviso(user_id, monkeypatch):
    """Relógio órfão em conta `active` é dado morto — a invariante vale também
    no aviso, e ela é aplicada no SQL do funil."""
    _inadimplente(user_id, dias=6.5, status="active")
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    _tick()
    assert enviados == []


def test_opt_out_de_engajamento_e_respeitado(user_id, monkeypatch):
    _inadimplente(user_id, dias=6.5)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update auth_accounts set engagement_opt_out = true"
                        " where user_id = %s", (user_id,))
        conn.commit()
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    _tick()
    assert enviados == []
