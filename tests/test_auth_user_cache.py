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


def test_teto_e_poda_do_cache(user_id):
    """Codex-4: o dict guarda PII decifrada — expirado tem que sair na poda
    oportunista e o tamanho tem que respeitar o teto."""
    import time
    _mk_auth(user_id)
    try:
        db_support.invalidate_auth_user_cache()
        now = time.monotonic()
        for i in range(300):  # expiradas (ts além do TTL)
            db_support._auth_user_cache[10_000_000 + i] = (now - 999, {"plan": "x"})
        for i in range(300):  # frescas
            db_support._auth_user_cache[20_000_000 + i] = (now, {"plan": "x"})

        db_support.get_auth_user_impl(_CountingGetConn(), user_id)  # insert ⇒ poda
        cache = db_support._auth_user_cache
        assert len(cache) <= db_support.AUTH_USER_CACHE_MAX, f"teto furado: {len(cache)}"
        assert all(u >= 20_000_000 or u == user_id for u in cache), "entrada expirada sobreviveu à poda"

        # Negativo: sem o teto (MAX=inf ⇒ poda nunca dispara), cresce sem limite.
        db_support.invalidate_auth_user_cache()
        original_max = db_support.AUTH_USER_CACHE_MAX
        db_support.AUTH_USER_CACHE_MAX = float("inf")
        try:
            for i in range(600):
                db_support._auth_user_cache[30_000_000 + i] = (now - 999, {})
            db_support.get_auth_user_impl(_CountingGetConn(), user_id)
            assert len(db_support._auth_user_cache) == 601, "sem a poda tinha de passar de 512"
        finally:
            db_support.AUTH_USER_CACHE_MAX = original_max
            db_support.invalidate_auth_user_cache()
    finally:
        _rm_auth(user_id)


def test_cache_no_teto_sob_concorrencia_nao_estoura():
    """Codex-7: get_auth_user roda em THREADS (asyncio.to_thread). Com o cache
    no teto, a poda e o evict ITERAM o dict — sem sincronização isso levanta
    'dictionary keys changed during iteration' numa request de auth legítima.

    O teto é elevado no teste de propósito: com 512 entradas a iteração dura
    microssegundos e a corrida quase nunca aparece; com o cache grande ela é
    reproduzível (medido: sem lock 1+ exceção, com lock 0).
    """
    import contextlib
    import threading
    import time as _time

    class _FakeConn:
        """get_conn leve: exercita o caminho do impl sem I/O de banco."""
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return self
        def execute(self, *a, **k): return None
        def fetchone(self): return {"email": "x@test.local", "plan": "free"}

    original_max = db_support.AUTH_USER_CACHE_MAX
    db_support.AUTH_USER_CACHE_MAX = 200_000
    parar = threading.Event()
    erros: list[str] = []
    try:
        db_support.invalidate_auth_user_cache()
        velho = _time.monotonic() - 999
        for i in range(db_support.AUTH_USER_CACHE_MAX):  # no teto, tudo expirado
            db_support._auth_user_cache[40_000_000 + i] = (velho, {"plan": "x"})

        # As threads usam SÓ as APIs reais (impl e invalidate) — é o que os
        # `asyncio.to_thread(get_auth_user, ...)` fazem. Escrita crua no dict
        # não existe em produção e criaria uma corrida que nenhum lock cobre.
        def lendo_e_inserindo(base):
            try:
                uid = base
                while not parar.is_set():
                    # user NOVO a cada giro: sempre cache MISS, então a poda
                    # (que ITERA o dict) roda em todo giro. Com id fixo o 2º
                    # giro seria hit e a seção crítica não seria exercitada.
                    uid += 1
                    db_support.get_auth_user_impl(lambda: _FakeConn(), uid)
            except BaseException as e:  # noqa: BLE001 — o teste É sobre a exceção
                erros.append(repr(e)[:120])

        def invalidando(base):
            try:
                n = 0
                while not parar.is_set():
                    n += 1
                    db_support.invalidate_auth_user_cache(base + (n % 5000))
            except BaseException as e:  # noqa: BLE001
                erros.append(repr(e)[:120])

        threads = [threading.Thread(target=lendo_e_inserindo, args=(60_000_000 + i * 1_000_000,))
                   for i in range(3)]
        threads += [threading.Thread(target=invalidando, args=(40_000_000 + i * 10_000,)) for i in range(2)]
        for t in threads:
            t.start()
        _time.sleep(3.0)
        parar.set()
        for t in threads:
            t.join(timeout=15)
        assert not erros, f"mutação concorrente estourou: {erros[:3]}"
    finally:
        parar.set()
        db_support.AUTH_USER_CACHE_MAX = original_max
        db_support.invalidate_auth_user_cache()


def test_ttl_conta_do_inicio_da_leitura(user_id):
    """Codex-9: leitor em cache-miss que publica uma linha que ficou velha
    durante a leitura não pode RENOVAR o TTL a partir do fim dela — senão o
    stale dura TTL + duração da leitura, mais que o teto documentado."""
    import time

    _mk_auth(user_id)
    try:
        db_support.invalidate_auth_user_cache()

        class _LentoConn:
            """Leitura que demora: simula o escritor commitando no meio."""
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def cursor(self): return self
            def execute(self, *a, **k): return None
            def fetchone(self):
                time.sleep(0.3)  # a linha "envelhece" aqui
                return {"email": "x@test.local", "plan": "free"}

        t0 = time.monotonic()
        db_support.get_auth_user_impl(lambda: _LentoConn(), user_id)
        ts = db_support._auth_user_cache[int(user_id)][0]
        assert ts <= t0 + 0.05, (
            f"TTL renovado a partir do FIM da leitura (ts-t0={ts - t0:.3f}s): "
            "o stale passaria do teto de 10 s"
        )
    finally:
        _rm_auth(user_id)


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
