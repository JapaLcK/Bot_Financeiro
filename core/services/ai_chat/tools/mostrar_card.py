"""
core/services/ai_chat/tools/mostrar_card.py — `show_card`: mostra no app um dos 16
cards do painel, com os mesmos números do widget (`cards.py`).

Fica FORA de `SCHEMAS` (WhatsApp e /ai/chat não a oferecem): só `SCHEMAS_APP`, com
emissor. `get_tool` a resolve sempre, para o modelo que a imite no histórico
compartilhado: sem emissor devolve só os dados, sem bloco.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError

from .. import streaming
from .._context import CARDS_DO_TURNO
from ..cards import IDS, MAX_MESES, MAX_MESES_RENDA, MAX_POR_TURNO, CardIndisponivel, montar
from ._base import Tool

_MES = re.compile(r"^[1-9][0-9]{3}-(0[1-9]|1[0-2])$")  # = api.v2.resumo_mes.PADRAO_MES
_COM_MES = ("resumo", "categorias", "calendario", "simulador")


def _erro(codigo: str, msg: str) -> dict[str, Any]:
    return {"error": codigo, "message": msg}


def _show_card(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    card = args.get("card")
    if card not in IDS:
        return _erro("card_desconhecido", f"card deve ser um de: {', '.join(IDS)}")
    usados = CARDS_DO_TURNO.get()  # só o card que emitiu bloco gasta uma vaga
    if usados is not None and len(usados) >= MAX_POR_TURNO:
        return _erro("limite_de_cards", f"no máximo {MAX_POR_TURNO} cards por resposta")

    meses = args.get("meses") or []
    if (not isinstance(meses, list) or not all(isinstance(m, str) and _MES.match(m) for m in meses)
            or len(set(meses)) != len(meses)):
        return _erro("mes_invalido", "meses: lista de AAAA-MM sem repetição")
    if len(meses) > (MAX_MESES if card == "categorias" else 1):
        return _erro("meses_demais", f"{MAX_MESES} meses só em categorias; os outros cards, 1")
    dias = args.get("dias")
    if dias is not None and str(dias) not in ("30", "60", "90"):
        return _erro("dias_invalido", "dias: 30, 60 ou 90")
    qtd = args.get("quantidade_meses")
    if qtd is not None and (type(qtd) is not int or not 1 <= qtd <= MAX_MESES_RENDA):
        return _erro("quantidade_invalida", f"quantidade_meses: 1 a {MAX_MESES_RENDA}")

    try:
        bloco, visao = montar(card, user_id, {"meses": meses if card in _COM_MES else [],
                                              "dias": None if dias is None else str(dias),
                                              "quantidade_meses": qtd},
                                 previa=lambda: streaming.passo("show_card", False, card))
    except HTTPException as e:
        d = e.detail if isinstance(e.detail, dict) else {}
        if e.status_code == 402:  # conta sem plano ativo: o app a recusa também
            return _erro("sem_plano_ativo", "A conta não tem plano ativo. Oriente a escolher um plano "
                         "em pigbankai.com/precos, sem citar números.")
        if d.get("error") == "pro_required":
            return _erro("pro_required", "Esse card faz parte de um plano superior. Ofereça o upgrade com jeitinho, sem números.")
        return _erro(d.get("error") or f"http_{e.status_code}", "O card não está disponível para esta conta.")
    except CardIndisponivel as e:
        return _erro("indisponivel", f"{e}. Diga que ainda não estão prontas e siga sem o card.")
    except RequestValidationError:
        return _erro("mes_invalido", "Mês no futuro ou fora do formato.")
    if usados is not None:
        usados.append(card)
    streaming.emitir({"tipo": "bloco", "bloco": bloco})
    return visao


SCHEMA = {
    "type": "function",
    "function": {
        "name": "show_card",
        "description": (
            f"Mostra no app um card do painel com os números do usuário. Chame UMA vez por card, "
            f"no máximo {MAX_POR_TURNO} por resposta, e escreva os números a partir do resultado desta tool. "
            "Cards: contas (saldo por conta), hero (previsão de saldo), compromissos (próximos "
            "pagamentos), resumo (entrou/saiu do mês), categorias (gastos por categoria; aceita "
            "até 3 meses para comparar), calendario (gastos por dia), simulador (Pro), patrimonio, "
            "wealth (distribuição do patrimônio), rendimento (taxas contratadas), assinaturas, "
            "metas (caixinhas com meta), fatura (cartões e limite), parcelas, renda (evolução "
            "mensal), piggy (insights). Erro de plano: ofereça o upgrade, sem números."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "card": {"type": "string", "enum": list(IDS)},
                "meses": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_MESES,
                          "description": "AAAA-MM, só para resumo/categorias/calendario/simulador. Omita = mês atual."},
                "dias": {"type": "string", "enum": ["30", "60", "90"],
                         "description": "Horizonte, só para hero/compromissos."},
                "quantidade_meses": {"type": "integer", "minimum": 1, "maximum": MAX_MESES_RENDA,
                                     "description": "Só para renda. Padrão 6."},
            },
            "required": ["card"],
        },
    },
}


TOOLS: list[Tool] = [Tool(schema=SCHEMA, is_write=False, execute=_show_card)]
