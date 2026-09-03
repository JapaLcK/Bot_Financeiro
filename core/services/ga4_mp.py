"""
core/services/ga4_mp.py — Google Analytics 4 via Measurement Protocol (server-side).

O navegador manda o que ele vê: `page_view`, `view_item_list`, `begin_checkout`,
`sign_up`, `start_trial`. Este módulo manda o que só o servidor sabe:

  - `purchase` da compra imediata, com o VALOR real cobrado;
  - `purchase` da cobrança que acontece no FIM DO TRIAL e das renovações — que
    não passam por navegador nenhum (o usuário nem está no site nessa hora).

Sem isto o GA4 mostra assinatura sem receita: dá pra ver quantos assinaram, não
quanto entrou. É o mesmo papel da Conversions API do Meta (`meta_capi.py`), e os
dois disparam lado a lado nos mesmos pontos do webhook do Stripe.

Configuração (as duas obrigatórias; faltando qualquer uma, tudo vira no-op):
  - GA4_MEASUREMENT_ID — o mesmo do gtag.js (`frontend/routes/shared.py`).
  - GA4_API_SECRET     — GA4 → Admin → Fluxos de dados → o fluxo do site →
    "Chaves secretas da API do Measurement Protocol" → Criar.

Sobre o `client_id`: é ele que liga esta compra à MESMA pessoa que navegou o
site. Nasce no cookie `_ga` do navegador, é enviado no corpo do
/billing/create-checkout, viaja no `metadata` da sessão e da assinatura do
Stripe, e volta aqui no webhook. Quando não vier (cookie bloqueado, sessão
criada antes desta versão, renovação de assinatura antiga), cai no
`fallback_client_id`: a receita é registrada do mesmo jeito, mas como um usuário
novo sem origem — melhor que perder a venda no relatório.

Teto conhecido: não mandamos `session_id`. Sem ele o GA4 conta o evento no
usuário, mas não o costura na sessão de navegação que gerou a compra. Vale
quando/se a atribuição por sessão passar a importar — precisa ler o cookie
`_ga_<ID>` no navegador, cujo formato o Google não documenta.
"""
from __future__ import annotations

import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

_ENDPOINT = "https://www.google-analytics.com/mp/collect"
_REQUEST_TIMEOUT_SECONDS = 10.0

_MEASUREMENT_ID_ENV = "GA4_MEASUREMENT_ID"
_API_SECRET_ENV = "GA4_API_SECRET"

# O ID que o gtag.js usa quando a env não está setada (ver `shared.GA4_MEASUREMENT_ID`).
# Os dois lados precisam apontar pra MESMA propriedade, senão o evento do servidor
# cai numa propriedade e o do navegador em outra.
_MEASUREMENT_ID_PADRAO = "G-0H8FHNQ3C4"

# Formato do client_id do GA: dois inteiros separados por ponto ("1234567890.1712345678").
# A validação é de fronteira: o valor chega do NAVEGADOR e vai parar no metadata
# do Stripe. Qualquer outra coisa é descartada em vez de viajar.
_CLIENT_ID_RE = re.compile(r"^\d{1,20}\.\d{1,20}$")


def measurement_id() -> str:
    return (os.getenv(_MEASUREMENT_ID_ENV, _MEASUREMENT_ID_PADRAO) or "").strip()


def mp_configured() -> bool:
    """True quando measurement id + segredo estão setados (senão tudo é no-op)."""
    return bool(measurement_id() and (os.getenv(_API_SECRET_ENV) or "").strip())


def sanitize_client_id(raw: object) -> str | None:
    """client_id vindo do navegador → o próprio valor, ou None se não for um.

    Não tenta consertar o que veio errado: entrada fora do formato é ruído (ou
    alguém testando o endpoint), e um metadata inventado suja o relatório sem
    ninguém perceber.
    """
    if not isinstance(raw, str):
        return None
    valor = raw.strip()
    return valor if _CLIENT_ID_RE.match(valor) else None


def fallback_client_id(user_id: int | str) -> str:
    """client_id sintético pra quando o do navegador não chegou.

    O prefixo garante que ele NUNCA colida com um client_id real (que é sempre
    `digitos.digitos`) — colisão fundiria duas pessoas diferentes no relatório.
    """
    return f"pb-{user_id}"


def send_event(
    *,
    name: str,
    client_id: str,
    params: dict | None = None,
    user_id: int | str | None = None,
) -> bool:
    """Envia um evento pro GA4. Retorna True se o Google aceitou (2xx).

    No-op → False quando não configurado. Falha silenciosa (log + False): o
    webhook do Stripe nunca pode cair por causa de analytics.

    O endpoint responde 204 pra praticamente tudo, inclusive payload inválido —
    2xx aqui significa "chegou", não "está certo". Pra ver o que o GA4 achou do
    evento, use o /debug/mp/collect (mesmo corpo) ou o DebugView.
    """
    mid = measurement_id()
    secret = (os.getenv(_API_SECRET_ENV) or "").strip()
    if not mid or not secret or not client_id:
        return False

    corpo: dict = {
        "client_id": client_id,
        # `engagement_time_msec` é exigido pelo GA4 pra contar o evento como
        # interação; sem ele o evento chega e some de vários relatórios.
        "events": [{"name": name, "params": {"engagement_time_msec": 1, **(params or {})}}],
    }
    if user_id is not None:
        corpo["user_id"] = str(user_id)

    try:
        resp = requests.post(
            _ENDPOINT,
            params={"measurement_id": mid, "api_secret": secret},
            json=corpo,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        if 200 <= resp.status_code < 300:
            return True
        logger.warning("[ga4_mp] %s rejeitado (%s): %s", name, resp.status_code, resp.text[:300])
        return False
    except Exception as exc:
        logger.warning("[ga4_mp] falha ao enviar %s: %s", name, exc, exc_info=True)
        return False


def send_purchase(
    *,
    transaction_id: str,
    value: float,
    currency: str,
    plan: str | None,
    client_id: str,
    user_id: int | str | None = None,
) -> bool:
    """`purchase` no formato de e-commerce do GA4 (é o que liga os relatórios de
    receita e o ROAS — `value` solto, sem `items`, não preenche todos eles).

    `transaction_id` é a chave de deduplicação do GA4: sessão do Stripe na compra
    imediata, id da fatura nas cobranças seguintes. Retry de webhook manda o mesmo
    id e não vira receita dobrada.
    """
    item = {"item_id": plan or "assinatura", "item_name": plan or "assinatura",
            "price": round(float(value), 2), "quantity": 1}
    return send_event(
        name="purchase",
        client_id=client_id,
        user_id=user_id,
        params={
            "transaction_id": transaction_id,
            "value": round(float(value), 2),
            "currency": (currency or "BRL").upper(),
            "items": [item],
        },
    )
