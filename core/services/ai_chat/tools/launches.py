"""
core/services/ai_chat/tools/launches.py — tools de lançamentos (despesas/receitas).

Read:
  - list_recent_launches: últimos N lançamentos do user (default 10)
  - get_period_summary: soma de despesas e receitas em um período (default: mês corrente)
"""
from __future__ import annotations

from datetime import date
from typing import Any

import db

from ._base import Tool


def _list_recent_launches(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    limit = int(args.get("limit") or 10)
    limit = max(1, min(limit, 50))

    rows = db.list_launches(user_id, limit=limit)
    return {
        "launches": [
            {
                "id": r["id"],
                "user_seq": r.get("user_seq"),
                "tipo": r["tipo"],
                "valor": float(r["valor"] or 0),
                "alvo": r.get("alvo"),
                "nota": r.get("nota"),
                "categoria": r.get("categoria"),
                "criado_em": r["criado_em"].isoformat() if r.get("criado_em") else None,
            }
            for r in rows
        ]
    }


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _get_period_summary(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    today = date.today()
    start = _parse_iso_date(args.get("start_date")) or today.replace(day=1)
    end = _parse_iso_date(args.get("end_date")) or today

    if end < start:
        return {"error": "end_date anterior a start_date"}

    summary = db.get_summary_by_period(user_id, start, end)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "receita": float(summary.get("receita") or 0),
        "despesa": float(summary.get("despesa") or 0),
        "aporte_investimento": float(summary.get("aporte_investimento") or 0),
        "saldo_periodo": float(summary.get("receita") or 0) - float(summary.get("despesa") or 0),
    }


TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "list_recent_launches",
                "description": "Retorna os lançamentos (despesas e receitas) mais recentes do usuário, em ordem cronológica decrescente. Use pra perguntas tipo 'meus últimos gastos', 'o que registrei hoje', 'mostra os lançamentos'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "default": 10,
                            "description": "Quantos lançamentos retornar (1 a 50, padrão 10).",
                        },
                    },
                },
            },
        },
        is_write=False,
        execute=_list_recent_launches,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_period_summary",
                "description": "Retorna o resumo financeiro de um período: total de receitas, total de despesas, aportes de investimento e saldo do período (receita - despesa). Use pra 'quanto gastei esse mês?', 'meu resumo de abril', 'quanto recebi em maio'. Sem datas, retorna o mês corrente até hoje.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "start_date": {
                            "type": "string",
                            "description": "Data inicial em ISO 8601 (YYYY-MM-DD). Se omitido, usa o primeiro dia do mês corrente.",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "Data final em ISO 8601 (YYYY-MM-DD), inclusiva. Se omitida, usa a data de hoje.",
                        },
                    },
                },
            },
        },
        is_write=False,
        execute=_get_period_summary,
    ),
]
