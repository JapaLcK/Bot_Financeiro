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


def _try_int(s: str) -> int | None:
    try:
        n = int(s)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _delete_launch_validate(user_id: int, args: dict[str, Any]) -> str | None:
    """Roda ANTES da confirmação. Se o ID nem existe, mostra erro imediato
    em vez de pedir 'confirma apagar #X?' enganoso. Crítico porque o LLM
    às vezes inventa IDs quando o user diz 'aquele' sem especificar.

    Aceita 3 formas:
      1. user_seq de launches (#5, #142) — número pequeno
      2. id de credit_transaction (numérico, mais alto)
      3. código de parcelamento (PCxxxxxxxx, ex: PC81524273)
    """
    raw_id = str(args.get("launch_id") or "").strip()
    if not raw_id:
        return "🐷 Faltou o ID do lançamento."

    lid = _try_int(raw_id)
    if lid is not None:
        # user_seq de launches?
        if db.resolve_user_seq_to_id(user_id, lid):
            return None
        # id de credit_transaction?
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select 1 from credit_transactions where user_id=%s and id=%s",
                    (user_id, lid),
                )
                if cur.fetchone():
                    return None

    # Código de parcelamento (PCxxxxxxxx ou só os hex do início do group_id)
    if db.resolve_installment_group_id(user_id, raw_id):
        return None

    return (
        f"🐷 Não achei o lançamento '{raw_id}'. "
        f"Manda o ID exato (aparece no histórico como #N, ou o código do "
        f"parcelamento tipo PCxxxxxxxx) ou pede pra eu listar os últimos lançamentos."
    )


def _delete_launch_execute(user_id: int, args: dict[str, Any]) -> str:
    """Apaga um lançamento. Tenta na ordem:
      1. user_seq de `launches` (cenário comum: "#5") → delete_launch_and_rollback
      2. id global de credit_transactions → undo_credit_transaction (auto desfaz
         grupo inteiro se for parcelado)
      3. código de parcelamento (PCxxxxxxxx) → undo_installment_group
    """
    raw_id = str(args.get("launch_id") or "").strip()
    if not raw_id:
        return "🐷 Faltou o ID do lançamento."

    lid = _try_int(raw_id)
    if lid is not None:
        # 1. user_seq de launches
        internal_id = db.resolve_user_seq_to_id(user_id, lid)
        if internal_id:
            try:
                db.delete_launch_and_rollback(user_id, internal_id)
                return f"🗑️ Lançamento #{lid} apagado. Saldo revertido."
            except LookupError:
                return f"🐷 Não achei o lançamento #{lid}."
            except Exception as e:
                return f"🐷 Não consegui apagar: {e}"

        # 2. id de credit_transaction
        try:
            result = db.undo_credit_transaction(user_id, lid)
        except Exception as e:
            return f"🐷 Não consegui apagar: {e}"
        if result is not None:
            removed = int(result.get("removed_count") or 1)
            if removed > 1:
                return f"🗑️ Parcelamento apagado ({removed} parcelas)."
            return f"🗑️ Compra no crédito #{lid} apagada."

    # 3. Código de parcelamento (PCxxxxxxxx)
    group_id = db.resolve_installment_group_id(user_id, raw_id)
    if group_id:
        try:
            result = db.undo_installment_group(user_id, group_id)
        except Exception as e:
            return f"🐷 Não consegui apagar: {e}"
        if result:
            removed = int(result.get("removed_count") or 0)
            return f"🗑️ Parcelamento {raw_id.upper()} apagado ({removed} parcelas)."

    return f"🐷 Não achei o lançamento '{raw_id}'."


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
                            "type": "string",
                            "description": (
                                "ID exatamente como aparece no histórico. "
                                "Aceita: '#5' (user_seq de lançamento normal), "
                                "número grande (id de compra no cartão), OU "
                                "'PCxxxxxxxx' (código de parcelamento, ex: "
                                "PC81524273). Use sempre o que o user disse "
                                "literalmente, NUNCA invente."
                            ),
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
