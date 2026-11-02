"""#657 C2 (PR-7 do grupo D), os agendadores de serviço: a falha de um usuário grava o dono na
coluna `user_id` de `system_event_logs`, só com tipo + sqlstate (D6) e sem o texto cru da
exceção (o `DETAIL` do psycopg, o e-mail na recusa do provedor, o token do aparelho), e o laço
segue para o próximo usuário.

Cada laço roda o JOB real com A e B, A falhando e B seguindo; a prova é sobre B (acumulou,
recebeu, foi chamado, foi entregue). Postgres real, o `_DashboardHandler` do root,
`build_user_export_zip` e `delete_user_data` reais.

T14 rendimento (`accrue_all_users_investments`); T15 lembrete de pagamento, pelas 4 etapas de
`check_payment_reminder`; T16 `_wa_lembrete`, tabela de chamadas diretas (os 5 sites, e o
"nunca levanta"); T17 trial ending (`_check_trial_ending`); T18 IA proativa
(`_precompute_once`); T19 push, falha geral de `send_push_to_user` (não é laço: os chamadores
em laço têm `except` próprio); T20 entrega do e-book (`entregar_pendentes`).
`stripe_email_sync._sincronizar` está nas `JUSTIFICADAS` da guarda; o teste dela mora em
`test_settings_contact_stripe_sync.py::test_email_recusado_fica_fora_do_log`.

Os laços rodam com a falha no 1º visitado (o laço SEGUE) e no 2º (a linha é do dono da volta
ATUAL: um `extra={"user_id": lista[0]}` só passaria com a falha no 1º). O T14 já distingue,
porque A e B falham os dois, cada um num tipo.

CONTROLES (CLAUDE.md §3), medidos no PR: tirar o `extra` de `investment_scheduler` → T14
vermelho; `exc_info=True` de volta → T14 vermelho (`traceback` no `details`); voltar `exc` em
`payment_reminder` (envio) → T15 `envio` vermelho (o e-mail de A); voltar a f-string do e-book
→ T20 vermelho; o dono do 1º do lote no lugar do da volta (`users[0]` no proactive,
`elegiveis[0]` no payment_reminder, `users[0]` no trial ending) → T18, T15 e T17 `falha_no_2o`
vermelhos. Positivos: B segue em todos; os retornos do T16 e do T19 são os da base.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

import db
import db.privacy as privacy
from _billing_grants_helpers import conta as _conta_auth
from _log_dono_helpers import (  # noqa: F401 (banco_real é fixture autouse)
    FALHA_NO, FONE, _exportados, _isola_e_apaga, _limpa, _linhas, _marca, _marcador, _nenhuma, _uid, _uma,
    _violacao, banco_real)
from core.services import engagement_scheduler, investment_scheduler, payment_reminder
from core.services import ebook_entrega, proactive_ai_scheduler, push_service
from core.services.payment_reminder_wa import _wa_lembrete
from db.connection import get_conn
from test_ebook_entrega import _conta as _conta_ebook, mundo  # noqa: F401 (mundo é fixture)
from test_payment_reminder import _inadimplente, _limpar_eventos, _tick
from test_payment_reminder_lote import _segunda_conta

CAUSA_PG = "causa=UniqueViolation sqlstate=23505"


def _so_para(alvo, real, exc):
    """Envelope do real que levanta `exc` só quando o 1º argumento é `alvo`."""
    def f(x, *a, **k):
        if x == alvo:
            raise exc
        return real(x, *a, **k)
    return f


# ── T14. rendimento ──────────────────────────────────────────────────────────

def test_t14_rendimento_a_e_b_falham_em_tipos_diferentes_e_o_laco_segue(monkeypatch):
    a, b, mark_a, mark_b = _uid(), _uid(), _marcador(), _marcador()
    _, inv_b, _ = db.create_investment_db(b, "cdb do b", 0.01, "daily")
    db.create_investment_db(a, "cdb do a", 0.01, "daily")
    db.create_pocket(b, "viagem")
    monkeypatch.setattr(db, "list_users_with_unfrozen_interest", lambda: [a, b])
    monkeypatch.setattr(db, "accrue_all_investments", _so_para(a, db.accrue_all_investments, _violacao(mark_a)))
    monkeypatch.setattr(db, "accrue_all_pockets", _so_para(b, db.accrue_all_pockets, _violacao(mark_b)))
    marca = _marca()

    assert investment_scheduler.accrue_all_users_investments() == {"users": 2, "updated": 0, "failed": 2}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select interest_frozen_at from investments where id = %s", (inv_b,))
        assert cur.fetchone()["interest_frozen_at"] is not None, "o investimento de B não acumulou"
    linhas = {}
    for u, tipo, marcador in ((a, "investimentos", mark_a), (b, "caixinhas", mark_b)):
        da_u = _linhas(marca, "user_id = %s", (u,))
        log = _uma(da_u, "logger.warning", investment_scheduler.__name__, f"Falha ao atualizar {tipo} ")
        evento = _uma(da_u, "investment_accrual_user_failed", "investment_scheduler", f"Falha ao atualizar {tipo} ")
        for linha in (log, evento):
            _limpa(linha, marcador)
        assert CAUSA_PG in log["message"] and evento["details"]["sqlstate"] == "23505", (log, evento)
        linhas[u] = (log["id"], evento["id"])
    _isola_e_apaga(a, b, set(linhas[a]), linhas[b][0], marca)


# ── T15. lembrete de pagamento ───────────────────────────────────────────────

def _envio_de_email(alvo_email, exc):
    from core.services import email_service
    destinos: list[str] = []

    def f(to, dash=""):
        if to == alvo_email:
            raise exc
        if str(to).startswith("dun-"):
            destinos.append(to)
        return True
    return email_service, f, destinos


_COBRANCA = [
    ("grant", "[cobranca] checagem de grant falhou"),
    ("revalidacao", "[cobranca] revalidacao falhou"),
    ("decriptacao", "[cobranca] decriptacao do e-mail falhou"),
    ("envio", "[cobranca] falha enviando"),
]


def _lote_com_a_em(monkeypatch, a: int, falha_no: int) -> list:
    """O lote REAL, reordenado para A vir em 1º ou por ÚLTIMO (o funil não tem `ORDER BY`)."""
    from db import dunning
    real, lotes = dunning.list_payment_reminder_candidates, []

    def envelope(grace_days):
        rows = sorted(real(grace_days), key=lambda r: (int(r["user_id"]) == a) == (falha_no == 2))
        lotes.append([int(r["user_id"]) for r in rows])
        return rows
    monkeypatch.setattr(dunning, "list_payment_reminder_candidates", envelope)
    return lotes


@FALHA_NO
@pytest.mark.parametrize("etapa,prefixo", _COBRANCA, ids=[c[0] for c in _COBRANCA])
def test_t15_lembrete_a_falha_numa_etapa_e_b_recebe(user_id, monkeypatch, etapa, prefixo, falha_no):
    from db import dunning
    monkeypatch.setenv("PAYMENT_REMINDER_ENABLED", "1")
    monkeypatch.delenv("WA_TEMPLATE_PAYMENT_REMINDER", raising=False)
    a, marcador = user_id, _marcador()
    _inadimplente(a, dias=6.5)
    _limpar_eventos(a)
    b = _segunda_conta(a)
    email_a = f"dun-{a}@t.local"
    exc = (RuntimeError(f"550 caixa {email_a} recusou {marcador} {FONE}") if etapa == "envio"
           else _violacao(marcador))
    modulo, envio, destinos = _envio_de_email(email_a if etapa == "envio" else None, exc)
    monkeypatch.setattr(modulo, "send_payment_reminder_email", envio)
    if etapa == "grant":
        monkeypatch.setattr(payment_reminder, "_pago_por_outro_caminho",
                            _so_para(a, payment_reminder._pago_por_outro_caminho, exc))
    elif etapa == "revalidacao":
        monkeypatch.setattr(dunning, "lembrete_ainda_vale", _so_para(a, dunning.lembrete_ainda_vale, exc))
    elif etapa == "decriptacao":
        monkeypatch.setattr(payment_reminder, "_resolver_email", _so_para(a, payment_reminder._resolver_email, exc))
    lotes = _lote_com_a_em(monkeypatch, a, falha_no)
    marca = _marca()
    _tick()

    pos = lotes[0].index(a)
    assert pos == (0 if falha_no == 1 else len(lotes[0]) - 1) and (pos < lotes[0].index(b)) == (falha_no == 1), lotes
    assert destinos == [f"dun-{b}@t.local"], destinos
    da_b = _linhas(marca, "user_id = %s", (b,))
    ok_b = _uma(da_b, "payment_reminder_sent", "engagement_scheduler")
    _nenhuma(da_b, "logger.error", prefixo)
    linha = _uma(_linhas(marca, "user_id = %s", (a,)), "logger.error", payment_reminder.__name__, prefixo)
    _limpa(linha, marcador, email_a)
    assert ("causa=RuntimeError sqlstate=None" if etapa == "envio" else CAUSA_PG) in linha["message"], linha
    _isola_e_apaga(a, b, {linha["id"]}, ok_b["id"], marca)


# ── T16. `_wa_lembrete`: tabela de chamadas diretas ──────────────────────────

def _identidades(*nums):
    return lambda uid: [{"provider": "whatsapp", "external_id": n} for n in nums]


def _envio_wa(*respostas):
    """`send_template` por destino, na ordem: exceção levanta, o resto é devolvido."""
    fila = list(respostas)

    def f(to, nome, language_code=None):
        r = fila.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return f


_WA = [  # (caso, opt-out, ciclo aberto, identidades, envios, retorno, [(prefixo, com causa)])
    ("opt_out", "levanta", True, None, None, False,
     [("[cobranca] leitura do opt-out de WhatsApp falhou", CAUSA_PG)]),
    ("ciclo", None, "levanta", None, None, False,
     [("[cobranca] revalidacao da inadimplencia falhou", CAUSA_PG)]),
    ("destino_e_parcial", None, True, ("5521911112222", "5521933334444"), ({}, "meta"), True,
     [("[cobranca] WhatsApp: um destino falhou", "causa=RuntimeError sqlstate=None"),
      ("[cobranca] WhatsApp parcial", "1 de 2 destinos")]),
    ("parcial_401", None, True, ("5521911112222", "5521933334444"), ({}, None), True,
     [("[cobranca] WhatsApp parcial", "1 de 2 destinos")]),
    ("setup", None, True, "levanta", None, False,
     [("[cobranca] WhatsApp não enviado", CAUSA_PG)]),
]


@pytest.mark.parametrize("caso,opt_out,ciclo,ids,envios,retorno,esperados", _WA, ids=[w[0] for w in _WA])
def test_t16_wa_lembrete_loga_com_dono_e_nao_levanta(monkeypatch, caplog, caso, opt_out, ciclo, ids, envios,
                                                     retorno, esperados):
    from adapters.whatsapp import wa_client
    from db import dunning, reports
    uid, marcador = _uid(), _marcador()
    monkeypatch.setenv("WA_TEMPLATE_PAYMENT_REMINDER", "lembrete_pagamento")
    if opt_out == "levanta":
        monkeypatch.setattr(reports, "get_whatsapp_updates_opt_out", _so_para(uid, lambda u: False, _violacao(marcador)))
    monkeypatch.setattr(dunning, "ciclo_de_atraso_aberto",
                        _so_para(uid, lambda u: True, _violacao(marcador)) if ciclo == "levanta" else lambda u: True)
    if ids == "levanta":
        monkeypatch.setattr(db, "list_identities_by_user", _so_para(uid, lambda u: [], _violacao(marcador)))
    elif ids:
        monkeypatch.setattr(db, "list_identities_by_user", _identidades(*ids))
    if envios:
        meta = RuntimeError(f'WA send_template failed 400: {{"details": "{FONE} {marcador}"}}')
        monkeypatch.setattr(wa_client, "send_template", _envio_wa(*(meta if e == "meta" else e for e in envios)))
    with caplog.at_level(logging.WARNING, logger="core.services.payment_reminder_wa"):
        assert _wa_lembrete(uid) is retorno

    records = [r for r in caplog.records if r.name == "core.services.payment_reminder_wa"]
    assert [r.getMessage().split(" user_id=")[0] for r in records] == [p for p, _ in esperados], \
        [r.getMessage() for r in records]
    for r, (_, trecho) in zip(records, esperados):
        assert getattr(r, "user_id", None) == uid and r.exc_info is None, r.__dict__
        assert trecho in r.getMessage() and marcador not in r.getMessage() and FONE not in r.getMessage(), r.getMessage()


# ── T17. trial ending ────────────────────────────────────────────────────────

@FALHA_NO
def test_t17_trial_ending_um_falha_e_o_outro_recebe(monkeypatch, falha_no):
    from core.services import email_service
    a, b = (1_000_000 + uuid.uuid4().int % 1_000_000_000 for _ in "ab")
    marcador = _marcador()
    fim = datetime.now(timezone.utc) + timedelta(days=3)
    emails = {f"gr-{u}@t.local": u for u in (a, b)}
    for u in (a, b):
        db.ensure_user(u)                                # sem o `promote_to_pro`: o e-mail é o do `conta`
        _conta_auth(u, "pro", fim, "trialing")
    visitados: list[int] = []                            # a busca não tem `ORDER BY`: vale a visita
    recebidos: list[int] = []

    def envio(email, plan, expires_at, dashboard_url):
        if email not in emails:
            return False                                 # resíduo de outro teste: não é nosso
        visitados.append(emails[email])
        if len(visitados) == falha_no:
            raise RuntimeError(f"provedor recusou {email} {marcador} {FONE}")
        recebidos.append(emails[email])
        return True
    monkeypatch.setattr(email_service, "send_trial_ending_email", envio)
    marca = _marca()
    asyncio.run(engagement_scheduler._check_trial_ending())

    assert len(visitados) == 2, visitados
    alvo = visitados[falha_no - 1]
    outro = b if alvo == a else a
    assert recebidos == [outro], (recebidos, alvo)
    do_outro = _linhas(marca, "user_id = %s", (outro,))
    ok = _uma(do_outro, "trial_ending_email_sent", "engagement_scheduler")
    _nenhuma(do_outro, "logger.error", "[trial-ending] falha enviando")
    linha = _uma(_linhas(marca, "user_id = %s", (alvo,)), "logger.error", engagement_scheduler.__name__,
                 "[trial-ending] falha enviando")
    _limpa(linha, marcador, f"gr-{alvo}@t.local")
    assert "causa=RuntimeError sqlstate=None" in linha["message"], linha
    _isola_e_apaga(alvo, outro, {linha["id"]}, ok["id"], marca)


# ── T18. IA proativa ─────────────────────────────────────────────────────────

@FALHA_NO
def test_t18_ia_proativa_a_falha_e_b_e_chamado(monkeypatch, falha_no):
    a, b, marcador = _uid(), _uid(), _marcador()
    chamados: list[int] = []
    monkeypatch.setattr(proactive_ai_scheduler, "_fetch_active_users",
                        lambda: [a, b] if falha_no == 1 else [b, a])
    monkeypatch.setattr(proactive_ai_scheduler, "_precompute_user",
                        _so_para(a, chamados.append, _violacao(marcador)))
    marca = _marca()
    asyncio.run(proactive_ai_scheduler._precompute_once())

    assert chamados == [b], chamados
    (resumo,) = [x for x in _linhas(marca) if x["event_type"] == "proactive_ai_run"]
    assert (resumo["details"]["ok"], resumo["details"]["err"]) == (1, 1), resumo
    linha = _uma(_linhas(marca, "user_id = %s", (a,)), "logger.warning", proactive_ai_scheduler.__name__,
                 "[proactive-ai] user=")
    _limpa(linha, marcador)
    assert CAUSA_PG in linha["message"], linha
    assert _linhas(marca, "user_id = %s", (b,)) == []
    # B não grava linha: o positivo da exclusão é o resumo da rodada (sem dono), que fica.
    _isola_e_apaga(a, b, {linha["id"]}, resumo["id"], marca)


# ── T19. push, falha geral ───────────────────────────────────────────────────

def test_t19_push_falha_geral_tem_dono_e_nao_leva_o_token(monkeypatch):
    from _apoio_push import apns
    uid, marcador, token = _uid(), _marcador(), uuid.uuid4().hex * 2
    espiao = apns(monkeypatch)
    espiao.respostas[token] = (410, "Unregistered")
    monkeypatch.setattr(push_service, "list_push_tokens", lambda u: [{"token": token, "environment": "sandbox"}])

    def apaga(t):
        raise psycopg.errors.UniqueViolation(f"DETAIL:  Key (token)=({t} {marcador} {FONE}) already exists.")
    monkeypatch.setattr(push_service, "delete_push_token", apaga)
    marca = _marca()

    assert push_service.send_push_to_user(uid, "t", "c") == 0
    assert espiao.tokens() == [token]
    linha = _uma(_linhas(marca, "user_id = %s", (uid,)), "logger.error", push_service.__name__,
                 "APNs: falha geral")
    _limpa(linha, marcador, token)
    assert CAUSA_PG in linha["message"], linha


# ── T20. entrega do e-book ───────────────────────────────────────────────────

@FALHA_NO
@pytest.mark.parametrize("erro,sqlstate", [("RuntimeError", None), ("UniqueViolation", "23505")])
def test_t20_ebook_a_falha_e_b_e_entregue(mundo, monkeypatch, erro, sqlstate, falha_no):  # noqa: F811
    (a, email_a), (b, _) = _conta_ebook(senha="hash"), _conta_ebook(senha="hash")
    marcador = _marcador()
    for u, sid in ([(a, f"cs_a_{a}"), (b, f"cs_b_{b}")] if falha_no == 1 else [(b, f"cs_b_{b}"), (a, f"cs_a_{a}")]):
        mundo.pendencia(u, sid)                          # `abertas()` é por `criada_em`: a ordem do registro
    ordem = [u for u, _, _ in ebook_entrega.abertas() if u in (a, b)]
    assert ordem == ([a, b] if falha_no == 1 else [b, a]), ordem
    exc = (RuntimeError(f"provedor recusou {email_a} {marcador} {FONE}") if erro == "RuntimeError"
           else _violacao(marcador))
    monkeypatch.setattr(ebook_entrega, "send_ebook_email", _so_para(email_a, ebook_entrega.send_ebook_email, exc))
    marca = _marca()
    ebook_entrega.entregar_pendentes()

    assert mundo.linha(b, f"cs_b_{b}")["resultado"] == "enviado", "o laço não seguiu para B"
    assert mundo.linha(a, f"cs_a_{a}")["fechada_em"] is None
    linha = _uma(_linhas(marca, "user_id = %s", (a,)), "ebook_entrega_falhou", "billing")
    _limpa(linha, marcador, email_a)
    assert (linha["details"]["erro"], linha["details"]["sqlstate"]) == (erro, sqlstate), linha
    _nenhuma(_linhas(marca, "user_id = %s", (b,)), "ebook_entrega_falhou")
    # A entrega de B não grava evento: o positivo da exclusão é a pendência dele, que fica.
    assert linha["id"] in _exportados(a)[1] and linha["id"] not in _exportados(b)[1]
    privacy.delete_user_data(a)
    assert _linhas(marca, "user_id = %s", (a,)) == [], "log de A sobreviveu à exclusão"
    assert mundo.linha(b, f"cs_b_{b}")["resultado"] == "enviado"
