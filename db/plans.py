"""
Camada de banco do sistema de planos v2 (escada Grátis/Essencial/Plus/Pro).

Trial de 30 dias sem cartão, com trava de 1 trial por TELEFONE na vida:
`plan_trials` é keyed por phone_hash e sobrevive à deleção da conta — recriar
conta com o mesmo número herda o started_at original (trial já queimado).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .connection import get_conn

logger = logging.getLogger(__name__)


def claim_trial_for_user(user_id: int) -> datetime | None:
    """Ancora o trial do usuário no telefone dele (idempotente).

    Regra fechada: o contador é UM SÓ — 30 dias por telefone, na vida.
    - Telefone nunca usou trial → registra now() em plan_trials e ancora a conta.
    - Telefone JÁ usou trial (nesta ou noutra conta, mesmo deletada) → a conta
      herda o started_at ORIGINAL; se o trial já venceu, days_left = 0.

    Retorna o started_at efetivo (ou None se a conta não tem telefone).
    Nunca levanta — falha aqui não pode quebrar cadastro/vinculação.
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select phone_hash, trial_started_at from auth_accounts where user_id = %s",
                    (int(user_id),),
                )
                row = cur.fetchone()
                if not row or not row.get("phone_hash"):
                    return None
                phone_hash = row["phone_hash"]

                cur.execute(
                    """
                    insert into plan_trials (phone_hash, user_id, started_at)
                    values (%s, %s, now())
                    on conflict (phone_hash) do nothing
                    """,
                    (phone_hash, int(user_id)),
                )
                cur.execute(
                    "select started_at from plan_trials where phone_hash = %s",
                    (phone_hash,),
                )
                trial_row = cur.fetchone()
                started_at = trial_row["started_at"] if trial_row else None

                if started_at is not None:
                    # Ancora na conta o started_at mais ANTIGO conhecido (nunca
                    # rejuvenesce um trial já queimado).
                    cur.execute(
                        """
                        update auth_accounts
                        set trial_started_at = %s
                        where user_id = %s
                          and (trial_started_at is null or trial_started_at > %s)
                        """,
                        (started_at, int(user_id), started_at),
                    )
            conn.commit()
        return started_at
    except Exception:
        logger.warning("claim_trial_for_user falhou pro user %s", user_id, exc_info=True)
        return None


def get_trial_started_at(user_id: int) -> datetime | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select trial_started_at from auth_accounts where user_id = %s",
                (int(user_id),),
            )
            row = cur.fetchone()
    if not row or row.get("trial_started_at") is None:
        return None
    started = row["trial_started_at"]
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return started


def count_launches_this_month(user_id: int) -> int:
    """Lançamentos do mês-calendário corrente (limite do tier Grátis)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select count(*) as n
                from launches
                where user_id = %s
                  and source = 'manual'
                  and date_trunc('month', criado_em) = date_trunc('month', now())
                """,
                (int(user_id),),
            )
            row = cur.fetchone()
    return int(row["n"]) if row else 0
