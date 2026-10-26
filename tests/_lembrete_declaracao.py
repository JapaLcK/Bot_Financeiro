"""Molde comum dos testes do lembrete de 7 dias da declaração (PR-A2c, ADR 0006).

Banco real, `send_text` REAL; só o `requests.post` é trocado (a borda HTTP, `_armar` do autopay). UM relógio:
`agora` entra em `notify_declaracoes_whatsapp_once(now=agora)`, e `declared_at`/`last_activity_at` entram por SQL
relativos a ele (o `update_last_activity` e o default de `criado_em` usam o `now()` do banco, que não segue o
relógio do teste: a classe do #550/#994).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import db
from _billing_grants_helpers import garantir_system_event_logs
from conftest import promote_to_pro
from tests._patrimonio_helpers import q
from tests.test_aviso_autopay_whatsapp import _armar, _corpos as corpos, _para as para  # noqa: F401
from tests.test_declaracao_fluxo_reconcilia import declara
from utils_date import _tz, now_tz

FONE_A = "5511999961701"
FONE_B = "5511999961702"
EVENTO = "whatsapp_declaracao_lembrete_sent"
RODAPE = "\n\n💡É só um lembrete: não mudei nada nos seus lançamentos."
LINK = "https://pigbankai.com/app?view=overview"


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


def armar(monkeypatch, status: int = 200, atraso: float = 0.0) -> list[dict]:
    """A borda HTTP trocada; `DASHBOARD_URL` ausente (o link cai no domínio de produção)."""
    monkeypatch.delenv("DASHBOARD_URL", raising=False)
    return _armar(monkeypatch, status, atraso)


def agora(hora: int = 12) -> datetime:
    """Hoje, no fuso do app, na hora pedida (nunca `now()+40s`)."""
    return now_tz().replace(hour=hora, minute=0, second=0, microsecond=0)


def agora_2031() -> datetime:
    """Distante do relógio real: se a regra, a janela ou a hora lerem o relógio real, o resultado vira."""
    return datetime(2031, 3, 10, 12, 0, tzinfo=_tz())


def dono(uid: int, fone: str | None, ag: datetime, janela: timedelta | None = timedelta(hours=1)) -> int:
    """Pro, com WhatsApp, que falou com o Piggy `janela` antes de `ag` (None = nunca falou)."""
    promote_to_pro(uid)
    if fone:
        db.bind_identity("whatsapp", fone, uid)
    q("update auth_accounts set last_activity_at=%s where user_id=%s", (ag - janela if janela else None, uid))
    return uid


def declara_ha(uid: int, ag: datetime, dias: int = 7, *, quando: datetime | None = None, valor=50,
               tipo: str = "despesa", alvo: str = "mercado") -> int:
    """Declaração de fluxo viva cuja data do gasto é `ag - dias` (ou `quando`), por SQL nos dois lados."""
    lid = declara(uid, valor, tipo, alvo=alvo, categoria="mercado" if tipo == "despesa" else "salario")
    instante = quando or ag - timedelta(days=dias)
    q("update bank_movement_declarations set declared_at=%s where launch_id=%s", (instante, lid))
    q("update launches set criado_em=%s where id=%s", (instante, lid))
    return lid


def lembretes(uid: int) -> dict[int, datetime | None]:
    rows = q("select launch_id, lembrete_em from bank_movement_declarations where user_id=%s", (uid,), fetch=True)
    return {r["launch_id"]: r["lembrete_em"] for r in rows}


def reservadas(uid: int) -> int:
    return sum(v is not None for v in lembretes(uid).values())

