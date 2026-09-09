"""
tests/test_payment_reminder.py — o lembrete de pagamento do 6º dia de atraso.

`core.services.payment_reminder.check_payment_reminder`, no mesmo tick de 24 h
que já hospeda `_check_trial_ending` e `_check_free_upgrade_nudge`.

Ele NÃO avisa de corte nenhum — este PR não corta acesso. O teste da copy
abaixo existe justamente porque a versão anterior prometia "amanhã eu pauso"
sem nada pausar depois, e foi o que reprovou o PR.

CONTROLES do grupo (o "conserto" aqui é o funil inteiro; a injeção vai num caso
que estava VERDE):


Regra dos controles deste arquivo: citar o PREDICADO a injetar, nomear só os
VERMELHOS, nunca escrever `N passed` — ver `docs/controles_declarados.md`.

  • NEGATIVO 1 — troque a janela do SQL (`db.dunning.list_payment_reminder_
    candidates`) por `past_due_since is not null` sem as duas fronteiras:
      VERMELHO: test_janela[5.9] e [9.1]. Os casos [6.1] e [8.9] continuam
                passando, e é isso que prova que a janela DISCRIMINA em vez de
                "mandar para todo mundo".
  • NEGATIVO 2 — apague o `if not payment_reminder_enabled(): return` da
    primeira linha de `check_payment_reminder`:
      VERMELHO: test_flag_desligada_nao_manda_nem_consulta, e só ele — a
                fixture liga a flag nos outros, o que é o que prova que a
                injeção mede o FREIO e não o funil.
  • POSITIVO — test_janela[6.1]/[8.9] e a segunda metade do teste da flag
    (religa e o e-mail sai, na mesma conta e no mesmo tick). Sem eles o arquivo
    passaria num funil que nunca manda e-mail, que é pior que o bug.

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
from core.services import payment_reminder, plan_service
from db.connection import get_conn
from db.plan_grants import upsert_grant


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    # A flag nasce DESLIGADA em produção; aqui ela é ligada para que os testes
    # de janela/dedupe/opt-out meçam o funil, e o teste dela é o único que a
    # desliga. O template de WhatsApp continua opt-in por env.
    monkeypatch.setenv("PAYMENT_REMINDER_ENABLED", "1")
    monkeypatch.delenv("WA_TEMPLATE_PAYMENT_REMINDER", raising=False)
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
    """Destinos do lembrete, SÓ desta conta.

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

    monkeypatch.setattr(email_service, "send_payment_reminder_email", _fake)
    return meus


def _tick():
    asyncio.run(payment_reminder.check_payment_reminder())


def _limpar_eventos(uid: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from system_event_logs where user_id = %s", (uid,))
        conn.commit()


# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dias,avisa", [
    (5.9, False),   # fronteira de baixo, fora: cedo demais (smart retry inicial)
    (6.1, True),    # fronteira de baixo, dentro: o 6º dia
    (8.9, True),    # fronteira de cima, dentro: a largura é de 3 dias
    (9.1, False),   # fronteira de cima, fora
])
def test_janela(user_id, monkeypatch, dias, avisa):
    """A janela abre no 6º dia e tem `PAYMENT_REMINDER_WINDOW_DAYS` de largura:
    `[6d, 9d)`. As quatro fronteiras, nos DOIS lados. Ela não é de 1 dia porque
    o tick não é de 24 h exatos (o `sleep` de `run_engagement_loop` vem depois
    de todo o trabalho) e restart/deploy atrasam muito mais — ver a invariante
    em `core/services/billing_dunning`."""
    _inadimplente(user_id, dias=dias)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    _tick()
    assert bool(enviados) is avisa, (dias, enviados)


# O tick INTEIRO perdido e o "dois ticks na mesma janela larga" moram em
# tests/test_payment_reminder_janela.py: são o par da LARGURA da janela (e
# precisam envelhecer o evento de dedupe), e este arquivo está perto do teto de
# 350 linhas de tests/test_max_lines_python.py.


def test_dedupe_nao_reenvia_no_mesmo_ciclo(user_id, monkeypatch):
    """Um lembrete por ciclo. Dois ticks dentro da mesma janela e na MESMA
    idade mandam UM e-mail — dedupe por system_event_logs, não por coluna. Quem
    mede a largura (ticks em dias diferentes) é o arquivo apontado acima."""
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
                " where event_type = 'payment_reminder_sent' and user_id = %s",
                (user_id,))
            assert cur.fetchone()["n"] == 1


def test_email_sai_sem_template_de_whatsapp(user_id, monkeypatch):
    """O e-mail é o caminho GARANTIDO; o WhatsApp é melhoria que liga quando o
    template existir na Meta. Sem a env, `_wa_lembrete` devolve False sem
    sequer importar o wa_client — e o e-mail sai igual.

    O canal WhatsApp mora em `core/services/payment_reminder_wa.py` (assunto
    próprio, e o `payment_reminder.py` bateu no teto de 350 linhas); o resto
    dele tem arquivo de teste próprio, `test_payment_reminder_whatsapp.py`."""
    from core.services.payment_reminder_wa import _wa_lembrete
    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    # Esta conta NÃO desligou o canal (o `_inadimplente` não liga o opt-out),
    # então o que faz o retorno ser False aqui é a ausência do template, que é o
    # que o teste mede. O opt-out é lido DENTRO de `_wa_lembrete`, no ponto do
    # envio, e quem o mede é `test_payment_reminder_consentimento.py`.
    assert _wa_lembrete(user_id) is False
    _tick()
    assert len(enviados) == 1


def test_grant_pix_vigente_nao_recebe_lembrete(user_id, monkeypatch):
    """Quem tem grant Pix vigente não depende do cartão para acessar — lembrar
    de pagar algo que já está pago por outro caminho é ruído."""
    _inadimplente(user_id, dias=6.5)
    agora = datetime.now(timezone.utc)
    upsert_grant(user_id, "pix", f"pix:{user_id}", "pro",
                 agora - timedelta(days=10), agora + timedelta(days=355), 1, "evt_pix")
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    _tick()
    assert enviados == []


def test_allowlist_nao_recebe_lembrete(user_id, monkeypatch):
    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)
    monkeypatch.setattr(plan_service, "_ACCESS_ALLOWLIST", {int(user_id)})
    _tick()
    assert enviados == []


def test_status_fora_da_lista_de_tres_nao_recebe_lembrete(user_id, monkeypatch):
    """Relógio órfão (status fora dos três, relógio preenchido) não gera
    lembrete: o funil filtra por status no SQL.

    Órfão NÃO é dado morto — quem escreveu isso aqui contradizia, no mesmo
    commit, a docstring de `billing_dunning`. Ele é dado dormente: o
    `invoice.payment_failed` seguinte devolve o status para a lista, o
    `claim_past_due_since` vê `rowcount 0` e o relógio do ciclo novo fica preso
    na data velha, fora da janela do lembrete. Quem impede o órfão de existir são
    os dois writers de `last_payment_status` (tests/test_billing_dunning.py);
    este filtro é defesa em profundidade."""
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


def test_copy_do_lembrete_nao_promete_perda_de_acesso():
    """A regra dura deste PR, virada em teste porque foi ela que reprovou a
    versão anterior: a copy do lembrete não pode prometer pausa, bloqueio ou
    perda de acesso, porque nada disso acontece.

    Não é teste de estilo. `send_payment_reminder_email` é a ÚNICA mensagem que
    este PR manda por causa de inadimplência; quem trouxer o corte reescreve a
    copy junto — e este teste fica vermelho para lembrar.
    """
    from core.services import email_service

    capturado = {}

    def _fake(**kwargs):
        capturado.update(kwargs)
        return True

    original = email_service.send_email
    email_service.send_email = _fake
    try:
        assert email_service.send_payment_reminder_email("a@b.local") is True
    finally:
        email_service.send_email = original

    texto = " ".join([capturado["subject"], capturado["html_body"],
                      capturado["text_body"]]).lower()
    for proibida in ("pauso", "pausar", "pausei", "bloque", "suspend",
                     "perder acesso", "para de atender", "amanhã", "amanha"):
        assert proibida not in texto, (proibida, capturado["subject"])
    # POSITIVO do par: o e-mail continua sendo sobre a cobrança pendente e
    # continua levando para a página de atualizar o cartão. Sem isto, uma copy
    # vazia passaria neste teste.
    assert "cobrança" in texto or "cobranca" in texto
    assert "/conta" in capturado["html_body"]


def test_flag_desligada_nao_manda_nem_consulta(user_id, monkeypatch):
    """`PAYMENT_REMINDER_ENABLED` é o freio, e ele para ANTES da query.

    A guarda é a primeira linha de `check_payment_reminder` de propósito: com a
    flag desligada não há e-mail, não há evento gravado e o funil não é nem
    consultado. Contar a chamada em vez de fazê-la levantar não é preciosismo —
    `check_payment_reminder` embrulha a busca de candidatos num try/except que
    LOGA e retorna, então um fake que levanta deixaria este teste verde com e
    sem o freio.

    A conta é a MESMA que `test_janela[6.1]` avisa, e a segunda metade
    religa a flag no mesmo tick: é o par que separa "o freio funciona" de "esta
    conta nunca receberia".
    """
    import db.dunning

    _inadimplente(user_id, dias=6.5)
    enviados = _espia(monkeypatch, user_id)
    _limpar_eventos(user_id)

    consultas = []
    real = db.dunning.list_payment_reminder_candidates

    def _spy(*a, **k):
        consultas.append(a)
        return real(*a, **k)

    monkeypatch.setattr(db.dunning, "list_payment_reminder_candidates", _spy)

    monkeypatch.setenv("PAYMENT_REMINDER_ENABLED", "0")
    _tick()
    assert enviados == []
    assert consultas == [], "consultou o funil com a flag desligada"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from system_event_logs"
                        " where event_type = 'payment_reminder_sent'"
                        "   and user_id = %s", (user_id,))
            assert cur.fetchone()["n"] == 0

    # POSITIVO, mesma conta, mesmo tick: religou → consulta e manda.
    monkeypatch.setenv("PAYMENT_REMINDER_ENABLED", "1")
    _tick()
    assert len(consultas) == 1, consultas
    assert len(enviados) == 1, enviados


def test_flag_nasce_desligada_e_le_o_ambiente_a_cada_chamada(monkeypatch):
    """Default OFF e leitura dinâmica (sem cache de módulo) — mesmo contrato de
    `plan_service.paywall_enabled`, para ligar/desligar sem redeploy."""
    monkeypatch.delenv("PAYMENT_REMINDER_ENABLED", raising=False)
    assert payment_reminder.payment_reminder_enabled() is False
    for ligado in ("1", "true", "yes", "ON", " True "):
        monkeypatch.setenv("PAYMENT_REMINDER_ENABLED", ligado)
        assert payment_reminder.payment_reminder_enabled() is True, ligado
    for desligado in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("PAYMENT_REMINDER_ENABLED", desligado)
        assert payment_reminder.payment_reminder_enabled() is False, desligado
