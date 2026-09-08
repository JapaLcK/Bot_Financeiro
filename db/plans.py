"""
Camada de banco do sistema de planos v2 (escada Grátis/Essencial/Plus/Pro).

Trial de 15 dias do plano escolhido, via Stripe COM CARTÃO (2026-08-06), com
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


class TrialEligibilityError(RuntimeError):
    """Não foi possível decidir com segurança se o telefone pode usar trial."""


class TrialClaimError(RuntimeError):
    """O trial nasceu na Stripe, mas sua trava ainda não foi persistida."""


def claim_trial_for_user(user_id: int) -> datetime | None:
    """Ancora o trial do usuário no telefone dele (idempotente).

    Regra fechada: o contador é UM SÓ — 15 dias por telefone, na vida.
    - Telefone nunca usou trial → registra now() em plan_trials e ancora a conta.
    - Telefone JÁ usou trial (nesta ou noutra conta, mesmo deletada) → a conta
      herda o started_at ORIGINAL; se o trial já venceu, days_left = 0.

    Retorna o started_at efetivo. Levanta TrialClaimError para que o webhook
    responda com erro e a Stripe tente novamente; confirmar sem gravar a trava
    permitiria um segundo trial depois.
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
                    raise TrialClaimError("Conta sem telefone para registrar o trial.")
                phone_hash = row["phone_hash"]

                cur.execute(
                    """
                    insert into plan_trials (phone_hash, user_id, started_at, model_version)
                    values (%s, %s, now(), 2)
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
                if started_at is None:
                    raise TrialClaimError("O registro do trial não pôde ser confirmado.")

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
        from db_support import invalidate_auth_user_cache
        invalidate_auth_user_cache(user_id)
        return started_at
    except TrialClaimError:
        raise
    except Exception as exc:
        logger.warning("claim_trial_for_user falhou pro user %s", user_id, exc_info=True)
        raise TrialClaimError("Falha ao persistir o uso do trial.") from exc


def is_trial_eligible_for_user(user_id: int) -> bool:
    """O telefone deste usuário ainda tem direito ao trial de 15 dias?

    Regra: 1 trial por telefone na vida. Elegível = o phone_hash NUNCA apareceu
    em plan_trials (nesta conta ou em outra, mesmo deletada). Usado na criação
    do checkout pra decidir se manda trial_period_days=30 ou cobra na hora.

    Sem telefone vinculado → inelegível, pois não há como aplicar a regra por
    número. Falha de banco levanta TrialEligibilityError: o checkout responde
    503 em vez de cobrar na hora ou conceder trial repetido no escuro.
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
                    return False
                cur.execute(
                    "select 1 from plan_trials where phone_hash = %s",
                    (row["phone_hash"],),
                )
                return cur.fetchone() is None
    except Exception as exc:
        logger.warning("is_trial_eligible_for_user falhou pro user %s", user_id, exc_info=True)
        raise TrialEligibilityError("Falha ao consultar a elegibilidade do trial.") from exc


def reset_trial_for_user(user_id: int) -> dict | None:
    """Apaga a trava de trial do TELEFONE desta conta e reancora a conta.

    Ação de admin (drill-down do painel): devolve a esta conta o direito ao
    trial de 15 dias. Tudo numa transação só, e o SELECT é `for update`: sem o
    lock de linha a transação seria READ COMMITTED pura, e uma troca de telefone
    concorrente (frontend/routes/settings.py reescreve phone_hash) commitando
    entre o SELECT e o DELETE faria isto apagar a trava do número ANTIGO e
    reportar sucesso com o número atual ainda travado. O `for update` faz a
    troca esperar ou o reset ler o hash já novo — nas duas ordens a trava
    apagada é a do telefone que a conta tem no fim.

    Apaga por UM phone_hash, o da própria conta, lido exatamente como
    is_trial_eligible_for_user lê. NUNCA por phone_lookup_candidates: as
    variantes de nono dígito produzem hash que pode ser de OUTRA conta, e
    apagar a trava dela seria vazar a ação entre usuários. phone_hash é único
    em auth_accounts e PK em plan_trials, então um hash é de no máximo uma conta.

    Limpar `trial_started_at` é parte do conserto, não enfeite:
    claim_trial_for_user só grava a âncora quando ela é nula ou mais nova que o
    started_at novo, então uma âncora velha faria o trial seguinte nascer
    vencido (get_trial_status calcula o fim pela data antiga e devolve
    days_left=0 enquanto a Stripe dá os 15 dias). `trial_downsell_sent_at` vai
    no mesmo UPDATE pro funil de downsell poder ser testado de novo.

    Não fala com a Stripe: assinatura viva continua onde está (quem recusa esse
    caso é o chamador). Retorna None se a conta não existe; `phone: False`
    quando não há telefone vinculado — aí não há trava a remover e nada é escrito.

    Devolve as DUAS datas, porque elas não são a mesma e a auditoria precisa da
    que se perde: `previous_lock_started_at` é o started_at da linha de
    plan_trials destruída (quando o trial foi queimado NAQUELE telefone) e
    `previous_started_at` é a âncora da conta. Numa trava herdada — conta
    anterior apagada, plan_trials.user_id nulo por ON DELETE SET NULL, número
    recadastrado — a âncora é nula e só a primeira registra o que existia.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select phone_hash, trial_started_at from auth_accounts "
                "where user_id = %s for update",
                (int(user_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            phone_hash = row.get("phone_hash")
            deleted = 0
            lock_started_at = None
            if phone_hash:
                cur.execute(
                    "delete from plan_trials where phone_hash = %s returning started_at",
                    (phone_hash,),
                )
                # phone_hash é PK em plan_trials: no máximo uma linha.
                apagada = cur.fetchone()
                deleted = 1 if apagada else 0
                lock_started_at = apagada.get("started_at") if apagada else None
                cur.execute(
                    """
                    update auth_accounts
                    set trial_started_at = null, trial_downsell_sent_at = null
                    where user_id = %s
                    """,
                    (int(user_id),),
                )
        conn.commit()
    # get_auth_user cacheia trial_started_at (db_support.py) — mesmo par que
    # claim_trial_for_user usa depois de escrever.
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)
    return {
        "phone": bool(phone_hash),
        "deleted": deleted,
        "previous_started_at": row.get("trial_started_at"),
        "previous_lock_started_at": lock_started_at,
    }


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
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)


# ── Inadimplência de cartão: o relógio da carência de 7 dias ─────────────────
# Ver core/services/billing_dunning para a regra que LÊ estas colunas.


def claim_past_due_since(user_id: int) -> bool:
    """Carimba o início da inadimplência. True se foi ESTA chamada que carimbou.

    A idempotência é SQL, não Python (decisão do dono): UMA instrução com
    `past_due_since is null` no `where`, sem read-modify-write. A Stripe manda
    um `invoice.payment_failed` por smart retry, e reentrega o mesmo evento em
    cima de 5xx — o relógio NÃO pode reiniciar em nenhum dos dois casos.

    O `rowcount` sai de graça e diz se foi esta chamada que abriu o ciclo (um
    `coalesce` no `set` seria idempotente também, mas o `RETURNING` veria o
    valor novo e não diria isso). **NÃO o use como dedupe de e-mail**: ele já
    foi a chave do "seu pagamento falhou" e o carimbo COMMITA antes do envio,
    então SMTP fora do ar na 1ª entrega calava o ciclo inteiro (medido: 1ª
    entrega + 3 reentregas da Stripe = 0 e-mails). A dedupe de e-mail mora no
    `_fire_email` do webhook, que grava a chave DEPOIS do envio.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set past_due_since = now()"
                " where user_id = %s and past_due_since is null",
                (int(user_id),),
            )
            carimbou = cur.rowcount == 1
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)
    return carimbou


def clear_past_due_since(user_id: int) -> None:
    """Zera o relógio: pagou, cancelou ou a assinatura morreu. Desbloqueio
    automático — o gate volta a passar na próxima mensagem."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update auth_accounts set past_due_since = null where user_id = %s",
                (int(user_id),),
            )
        conn.commit()
    from db_support import invalidate_auth_user_cache
    invalidate_auth_user_cache(user_id)


def list_dunning_warning_candidates(grace_days: int = 7) -> list[dict]:
    """Contas na VÉSPERA do corte por inadimplência (aviso de 1 dia antes).

    Janela de 1 dia — `past_due_since` entre `grace_days` e `grace_days - 1`
    dias atrás — no mesmo desenho do `_check_trial_ending`: o tick roda a cada
    24 h, então cada conta entra na janela exatamente uma vez por ciclo de
    inadimplência.

    O funil grosso é SQL (lista de três, relógio presente, e-mail presente,
    opt-out); o filtro fino que precisa de Python (allowlist, grant pix/admin
    vigente) é do chamador, como em `list_trial_downsell_candidates`.
    """
    from core.services.billing_dunning import PAST_DUE_PAYMENT_STATUSES
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select user_id, email, email_enc
                from auth_accounts
                where past_due_since is not null
                  and lower(coalesce(last_payment_status, '')) = any(%s)
                  and coalesce(engagement_opt_out, false) = false
                  and email is not null and email <> ''
                  and past_due_since <= now() - make_interval(days => %s)
                  and past_due_since >  now() - make_interval(days => %s)
                """,
                (list(PAST_DUE_PAYMENT_STATUSES),
                 int(grace_days) - 1, int(grace_days)),
            )
            return [dict(r) for r in cur.fetchall() or []]


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
