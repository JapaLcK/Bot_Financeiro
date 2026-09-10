"""
frontend/routes/billing_pix.py — as três rotas do Pix anual. FINAS de propósito.

Validação de entrada, autenticação e tradução de exceção em status HTTP. **Nada
de saga**: quem decide dinheiro é `core/services/pix_checkout.py`, quem decide
acesso é `core/services/pix_drain.py`, e este arquivo não sabe o que é uma
cobrança `creating`.

Plano: docs/plano_pix_anual_asaas.md §8.1, §9, §13.6 e §14 item 11.

## O webhook, e por que ele responde 200 tão cedo

`POST /billing/asaas/webhook` grava na outbox e responde. Ele **não** concede
acesso, não chama GA4/CAPI, não manda e-mail e não fala com o Stripe: tudo isso
é o dreno, disparado por `background_tasks` e recuperado pelo laço de 60 s. Um
handler que fizesse o trabalho dentro da requisição colocaria a fila do Asaas —
que pausa após 15 falhas — na dependência do Stripe estar de pé.

`/billing/asaas/webhook` é o ÚNICO path deste arquivo em `CSRF_EXEMPT_PATHS`: o
checkout e o poll são chamadas do navegador logado, com cookie e token de CSRF,
e isenção que não é necessária é privilégio esquecido.

## O poll NÃO devolve o QR

`GET /billing/pix/{public_token}` responde status e valores. O `_COLUNAS` de
`db/pix_charges.py` nem traz o `qr_payload_enc`, e está certo: o QR sai do
servidor **uma vez**, na resposta do checkout. O identificador na URL é o
`public_token` opaco — `asaas_payment_id` ali dá 404, porque a busca filtra por
dono e por token (§13.6, caso 42b).
"""

# Sem `from __future__ import annotations`: o FastAPI resolve o modelo do corpo
# pela anotação, e com ela adiada o `PixCheckoutBody` chega como `ForwardRef` —
# `app.openapi()` estoura em `PydanticUserError`, o que derruba o portão de rota
# antes de qualquer requisição. Nenhum outro router de `frontend/routes/` a usa.
import asyncio
import os
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from core.observability import log_system_event_sync
from core.secure_compare import constant_time_eq
from core.services.pix_checkout import (
    CheckoutIndisponivel,
    StripeAtivo,
    Vitalicio,
    criar_checkout,
)
from core.services.pix_pricing import CoberturaJaPaga
from frontend.routes import shared

router = APIRouter()

# Planos vendáveis no Pix, no valor LEGADO da coluna (`pro` = Plus, `pro_max` =
# Pro). Mesma lista que `_STORED_PLAN_TO_TIER` conhece; `free` não é venda.
_PLANOS = ("essencial", "pro", "pro_max")

# CPF tem 11 dígitos, CNPJ tem 14. A validação aqui é de FORMA e só: quem
# valida de verdade é o Asaas, e replicar o dígito verificador seria uma segunda
# fonte da mesma regra (§0.7).
_TAMANHOS_DOC = (11, 14)


class PixCheckoutBody(BaseModel):
    plan: str
    cpf_cnpj: str
    confirm_cancel_stripe: bool = False


@router.post("/billing/pix/checkout")
@shared.limiter.limit("20/hour")
async def billing_pix_checkout(request: Request, payload: PixCheckoutBody):
    """Emite a cobrança Pix anual e devolve o QR.

    `_billing_user_lock` é reusado do monólito (o mesmo do checkout do Stripe):
    duas requisições do mesmo usuário seriam duas cobranças precificadas contra
    o mesmo crédito, e o índice parcial do banco é a segunda barreira, não a
    primeira. Import tardio para não inverter a direção do import.
    """
    user_id = shared.resolve_dashboard_user_id(request)
    plan = (payload.plan or "").strip().lower()
    if plan not in _PLANOS:
        raise HTTPException(status_code=400, detail="plan inválido.")
    doc = "".join(c for c in (payload.cpf_cnpj or "") if c.isdigit())
    if len(doc) not in _TAMANHOS_DOC:
        raise HTTPException(status_code=400,
                            detail="Informe um CPF ou CNPJ válido.")

    from frontend.finance_bot_websocket_custom import _billing_user_lock  # noqa: PLC0415

    nome, email = await asyncio.to_thread(_titular, user_id)
    try:
        async with _billing_user_lock(user_id):
            return await asyncio.to_thread(
                criar_checkout, user_id, plan_stored=plan, cpf_cnpj=doc,
                nome=nome, email=email, rastreio=_rastreio(request),
                confirm_cancel_stripe=bool(payload.confirm_cancel_stripe))
    except CoberturaJaPaga as exc:
        # `plano` e `cobertura_ate` vêm da PRÓPRIA exceção: reconsultar o banco
        # aqui poderia devolver um estado diferente do que motivou a recusa.
        raise HTTPException(status_code=409, detail={
            "error": exc.ERRO, "plan": exc.plano,
            "covered_until": exc.cobertura_ate.isoformat()}) from exc
    except Vitalicio as exc:
        # Sem consultar nada de novo: quem decidiu foi o `criar_checkout`. A
        # mensagem é COPIADA de `finance_bot_websocket_custom.py:4193` para o
        # cliente ler o mesmo texto nas duas recusas.
        # **Isto NÃO é o §0.7, e invocá-lo aqui era errado**: o §0.7 trata
        # duplicação INEVITÁVEL (HTML que não importa Python) e manda um teste
        # comparar as fontes. Esta é evitável — e consolidar num literal só
        # resolveria 2 dos 4 sites: `:4679` já diz outra coisa ("Você tem acesso
        # vitalício de brinde — trocar de plano substituiria isso."), e unificar
        # o texto que o cliente lê é decisão de copy do dono, não deste PR.
        raise HTTPException(status_code=409, detail={
            "error": exc.ERRO,
            "message": "Você já tem acesso vitalício de brinde — assinar um plano "
                       "substituiria isso. Fala com a gente se quiser mudar.",
        }) from exc
    except StripeAtivo as exc:
        raise HTTPException(status_code=409, detail={
            "error": exc.ERRO,
            "current_period_end": exc.current_period_end.date().isoformat(),
        }) from exc
    except CheckoutIndisponivel as exc:
        log_system_event_sync("warning", "pix_checkout_indisponivel",
                              "Checkout Pix recusado.", source="pix",
                              user_id=user_id, details={"motivo": exc.codigo})
        raise HTTPException(
            status_code=503,
            detail="Não consegui emitir o Pix agora. Tenta de novo em instantes.",
        ) from exc


@router.get("/billing/pix/{public_token}")
@shared.limiter.limit("120/minute")
async def billing_pix_status(request: Request, public_token: str):
    """O POLL. Sem QR, e o teto do rate limit cabe num poll de 3 s.

    120/min é o dobro do mínimo que o PR 2 pediu (≥60/min): o teto tem de caber
    numa aba aberta com o modal e outra aba do mesmo IP — casa e escritório
    compartilham IP, e `shared.limiter` chaveia por endereço remoto, não por
    usuário. Estourar aqui apagaria o QR da tela de quem só esperou.
    """
    from db.pix_charges import buscar_por_public_token  # noqa: PLC0415

    user_id = shared.resolve_dashboard_user_id(request)
    linha = await asyncio.to_thread(buscar_por_public_token, user_id, public_token)
    if linha is None:
        raise HTTPException(status_code=404, detail="Cobrança não encontrada.")
    return {
        "status": linha["status"],
        "plan": linha["plan"],
        "amount_cents": int(linha["amount_cents"]),
        "credit_cents": int(linha["credit_cents"]),
        "expires_at": (linha["qr_expires_at"].isoformat()
                       if linha["qr_expires_at"] else None),
        "starts_at": (linha["access_starts_at"].isoformat()
                      if linha["access_starts_at"] else None),
        "expires_access_at": (linha["access_expires_at"].isoformat()
                              if linha["access_expires_at"] else None),
    }


@router.post("/billing/asaas/webhook")
async def asaas_webhook(request: Request, background_tasks: BackgroundTasks):
    """Grava o evento e responde 200. O trabalho é do dreno (§8.1).

    A escada de respostas é a do precedente do Pluggy, e cada degrau é uma
    decisão:

      * **sem token configurado → 503** e log de erro. Não é 401: um webhook que
        chega antes de a env existir é problema NOSSO, e 401 mandaria o Asaas
        pausar a fila depois de 15 tentativas por um erro de deploy;
      * **token errado → 401**, com IP truncado e sem corpo no log;
      * **corpo não-JSON ou sem `id` de evento → 400** — o Asaas não reenvia o
        que ele mesmo mandou malformado;
      * **duplicata → 200 sem trabalho**. Quem decide é o `on conflict` do
        insert, não um `select` antes (que teria a corrida entre duas entregas).

    O `event_version` é o `received_at` em epoch: o Asaas não numera eventos, e a
    ordem que importa é a de CHEGADA — o §6 exige que toda escrita em grant
    carregue versão, e esta é a única marca d'água honesta que temos aqui.
    """
    from core.services.pix_drain import drenar_evento  # noqa: PLC0415
    from db.webhook_outbox import registrar_evento  # noqa: PLC0415

    token = (os.getenv("ASAAS_WEBHOOK_TOKEN") or "").strip()
    if not token:
        log_system_event_sync(
            "error", "asaas_webhook_nao_configurado",
            "Webhook do Asaas chamado sem ASAAS_WEBHOOK_TOKEN configurado.",
            source="pix")
        raise HTTPException(status_code=503, detail="Webhook não configurado.")

    enviado = (request.headers.get("asaas-access-token") or "").strip()
    if not enviado or not constant_time_eq(enviado, token):
        log_system_event_sync(
            "warning", "asaas_webhook_nao_autorizado",
            "Webhook do Asaas recusado (credencial invalida).", source="pix",
            details={"ip_prefix": _ip_prefix(request)})
        raise HTTPException(status_code=401, detail="Não autorizado.")

    try:
        corpo = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Webhook inválido.") from exc
    if not isinstance(corpo, dict):
        raise HTTPException(status_code=400, detail="Webhook inválido.")
    event_id = str(corpo.get("id") or "").strip()
    event_type = str(corpo.get("event") or "").strip()
    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="Webhook sem id de evento.")

    novo = await asyncio.to_thread(
        registrar_evento, event_id, event_type, corpo,
        int(datetime.now(timezone.utc).timestamp()))
    if novo:
        # Função SÍNCRONA de propósito: o Starlette roda `add_task` de função
        # comum no threadpool, então o dreno (que abre conexão bloqueante e fala
        # com Stripe/GA4/Meta) não segura o event loop. `drenar_evento` nunca
        # levanta — falha vira `attempts` e o laço de 60 s retoma.
        background_tasks.add_task(drenar_evento, event_id)
    return {"ok": True}


def _titular(user_id: int) -> tuple[str, str | None]:
    """Nome e e-mail para o cadastro no Asaas. O e-mail é opcional lá; o nome
    não, e um nome vazio faria o `POST /v3/customers` recusar a venda inteira."""
    from db import get_auth_user

    conta = get_auth_user(user_id) or {}
    # `display_name`, e NÃO `name`: é a chave que `get_auth_user` devolve
    # (db_support.py:578, decifrada de `display_name_enc` em :607). Com `name` o
    # `.get` era sempre `None` e o e-mail ia no campo `name` do Asaas.
    nome = (conta.get("display_name") or conta.get("email") or f"PigBank {user_id}").strip()
    return (nome or f"PigBank {user_id}", conta.get("email"))


def _rastreio(request: Request) -> dict[str, str]:
    """Os identificadores de anúncio, lidos dos COOKIES que o navegador já manda
    neste POST — os mesmos sanitizadores do checkout do Stripe (§0.1)."""
    from core.services.ga4_mp import client_id_from_ga_cookie
    from core.services.meta_capi import sanitize_fb_cookie

    bruto = {
        "ga_client_id": client_id_from_ga_cookie(request.cookies.get("_ga")),
        "fbp": sanitize_fb_cookie(request.cookies.get("_fbp")),
        "fbc": sanitize_fb_cookie(request.cookies.get("_fbc")),
    }
    return {k: v for k, v in bruto.items() if v}


def _ip_prefix(request: Request) -> str:
    """Só os dois primeiros octetos — rastro sem identificar pessoa."""
    ip = (request.client.host if request.client else "") or ""
    return ".".join(ip.split(".")[:2]) if "." in ip else ""
