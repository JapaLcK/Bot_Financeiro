"""
core/services/ai_chat/tools/metas.py — `list_metas`: as metas e a reserva de emergência do /painel.

Meta é objetivo sobre o dinheiro do BANCO (Open Finance); não guarda dinheiro e não é caixinha
(`pockets.py`, o cofrinho antigo). Só quem tem a chave do /painel novo (`metas_ligadas`, P1-b)
lê e escreve metas (`metas_escrita.py`): para os demais NADA daqui chega ao modelo, nem o schema (`schemas_com_metas`), e
o prompt, o `list_pockets` e o `show_card` ficam como eram, com as caixinhas. O texto sai de
`metas_conversa` (o mesmo do card `metas` do app). `meta.py` é telemetria: outra coisa.
"""
from __future__ import annotations

from typing import Any

from core.services import metas_conversa as mc

from .._context import metas_do_turno
from . import metas_escrita
from ._base import Tool


def _list_metas(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    # `user_id` vem do runner, nunca de `args`.
    if not metas_do_turno(user_id):  # só se o modelo imitar o histórico: o schema nem é oferecido
        return {"use_caixinhas": True, "instrucao": "chame list_pockets e chame-as de caixinhas"}
    return {**mc.para_conversa(mc.ler(user_id)), "onde_criar": mc.ONDE_CRIAR}


# Só com a chave: o `list_pockets` deixa de citar meta e reserva (a rota delas é `list_metas`).
_QUEM_E_META = ", 'já bati a meta?', 'quanto falta pra minha reserva?', 'tô no ritmo?'."


def schemas_com_metas(schemas: list[dict[str, Any]], app: bool) -> list[dict[str, Any]]:
    """`schemas` (os de antes das metas) como o modelo os vê para quem tem a chave. No app o
    número vem do card (`list_metas` é oculta lá), então não entra."""
    from . import mostrar_card, pockets
    out = []
    for s in schemas:
        f = s["function"]
        if f["name"] == "show_card":
            s = mostrar_card.SCHEMA_METAS
        elif f["name"] == "list_pockets":
            assert _QUEM_E_META in f["description"]
            ler = "show_card (card metas)" if app else "list_metas"  # no app `list_metas` é oculta
            s = {**s, "function": {**f, "description": f["description"].replace(
                _QUEM_E_META, f". Metas e reserva de emergência NÃO são caixinhas: use {ler}.")}}
        out.append(s)
    # criar/editar/apagar vão ao WhatsApp e ao app (o nome da meta vem do card, no app)
    return [*out, *(() if app else (TOOLS[0].schema,)), *(t.schema for t in metas_escrita.TOOLS)]


TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "list_metas",
                "description": "Lista as METAS (objetivos sobre o dinheiro do banco: viagem, apê, curso) e a RESERVA DE EMERGÊNCIA, com `resumo` pronto (quanto tem, alvo, quanto falta, quanto guardar por mês até o prazo, ritmo e quando chega) e `avisos`. Use pra 'como estão minhas metas?', 'minha meta de X', 'quanto falta pra minha reserva?', 'reserva de emergência', 'tô no ritmo da meta?', 'quando chego na meta?'. Meta NÃO é caixinha (cofrinho): pra caixinhas use list_pockets. Não calcule ritmo nem prazo: use `resumo` e `avisos` como vieram.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_list_metas,
    ),
    *metas_escrita.TOOLS,
]
