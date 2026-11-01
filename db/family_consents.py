"""db/family_consents.py — Plano Família: consentimento do membro e log de leitura (só banco).

Mesmas regras de db/family.py: o lock do grupo (`FOR UPDATE`) serializa concessão, revogação e leitura.
"""
import re
from typing import Literal, get_args

from .connection import get_conn
from .family import SCOPES, FamilyError, _lock_group, _lock_member_group

ESCOPO_BANCO = {"agregados": "aggregates", "lancamentos": "transactions"}  # nome na API -> escopo no banco
ESCOPO_API = {v: k for k, v in ESCOPO_BANCO.items()}
Escopo = Literal["agregados", "lancamentos"]  # a fonte única do nome na API (as chaves de ESCOPO_BANCO)
RECURSO = re.compile(rf"({'|'.join(get_args(Escopo))}):[0-9]{{4}}-[0-9]{{2}}")  # contrato do histórico: `escopo:AAAA-MM`


def grant_consent(member_user_id: int, scope: str) -> None:
    """O membro autoriza o titular do grupo dele a ver `scope`. `transactions` exige `aggregates` ativo."""
    if scope not in SCOPES:
        raise FamilyError("bad_scope")
    with get_conn() as conn, conn.cursor() as cur:
        gid, viewer = _lock_member_group(cur, member_user_id)
        if scope == "transactions":
            cur.execute(
                "select 1 from family_consents where group_id = %s and member_user_id = %s "
                "and viewer_user_id = %s and scope = 'aggregates' and revoked_at is null",
                (gid, member_user_id, viewer),
            )
            if not cur.fetchone():
                raise FamilyError("aggregates_required")
        cur.execute(
            "insert into family_consents (group_id, member_user_id, viewer_user_id, scope) "
            "values (%s, %s, %s, %s) on conflict do nothing",
            (gid, member_user_id, viewer, scope),
        )
        conn.commit()


def revoke_consent(member_user_id: int, scope: str) -> None:
    """Revogar `aggregates` revoga `transactions` junto (ela depende dele)."""
    if scope not in SCOPES:
        raise FamilyError("bad_scope")
    escopos = list(SCOPES) if scope == "aggregates" else [scope]
    with get_conn() as conn, conn.cursor() as cur:
        gid, _ = _lock_member_group(cur, member_user_id)
        cur.execute(
            "update family_consents set revoked_at = now() "
            "where group_id = %s and member_user_id = %s and scope = any(%s) and revoked_at is null",
            (gid, member_user_id, escopos),
        )
        conn.commit()


def list_consents(owner_user_id: int) -> list[dict]:
    """Consentimentos ativos concedidos ao titular (viewer = ele) no grupo dele."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """select c.member_user_id, c.scope, c.granted_at from family_consents c
                 join family_groups g on g.id = c.group_id
                where g.owner_user_id = %s and c.viewer_user_id = %s and c.revoked_at is null
                order by c.id""",
            (owner_user_id, owner_user_id),
        )
        return cur.fetchall()


def log_read(group_id: int, viewer_user_id: int, member_user_id: int, scope: str, recurso: str) -> None:
    """Grava só leitura SERVIDA: exige titular do grupo, membro ativo e consentimento ativo no escopo.
    Negada levanta FamilyError e não grava nada. `recurso` = `escopo:AAAA-MM` do MESMO escopo: `/familia/historico` o fatia."""
    if scope not in SCOPES:
        raise FamilyError("bad_scope")
    r = RECURSO.fullmatch(recurso)
    if not r or r[1] != ESCOPO_API[scope]:
        raise FamilyError("bad_recurso")
    with get_conn() as conn, conn.cursor() as cur:
        if _lock_group(cur, viewer_user_id) != group_id:
            raise FamilyError("not_owner")
        cur.execute(
            """select 1 from family_members m join family_consents c
                 on c.group_id = m.group_id and c.member_user_id = m.user_id
                where m.group_id = %s and m.user_id = %s and m.role = 'member' and m.left_at is null
                  and c.viewer_user_id = %s and c.scope = %s and c.revoked_at is null""",
            (group_id, member_user_id, viewer_user_id, scope),
        )
        if not cur.fetchone():
            raise FamilyError("no_consent")
        cur.execute(
            "insert into family_access_log (group_id, viewer_user_id, member_user_id, scope, recurso) "
            "values (%s, %s, %s, %s, %s)",
            (group_id, viewer_user_id, member_user_id, scope, recurso),
        )
        conn.commit()


def active_consent(viewer_user_id: int, member_user_id: int, scope: str) -> dict | None:
    """{"group_id", "joined_at"} se `viewer` é o titular do grupo do membro ATIVO e há consentimento vigente em
    `scope`; senão None. Só leitura, sem lock: quem grava (`log_read`) confere de novo sob o lock."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """select m.group_id, m.joined_at from family_members m
                 join family_groups g on g.id = m.group_id
                 join family_consents c on c.group_id = m.group_id and c.member_user_id = m.user_id
                where g.owner_user_id = %s and m.user_id = %s and m.role = 'member' and m.left_at is null
                  and c.viewer_user_id = %s and c.scope = %s and c.revoked_at is null""",
            (viewer_user_id, member_user_id, viewer_user_id, scope),
        )
        return cur.fetchone()


def member_scopes(member_user_id: int) -> list[str]:
    """Escopos que o membro tem ativos no grupo ativo dele."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """select c.scope from family_consents c join family_members m
                 on m.group_id = c.group_id and m.user_id = c.member_user_id
                where c.member_user_id = %s and m.left_at is null and c.revoked_at is null order by c.id""",
            (member_user_id,),
        )
        return [r["scope"] for r in cur.fetchall()]


def access_log(member_user_id: int, antes_id: int | None, limite: int) -> list[dict]:
    """Quem leu o que do membro, mais recente primeiro. Só filtra pelo membro: vale depois de sair ou da dissolução."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select id, viewer_user_id, scope, recurso, at from family_access_log "
            "where member_user_id = %s and (%s::bigint is null or id < %s) order by id desc limit %s",
            (member_user_id, antes_id, antes_id, limite),
        )
        return cur.fetchall()
