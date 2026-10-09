"""Fonte STRIPE do painel de funil: assinaturas (agora) e cobranças de cartão (7d e 30d).

Só agregados: nenhum e-mail, id de cliente/assinatura/cobrança, nem texto livre do
Stripe sai daqui. `failure_code` só passa se casar `_CODIGO` (e o resto vira "outros").

Reaproveita `admin_dashboard.fetch_billing_summary` (cache de 5 min, `stale`, backoff;
zero chamada nova ao Stripe para assinaturas) e acrescenta UMA listagem de cobranças dos
últimos 30 dias, de onde saem as janelas de 7d e 30d. O `reason` do resumo NÃO é repassado.
Este módulo não loga erro do SDK: ele vira um código fechado de `funil_fontes.MENSAGENS`.
O SDK, porém, loga sozinho no logger "stripe" (INFO traz `error_message`; DEBUG, o corpo
inteiro); por isso o nível dele é fixado em WARNING logo abaixo.

Limites declarados (sem correção):
- "aprovada" = `status == "succeeded"` e NÃO `captured is False` (só autorização não é
  receita). Disputas e captura parcial (`amount_captured`) não são abatidas.
- `buscado_em` pode subestimar a idade das assinaturas em até ~5 min (cache em memória
  do resumo).
- `STRIPE_LOG=debug` faz o SDK imprimir o corpo de `charges.list` no stderr: não habilitar
  em produção.
- Threads depois do timeout do `funil_fontes` (não dá para matar thread): o `timeout=8` do
  requests é POR OPERAÇÃO de rede, então um servidor que goteja bytes pode segurar a thread
  da listagem além do prazo de 10 s. O resumo reaproveitado roda no executor PADRÃO com o
  timeout de 80 s do SDK e `max_network_retries=2` (até ~240 s); o backoff de 60 s limita
  isso a ~4-5 threads vivas ao mesmo tempo.
- `captured` ausente ou None conta como capturada (o Stripe sempre manda um bool).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from typing import Any

import requests

from core import admin_dashboard
from core.funil_fontes import EXECUTOR, Fonte, FonteErro

# O SDK loga sozinho em INFO/DEBUG com a mensagem de erro (chave, e-mail, id de cliente): só
# WARNING+ do logger "stripe" passa (o SDK não usa esses níveis). Vale a partir do import.
logging.getLogger("stripe").setLevel(logging.WARNING)

_CODIGO = re.compile(r"[a-z_]{1,40}")  # com `fullmatch` ("$" aceitaria "x\n")
_TETO_COBRANCAS = 1000
_PRAZO_LISTAGEM_S = 10  # relógio total da paginação (cada requisição ainda tem 8 s)
_DIAS = {"7d": 7, "30d": 30}


def _faltam() -> list[str]:
    # No momento da chamada: o teste (e o admin) lê o atributo do módulo, não a env.
    return [] if admin_dashboard.STRIPE_SECRET_KEY else ["STRIPE_SECRET_KEY"]


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return 0


def agregar_cobrancas(cobrancas, agora_ts: int, prazo: float | None = None) -> dict:
    """Soma as cobranças (já limitadas a 30d) em 7d e 30d. Tolerante: lista vazia = zeros.
    `truncado` = passou do teto de itens OU do `prazo` (relógio `time.monotonic()`)."""
    jan = {k: {"aprovadas": 0, "recusadas": 0, "centavos": 0, "motivos": Counter()} for k in _DIAS}
    truncado = False
    for i, ch in enumerate(cobrancas):
        if i >= _TETO_COBRANCAS or (prazo is not None and time.monotonic() > prazo):
            truncado = True
            break
        status = getattr(ch, "status", None)
        if status not in ("succeeded", "failed"):
            continue
        # ponytail: só autorização (captured False) não é receita nem aprovada.
        if status == "succeeded" and getattr(ch, "captured", True) is False:
            continue
        criada = _int(getattr(ch, "created", 0))
        # `agora_ts - dias*86400` é a mesma borda da listagem (`created >= ...`).
        for k, dias in _DIAS.items():
            if criada < agora_ts - dias * 86400:
                continue
            j = jan[k]
            if status == "succeeded":
                j["aprovadas"] += 1
                if str(getattr(ch, "currency", "")).lower() == "brl":
                    j["centavos"] += _int(getattr(ch, "amount", 0)) - _int(getattr(ch, "amount_refunded", 0))
            else:
                j["recusadas"] += 1
                code = getattr(ch, "failure_code", None)
                j["motivos"][code if isinstance(code, str) and _CODIGO.fullmatch(code) else "outros"] += 1
    out: dict[str, Any] = {}
    for k, j in jan.items():
        ordenados = sorted(((c, n) for c, n in j["motivos"].items() if c != "outros"),
                           key=lambda x: (-x[1], x[0]))
        resto = j["motivos"].get("outros", 0) + sum(n for _, n in ordenados[5:])
        motivos = [{"codigo": c, "n": n} for c, n in ordenados[:5]]
        if resto:
            motivos.append({"codigo": "outros", "n": resto})
        out[k] = {"aprovadas": j["aprovadas"], "recusadas": j["recusadas"],
                  "receita_liquida": round(j["centavos"] / 100, 2), "motivos_recusa": motivos}
    out["truncado"] = truncado
    return out


def _listar_cobrancas(chave: str, agora_ts: int) -> dict:
    """Síncrona (roda em thread). Cliente próprio: timeout curto e sem retry, para não
    herdar o `stripe.api_key` global nem os 80s do padrão do SDK."""
    import stripe

    try:
        client = stripe.StripeClient(
            chave, http_client=stripe.RequestsClient(timeout=8), max_network_retries=0)
        lista = client.v1.charges.list(
            params={"created": {"gte": agora_ts - 30 * 86400}, "limit": 100})
        return agregar_cobrancas(lista.auto_paging_iter(), agora_ts,
                                 time.monotonic() + _PRAZO_LISTAGEM_S)
    except stripe.AuthenticationError:
        raise FonteErro("auth") from None
    except stripe.PermissionError:
        raise FonteErro("permissao") from None
    except stripe.RateLimitError:
        raise FonteErro("limite") from None
    except stripe.APIConnectionError as exc:
        # O SDK embrulha tudo em APIConnectionError; só timeout de verdade vira "timeout".
        timeout = isinstance(exc.__cause__, requests.exceptions.Timeout)
        raise FonteErro("timeout" if timeout else "indisponivel") from None
    except stripe.StripeError:
        raise FonteErro("indisponivel") from None


async def _buscar() -> dict:
    resumo = await admin_dashboard.fetch_billing_summary()
    # `stale` = o Stripe falhou há pouco e só há números velhos em memória: trata como
    # falha, e o cache em tabela devolve o último payload bom como `stale`.
    # ponytail: não repassa o `stale` do resumo como dado; add se o painel precisar.
    if not resumo.get("available") or resumo.get("stale"):
        raise FonteErro("indisponivel")
    cobr = await asyncio.get_running_loop().run_in_executor(
        EXECUTOR, _listar_cobrancas, admin_dashboard.STRIPE_SECRET_KEY, int(time.time()))
    subs = resumo.get("subscriptions") or {}
    return {
        "assinaturas": {"ativas": _int(subs.get("active")), "em_trial": _int(subs.get("trialing")),
                        "em_atraso": _int(subs.get("past_due")), "canceladas": _int(subs.get("canceled")),
                        "outras": _int(subs.get("other"))},
        "mrr": float(resumo.get("mrr") or 0),
        "ticket_medio": float(resumo.get("ticket_medio") or 0),
        "mrr_trial_potencial": float(resumo.get("trial_mrr_potencial") or 0),
        "cobrancas": cobr,
    }


FONTE = Fonte(
    nome="stripe",
    ttl_s=300,
    backoff_s=60,
    janela={"rotulo": "Assinaturas agora; cobranças dos últimos 7 e 30 dias", "fuso": "UTC"},
    faltam=_faltam,
    buscar=_buscar,
)
