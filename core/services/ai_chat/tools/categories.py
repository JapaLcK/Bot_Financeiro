"""
core/services/ai_chat/tools/categories.py — tools de categorização.

Read:
  - list_categories: lista as categorias permitidas pelo PigBank
  - list_user_rules: regras keyword → categoria do user
  - get_uncategorized_launches: lançamentos sem categoria útil (pra sugerir regras)

Write (precisam de confirmação humana):
  - create_category_rule: cria/atualiza regra
  - delete_category_rule: apaga regra
  - recategorize_launch: muda categoria de um lançamento existente
"""
from __future__ import annotations

from typing import Any

import db
from ai_router import ALLOWED_CATEGORIES

from ._base import Tool


# ─── Read handlers ──────────────────────────────────────────────────────────

def _list_categories(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    return {"categories": list(ALLOWED_CATEGORIES)}


def _list_user_rules(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    rules = db.list_user_category_rules(user_id)
    return {"rules": [{"keyword": k, "category": c} for k, c in rules]}


def _get_uncategorized_launches(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    limit = int(args.get("limit") or 10)
    launches = db.get_uncategorized_launches(user_id, limit=limit)
    return {
        "launches": [
            {
                "id": l["id"],
                "valor": l["valor"],
                "alvo": l.get("alvo"),
                "nota": l.get("nota"),
                "categoria": l.get("categoria"),
            }
            for l in launches
        ]
    }


# ─── Write: create_category_rule ────────────────────────────────────────────

def _create_category_rule_summary(args: dict[str, Any]) -> str:
    return f'criar regra "{args.get("keyword")}" → {args.get("category")}'


def _create_category_rule_execute(user_id: int, args: dict[str, Any]) -> str:
    keyword = (args.get("keyword") or "").strip()
    category = (args.get("category") or "").strip()
    if not keyword or not category:
        return "🐷 Faltou algum dado pra criar a regra. Manda de novo."
    db.upsert_category_rule(user_id, keyword, category)
    return f'✅ Regra criada: "{keyword}" → {category}.'


# ─── Write: delete_category_rule ────────────────────────────────────────────

def _delete_category_rule_summary(args: dict[str, Any]) -> str:
    return f'apagar a regra "{args.get("keyword")}"'


def _delete_category_rule_execute(user_id: int, args: dict[str, Any]) -> str:
    keyword = (args.get("keyword") or "").strip()
    n = db.delete_category_rule(user_id, keyword)
    if n > 0:
        return f'✅ Regra "{keyword}" apagada.'
    return f'🐷 Não achei a regra "{keyword}".'


# ─── Write: recategorize_launch ─────────────────────────────────────────────

def _recategorize_launch_summary(args: dict[str, Any]) -> str:
    return (
        f'mudar a categoria do lançamento #{args.get("launch_id")} '
        f'para "{args.get("new_category")}"'
    )


def _recategorize_launch_execute(user_id: int, args: dict[str, Any]) -> str:
    from db.accounts import update_launch_fields
    launch_id = int(args.get("launch_id") or 0)
    new_category = (args.get("new_category") or "").strip()
    ok = update_launch_fields(user_id, launch_id, categoria=new_category)
    if ok:
        return f"✅ Lançamento #{launch_id} agora é {new_category}."
    return f"🐷 Não consegui atualizar o lançamento #{launch_id}."


# ─── Tools registry ─────────────────────────────────────────────────────────

TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "list_categories",
                "description": "Lista todas as categorias de despesa permitidas no PigBank. Use quando o user perguntar quais categorias existem ou quando precisar validar uma categoria antes de criar regra.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_list_categories,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "list_user_rules",
                "description": "Lista as regras de categorização do usuário (keyword → categoria). Use quando ele perguntar 'quais regras tenho?' ou antes de modificar/apagar regra.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        is_write=False,
        execute=_list_user_rules,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_uncategorized_launches",
                "description": "Retorna os lançamentos recentes do usuário que estão sem categoria útil (em 'outros' ou null). Útil pra sugerir criar regras.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    },
                },
            },
        },
        is_write=False,
        execute=_get_uncategorized_launches,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "create_category_rule",
                "description": "Cria (ou atualiza) uma regra de categorização: toda vez que aparecer KEYWORD num lançamento, categoria vira CATEGORY. Esta é uma ação de ESCRITA — o sistema vai pedir confirmação do usuário antes de salvar.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "Palavra-chave (ex: 'uber', 'ifood'). Será comparada em minúsculas."},
                        "category": {"type": "string", "description": "Categoria. Deve ser uma das permitidas (use list_categories pra ver opções)."},
                    },
                    "required": ["keyword", "category"],
                },
            },
        },
        is_write=True,
        summary=_create_category_rule_summary,
        execute=_create_category_rule_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "delete_category_rule",
                "description": "Apaga uma regra de categorização pelo keyword. Ação de ESCRITA — pede confirmação.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string"},
                    },
                    "required": ["keyword"],
                },
            },
        },
        is_write=True,
        summary=_delete_category_rule_summary,
        execute=_delete_category_rule_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "recategorize_launch",
                "description": "Muda a categoria de um lançamento específico. Ação de ESCRITA — pede confirmação. Use o launch_id retornado por get_uncategorized_launches.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "launch_id": {"type": "integer"},
                        "new_category": {"type": "string"},
                    },
                    "required": ["launch_id", "new_category"],
                },
            },
        },
        is_write=True,
        summary=_recategorize_launch_summary,
        execute=_recategorize_launch_execute,
    ),
]
