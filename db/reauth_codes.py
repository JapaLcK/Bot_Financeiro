"""
db/reauth_codes.py — código de reautenticação por e-mail (exclusão de conta).

Fonte única do "modo" de reautenticação da conta (`MODO_SQL`) e do ciclo do
código: `emitir` (upsert, só o último vale) e `confere_e_consome` (no cursor de
quem chama). Só o HMAC do código fica no banco (`account_reauth_codes`).
"""
from __future__ import annotations

import secrets

from core.crypto import hash_pii
from core.pg_text import tem_veneno

from .connection import get_conn
from .google_auth import SEM_CREDENCIAL_SQL

EXCLUIR = "account_delete"
MINUTOS = 15
MAX_TENTATIVAS = 5

# Sobre a linha `auth_accounts a`. `senha`: tem senha (`''` conta como sem, igual a
# `auth_account_has_password`). `sem_credencial`: sem senha e sem identidade
# Google/Apple (e-mail NÃO provado: não se manda código a ele). `codigo`: o resto.
MODO_SQL = (
    "case when a.password_hash is not null and a.password_hash <> '' then 'senha' "
    f"when {SEM_CREDENCIAL_SQL} then 'sem_credencial' else 'codigo' end"
)


def modo(user_id: int) -> str | None:
    """'senha' | 'codigo' | 'sem_credencial'; None se não há conta de login."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"select ({MODO_SQL}) as modo from auth_accounts a where a.user_id = %s limit 1",
            (int(user_id),),
        )
        row = cur.fetchone()
    return row["modo"] if row else None


def _hash(user_id: int, purpose: str, code: str) -> str:
    # HMAC com pepper: um dump do banco sozinho não quebra os 10^6 códigos.
    return hash_pii(f"{user_id}:{purpose}:{code}")


def emitir(user_id: int, purpose: str) -> str:
    """Gera um código de 6 dígitos (substitui o anterior) e o devolve em claro.

    O código em claro só serve para quem envia o e-mail: nunca em resposta nem log.
    """
    code = f"{secrets.randbelow(10**6):06d}"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into account_reauth_codes
              (user_id, purpose, code_hash, expires_at, attempts, used_at, created_at)
            values (%s, %s, %s, now() + make_interval(mins => %s), 0, null, now())
            on conflict (user_id, purpose) do update
               set code_hash = excluded.code_hash, expires_at = excluded.expires_at,
                   attempts = 0, used_at = null, created_at = now()
            """,
            (int(user_id), purpose, _hash(user_id, purpose, code), MINUTOS),
        )
        conn.commit()
    return code


def confere_e_consome(cur, user_id: int, purpose: str, code: str) -> str:
    """'ok' | 'invalido' | 'expirado', no cursor de quem chama (o commit é dele).

    Cada chamada gasta uma tentativa; 'ok' marca o código como usado. Sem linha
    utilizável (inexistente, usado, vencido ou esgotado) é 'expirado'.
    """
    code = (code or "").strip().replace(" ", "")
    # NUL/surrogate não chega ao hash (UnicodeEncodeError) nem ao banco: conta
    # como tentativa errada, sem 500. O hash "x" nunca bate com um HMAC real.
    h = "x" if (not code or tem_veneno(code)) else _hash(user_id, purpose, code)
    cur.execute(
        """
        update account_reauth_codes
           set attempts = attempts + 1,
               used_at = case when code_hash = %(h)s then now() else used_at end
         where user_id = %(u)s and purpose = %(p)s
           and used_at is null and expires_at > now() and attempts < %(max)s
        returning (used_at is not null) as ok
        """,
        {"h": h, "u": int(user_id), "p": purpose, "max": MAX_TENTATIVAS},
    )
    row = cur.fetchone()
    if not row:
        return "expirado"
    return "ok" if row["ok"] else "invalido"
