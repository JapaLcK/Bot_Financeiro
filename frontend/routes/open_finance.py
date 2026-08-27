"""Rotas de Open Finance (Pluggy + mock) — conexão, snapshot e webhook.

Etapa 4 do refactor Fase 1 (docs/refactor_plan.md): movidas de
finance_bot_websocket_custom.py sem mudança de comportamento.

O webhook /open-finance/pluggy/webhook está em CSRF_EXEMPT_PATHS no app —
o middleware CSRF compara o path da request, então a isenção segue valendo
com a rota registrada via router.
"""

import asyncio
import hashlib
import hmac
import json
import os
import random
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.admin_dashboard import log_system_event
from core.audit import AuditEvent, record_audit_event
from core.services.pluggy import (
    PluggyApiError,
    PluggyConfigError,
    create_pluggy_api_key,
    create_pluggy_connect_token,
    delete_pluggy_item,
    get_pluggy_item,
    list_pluggy_connectors,
)
from core.services.plan_service import is_pro
from core.services.pluggy_sync import (
    refresh_and_sync_pluggy_user,
    sync_pluggy_item,
    sync_pluggy_user,
)
from db import (
    count_open_finance_connections,
    create_mock_open_finance_connection,
    delete_open_finance_transactions,
    disconnect_open_finance_connection,
    get_connections_by_item_id,
    get_open_finance_connection_by_item_id,
    get_open_finance_snapshot,
    list_pluggy_item_ids,
    pluggy_item_lock,
    register_item,
    save_pluggy_open_finance_item,
    token_hash,
    update_pluggy_open_finance_item_status,
)
from frontend.routes import shared

router = APIRouter()

# Default DESLIGADO: o "Pluggy Bank" (dados sintéticos) não deve aparecer no
# catálogo em produção. Só ligar (=1) em ambiente de teste/sandbox.
PLUGGY_INCLUDE_SANDBOX = os.getenv("PLUGGY_INCLUDE_SANDBOX", "0") == "1"

# Eventos da Pluggy que disparam um sync (puxar contas/transações).
# transactions/deleted é tratado à parte (remove ids), não re-sincroniza.
PLUGGY_SYNC_EVENTS = {
    "item/created",
    "item/updated",
    "transactions/created",
    "transactions/updated",
}


# Um sync em voo por item + UM bit de "chegou evento enquanto rodava". A Pluggy
# manda `item/updated` e `transactions/created` com 0–17s de intervalo, e cada
# evento virava uma task: 10 `deadlock detected` medidos em produção. O dirty é
# BOOLEANO por item de propósito — fila de eventos aqui só adiaria o mesmo sync
# N vezes, e o sync é idempotente: uma re-execução cobre qualquer número de
# eventos que chegaram durante a anterior.
_INFLIGHT: dict[str, asyncio.Task] = {}
_DIRTY: set[str] = set()

# Só erro TRANSITÓRIO é retentado. Erro de programação ou de validação repetido
# 3x é o mesmo erro 3x — e ainda esconde o defeito no log.
_SYNC_MAX_ATTEMPTS = 3

# HTTP da Pluggy que some sozinho: cota estourada e erro do lado dela. 404 fica
# de fora de propósito (é `item_missing`, tratado no sync), 4xx de credencial
# também — repetir não conserta chave errada.
_HTTP_TRANSITORIO = {429, 500, 502, 503, 504}


def _backoff_sec(tentativa: int) -> float:
    """Espera exponencial com jitter. Dois chamadores: a exceção `_retryable` e o
    `sync_in_progress`, que NÃO é exceção — duas cópias da fórmula seriam a
    mesma regra em dois lugares (§0.7)."""
    return 0.5 * (2 ** (tentativa - 1)) * (0.75 + random.random() / 2)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, PluggyApiError) and exc.status_code in _HTTP_TRANSITORIO:
        return True
    try:
        from psycopg import errors as pg_errors
        return isinstance(exc, (pg_errors.DeadlockDetected, pg_errors.SerializationFailure))
    except Exception:  # psycopg ausente/alterado: não retenta
        return False


def _salva_item_sob_lock(user_id: int, remote: dict, item_id: str) -> tuple[dict, bool]:
    """Grava a reconexão DENTRO do `pluggy_item_lock` do item.

    A relectura da geração em `_sync_pluggy_item_confirmado` não é atômica com as
    escritas que vêm depois dela. Uma reconexão que caísse nessa fresta deixava o
    run de geração velha gravar o espelho E rodar `import_open_finance_launches` /
    `import_open_finance_credit` — que criam LANÇAMENTO e COMPRA DE CARTÃO do
    usuário. O carimbo era recusado, mas nenhum sync posterior remove lançamento:
    o upsert só acrescenta. Resultado medido pelo Codex (#162, P1): transação
    fantasma de uma autorização que não vale mais, sobrevivendo à recuperação —
    tipicamente a conta que o usuário DESMARCOU ao reconectar.

    Pegar o mesmo lock aqui fecha a fresta na origem: enquanto um sync escreve, a
    reconexão espera; enquanto a reconexão grava, nenhum sync entra na fase de
    escrita — e o próximo a entrar relê a geração nova e aborta.

    A janela do lock é curta de propósito (só escrita, nunca a leitura remota),
    então esperar a vez é barato. Estourou o teto (`OF_SYNC_LOCK_WAIT_MS`, 15s):
    grava assim mesmo e devolve `False`. Recusar seria pior — o item já existe na
    Pluggy, e não gravar deixaria o banco conectado lá e invisível aqui.
    """
    with pluggy_item_lock(item_id) as locked:
        return save_pluggy_open_finance_item(user_id, remote), bool(locked)


async def _run_pluggy_sync_bg(item_id: str) -> None:
    """Roda o sync fora do request (fire-and-forget), logando o resultado REAL.

    Antes isto logava `pluggy_sync_done` em nível info mesmo com `ok:false` —
    397 sucessos e 41 falhas na mesma prateleira, e ninguém procurando por elas.
    """
    result: dict | None = None
    try:
        for tentativa in range(1, _SYNC_MAX_ATTEMPTS + 1):
            try:
                result = await asyncio.to_thread(sync_pluggy_item, item_id)
                # `sync_in_progress` não é exceção: volta como dict, então o
                # `break` abaixo encerrava a tarefa em silêncio e NINGUÉM mais
                # sincronizava. Cenário medido (Codex #162): o run de geração
                # velha segura o `pluggy_item_lock` enquanto escreve, o sync que
                # a reconexão agendou bate no lock e desiste — e com
                # `OF_REFRESH_ENABLED` off (o default) nada mais roda sozinho, o
                # espelho velho fica indefinidamente e a tela fica em âmbar até
                # o usuário tocar "Atualizar".
                #
                # SÓ `sync_in_progress`. `stale_authorization` NÃO se retenta: ele
                # significa "alguém mais novo assumiu", e quem assumiu já agendou
                # o próprio sync — retentar seria correr atrás de um trabalho que
                # já tem dono, e num par de runs que se atropelam viraria laço.
                if (result or {}).get("reason") != "sync_in_progress":
                    break
                if tentativa == _SYNC_MAX_ATTEMPTS:
                    break
                espera = _backoff_sec(tentativa)
                await log_system_event(
                    "warning", "pluggy_sync_retry",
                    f"Sync Pluggy vai repetir ({tentativa}/{_SYNC_MAX_ATTEMPTS}): {item_id}",
                    source="open_finance",
                    details={"item_id": item_id, "attempt": tentativa,
                             "error": "sync_in_progress", "sleep_sec": round(espera, 3)},
                )
                await asyncio.sleep(espera)
            except Exception as exc:
                if not _retryable(exc) or tentativa == _SYNC_MAX_ATTEMPTS:
                    raise
                espera = _backoff_sec(tentativa)
                await log_system_event(
                    "warning", "pluggy_sync_retry",
                    f"Sync Pluggy vai repetir ({tentativa}/{_SYNC_MAX_ATTEMPTS}): {item_id}",
                    source="open_finance",
                    details={"item_id": item_id, "attempt": tentativa,
                             "error": type(exc).__name__, "sleep_sec": round(espera, 3)},
                )
                await asyncio.sleep(espera)

        result = result if isinstance(result, dict) else {}
        # Atualização ao vivo (PWA): avisa o cliente conectado pra recarregar saldo/timeline.
        uid = result.get("user_id")
        if uid:
            try:
                from frontend.finance_bot_websocket_custom import manager
                await manager.broadcast_to_user(
                    int(uid), json.dumps({"type": "open_finance_synced", "item_id": item_id})
                )
            except Exception:
                pass

        ok = bool(result.get("ok"))
        reason = str(result.get("reason") or "")
        # Sync que deu certo mas deixou produto pra trás não pode ficar mudo: é
        # daqui que sai "atualizei o que deu" na tela e a contagem do painel.
        if ok and result.get("stale_products"):
            await log_system_event(
                "warning", "of_product_stale",
                f"Sync concluído com produto atrasado: {item_id}",
                source="open_finance",
                details={"item_id": item_id, "stale_products": result["stale_products"]},
            )
        if ok:
            nivel, evento = "info", "pluggy_sync_done"
        elif reason in ("item_missing", "owner_conflict"):
            nivel, evento = "error", "of_item_missing"
        else:
            nivel, evento = "warning", "pluggy_sync_done"
        await log_system_event(
            nivel,
            evento,
            f"Sync Pluggy {'concluído' if ok else 'sem sucesso'}: {item_id}"
            + (f" ({reason})" if reason else ""),
            source="open_finance",
            details={**result, "reason": reason or None},
        )
    except Exception as exc:  # noqa: BLE001 — background, não pode derrubar nada
        await log_system_event(
            "error",
            "pluggy_sync_failed",
            f"Sync Pluggy falhou: {item_id}: {exc}",
            source="open_finance",
            details={"item_id": item_id, "error": str(exc)[:200]},
        )


def _on_sync_done(item_id: str) -> None:
    """Fim de um sync: solta o slot e, se chegou evento no meio, roda UMA vez mais."""
    _INFLIGHT.pop(item_id, None)
    if item_id in _DIRTY:
        _DIRTY.discard(item_id)
        _schedule_pluggy_sync(item_id)


def _schedule_pluggy_sync(item_id: str) -> None:
    if not item_id:
        return
    if item_id in _INFLIGHT:
        _DIRTY.add(item_id)   # coalesce: uma re-execução no fim, não uma task por evento
        return
    task = asyncio.create_task(_run_pluggy_sync_bg(item_id), name=f"pluggy_sync_{item_id}")
    _INFLIGHT[item_id] = task
    task.add_done_callback(lambda _t: _on_sync_done(item_id))


def _bank_limit_enabled() -> bool:
    # Gate dormante: só bloqueia quando ligado no ambiente (como os outros gates do projeto).
    return (os.getenv("OF_BANK_LIMIT_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


async def _enforce_bank_limit(user_id: int, new_item_id: str | None = None) -> None:
    """Teto de conexões OF por plano.

    v2 (PLANS_V2_ENABLED): teto vem do tier — of_banks_max da escada
    (trial 1 / Essencial 1 / Plus 2 / Pro 5 / None = ilimitado). Ativo sempre
    que a escada estiver ligada, sem env extra.
    v1 (flag off): gate Fase 7 legado, dormante atrás de OF_BANK_LIMIT_ENABLED.

    P1: reconectar/renovar um banco JÁ conectado (mesmo provider_item_id) NÃO conta como
    banco novo — senão o usuário no limite ficava travado de reautorizar o próprio
    banco. Só bloqueia banco realmente novo.
    """
    from core.services.plan_service import plans_v2_enabled, get_user_limits

    if plans_v2_enabled():
        limit = (await asyncio.to_thread(get_user_limits, user_id)).get("of_banks_max")
        if limit is None:
            return  # ilimitado (Premium futuro)
        if new_item_id:
            existing = await asyncio.to_thread(get_open_finance_connection_by_item_id, str(new_item_id))
            if existing and int(existing.get("user_id")) == int(user_id):
                return  # upsert de item existente: reconexão, não é banco novo
        if limit <= 0:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "OF_BANK_LIMIT",
                    "limit": 0,
                    "message": "Conectar banco faz parte dos planos pagos — no Grátis a conexão "
                               "vale durante os 30 dias de teste. Assine pra reativar: /precos",
                },
            )
        count = await asyncio.to_thread(count_open_finance_connections, user_id)
        if count >= limit:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "OF_BANK_LIMIT",
                    "limit": limit,
                    "message": f"Seu plano conecta até {limit} banco{'s' if limit > 1 else ''}. "
                               "Faça upgrade pra conectar mais: /precos",
                },
            )
        return

    if not _bank_limit_enabled():
        return
    if await asyncio.to_thread(is_pro, user_id):
        return
    if new_item_id:
        existing = await asyncio.to_thread(get_open_finance_connection_by_item_id, str(new_item_id))
        if existing and int(existing.get("user_id")) == int(user_id):
            return  # upsert de item existente: reconexão, não é banco novo
    limit = int(os.getenv("OF_FREE_BANK_LIMIT", "1"))
    count = await asyncio.to_thread(count_open_finance_connections, user_id)
    if count >= limit:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "OF_BANK_LIMIT",
                "limit": limit,
                "message": f"No plano grátis você conecta {limit} banco. Assine o Pro para conectar mais.",
            },
        )


async def _ensure_of_access_allowed(user_id: int) -> None:
    """Barra a EMISSÃO do connect-token quando o plano não dá Open Finance nenhum
    (of_banks_max <= 0: Free pós-trial). Diferente do teto por contagem, esse caso é
    inequívoco — o usuário não tem banco pra adicionar nem reconexão liberada (banco
    do Free fica pausado; reativar = upgrade). Fecha o abuso direto do endpoint e evita
    item/consentimento órfão na Pluggy (cada conexão custa). Planos com teto > 0 seguem
    liberados aqui pra não travar reconexão de um banco existente — a contagem é cobrada
    no /pluggy-item, onde já se sabe se é banco novo ou upsert.
    """
    from core.services.plan_service import plans_v2_enabled, get_user_limits

    if plans_v2_enabled():
        limit = (await asyncio.to_thread(get_user_limits, user_id)).get("of_banks_max")
        if limit is not None and limit <= 0:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "OF_BANK_LIMIT",
                    "limit": 0,
                    "message": "Conectar banco faz parte dos planos pagos — no Grátis a conexão "
                               "vale durante os 30 dias de teste. Assine pra reativar: /precos",
                },
            )
        return

    # v1 legado: só barra quando o gate está ligado E o usuário não é Pro.
    if not _bank_limit_enabled():
        return
    if await asyncio.to_thread(is_pro, user_id):
        return
    limit = int(os.getenv("OF_FREE_BANK_LIMIT", "1"))
    if limit <= 0:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "OF_BANK_LIMIT",
                "limit": 0,
                "message": "Conectar banco faz parte dos planos pagos. Assine o Pro para conectar.",
            },
        )


class OpenFinanceMockConnectPayload(BaseModel):
    institution: str | None = None


class OpenFinancePluggyItemPayload(BaseModel):
    item: dict


@router.get("/open-finance/{user_id}")
async def open_finance_snapshot_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, **snapshot}))


# Só contas de pessoa física no modal (bate com o preview aprovado; evita os
# duplicados "… Empresas"). Pra incluir PJ, adicionar "BUSINESS_BANK".
_CONNECTABLE_TYPES = {"PERSONAL_BANK"}


@router.get("/open-finance/{user_id}/connectors")
async def open_finance_connectors_route(request: Request, user_id: int):
    """Catálogo completo de bancos da Pluggy pro modal "Conectar banco".

    Fluxo padrão: a escolha do banco acontece no site (modal com busca) e o widget da
    Pluggy abre já no banco escolhido. Retorna dicts enxutos (id/name/type/color/inv)."""
    shared.authorize_dashboard_access(request, user_id)
    try:
        raw = await asyncio.to_thread(
            list_pluggy_connectors, None, include_sandbox=PLUGGY_INCLUDE_SANDBOX
        )
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    banks = []
    for c in raw:
        if str(c.get("type") or "") not in _CONNECTABLE_TYPES:
            continue
        products = [str(p).upper() for p in (c.get("products") or [])]
        banks.append({
            "id": c.get("id"),
            "name": (c.get("name") or "").strip(),
            "type": c.get("type"),
            "color": (c.get("primaryColor") or "").lstrip("#"),
            "logo": c.get("imageUrl") or "",
            "inv": "INVESTMENTS" in products,
        })
    banks.sort(key=lambda b: b["name"].lower())
    return {"ok": True, "connectors": banks}


def _require_caixinha_access(user_id: int) -> None:
    # Caixinha (Open Finance) é feature paga — desacoplada do beta de agentes:
    # qualquer plano pago (Essencial+) tem acesso à UI de vínculo, não só o beta.
    from core.services.plan_service import require_min_tier
    if not require_min_tier(user_id, "essencial"):
        raise HTTPException(status_code=404, detail="Feature indisponível.")


@router.get("/open-finance/{user_id}/caixinhas")
async def open_finance_caixinhas_route(request: Request, user_id: int):
    """Banqueiro: caixinhas OF detectadas + metas do usuário, pra montar o vínculo."""
    shared.authorize_dashboard_access(request, user_id)
    await asyncio.to_thread(_require_caixinha_access, user_id)
    from db import list_caixinha_candidates, list_pockets

    candidates = await asyncio.to_thread(list_caixinha_candidates, user_id)
    pockets = await asyncio.to_thread(lambda: list_pockets(user_id, accrue=False))
    metas = [
        {"id": p["id"], "name": p["name"],
         "target_amount": float(p["target_amount"]) if p.get("target_amount") is not None else None}
        for p in pockets
    ]
    caixinhas = [
        {"of_investment_id": c["of_investment_id"], "name": c["name"],
         "balance": float(c["balance"] or 0),
         "pocket_id": c["pocket_id"], "pocket_name": c["pocket_name"]}
        for c in candidates
    ]
    return {"ok": True, "caixinhas": caixinhas, "metas": metas}


class CaixinhaBindBody(BaseModel):
    pocket_id: int
    of_investment_id: int | None = None


@router.post("/open-finance/{user_id}/caixinhas/bind")
async def open_finance_caixinha_bind_route(request: Request, user_id: int, body: CaixinhaBindBody):
    """Vincula (ou desvincula com of_investment_id=null) uma meta a uma caixinha OF."""
    shared.authorize_dashboard_access(request, user_id)
    await asyncio.to_thread(_require_caixinha_access, user_id)
    from db import bind_pocket_to_caixinha

    ok = await asyncio.to_thread(
        bind_pocket_to_caixinha, user_id, body.pocket_id, body.of_investment_id
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Não foi possível vincular (meta ou caixinha inválida).")
    return {"ok": True}


@router.post("/open-finance/{user_id}/connect-token")
async def open_finance_connect_token_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)
    # Barra só o caso inequívoco (plano sem OF): não emite token pra quem não pode
    # conectar nada, fechando o abuso direto do endpoint e evitando item órfão na Pluggy.
    # O TETO POR CONTAGEM (planos pagos no limite) NÃO é cobrado aqui de propósito — o
    # widget também reconecta um banco existente, e a contagem é validada no /pluggy-item,
    # onde já se sabe se o item é novo ou um upsert de um banco já conectado.
    await _ensure_of_access_allowed(user_id)

    webhook_url = (os.getenv("PLUGGY_WEBHOOK_URL") or "").strip()
    if not webhook_url and shared.DASHBOARD_URL.startswith("https://"):
        webhook_url = f"{shared.DASHBOARD_URL}/open-finance/pluggy/webhook"

    # Anexa o secret como token na URL (a Pluggy chama de volta preservando a query).
    # É como o webhook se autentica (a Pluggy não assina o corpo). Não duplica se já
    # veio com token (ex.: PLUGGY_WEBHOOK_URL setado à mão com o token).
    webhook_secret = (os.getenv("PLUGGY_WEBHOOK_SECRET") or "").strip()
    if webhook_url and webhook_secret and "token=" not in webhook_url:
        sep = "&" if "?" in webhook_url else "?"
        webhook_url = f"{webhook_url}{sep}token={webhook_secret}"

    try:
        token_data = await asyncio.to_thread(
            create_pluggy_connect_token,
            user_id,
            webhook_url or None,
        )
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Registra que ESTE usuário pediu um token. O `GET /items` da Pluggy devolve
    # 401, então sem este rastro um item criado e nunca reportado ao /pluggy-item
    # é invisível para nós. Guarda o HASH: o token bruto abre a conexão e NUNCA
    # pode ir para o banco.
    try:
        await asyncio.to_thread(
            register_item, user_id,
            token_hash=token_hash(token_data["accessToken"]), origin="connect_token",
        )
    except Exception as exc:  # noqa: BLE001 — rastro nunca derruba a emissão do token
        await log_system_event(
            "warning", "of_item_registry_failed", "Falha ao registrar connect token",
            source="open_finance", details={"error": str(exc)[:200]},
        )

    return {
        "ok": True,
        "accessToken": token_data["accessToken"],
        "includeSandbox": PLUGGY_INCLUDE_SANDBOX,
        "provider": "pluggy",
    }


@router.post("/open-finance/{user_id}/pluggy-item")
async def open_finance_pluggy_item_route(request: Request, user_id: int, payload: OpenFinancePluggyItemPayload):
    """Registra o item que o widget da Pluggy acabou de criar.

    O dict `item` vem DO NAVEGADOR: nome do banco, status e id são todos escolhidos
    pelo cliente. A única coisa aproveitada dele é o `id`, e só para perguntar à
    Pluggy quem é o dono — o que grava é a resposta REMOTA. Duas checagens:

      • `clientUserId` do item (que nós mesmos setamos ao emitir o connect token)
        precisa bater com o usuário logado. É ESTA que discrimina: sem ela, o id
        de um item de outra pessoa vira uma conexão nesta conta;
      • se o item já pertence a outra conta aqui dentro, 409 e nada é gravado.

    Comparar `session_uid` com o `{user_id}` da URL seria redundante e não é o
    conserto: `shared.authorize_dashboard_access` já levanta 403 quando os dois
    diferem (frontend/routes/shared.py), então aqui eles são iguais por
    construção. `session_uid` é usado abaixo por clareza de origem, não por
    segurança adicional.
    """
    session_uid = shared.authorize_dashboard_access(request, user_id)
    item = payload.item if isinstance(payload.item, dict) else {}
    new_item_id = str(item.get("id") or item.get("itemId") or "").strip()
    if not new_item_id:
        raise HTTPException(status_code=400, detail="Item Pluggy sem id.")

    try:
        remote = await asyncio.to_thread(get_pluggy_item, new_item_id)
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        if getattr(exc, "status_code", None) == 404:
            raise HTTPException(status_code=404, detail="Item não existe na Pluggy.") from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    dono_remoto = remote.get("clientUserId")
    if str(dono_remoto or "") != str(session_uid):
        await log_system_event(
            "error", "of_item_owner_conflict",
            "Item Pluggy não pertence ao usuário da sessão",
            source="open_finance",
            details={"item_id": new_item_id, "origin": "pluggy_item_route"},
        )
        raise HTTPException(status_code=403, detail="Este item não pertence a esta conta.")

    outros = [c for c in await asyncio.to_thread(get_connections_by_item_id, new_item_id)
              if int(c["user_id"]) != int(session_uid)]
    if outros:
        await log_system_event(
            "error", "of_item_owner_conflict",
            "Item Pluggy já vinculado a outra conta",
            source="open_finance",
            details={"item_id": new_item_id, "connections": len(outros)},
        )
        raise HTTPException(status_code=409, detail="Este item já está vinculado a outra conta.")

    await _enforce_bank_limit(session_uid, new_item_id)
    try:
        connection, sob_lock = await asyncio.to_thread(
            _salva_item_sob_lock, session_uid, remote, new_item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not sob_lock:
        await log_system_event(
            "warning", "of_reconnect_sem_lock",
            f"Reconexão gravada sem o lock de escrita: {new_item_id}",
            source="open_finance", details={"item_id": new_item_id},
        )

    await asyncio.to_thread(
        register_item, session_uid,
        provider_item_id=new_item_id, origin="pluggy_item",
        status=str(remote.get("status") or "") or None,
    )

    await asyncio.to_thread(
        record_audit_event,
        user_id,
        AuditEvent.OPEN_FINANCE_CONNECTED,
        request=request,
        details={"provider": "pluggy", "item_id": (connection or {}).get("provider_item_id")},
    )

    # Sync inicial: puxa contas + transações do banco recém-conectado.
    _schedule_pluggy_sync(str((connection or {}).get("provider_item_id") or ""))

    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, "connection": connection, **snapshot}))


@router.post("/open-finance/{user_id}/sync")
async def open_finance_sync_route(request: Request, user_id: int):
    """Força um sync de todos os bancos Pluggy do usuário (leitura sob demanda)."""
    shared.authorize_dashboard_access(request, user_id)
    try:
        result = await asyncio.to_thread(sync_pluggy_user, user_id)
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, "sync": result, **snapshot}))


@router.post("/open-finance/{user_id}/refresh")
async def open_finance_refresh_route(request: Request, user_id: int, wait: int | None = None):
    """Refresh manual (botão "Atualizar" e pull-to-refresh do app): pede pra Pluggy
    re-buscar do banco (PATCH /items), espera concluir e sincroniza. Difere do
    /sync, que só relê o que a Pluggy já tem sem forçar nova busca no banco.

    `?wait=` (teto 18s) existe porque o pull-to-refresh do app tem watchdog de 12s:
    com a espera padrão o gesto virava âmbar mesmo quando tudo dava certo.
    """
    shared.authorize_dashboard_access(request, user_id)
    espera = max(0, min(int(wait), 18)) if wait is not None else None
    t0 = time.monotonic()
    try:
        result = await asyncio.to_thread(refresh_and_sync_pluggy_user, user_id, wait_seconds=espera)
    except PluggyConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PluggyApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Clique registrado com o usuário ANONIMIZADO (hash), sem token, credencial
    # ou valor — só quem, quando, quanto demorou e como terminou.
    itens = result.get("items") or []
    await log_system_event(
        "info" if result.get("ok") else "warning",
        "of_manual_refresh",
        f"Refresh manual de Open Finance ({'ok' if result.get('ok') else 'com pendências'})",
        source="open_finance",
        details={
            "user_hash": hashlib.sha256(str(user_id).encode()).hexdigest()[:16],
            "ok": bool(result.get("ok")),
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "items": [{"item_id": i.get("item_id"), "state": i.get("state"),
                       "reason": i.get("reason")} for i in itens],
        },
    )

    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    # `ok` aqui é só "a requisição foi atendida" — NÃO é o veredito do refresh.
    # Quem diz se deu certo é `sync.ok` (conjunção por item) e `sync.items[]`, que
    # é o que o settings.html lê. Ler `data.ok` reintroduz o toast verde em cima
    # de um item perdido.
    return json.loads(shared.jdump({"ok": True, "sync": result, **snapshot}))


def _ip_prefix(request: Request) -> str:
    """IP do chamador truncado (/24 em v4, /48 em v6) — o suficiente pra ver um
    padrão de abuso, insuficiente pra identificar alguém."""
    ip = (request.client.host if request.client else "") or ""
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::/48"
    partes = ip.split(".")
    return ".".join(partes[:3]) + ".0/24" if len(partes) == 4 else "desconhecido"


def _verify_pluggy_webhook_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    signature = (signature_header or "").strip()
    if signature.startswith("sha256="):
        signature = signature.split("=", 1)[1]
    if not signature:
        return False

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


# Headers de secret compartilhado aceitos (caso configurados no painel da Pluggy).
_PLUGGY_WEBHOOK_SECRET_HEADERS = ("x-webhook-token", "x-pluggy-token", "x-api-key")


def _authorize_pluggy_webhook(request: Request, raw_body: bytes, secret: str) -> bool:
    """Autentica o webhook da Pluggy.

    ⚠️ A Pluggy NÃO assina o corpo com HMAC (a doc dela só oferece IP fixo +
    header custom opcional). Então NÃO dá pra exigir `X-Pluggy-Signature` — isso
    rejeitava todo evento real com 401. Aceitamos um secret compartilhado que a
    GENTE controla ao registrar o webhook:

    1. token na URL (`?token=<secret>`) — a URL do webhook é registrada por nós, no
       connect-token e no painel; é o caminho principal e não depende de o painel
       suportar header custom.
    2. header com o secret (`X-Webhook-Token`/`X-Pluggy-Token`/`X-Api-Key`) — caso
       você prefira configurar um header no painel.
    3. assinatura HMAC (`X-Pluggy-Signature`) — mantida por compat/futuro; hoje a
       Pluggy não manda, mas se um dia mandar, continua valendo.

    Comparações em tempo constante. Sem secret configurado, o chamador já barra (503).
    """
    # 1. token na query string
    token = request.query_params.get("token") or ""
    if token and hmac.compare_digest(token, secret):
        return True
    # 2. header com o secret
    for header_name in _PLUGGY_WEBHOOK_SECRET_HEADERS:
        value = (request.headers.get(header_name) or "").strip()
        if value and hmac.compare_digest(value, secret):
            return True
    # 3. assinatura HMAC do corpo (compat)
    signature = request.headers.get("X-Pluggy-Signature") or ""
    if signature and _verify_pluggy_webhook_signature(raw_body, signature, secret):
        return True
    return False


@router.post("/open-finance/pluggy/webhook")
async def open_finance_pluggy_webhook(request: Request):
    """
    Recebe eventos da Pluggy e responde rapido.
    Trabalho pesado de sync deve rodar fora do request.

    Autenticação: secret compartilhado via token na URL / header (a Pluggy não
    assina o corpo com HMAC). Ver `_authorize_pluggy_webhook`.
    """
    secret = (os.getenv("PLUGGY_WEBHOOK_SECRET") or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook não configurado.")

    raw_body = await request.body()
    if not _authorize_pluggy_webhook(request, raw_body, secret):
        # 401 mudo era um buraco de observabilidade: um webhook mal configurado
        # (ou uma tentativa de fora) não deixava rastro nenhum. O evento NÃO leva
        # secret, headers, corpo, nomes, contas, CPF, e-mail nem valores — só um
        # IP truncado e o horário.
        await log_system_event(
            "warning", "pluggy_webhook_unauthorized",
            "Webhook Pluggy recusado (credencial inválida)",
            source="open_finance",
            details={"ip_prefix": _ip_prefix(request), "has_token_param": bool(request.query_params.get("token"))},
        )
        raise HTTPException(status_code=401, detail="Não autorizado.")

    try:
        event = json.loads(raw_body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Webhook inválido.") from exc

    event_name = str(event.get("event") or event.get("type") or "")
    item_id = str(event.get("itemId") or event.get("item_id") or event.get("item", {}).get("id") or "")
    # `item/updated` NÃO escreve mais ACTIVE: quem afirma que sincronizou é o sync,
    # depois de consultar o item e puxar as contas. O webhook só diz o que a Pluggy
    # disse.
    status_by_event = {
        "item/created": "UPDATING",
        "item/error": "ERROR",
        "item/deleted": "DELETED",
    }
    status = status_by_event.get(event_name)
    if item_id and status:
        await asyncio.to_thread(update_pluggy_open_finance_item_status, item_id, status, event)

    # Antes de qualquer sync, resolve a POSSE do item. O `limit 1` sem `order by`
    # que existia aqui sorteava um dono quando o item aparecia em duas contas.
    conexoes = await asyncio.to_thread(get_connections_by_item_id, item_id) if item_id else []

    # transactions/deleted: a Pluggy manda os ids removidos — apaga direto, senão ficam
    # órfãos (um re-sync não os removeria, pois não voltam no list_transactions).
    if item_id and event_name == "transactions/deleted":
        deleted_ids = event.get("transactionIds") or event.get("transactionsIds") or []
        if isinstance(deleted_ids, list) and deleted_ids:
            await asyncio.to_thread(delete_open_finance_transactions, item_id, deleted_ids)
    elif item_id and event_name in PLUGGY_SYNC_EVENTS:
        if len(conexoes) == 1:
            _schedule_pluggy_sync(item_id)
        elif not conexoes:
            # Item que não conhecemos: registra (o GET /items lista devolve 401,
            # então este é o único jeito de saber que ele existe) e NÃO sincroniza.
            await log_system_event(
                "warning", "of_webhook_item_unknown",
                "Webhook de item sem conexão local",
                source="open_finance", details={"item_id": item_id, "event": event_name},
            )
            await asyncio.to_thread(
                register_item, None, provider_item_id=item_id,
                origin="webhook", last_event=event_name,
            )
        else:
            # Dois donos possíveis: sincronizar um deles é sincronizar a carteira
            # do usuário errado. Recusa.
            await log_system_event(
                "error", "of_item_owner_conflict",
                "Item Pluggy ligado a mais de uma conexão — sync recusado",
                source="open_finance",
                details={"item_id": item_id, "connections": len(conexoes), "event": event_name},
            )

    await log_system_event(
        "info" if event_name != "item/error" else "warning",
        "pluggy_webhook_received",
        f"Webhook Pluggy recebido: {event_name or 'evento desconhecido'}",
        source="open_finance",
        details={"event": event_name, "item_id": item_id},
    )
    return {"received": True}


@router.post("/open-finance/{user_id}/mock-connect")
async def open_finance_mock_connect_route(request: Request, user_id: int, payload: OpenFinanceMockConnectPayload):
    shared.authorize_dashboard_access(request, user_id)
    result = await asyncio.to_thread(
        create_mock_open_finance_connection,
        user_id,
        payload.institution or "nubank",
    )

    await asyncio.to_thread(
        record_audit_event,
        user_id,
        AuditEvent.OPEN_FINANCE_CONNECTED,
        request=request,
        details={"provider": "mock", "institution": payload.institution or "nubank"},
    )

    snapshot = await asyncio.to_thread(get_open_finance_snapshot, user_id)
    return json.loads(shared.jdump({"ok": True, "sync": result, **snapshot}))


@router.delete("/open-finance/{user_id}")
async def open_finance_disconnect_route(request: Request, user_id: int):
    shared.authorize_dashboard_access(request, user_id)

    # Deleta os items na Pluggy ANTES do disconnect local (best-effort). Sem isso, remover
    # a conexão apagava só o nosso registro e o item ficava órfão na Pluggy, bloqueando a
    # reconexão ("já possui conexão com este acesso"). Falha por item não impede o
    # disconnect local (não pioramos o comportamento antigo).
    pluggy_item_ids = await asyncio.to_thread(list_pluggy_item_ids, user_id)
    if pluggy_item_ids:
        api_key = None
        try:
            api_key = await asyncio.to_thread(create_pluggy_api_key)
        except Exception as exc:  # noqa: BLE001 — best-effort; segue pro disconnect local
            await log_system_event(
                "warning", "pluggy_disconnect_auth_failed",
                f"Sem apiKey pra deletar items no disconnect do user {user_id}: {exc}",
                source="open_finance", details={"user_id": user_id, "error": str(exc)[:200]},
            )
        if api_key:
            for item_id in pluggy_item_ids:
                try:
                    await asyncio.to_thread(delete_pluggy_item, item_id, api_key)
                except Exception as exc:  # noqa: BLE001 — best-effort por item
                    await log_system_event(
                        "warning", "pluggy_item_delete_failed",
                        f"Falha ao deletar item {item_id} na Pluggy no disconnect: {exc}",
                        source="open_finance",
                        details={"item_id": item_id, "error": str(exc)[:200]},
                    )

    deleted = await asyncio.to_thread(disconnect_open_finance_connection, user_id)

    if deleted:
        await asyncio.to_thread(
            record_audit_event,
            user_id,
            AuditEvent.OPEN_FINANCE_DISCONNECTED,
            request=request,
        )

    return {"ok": True, "deleted": deleted}
