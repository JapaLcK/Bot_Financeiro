"""Rotas de caixinhas (pockets) e metas (goals).

Etapa 5 do refactor Fase 1 (docs/refactor_plan.md): movidas de
finance_bot_websocket_custom.py sem mudança de comportamento.
"""

import asyncio
import json
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.pg_text import detalhe_seguro
from core.services.metas_status import status_metas
from db import (
    create_pocket,
    delete_pocket,
    pocket_deposit_from_account,
    pocket_withdraw_to_account,
)
from frontend.routes import shared
from utils_text import valor_valido

router = APIRouter()


class PocketCreatePayload(BaseModel):
    name: str
    description: str | None = None
    interest_enabled: bool = True
    interest_rate: float = 1.0


class PocketMovePayload(BaseModel):
    amount: float | None = None
    nota: str | None = None
    withdraw_all: bool = False


@router.post("/pockets/{user_id}")
async def create_pocket_route(request: Request, user_id: int, payload: PocketCreatePayload):
    """Cria uma caixinha (pocket) com saldo zero."""
    shared.authorize_dashboard_access(request, user_id)

    # Free: respeita pockets_max do plano (1). Pro: ilimitado.
    from core.services.plan_service import get_user_limits
    from db.pockets import list_pockets
    limits = get_user_limits(user_id)
    pockets_max = limits["pockets_max"]
    if pockets_max is not None:
        existing = await asyncio.to_thread(list_pockets, user_id)
        if len(existing) >= pockets_max:
            raise HTTPException(
                status_code=403,
                detail={"error": "pro_required", "feature": "pockets_unlimited"},
            )

    name = (payload.name or "").strip()
    description = (payload.description or "").strip() or None
    interest_rate = float(payload.interest_rate or 1.0)
    if not name:
        raise HTTPException(status_code=400, detail="Nome da caixinha é obrigatório.")
    if len(name) > 80:
        raise HTTPException(status_code=400, detail="Nome muito longo (máx. 80 caracteres).")
    if description and len(description) > 200:
        raise HTTPException(status_code=400, detail="Descrição muito longa (máx. 200 caracteres).")
    if interest_rate <= 0:
        raise HTTPException(status_code=400, detail="Rendimento inválido.")

    try:
        launch_id, pocket_id, canon = await asyncio.to_thread(
            create_pocket,
            user_id,
            name,
            None,
            description,
            interest_enabled=payload.interest_enabled,
            interest_rate=interest_rate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detalhe_seguro(exc)) from exc
    if launch_id is None:
        # Já existe (#596: também com outra maiúscula). O 200 `created:false` fazia
        # a tela de Metas pegar o id e reescrever meta e rendimento da existente.
        raise HTTPException(status_code=400, detail="Já existe uma caixinha com esse nome.")

    shared.invalidate_dashboard_current_cache(user_id)
    return {
        "ok": True,
        "created": launch_id is not None,
        "pocket": {
            "id": int(pocket_id),
            "name": canon,
            "description": description,
            "interest_enabled": False,  # Q43: caixinha nova nasce sem rendimento
            "interest_rate": interest_rate,
            "interest_period": "cdi",
        },
    }


class PocketMetaPayload(BaseModel):
    name: str | None = None
    description: str | None = None
    target_amount: float | None = None
    target_date: str | None = None
    emoji: str | None = None
    color: str | None = None
    status: str | None = None
    interest_enabled: bool | None = None
    interest_rate: float | None = None
    clear_target: bool = False


@router.patch("/pockets/{user_id}/{pocket_id}/meta")
async def update_pocket_meta_route(
    request: Request, user_id: int, pocket_id: int, payload: PocketMetaPayload
):
    """PATCH em metadata da caixinha — incluindo target_amount/date pra virar meta.

    Pra remover a meta (mantendo a caixinha), passar `clear_target=true`.
    """
    shared.authorize_dashboard_access(request, user_id)
    from db.pockets import update_pocket_meta

    try:
        row = await asyncio.to_thread(
            update_pocket_meta,
            user_id, pocket_id,
            name=payload.name, description=payload.description,
            target_amount=payload.target_amount, target_date=payload.target_date,
            emoji=payload.emoji, color=payload.color, status=payload.status,
            interest_enabled=payload.interest_enabled, interest_rate=payload.interest_rate,
            clear_target=payload.clear_target,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=detalhe_seguro(exc))
    if not row:
        raise HTTPException(status_code=404, detail="Caixinha não encontrada.")
    shared.invalidate_dashboard_current_cache(user_id)
    pocket = dict(row)
    if pocket.get("target_amount") is not None:
        pocket["target_amount"] = float(pocket["target_amount"])
    if pocket.get("target_date"):
        pocket["target_date"] = pocket["target_date"].isoformat()
    if pocket.get("balance") is not None:
        pocket["balance"] = float(pocket["balance"])
    if pocket.get("interest_rate") is not None:
        pocket["interest_rate"] = float(pocket["interest_rate"])
    if pocket.get("last_interest_date"):
        pocket["last_interest_date"] = pocket["last_interest_date"].isoformat()
    return {"ok": True, "pocket": pocket}


@router.get("/goals/{user_id}/status")
async def goals_status_route(request: Request, user_id: int):
    """Lista as caixinhas COM meta (target_amount NOT NULL) + cálculo de ritmo.

    Retorna pra cada meta: pct_complete, days_left, monthly_pace_needed,
    monthly_pace_current (média dos últimos 3 meses), projected_at_current_pace,
    status_indicator ('ahead'|'on_track'|'tight'|'behind').
    """
    shared.authorize_dashboard_access(request, user_id)

    goals = await asyncio.to_thread(status_metas, user_id)
    return {"ok": True, "goals": goals}


@router.delete("/pockets/{user_id}/{pocket_name:path}")
async def delete_pocket_route(request: Request, user_id: int, pocket_name: str):
    """Exclui uma caixinha (apenas se o saldo estiver zerado)."""
    shared.authorize_dashboard_access(request, user_id)
    name = urllib.parse.unquote(pocket_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nome da caixinha é obrigatório.")

    try:
        launch_id, canon = await asyncio.to_thread(delete_pocket, int(user_id), name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Caixinha não encontrada.") from exc
    except ValueError as exc:
        msg = str(exc)
        if msg == "POCKET_NOT_ZERO":
            raise HTTPException(
                status_code=400,
                detail="Zere o saldo (saque para a conta) antes de remover a caixinha.",
            ) from exc
        raise HTTPException(status_code=400, detail=msg) from exc

    shared.invalidate_dashboard_current_cache(int(user_id))
    return {"ok": True, "launch_id": launch_id, "name": canon}


def _pocket_move_error(exc: ValueError) -> HTTPException:
    msg = str(exc)
    mapping = {
        "AMOUNT_INVALID": "Valor inválido (precisa ser maior que zero).",
        "INSUFFICIENT_ACCOUNT": "Saldo da conta principal não cobre esse valor.",
        "INSUFFICIENT_POCKET": "A caixinha não tem esse valor disponível.",
        "OF_POCKET_READONLY": "Essa caixinha é sincronizada com seu banco — mova o dinheiro pelo app do banco.",
    }
    return HTTPException(status_code=400, detail=mapping.get(msg, msg))


@router.post("/pockets/{user_id}/{pocket_name:path}/deposit")
async def pocket_deposit_route(request: Request, user_id: int, pocket_name: str, payload: PocketMovePayload):
    """Conta principal → caixinha."""
    shared.authorize_dashboard_access(request, user_id)
    name = urllib.parse.unquote(pocket_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nome da caixinha é obrigatório.")
    nota = (payload.nota or "").strip() or None
    if payload.amount is not None and not valor_valido(payload.amount):  # NaN quebrava o `funding`
        raise HTTPException(status_code=400, detail="Valor inválido (precisa ser maior que zero).")
    from core.services import funding

    source = (await asyncio.to_thread(
        funding.resolve_deterministic, int(user_id), payload.amount))["source"]
    try:
        launch_id, new_acc, new_pocket, canon = await asyncio.to_thread(
            pocket_deposit_from_account, int(user_id), name, payload.amount, nota,
            funding_source=funding.to_db_arg(source),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Caixinha não encontrada.") from exc
    except ValueError as exc:
        if str(exc) == "INSUFFICIENT_ACCOUNT":
            detail = funding.msg_insuficiente(int(user_id), payload.amount, acao="depósito", plain=True)
            raise HTTPException(status_code=400, detail=detail) from exc
        raise _pocket_move_error(exc) from exc

    shared.invalidate_dashboard_current_cache(int(user_id))
    return json.loads(shared.jdump({
        "ok": True,
        "launch_id": launch_id,
        "name": canon,
        "account_balance": new_acc,
        "pocket_balance": new_pocket,
    }))


@router.post("/pockets/{user_id}/{pocket_name:path}/withdraw")
async def pocket_withdraw_route(request: Request, user_id: int, pocket_name: str, payload: PocketMovePayload):
    """Caixinha → conta principal."""
    shared.authorize_dashboard_access(request, user_id)
    name = urllib.parse.unquote(pocket_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nome da caixinha é obrigatório.")
    nota = (payload.nota or "").strip() or None

    try:
        # O destino do saque sai de `db.destination_of_lots`, dentro da transação (#282).
        launch_id, new_acc, new_pocket, canon, taxes, _dest = await asyncio.to_thread(
            pocket_withdraw_to_account, int(user_id), name, payload.amount, nota,
            withdraw_all=bool(payload.withdraw_all),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Caixinha não encontrada.") from exc
    except ValueError as exc:
        raise _pocket_move_error(exc) from exc

    shared.invalidate_dashboard_current_cache(int(user_id))
    return json.loads(shared.jdump({
        "ok": True,
        "launch_id": launch_id,
        "name": canon,
        "account_balance": new_acc,
        "pocket_balance": new_pocket,
        "tax_summary": taxes,
    }))


@router.get("/pockets/{user_id}/{pocket_name:path}/history")
async def get_pocket_history_route(request: Request, user_id: int, pocket_name: str, limit: int = 100):
    """Histórico de depósitos e saques de uma caixinha específica."""
    shared.authorize_dashboard_access(request, user_id)
    name = (pocket_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nome da caixinha é obrigatório.")
    limit = max(min(int(limit or 100), 500), 1)

    async with await shared.db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, name, balance, target_amount, target_date, emoji, color, status,
                       description, interest_enabled, interest_rate, interest_period,
                       interest_tax_profile, last_interest_date,
                       source, of_investment_id
                FROM pockets
                WHERE user_id = %s AND lower(name) = lower(%s)
                LIMIT 1
                """,
                (int(user_id), name),
            )
            pocket_row = await cur.fetchone()
            if not pocket_row:
                raise HTTPException(status_code=404, detail="Caixinha não encontrada.")

            canon = pocket_row["name"]

            await cur.execute(
                """
                SELECT id, tipo, valor, alvo, nota, criado_em
                FROM launches
                WHERE user_id = %s
                  AND lower(alvo) = lower(%s)
                  AND tipo IN ('deposito_caixinha', 'saque_caixinha', 'criar_caixinha')
                ORDER BY criado_em DESC, id DESC
                LIMIT %s
                """,
                (int(user_id), canon, limit),
            )
            rows = await cur.fetchall()

    from core.services.plan_service import require_min_tier
    # a mesma régua do /goals/status: no Grátis a caixinha do banco congela e o
    # subtítulo do histórico pede pra reativar em vez de dizer "atualizado".
    of_plan_active = await asyncio.to_thread(require_min_tier, user_id, "essencial")

    history = []
    deposits_total = 0.0
    withdrawals_total = 0.0
    for r in (rows or []):
        v = float(r["valor"] or 0)
        tipo = r["tipo"]
        if tipo == "deposito_caixinha":
            deposits_total += v
        elif tipo == "saque_caixinha":
            withdrawals_total += v
        history.append({
            "id": int(r["id"]),
            "tipo": tipo,
            "valor": v,
            "nota": r.get("nota"),
            "criado_em": r["criado_em"].isoformat() if r.get("criado_em") else None,
        })

    return {
        "ok": True,
        "pocket": {
            "id": int(pocket_row["id"]),
            "name": canon,
            "balance": float(pocket_row["balance"] or 0),
            "target_amount": float(pocket_row["target_amount"]) if pocket_row.get("target_amount") is not None else None,
            "target_date": pocket_row["target_date"].isoformat() if pocket_row.get("target_date") else None,
            "emoji": pocket_row.get("emoji"),
            "color": pocket_row.get("color"),
            "status": pocket_row.get("status") or "active",
            "description": pocket_row.get("description"),
            "interest_enabled": bool(pocket_row.get("interest_enabled")),
            "interest_rate": float(pocket_row.get("interest_rate") or 1),
            "interest_period": pocket_row.get("interest_period") or "cdi",
            "interest_tax_profile": pocket_row.get("interest_tax_profile"),
            "last_interest_date": pocket_row["last_interest_date"].isoformat()
                                  if pocket_row.get("last_interest_date") else None,
            # a UI usa os dois pra esconder Depositar/Sacar: caixinha do banco é
            # read-only (db/pockets.py recusa com OF_POCKET_READONLY).
            "source": pocket_row.get("source"),
            "of_investment_id": (int(pocket_row["of_investment_id"])
                                 if pocket_row.get("of_investment_id") is not None else None),
            "of_plan_active": of_plan_active,
        },
        "totals": {
            "deposits": deposits_total,
            "withdrawals": withdrawals_total,
            "count": len(history),
        },
        "history": history,
    }
