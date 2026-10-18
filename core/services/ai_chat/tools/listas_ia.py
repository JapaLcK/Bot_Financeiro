"""
core/services/ai_chat/tools/listas_ia.py — as listas de lançamento da IA (`list_recent_launches`), lidas
pelo MESMO `listar` do Extrato (`db/lancamentos_leitura.recentes`): mesmos itens, mesmo `id`
(`l<id>`/`c<id>`, o que `extrato_ia.py` lê nas escritas), mesmo `pode`.

O modelo recebe cada item como `_para_ia`: dinheiro em float sob `valor` (o `ai_guard` o lê), o `tipo`
de receita/despesa (o interno sai `entrada`/`saída`, senão o saque em espécie seria lido como ganho), a categoria
pelo nome e, nos `l`, o `user_seq` (o `#N` que o usuário vê; é a única chave de id que o `ai_guard` reconhece).
Diferenças para a 1ª página de `/api/app/lancamentos`, declaradas: a janela vai até o mês seguinte (a
compra no cartão depois do fechamento) e o guardado entra.
"""
from __future__ import annotations

from typing import Any

from ai_router import ALLOWED_CATEGORIES
from db import lancamentos_leitura
from db.connection import cat_key
from utils_date import now_tz

# O Extrato entrega a CHAVE da categoria (minúscula, sem acento); a conversa escreve o nome. As do catálogo
# voltam com o nome; a personalizada fica como a chave (ponytail: ler `user_categories` se isso incomodar).
_NOME = {cat_key(c): c for c in ALLOWED_CATEGORIES}

def _para_ia(i: dict) -> dict:
    tipo = ("entrada" if i["tipo"] == "entrada" else "saída") if i["interno"] else (
        "receita" if i["tipo"] == "entrada" else "despesa")
    return {"id": i["id"], **({"user_seq": i["numero"]} if i.get("numero") else {}), "data": i["data"], "tipo": tipo,
            "valor": float(i["valor"]), "moeda": i["moeda"], "descricao": i["descricao"] or i["mensagem"],
            "categoria": _NOME.get(i["categoria"], i["categoria"]), "origem": i["origem"], "guardado": i["guardado"],
            "parcela": i["parcela"], "fatura": i["fatura"], "pode": i["pode"]}


def ultimos_lancamentos(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    limit = max(1, min(int(args.get("limit") or 10), 50))
    d = lancamentos_leitura.recentes(user_id, now_tz(), limit)
    return {"itens": [_para_ia(i) for i in d["itens"]], "motivos": d["motivos"]}
