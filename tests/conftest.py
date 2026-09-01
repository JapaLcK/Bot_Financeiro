import importlib.util
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


# ── Coleta: arquivos que dependem de `ofxparse` ──────────────────────────────
# Sem o pacote, estes 10 arquivos estouram no IMPORT e o pytest aborta a suíte
# inteira antes de rodar um teste — o resultado não é "alguns testes falham",
# é sinal nenhum, nem verde nem vermelho.
#
# A lista é FIXA de propósito. Gerá-la a partir dos erros de coleta ("ignore
# tudo que falhou ao importar") engoliria também um ImportError ou erro de
# sintaxe recém-introduzido, e o arquivo quebrado nunca mais rodaria.
#
# A condição é a ausência do pacote, não o erro: no CI o ofxparse está no
# requirements.txt, então nada aqui é ignorado e os 10 rodam normalmente.
_OFXPARSE_DEPENDENTES = [
    "test_audio_clarification.py",
    "test_audio_multi_launch_ask_value.py",
    "test_category_normalization.py",
    "test_full_handler_smoke.py",
    "test_handle_incoming_routing.py",
    "test_recurring_value.py",
    "test_split_audio_transactions.py",
    "test_whatsapp_confirmations.py",
    "test_whatsapp_daily_report.py",
    "test_whatsapp_simulation.py",
]

# Estes quatro NÃO estouram na coleta: importam `core.handle_incoming` ou
# `statement_import` DENTRO do corpo do teste, e a cadeia
# handle_incoming -> ofx_service -> ofx_import -> ofxparse só é percorrida
# quando o teste roda. O collect_ignore acima não os alcança, então sem este
# skip a suíte "de dependências reduzidas" ainda termina com 4 vermelhos que
# nada têm a ver com a mudança em revisão.
_OFXPARSE_IMPORT_TARDIO = [
    "tests/test_statement_import.py::test_attachment_detection",
    "tests/test_statement_import.py::test_import_statement_bytes_csv_idempotente",
    "tests/test_statement_import.py::test_import_statement_bytes_vazio_ou_grande",
    "tests/test_nlp_and_pending_flow.py::test_handle_incoming_clarification_tem_precedencia_sobre_fallback_ia",
]

# O alívio NÃO é automático: exige PYTEST_ALLOW_MISSING_OPTIONAL_DEPS=1.
#
# Detectar a ausência sozinho seria pior que o problema — se o ofxparse caísse
# do requirements.txt por engano, ou sumisse do CI, a suíte silenciaria 9
# arquivos e pularia 4 testes e passaria VERDE, enquanto `core.handle_incoming`
# estaria quebrado em produção. Um ambiente de dependências reduzidas é uma
# decisão consciente de quem o monta (o .claude/hooks/session-start.sh exporta
# a variável); a execução normal do dev e do CI continua estourando, que é o
# comportamento certo para dependência obrigatória faltando.
_DEPS_REDUZIDAS = os.getenv("PYTEST_ALLOW_MISSING_OPTIONAL_DEPS") == "1"

# find_spec em vez de `try: import`: `except ImportError` também captura um
# ImportError levantado DE DENTRO de um ofxparse instalado (dependência interna
# quebrada, por exemplo) e trataria pacote defeituoso como pacote ausente,
# escondendo justamente o que precisa aparecer.
_TEM_OFXPARSE = importlib.util.find_spec("ofxparse") is not None

collect_ignore = []
if _DEPS_REDUZIDAS and not _TEM_OFXPARSE:
    collect_ignore = list(_OFXPARSE_DEPENDENTES)
    print(
        f"[conftest] PYTEST_ALLOW_MISSING_OPTIONAL_DEPS=1 e ofxparse ausente — "
        f"ignorando {len(collect_ignore)} arquivos e pulando "
        f"{len(_OFXPARSE_IMPORT_TARDIO)} testes de import tardio."
    )


def pytest_collection_modifyitems(config, items):
    """Pula os testes que só descobrem a falta do ofxparse ao executar.

    Mesma filosofia da lista de arquivos: nomes FIXOS e condição ligada à
    ausência do pacote, não ao erro. Marcar pelo erro esconderia um
    ImportError novo introduzido por outra mudança.
    """
    if _TEM_OFXPARSE or not _DEPS_REDUZIDAS:
        return
    motivo = pytest.mark.skip(reason="ofxparse ausente (import tardio); no CI o pacote existe")
    alvos = set(_OFXPARSE_IMPORT_TARDIO)
    for item in items:
        if item.nodeid in alvos:
            item.add_marker(motivo)



# ── Isolamento do banco por execução ─────────────────────────────────────────
# Cada execução da suíte trabalha num database só dela. Sem isso, duas
# execuções simultâneas se destroem: a fixture `_auto_cleanup_orphan_users`
# abaixo apaga QUALQUER usuário que apareça em `users` durante um teste, e não
# tem como distinguir os da outra execução.
#
# A troca acontece em `pytest_configure`, que roda ANTES da coleta. Isso é
# essencial e não é detalhe de estilo: o pytest importa os módulos de teste na
# coleta, e módulos como `frontend/routes/shared.py` e `core/admin_dashboard.py`
# leem DATABASE_URL no nível de módulo. Numa fixture de sessão (que roda depois
# da coleta) esses módulos já teriam cacheado a URL antiga e as rotas async
# continuariam batendo no banco compartilhado — o isolamento valeria só para o
# pool síncrono.
#
# PYTEST_DB_ISOLATION=0 desliga, para inspecionar o banco depois da rodada.

_ISOLATION: dict[str, str] = {}


def _cached_database_url_modules() -> tuple[str, ...]:
    """Módulos que leem DATABASE_URL no import — a razão de trocar antes da coleta.

    Levantado com `grep -rn DATABASE_URL --include=*.py` e filtrando as leituras
    a nível de módulo. `finance_bot_websocket_custom.py` também guarda a URL, mas
    só para validar que está presente; não conecta com ela.
    """
    return ("frontend/routes/shared.py", "core/admin_dashboard.py")


def pytest_configure(config):
    base_url = os.getenv("DATABASE_URL")
    if not base_url or os.getenv("PYTEST_DB_ISOLATION", "1") == "0":
        return

    import psycopg
    from urllib.parse import urlsplit, urlunsplit

    name = f"pytest_{uuid.uuid4().hex[:12]}"
    try:
        with psycopg.connect(base_url, autocommit=True) as conn:
            # Nome gerado aqui (hex de uuid), não vem de fora — sem risco de
            # injeção. Aspas duplas porque CREATE DATABASE não aceita parâmetro.
            conn.execute(f'create database "{name}"')
    except Exception as exc:
        # ABORTA — não cai de volta no DATABASE_URL original. Antes isto era um
        # print seguido de `return`, e a suíte seguia rodando contra o banco que
        # a pessoa passou. Como `_auto_cleanup_orphan_users` é autouse e o
        # `_cleanup_user` faz DELETE em launches, pockets, investments, accounts,
        # credit_* e users, um papel sem CREATEDB apontando para um banco de
        # verdade significava apagar linhas reais — com um aviso de uma linha no
        # meio de centenas de pontinhos verdes.
        #
        # Quem quer mesmo rodar sem isolamento continua tendo a porta explícita:
        # PYTEST_DB_ISOLATION=0. A diferença é que agora é decisão, não acidente.
        raise pytest.UsageError(
            f"não consegui criar o database isolado desta execução ({exc}).\n"
            "A suíte APAGA linhas (users, launches, pockets, investments, "
            "accounts, credit_*), então rodar contra o DATABASE_URL que você "
            "passou destruiria dado real.\n"
            "Conserto: use um papel com CREATEDB, ou aponte o DATABASE_URL para "
            "um Postgres descartável.\n"
            "Para rodar sem isolamento de propósito: PYTEST_DB_ISOLATION=0."
        )

    parts = urlsplit(base_url)
    os.environ["DATABASE_URL"] = urlunsplit(parts._replace(path=f"/{name}"))
    _ISOLATION["base_url"] = base_url
    _ISOLATION["name"] = name


def pytest_unconfigure(config):
    """Remove o database desta execução.

    Roda no shutdown do pytest, inclusive quando `init_db()` estoura no setup —
    numa fixture, um erro antes do `yield` pularia a limpeza e deixaria um
    database `pytest_*` órfão a cada tentativa falha.
    """
    base_url = _ISOLATION.pop("base_url", None)
    name = _ISOLATION.pop("name", None)
    if not base_url or not name:
        return

    import psycopg

    os.environ["DATABASE_URL"] = base_url
    try:
        from db.connection import close_pool

        close_pool()  # solta as conexões, senão o DROP é recusado
    except Exception:
        pass
    try:
        with psycopg.connect(base_url, autocommit=True) as conn:
            # FORCE derruba conexões remanescentes (Postgres 13+).
            conn.execute(f'drop database if exists "{name}" with (force)')
    except Exception as exc:
        print(f"[conftest] não consegui remover o database {name}: {exc}")


@pytest.fixture(scope="session", autouse=True)
def _init_schema():
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError("Faltou DATABASE_URL no ambiente para rodar os testes.")
    init_db()


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
    import requests as _requests
    async def _blocked_async(*args, **kwargs):
        raise RuntimeError(
            "Outbound HTTP (async) blocked in tests. Mock the call locally."
        )
    monkeypatch.setattr(_httpx.AsyncClient, "request", _blocked_async, raising=False)
    monkeypatch.setattr(_httpx.AsyncClient, "get", _blocked_async, raising=False)
    monkeypatch.setattr(_httpx.AsyncClient, "post", _blocked_async, raising=False)

    # Os clientes, não só os helpers de módulo. `httpx.Client` é a forma MAIS
    # usada em produção (8 ocorrências) — core/services/pluggy.py inteiro
    # depende dela, com GET/POST/PATCH/DELETE autenticados contra a conta
    # bancária real do usuário. Enquanto só os helpers eram bloqueados, esse
    # caminho saía para a rede sem nada falhar.
    #
    # O bloqueio vai no `send`, não verbo a verbo: get/post/put/patch/delete
    # chamam `request`, que chama `send`. Um ponto só cobre todos, inclusive
    # os que ninguém lembrou de listar (foi assim que PATCH e DELETE, usados
    # pelo Pluggy, ficaram de fora da lista original).
    #
    # MAS o critério não pode ser "é um httpx.Client": o TestClient do
    # Starlette/FastAPI HERDA de httpx.Client e é como a suíte chama as próprias
    # rotas. Bloquear por classe derrubaria 125 testes que não têm nada a ver
    # com rede externa. O que separa os dois é o TRANSPORTE: só o HTTPTransport
    # do httpx abre socket; o do TestClient (_TestClientTransport) e os mocks
    # rodam in-process.
    _transportes_de_rede = tuple(
        t for t in (getattr(_httpx, "HTTPTransport", None), getattr(_httpx, "AsyncHTTPTransport", None)) if t
    )

    def _vai_para_a_rede(client) -> bool:
        return isinstance(getattr(client, "_transport", None), _transportes_de_rede)

    _send_sync, _send_async = _httpx.Client.send, _httpx.AsyncClient.send

    def _send_guardado(self, *a, **kw):
        if _vai_para_a_rede(self):
            _blocked_call()
        return _send_sync(self, *a, **kw)

    async def _send_guardado_async(self, *a, **kw):
        if _vai_para_a_rede(self):
            _blocked_call()
        return await _send_async(self, *a, **kw)

    monkeypatch.setattr(_httpx.Client, "send", _send_guardado, raising=False)
    monkeypatch.setattr(_httpx.AsyncClient, "send", _send_guardado_async, raising=False)

    # Mesmo furo do lado do requests: os verbos do módulo estavam cobertos, os
    # da sessão não. Session.get/post/... chamam self.request.
    monkeypatch.setattr(_requests.Session, "request", _blocked_call, raising=False)

    # OpenAI — qualquer atributo do client levanta
    class _FakeOpenAIClient:
        def __getattr__(self, name):
            raise RuntimeError(
                f"OpenAI client blocked in tests (acessou .{name}). "
                "Mock the call locally."
            )
    monkeypatch.setattr("openai.OpenAI", lambda *a, **kw: _FakeOpenAIClient())

    # `ai_router.py:20` faz `from openai import OpenAI`, o que COPIA o nome para
    # dentro do módulo no momento do import. Trocar `openai.OpenAI` não alcança
    # essa cópia — e `core/handle_incoming.py:38` importa o ai_router na coleta,
    # antes desta fixture rodar. Sem o patch abaixo, `_get_client()` constrói o
    # cliente REAL e a chamada é cobrada.
    try:
        import ai_router as _ai_router

        monkeypatch.setattr(_ai_router, "OpenAI", lambda *a, **kw: _FakeOpenAIClient())
        # `_get_client()` guarda o cliente num global; um cliente real cacheado
        # por outro teste sobreviveria ao patch.
        monkeypatch.setattr(_ai_router, "_client", None, raising=False)
    except Exception:
        pass  # ai_router indisponível (deps faltando) — nada a proteger aqui


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
    # Testes escrevem auth_accounts com SQL cru (sem passar pelos writers que
    # invalidam) — zera o cache TTL do get_auth_user pra um teste não ler o
    # estado que o anterior deixou cacheado.
    import db_support
    db_support.invalidate_auth_user_cache()
    # Mesmo motivo para o memo "rede confirmou hoje" das séries do BCB: um
    # teste que stuba a rede não pode herdar a confirmação do teste anterior.
    import db.investments
    db.investments._sgs_confirmed_until.clear()
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


def promote_to_pro(user_id: int) -> int:
    """Mesma promoção da fixture `pro_user_id`, chamável no meio de um teste
    (quando o user vem de outra fixture, ex.: id pequeno pro WhatsApp)."""
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


@pytest.fixture()
def pro_user_id(user_id: int):
    """user_id já promovido para plano Pro — use em testes que precisam criar
    múltiplas caixinhas/cartões ou exercem features Pro."""
    return promote_to_pro(user_id)
