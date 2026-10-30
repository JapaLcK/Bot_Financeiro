"""db/family.py — Plano Família: grupo, membros, convites, consentimento, log (só banco).

Toda função que recebe `owner_user_id` filtra por ele (o titular só enxerga o próprio grupo).
O teto de pessoas é checado sob `SELECT ... FOR UPDATE` na linha de family_groups, na mesma
transação do insert: dois aceites na última vaga se serializam.
"""
import hashlib
import secrets
import time

from psycopg import errors as pgerr

from .connection import get_conn

FAMILY_BASE_SEATS = 3     # pessoas incluídas (titular conta)
FAMILY_MAX_EXTRA = 3      # vagas extras pagas
FAMILY_MAX_SEATS = FAMILY_BASE_SEATS + FAMILY_MAX_EXTRA
INVITE_DAYS = 7
SCOPES = ("aggregates", "transactions")


class FamilyError(Exception):
    """`args[0]` é o código: no_group, already_in_family, full, invite_invalid, not_member, ..."""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


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


def over_capacity(owner_user_id: int) -> list[int]:
    """user_ids a cortar para o grupo caber na capacidade: os membros mais recentes (LIFO por joined_at)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_groups where owner_user_id = %s", (owner_user_id,))
        row = cur.fetchone()
        if not row:
            return []
        cur.execute("select count(*) as n from family_members where group_id = %s and left_at is null", (row["id"],))
        excesso = cur.fetchone()["n"] - _capacity(cur, row["id"])
        if excesso <= 0:
            return []
        cur.execute(
            """select user_id from family_members where group_id = %s and role = 'member' and left_at is null
                order by joined_at desc, id desc limit %s""",
            (row["id"], excesso),
        )
        return [r["user_id"] for r in cur.fetchall()]


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


def _lock_group(cur, owner_user_id: int):
    cur.execute("select id from family_groups where owner_user_id = %s for update", (owner_user_id,))
    row = cur.fetchone()
    if not row:
        raise FamilyError("no_group")
    return row["id"]


def _capacity(cur, group_id: int) -> int:
    cur.execute(
        """select count(*) as n from family_seat_purchases
            where group_id = %s and status = 'paid' and valid_from <= clock_timestamp()
              and (valid_until is null or valid_until > clock_timestamp())""",
        (group_id,),
    )
    return min(FAMILY_BASE_SEATS + cur.fetchone()["n"], FAMILY_MAX_SEATS)


def _insert_member(cur, group_id: int, user_id: int, role: str) -> None:
    """Uso interno: criação do titular e aceite de convite (único caminho de membro; a API nunca expõe
    inserção direta, pois membro entra só por convite aceito). Checa o teto e insere; o grupo já deve estar travado pelo chamador."""
    cur.execute("select count(*) as n from family_members where group_id = %s and left_at is null", (group_id,))
    if cur.fetchone()["n"] >= _capacity(cur, group_id):
        raise FamilyError("full")
    cur.execute("select 1 from family_members where user_id = %s and left_at is null", (user_id,))
    if cur.fetchone():
        raise FamilyError("already_in_family")
    try:
        cur.execute(
            "insert into family_members (group_id, user_id, role) values (%s, %s, %s)",
            (group_id, user_id, role),
        )
    except pgerr.UniqueViolation:  # corrida em outro grupo: o índice parcial segurou
        raise FamilyError("already_in_family") from None


def create_group(owner_user_id: int, tier: str = "familia") -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select 1 from family_members where user_id = %s and left_at is null", (owner_user_id,))
        if cur.fetchone():
            raise FamilyError("already_in_family")
        try:
            cur.execute(
                "insert into family_groups (owner_user_id, tier) values (%s, %s) "
                "on conflict (owner_user_id) do nothing returning id",
                (owner_user_id, tier),
            )
        except pgerr.ForeignKeyViolation:  # owner inexistente
            raise FamilyError("user_not_found") from None
        row = cur.fetchone()
        if not row:
            raise FamilyError("already_in_family")
        _insert_member(cur, row["id"], owner_user_id, "owner")
        conn.commit()
        return row["id"]


def get_group(owner_user_id: int) -> dict | None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from family_groups where owner_user_id = %s", (owner_user_id,))
        return cur.fetchone()


def seat_capacity(owner_user_id: int) -> int:
    """Base + vagas extras pagas vigentes, nunca acima de FAMILY_MAX_SEATS."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_groups where owner_user_id = %s", (owner_user_id,))
        row = cur.fetchone()
        if not row:
            raise FamilyError("no_group")
        return _capacity(cur, row["id"])


def remove_member(owner_user_id: int, user_id: int, reason: str = "removed") -> None:
    """Marca a saída (left_at) e revoga os consentimentos dele. O titular não sai por aqui."""
    with get_conn() as conn, conn.cursor() as cur:
        group_id = _lock_group(cur, owner_user_id)
        cur.execute(
            """update family_members set left_at = now(), left_reason = %s
                where group_id = %s and user_id = %s and role = 'member' and left_at is null""",
            (reason, group_id, user_id),
        )
        if cur.rowcount == 0:
            raise FamilyError("not_member")
        cur.execute(
            "update family_consents set revoked_at = now() "
            "where group_id = %s and member_user_id = %s and revoked_at is null",
            (group_id, user_id),
        )
        # Só o grant `family`: o individual do membro nunca é tocado; o recompute escolhe o maior.
        revogados = _revoke_family_grants(cur, group_id, f"family_{reason}", user_id)
        conn.commit()
    recompute_users(revogados)


def list_members(owner_user_id: int) -> list[dict]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """select m.user_id, m.role, m.joined_at from family_members m
                 join family_groups g on g.id = m.group_id
                where g.owner_user_id = %s and m.left_at is null order by m.id""",
            (owner_user_id,),
        )
        return cur.fetchall()


def create_invite(owner_user_id: int) -> str:
    """Devolve o token cru (só aqui); no banco fica o sha256."""
    token = secrets.token_urlsafe(32)
    with get_conn() as conn, conn.cursor() as cur:
        group_id = _lock_group(cur, owner_user_id)
        cur.execute(
            "insert into family_invites (group_id, token_hash, expires_at) "
            "values (%s, %s, now() + make_interval(days => %s))",
            (group_id, hash_token(token), INVITE_DAYS),
        )
        conn.commit()
    return token


def revoke_invite(owner_user_id: int, token: str) -> bool:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """update family_invites i set revoked_at = now() from family_groups g
                where g.id = i.group_id and g.owner_user_id = %s and i.token_hash = %s
                  and i.used_at is null and i.revoked_at is null""",
            (owner_user_id, hash_token(token)),
        )
        conn.commit()
        return cur.rowcount == 1


def accept_invite(token: str, user_id: int) -> int:
    """Consome o convite e adiciona `user_id` ao grupo, tudo numa transação. Devolve o group_id."""
    th = hash_token(token)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select g.owner_user_id from family_invites i join family_groups g on g.id = i.group_id "
                    "where i.token_hash = %s", (th,))
        row = cur.fetchone()
        if not row:
            raise FamilyError("invite_invalid")
        try:
            group_id = _lock_group(cur, row["owner_user_id"])
        except FamilyError:  # dissolvido entre a leitura e o lock
            raise FamilyError("invite_invalid") from None
        try:
            cur.execute(
                """update family_invites set used_at = now(), used_by_user_id = %s
                    where token_hash = %s and group_id = %s and used_at is null
                      and revoked_at is null and expires_at > clock_timestamp()""",
                (user_id, th, group_id),
            )
        except pgerr.ForeignKeyViolation:  # used_by_user_id inexistente
            raise FamilyError("user_not_found") from None
        if cur.rowcount == 0:
            raise FamilyError("invite_invalid")
        _insert_member(cur, group_id, user_id, "member")  # erro aqui desfaz o "used" (rollback)
        conn.commit()
        owner_id = row["owner_user_id"]
    from core.services.family_access import grant_member
    grant_member(owner_id, user_id)  # no-op se o titular não tem cobertura ativa
    return group_id


def _lock_member_group(cur, member_user_id: int) -> tuple[int, int]:
    """Trava o grupo do membro (mesma ordem de lock de remove_member) e reconfere o vínculo ativo."""
    cur.execute(
        """select g.id, g.owner_user_id from family_members m join family_groups g on g.id = m.group_id
            where m.user_id = %s and m.role = 'member' and m.left_at is null""",
        (member_user_id,),
    )
    row = cur.fetchone()
    if not row:
        raise FamilyError("not_member")
    try:
        gid = _lock_group(cur, row["owner_user_id"])
    except FamilyError:  # grupo dissolvido entre a leitura e o lock
        raise FamilyError("not_member") from None
    cur.execute(
        "select 1 from family_members where group_id = %s and user_id = %s and role = 'member' and left_at is null",
        (gid, member_user_id),
    )
    if not cur.fetchone():
        raise FamilyError("not_member")
    return gid, row["owner_user_id"]


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
    Negada levanta FamilyError e não grava nada."""
    if scope not in SCOPES:
        raise FamilyError("bad_scope")
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


def dissolve_group(owner_user_id: int) -> bool:
    """Apaga na hora a casa inteira (cascade); o log sobrevive com group_id NULL."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from family_groups where owner_user_id = %s for update", (owner_user_id,))
        row = cur.fetchone()
        revogados = _revoke_family_grants(cur, row["id"], "family_dissolved") if row else []
        cur.execute("delete from family_groups where owner_user_id = %s", (owner_user_id,))
        apagou = cur.rowcount == 1
        conn.commit()
    recompute_users(revogados)
    return apagou
