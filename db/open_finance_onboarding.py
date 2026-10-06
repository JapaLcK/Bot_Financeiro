"""Marco bancário persistente, promovido exclusivamente por prova do servidor."""

from .bank_movements import _lock_user
from .connection import get_conn
from .open_finance import _read_open_finance_connections
from .users import ensure_user


def get_open_finance_onboarding(user_id: int) -> dict:
    """Reconcilia legado do site; remover bancos não revoga uma conclusão passada.

    Prova e carimbo compartilham transação e a ordem accounts → conexões de
    sync/remoção. O wizard pulável não é prova; nem observação de saúde sem sync.
    """
    ensure_user(user_id)
    with get_conn() as conn, conn.cursor() as cur:
        _lock_user(cur, user_id)
        cur.execute(
            "select open_finance_onboarding_completed_at as completed_at "
            "from auth_accounts where user_id=%s", (user_id,),
        )
        row = cur.fetchone()
        completed_at = row["completed_at"] if row else None
        if row and completed_at is None:
            connections = _read_open_finance_connections(cur, user_id, lock=True)
            candidates = [c["id"] for c in connections
                          if c["provider"] == "pluggy"
                          and c["last_sync_at"] is not None
                          and (c["reconnected_at"] is None
                               or c["last_sync_at"] >= c["reconnected_at"])
                          and c["ui"]["state"] in {"updated", "partial"}]
            if candidates:
                cur.execute(
                    """select 1 from open_finance_connections c
                         where c.user_id=%s and c.id=any(%s)
                           and (exists (select 1 from open_finance_accounts a
                                         where a.connection_id=c.id)
                                or exists (select 1 from open_finance_investments i
                                            where i.connection_id=c.id)) limit 1""",
                    (user_id, candidates),
                )
                if cur.fetchone():
                    cur.execute(
                        """update auth_accounts
                              set open_finance_onboarding_completed_at=now()
                            where user_id=%s and open_finance_onboarding_completed_at is null
                            returning open_finance_onboarding_completed_at as completed_at""",
                        (user_id,),
                    )
                    completed_at = cur.fetchone()["completed_at"]
        conn.commit()
    return {"ok": True, "completed": completed_at is not None,
            "completed_at": completed_at.isoformat() if completed_at else None}
