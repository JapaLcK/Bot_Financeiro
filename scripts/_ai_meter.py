#!/usr/bin/env python3
"""
Medidor de chamadas OpenAI para os harnesses de QA (`scripts/`).

POR QUE UM HOOK SÓ
  `install()` embrulha `chat.completions.create` NA CLASSE, não num client.
  Pega toda chamada do processo — ai_chat/runner, intent_classifier,
  ai_router, ai_patterns, greeting, visão do media_service — sem que nenhum
  desses módulos saiba que está sendo medido, e sem depender de ordem de
  import (todos fazem `from openai import OpenAI` dentro da função).

  Da mesma linha saem três coisas:
    · qual modelo foi REALMENTE chamado (`served`), não o que o .env diz;
    · quantas chamadas um turno gastou — ZERO significa que a mensagem foi
      resolvida por comando/regex e nunca chegou na IA;
    · o custo em USD.

NÃO MEDE (tetos conhecidos)
  · `audio.transcriptions` — whisper é cobrado por minuto, não por token;
  · chamadas async (`AsyncCompletions`) — este repo só usa o client síncrono.

Sem efeito colateral no import: dá pra importar, testar e rodar o
autoteste (`python scripts/_ai_meter.py`) sem Postgres e sem chave.
"""
from __future__ import annotations

import functools
import json
import os
from datetime import date, datetime

# Preço em USD por 1 MILHÃO de tokens: (entrada nova, entrada CACHEADA, saída).
# MEDIDO EM 2026-09-01 na tabela pública da OpenAI (openai.com/api/pricing).
# Número de fora envelhece em silêncio (CLAUDE.md §2): RECONFIRA antes de
# citar o custo em qualquer lugar que não seja este relatório.
#
# A coluna do meio não é detalhe: medido em 2026-09-01 neste repo, 12.288 dos
# 12.391 tokens de entrada de uma mensagem de chat vieram cacheados (99%) —
# o system prompt + os 52 schemas de tool são prefixo estável, e a OpenAI
# cacheia prefixo sozinha acima de 1024 tokens. Ignorar isso fazia o custo
# sair ~2x acima do real.
_PRICES_USD_PER_MTOK = {
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-4o": (2.50, 1.25, 10.00),
}

# Ledger fora do worktree: acumula entre worktrees, que é o que "acumulado do
# mês" quer dizer, e nunca entra no git.
_LEDGER_PREFERIDO = "/Users/lucaskuramoti/Desktop/bot/bot_wa/.qa_ai_cost.jsonl"


def _default_ledger() -> str:
    """O caminho preferido é a raiz do checkout real — mas só se ela existir.
    Cravado, ele quebrava a rodada em qualquer outra máquina, e desde que o
    append passou a rodar ANTES do relatório isso custaria a rodada inteira,
    já paga."""
    do_ambiente = os.getenv("PIGBANK_QA_LEDGER")
    if do_ambiente:
        return do_ambiente
    if os.path.isdir(os.path.dirname(_LEDGER_PREFERIDO)):
        return _LEDGER_PREFERIDO
    return os.path.join(os.path.expanduser("~"), ".pigbank_qa_ai_cost.jsonl")


DEFAULT_LEDGER = _default_ledger()

CALLS: list[dict] = []
_installed = False


def _record(requested: str | None, served: str | None, usage) -> None:
    """Uma chamada → uma linha em CALLS. `usage` é o objeto da resposta
    (ou None: modelo que não devolve usage ainda conta como chamada)."""
    cached = int(getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0)
    CALLS.append({
        "requested": requested or "?",
        "served": served or requested or "?",
        "in": int(getattr(usage, "prompt_tokens", 0) or 0),
        "cached": cached,
        "out": int(getattr(usage, "completion_tokens", 0) or 0),
        "erro": None,
    })


def install() -> None:
    """Idempotente. Levanta se o alvo do patch sumiu — silenciosamente medir
    zero seria pior que quebrar (o relatório sairia com custo 0,00 e todo
    turno marcado como 'comando')."""
    global _installed
    if _installed:
        return
    from openai.resources.chat.completions import Completions

    original = Completions.create
    if not callable(original):
        raise RuntimeError("_ai_meter: Completions.create não é chamável — openai mudou de forma")

    @functools.wraps(original)
    def create(self, *args, **kwargs):
        try:
            resp = original(self, *args, **kwargs)
        except Exception as e:
            # A tentativa que FALHOU também é uma chamada. Sem esta linha, um
            # modelo inválido, credencial vencida ou API fora do ar deixava o
            # turno com zero chamadas — e o relatório o rotulava
            # "comando (0 chamadas)", escondendo exatamente a falha de IA que o
            # harness existe pra diagnosticar. ZERO tem que significar "não
            # chegou na OpenAI", nunca "chegou e quebrou".
            CALLS.append({"requested": kwargs.get("model") or "?", "served": "(falhou)",
                          "in": 0, "cached": 0, "out": 0, "erro": e.__class__.__name__})
            raise
        _record(kwargs.get("model"), getattr(resp, "model", None), getattr(resp, "usage", None))
        return resp

    Completions.create = create
    _installed = True


def snapshot() -> int:
    """Marca o ponto atual de CALLS. Guarde antes do turno, passe pra
    `since()` depois — é assim que um turno vira 'comando' ou 'IA'."""
    return len(CALLS)


def since(mark: int) -> list[dict]:
    return CALLS[mark:]


def path_label(calls: list[dict]) -> str:
    """Como o turno foi resolvido. É a coluna comando × IA do relatório."""
    if not calls:
        return "comando (0 chamadas)"
    falhas = [c for c in calls if c.get("erro")]
    models = sorted({c["served"] for c in calls})
    rotulo = f"IA ({len(calls)} chamada{'s' if len(calls) > 1 else ''}: {', '.join(models)})"
    if falhas:
        rotulo += f" — {len(falhas)} FALHOU: {', '.join(sorted({c['erro'] for c in falhas}))}"
    return rotulo


def cost_usd(calls: list[dict]) -> tuple[float, list[str]]:
    """(custo, modelos sem preço na tabela). O 2º elemento NÃO é decorativo:
    modelo desconhecido some do custo, então quem imprime tem que dizer."""
    total = 0.0
    unknown = set()
    for c in calls:
        price = _PRICES_USD_PER_MTOK.get(c["requested"])
        if price is None:
            unknown.add(c["requested"])
            continue
        fresh_in, cached_in, out = price
        cached = min(c["cached"], c["in"])  # nunca mais cacheado que o total
        total += ((c["in"] - cached) / 1_000_000 * fresh_in
                  + cached / 1_000_000 * cached_in
                  + c["out"] / 1_000_000 * out)
    return total, sorted(unknown)


def check_models(models: list[str]) -> list[str]:
    """Confere em /v1/models que cada modelo existe PRA ESTA CHAVE.
    Retorna a lista de problemas — vazia significa tudo certo."""
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        return ["OPENAI_API_KEY ausente — não deu pra conferir modelo nenhum"]
    try:
        from openai import OpenAI
        available = {m.id for m in OpenAI(api_key=key).models.list()}
    except Exception as e:
        return [f"falha ao listar /v1/models: {e.__class__.__name__}: {e}"]
    return [f"modelo {m!r} NÃO existe para esta chave" for m in models if m not in available]


def append_ledger(usd: float, label: str, ledger: str = DEFAULT_LEDGER) -> bool:
    """True se gravou. NUNCA levanta: contabilidade de custo não pode derrubar
    uma rodada de QA que já foi paga em dólar e em minutos."""
    try:
        with open(ledger, "a") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                "usd": round(usd, 6), "label": label}) + "\n")
        return True
    except OSError as e:
        print(f"[_ai_meter] AVISO: não deu pra gravar o ledger {ledger}: {e}")
        return False


def month_total_usd(ledger: str = DEFAULT_LEDGER, month: str | None = None) -> float:
    """Soma o mês corrente do ledger. Só vê o que passou por aqui — não é a
    fatura da OpenAI, é o gasto dos harnesses."""
    month = month or date.today().strftime("%Y-%m")
    try:
        with open(ledger) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return 0.0
    total = 0.0
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if str(row.get("ts", "")).startswith(month):
            total += float(row.get("usd", 0) or 0)
    return total


if __name__ == "__main__":
    import tempfile

    class _D:
        cached_tokens = 0

    class _U:
        prompt_tokens, completion_tokens = 1000, 500
        prompt_tokens_details = _D()

    class _UC(_U):
        """Mesma chamada, 800 dos 1000 tokens de entrada cacheados."""
        class prompt_tokens_details:  # noqa: N801
            cached_tokens = 800

    CALLS.clear()
    assert path_label(since(snapshot())).startswith("comando"), "turno sem chamada = comando"

    mark = snapshot()
    _record("gpt-4o-mini", "gpt-4o-mini-2024-07-18", _U())
    calls = since(mark)
    assert len(calls) == 1 and calls[0]["in"] == 1000 and calls[0]["out"] == 500
    assert path_label(calls).startswith("IA (1 chamada: gpt-4o-mini-2024-07-18"), path_label(calls)

    # 1000 in × 0.15/M + 500 out × 0.60/M = 0.00015 + 0.0003
    usd, unknown = cost_usd(calls)
    assert abs(usd - 0.00045) < 1e-9, usd
    assert unknown == [], unknown

    # Modelo fora da tabela não pode virar custo 0,00 calado.
    _record("modelo-inventado", "modelo-inventado", _U())
    usd2, unknown2 = cost_usd(since(mark))
    assert abs(usd2 - usd) < 1e-9 and unknown2 == ["modelo-inventado"], (usd2, unknown2)

    # Cache TEM que baixar o custo — se der igual, a coluna do meio não faz nada.
    mark_c = snapshot()
    _record("gpt-4o-mini", "gpt-4o-mini", _UC())
    usd_c, _ = cost_usd(since(mark_c))
    # 200 novos × 0.15/M + 800 cacheados × 0.075/M + 500 saída × 0.60/M
    assert abs(usd_c - (0.00003 + 0.00006 + 0.0003)) < 1e-9, usd_c
    assert usd_c < 0.00045, "custo com cache tem que ser MENOR que sem cache"

    # Usage ausente conta como chamada (senão o turno viraria "comando").
    _record("gpt-4o", "gpt-4o", None)
    assert since(mark)[-1]["in"] == 0 and since(mark)[-1]["cached"] == 0

    # Chamada que FALHOU continua sendo chamada — se virar zero, o relatório
    # rotula o turno como "comando" e esconde a falha de IA.
    mark_f = snapshot()
    CALLS.append({"requested": "gpt-4o-mini", "served": "(falhou)",
                  "in": 0, "cached": 0, "out": 0, "erro": "APITimeoutError"})
    rot = path_label(since(mark_f))
    assert not rot.startswith("comando"), rot
    assert "FALHOU" in rot and "APITimeoutError" in rot, rot

    # Ledger num caminho impossível AVISA e devolve False — nunca levanta, pra
    # não derrubar uma rodada de QA já paga.
    assert append_ledger(1.0, "x", "/nao/existe/mesmo/ledger.jsonl") is False

    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as tf:
        led = tf.name
    assert month_total_usd(led + ".naoexiste") == 0.0, "ledger ausente = 0, não estoura"
    assert append_ledger(1.25, "t1", led) is True
    append_ledger(0.75, "t2", led)
    assert abs(month_total_usd(led) - 2.0) < 1e-9, month_total_usd(led)
    assert month_total_usd(led, month="1999-01") == 0.0, "mês diferente não soma"
    os.unlink(led)

    # O patch de verdade: se o alvo mudar de forma, install() TEM que quebrar.
    install()
    from openai.resources.chat.completions import Completions
    assert hasattr(Completions.create, "__wrapped__"), "install() não embrulhou nada"
    install()  # idempotente

    print("_ai_meter: autoteste OK")
