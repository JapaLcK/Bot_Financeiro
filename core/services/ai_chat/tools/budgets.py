"""
core/services/ai_chat/tools/budgets.py — tools de orçamento por categoria.

Read:
  - get_budget_status: orçamentos + gasto do mês + % usado. Sem args → todos;
    com `categoria` → só essa (fuzzy match incluso pra tolerar typo).

Write:
  - set_budget: cria OU atualiza orçamento. Comportamento misto:
      * categoria com typo (não match exato, tem similar) → BLOQUEIA com sugestão
      * categoria nova/limpa, sem orçamento existente → auto-execute
      * categoria com orçamento existente → pede confirmação (template 3)
    Pra contornar a checagem de typo, passe `force_new=true`.
  - delete_budget: remove orçamento — pede confirmação.

A checagem de typo é defensiva: orçamento órfão (typo que não casa com
nenhum lançamento) nunca dispararia alertas, ficaria silencioso no DB.
Melhor bloquear na hora e perguntar.
"""
from __future__ import annotations

import difflib
import unicodedata
from typing import Any

import db

from ._base import Tool


# ─── Helpers ────────────────────────────────────────────────────────────────


_FUZZY_CUTOFF = 0.75


def _norm(s: str) -> str:
    """Normaliza pra fuzzy match: lower, sem acentos, sem ç.

    A canônica continua com case/acento original — esse helper só serve
    pra comparação. Sem isso, "alimemtacao" vs "alimentação" pontuava 0.68
    (abaixo do cutoff). Removendo acentos: 0.91.
    """
    s = (s or "").lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s


def _resolve_category(
    user_id: int,
    raw: str,
    *,
    force_new: bool = False,
) -> tuple[str, str]:
    """Resolve categoria contra catálogo do user. Retorna (resultado, ação).

    ação ∈ {"ok", "block"}:
      - "ok": `resultado` é a categoria canônica pra usar.
      - "block": `resultado` é mensagem pro user (typo detectado / sem cat).

    Sequência:
      1. exact match (case-insensitive) com launches OU budget existente → canônica
      2. force_new=True → aceita como veio
      3. fuzzy similar (cutoff 0.75) → block com sugestão
      4. sem similar → aceita (categoria nova legítima)
    """
    cat = (raw or "").strip()
    if not cat:
        return ("🐷 Me diz qual categoria.", "block")

    user_cats = db.list_user_categories(user_id)
    existing_budgets = [b["categoria"] for b in db.list_budgets(user_id)]
    # `norm_map`: normalizada → canônica (com acento/case originais).
    # Budgets têm precedência sobre launches pra preservar a forma escolhida pelo user.
    norm_map: dict[str, str] = {}
    for c in user_cats:
        norm_map.setdefault(_norm(c), c)
    for b in existing_budgets:
        norm_map[_norm(b)] = b

    cat_norm = _norm(cat)
    if cat_norm in norm_map:
        return (norm_map[cat_norm], "ok")

    if force_new:
        return (cat, "ok")

    if norm_map:
        close = difflib.get_close_matches(
            cat_norm, list(norm_map.keys()), n=1, cutoff=_FUZZY_CUTOFF
        )
        if close:
            suggestion = norm_map[close[0]]
            msg = (
                f'🐷 Não achei "{cat}" nos teus lançamentos. '
                f'Você quis dizer *{suggestion}*? '
                f'Se sim, me confirma com a categoria certa. '
                f'Se é uma categoria nova mesmo, repete com `force_new=true`.'
            )
            return (msg, "block")

    return (cat, "ok")


def _status_label(pct: float) -> str:
    if pct >= 100:
        return "estourado"
    if pct >= 80:
        return "alerta"
    return "ok"


def _budget_row_with_spent(user_id: int, b: dict[str, Any]) -> dict[str, Any]:
    cat = b["categoria"]
    budget = float(b["budget"])
    spent = db.sum_spent_in_category_this_month(user_id, cat)
    pct = round(100 * spent / budget, 1) if budget > 0 else 0.0
    return {
        "categoria": cat,
        "budget": budget,
        "spent": spent,
        "remaining": max(0.0, budget - spent),
        "pct": pct,
        "status": _status_label(pct),
    }


# ─── Read: get_budget_status ────────────────────────────────────────────────


def _get_budget_status(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """Status de orçamento(s) com gasto do mês corrente.

    Sem `categoria` → lista todos. Com → só essa (faz fuzzy match leve).
    """
    cat_arg = (args.get("categoria") or "").strip()

    if cat_arg:
        resolved, action = _resolve_category(user_id, cat_arg)
        if action == "block":
            # No read, a "block" vira só uma dica — devolve estrutura vazia
            # com a mensagem, sem forçar a IA a re-chamar.
            return {"hint": resolved, "categoria": cat_arg, "found": False}
        b = db.get_budget(user_id, resolved)
        if not b:
            return {
                "categoria": resolved,
                "found": False,
                "hint": f'🐷 Não tem orçamento definido em "{resolved}".',
            }
        return {"found": True, **_budget_row_with_spent(user_id, b)}

    budgets = db.list_budgets(user_id)
    return {
        "budgets": [_budget_row_with_spent(user_id, b) for b in budgets],
        "count": len(budgets),
    }


# ─── Write: set_budget ──────────────────────────────────────────────────────


def _set_budget_execute(user_id: int, args: dict[str, Any]) -> str:
    """Cria ou atualiza orçamento.

    Fluxo:
      1. Resolve categoria. Se bloquear (typo), retorna msg pra IA repassar.
      2. Se orçamento JÁ EXISTE e args não tem `_confirmed`, salva pending
         action manualmente e retorna template 3 ("vou atualizar X pra Y?").
      3. Caso contrário, faz upsert e retorna template 4 ("✅ Salvo.").
    """
    try:
        budget = float(args.get("budget") or 0)
    except (TypeError, ValueError):
        return "🐷 Valor do orçamento inválido."
    if budget <= 0:
        return "🐷 O orçamento precisa ser maior que zero."

    force_new = bool(args.get("force_new"))
    confirmed = bool(args.get("_confirmed"))

    raw_cat = args.get("categoria") or ""
    if confirmed:
        # Categoria já foi resolvida no 1º turno; usa como veio.
        canon = (raw_cat or "").strip()
    else:
        canon, action = _resolve_category(user_id, raw_cat, force_new=force_new)
        if action == "block":
            return canon  # mensagem pro user

    existing = db.get_budget(user_id, canon)
    if existing and not confirmed:
        # UPDATE — vira pending action manual.
        summary = (
            f"atualizar orçamento de {canon} de R$ {existing['budget']:.2f} "
            f"pra R$ {budget:.2f}"
        )
        pending_args = {
            "categoria": canon,
            "budget": budget,
            "_confirmed": True,
        }
        db.ai_set_pending_action(user_id, "set_budget", pending_args, summary)
        return (
            f"🐷 Já tem orçamento de R$ {existing['budget']:.2f} em *{canon}*.\n\n"
            f"Atualizar pra R$ {budget:.2f}?\n\n"
            f"Confirma com *sim* ou cancela com *não*."
        )

    try:
        saved_cat, created = db.upsert_budget(user_id, canon, budget)
    except ValueError as e:
        return f"🐷 Não consegui salvar: {e}"

    if created:
        return (
            f"✅ Orçamento de R$ {budget:.2f} em *{saved_cat}* criado.\n"
            f"Vou te avisar quando você passar de 80%, 100% e 120% do limite no mês."
        )
    return f"✅ Orçamento de *{saved_cat}* atualizado pra R$ {budget:.2f}."


# ─── Write: delete_budget ───────────────────────────────────────────────────


def _normalize_cat_list(args: dict[str, Any]) -> list[str]:
    """Aceita `categorias` (lista) ou `categoria` (string) como input.

    O LLM às vezes passa singular mesmo quando o user pediu múltiplas. Por
    isso a tool aceita os 2 e normaliza pra lista. Pra apagar várias de uma
    vez (1 confirma só), passar `categorias=["x","y"]`.
    """
    cats = args.get("categorias")
    if cats is None:
        single = args.get("categoria")
        cats = [single] if single else []
    elif isinstance(cats, str):
        cats = [cats]
    return [c.strip() for c in cats if isinstance(c, str) and c.strip()]


def _delete_budget_summary(args: dict[str, Any]) -> str:
    cats = _normalize_cat_list(args)
    if not cats:
        return "apagar orçamento (sem categoria)"
    if len(cats) == 1:
        return f"apagar o orçamento de {cats[0]}"
    return f"apagar os orçamentos: {', '.join(cats)}"


def _delete_budget_validate(user_id: int, args: dict[str, Any]) -> str | None:
    cats = _normalize_cat_list(args)
    if not cats:
        return "🐷 Me diz qual orçamento você quer apagar."

    resolved: list[str] = []
    nao_existem: list[str] = []
    for c in cats:
        canon, action = _resolve_category(user_id, c)
        if action == "block":
            return canon
        if not db.get_budget(user_id, canon):
            nao_existem.append(canon)
            continue
        resolved.append(canon)

    if nao_existem and not resolved:
        if len(nao_existem) == 1:
            return f'🐷 Você não tem orçamento em "{nao_existem[0]}" pra apagar.'
        joined = ", ".join(f'"{c}"' for c in nao_existem)
        return f"🐷 Você não tem orçamento em {joined} pra apagar."
    if nao_existem:
        # Misto: filtra os inexistentes e segue só com os que existem.
        joined = ", ".join(f'"{c}"' for c in nao_existem)
        # Não retorna erro — só normaliza args e segue. Mensagem com aviso
        # é construída no execute pra não bloquear o flow.
        args["_skipped"] = nao_existem

    args["categorias"] = resolved
    args.pop("categoria", None)
    return None


def _delete_budget_execute(user_id: int, args: dict[str, Any]) -> str:
    cats = _normalize_cat_list(args)
    deleted: list[str] = []
    for c in cats:
        if db.delete_budget(user_id, c):
            deleted.append(c)

    if not deleted:
        return "🐷 Não consegui apagar — nenhum orçamento encontrado."

    if len(deleted) == 1:
        msg = f"✅ Orçamento de *{deleted[0]}* apagado."
    else:
        joined = ", ".join(f"*{c}*" for c in deleted)
        msg = f"✅ Orçamentos apagados: {joined}."

    skipped = args.get("_skipped") or []
    if skipped:
        joined = ", ".join(f'"{c}"' for c in skipped)
        msg += f"\n(Não tinha orçamento em {joined} — pulei.)"
    return msg


# ─── Tools registry ─────────────────────────────────────────────────────────


TOOLS: list[Tool] = [
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "get_budget_status",
                "description": (
                    "Retorna status de orçamento(s) com gasto do mês corrente "
                    "e % usado. Sem `categoria`, lista TODOS os orçamentos. "
                    "Com `categoria`, retorna só esse. Use pra 'meus orçamentos', "
                    "'como tá meu orçamento de alimentação?', 'já passei do "
                    "limite?'. Cada item tem: categoria, budget, spent, "
                    "remaining, pct, status (ok/alerta/estourado)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "categoria": {
                            "type": "string",
                            "description": "Nome da categoria. Omita pra listar todas.",
                        },
                    },
                },
            },
        },
        is_write=False,
        execute=_get_budget_status,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "set_budget",
                "description": (
                    "Cria ou atualiza um orçamento mensal pra uma categoria. "
                    "Use pra 'define R$ 800 de orçamento em alimentação', "
                    "'quero gastar no máximo R$ 200 com lazer', 'orçamento "
                    "de transporte 300'. Comportamento: se a categoria não "
                    "casa com nenhuma que você já usou nos lançamentos, "
                    "BLOQUEIA e sugere a próxima parecida (anti-typo). Pra "
                    "criar uma categoria nova mesmo (ex: 'viagem' antes da "
                    "primeira viagem), passe `force_new=true`. Se já existe "
                    "orçamento nessa categoria, pede confirmação antes de "
                    "sobrescrever."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "categoria": {
                            "type": "string",
                            "description": "Categoria do orçamento. Ex: 'alimentação', 'lazer'.",
                        },
                        "budget": {
                            "type": "number",
                            "minimum": 0.01,
                            "description": "Valor mensal do orçamento em reais. Ex: 500, 1200.",
                        },
                        "force_new": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "Pula a validação anti-typo. Use quando o user "
                                "explicitamente quer criar orçamento pra categoria "
                                "nova que ainda não apareceu nos lançamentos."
                            ),
                        },
                    },
                    "required": ["categoria", "budget"],
                },
            },
        },
        is_write=True,
        requires_confirmation=False,  # gerenciado manualmente dentro do execute
        execute=_set_budget_execute,
    ),
    Tool(
        schema={
            "type": "function",
            "function": {
                "name": "delete_budget",
                "description": (
                    "Remove o orçamento de uma OU MAIS categorias. Use pra "
                    "'apaga orçamento de alimentação', 'remove orçamento de "
                    "lazer e transporte', 'apaga todos meus orçamentos de X "
                    "e Y'. DESTRUTIVO — pede uma única confirmação cobrindo "
                    "TODAS as categorias passadas. Pra apagar várias de uma "
                    "vez, passe lista em `categorias`."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "categorias": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Lista de categorias a remover. Aceita 1 ou mais.",
                        },
                    },
                    "required": ["categorias"],
                },
            },
        },
        is_write=True,
        requires_confirmation=True,
        summary=_delete_budget_summary,
        validate=_delete_budget_validate,
        execute=_delete_budget_execute,
    ),
]


__all__ = ["TOOLS"]
