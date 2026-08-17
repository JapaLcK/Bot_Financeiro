import os
import sys
import uuid
import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-for-pytest-only-32-bytes")

# Cifragem PII (core/crypto.py): testes precisam de chaves válidas.
# Geramos dinamicamente a Fernet pra não vazar uma chave fixa em CI.
from cryptography.fernet import Fernet as _Fernet  # noqa: E402
os.environ.setdefault("PII_ENCRYPTION_KEY", _Fernet.generate_key().decode())
os.environ.setdefault("PII_HASH_PEPPER", "test-pepper-for-pytest-only-must-be-32-chars-long")
# Não polui pii_access_log durante testes (cada decrypt registra uma row).
os.environ.setdefault("PII_AUDIT_DISABLED", "1")
# Escada de planos v2 lançada com default LIGADO (2026-08-06). A suíte foi
# escrita no mundo v1 (gates binários Free×Pro, mocks de is_pro), então os
# testes rodam com o freio puxado por padrão — os testes da escada
# (test_plan_tiers, test_of_trial_expiry, etc.) ligam com setenv("...", "1").
os.environ.setdefault("PLANS_V2_ENABLED", "0")

from db import init_db, ensure_user, get_conn



def _make_isolated_db(base_url: str) -> tuple[str, str] | None:
    """Cria um database só desta execução. Devolve (url_nova, nome) ou None
    se não for possível (sem permissão de CREATE DATABASE, por exemplo)."""
    import psycopg
    from urllib.parse import urlsplit, urlunsplit

    name = f"pytest_{uuid.uuid4().hex[:12]}"
    try:
        with psycopg.connect(base_url, autocommit=True) as conn:
            # Nome é gerado aqui (hex de uuid), não vem de fora — sem risco
            # de injeção. Aspas duplas porque CREATE DATABASE não aceita
            # parâmetro ligado.
            conn.execute(f'create database "{name}"')
    except Exception as exc:
        print(f"[conftest] sem isolamento de banco ({exc}); usando o DATABASE_URL original")
        return None
    parts = urlsplit(base_url)
    return urlunsplit(parts._replace(path=f"/{name}")), name


def _drop_isolated_db(base_url: str, name: str) -> None:
    import psycopg

    from db.connection import close_pool

    close_pool()  # solta as conexões, senão o DROP é recusado
    try:
        with psycopg.connect(base_url, autocommit=True) as conn:
            # FORCE derruba conexões remanescentes (Postgres 13+).
            conn.execute(f'drop database if exists "{name}" with (force)')
    except Exception as exc:
        print(f"[conftest] não consegui remover o database {name}: {exc}")


@pytest.fixture(scope="session", autouse=True)
def _init_schema():
    """Prepara o schema — num database exclusivo desta execução.

    Sem o isolamento, duas execuções simultâneas da suíte contra o mesmo
    DATABASE_URL se destroem: a fixture `_auto_cleanup_orphan_users` abaixo
    apaga QUALQUER usuário que apareça em `users` durante um teste, e não
    tem como distinguir os da outra execução. O resultado são falhas em
    testes sem relação com a mudança, que não reproduzem sozinhas.

    Custa ~1s (createdb + init_db). Para inspecionar o banco depois de rodar,
    desligue com PYTEST_DB_ISOLATION=0.
    """
    base_url = os.getenv("DATABASE_URL")
    if not base_url:
        raise RuntimeError("Faltou DATABASE_URL no ambiente para rodar os testes.")

    isolated = None
    if os.getenv("PYTEST_DB_ISOLATION", "1") != "0":
        isolated = _make_isolated_db(base_url)

    if isolated:
        url, name = isolated
        # Trocar antes de qualquer conexão: o pool de db/connection.py é lazy
        # e só lê DATABASE_URL quando abre.
        os.environ["DATABASE_URL"] = url
        init_db()
        yield
        os.environ["DATABASE_URL"] = base_url
        _drop_isolated_db(base_url, name)
    else:
        init_db()
        yield


@pytest.fixture(autouse=True)
def _block_outbound_network(monkeypatch):
    """Kill switch de rede para testes — bloqueia qualquer chamada externa.

    Sem isso, register_auth_user disparava welcome via Resend, o ai_router
    podia chamar OpenAI, ipgeo batia em ipapi.co, etc. Cada execucao do
    pytest gastava quota de APIs pagas e poluia dashboards de provedores.

    Cobre:
      - send_email (Resend)
      - requests.get/post/put/delete/request (sync HTTP — ipapi, WhatsApp Cloud, CDI)
      - httpx.get/post/put/delete (sync) e httpx.AsyncClient.request (async — Pluggy, Google OAuth)
      - OpenAI client (categorizacao, intent, greeting, media)

    Tests que precisam observar/simular respostas devem usar patch local
    com mock especifico (e.g. mock requests.get retornando objeto fake).
    """
    # E-mail
    monkeypatch.setattr("core.services.email_service.send_email", lambda *a, **kw: True)

    # Geolocation kill switch (env var ja suportada em core/services/ipgeo.py)
    monkeypatch.setenv("IPGEO_DISABLED", "1")

    def _blocked_call(*args, **kwargs):
        raise RuntimeError(
            "Outbound HTTP blocked in tests. Mock the call locally if you "
            "need to simulate the response."
        )

    # requests (sync)
    monkeypatch.setattr("requests.get", _blocked_call)
    monkeypatch.setattr("requests.post", _blocked_call)
    monkeypatch.setattr("requests.put", _blocked_call)
    monkeypatch.setattr("requests.delete", _blocked_call)
    monkeypatch.setattr("requests.request", _blocked_call)

    # httpx (sync)
    monkeypatch.setattr("httpx.get", _blocked_call)
    monkeypatch.setattr("httpx.post", _blocked_call)
    monkeypatch.setattr("httpx.put", _blocked_call)
    monkeypatch.setattr("httpx.delete", _blocked_call)

    # httpx async client — qualquer .request bloqueia
    import httpx as _httpx
    async def _blocked_async(*args, **kwargs):
        raise RuntimeError(
            "Outbound HTTP (async) blocked in tests. Mock the call locally."
        )
    monkeypatch.setattr(_httpx.AsyncClient, "request", _blocked_async, raising=False)
    monkeypatch.setattr(_httpx.AsyncClient, "get", _blocked_async, raising=False)
    monkeypatch.setattr(_httpx.AsyncClient, "post", _blocked_async, raising=False)

    # OpenAI — qualquer atributo do client levanta
    class _FakeOpenAIClient:
        def __getattr__(self, name):
            raise RuntimeError(
                f"OpenAI client blocked in tests (acessou .{name}). "
                "Mock the call locally."
            )
    monkeypatch.setattr("openai.OpenAI", lambda *a, **kw: _FakeOpenAIClient())


def _cleanup_user(user_id: int):
    """Apaga um user e todas as suas dependencias (NO ACTION + CASCADE)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Tabelas com on delete NO ACTION precisam ser apagadas a mao
            cur.execute("delete from credit_transactions where user_id = %s", (user_id,))
            cur.execute("delete from credit_bills where user_id = %s", (user_id,))
            cur.execute("delete from credit_cards where user_id = %s", (user_id,))
            cur.execute("delete from launches where user_id = %s", (user_id,))
            cur.execute("delete from pockets where user_id = %s", (user_id,))
            cur.execute("delete from investments where user_id = %s", (user_id,))
            cur.execute("delete from accounts where user_id = %s", (user_id,))
            # users.id cascateia o resto (auth_accounts, user_identities, etc.)
            cur.execute("delete from users where id = %s", (user_id,))
        conn.commit()


def _all_user_ids() -> set[int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from users")
            return {row["id"] for row in cur.fetchall()}


@pytest.fixture(autouse=True)
def _auto_cleanup_orphan_users():
    """Salva quem ja existia em `users` antes do teste e apaga qualquer
    novo registro depois — pega ids secundarios criados manualmente
    dentro do teste (ex.: ensure_user(stale_wa_uid)) e os criados via
    confirm_email_verification, que nao usam o fixture `user_id`.

    Roda em todo teste (autouse) pra evitar leaks que quebram testes
    seguintes via FK accounts_user_id_fkey.
    """
    before = _all_user_ids()
    yield
    after = _all_user_ids()
    for orphan in (after - before):
        try:
            _cleanup_user(orphan)
        except Exception:
            # Se ainda assim falhar, silencia — o teste em si ja terminou.
            pass


@pytest.fixture()
def user_id():
    uid = int(uuid.uuid4().int % 10_000_000_000)  # bigint ok
    ensure_user(uid)
    yield uid
    _cleanup_user(uid)


@pytest.fixture()
def pro_user_id(user_id: int):
    """user_id já promovido para plano Pro — use em testes que precisam criar
    múltiplas caixinhas/cartões ou exercem features Pro.

    Garante uma row em auth_accounts (necessária pra is_pro() ler o plano)
    e seta plan='pro'. plan_expires_at=None significa "ilimitado".
    """
    import uuid as _uuid
    from db.connection import get_conn
    fake_email = f"pro-{_uuid.uuid4().hex[:8]}@test.local"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from auth_accounts where user_id = %s limit 1", (user_id,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "update auth_accounts set plan='pro', plan_expires_at=null where user_id = %s",
                    (user_id,),
                )
            else:
                cur.execute(
                    "insert into auth_accounts(user_id, email, password_hash, plan) values (%s, %s, 'x', 'pro')",
                    (user_id, fake_email),
                )
        conn.commit()
    return user_id
