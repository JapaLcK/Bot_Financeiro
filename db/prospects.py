"""
db/prospects.py — Atribuição de cadastro vindo do funil de prospecção (lead engine).

Fluxo (espelho enxuto de db/affiliates.py, sem comissão):
  1. O lead engine (repo próprio) gera o código e divulga o link /i/{code}.
  2. Visitante clica → cookie prospect_code (30 dias) → ao criar conta,
     record_prospect_referral() grava a atribuição (1 código por usuário,
     primeiro ganha). Não há pré-registro de código: lixo de crawler que
     virar cadastro é linha morta inofensiva.
  3. O lead engine consulta o resultado via POST /api/prospect/status
     (list_prospect_status) — sem PII, só {code, registered_at, active}.
"""
import re

from .connection import get_conn

PROSPECT_COOKIE_MAX_AGE_DAYS = 30   # janela de atribuição do link

_CODE_RE = re.compile(r"^[A-Za-z0-9]{4,20}$")


def is_valid_prospect_code(code: str) -> bool:
    # fullmatch, não match: com `$` o re aceita "\n" final ("abc12345\n"
    # passaria e o cookie sairia com %0A).
    return bool(_CODE_RE.fullmatch(code or ""))


def record_prospect_referral(code: str, referred_user_id: int) -> bool:
    """Atribui o usuário recém-criado ao código de prospecção.

    Silenciosamente não faz nada (return False) se o código é malformado ou o
    usuário já tem atribuição (primeiro ganha) — nunca pode quebrar o cadastro.
    """
    # Sem strip aqui: o caller (_apply_prospect_attribution) já normaliza, e
    # "abc12345\n" tem de ser rejeitado, não consertado em silêncio.
    if not is_valid_prospect_code(code):
        return False
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into prospect_referrals(code, referred_user_id)
                values (%s, %s)
                on conflict (referred_user_id) do nothing
                """,
                (code, int(referred_user_id)),
            )
            conn.commit()
            return cur.rowcount > 0


def list_prospect_status(codes: list[str]) -> list[dict]:
    """Status dos códigos que viraram cadastro: {code, registered_at, active}.

    active = conta pagante OU em trial (account_status derivado ∈ paying/trial —
    decisão do dono, 2026-08-31). Só devolve códigos encontrados. SEM e-mail,
    SEM user_id, SEM PII: a resposta vai para um serviço externo.

    Code repetido (lead compartilhou o link): 1 linha por code, e vale o
    PRIMEIRO cadastro (distinct on + created_at asc).
    """
    codes = [c for c in (codes or []) if is_valid_prospect_code(c)]
    if not codes:
        return []
    # §0.7 uma fonte de verdade: o derivado de status vive no admin_dashboard.
    # Import tardio (padrão do repo) — o módulo puxa stripe/config no import.
    from core.admin_dashboard import _ACCOUNT_STATUS_SQL
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                select distinct on (p.code)
                       p.code,
                       p.created_at as registered_at,
                       coalesce({_ACCOUNT_STATUS_SQL} in ('paying', 'trial'), false) as active
                  from prospect_referrals p
                  left join auth_accounts a on a.user_id = p.referred_user_id
                 where p.code = any(%s)
                 order by p.code, p.created_at asc, p.id asc
                """,
                (codes,),
            )
            return [
                {
                    "code": row["code"],
                    "registered_at": row["registered_at"].isoformat(),
                    "active": bool(row["active"]),
                }
                for row in cur.fetchall()
            ]
