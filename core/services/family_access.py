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

from core.services.billing_access import _ler_conta, recompute_entitlement
from core.services.billing_dunning import PAST_DUE_PAYMENT_STATUSES
from db import family as fam
from db.connection import get_conn
from db.schema import RESYNC_LEGACY_GRANT_USER_SQL
from db.plan_grants import list_grants, upsert_grant


log = logging.getLogger(__name__)

# Fontes que cobrem o titular independente do Stripe. 'legacy' é a assinatura Stripe materializada
# pelo backfill (só some no 1º invoice.paid), então NÃO entra.
_COBERTURA_INDEPENDENTE = ("pix", "admin")


def _cobertura(owner_id: int):
    """(plan_stored, ends_at) do titular, ou None se ele não tem cobertura que possa ser repassada."""
    conta = _ler_conta(owner_id)
    if not conta:
        return None
    plano = (conta.get("plan") or "free").lower()
    fim = conta.get("plan_expires_at")
    if plano == "free" or fim is None or fim <= datetime.now(timezone.utc):
        return None
    if (conta.get("last_payment_status") or "").lower() in PAST_DUE_PAYMENT_STATUSES:
        # atraso é do Stripe: um grant não-Stripe vigente (Pix) segue cobrindo
        if not any(g["status"] == "active" and g["source"] in _COBERTURA_INDEPENDENTE
                   and g["ends_at"] and g["ends_at"] > datetime.now(timezone.utc) for g in list_grants(owner_id)):
            return None
    return plano, fim


def grant_member(owner_id: int, member_user_id: int) -> bool:
    """Concede (ou renova, idempotente pela unique source+external_ref) o grant do membro."""
    cob = _cobertura(owner_id)
    if fam.is_suspended(owner_id):  # cancelamento agendado / pagamento falho: só volta com `retomar`
        return False
    membro = next((m for m in fam.active_members(owner_id) if m["user_id"] == member_user_id), None)
    if not cob or not membro:
        return False
    # Plano pago legado sem nenhum grant: sem materializá-lo, o 1º grant family faria `recompute` tratá-lo
    # como "tem grants" e, ao sair, rebaixá-lo. A SQL é a do backfill (só age se não há grant algum).
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(RESYNC_LEGACY_GRANT_USER_SQL, (member_user_id,))
        conn.commit()
    upsert_grant(member_user_id, "family", f"family_member:{membro['id']}", cob[0],
                 datetime.now(timezone.utc), cob[1], fam.event_version_now())
    recompute_entitlement(member_user_id)
    return True


def sync_from_owner(owner_id: int) -> int:
    """Alinha os membros ao titular: com cobertura concede/renova a todos; sem, revoga. Devolve quantos."""
    if not _cobertura(owner_id):
        _revogar(owner_id, "family_owner_sem_cobertura")
        return 0
    n = 0
    for m in fam.active_members(owner_id):
        try:  # um membro quebrado não derruba o recompute do titular nem os outros membros
            n += grant_member(owner_id, m["user_id"])
        except Exception:
            log.exception("family: grant do membro %s (titular %s) falhou", m["user_id"], owner_id)
    return n


def retomar(owner_id: int) -> int:
    """O titular voltou (assinatura ativa sem cancelamento agendado): fim da suspensão e membros realinhados."""
    fam.set_suspended(owner_id, False)
    return sync_from_owner(owner_id)


def encerrando(owner_id: int, source: str, external_ref: str, motivo: str, desde: int | None = None) -> None:
    """Assinatura `(source, external_ref)` do titular está acabando (cancelamento agendado, atraso): os
    membros perdem agora, EXCETO se outro grant dele ainda cobre — aí o estado (sync) decide."""
    agora = datetime.now(timezone.utc)
    outro = any(g["status"] == "active" and g["source"] in (*_COBERTURA_INDEPENDENTE, "stripe") and g["ends_at"] and g["ends_at"] > agora
                and (g["source"], g["external_ref"]) != (source, external_ref)
                for g in list_grants(owner_id))
    if outro:
        sync_from_owner(owner_id)
    else:
        revoke_members(owner_id, motivo, desde)


def _revogar(owner_id: int, motivo: str) -> int:
    uids = fam.revoke_owner_grants(owner_id, motivo)
    for uid in uids:
        try:  # a falha de um membro não impede os outros nem o pagamento do titular
            recompute_entitlement(uid)
        except Exception:
            log.exception("family: recompute do membro %s (titular %s) falhou", uid, owner_id)
    return len(uids)


def revoke_members(owner_id: int, motivo: str, desde: int | None = None) -> int:
    """Os membros perdem o acesso AGORA e ASSIM FICAM (suspensão persistida: o recompute do titular não os
    religa) até `retomar` ou um pagamento novo aplicado. Devolve quantos foram revogados."""
    fam.set_suspended(owner_id, True, desde)
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
