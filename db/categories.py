"""
db/categories.py — Regras de categorização automática + metadata visual (Sprint 3).

Duas tabelas distintas:
- `user_category_rules`: keyword → category (memorização automática). Funções
  abaixo de `list_category_rules`, `add_category_rule` etc.
- `user_categories`: nome + emoji + cor por usuário (metadata visual da
  Sprint 3). Não tem FK em launches — `launches.categoria` continua string
  livre. Rename emite UPDATE em cascata nas 5 tabelas que referenciam o
  texto da categoria.
"""
from .connection import get_conn
from .users import ensure_user
from utils_text import normalize_text


# ─── Seed das 14 categorias canônicas (Sprint 3) ─────────────────────────────
# Mesma lista de `ai_router.py:ALLOWED_CATEGORIES`. Emoji/cor escolhidos pra
# bater com a paleta do dashboard (gradient roxo→azul) e contraste visual.
SYSTEM_CATEGORIES_SEED: list[tuple[str, str, str]] = [
    ("alimentação",         "🍔", "#f59e0b"),
    ("transporte",          "🚗", "#3b82f6"),
    ("saúde",               "💊", "#ec4899"),
    ("moradia",             "🏠", "#8b5cf6"),
    ("lazer",               "🎬", "#10b981"),
    ("educação",            "📚", "#06b6d4"),
    ("assinaturas",         "📺", "#6366f1"),
    ("pets",                "🐾", "#f97316"),
    ("compras online",      "🛒", "#a855f7"),
    ("beleza",              "💄", "#f43f5e"),
    ("investimento_aporte", "📈", "#22c55e"),
    ("criptomoedas",        "₿",  "#eab308"),
    ("rendimentos",         "💰", "#14b8a6"),
    ("outros",              "🏷️", "#64748b"),
]


def list_category_rules(user_id: int) -> list[tuple[str, str]]:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select keyword, category from user_category_rules "
                "where user_id=%s order by length(keyword) desc",
                (user_id,),
            )
            rows = cur.fetchall()
    return [(r["keyword"], r["category"]) for r in rows]


def add_category_rule(user_id: int, keyword: str, category: str) -> None:
    ensure_user(user_id)
    keyword = (keyword or "").strip()
    category = (category or "").strip()
    if not keyword:
        raise ValueError("keyword vazio")
    if not category:
        raise ValueError("category vazia")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_category_rules (user_id, keyword, category) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, keyword) DO UPDATE SET category = EXCLUDED.category",
                (user_id, keyword, category),
            )
        conn.commit()


def delete_category_rule(user_id: int, keyword: str) -> int:
    ensure_user(user_id)
    keyword = (keyword or "").strip()
    if not keyword:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_category_rules WHERE user_id=%s AND keyword=%s",
                (user_id, keyword),
            )
            n = cur.rowcount
        conn.commit()
    return n


def delete_category_rules_by_category(user_id: int, category: str) -> int:
    ensure_user(user_id)
    category = (category or "").strip()
    if not category:
        return 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM user_category_rules WHERE user_id=%s AND lower(category)=lower(%s)",
                (user_id, category),
            )
            n = cur.rowcount
        conn.commit()
    return n


def list_categories(user_id: int) -> list[str]:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT category FROM user_category_rules "
                "WHERE user_id=%s ORDER BY category",
                (user_id,),
            )
            rows = cur.fetchall()
    return [r["category"] if isinstance(r, dict) else r[0] for r in rows]


def get_memorized_category(user_id: int, memo: str) -> str | None:
    """
    Retorna categoria memorizada se alguma keyword bater com o texto.
    """
    from utils_text import normalize_text, contains_word  # import local pra evitar loop circular

    ensure_user(user_id)
    memo_norm = normalize_text(memo or "")
    if not memo_norm:
        return None

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT keyword, category FROM user_category_rules "
                "WHERE user_id = %s ORDER BY LENGTH(keyword) DESC",
                (user_id,),
            )
            rows = cur.fetchall()

    for r in rows:
        keyword = r.get("keyword") if isinstance(r, dict) else r[0]
        category = r.get("category") if isinstance(r, dict) else r[1]
        kw_norm = normalize_text(keyword or "")
        if not kw_norm:
            continue
        if contains_word(memo_norm, kw_norm) or (kw_norm in memo_norm):
            return (category or "").strip() or None

    return None


def upsert_category_rule(user_id: int, keyword: str, category: str) -> None:
    keyword = (keyword or "").strip().lower()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO user_category_rules (user_id, keyword, category) VALUES (%s, %s, %s) "
                "ON CONFLICT (user_id, keyword) DO UPDATE SET category = EXCLUDED.category",
                (user_id, keyword, category),
            )
        conn.commit()


def list_user_category_rules(user_id: int) -> list[tuple[str, str]]:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT keyword, category FROM user_category_rules "
                "WHERE user_id = %s ORDER BY LENGTH(keyword) DESC",
                (user_id,),
            )
            rows = cur.fetchall() or []

    out: list[tuple[str, str]] = []
    for r in rows:
        if isinstance(r, dict):
            out.append((r.get("keyword") or "", r.get("category") or ""))
        else:
            out.append((r[0] or "", r[1] or ""))
    return out


def get_uncategorized_launches(user_id: int, limit: int = 20) -> list[dict]:
    """
    Lançamentos em 'outros' ou sem categoria — candidatos a virar regra.
    Útil pra IA sugerir criar regras de categorização.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, tipo, valor, alvo, nota, categoria, criado_em
                from launches
                where user_id = %s
                  and tipo = 'despesa'
                  and is_internal_movement = false
                  and (categoria is null or lower(categoria) = 'outros')
                order by criado_em desc
                limit %s
                """,
                (user_id, int(limit)),
            )
            rows = cur.fetchall() or []

    out = []
    for r in rows:
        if isinstance(r, dict):
            out.append({
                "id": r.get("id"),
                "tipo": r.get("tipo"),
                "valor": float(r.get("valor") or 0),
                "alvo": r.get("alvo"),
                "nota": r.get("nota"),
                "categoria": r.get("categoria"),
                "criado_em": r.get("criado_em"),
            })
        else:
            out.append({
                "id": r[0], "tipo": r[1], "valor": float(r[2] or 0),
                "alvo": r[3], "nota": r[4], "categoria": r[5], "criado_em": r[6],
            })
    return out


# ─── user_categories (metadata visual — Sprint 3) ───────────────────────────


def _normalize_category_name(name: str) -> str:
    """Normaliza nome de categoria pro storage (lowercase, trim, espaços únicos)."""
    return " ".join((name or "").lower().strip().split())


def ensure_user_categories_seeded(user_id: int) -> None:
    """Seed lazy: na primeira chamada, popula as 14 canônicas com is_system=true.

    Idempotente: se já existe qualquer row com is_system=true pro user, sai.
    Adicional: importa categorias customizadas que o user JÁ TEM em launches
    (lower distinto) — assim a tela começa povoada com a realidade dele.
    """
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select 1 from user_categories where user_id=%s and is_system=true limit 1",
                (user_id,),
            )
            if cur.fetchone():
                return

            for name, emoji, color in SYSTEM_CATEGORIES_SEED:
                cur.execute(
                    """
                    insert into user_categories (user_id, name, emoji, color, is_system)
                    values (%s, %s, %s, %s, true)
                    on conflict (user_id, name) do nothing
                    """,
                    (user_id, name, emoji, color),
                )

            # Importa categorias customizadas já presentes em launches.
            cur.execute(
                """
                insert into user_categories (user_id, name, emoji, color, is_system)
                select %s, lower(trim(categoria)), '🏷️', '#7c3aed', false
                from (
                    select distinct categoria from launches
                    where user_id=%s and categoria is not null
                    union
                    select distinct categoria from credit_transactions
                    where user_id=%s and categoria is not null
                ) src
                where trim(coalesce(categoria,'')) <> ''
                on conflict (user_id, name) do nothing
                """,
                (user_id, user_id, user_id),
            )
        conn.commit()


def list_user_categories_full(
    user_id: int, include_archived: bool = True
) -> list[dict]:
    """Lista categorias da tabela `user_categories` com contagem de uso.

    Retorna dicts com: id, name, emoji, color, is_archived, is_system,
    usage_count (qtd de launches+credit_transactions com esse texto).
    Ordenação: não arquivadas primeiro, depois alfabético.
    """
    ensure_user(user_id)
    ensure_user_categories_seeded(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                with usage as (
                  select lower(categoria) as name, count(*) as n
                  from launches
                  where user_id=%s and categoria is not null
                  group by lower(categoria)
                  union all
                  select lower(categoria) as name, count(*) as n
                  from credit_transactions
                  where user_id=%s and categoria is not null
                  group by lower(categoria)
                )
                select
                  uc.id, uc.name, uc.emoji, uc.color,
                  uc.is_archived, uc.is_system,
                  coalesce((select sum(n) from usage where usage.name = uc.name), 0)::int as usage_count
                from user_categories uc
                where uc.user_id=%s
                  and (%s::boolean = true or uc.is_archived = false)
                order by uc.is_archived asc, uc.name asc
                """,
                (user_id, user_id, user_id, include_archived),
            )
            rows = cur.fetchall() or []
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "name": r["name"],
            "emoji": r["emoji"],
            "color": r["color"],
            "is_archived": bool(r["is_archived"]),
            "is_system": bool(r["is_system"]),
            "usage_count": int(r["usage_count"] or 0),
        })
    return out


def get_user_category(user_id: int, cat_id: int) -> dict | None:
    ensure_user(user_id)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, name, emoji, color, is_archived, is_system "
                "from user_categories where user_id=%s and id=%s",
                (user_id, int(cat_id)),
            )
            r = cur.fetchone()
            if not r:
                return None
            return {
                "id": r["id"], "name": r["name"], "emoji": r["emoji"],
                "color": r["color"], "is_archived": bool(r["is_archived"]),
                "is_system": bool(r["is_system"]),
            }


def create_user_category(
    user_id: int, name: str, emoji: str | None = None, color: str | None = None
) -> dict:
    """Cria categoria custom. Levanta ValueError("CATEGORIA_DUPLICADA") se já existe."""
    ensure_user(user_id)
    ensure_user_categories_seeded(user_id)
    norm = _normalize_category_name(name)
    if not norm:
        raise ValueError("CATEGORIA_INVALIDA")
    emoji = (emoji or "🏷️").strip() or "🏷️"
    color = (color or "#7c3aed").strip() or "#7c3aed"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from user_categories where user_id=%s and name=%s",
                (user_id, norm),
            )
            if cur.fetchone():
                raise ValueError("CATEGORIA_DUPLICADA")
            cur.execute(
                """
                insert into user_categories (user_id, name, emoji, color, is_system)
                values (%s, %s, %s, %s, false)
                returning id
                """,
                (user_id, norm, emoji, color),
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
    return get_user_category(user_id, new_id)


def update_user_category(
    user_id: int,
    cat_id: int,
    *,
    new_name: str | None = None,
    emoji: str | None = None,
    color: str | None = None,
) -> dict:
    """PATCH em uma categoria. Se `new_name` muda, faz UPDATE em cascata.

    Levanta:
      ValueError("CATEGORIA_NAO_ENCONTRADA")
      ValueError("CATEGORIA_DUPLICADA") — novo nome já existe pra outra row
    """
    ensure_user(user_id)
    current = get_user_category(user_id, cat_id)
    if not current:
        raise ValueError("CATEGORIA_NAO_ENCONTRADA")

    next_emoji = (emoji or current["emoji"]).strip() or current["emoji"]
    next_color = (color or current["color"]).strip() or current["color"]
    next_name = current["name"]

    if new_name is not None:
        norm = _normalize_category_name(new_name)
        if not norm:
            raise ValueError("CATEGORIA_INVALIDA")
        next_name = norm

    rename = next_name != current["name"]

    with get_conn() as conn:
        with conn.cursor() as cur:
            if rename:
                cur.execute(
                    "select id from user_categories "
                    "where user_id=%s and name=%s and id<>%s",
                    (user_id, next_name, int(cat_id)),
                )
                if cur.fetchone():
                    raise ValueError("CATEGORIA_DUPLICADA")

                old_name = current["name"]
                # Cascata em todas as tabelas que armazenam o texto.
                cur.execute(
                    "update launches set categoria=%s "
                    "where user_id=%s and lower(categoria)=lower(%s)",
                    (next_name, user_id, old_name),
                )
                cur.execute(
                    "update credit_transactions set categoria=%s "
                    "where user_id=%s and lower(categoria)=lower(%s)",
                    (next_name, user_id, old_name),
                )
                cur.execute(
                    "update category_budgets set categoria=%s "
                    "where user_id=%s and lower(categoria)=lower(%s) "
                    "and not exists ("
                    "  select 1 from category_budgets cb2 "
                    "  where cb2.user_id=%s and lower(cb2.categoria)=lower(%s) and cb2.id<>category_budgets.id"
                    ")",
                    (next_name, user_id, old_name, user_id, next_name),
                )
                cur.execute(
                    "update budget_alert_sent set categoria=%s "
                    "where user_id=%s and lower(categoria)=lower(%s) "
                    "and not exists ("
                    "  select 1 from budget_alert_sent bs2 "
                    "  where bs2.user_id=%s and lower(bs2.categoria)=lower(%s) "
                    "    and bs2.ym=budget_alert_sent.ym and bs2.threshold=budget_alert_sent.threshold"
                    ")",
                    (next_name, user_id, old_name, user_id, next_name),
                )
                cur.execute(
                    "update user_category_rules set category=%s "
                    "where user_id=%s and lower(category)=lower(%s)",
                    (next_name, user_id, old_name),
                )

            cur.execute(
                """
                update user_categories
                   set name=%s, emoji=%s, color=%s
                 where user_id=%s and id=%s
                """,
                (next_name, next_emoji, next_color, user_id, int(cat_id)),
            )
        conn.commit()
    return get_user_category(user_id, cat_id)


def set_user_category_archived(user_id: int, cat_id: int, archived: bool) -> dict:
    ensure_user(user_id)
    current = get_user_category(user_id, cat_id)
    if not current:
        raise ValueError("CATEGORIA_NAO_ENCONTRADA")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update user_categories set is_archived=%s where user_id=%s and id=%s",
                (bool(archived), user_id, int(cat_id)),
            )
        conn.commit()
    return get_user_category(user_id, cat_id)


def delete_user_category(user_id: int, cat_id: int) -> None:
    """Deleta categoria. Levanta ValueError se tem lançamentos vinculados.

    Categorias system (is_system=true) só podem ser arquivadas, nunca deletadas
    — manter a lista canônica disponível pro user reverter um arquivamento.
    """
    ensure_user(user_id)
    current = get_user_category(user_id, cat_id)
    if not current:
        raise ValueError("CATEGORIA_NAO_ENCONTRADA")
    if current["is_system"]:
        raise ValueError("CATEGORIA_SISTEMA_INDELETAVEL")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select
                  (select count(*) from launches
                   where user_id=%s and lower(categoria)=lower(%s)) +
                  (select count(*) from credit_transactions
                   where user_id=%s and lower(categoria)=lower(%s)) as total
                """,
                (user_id, current["name"], user_id, current["name"]),
            )
            total = int(cur.fetchone()["total"] or 0)
            if total > 0:
                raise ValueError("CATEGORIA_COM_LANCAMENTOS")
            cur.execute(
                "delete from user_categories where user_id=%s and id=%s",
                (user_id, int(cat_id)),
            )
        conn.commit()


def resolve_category_rule_target(user_id: int, target: str) -> tuple[str, str, int]:
    """
    Resolve um alvo de remoção informado pelo usuário.

    Retorna:
      ("keyword", keyword_original, 1)
      ("category", category_original, qtd_regras)
      ("", "", 0) se não encontrar
    """
    target_norm = normalize_text(target or "")
    if not target_norm:
        return ("", "", 0)

    rules = list_user_category_rules(user_id)
    if not rules:
        return ("", "", 0)

    for keyword, _category in rules:
        if normalize_text(keyword) == target_norm:
            return ("keyword", keyword, 1)

    category_matches: dict[str, int] = {}
    for _keyword, category in rules:
        if normalize_text(category) == target_norm:
            category_matches[category] = category_matches.get(category, 0) + 1

    if category_matches:
        category, count = max(category_matches.items(), key=lambda item: item[1])
        return ("category", category, count)

    return ("", "", 0)
