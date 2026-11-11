"""O que os testes do envio do aviso de insight (PL-06 B6, PR 2) compartilham.

Sem prefixo `test_`: o pytest não coleta. Postgres real; o único falso é o Resend (`_get_resend`): o resto do
`send_email` roda, inclusive o log em `system_event_logs`. `INSIGHT_EMAIL_USERS` lista só os usuários do teste,
então a volta do laço não toca conta de outro teste que rode no mesmo banco.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import db
from _insights_adesao_apoio import aderir, diga, sql
from conftest import usuario_pagante
from core.services import email_service
from core.services import insights_notificacao as notif
from test_api_app_sessao import completo
from utils_date import _tz

# O `send_email` de verdade: o `conftest` o troca por `lambda: True` em todo teste (autouse), e aqui só o Resend é falso.
_SEND_EMAIL = email_service.send_email


def as_(h=10, m=5, dias=0):
    """Um instante de hoje (+`dias`) no fuso do app."""
    return datetime.now(_tz()).replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=dias)


class Envio:
    def __init__(self, monkeypatch):
        self.mp = monkeypatch
        self.liberados: list[int] = []
        self.enviados: list[dict] = []
        self.recusar: set[str] = set()  # destinatários que o Resend recusa (send_email devolve False)

        def send(params):
            if params["to"][0] in self.recusar:
                raise RuntimeError("422 validation_error")
            self.enviados.append(params)

        monkeypatch.setattr(email_service, "send_email", _SEND_EMAIL)
        monkeypatch.setenv("RESEND_API_KEY", "re_teste")
        monkeypatch.setattr(email_service, "_get_resend", lambda: SimpleNamespace(Emails=SimpleNamespace(send=send)))
        monkeypatch.setenv("PLANS_V2_ENABLED", "1")
        monkeypatch.setenv("INSIGHT_EMAIL_USERS", "")

    def liberar(self, *uids):
        self.liberados += uids
        self.mp.setenv("INSIGHT_EMAIL_USERS", ",".join(map(str, self.liberados)))

    def usuario(self, plano="pro", aderir_=True):
        uid = completo(usuario_pagante(plano))
        self.liberar(uid)
        if aderir_:
            aderir(uid)
        return uid

    def tique(self, h=10, m=5, dias=0):
        return notif.run_insight_emails_once(as_(h, m, dias))

    def de_email(self, uid):
        return db.get_auth_user(uid)["email"]

    def de(self, uid):
        return [p for p in self.enviados if p["to"] == [self.de_email(uid)]]


@pytest.fixture
def envio(monkeypatch):
    return Envio(monkeypatch)


def gasto(uid, texto="gastei 90 no mercado", limite=100):
    """Um gasto pela conversa e um orçamento de `limite` na categoria que o Piggy escolheu: a chave do item."""
    diga(uid, texto)
    (cat,) = {r["categoria"] for r in sql("select categoria from launches where user_id = %s", (uid,))}
    db.upsert_budget(uid, cat, limite)
    return cat


def prefs(uid):
    linhas = sql("select last_insight_email_date d, insight_email_pendente p from daily_report_prefs"
                 " where user_id = %s", (uid,))
    return linhas[0] if linhas else {"d": None, "p": None}


def avisos(uid):
    """chave -> gravidade avisada."""
    return {r["chave"]: r["notificado_severidade"] for r in
            sql("select chave, notificado_severidade from insight_estado where user_id = %s and notificado_em is not null",
                (uid,))}
