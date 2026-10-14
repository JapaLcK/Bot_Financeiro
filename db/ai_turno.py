"""db/ai_turno.py — um turno do chat IA por usuário por vez, em qualquer réplica.

O turno grava `assistant(tool_calls)` e só depois os `tool`; um segundo turno do
mesmo usuário (app, WhatsApp, `/ai/chat`) que gravasse o `user` no meio deixa um
histórico que a OpenAI recusa (400) em todo turno futuro. A trava é um advisory
lock de SESSÃO, numa conexão própria (fora do pool: ele fica preso o turno
inteiro, e o turno ainda precisa de conexões do pool), solto ao fechar a
conexão: exceção, cancelamento ou queda do processo.

Chave no formato de DOIS inteiros: é outro espaço de chaves que o
`pg_advisory_xact_lock(hashtext(...))` de `ai_chat._TRAVA_DO_HISTORICO` (um
bigint), então nunca colidem; e o turno só pega a do histórico dentro das
curtas, nunca espera esta com a outra na mão: sem deadlock.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

import psycopg

_CLASSE = 7101  # espaço de chaves do turno do chat IA


@contextmanager
def trava_do_turno(user_id: int, espera_s: float):
    """Rende True com a trava na mão; False se outro turno do usuário não acabou
    em `espera_s`. # ponytail: PgBouncer em modo transação quebraria o lock de
    sessão; hoje a conexão é direta (ver `escutar_banco`, que tem a mesma regra)."""
    conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=5, application_name="pigbank-ia-turno")
    try:
        conn.execute("select set_config('lock_timeout', %s, false)", (f"{int(espera_s * 1000)}ms",))
        try:
            conn.execute("select pg_advisory_lock(%s, hashtext(%s))", (_CLASSE, str(int(user_id))))
        except psycopg.errors.LockNotAvailable:
            yield False
            return
        yield True
    finally:
        conn.close()
