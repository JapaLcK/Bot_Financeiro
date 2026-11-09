"""Assunto com texto do usuário fica fora do log e de `details.subject` (D4 da #657, decisões do dono).

O `send_email` grava o assunto com a coluna `user_id` nula, então a linha não sai com a conta e fica
até a retenção. Dois fluxos levam texto do usuário no assunto: o e-mail de agente (categoria, conta,
comerciante, caixinha) e o formulário de contato (assunto e nome do visitante). Os dois passam
`log_subject=` com um rótulo fixo, e o assunto ENVIADO não muda.

Caminho real: `POST /contact` e `run_xerife_once` + `run_agent_emails_once` até o `send_email` de
verdade. Só o Resend é dublado, e nos agentes os 3 gates de acesso e a leitura do e-mail.

CONTROLES (CLAUDE.md §3): trocar o rótulo pelo assunto em cada um dos 3 pontos de `send_email` ou tirar
`log_subject=` de um chamador deixa o caso daquele ponto vermelho. Positivo: o assunto entregue ao
Resend continua completo, e quem não passa `log_subject` segue gravando o assunto dele.

CLASSE CEGA: linhas antigas do banco seguem até a retenção. Chamador novo é com
`tests/test_log_d4_guarda.py::test_assunto_dinamico_de_send_email`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import core.reports.reports_daily as reports_daily
import core.services.email_service as email_service
import core.services.piggy_agents as piggy_agents
import core.services.plan_service as plan_service
import db
import frontend.finance_bot_websocket_custom as dashboard
from _log_dono_helpers import _marcador, _uid, _vazou
from test_account_deletion_pii_logs import (_SEND_EMAIL_REAL, _novos, marca,  # noqa: F401
                                            tabelas_admin)  # noqa: F401

ROTULO = "🤠 Xerife"
EMAIL = "fulana.agente@example.com"


def _resend(monkeypatch, falha: Exception | None = None) -> list[dict]:
    enviados: list[dict] = []

    def send(params):
        if falha:
            raise falha
        enviados.append(params)
        return {"id": "fake"}

    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    monkeypatch.setattr(email_service, "send_email", _SEND_EMAIL_REAL)
    monkeypatch.setattr(email_service, "_get_resend",
                        lambda: SimpleNamespace(Emails=SimpleNamespace(send=send)))
    return enviados


def _confere(caplog, marca: int, evento: str, rotulo: str, *proibidos: str) -> None:  # noqa: F811
    linhas = _novos(marca, evento)
    assert len(linhas) == 1, linhas
    assert linhas[0]["details"]["subject"] == rotulo, linhas[0]
    texto = f"{caplog.text} {linhas[0]['message']} {linhas[0]['details']}"
    assert not (p := _vazou(texto, *proibidos)), f"{p!r} vazou: {texto}"


# ── Contato ──────────────────────────────────────────────────────────────────

def _contato(marcador: str) -> tuple[dict, str, str]:
    nome, assunto = f"Zélia Conceição {marcador}a", f"Dúvida da fatura do açaí {marcador}b"
    corpo = {"name": nome, "email": "zelia@example.com", "subject": assunto, "message": "oi, tudo bem?"}
    return corpo, nome, assunto


def test_contato_grava_so_o_rotulo_e_o_assunto_enviado_nao_muda(monkeypatch, marca, caplog):  # noqa: F811
    corpo, nome, assunto = _contato(_marcador())
    enviados = _resend(monkeypatch)

    with caplog.at_level(logging.INFO):
        resposta = TestClient(dashboard.app).post("/contact", json=corpo)

    assert resposta.status_code == 200, resposta.text
    assert [e["subject"] for e in enviados] == [f"[Contato site] {assunto} — {nome}"]
    assert enviados[0]["to"] == ["contato@pigbankai.com"] and nome in enviados[0]["text"]
    assert any(r.getMessage().endswith(": Contato site") for r in caplog.records), caplog.text
    _confere(caplog, marca, "email_sent", "Contato site", nome, assunto)


def test_contato_com_falha_no_envio_grava_so_o_rotulo(monkeypatch, marca, caplog):  # noqa: F811
    corpo, nome, assunto = _contato(_marcador())
    _resend(monkeypatch, RuntimeError("resend fora"))

    with caplog.at_level(logging.INFO):
        resposta = TestClient(dashboard.app).post("/contact", json=corpo)

    assert resposta.status_code == 502, resposta.text
    _confere(caplog, marca, "email_failed", "Contato site", nome, assunto)


# ── Agente ───────────────────────────────────────────────────────────────────

def _xerife(monkeypatch, *categorias: str) -> int:
    """O Xerife de verdade: limite estourado em cada categoria digitada, até o evento gravado."""
    uid = _uid()
    db.activate_agent(uid, "xerife", {"limites": {c: 10 for c in categorias}})
    for c in categorias:
        db.add_launch_and_update_balance(uid, "despesa", 50.0, "consulta", None, categoria=c)
    monkeypatch.setattr(plan_service, "agents_ui_enabled", lambda *a, **k: True)
    monkeypatch.setattr(plan_service, "agent_kind_allowed", lambda *a, **k: True)
    monkeypatch.setattr(reports_daily, "filtrar_por_acesso", lambda ids: ids)
    monkeypatch.setattr(db, "get_user_email", lambda u: EMAIL)
    assert piggy_agents.run_xerife_once(user_id=uid)["fired"] == len(categorias)
    return uid


def _manda_emails() -> None:
    piggy_agents.run_agent_emails_once(now=datetime.now(timezone.utc) + timedelta(hours=1))


def test_agente_com_evento_unico_grava_so_o_rotulo(monkeypatch, marca, caplog):  # noqa: F811
    categoria = f"psiquiatra {_marcador()}".lower()
    _xerife(monkeypatch, categoria)
    enviados = _resend(monkeypatch)

    with caplog.at_level(logging.INFO):
        _manda_emails()

    assert [e["subject"] for e in enviados] == [f"{ROTULO} — Limite de {categoria} estourado"]
    assert any(r.getMessage().endswith(f": {ROTULO}") for r in caplog.records), caplog.text
    _confere(caplog, marca, "email_sent", ROTULO, categoria)


def test_agente_com_n_novidades_grava_so_o_rotulo(monkeypatch, marca, caplog):  # noqa: F811
    a, b = f"psiquiatra {_marcador()}".lower(), f"igreja {_marcador()}".lower()
    _xerife(monkeypatch, a, b)
    enviados = _resend(monkeypatch)

    with caplog.at_level(logging.INFO):
        _manda_emails()

    assert [e["subject"] for e in enviados] == [f"{ROTULO} — 2 novidades"]
    assert a in enviados[0]["html"] and b in enviados[0]["html"], "o corpo segue com as categorias"
    assert any(r.getMessage().endswith(f": {ROTULO}") for r in caplog.records), caplog.text
    _confere(caplog, marca, "email_sent", ROTULO, a, b)


def test_agente_com_falha_no_envio_grava_so_o_rotulo(monkeypatch, marca, caplog):  # noqa: F811
    categoria = f"psiquiatra {_marcador()}".lower()
    uid = _xerife(monkeypatch, categoria)
    _resend(monkeypatch, RuntimeError("resend fora"))

    with caplog.at_level(logging.INFO):
        _manda_emails()

    _confere(caplog, marca, "email_failed", ROTULO, categoria)
    agente = db.get_agent(uid, "xerife")
    assert len(db.list_unemailed_events(agente["id"])) == 1, "falha no envio devolve o evento à fila"


def test_relatorio_sem_rotulo_nao_cai_no_assunto(monkeypatch, marca, caplog):  # noqa: F811
    """Chamador que esquece o rótulo, como `scripts/smoke_test_emails.py`, não grava o assunto cheio."""
    categoria = f"psiquiatra {_marcador()}".lower()
    enviados = _resend(monkeypatch)

    with caplog.at_level(logging.INFO):
        assert email_service.send_agent_report_email(EMAIL, 1, f"Limite de {categoria}", "<p>oi</p>")

    assert enviados[0]["subject"] == f"Limite de {categoria}"
    _confere(caplog, marca, "email_sent", "Relatório de agente", categoria)


def test_log_subject_vazio_nao_cai_no_assunto(monkeypatch, marca, caplog):  # noqa: F811
    categoria = f"psiquiatra {_marcador()}".lower()
    _resend(monkeypatch)

    with caplog.at_level(logging.INFO):
        assert _SEND_EMAIL_REAL(EMAIL, f"Limite de {categoria}", "<p>oi</p>", log_subject="") is True

    _confere(caplog, marca, "email_sent", "", categoria)


# ── Quem não passa `log_subject` ─────────────────────────────────────────────

@pytest.mark.parametrize("falha, evento, ok", [(None, "email_sent", True),
                                               (RuntimeError("resend fora"), "email_failed", False)],
                         ids=["enviado", "falhou"])
def test_sem_log_subject_o_assunto_segue_gravado(monkeypatch, marca, caplog, falha, evento, ok):  # noqa: F811
    _resend(monkeypatch, falha)
    assunto = "Sua conta PigBank foi criada"

    with caplog.at_level(logging.INFO):
        assert _SEND_EMAIL_REAL(EMAIL, assunto, "<p>oi</p>") is ok

    assert _novos(marca, evento)[0]["details"]["subject"] == assunto
    assert not ok or any(r.getMessage().endswith(f": {assunto}") for r in caplog.records), caplog.text
