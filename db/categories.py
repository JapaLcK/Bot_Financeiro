"""
db/categories.py — Regras de categorização automática de lançamentos.
"""
from .connection import get_conn
from .users import ensure_user
from utils_text import normalize_text


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
