"""db/family_views.py — Plano Família: leituras para a API (só banco; a escrita mora em db/family.py).

Toda query filtra por user_id (o próprio) ou por owner_user_id (o titular só enxerga o próprio grupo).
O que sai daqui para o titular é só o necessário: ref do membro, primeiro nome e status. Nunca user_id,
e-mail, telefone nem dado financeiro de membro.
"""
import logging
import re

from .connection import get_conn
from .family import _capacity

logger = logging.getLogger(__name__)
_REF = re.compile(r"[0-9]{1,18}")


def my_family(user_id: int) -> dict | None:
    """{"role": "owner"|"member", "group_id", "owner_user_id"} do vínculo ativo, ou None. Uma query, índice parcial."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """select m.role, g.id as group_id, g.owner_user_id from family_members m
                 join family_groups g on g.id = m.group_id
                where m.user_id = %s and m.left_at is null""",
            (user_id,),
        )
        return cur.fetchone()


def _convites_ativos(cur, group_id: int) -> int:
    cur.execute(
        "select count(*) as n from family_invites where group_id = %s and used_at is null "
        "and revoked_at is null and expires_at > clock_timestamp()", (group_id,))
    return cur.fetchone()["n"]


def _primeiro_nome(user_id: int) -> str:
    from db import get_auth_user  # display_name é PII cifrada: quem decifra é o get_auth_user
    try:
        nome = ((get_auth_user(user_id) or {}).get("display_name") or "").split()
    except Exception as exc:  # PII que não decifra não pode derrubar a tela do titular: este membro fica sem nome
        logger.warning("family_views: display_name ilegível (%s)", type(exc).__name__, extra={"user_id": user_id})
        return ""
    return nome[0] if nome else ""


def owner_view(owner_user_id: int) -> dict | None:
    """{"capacidade", "membros": [{"ref", "nome", "status"}], "convites_ativos"} do grupo do titular, ou None."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_groups where owner_user_id = %s", (owner_user_id,))
        g = cur.fetchone()
        if not g:
            return None
        # status = acesso efetivo: o marcador do grupo fica setado mesmo quando Pix/admin cobrem os membros
        cur.execute(
            """select m.id, m.user_id, exists (
                     select 1 from plan_grants pg where pg.user_id = m.user_id and pg.source = 'family'
                        and pg.external_ref = 'family_member:' || m.id and pg.status = 'active'
                        and pg.starts_at <= now() and pg.ends_at > now()) as ativo
                 from family_members m
                where m.group_id = %s and m.role = 'member' and m.left_at is null order by m.joined_at, m.id""",
            (g["id"],),
        )
        membros = cur.fetchall()
        capacidade, convites = _capacity(cur, g["id"]), _convites_ativos(cur, g["id"])
    return {"capacidade": capacidade, "convites_ativos": convites,
            "membros": [{"ref": str(m["id"]), "nome": _primeiro_nome(m["user_id"]),
                         "status": "ativo" if m["ativo"] else "suspenso"} for m in membros]}


def member_user_id(owner_user_id: int, ref: str) -> int | None:
    """user_id do membro ATIVO `ref` do grupo deste titular; qualquer outra coisa (de outro grupo, o titular,
    quem já saiu, ref malformado) é None. O filtro por owner é o que impede o titular A de alcançar o membro de B."""
    if not _REF.fullmatch(ref):
        return None
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """select m.user_id from family_members m join family_groups g on g.id = m.group_id
                where g.owner_user_id = %s and m.id = %s and m.role = 'member' and m.left_at is null""",
            (owner_user_id, int(ref)),
        )
        row = cur.fetchone()
        return row["user_id"] if row else None


def dissolve_preview(owner_user_id: int) -> dict | None:
    """O que a dissolução apaga, em contagens, mais o group_id (que entra no nonce de confirmação)."""
    with get_conn() as conn, conn.cursor() as cur:
        return dissolve_preview_cur(cur, owner_user_id)


def dissolve_preview_cur(cur, owner_user_id: int) -> dict | None:
    """O mesmo, no cursor de quem chama (a dissolução confere o nonce sob o lock do grupo)."""
    cur.execute("select id from family_groups where owner_user_id = %s", (owner_user_id,))
    g = cur.fetchone()
    if not g:
        return None
    cur.execute("select id from family_members where group_id = %s and role = 'member' "
                "and left_at is null order by id", (g["id"],))
    ids = [m["id"] for m in cur.fetchall()]
    cur.execute("select count(*) as n from family_consents where group_id = %s and revoked_at is null",
                (g["id"],))
    return {"group_id": g["id"], "membros": len(ids), "ids_membros": ids, "consentimentos": cur.fetchone()["n"],
            "convites": _convites_ativos(cur, g["id"])}
