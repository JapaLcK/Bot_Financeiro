"""Listagem de lançamentos de UMA categoria (tela Categorias do dashboard).

Só leitura. O CRUD de /categories continua no monólito
(finance_bot_websocket_custom.py) — mover é outro PR.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from frontend.routes import shared

router = APIRouter()


@router.get("/categories/{user_id}/launches")
async def category_launches_route(
    request: Request,
    user_id: int,
    categoria: str = Query(...),
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    tipo: str | None = None,
    include_internal: bool = True,
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
):
    """Lançamentos de uma categoria (launches + cartão), mais recente primeiro.

    Sem janela de data → histórico inteiro da categoria (limitado pelo plano,
    igual a /history/{id}/list). O `resumo` cobre TODAS as linhas que casam, não
    só as `limit` devolvidas (window aggregate antes do LIMIT), então o rodapé
    "N de M" mostra o número real.

    `include_internal=false` + `tipo=despesa` é o que a Distribuição do mês
    manda: sem os dois a lista contradiz o número que o usuário acabou de
    clicar (o donut filtra movimento interno e só conta despesa).

    `offset` é o "carregar mais" da tela: `resumo.n_total` é o total REAL
    (window aggregate ANTES do LIMIT), então o front sabe quantas páginas faltam
    sem uma segunda query. A ordem do SQL é total (`db/accounts.py`), senão a
    página 2 repetiria linha da 1.

    Fronteira: `limit` fora de [1, ∞) e `offset` negativo são 422 (o `ge` do
    FastAPI, mesma classe de `limit=abc`) — pedir "nenhuma linha" e receber uma
    seria inventar resposta; `limit` acima de 100 o servidor CORTA, que é teto,
    não contradição. `from` depois de `to` é 400: hoje devolvia 200 com lista
    vazia, e "não achei nada" é uma resposta errada pra um pedido impossível.
    """
    shared.authorize_dashboard_access(request, user_id)
    from core.services.plan_service import history_earliest_date
    from db import list_launches_by_category

    cat = (categoria or "").strip()
    if not cat:
        raise HTTPException(status_code=400, detail="Parâmetro 'categoria' é obrigatório.")

    start = shared.parse_date_param(from_, "from")
    end = shared.parse_date_param(to, "to")
    if start and end and start > end:
        raise HTTPException(
            status_code=400, detail="Janela inválida: 'from' é depois de 'to'.",
        )
    # Teto de histórico do plano — mesmo clamp de /history/{id}/list e
    # /history/{id}/quick-stats. Sem ele esta rota mostrava, por uma porta nova,
    # o histórico inteiro pra quem paga por 3 meses.
    earliest = await asyncio.to_thread(history_earliest_date, user_id)
    # `capped_by_plan` sai na resposta porque a TELA muda de frase com ele: sem
    # corte ela promete "tudo nesta categoria", com corte ela tem que dizer
    # desde quando. Derivar isso no front a partir de `window.from` só funciona
    # quando o pedido veio sem janela — pela Distribuição do mês (que manda
    # from/to) o corte ficaria invisível, e numa conta Grátis
    # (`history_current_month_only`) o mês anterior sairia vazio sem explicação.
    capped = bool(earliest and (start is None or start < earliest))
    if capped:
        start = earliest

    rows, resumo = await asyncio.to_thread(
        list_launches_by_category,
        user_id,
        cat,
        start,
        end,
        tipo,
        min(int(limit), 100),
        include_internal,
        offset,
    )
    return {
        "ok": True,
        "launches": [{**r, "data": r["data"].isoformat() if r["data"] else None} for r in rows],
        "resumo": resumo,
        # Janela REALMENTE aplicada: o front avisa "desde dd/mm" quando o plano
        # cortou um pedido de histórico inteiro.
        "window": {
            "from": start.isoformat() if start else None,
            "to": end.isoformat() if end else None,
            "capped_by_plan": capped,
        },
    }
