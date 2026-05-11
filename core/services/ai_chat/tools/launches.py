"""
core/services/ai_chat/tools/launches.py — tools de lançamentos (despesas/receitas).

Read:
  - list_recent_launches: últimos N lançamentos do user (default 10)
  - get_period_summary: soma de despesas e receitas em um período (default: mês corrente)

Write (precisam de confirmação humana):
  - add_launch: registra despesa/receita NÃO-CARTÃO (saída/entrada da conta corrente)
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import db
from utils_date import _tz
from utils_text import fmt_brl, is_internal_category

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
        "saldo_periodo": float(summary.get("receita") or 0) - float(summary.get("despesa") or 0),
    }


# ─── Write: add_launch ──────────────────────────────────────────────────────

_TIPOS_VALIDOS = ("despesa", "receita")


def _parse_iso_datetime_for_launch(s: str | None) -> datetime | None:
    """YYYY-MM-DD → datetime ao meio-dia no tz local (evita edge cases com DST)."""
    if not s:
        return None
    try:
        d = date.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    return datetime.combine(d, time(12, 0), tzinfo=_tz())


def _add_launch_summary(args: dict[str, Any]) -> str:
    tipo = (args.get("tipo") or "").strip().lower()
    valor = args.get("valor")
    alvo = (args.get("alvo") or "").strip()
    categoria = (args.get("categoria") or "").strip()
    data_str = args.get("data")

    if not isinstance(valor, (int, float)) or tipo not in _TIPOS_VALIDOS:
        return "registrar um lançamento"

    valor_fmt = fmt_brl(float(valor))
    prep = "em" if tipo == "despesa" else "como"
    base = f"registrar {tipo} de {valor_fmt}"
    if alvo:
        base += f" {prep} {alvo}"
    if categoria:
        base += f" ({categoria})"
    if isinstance(data_str, str):
        try:
            d = date.fromisoformat(data_str)
            base += f" em {d.strftime('%d/%m/%Y')}"
        except ValueError:
            pass
    return base


def _add_launch_execute(user_id: int, args: dict[str, Any]) -> str:
    tipo = (args.get("tipo") or "").strip().lower()
    if tipo not in _TIPOS_VALIDOS:
        return "🐷 Tipo inválido — precisa ser despesa ou receita."

    try:
        valor = float(args.get("valor") or 0)
    except (TypeError, ValueError):
        return "🐷 Valor inválido."
    if valor <= 0:
        return "🐷 O valor precisa ser maior que zero."

    alvo = (args.get("alvo") or "").strip() or None
    nota_raw = (args.get("nota") or "").strip()
    nota = nota_raw or None
    categoria_explicit = (args.get("categoria") or "").strip() or None
    criado_em = _parse_iso_datetime_for_launch(args.get("data"))

    text_base = nota or alvo or ""

    from core.services.category_service import infer_category, learn_from_inference

    infer = infer_category(user_id, text_base, explicit_category=categoria_explicit)
    categoria = infer.category or "outros"
    is_internal = is_internal_category(categoria)

    try:
        launch_id, user_seq, new_balance = db.add_launch_and_update_balance(
            user_id=user_id,
            tipo=tipo,
            valor=valor,
            alvo=alvo,
            nota=nota or alvo,
            categoria=categoria,
            criado_em=criado_em,
            is_internal_movement=is_internal,
        )
    except Exception as e:
        return f"🐷 Não consegui registrar o lançamento: {e}"

    try:
        learn_from_inference(
            user_id,
            text_base,
            categoria,
            target_hint=alvo,
            reason=infer.reason,
        )
    except Exception:
        pass

    extra = ""
    if tipo == "despesa" and not is_internal and categoria:
        try:
            from core.budget_alerts import evaluate_after_expense, format_alert_text
            when = criado_em or datetime.now(_tz())
            alert = evaluate_after_expense(user_id, categoria, valor, when)
            if alert:
                extra = "\n" + format_alert_text(alert).lstrip()
        except Exception:
            pass

    emoji = "💸" if tipo == "despesa" else "💰"
    alvo_str = f" — {alvo}" if alvo else ""
    return (
        f"{emoji} {tipo.capitalize()} de {fmt_brl(valor)}{alvo_str} ({categoria}). "
        f"Saldo: {fmt_brl(float(new_balance))}. ID: #{user_seq}.{extra}"
    )


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
                "description": "Retorna o resumo financeiro de um período: total de receitas e despesas reais (NÃO inclui aportes de investimento — pra aportes, use `get_investment_contributions`). Use pra 'quanto gastei esse mês?', 'meu resumo de abril', 'quanto recebi em maio'. Sem datas, retorna o mês corrente até hoje.",
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
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "add_launch",
                "description": (
                    "Registra uma despesa ou receita NÃO-CARTÃO (saída/entrada "
                    "da conta corrente). Use pra 'gastei 50 no mercado', "
                    "'recebi 1000 de salário', 'paguei 80 de luz'. NÃO use pra "
                    "compras no cartão de crédito — isso é outra ferramenta. "
                    "Categoria é inferida automaticamente; só passe `categoria` "
                    "se o user disse explicitamente. ESCRITA — pede confirmação."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tipo": {
                            "type": "string",
                            "enum": ["despesa", "receita"],
                            "description": "despesa (saiu) ou receita (entrou).",
                        },
                        "valor": {
                            "type": "number",
                            "minimum": 0.01,
                            "description": "Valor em reais (ex: 50, 1234.56).",
                        },
                        "alvo": {
                            "type": "string",
                            "description": "Onde o dinheiro foi/veio. Ex: 'mercado', 'salário', 'uber'.",
                        },
                        "categoria": {
                            "type": "string",
                            "description": "Categoria explícita. Omita se o user não mencionou — o sistema infere.",
                        },
                        "nota": {
                            "type": "string",
                            "description": "Observação livre, opcional.",
                        },
                        "data": {
                            "type": "string",
                            "description": "Data do lançamento em ISO 8601 (YYYY-MM-DD). Omita pra usar hoje.",
                        },
                    },
                    "required": ["tipo", "valor"],
                },
            },
        },
        is_write=True,
        summary=_add_launch_summary,
        execute=_add_launch_execute,
    ),
]
