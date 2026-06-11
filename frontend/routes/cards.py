"""Rotas de cartões de crédito, parcelamentos e faturas.

Etapa 5 do refactor Fase 1 (docs/refactor_plan.md): movidas de
finance_bot_websocket_custom.py sem mudança de comportamento.
"""

import asyncio
import pathlib

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db import create_card
from frontend.routes import shared

router = APIRouter()


class CardCreatePayload(BaseModel):
    name: str
    closing_day: int
    due_day: int
    color: str | None = None
    flag: str | None = None
    last4: str | None = None
    credit_limit: float | None = None


@router.post("/cards/{user_id}")
async def create_card_route(request: Request, user_id: int, payload: CardCreatePayload):
    """Cria um cartão de crédito."""
    shared.authorize_dashboard_access(request, user_id)

    # Free: respeita cards_max do plano (1). Pro: ilimitado.
    from core.services.plan_service import get_user_limits
    from db.cards import list_cards
    limits = get_user_limits(user_id)
    cards_max = limits["cards_max"]
    if cards_max is not None:
        existing = await asyncio.to_thread(list_cards, user_id)
        if len(existing) >= cards_max:
            raise HTTPException(
                status_code=403,
                detail={"error": "pro_required", "feature": "cards_unlimited"},
            )

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nome do cartão é obrigatório.")
    if len(name) > 80:
        raise HTTPException(status_code=400, detail="Nome muito longo (máx. 80 caracteres).")
    if not (1 <= payload.closing_day <= 31):
        raise HTTPException(status_code=400, detail="Dia de fechamento deve estar entre 1 e 31.")
    if not (1 <= payload.due_day <= 31):
        raise HTTPException(status_code=400, detail="Dia de vencimento deve estar entre 1 e 31.")
    if payload.credit_limit is not None and payload.credit_limit < 0:
        raise HTTPException(status_code=400, detail="Limite não pode ser negativo.")

    try:
        card_id = await asyncio.to_thread(
            create_card, user_id, name, payload.closing_day, payload.due_day,
            color=payload.color, flag=payload.flag, last4=payload.last4,
            credit_limit=payload.credit_limit,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("nome_duplicado:"):
            raise HTTPException(status_code=409, detail=f"Já existe um cartão chamado \"{name}\".") from exc
        if msg.startswith("color_invalido:"):
            raise HTTPException(status_code=400, detail="Cor inválida. Use: purple, coral, gold, green, blue ou gray.") from exc
        if msg.startswith("flag_invalida:"):
            raise HTTPException(status_code=400, detail="Bandeira inválida. Use: Visa, Mastercard, Elo, Amex, Hipercard ou Outros.") from exc
        if msg.startswith("last4_invalido:"):
            raise HTTPException(status_code=400, detail="Últimos 4 dígitos devem ser exatamente 4 números.") from exc
        raise HTTPException(status_code=400, detail=msg) from exc

    shared.invalidate_dashboard_current_cache(user_id)
    return {
        "ok": True,
        "card": {
            "id": int(card_id),
            "name": name,
            "closing_day": payload.closing_day,
            "due_day": payload.due_day,
            "color": payload.color,
            "flag": payload.flag,
            "last4": payload.last4,
            "credit_limit": payload.credit_limit,
        },
    }


class CardUpdatePayload(BaseModel):
    name: str | None = None
    closing_day: int | None = None
    due_day: int | None = None
    color: str | None = None
    flag: str | None = None
    last4: str | None = None
    credit_limit: float | None = None
    clear_last4: bool = False
    clear_limit: bool = False


class CardReorderPayload(BaseModel):
    ordered_ids: list[int]


@router.get("/cards/{user_id}/summary")
async def cards_summary_route(request: Request, user_id: int):
    """Lista todos os cartões com dados agregados (fatura aberta, limite usado).
    Alimenta a view /app#cards do dashboard.

    Performance: faz 1 query Postgres com subqueries agregadas em vez de
    N+1 (era ~22 queries × latência por chamada).
    """
    shared.authorize_dashboard_access(request, user_id)
    from db.connection import get_conn

    def _build():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select
                      c.id, c.name, c.color, c.flag, c.last4,
                      c.closing_day, c.due_day, c.credit_limit,
                      (u.default_card_id = c.id) as is_default,
                      coalesce((
                        select b.total - coalesce(b.paid_amount, 0)
                          from credit_bills b
                         where b.card_id = c.id and b.user_id = c.user_id and b.status = 'open'
                         order by b.period_end desc
                         limit 1
                      ), 0) as open_due,
                      coalesce((
                        select b.total
                          from credit_bills b
                         where b.card_id = c.id and b.user_id = c.user_id and b.status = 'open'
                         order by b.period_end desc
                         limit 1
                      ), 0) as open_total,
                      coalesce((
                        select b.paid_amount
                          from credit_bills b
                         where b.card_id = c.id and b.user_id = c.user_id and b.status = 'open'
                         order by b.period_end desc
                         limit 1
                      ), 0) as open_paid,
                      (
                        select b.id
                          from credit_bills b
                         where b.card_id = c.id and b.user_id = c.user_id and b.status = 'open'
                         order by b.period_end desc
                         limit 1
                      ) as open_bill_id,
                      (
                        select b.period_end
                          from credit_bills b
                         where b.card_id = c.id and b.user_id = c.user_id and b.status = 'open'
                         order by b.period_end desc
                         limit 1
                      ) as open_period_end,
                      coalesce((
                        select sum(b.total - coalesce(b.paid_amount, 0))
                          from credit_bills b
                         where b.card_id = c.id and b.user_id = c.user_id
                      ), 0) as credit_used
                    from credit_cards c
                    left join users u on u.id = c.user_id
                    where c.user_id = %s
                    order by c.name
                    """,
                    (user_id,),
                )
                rows = cur.fetchall()

        out = []
        for r in rows:
            credit_limit = float(r["credit_limit"]) if r.get("credit_limit") is not None else None
            usage = float(r["credit_used"] or 0)
            available = (credit_limit - usage) if credit_limit is not None else None
            out.append({
                "id": int(r["id"]),
                "name": r["name"],
                "color": r.get("color"),
                "flag": r.get("flag"),
                "last4": r.get("last4"),
                "closing_day": r["closing_day"],
                "due_day": r["due_day"],
                "credit_limit": credit_limit,
                "is_default": bool(r.get("is_default")),
                "open_bill": {
                    "id": int(r["open_bill_id"]) if r.get("open_bill_id") else None,
                    "total": float(r["open_total"] or 0),
                    "paid_amount": float(r["open_paid"] or 0),
                    "due_amount": float(r["open_due"] or 0),
                    "period_end": r["open_period_end"].isoformat() if r.get("open_period_end") else None,
                },
                "next_bill": {"total": 0.0, "period_end": None},  # TODO Sprint 2: calcular se necessário
                "credit_used": usage,
                "credit_available": available,
            })
        return out

    cards = await asyncio.to_thread(_build)
    return {"ok": True, "cards": cards}


@router.patch("/cards/{user_id}/reorder")
async def reorder_cards_route(request: Request, user_id: int, payload: CardReorderPayload):
    """Salva nova ordem manual dos cartões (drag-to-reorder).
    Recebe ordered_ids = [id_primeiro, id_segundo, ...] e grava display_order
    sequencial (0..N-1). Cartões fora da lista mantêm o que já tinham."""
    shared.authorize_dashboard_access(request, user_id)

    if not payload.ordered_ids:
        raise HTTPException(status_code=400, detail="Lista de cartões vazia.")
    if len(payload.ordered_ids) > 200:
        raise HTTPException(status_code=400, detail="Muitos cartões na lista.")

    from db.cards import reorder_cards

    updated = await asyncio.to_thread(reorder_cards, user_id, payload.ordered_ids)
    shared.invalidate_dashboard_current_cache(user_id)
    return {"ok": True, "updated": updated}


@router.patch("/cards/{user_id}/{card_id}")
async def update_card_route(request: Request, user_id: int, card_id: int, payload: CardUpdatePayload):
    """Edita campos de um cartão (name, dias, color, flag, last4, credit_limit).
    Use clear_last4=true ou clear_limit=true pra apagar explicitamente."""
    shared.authorize_dashboard_access(request, user_id)

    from db.cards import update_card_meta, get_card_by_id

    if payload.name is not None:
        n = (payload.name or "").strip()
        if not n:
            raise HTTPException(status_code=400, detail="Nome do cartão é obrigatório.")
        if len(n) > 80:
            raise HTTPException(status_code=400, detail="Nome muito longo (máx. 80 caracteres).")
    if payload.closing_day is not None and not (1 <= payload.closing_day <= 31):
        raise HTTPException(status_code=400, detail="Dia de fechamento deve estar entre 1 e 31.")
    if payload.due_day is not None and not (1 <= payload.due_day <= 31):
        raise HTTPException(status_code=400, detail="Dia de vencimento deve estar entre 1 e 31.")
    if payload.credit_limit is not None and payload.credit_limit < 0:
        raise HTTPException(status_code=400, detail="Limite não pode ser negativo.")

    try:
        updated = await asyncio.to_thread(
            update_card_meta, user_id, card_id,
            name=payload.name,
            closing_day=payload.closing_day,
            due_day=payload.due_day,
            color=payload.color,
            flag=payload.flag,
            last4=payload.last4,
            credit_limit=payload.credit_limit,
            clear_last4=payload.clear_last4,
            clear_limit=payload.clear_limit,
        )
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("nome_duplicado:"):
            raise HTTPException(status_code=409, detail="Já existe outro cartão com esse nome.") from exc
        if msg.startswith("color_invalido:"):
            raise HTTPException(status_code=400, detail="Cor inválida.") from exc
        if msg.startswith("flag_invalida:"):
            raise HTTPException(status_code=400, detail="Bandeira inválida.") from exc
        if msg.startswith("last4_invalido:"):
            raise HTTPException(status_code=400, detail="Últimos 4 dígitos devem ser exatamente 4 números.") from exc
        raise HTTPException(status_code=400, detail=msg) from exc

    if not updated:
        raise HTTPException(status_code=404, detail="Cartão não encontrado ou nenhum campo alterado.")

    shared.invalidate_dashboard_current_cache(user_id)
    fresh = await asyncio.to_thread(get_card_by_id, user_id, card_id)
    return {
        "ok": True,
        "card": {
            "id": int(fresh["id"]),
            "name": fresh["name"],
            "closing_day": fresh["closing_day"],
            "due_day": fresh["due_day"],
            "color": fresh.get("color"),
            "flag": fresh.get("flag"),
            "last4": fresh.get("last4"),
            "credit_limit": float(fresh["credit_limit"]) if fresh.get("credit_limit") is not None else None,
        },
    }


@router.get("/cards/{user_id}/{card_id}/delete-impact")
async def card_delete_impact_route(request: Request, user_id: int, card_id: int):
    """Retorna o que será apagado se o cartão for excluído.
    Frontend usa pra mostrar confirmação ao usuário."""
    shared.authorize_dashboard_access(request, user_id)
    from db.cards import get_card_delete_impact, get_card_by_id

    card = await asyncio.to_thread(get_card_by_id, user_id, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Cartão não encontrado.")
    impact = await asyncio.to_thread(get_card_delete_impact, user_id, card_id)
    return {
        "ok": True,
        "card_name": card["name"],
        "impact": impact,
    }


@router.delete("/cards/{user_id}/{card_id}")
async def delete_card_route(request: Request, user_id: int, card_id: int):
    """Exclui um cartão e tudo vinculado (faturas, parcelamentos).
    ON DELETE CASCADE cuida das tabelas credit_bills/credit_transactions."""
    shared.authorize_dashboard_access(request, user_id)
    from db.cards import delete_card, get_card_by_id

    card = await asyncio.to_thread(get_card_by_id, user_id, card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Cartão não encontrado.")

    deleted = await asyncio.to_thread(delete_card, user_id, card_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cartão não encontrado.")

    shared.invalidate_dashboard_current_cache(user_id)
    return {"ok": True, "deleted_card_id": card_id, "deleted_card_name": card["name"]}


# ─────────────────────────────────────────────────────────────────────────────
# Parcelamentos (Sprint 2 — 2026-05-14)
# ─────────────────────────────────────────────────────────────────────────────

class InstallmentUpdatePayload(BaseModel):
    nome: str | None = None
    categoria: str | None = None


@router.get("/installments/{user_id}/list")
async def installments_list_route(
    request: Request,
    user_id: int,
    sort: str = "urgency",
):
    """Lista parcelamentos do user com detalhe por parcela.
    Alimenta a view /app#installments do dashboard."""
    shared.authorize_dashboard_access(request, user_id)
    from db.cards import list_installment_groups_detailed

    if sort not in ("urgency", "recent"):
        sort = "urgency"
    groups = await asyncio.to_thread(list_installment_groups_detailed, user_id, sort)
    return {"ok": True, "installments": groups}


@router.get("/installments/{user_id}/{group_id}/delete-impact")
async def installment_delete_impact_route(request: Request, user_id: int, group_id: str):
    """Retorna o impacto de excluir o parcelamento (sem excluir).
    Frontend usa pra escolher mensagem do modal: com vs sem parcelas pagas."""
    shared.authorize_dashboard_access(request, user_id)
    from db.cards import get_installment_group_delete_impact

    impact = await asyncio.to_thread(get_installment_group_delete_impact, user_id, group_id)
    if not impact:
        raise HTTPException(status_code=404, detail="Parcelamento não encontrado.")
    return {"ok": True, "impact": impact}


@router.post("/installments/{user_id}/{group_id}/anticipate")
async def installment_anticipate_route(request: Request, user_id: int, group_id: str):
    """Antecipa a próxima parcela pendente: paga à vista da conta corrente.
    Deleta a tx do parcelamento + reduz fatura aberta + cria launch de despesa."""
    shared.authorize_dashboard_access(request, user_id)
    from db.cards import anticipate_installment

    result = await asyncio.to_thread(anticipate_installment, user_id, group_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail="Sem parcelas pendentes pra antecipar (parcelamento já quitado).",
        )
    shared.invalidate_dashboard_current_cache(user_id)
    return {"ok": True, "result": result}


@router.delete("/installments/{user_id}/{group_id}")
async def installment_delete_route(request: Request, user_id: int, group_id: str):
    """Exclui parcelamento. Comportamento Option B:
    - tx em faturas abertas: deletadas (open bill cai, saldo do mês volta)
    - tx em faturas pagas: viram órfãs (group_id=null, nota+sufixo).
      Fatura paga intacta — dinheiro não volta pra conta."""
    shared.authorize_dashboard_access(request, user_id)
    from db.cards import undo_installment_group

    result = await asyncio.to_thread(undo_installment_group, user_id, group_id)
    if not result:
        raise HTTPException(status_code=404, detail="Parcelamento não encontrado.")
    shared.invalidate_dashboard_current_cache(user_id)
    return {"ok": True, "result": result}


@router.patch("/installments/{user_id}/{group_id}")
async def installment_update_route(
    request: Request, user_id: int, group_id: str,
    payload: InstallmentUpdatePayload,
):
    """Edita nome (nota) e/ou categoria de todas as parcelas do grupo."""
    shared.authorize_dashboard_access(request, user_id)
    from db.cards import update_installment_group_meta

    if payload.nome is not None:
        n = (payload.nome or "").strip()
        if len(n) > 200:
            raise HTTPException(status_code=400, detail="Nome muito longo (máx. 200 caracteres).")
    if payload.categoria is not None:
        c = (payload.categoria or "").strip()
        if len(c) > 80:
            raise HTTPException(status_code=400, detail="Categoria muito longa (máx. 80 caracteres).")

    updated = await asyncio.to_thread(
        update_installment_group_meta, user_id, group_id,
        nome=payload.nome, categoria=payload.categoria,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Parcelamento não encontrado ou nenhum campo alterado.")
    shared.invalidate_dashboard_current_cache(user_id)
    return {"ok": True}


def _serialize_bill(row: dict) -> dict:
    total = float(row.get("total") or 0)
    paid = float(row.get("paid_amount") or 0)
    due = max(0.0, total - paid)
    pe = row.get("period_end")
    label = ""
    if pe:
        label = f"{shared.months_pt()[pe.month - 1]}/{pe.year}"
    return {
        "id": int(row["id"]),
        "card_id": int(row["card_id"]) if row.get("card_id") is not None else None,
        "card_name": row.get("card_name") or "",
        "period_start": pe and row.get("period_start").isoformat() if row.get("period_start") else None,
        "period_end": pe.isoformat() if pe else None,
        "label": label,
        "status": row.get("status") or "open",
        "total": total,
        "paid_amount": paid,
        "due_amount": due,
    }


@router.get("/bills/{user_id}")
async def list_bills_route(
    request: Request,
    user_id: int,
    card_id: int | None = None,
    include_closed: bool = False,
):
    """Lista faturas do usuário (cartão e valores).

    - Sem params: só abertas com saldo > 0 (default histórico, usado por
      `onCardRowClick` e modal de pagamento).
    - `card_id`: filtra por um cartão específico.
    - `include_closed=true`: inclui também `paid` e `closed` (usado pelas
      setas de navegação no modal de fatura pra ver meses passados/
      próximos cheios).
    """
    shared.authorize_dashboard_access(request, user_id)
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

    async with await shared.db_connect() as conn:
        async with conn.cursor() as cur:
            status_filter = "" if include_closed else "AND b.status = 'open'"
            card_filter = "AND b.card_id = %s" if card_id else ""
            params: list = [int(user_id)]
            if card_id:
                params.append(int(card_id))
            await cur.execute(
                f"""
                SELECT b.id, b.card_id, c.name AS card_name,
                       b.period_start, b.period_end, b.status,
                       b.total, COALESCE(b.paid_amount, 0) AS paid_amount
                FROM credit_bills b
                JOIN credit_cards c ON c.id = b.card_id
                WHERE b.user_id = %s
                  {status_filter}
                  {card_filter}
                ORDER BY b.period_end ASC, c.name ASC
                """,
                params,
            )
            rows = await cur.fetchall()

            await cur.execute(
                "SELECT balance FROM accounts WHERE user_id=%s", (int(user_id),)
            )
            bal_row = await cur.fetchone()

    bills = [_serialize_bill(dict(r)) for r in (rows or [])]
    if not include_closed:
        # Comportamento original — só esconde bills "vazias" no fluxo padrão.
        bills = [b for b in bills if b["due_amount"] > 0 or b["total"] > 0]

    balance = float(bal_row["balance"]) if bal_row else 0.0
    return {"ok": True, "balance": balance, "bills": bills}


@router.get("/bills/{user_id}/{bill_id}")
async def get_bill_detail_route(request: Request, user_id: int, bill_id: int):
    """Detalhe da fatura: período, totais e lista de transações."""
    shared.authorize_dashboard_access(request, user_id)
    async with await shared.db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT b.id, b.card_id, c.name AS card_name,
                       b.period_start, b.period_end, b.status,
                       b.total, COALESCE(b.paid_amount, 0) AS paid_amount
                FROM credit_bills b
                JOIN credit_cards c ON c.id = b.card_id
                WHERE b.user_id=%s AND b.id=%s
                LIMIT 1
                """,
                (int(user_id), int(bill_id)),
            )
            bill_row = await cur.fetchone()
            if not bill_row:
                raise HTTPException(status_code=404, detail="Fatura não encontrada.")

            await cur.execute(
                """
                SELECT id, valor, categoria, nota, purchased_at, is_refund,
                       installment_no, installments_total
                FROM credit_transactions
                WHERE user_id=%s AND bill_id=%s
                ORDER BY purchased_at DESC, id DESC
                """,
                (int(user_id), int(bill_id)),
            )
            tx_rows = await cur.fetchall()

    transactions = []
    for t in (tx_rows or []):
        transactions.append({
            "id": int(t["id"]),
            "valor": float(t["valor"] or 0),
            "categoria": t.get("categoria"),
            "nota": t.get("nota"),
            "purchased_at": t["purchased_at"].isoformat() if t.get("purchased_at") else None,
            "is_refund": bool(t.get("is_refund")),
            "installment_no": t.get("installment_no"),
            "installments_total": t.get("installments_total"),
        })

    return {"ok": True, "bill": _serialize_bill(dict(bill_row)), "transactions": transactions}


class PayBillPayload(BaseModel):
    amount: float | None = None  # None = paga total em aberto


@router.post("/bills/{user_id}/{bill_id}/pay")
async def pay_bill_route(
    request: Request,
    user_id: int,
    bill_id: int,
    payload: PayBillPayload,
):
    """Paga (parcial ou total) uma fatura. Bloqueia se saldo insuficiente.
    Reusa `pay_bill_amount` (mesmo fluxo do `pagar fatura` do WhatsApp)."""
    shared.authorize_dashboard_access(request, user_id)
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
    from db import pay_bill_amount

    # Carrega fatura + saldo
    async with await shared.db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT b.id, b.card_id, c.name AS card_name,
                       b.period_end, b.total,
                       COALESCE(b.paid_amount, 0) AS paid_amount,
                       b.status
                FROM credit_bills b
                JOIN credit_cards c ON c.id = b.card_id
                WHERE b.user_id=%s AND b.id=%s
                LIMIT 1
                """,
                (int(user_id), int(bill_id)),
            )
            bill = await cur.fetchone()
            if not bill:
                raise HTTPException(status_code=404, detail="Fatura não encontrada.")

            await cur.execute(
                "SELECT balance FROM accounts WHERE user_id=%s", (int(user_id),)
            )
            acc = await cur.fetchone()
    balance = float(acc["balance"]) if acc else 0.0

    total = float(bill["total"] or 0)
    paid = float(bill["paid_amount"] or 0)
    due = max(0.0, total - paid)
    if due <= 0:
        raise HTTPException(status_code=400, detail="Esta fatura já está paga.")

    requested_amount = payload.amount
    if requested_amount is None:
        amount = due
    else:
        try:
            amount = float(requested_amount)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Valor inválido.")
        if amount <= 0:
            raise HTTPException(status_code=400, detail="O valor deve ser maior que zero.")
        if amount > due + 0.005:
            raise HTTPException(
                status_code=400,
                detail=f"Valor maior que o em aberto. Em aberto: R$ {due:.2f}",
            )

    # Saldo insuficiente bloqueia
    if balance < amount - 0.005:
        raise HTTPException(
            status_code=400,
            detail=f"Saldo insuficiente. Saldo atual: R$ {balance:.2f}, valor pedido: R$ {amount:.2f}.",
        )

    card_name = bill["card_name"] or "cartão"
    try:
        res = await asyncio.to_thread(
            pay_bill_amount, int(user_id), int(bill["card_id"]), card_name, float(amount), int(bill_id)
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao pagar fatura: {exc}") from exc

    if isinstance(res, dict) and res.get("error") == "amount_too_high":
        raise HTTPException(status_code=400, detail="Valor maior que o em aberto.")
    if isinstance(res, dict) and res.get("error") == "invalid_amount":
        raise HTTPException(status_code=400, detail="Valor inválido.")
    if not res:
        raise HTTPException(status_code=400, detail="Nada para pagar nessa fatura.")

    # Re-lê o estado da fatura pós-pagamento
    async with await shared.db_connect() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT status, total, COALESCE(paid_amount,0) AS paid_amount FROM credit_bills WHERE id=%s",
                (int(bill_id),),
            )
            after = await cur.fetchone()
    new_total = float(after["total"] or 0) if after else total
    new_paid = float(after["paid_amount"] or 0) if after else paid + amount
    new_due = max(0.0, new_total - new_paid)

    return {
        "ok": True,
        "paid": float(res.get("paid", amount)),
        "launch_id": int(res.get("launch_id")) if res.get("launch_id") is not None else None,
        "new_balance": float(res.get("new_balance", balance - amount)),
        "card_name": card_name,
        "bill_id": int(bill_id),
        "bill_status": (after or {}).get("status") or "open",
        "bill_total": new_total,
        "bill_paid_amount": new_paid,
        "bill_due_amount": new_due,
    }
