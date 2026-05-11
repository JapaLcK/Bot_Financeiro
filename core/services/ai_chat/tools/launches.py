"""
core/services/ai_chat/tools/launches.py — tools de lançamentos (despesas/receitas).

Read:
  - list_recent_launches: últimos N lançamentos do user (default 10)
  - get_period_summary: soma de despesas e receitas em um período (default: mês corrente)

Write (auto-executado, SEM confirmação):
  - add_launch: IA extrai os args, delega pra `core.handlers.launches.add_from_entities`
    (a mesma fn que o bot tradicional usa) e devolve a resposta padrão.

Write (PEDE confirmação — destrutivo):
  - delete_launch: apaga um lançamento (despesa, receita ou compra no cartão).
    Bifurca entre `launches` (user_seq) e `credit_transactions` (id global).
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import db
from utils_date import _tz

from .._context import CURRENT_PLATFORM
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


def _add_launch_execute(user_id: int, args: dict[str, Any]) -> str:
    """IA já extraiu os args; aqui só valida e delega pra fonte única de
    verdade (`add_from_entities` no handler). Toda lógica de categorização,
    learn, budget alert e formato de resposta vive lá."""
    tipo = (args.get("tipo") or "").strip().lower()
    if tipo not in _TIPOS_VALIDOS:
        return "🐷 Tipo inválido — precisa ser despesa ou receita."

    try:
        valor = float(args.get("valor") or 0)
    except (TypeError, ValueError):
        return "🐷 Valor inválido."
    if valor <= 0:
        return "🐷 O valor precisa ser maior que zero."

    from core.handlers.launches import add_from_entities

    return add_from_entities(
        user_id,
        tipo=tipo,
        valor=valor,
        alvo=(args.get("alvo") or "").strip() or None,
        nota=(args.get("nota") or "").strip() or None,
        categoria=(args.get("categoria") or "").strip() or None,
        criado_em=_parse_iso_datetime_for_launch(args.get("data")),
        platform=CURRENT_PLATFORM.get(),
    )


# ─── Write: delete_launch (PEDE confirmação — destrutivo) ───────────────────

def _delete_launch_summary(args: dict[str, Any]) -> str:
    lid = args.get("launch_id")
    return f'apagar o lançamento #{lid}' if lid else "apagar lançamento"


def _delete_launch_validate(user_id: int, args: dict[str, Any]) -> str | None:
    """Roda ANTES da confirmação. Se o ID nem existe, mostra erro imediato
    em vez de pedir 'confirma apagar #X?' enganoso. Critico porque o LLM
    às vezes inventa IDs quando o user diz 'aquele' sem especificar."""
    try:
        lid = int(args.get("launch_id") or 0)
    except (TypeError, ValueError):
        return "🐷 ID inválido — me diz o número do lançamento (ex: #5)."
    if lid <= 0:
        return "🐷 Faltou o ID do lançamento."

    # Existe como user_seq de launches?
    if db.resolve_user_seq_to_id(user_id, lid):
        return None

    # Existe como id de credit_transaction?
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select 1 from credit_transactions where user_id=%s and id=%s",
                (user_id, lid),
            )
            if cur.fetchone():
                return None

    return (
        f"🐷 Não achei o lançamento #{lid}. "
        f"Manda o ID exato (aparece no histórico como #N) ou pede pra eu "
        f"listar os últimos lançamentos."
    )


def _delete_launch_execute(user_id: int, args: dict[str, Any]) -> str:
    """Apaga um lançamento. Tenta primeiro como user_seq de `launches`
    (cenário comum: user digita "#5"). Se não achar, tenta como id de
    `credit_transactions` — nesse caso, parcelamento derruba o grupo inteiro
    via `undo_credit_transaction`."""
    try:
        lid = int(args.get("launch_id") or 0)
    except (TypeError, ValueError):
        return "🐷 ID inválido — informe o número do lançamento (ex: #5)."
    if lid <= 0:
        return "🐷 Faltou o ID do lançamento."

    # 1. Lançamento normal (despesa/receita) — resolve user_seq → id interno
    internal_id = db.resolve_user_seq_to_id(user_id, lid)
    if internal_id:
        try:
            db.delete_launch_and_rollback(user_id, internal_id)
            return f"🗑️ Lançamento #{lid} apagado. Saldo revertido."
        except LookupError:
            return f"🐷 Não achei o lançamento #{lid}."
        except Exception as e:
            return f"🐷 Não consegui apagar: {e}"

    # 2. Compra no crédito — id global em credit_transactions
    try:
        result = db.undo_credit_transaction(user_id, lid)
    except Exception as e:
        return f"🐷 Não consegui apagar: {e}"
    if result is None:
        return f"🐷 Não achei o lançamento #{lid}."

    removed = int(result.get("removed_count") or 1)
    if removed > 1:
        return f"🗑️ Parcelamento apagado ({removed} parcelas)."
    return f"🗑️ Compra no crédito #{lid} apagada."


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
                    "Registra uma despesa ou receita NÃO-CARTÃO (sai/entra da "
                    "conta corrente). Use pra 'gastei 50 no mercado', 'recebi "
                    "1000 de salário', 'paguei 80 de luz'. NÃO use pra compras "
                    "no cartão de crédito (essa é outra ferramenta). Categoria "
                    "é inferida automaticamente — só passe `categoria` se o "
                    "user disse explicitamente. EXECUTA DIRETO (sem perguntar "
                    "'confirma?'); o user reverte depois se quiser."
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
        requires_confirmation=False,
        execute=_add_launch_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "delete_launch",
                "description": (
                    "Apaga um lançamento (despesa, receita ou compra no "
                    "cartão). Use pra 'apaga o gasto #5', 'remove o último', "
                    "'apaga aquela compra'. Reverte saldo automaticamente. "
                    "Se for parcela de parcelamento, derruba o grupo inteiro. "
                    "ESCRITA DESTRUTIVA — pede confirmação (não tem undo)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "launch_id": {
                            "type": "integer",
                            "description": "ID do lançamento (o #N que aparece no histórico, ex: 5, 142).",
                        },
                    },
                    "required": ["launch_id"],
                },
            },
        },
        is_write=True,
        requires_confirmation=True,
        summary=_delete_launch_summary,
        execute=_delete_launch_execute,
        validate=_delete_launch_validate,
    ),
]
