# category_db.py
from __future__ import annotations

from dataclasses import dataclass
from db import get_conn  # <- esse deve existir no seu db.py


@dataclass(frozen=True)
class Trigger:
    category: str
    keyword: str
    match_type: str  # 'contains' | 'regex'


def list_triggers(user_id: int) -> list[Trigger]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select category, keyword, match_type
                from user_category_triggers
                where user_id=%s and is_enabled=true
                order by category asc, keyword asc
                """,
                (user_id,),
            )
            rows = cur.fetchall() or []
    return [Trigger(r[0], r[1], r[2]) for r in rows]  # caso teu cursor não seja dict


def upsert_candidate(user_id: int, category: str, keyword: str, source: str | None) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into user_trigger_candidates (user_id, category, keyword, seen_count, source)
                values (%s, %s, %s, 1, %s)
                on conflict (user_id, category, keyword)
                do update set
                  seen_count = user_trigger_candidates.seen_count + 1,
                  last_seen_at = now(),
                  source = coalesce(excluded.source, user_trigger_candidates.source)
                returning seen_count
                """,
                (user_id, category, keyword, source),
            )
            row = cur.fetchone()
            if isinstance(row, dict):
                seen = row.get("seen_count")
            else:
                seen = row[0]
    return int(seen)


def promote_candidate_to_trigger(
    user_id: int,
    category: str,
    keyword: str,
    match_type: str = "contains",
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into user_category_triggers (user_id, category, keyword, match_type, is_enabled)
                values (%s, %s, %s, %s, true)
                on conflict (user_id, category, keyword, match_type)
                do update set is_enabled=true
                """,
                (user_id, category, keyword, match_type),
            )
            cur.execute(
                """
                delete from user_trigger_candidates
                where user_id=%s and category=%s and keyword=%s
                """,
                (user_id, category, keyword),
            )


def insert_feedback(
    user_id: int,
    text_base: str,
    chosen_category: str,
    inferred_category: str | None,
    source: str | None,
    launch_id: int | None = None,
) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into user_category_feedback
                  (user_id, launch_id, text_base, source, chosen_category, inferred_category)
                values (%s, %s, %s, %s, %s, %s)
                """,
                (user_id, launch_id, text_base, source, chosen_category, inferred_category),
            )