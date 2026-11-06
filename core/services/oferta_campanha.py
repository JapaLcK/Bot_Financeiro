"""Oferta de campanha (Black Friday 2026): % fixo em janela fixa de Brasília, sem env.

Puro: só relógio e constantes. Vale para Plus e Pro (planos GRAVADOS `pro` e `pro_max`),
no cartão e no Pix. Com o desconto do link de retomada vale o MAIOR, nunca a soma.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_BRT = ZoneInfo("America/Sao_Paulo")
CAMPANHA_PCT = 40
CAMPANHA_INICIO = datetime(2026, 11, 1, tzinfo=_BRT)
CAMPANHA_FIM = datetime(2026, 12, 1, tzinfo=_BRT)   # exclusivo: 30/11 23:59:59 ainda vale
CAMPANHA_PLANOS = frozenset({"pro", "pro_max"})


def _agora() -> datetime:
    return datetime.now(_BRT)   # os testes congelam ESTE ponto


def campanha_pct(agora: datetime | None = None) -> int | None:
    agora = agora or _agora()
    return CAMPANHA_PCT if CAMPANHA_INICIO <= agora < CAMPANHA_FIM else None


def pct_da_oferta(plan_stored: str, pct_link: int | None = None, agora: datetime | None = None) -> int | None:
    """% efetivo da venda: o maior entre o link e a campanha (esta só em Plus/Pro). `plan_stored`
    é o nome GRAVADO; "plus" público não casa e daria só o do link, em silêncio."""
    camp = campanha_pct(agora) if plan_stored in CAMPANHA_PLANOS else None
    return max(pct_link or 0, camp or 0) or None
