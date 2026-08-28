"""Listagem de lançamentos de UMA categoria (tela Categorias do dashboard).

Só leitura. O CRUD de /categories continua no monólito
(finance_bot_websocket_custom.py) — mover é outro PR.
"""

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from frontend.routes import shared

router = APIRouter()

# Duas pernas na UNION de `list_launches_by_category`; o cursor carrega qual é
# porque o `ord_id` de cada uma vem de uma sequência diferente.
_FONTES = ("launches", "credito")


def _parse_cursor(raw: str | None):
    """`<criado_em ISO>|<fonte>|<ord_id>` → a tupla que o keyset compara.

    O cliente devolve o `next_cursor` que recebeu — mas o formato é LEGÍVEL, não
    cifrado: quem abrir o DevTools lê a data, a perna e o id cru da linha. Isso é
    aceito de propósito. O id só serve de marcador de página (nenhuma LINHA sai
    com ele, ver db/accounts.py) e as duas pernas filtram por `user_id`, então
    cursor forjado no máximo pagina a própria lista de quem forjou.

    Fronteira de confiança — o que chega aqui é texto de query string, então dt,
    fonte e id são validados antes de virar parâmetro (`ord_id` vai pro SQL
    como int e `fonte` só pode ser uma das duas pernas). Cursor corrompido é
    400, não uma página silenciosamente errada. A validação é mais FROUXA do que
    parece e pode continuar sendo: `int()` aceita espaço em volta e dígito
    arábico-índico ('١٢٣' = 123), `fromisoformat` aceita várias grafias de data —
    tudo isso vira um ponto de corte válido numa lista que já é do próprio
    usuário, e o que não converte cai no 400.
    """
    if not raw:
        return None
    partes = raw.split("|")
    if len(partes) != 3 or partes[1] not in _FONTES:
        raise HTTPException(status_code=400, detail="Cursor inválido.")
    try:
        return datetime.fromisoformat(partes[0]), partes[1], int(partes[2])
    except ValueError:
        raise HTTPException(status_code=400, detail="Cursor inválido.")


def _fmt_cursor(after) -> str | None:
    if not after:
        return None
    dt, fonte, ord_id = after
    return f"{dt.isoformat()}|{fonte}|{ord_id}"


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
    cursor: str | None = None,
):
    """Lançamentos de uma categoria (launches + cartão), mais recente primeiro.

    Sem janela de data → histórico inteiro da categoria (limitado pelo plano,
    igual a /history/{id}/list). O `resumo` cobre TODAS as linhas que casam, não
    só as `limit` devolvidas (window aggregate antes do LIMIT), então o rodapé
    "N de M" mostra o número real.

    `include_internal=false` + `tipo=despesa` é o que a Distribuição do mês
    manda: sem os dois a lista contradiz o número que o usuário acabou de
    clicar (o donut filtra movimento interno e só conta despesa).

    `cursor` é o "carregar mais" da tela: sai daqui como `next_cursor` e volta
    como veio (opaco por CONTRATO, não por cifra — ver `_parse_cursor`). É
    KEYSET, não OFFSET, porque o bot escreve no banco enquanto o dashboard está
    aberto — uma transação que chega pelo WhatsApp
    entra ACIMA do corte e desloca a fronteira, e o OFFSET repetia a última
    linha da página anterior e comia outra (`db/accounts.py`). `resumo.n_total`
    continua sendo o total REAL (window aggregate ANTES do LIMIT), então o front
    sabe quantas páginas faltam sem uma segunda query.

    Fronteira: `limit` fora de [1, ∞) é 422 (o `ge` do FastAPI, mesma classe de
    `limit=abc`) — pedir "nenhuma linha" e receber uma seria inventar resposta;
    `limit` acima de 100 o servidor CORTA, que é teto, não contradição. Cursor
    malformado é 400 (`_parse_cursor`). `from` depois de `to` é 400: hoje
    devolvia 200 com lista vazia, e "não achei nada" é uma resposta errada pra
    um pedido impossível.
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

    after = _parse_cursor(cursor)
    # Tudo por KEYWORD: `list_launches_by_category` tem 8 parâmetros e 6 deles
    # são opcionais — posicional, inserir um parâmetro na assinatura troca
    # `tipo` por `limit` sem erro nenhum, e a rota devolve outra lista calada.
    rows, resumo = await asyncio.to_thread(
        list_launches_by_category,
        user_id=user_id,
        categoria=cat,
        start_date=start,
        end_date=end,
        tipo=tipo,
        limit=min(int(limit), 100),
        include_internal=include_internal,
        after=after,
    )
    # `next_after` traz `ord_id`, que é o id CRU das duas tabelas — sai da LINHA
    # (onde seria handle de delete) e volta só dentro do cursor, em texto claro e
    # de propósito (ver `_parse_cursor` e `list_launches_by_category`).
    next_cursor = _fmt_cursor(resumo.pop("next_after", None))
    return {
        "ok": True,
        "launches": [{**r, "data": r["data"].isoformat() if r["data"] else None} for r in rows],
        "resumo": resumo,
        "next_cursor": next_cursor,
        # Janela REALMENTE aplicada: o front avisa "desde dd/mm" quando o plano
        # cortou um pedido de histórico inteiro.
        "window": {
            "from": start.isoformat() if start else None,
            "to": end.isoformat() if end else None,
            "capped_by_plan": capped,
        },
    }
