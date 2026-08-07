"""
Camada de banco do sistema de planos v2 (escada Grátis/Essencial/Plus/Pro).

Trial de 30 dias do plano escolhido, via Stripe COM CARTÃO (2026-08-06), com
trava de 1 trial por TELEFONE na vida: `plan_trials` é keyed por phone_hash e
sobrevive à deleção da conta — recriar conta com o mesmo número herda o
started_at original (trial já queimado). A elegibilidade é checada na criação
do checkout (is_trial_eligible_for_user) e o registro (claim_trial_for_user)
acontece quando a assinatura trialing nasce, não mais no cadastro.
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


def is_trial_eligible_for_user(user_id: int) -> bool:
    """O telefone deste usuário ainda tem direito ao trial de 30 dias?

    Regra: 1 trial por telefone na vida. Elegível = o phone_hash NUNCA apareceu
    em plan_trials (nesta conta ou em outra, mesmo deletada). Usado na criação
    do checkout pra decidir se manda trial_period_days=30 ou cobra na hora.

    Sem telefone vinculado → elegível (nada pra gatear; o registro só acontece
    se/quando houver phone_hash). Falha de banco → elegível (fail-open: melhor
    conceder o trial do que cobrar alguém por engano).
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select phone_hash from auth_accounts where user_id = %s",
                    (int(user_id),),
                )
                row = cur.fetchone()
                if not row or not row.get("phone_hash"):
                    return True
                cur.execute(
                    "select 1 from plan_trials where phone_hash = %s",
                    (row["phone_hash"],),
                )
                return cur.fetchone() is None
    except Exception:
        logger.warning("is_trial_eligible_for_user falhou pro user %s", user_id, exc_info=True)
        return True


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


def list_trial_downsell_candidates(window_days: int = 7, trial_days: int = 30) -> list[int]:
    """user_ids com trial vencido há até `window_days` dias, sem downsell enviado.

    A janela evita e-mail atrasado em massa se o flag ligar meses depois de
    trials antigos vencerem. O filtro fino (tier free de verdade, opt-out,
    allowlist) é do chamador — aqui é só o funil por SQL."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select user_id from auth_accounts
                where trial_started_at is not null
                  and trial_downsell_sent_at is null
                  and coalesce(engagement_opt_out, false) = false
                  and trial_started_at + make_interval(days => %s) < now()
                  and trial_started_at + make_interval(days => %s) > now() - make_interval(days => %s)
                """,
                (int(trial_days), int(trial_days), int(window_days)),
            )
            rows = cur.fetchall() or []
    return [int(r["user_id"]) for r in rows]


def mark_trial_downsell_sent(user_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set trial_downsell_sent_at = now() where user_id = %s",
                (int(user_id),),
            )
        conn.commit()


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
