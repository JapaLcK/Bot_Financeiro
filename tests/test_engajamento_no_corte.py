"""
tests/test_engajamento_no_corte.py — o laço de engajamento para junto com o
resto do produto.

`core.services.engagement_scheduler._check_and_send` manda dica, insight e
reengajamento. O funil (`db.get_users_for_engagement`, `db/reports.py`) filtra
só `engagement_opt_out = false` — sem plano, sem direito —, então a conta
BLOQUEADA continuava recebendo. E a copy da dica fecha com *"Qualquer dúvida, é
só chamar no bot"*: convite a fazer exatamente o que a mensagem seguinte recusa,
que é a mesma ordem invertida que o corte do tutorial existe para não cometer.

**Era o único laço da categoria que NÃO estava dormente.** Roda 1×/dia com
`RUN_BACKGROUND_TASKS` (registrado em `finance_bot_websocket_custom.py`), sem
flag própria — diferente do lembrete de pagamento (`PAYMENT_REMINDER_ENABLED`)
e do nudge de upgrade (`FREE_UPGRADE_NUDGE_ENABLED`), os dois off por padrão.

**Por que o portão não o pegou**, e isso está registrado em
`tests/test_portao_lacos_proativos.py`: as chamadas são
`run_in_executor(None, db.get_users_for_engagement)` e
`run_in_executor(None, send_tip_email, ...)` — função por REFERÊNCIA, não
`ast.Call`. Cegueira de FORMA, não de vocabulário.

CONTROLE DECLARADO (`docs/controles_declarados.md`) — em `_check_and_send`,
apague o bloco `if users:` que chama `filtrar_por_acesso` (é a única forma aqui:
não há valor a trocar, e o bloco inteiro É o conserto). VERMELHOS (medido
2026-09-11 — são DOIS, e a instrução anterior nomeava um):
  `test_cortado_nao_recebe_email_de_engajamento`
  `test_o_lote_separa_os_dois_na_mesma_passada`
Direção: e-mail proativo para conta sem acesso, com convite a usar o bot.

Positivo do par, VERDE sob a injeção (é o que o torna positivo):
  `test_pagante_continua_recebendo_engajamento`
Sem ele, um filtro que devolvesse lista vazia passaria no negativo — e aí o
produto perderia o engajamento de toda a base pagante.

O que este arquivo NÃO alcança: o envio de verdade. `send_*_email` é
substituído, e este ambiente não tem `RESEND_API_KEY` — nenhum e-mail sai daqui
(§6). O que se mede é QUEM entra no lote depois do filtro.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import db
import core.services.email_service as email_service
import core.services.engagement_scheduler as engagement
from db.connection import get_conn


@pytest.fixture(autouse=True)
def _gate_ligado(monkeypatch):
    monkeypatch.setenv("PLANS_V2_ENABLED", "1")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "1")


def _conta(*, cortada: bool) -> int:
    """Conta ATIVA (senão o funil de engajamento nem a considera) e com as
    marcas de e-mail zeradas, para que a dica seja devida hoje."""
    user = db.register_auth_user(f"eng-{uuid.uuid4().hex[:10]}@t.com", "senha-forte-123")
    uid = int(user["user_id"])
    db.mark_plan_selected(uid)
    agora = datetime.now(timezone.utc)
    expira = agora - timedelta(days=1) if cortada else agora + timedelta(days=30)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set plan='pro', plan_expires_at=%s,"
                "       last_payment_status=%s, last_activity_at=%s,"
                "       last_tip_sent_at=null, last_insight_sent_at=null,"
                "       last_reengagement_sent_at=null"
                " where user_id=%s",
                (expira, "canceled" if cortada else "active",
                 agora - timedelta(days=1), uid),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(uid)
    return uid


def _rodar_o_tick(monkeypatch, alvos: dict[int, str]) -> list[str]:
    """Roda `_check_and_send` de verdade e devolve os RÓTULOS que receberam.

    O funil real é restringido aos uids do caso — a base de teste tem outras
    contas, e sem isso o lote traria gente que não interessa. O que NÃO é
    mockado é o filtro: é ele que está sob teste.
    """
    recebidos: list[str] = []
    por_email = {(db.get_user_email(uid) or ""): rotulo for uid, rotulo in alvos.items()}

    for nome in ("send_tip_email", "send_insight_email", "send_reengagement_email"):
        monkeypatch.setattr(
            email_service, nome,
            lambda to, *a, **k: recebidos.append(por_email.get(to, "?")) or True)

    real = db.get_users_for_engagement
    monkeypatch.setattr(
        db, "get_users_for_engagement",
        lambda: [u for u in real() if u["user_id"] in alvos])

    asyncio.run(engagement._check_and_send())
    return recebidos


def test_cortado_nao_recebe_email_de_engajamento(monkeypatch):
    """O bloqueante: conta sem acesso saía no lote e recebia a dica."""
    cortado = _conta(cortada=True)
    from core.services.plan_service import has_app_access
    assert has_app_access(cortado) is False, "pré-condição: a conta tem de estar cortada"

    assert _rodar_o_tick(monkeypatch, {cortado: "CORTADO"}) == []


def test_pagante_continua_recebendo_engajamento(monkeypatch):
    """POSITIVO: o filtro DISCRIMINA. Sem este caso, um filtro que devolvesse
    lista vazia passaria no negativo acima e o produto perderia o engajamento
    da base inteira — pior que o bug."""
    pagante = _conta(cortada=False)

    assert _rodar_o_tick(monkeypatch, {pagante: "PAGANTE"}) == ["PAGANTE"]


def test_o_lote_separa_os_dois_na_mesma_passada(monkeypatch):
    """Os dois JUNTOS, que é como o lote roda em produção.

    Sem este caso, os dois de cima passariam num filtro que decidisse pelo
    TAMANHO do lote em vez de pelo veredito de cada conta."""
    cortado = _conta(cortada=True)
    pagante = _conta(cortada=False)

    recebidos = _rodar_o_tick(monkeypatch, {cortado: "CORTADO", pagante: "PAGANTE"})

    assert recebidos == ["PAGANTE"], recebidos
