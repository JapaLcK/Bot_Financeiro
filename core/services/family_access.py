"""core/services/family_access.py — Plano Família: o acesso do titular chega aos membros como
`plan_grants` source='family' (external_ref 'family_member:<family_members.id>').

Quem decide o tier do membro continua sendo `recompute_entitlement` (maior tier entre os grants
vigentes): o individual do membro nunca é tocado, e ao sair o recompute volta para ele.
Toda query filtra por user_id/owner (CLAUDE.md §0): operar o titular A não alcança membros de B.

ponytail: cobertura do titular = o par já projetado em auth_accounts (plan, plan_expires_at); titular
vitalício (expires nulo) não propaga. Cancelamento agendado e pagamento falho suspendem os membros
(family_groups.members_suspended_at) até `retomar` ou um pagamento novo aplicado (upsert_grant).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.services.billing_access import _ler_conta, grant_vigente, recompute_entitlement
from core.services.billing_dunning import PAST_DUE_PAYMENT_STATUSES
from db import family as fam
from db import family_grants as fg
from db.connection import get_conn
from db.schema import RESYNC_LEGACY_GRANT_USER_SQL
from db.plan_grants import list_grants, upsert_grant_cur


log = logging.getLogger(__name__)

# Fontes que cobrem o titular independente do Stripe. 'legacy' é a assinatura Stripe materializada
# pelo backfill (só some no 1º invoice.paid), então NÃO entra.
_COBERTURA_INDEPENDENTE = ("pix", "admin")


def _decidir_cobertura(conta, suspenso: bool, grants):
    """Regra única da cobertura (função pura): (plan_stored, ends_at) ou None. `conta` = linha de auth_accounts,
    `grants` = função que devolve os grants do titular (só chamada se a regra precisa deles). Usada fora (`_cobertura`) e dentro da transação do grant_member."""
    if not conta:
        return None
    agora = datetime.now(timezone.utc)
    plano = (conta.get("plan") or "free").lower()
    fim = conta.get("plan_expires_at")
    if plano == "free" or fim is None or fim <= agora:
        return None
    if (conta.get("last_payment_status") or "").lower() in PAST_DUE_PAYMENT_STATUSES or suspenso:
        # atraso ou encerramento é do Stripe (a suspensão é o marcador, persistido mesmo com outro grant
        # cobrindo): só um grant não-Stripe vigente (Pix/admin) segue cobrindo
        if not grant_vigente(grants(), agora, sources=_COBERTURA_INDEPENDENTE):
            return None
    return plano, fim


def _cobertura(owner_id: int):
    """(plan_stored, ends_at) do titular, ou None se ele não tem cobertura que possa ser repassada."""
    return _decidir_cobertura(_ler_conta(owner_id), fg.is_suspended(owner_id), lambda: list_grants(owner_id))


def cobertura_no_lock(cur, owner_id: int, group_id: int):
    """`_cobertura` lida no cursor de quem já tem o lock do grupo (a lida fora do lock pode ter sido vencida por uma
    revogação/suspensão que commitou no meio: ela faz UPDATE nesta linha do grupo ou na conta do titular)."""
    cur.execute("select plan, plan_expires_at, last_payment_status from auth_accounts where user_id = %s",
                (owner_id,))
    conta = cur.fetchone()
    cur.execute("select members_suspended_at is not null as s from family_groups where id = %s", (group_id,))
    suspenso = cur.fetchone()["s"]

    def grants_do_titular():
        cur.execute("select status, source, starts_at, ends_at from plan_grants where user_id = %s"
                    " and status = 'active'", (owner_id,))
        return cur.fetchall()
    return _decidir_cobertura(conta, suspenso, grants_do_titular)


def grant_member(owner_id: int, member_user_id: int) -> bool:
    """Concede (ou renova, idempotente pela unique source+external_ref) o grant do membro."""
    cob = _cobertura(owner_id)
    if not cob:
        return False
    # Lock do grupo (mesma ordem de remove_member) e vínculo revalidado NA MESMA transação do upsert: sem isso,
    # uma saída que commita entre a leitura e o upsert tinha o grant recriado (a versão nova vence a revogação).
    with get_conn() as conn, conn.cursor() as cur:
        try:
            group_id = fam._lock_group(cur, owner_id)
        except fam.FamilyError:
            return False
        cob = cobertura_no_lock(cur, owner_id, group_id)  # revalidada DEPOIS do lock, na mesma transação
        if not cob:
            return False
        cur.execute("select id from family_members where group_id = %s and user_id = %s and role = 'member'"
                    " and left_at is null", (group_id, member_user_id))
        membro = cur.fetchone()
        if not membro:
            return False
        # Plano pago legado sem nenhum grant: sem materializá-lo, o 1º grant family faria `recompute` tratá-lo
        # como "tem grants" e, ao sair, rebaixá-lo. A SQL é a do backfill (só age se não há grant algum).
        cur.execute(RESYNC_LEGACY_GRANT_USER_SQL, (member_user_id,))
        upsert_grant_cur(cur, member_user_id, "family", f"family_member:{membro['id']}", cob[0],
                         datetime.now(timezone.utc), cob[1], fg.event_version_now())
        conn.commit()
    recompute_entitlement(member_user_id)
    return True


def sync_from_owner(owner_id: int) -> int:
    """Alinha os membros ao titular: com cobertura concede/renova a todos; sem, revoga. Devolve quantos."""
    if not _cobertura(owner_id):
        _revogar(owner_id, "family_owner_sem_cobertura")
        return 0
    n = 0
    for m in fg.active_members(owner_id):
        try:  # um membro quebrado não derruba o recompute do titular nem os outros membros
            n += grant_member(owner_id, m["user_id"])
        except Exception as exc:
            log.warning("family: grant do membro %s (titular %s) falhou causa=%s sqlstate=%s", m["user_id"], owner_id,
                        type(exc).__name__, getattr(exc, "sqlstate", None), extra={"user_id": owner_id})
    return n


def retomar(owner_id: int) -> int:
    """O titular voltou (assinatura ativa sem cancelamento agendado): fim da suspensão e membros realinhados."""
    fg.set_suspended(owner_id, False)
    return sync_from_owner(owner_id)


def encerrando(owner_id: int, source: str, external_ref: str, motivo: str, desde: int | None = None) -> None:
    """Assinatura `(source, external_ref)` do titular está acabando (cancelamento agendado, atraso): os
    membros perdem agora, EXCETO se outro grant dele ainda cobre. Outra assinatura Stripe cobrindo: o estado (sync)
    decide. Só Pix/admin cobrindo: a suspensão é gravada MESMO ASSIM (é o marcador de que o Stripe encerra), e
    quando eles acabam o recompute revoga os membros em vez de aceitar o período Stripe já pago."""
    agora = datetime.now(timezone.utc)
    outros = [g for g in list_grants(owner_id) if (g["source"], g["external_ref"]) != (source, external_ref)]
    if grant_vigente(outros, agora, sources=("stripe",)):
        sync_from_owner(owner_id)
    elif grant_vigente(outros, agora, sources=_COBERTURA_INDEPENDENTE):
        fg.set_suspended(owner_id, True, desde)
        sync_from_owner(owner_id)
    else:
        revoke_members(owner_id, motivo, desde)


def _revogar(owner_id: int, motivo: str) -> int:
    uids = fg.revoke_owner_grants(owner_id, motivo)
    fg.recompute_users(uids, owner_id)
    return len(uids)


def revoke_members(owner_id: int, motivo: str, desde: int | None = None) -> int:
    """Os membros perdem o acesso AGORA e ASSIM FICAM (suspensão persistida: o recompute do titular não os
    religa) até `retomar` ou um pagamento novo aplicado. Devolve quantos foram revogados."""
    fg.set_suspended(owner_id, True, desde)
    return _revogar(owner_id, motivo)


def enforce_capacity(owner_id: int) -> list[int]:
    """Grupo acima da capacidade (vaga estornada/vencida): corta o membro mais recente (LIFO) pelo mesmo
    caminho de qualquer saída — remove_member revoga o grant family e recalcula o acesso dele."""
    cortados = []
    for uid in fam.over_capacity(owner_id):
        try:
            fam.remove_member(owner_id, uid, "seat_lost")
            cortados.append(uid)
        except fam.FamilyError:  # já saiu entre a leitura e o lock
            pass
    return cortados
