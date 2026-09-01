"""Cache TTL do get_auth_user (O1-5 da auditoria de performance).

Uma abertura de dashboard lia auth_accounts ~17×. O cache de 10 s corta isso;
a invalidação nos writers garante que mudança de plano (dinheiro/acesso pago)
reflete IMEDIATAMENTE, sem esperar o TTL.
"""
import db
import db_support
from db.connection import get_conn


class _CountingGetConn:
    """get_conn instrumentado: conta quantas vezes o impl foi ao banco."""
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return get_conn()


def _mk_auth(uid: int, plan: str = "free") -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into auth_accounts(user_id, email, password_hash, plan) "
                "values (%s, %s, 'x', %s)",
                (uid, f"cache-{uid}@test.local", plan),
            )
        conn.commit()


def _rm_auth(uid: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from auth_accounts where user_id = %s", (uid,))
        conn.commit()
    db_support.invalidate_auth_user_cache(uid)


def test_segunda_leitura_vem_do_cache_e_nao_do_banco(user_id):
    _mk_auth(user_id)
    try:
        contador = _CountingGetConn()
        primeira = db_support.get_auth_user_impl(contador, user_id)
        assert primeira is not None and primeira["plan"] == "free"
        assert contador.calls == 1

        # Positivo: leitura quente ⇒ 0 idas ao banco, MESMO conteúdo.
        segunda = db_support.get_auth_user_impl(contador, user_id)
        assert contador.calls == 1, "cache quente foi ao banco"
        assert segunda == primeira

        # Negativo (cache desligado via TTL=0, no MESMO estado verde):
        # cada leitura volta a bater no banco.
        original_ttl = db_support.AUTH_USER_CACHE_TTL_SECONDS
        db_support.AUTH_USER_CACHE_TTL_SECONDS = 0
        try:
            db_support.get_auth_user_impl(contador, user_id)
            db_support.get_auth_user_impl(contador, user_id)
        finally:
            db_support.AUTH_USER_CACHE_TTL_SECONDS = original_ttl
        assert contador.calls == 3, f"sem cache esperava 3 idas, veio {contador.calls}"
    finally:
        _rm_auth(user_id)


def test_update_de_plano_invalida_na_hora(user_id):
    """Writer → leitura seguinte reflete imediatamente (sem esperar TTL)."""
    _mk_auth(user_id, plan="free")
    try:
        assert db.get_auth_user(user_id)["plan"] == "free"  # popula o cache
        db.update_user_plan(user_id, "pro")                 # writer invalida
        assert db.get_auth_user(user_id)["plan"] == "pro", (
            "plan atualizado não refletiu — invalidação do writer falhou"
        )
    finally:
        _rm_auth(user_id)


def test_cache_e_por_usuario_sem_vazamento(user_id):
    """Regra dura de isolamento: o cache de A nunca serve B."""
    outro = user_id + 1
    db.ensure_user(outro)  # o autouse do conftest limpa o usuário novo depois
    _mk_auth(user_id, plan="pro")
    _mk_auth(outro, plan="free")
    try:
        a = db.get_auth_user(user_id)
        b = db.get_auth_user(outro)
        assert a["plan"] == "pro" and b["plan"] == "free"
        # Releitura quente: cada um continua com o próprio dado.
        assert db.get_auth_user(user_id)["email"] == f"cache-{user_id}@test.local"
        assert db.get_auth_user(outro)["email"] == f"cache-{outro}@test.local"
    finally:
        _rm_auth(user_id)
        _rm_auth(outro)


def test_mutacao_do_caller_nao_envenena_o_cache(user_id):
    """plan/plan_expires_at decidem acesso pago — caller que muta o dict
    devolvido não pode contaminar a próxima leitura."""
    _mk_auth(user_id, plan="free")
    try:
        primeiro = db.get_auth_user(user_id)
        primeiro["plan"] = "pro"  # caller malcomportado
        assert db.get_auth_user(user_id)["plan"] == "free"
    finally:
        _rm_auth(user_id)
