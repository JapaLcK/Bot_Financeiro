"""
core/services/ai_chat/tools/listas_ia.py — as listas de lançamento da IA (`list_recent_launches`, `get_uncategorized_launches`), lidas
pelo MESMO `listar` do Extrato (`db/lancamentos_leitura.recentes`): mesmos itens, mesmo `id`
(`l<id>`/`c<id>`, o que `extrato_ia.py` lê nas escritas), mesmo `pode`.

O modelo recebe cada item como `_para_ia`: dinheiro em float sob `valor` (o `ai_guard` o lê), o `tipo`
de receita/despesa (o interno sai `entrada`/`saída`, senão o saque em espécie seria lido como ganho), a categoria
pelo nome e, nos `l`, o `user_seq` (o `#N` que o usuário vê; é a única chave de id que o `ai_guard` reconhece).
Diferenças para a 1ª página de `/api/app/lancamentos`, declaradas: a janela vai até o mês seguinte (a
compra no cartão depois do fechamento), o guardado entra e o parcelamento vem em uma linha por data (a menor parcela:
o manual tem a mesma data em todas, o Open Finance uma por cobrança). A categoria vai como foi gravada (com acento e caixa), não como a chave do Extrato.

Cada lista é também um card do app (`ultimos_lancamentos`, `sem_categoria`): o bloco é o `Lancamento` da v2 (até
10 itens: o app mostra 5 e um "+5"), sem total; só sai pelo `mostrar_da_tool` (app que declara o card, com vaga).
O modelo recebe a lista inteira com `card_na_tela`: precisa dos `id` para "apaga o segundo".
"""
from __future__ import annotations

from typing import Any

from ai_router import ALLOWED_CATEGORIES
from db import lancamentos_leitura
from db.connection import cat_key
from db.estabelecimentos import SEM_DESCRICAO
from utils_date import now_tz

from .. import streaming
from ..cards import mostrar_da_tool

MAX_NO_CARD = 10

# O Extrato entrega a CHAVE da categoria (minúscula, sem acento); a conversa escreve o nome. As do catálogo
# voltam com o nome canônico; as outras, como foram gravadas (`categoria_nome`: "Café", "salário"), que é o que o
# usuário vê e o que o `resolve_category_input` reencontra no "recategoriza" sem criar uma gêmea.
_NOME = {cat_key(c): c for c in ALLOWED_CATEGORIES}


def descricao_do_item(i: dict) -> str:
    """O que o usuário vê como nome do item: a descrição, senão a nota, senão `SEM_DESCRICAO` (a compra sem nota e sem
    descrição do banco chega com os dois nulos). A lista da IA e o resumo da pendência dizem o mesmo."""
    return (i["descricao"] or i["mensagem"] or "").strip() or SEM_DESCRICAO


def _para_ia(i: dict) -> dict:
    tipo = ("entrada" if i["tipo"] == "entrada" else "saída") if i["interno"] else (
        "receita" if i["tipo"] == "entrada" else "despesa")
    return {"id": i["id"], **({"user_seq": i["numero"]} if i.get("numero") else {}), "data": i["data"], "tipo": tipo,
            "valor": float(i["valor"]), "moeda": i["moeda"], "descricao": descricao_do_item(i),
            "categoria": _NOME.get(i["categoria"]) or i["categoria_nome"] or i["categoria"], "origem": i["origem"], "guardado": i["guardado"],
            "parcela": i["parcela"], "fatura": i["fatura"], "pode": i["pode"]}


def mostrar_itens(user_id: int, card: str, itens: list[dict], motivos: list[str]) -> bool:
    """Os itens (como o Extrato os dá) no card `card`, até `MAX_NO_CARD`. False, sem emitir, se não há emissor, o app
    não declarou o card ou não há vaga (`mostrar_da_tool`)."""
    if not (itens and streaming.ativo()):
        return False
    from api.v2.lancamentos import Lancamento  # o contrato que o app lê; aqui para o WhatsApp não pagar o router

    bloco = [Lancamento(**i).model_dump(mode="json") for i in itens[:MAX_NO_CARD]]
    return mostrar_da_tool(user_id, card, {"itens": bloco, "motivos": motivos})


def _lista(user_id: int, args: dict[str, Any], sem_categoria: bool) -> dict[str, Any]:
    limit = max(1, min(int(args.get("limit") or 10), 50))
    d = lancamentos_leitura.recentes(user_id, now_tz(), limit, sem_categoria=sem_categoria)
    resultado = {"itens": [_para_ia(i) for i in d["itens"]], "motivos": d["motivos"]}
    if mostrar_itens(user_id, "sem_categoria" if sem_categoria else "ultimos_lancamentos", d["itens"], d["motivos"]):
        resultado["card_na_tela"] = True
    return resultado


def ultimos_lancamentos(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    return _lista(user_id, args, False)


def sem_categoria(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """`get_uncategorized_launches`: a mesma lista, só as saídas (cartão incluído) sem categoria ou em "outros"."""
    return _lista(user_id, args, True)
