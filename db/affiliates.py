"""
db/affiliates.py — Programa de afiliados.

Fluxo:
  1. Admin cria o afiliado (create_affiliate) → gera código único do link /r/{code}.
  2. Visitante clica no link → cookie ref_code (30 dias) → ao criar conta,
     record_referral() grava a atribuição (1 afiliado por usuário, primeiro ganha).
  3. Webhook Stripe invoice.paid (valor > 0) chama record_commission_for_invoice():
     comissão = commission_bps da fatura, APENAS na primeira cobrança paga do
     indicado — renovações não geram nada (mudou de recorrente pra primeira
     cobrança em 2026-07-23). Idempotente por stripe_invoice_id, só acumula se
     o afiliado estiver status='active'.
  4. Comissão fica 'pending' com carência (COMMISSION_HOLD_DAYS) antes de virar
     sacável; saque via request_payout() (mínimo MIN_PAYOUT_CENTS) trava as
     comissões no payout; admin paga por Pix fora do sistema e marca pago.
"""
import re
import secrets
from datetime import datetime, timedelta, timezone

from .connection import get_conn

DEFAULT_COMMISSION_BPS = 1000       # 10%
COMMISSION_HOLD_DAYS = 30           # carência anti-estorno antes de ficar sacável
MIN_PAYOUT_CENTS = 50_00            # saque mínimo R$ 50
REF_COOKIE_MAX_AGE_DAYS = 30        # janela de atribuição do link

# Sem 0/O/1/I/L pra código legível em post/print; case-insensitive no lookup.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_RE = re.compile(r"^[A-Za-z0-9]{4,20}$")


def _now():
    return datetime.now(timezone.utc)


def _normalize_code(code: str) -> str:
    return (code or "").strip().upper()


def _generate_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))


# ─── Afiliado ─────────────────────────────────────────────────────────────────

def create_affiliate(user_id: int, code: str | None = None,
                     commission_bps: int = DEFAULT_COMMISSION_BPS) -> dict:
    """Cria (ou retorna, se já existe) o afiliado do user_id."""
    if code is not None:
        code = _normalize_code(code)
        if not _CODE_RE.match(code):
            raise ValueError("Código inválido: use 4-20 letras/números.")
    if not (0 < int(commission_bps) <= 10_000):
        raise ValueError("commission_bps deve estar entre 1 e 10000.")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from affiliates where user_id = %s", (user_id,))
            existing = cur.fetchone()
            if existing:
                return existing
            for _ in range(5):
                candidate = code or _generate_code()
                try:
                    cur.execute(
                        """
                        insert into affiliates(user_id, code, commission_bps)
                        values (%s, %s, %s)
                        returning *
                        """,
                        (user_id, candidate, int(commission_bps)),
                    )
                    conn.commit()
                    return cur.fetchone()
                except Exception:
                    conn.rollback()
                    if code:  # código escolhido à mão já existe → erro claro
                        raise ValueError(f"Código '{code}' já está em uso.")
            raise RuntimeError("Não foi possível gerar código único de afiliado.")


def get_affiliate_by_user(user_id: int) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from affiliates where user_id = %s", (int(user_id),))
            return cur.fetchone()


def get_affiliate_by_code(code: str) -> dict | None:
    code = _normalize_code(code)
    if not code:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select * from affiliates where upper(code) = %s", (code,))
            return cur.fetchone()


def set_affiliate_status(affiliate_id: int, status: str) -> bool:
    """'active' volta a acumular comissão; 'disabled' para de acumular (saldo já
    acumulado continua visível e sacável)."""
    if status not in ("active", "disabled"):
        raise ValueError("status deve ser 'active' ou 'disabled'.")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update affiliates set status = %s where id = %s",
                (status, int(affiliate_id)),
            )
            conn.commit()
            return cur.rowcount > 0


def set_affiliate_pix_key(affiliate_id: int, pix_key_hash: str | None,
                          pix_key_enc: str | None) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update affiliates set pix_key_hash = %s, pix_key_enc = %s where id = %s",
                (pix_key_hash, pix_key_enc, int(affiliate_id)),
            )
            conn.commit()


# ─── Atribuição (referral) ────────────────────────────────────────────────────

def record_referral(code: str, referred_user_id: int) -> bool:
    """Atribui o usuário recém-criado ao afiliado do código.

    Silenciosamente não faz nada (return False) se: código não existe, afiliado
    desativado, auto-indicação, ou usuário já atribuído a alguém — nunca pode
    quebrar o fluxo de cadastro.
    """
    affiliate = get_affiliate_by_code(code)
    if not affiliate or affiliate["status"] != "active":
        return False
    if int(affiliate["user_id"]) == int(referred_user_id):
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into affiliate_referrals(affiliate_id, referred_user_id, code_used)
                values (%s, %s, %s)
                on conflict (referred_user_id) do nothing
                """,
                (int(affiliate["id"]), int(referred_user_id), _normalize_code(code)),
            )
            conn.commit()
            return cur.rowcount > 0


def get_referral_for_user(referred_user_id: int) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select * from affiliate_referrals where referred_user_id = %s",
                (int(referred_user_id),),
            )
            return cur.fetchone()


# ─── Comissão ─────────────────────────────────────────────────────────────────

def record_commission_for_invoice(referred_user_id: int, stripe_invoice_id: str,
                                  invoice_amount_cents: int) -> dict | None:
    """Gera a comissão da PRIMEIRA cobrança paga do usuário indicado.

    Retorna a linha criada, ou None se: usuário não tem afiliado, afiliado
    desativado, valor <= 0, fatura já comissionada (idempotência), ou o
    indicado já gerou comissão antes (renovação — só a 1ª cobrança conta).
    """
    if not stripe_invoice_id or int(invoice_amount_cents) <= 0:
        return None
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select r.affiliate_id, a.commission_bps
                  from affiliate_referrals r
                  join affiliates a on a.id = r.affiliate_id
                 where r.referred_user_id = %s
                   and a.status = 'active'
                """,
                (int(referred_user_id),),
            )
            row = cur.fetchone()
            if not row:
                return None
            # Comissão SÓ na primeira cobrança paga do indicado (decisão do Lucas
            # em 2026-07-23 — antes era recorrente em toda fatura). Se já existe
            # qualquer comissão desse usuário, as renovações não geram mais nada.
            # Conta inclusive comissão estornada: se a 1ª foi reembolsada, a
            # renovação seguinte não deve virar uma nova comissão.
            cur.execute(
                "select 1 from affiliate_commissions where referred_user_id = %s limit 1",
                (int(referred_user_id),),
            )
            if cur.fetchone():
                return None
            amount_cents = int(invoice_amount_cents) * int(row["commission_bps"]) // 10_000
            if amount_cents <= 0:
                return None
            cur.execute(
                """
                insert into affiliate_commissions(
                    affiliate_id, referred_user_id, stripe_invoice_id,
                    invoice_amount_cents, amount_cents, available_at)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (stripe_invoice_id) do nothing
                returning *
                """,
                (
                    int(row["affiliate_id"]),
                    int(referred_user_id),
                    stripe_invoice_id,
                    int(invoice_amount_cents),
                    amount_cents,
                    _now() + timedelta(days=COMMISSION_HOLD_DAYS),
                ),
            )
            created = cur.fetchone()
            conn.commit()
            return created


def get_affiliate_stats(affiliate_id: int) -> dict:
    """Saldos em centavos por bucket + total de indicados."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select count(*) as n from affiliate_referrals where affiliate_id = %s",
                (int(affiliate_id),),
            )
            referrals = int(cur.fetchone()["n"])
            cur.execute(
                """
                select
                  coalesce(sum(amount_cents) filter (
                    where status = 'pending' and payout_id is null and available_at >  now()), 0) as held_cents,
                  coalesce(sum(amount_cents) filter (
                    where status = 'pending' and payout_id is null and available_at <= now()), 0) as available_cents,
                  coalesce(sum(amount_cents) filter (
                    where status = 'pending' and payout_id is not null), 0) as requested_cents,
                  coalesce(sum(amount_cents) filter (where status = 'paid'), 0) as paid_cents,
                  count(*) filter (where status <> 'reversed') as commission_count
                  from affiliate_commissions
                 where affiliate_id = %s
                """,
                (int(affiliate_id),),
            )
            sums = cur.fetchone()
            return {
                "referrals": referrals,
                "held_cents": int(sums["held_cents"]),            # em carência
                "available_cents": int(sums["available_cents"]),  # sacável agora
                "requested_cents": int(sums["requested_cents"]),  # em saque pendente
                "paid_cents": int(sums["paid_cents"]),
                "commission_count": int(sums["commission_count"]),
            }


def list_affiliate_commissions(affiliate_id: int, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, invoice_amount_cents, amount_cents, status,
                       available_at, payout_id, created_at
                  from affiliate_commissions
                 where affiliate_id = %s
                 order by created_at desc
                 limit %s
                """,
                (int(affiliate_id), int(limit)),
            )
            return cur.fetchall()


def reverse_commission(commission_id: int) -> bool:
    """Estorna uma comissão (refund/chargeback da fatura). Só enquanto pending
    e fora de um saque em andamento."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update affiliate_commissions
                   set status = 'reversed'
                 where id = %s and status = 'pending' and payout_id is null
                """,
                (int(commission_id),),
            )
            conn.commit()
            return cur.rowcount > 0


# ─── Saque ────────────────────────────────────────────────────────────────────

def request_payout(affiliate_id: int, pix_key_enc: str | None = None) -> dict:
    """Cria um pedido de saque com TODO o saldo disponível, travando as comissões.

    Levanta ValueError se: já existe saque em aberto, ou saldo < MIN_PAYOUT_CENTS.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id from affiliate_payouts
                 where affiliate_id = %s and status = 'requested'
                 limit 1
                """,
                (int(affiliate_id),),
            )
            if cur.fetchone():
                raise ValueError("Você já tem um saque em análise. Aguarde o pagamento.")

            cur.execute(
                """
                select id, amount_cents
                  from affiliate_commissions
                 where affiliate_id = %s and status = 'pending'
                   and payout_id is null and available_at <= now()
                 for update
                """,
                (int(affiliate_id),),
            )
            rows = cur.fetchall()
            total = sum(int(r["amount_cents"]) for r in rows)
            if total < MIN_PAYOUT_CENTS:
                raise ValueError(
                    f"Saldo disponível (R$ {total / 100:.2f}) abaixo do mínimo "
                    f"de R$ {MIN_PAYOUT_CENTS / 100:.0f} para saque."
                )

            cur.execute(
                """
                insert into affiliate_payouts(affiliate_id, amount_cents, pix_key_enc)
                values (%s, %s, %s)
                returning *
                """,
                (int(affiliate_id), total, pix_key_enc),
            )
            payout = cur.fetchone()
            cur.execute(
                "update affiliate_commissions set payout_id = %s where id = any(%s)",
                (int(payout["id"]), [int(r["id"]) for r in rows]),
            )
            conn.commit()
            return payout


def list_affiliate_payouts(affiliate_id: int, limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id, amount_cents, status, requested_at, paid_at, note
                  from affiliate_payouts
                 where affiliate_id = %s
                 order by requested_at desc
                 limit %s
                """,
                (int(affiliate_id), int(limit)),
            )
            return cur.fetchall()


def mark_payout_paid(payout_id: int, note: str | None = None) -> bool:
    """Admin pagou o Pix fora do sistema → marca payout e comissões como pagos."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update affiliate_payouts
                   set status = 'paid', paid_at = now(), note = coalesce(%s, note)
                 where id = %s and status = 'requested'
                """,
                (note, int(payout_id)),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return False
            cur.execute(
                "update affiliate_commissions set status = 'paid' where payout_id = %s",
                (int(payout_id),),
            )
            conn.commit()
            return True


def reject_payout(payout_id: int, note: str | None = None) -> bool:
    """Rejeita o saque e devolve as comissões pro saldo disponível."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                update affiliate_payouts
                   set status = 'rejected', note = coalesce(%s, note)
                 where id = %s and status = 'requested'
                """,
                (note, int(payout_id)),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return False
            cur.execute(
                "update affiliate_commissions set payout_id = null where payout_id = %s",
                (int(payout_id),),
            )
            conn.commit()
            return True


# ─── Admin ────────────────────────────────────────────────────────────────────

def admin_list_affiliates() -> list[dict]:
    """Afiliados + email cifrado (decrypt fica no caller, com audit) + saldos."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select a.id, a.user_id, a.code, a.status, a.commission_bps,
                       a.pix_key_enc, a.created_at,
                       aa.email_enc,
                       (select count(*) from affiliate_referrals r
                         where r.affiliate_id = a.id) as referrals,
                       coalesce((select sum(c.amount_cents) from affiliate_commissions c
                         where c.affiliate_id = a.id and c.status = 'pending'), 0) as owed_cents,
                       coalesce((select sum(c.amount_cents) from affiliate_commissions c
                         where c.affiliate_id = a.id and c.status = 'paid'), 0) as paid_cents
                  from affiliates a
                  left join auth_accounts aa on aa.user_id = a.user_id
                 order by a.created_at desc
                """
            )
            return cur.fetchall()


def admin_list_payouts(status: str | None = None, limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select p.*, a.code, a.user_id, aa.email_enc
                  from affiliate_payouts p
                  join affiliates a on a.id = p.affiliate_id
                  left join auth_accounts aa on aa.user_id = a.user_id
                 where (%s::text is null or p.status = %s)
                 order by p.requested_at desc
                 limit %s
                """,
                (status, status, int(limit)),
            )
            return cur.fetchall()
