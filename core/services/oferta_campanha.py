"""Oferta de campanha (Black Friday 2026): % fixo em janela fixa de Brasília, sem env.

Puro: só relógio e constantes. Vale para o ANUAL de Plus e Pro (planos GRAVADOS `pro` e `pro_max`),
no cartão e no Pix, sem trial. Com o desconto do link de retomada vale o MAIOR, nunca a soma.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_BRT = ZoneInfo("America/Sao_Paulo")
CAMPANHA_PCT = 40
CAMPANHA_INICIO = datetime(2026, 11, 23, tzinfo=_BRT)
CAMPANHA_FIM = datetime(2026, 12, 1, tzinfo=_BRT)   # exclusivo: 30/11 23:59:59 ainda vale
CAMPANHA_PLANOS = frozenset({"pro", "pro_max"})


def _agora() -> datetime:
    return datetime.now(_BRT)   # os testes congelam ESTE ponto


def campanha_pct(agora: datetime | None = None) -> int | None:
    agora = agora or _agora()
    return CAMPANHA_PCT if CAMPANHA_INICIO <= agora < CAMPANHA_FIM else None


def campanha_estado(agora: datetime | None = None) -> str:
    """"antes" | "oferta" | "depois": a página /black-friday renderiza por ESTE relógio, nunca o do aparelho."""
    agora = agora or _agora()
    return "antes" if agora < CAMPANHA_INICIO else "oferta" if agora < CAMPANHA_FIM else "depois"


def campanha_vale(plan_stored: str, interval: str, agora: datetime | None = None) -> bool:
    """A campanha se aplica a esta venda: só ANUAL, só Plus/Pro, só na janela. É a mesma pergunta do
    desconto e do trial (cobrança na hora, sem os 7 dias grátis)."""
    return interval == "annual" and plan_stored in CAMPANHA_PLANOS and campanha_estado(agora) == "oferta"


def pct_da_oferta(plan_stored: str, pct_link: int | None = None, agora: datetime | None = None, *,
                  interval: str, elegivel: bool = True) -> int | None:
    """% efetivo da venda: o maior entre o link e a campanha (esta só no anual de Plus/Pro, e só com
    `elegivel`: quem já assina fica de fora). `plan_stored` é o nome GRAVADO; "plus" público não casa
    e daria só o do link, em silêncio. `interval` é obrigatório: nenhum chamador cai no padrão sem perceber."""
    camp = CAMPANHA_PCT if elegivel and campanha_vale(plan_stored, interval, agora) else None
    return max(pct_link or 0, camp or 0) or None
