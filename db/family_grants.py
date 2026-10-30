"""db/family_grants.py — Plano Família: grants `family` dos membros, suspensão e capacidade (só banco).

Parte de acesso do grupo (db/family.py guarda grupo, membros, convites e consentimento).
"""
import time

from .connection import get_conn


def event_version_now() -> int:
    """Versão (µs) de toda escrita de grant `family`: precisa vencer a revogação anterior da mesma
    linha (o upsert exige versão estritamente maior), e o relógio é o do app nas duas pontas."""
    return time.time_ns() // 1000


def _revoke_family_grants(cur, group_id: int, motivo: str, user_id: int | None = None) -> list[int]:
    """Revoga o grant `family` dos membros do grupo (ou só o de `user_id`), na transação do chamador.
    Casa por `external_ref` E por `user_id`: nunca toca grant de outro dono nem grant individual."""
    cur.execute(
        """update plan_grants g set status = 'revoked', revoked_reason = %s, revoked_at = now(),
                  event_version = %s, updated_at = now()
             from family_members m
            where m.group_id = %s and g.user_id = m.user_id and g.source = 'family'
              and g.external_ref = 'family_member:' || m.id and g.status <> 'revoked'
              and (%s::bigint is null or m.user_id = %s)
        returning g.user_id""",
        (motivo, event_version_now(), group_id, user_id, user_id),
    )
    return [r["user_id"] for r in cur.fetchall()]


def set_suspended(owner_user_id: int, suspended: bool, desde: int | None = None) -> None:
    """Liga/desliga a suspensão dos membros do titular (sem grupo é no-op). `desde` (epoch s) é o instante
    do EVENTO que suspendeu: só um evento Stripe mais novo que ele devolve os membros (upsert_grant)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update family_groups set members_suspended_at ="
                    " case when %s then coalesce(to_timestamp(%s::double precision), now()) end"
                    " where owner_user_id = %s", (suspended, desde, owner_user_id))
        conn.commit()


def is_suspended(owner_user_id: int) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select members_suspended_at is not null as s from family_groups where owner_user_id = %s",
                    (owner_user_id,))
        row = cur.fetchone()
        return bool(row and row["s"])


def recompute_users(user_ids) -> None:
    from core.services.billing_access import recompute_entitlement
    for uid in user_ids:
        recompute_entitlement(uid)


def active_members(owner_user_id: int) -> list[dict]:
    """Membros (não o titular) ativos do grupo do titular, mais antigos primeiro. Traz o `id` da linha,
    que é a chave do grant `family`."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """select m.id, m.user_id, m.joined_at from family_members m
                 join family_groups g on g.id = m.group_id
                where g.owner_user_id = %s and m.role = 'member' and m.left_at is null
                order by m.joined_at, m.id""",
            (owner_user_id,),
        )
        return cur.fetchall()


def _revoke_owner_grants(cur, owner_user_id: int, motivo: str) -> list[int]:
    cur.execute("select id from family_groups where owner_user_id = %s", (owner_user_id,))
    row = cur.fetchone()
    return _revoke_family_grants(cur, row["id"], motivo) if row else []


def revoke_owner_grants(owner_user_id: int, motivo: str) -> list[int]:
    """Revoga os grants `family` de todos os membros do titular. Devolve os user_ids revogados."""
    with get_conn() as conn, conn.cursor() as cur:
        uids = _revoke_owner_grants(cur, owner_user_id, motivo)
        conn.commit()
        return uids


def release_user(cur, user_id: int, motivo: str) -> list[int]:
    """Conta que vai sumir (exclusão, merge), na transação do chamador: os membros dela perdem o grant
    `family` (o cascade apagaria `family_members` e nada mais os alcançaria) e o grant `family` que ela
    mesma recebe não segue para outra conta. Devolve os membros a recalcular DEPOIS do commit."""
    uids = _revoke_owner_grants(cur, user_id, motivo)
    cur.execute("delete from plan_grants where user_id = %s and source = 'family'", (user_id,))
    return uids


def set_seat_status(source: str, external_ref: str, status: str, event_version: int) -> bool:
    """Muda o status de uma vaga extra (evento mais antigo que o gravado não se aplica) e, se o grupo
    ficou acima da capacidade, corta os membros mais recentes. O webhook que chama isto é do PR 7."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """update family_seat_purchases p set status = %s, event_version = %s
                 from family_groups g
                where g.id = p.group_id and p.source = %s and p.external_ref = %s and p.event_version <= %s
            returning g.owner_user_id""",
            (status, event_version, source, external_ref, event_version),
        )
        row = cur.fetchone()
        conn.commit()
    if not row:
        return False
    from core.services.family_access import enforce_capacity
    enforce_capacity(row["owner_user_id"])
    return True
