"""
core/services/ai_chat/streaming.py — o chat do app em streaming (`/ai/chat/stream`).

Tudo aqui só vale com um EMISSOR no `ContextVar`. Sem emissor (WhatsApp,
`/ai/chat`, todos os testes de antes) `completar` é o `create` de sempre, sem
`stream`, e nenhum evento sai: o runner roda byte a byte como antes.

Cancelamento: o `threading.Event` é ligado pelo endpoint quando o cliente
desconecta. O runner só o consulta em pontos seguros:
  - topo do `_chat_inner`, antes de reservar a vaga;
  - (a) topo de cada volta do loop;
  - (b) cada chunk do stream;
  - (c) a resposta da OpenAI chegou, antes de gravar a rodada;
  - depois das tools da rodada, antes de devolver o `terminal_msg`;
  - (d) antes da chamada dos chips (`executar`, já fora da trava do turno).
NUNCA entre as tools de uma rodada: `tool_calls` órfão no histórico faz a OpenAI
recusar todo turno futuro do usuário, inclusive no WhatsApp.
"""
from __future__ import annotations

import threading
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any, Callable

PORCO = "🐷"
EMISSOR: ContextVar[Callable[[dict], None] | None] = ContextVar("ai_chat_emissor", default=None)
CANCELA: ContextVar[threading.Event | None] = ContextVar("ai_chat_cancela", default=None)


class Interrompido(Exception):
    """O usuário parou (ou desconectou): o turno acaba sem resposta."""


def ativo() -> bool:
    return EMISSOR.get() is not None


def emitir(evento: dict) -> None:
    emissor = EMISSOR.get()
    if emissor is not None:
        emissor(evento)


def cancelado() -> bool:
    cancela = CANCELA.get()
    return cancela is not None and cancela.is_set()


# Rótulo do passo por tool; o que não está aqui lê como consulta.
_PASSOS = {
    "get_period_summary": "Resumindo o período",
    "compare_periods": "Comparando períodos",
    "get_top_categories": "Vendo suas categorias",
    "get_category_spend": "Somando a categoria",
    "get_largest_expenses": "Procurando as maiores despesas",
    "get_spending_trend": "Olhando a tendência dos gastos",
    "get_balance": "Conferindo seu saldo",
    "forecast_balance": "Projetando seu saldo",
    "forecast_month_end": "Projetando o fim do mês",
    "list_recent_launches": "Lendo seus últimos lançamentos",
    "get_open_bill": "Abrindo a fatura",
    "get_investment_summary": "Vendo seus investimentos",
    "list_pockets": "Vendo suas caixinhas",
    "list_metas": "Vendo suas metas",
    "simulate_purchase": "Simulando a compra",
    "show_card": "Montando o card",
}


def passo(nome: str, is_write: bool, previa: str | None = None) -> None:
    emitir({"tipo": "passo", "previa": previa,
            "texto": "Preparando o pedido" if is_write else _PASSOS.get(nome, "Consultando seus dados")})


def completar(client, **kw):
    """`client.chat.completions.create(**kw)`. Com emissor, em streaming: monta a
    resposta pelos deltas e a devolve no mesmo formato (`.choices[0].message`,
    com `tool_calls` em dict). `delta` enquanto for só texto; `reinicio` se uma
    tool call vier depois de texto (o texto provisório foi só preâmbulo)."""
    if not ativo():
        return client.chat.completions.create(**kw)

    stream = client.chat.completions.create(stream=True, **kw)
    texto: list[str] = []
    chamadas: dict[int, dict] = {}
    inicio = ""  # o texto do começo, até saber se ele abre com 🐷 (que o app não mostra; o histórico guarda o texto cru)
    aberto = True
    try:
        for chunk in stream:
            if cancelado():  # (b) a cada chunk
                raise Interrompido()
            if not chunk.choices:
                continue
            d = chunk.choices[0].delta
            for tc in getattr(d, "tool_calls", None) or []:
                if not chamadas and texto:
                    emitir({"tipo": "reinicio"})
                c = chamadas.setdefault(tc.index, {"id": "", "type": "function",
                                                   "function": {"name": "", "arguments": ""}})
                c["id"] = tc.id or c["id"]
                fn = tc.function
                if fn is not None:
                    c["function"]["name"] += fn.name or ""
                    c["function"]["arguments"] += fn.arguments or ""
            if d.content:
                texto.append(d.content)
                if not chamadas:
                    out = d.content
                    if aberto:
                        inicio += d.content
                        s = inicio.lstrip()
                        out = s[1:].lstrip() if s.startswith(PORCO) else inicio if s else ""
                        aberto = not out  # só espaço e/ou o 🐷 até agora: espera o próximo pedaço
                    if out:
                        emitir({"tipo": "delta", "texto": out})
    finally:
        fechar = getattr(stream, "close", None)
        if fechar:
            fechar()
    msg = SimpleNamespace(content="".join(texto) or None,
                          tool_calls=[chamadas[i] for i in sorted(chamadas)])
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def executar(user_id: int, texto: str, *, monthly_limit: int, emissor: Callable[[dict], None],
             cancela: threading.Event, cards: tuple[str, ...] = ()):
    """Roda UM turno do chat com emissor e cancelamento. Síncrona: o endpoint a
    chama em `to_thread` com `contextvars.copy_context()`. Devolve o `Resultado`
    do runner; o `fim` (usage, chips) é montado pelo endpoint. `cards` = os que o app sabe
    mostrar (`CARDS_DO_APP`). O app não mostra o 🐷 do começo da resposta: o `fim.reply` sai sem ele
    (o histórico gravado, compartilhado com o WhatsApp, fica como estava)."""
    from . import chips
    from ._context import CARDS_DO_APP
    from .runner import chat_detalhado

    t1, t2, t3 = EMISSOR.set(emissor), CANCELA.set(cancela), CARDS_DO_APP.set(tuple(cards))
    try:
        r = chat_detalhado(user_id, texto, monthly_limit=monthly_limit, platform="dashboard")
    finally:
        CARDS_DO_APP.reset(t3)
        CANCELA.reset(t2)
        EMISSOR.reset(t1)
    if r.reply and r.reply.lstrip().startswith(PORCO):
        r.reply = r.reply.lstrip()[1:].lstrip()
    if r.sugerivel and not cancela.is_set():  # (d)
        r.chips = chips.sugerir(texto, r.reply, r.cards)
    return r


__all__ = ["Interrompido", "ativo", "cancelado", "completar", "emitir", "executar", "passo"]
