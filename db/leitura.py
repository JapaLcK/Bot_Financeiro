"""
db/leitura.py — credencial de visualização da conta (condomínio).

Segunda credencial do MESMO user_id: usuário + senha próprios, definidos pelo
dono. O login com ela abre sessão `somente_leitura` (auth_sessions). Toda troca
ou desativação sobe `versao` e revoga, na mesma transação, as sessões de leitor
e os refresh tokens delas. A checagem de versão em cada request mora no SQL de
`core.sessions.get_active_session` (e no refresh).
"""
from __future__ import annotations

import re

import bcrypt
import psycopg.errors

from .connection import get_conn

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{3,39}$")  # sem '@': nunca colide com e-mail
SENHA_MIN = 8
SENHA_MAX_BYTES = 72  # teto do bcrypt: acima disso hashpw levanta ValueError
# Hash descartável: login de usuário inexistente gasta o mesmo bcrypt (sem oráculo de tempo).
_HASH_FALSO = bcrypt.hashpw(b"x", bcrypt.gensalt()).decode()


class LeituraInvalida(ValueError):
    """Usuário ou senha fora da regra (a mensagem vai ao dono)."""


class UsernameEmUso(Exception):
    pass


def normalizar_usuario(usuario: str) -> str:
    u = (usuario or "").strip().lower()
    if not USERNAME_RE.fullmatch(u):
        raise LeituraInvalida("Usuário: 4 a 40 caracteres (letras, números, ponto, hífen ou sublinhado), sem @.")
    return u


def validar_senha(senha: str) -> None:
    if len(senha or "") < SENHA_MIN:
        raise LeituraInvalida(f"Senha deve ter pelo menos {SENHA_MIN} caracteres.")
    if len(senha.encode("utf-8")) > SENHA_MAX_BYTES:
        raise LeituraInvalida("Senha longa demais: use no máximo 72 caracteres (acentos e emojis contam como mais de um).")


def _revogar_sessoes(cur, user_id: int) -> None:
    cur.execute(
        "update auth_refresh_tokens set revoked_at = now() where revoked_at is null and session_jti in "
        "(select jti from auth_sessions where user_id = %s and somente_leitura)",
        (int(user_id),),
    )
    cur.execute(
        "update auth_sessions set revoked_at = now() where user_id = %s and somente_leitura and revoked_at is null",
        (int(user_id),),
    )


def obter(user_id: int) -> dict:
    """{ativo, usuario} da credencial do dono. Nunca devolve o hash."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select username, ativo from credenciais_leitura where user_id = %s", (int(user_id),))
        row = cur.fetchone()
    return {"ativo": bool(row and row["ativo"]), "usuario": row["username"] if row else None}


def definir(user_id: int, usuario: str, senha: str) -> int:
    """Cria ou troca a credencial (versão +1) e derruba as sessões de leitor. Devolve a versão."""
    u = normalizar_usuario(usuario)
    validar_senha(senha)
    h = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode()
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into credenciais_leitura (user_id, username, password_hash)
                values (%s, %s, %s)
                on conflict (user_id) do update
                   set username = excluded.username, password_hash = excluded.password_hash,
                       versao = credenciais_leitura.versao + 1, ativo = true, updated_at = now()
                returning versao
                """,
                (int(user_id), u, h),
            )
            versao = int(cur.fetchone()["versao"])
            _revogar_sessoes(cur, user_id)
            conn.commit()
    except psycopg.errors.UniqueViolation as exc:
        raise UsernameEmUso() from exc
    return versao


def desativar(user_id: int) -> bool:
    """Desativa (versão +1) e derruba as sessões de leitor. False se não havia credencial."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update credenciais_leitura set ativo = false, versao = versao + 1, updated_at = now() "
            "where user_id = %s returning 1",
            (int(user_id),),
        )
        achou = cur.fetchone() is not None
        _revogar_sessoes(cur, user_id)
        conn.commit()
    return achou


def verificar_login(usuario: str, senha: str) -> dict | None:
    """{user_id, versao} se usuário e senha batem com credencial ATIVA; senão None.

    Bloqueante (bcrypt): chamar por `asyncio.to_thread`. Senha acima de 72 bytes
    é recusada sem exceção. Usuário inexistente gasta o mesmo bcrypt.
    """
    u = (usuario or "").strip().lower()
    senha = senha or ""
    if len(senha.encode("utf-8")) > SENHA_MAX_BYTES:
        return None
    row = None
    if USERNAME_RE.fullmatch(u):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select user_id, password_hash, versao from credenciais_leitura where lower(username) = %s and ativo",
                (u,),
            )
            row = cur.fetchone()
    try:
        ok = bcrypt.checkpw(senha.encode("utf-8"), (row["password_hash"] if row else _HASH_FALSO).encode("utf-8"))
    except ValueError:
        ok = False
    return {"user_id": int(row["user_id"]), "versao": int(row["versao"])} if (row and ok) else None
