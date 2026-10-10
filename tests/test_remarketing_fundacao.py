"""Régua de remarketing, PR 1 (docs/plano-remarketing.md): T0, `ja_comprou` e o schema.

Postgres real e rotas reais pelo TestClient. Nada envia mensagem.

Controles (rodados no PR 1):
- `do nothing` trocado por `do update set t0 = now()` em `registrar_t0` → vermelhos
  `test_dois_gets_logados_...` (t0 muda), `test_t0_da_assinar_nao_e_trocado_...` e
  `test_logado_com_t0_da_precos_...` (comparam a tupla);
- `user_id = %(uid)s` tirado da perna `plan_grants` de `_JA_COMPROU_SQL` → o isolamento dos
  4 casos de grant vermelho;
- `registrar_t0` antes da sessão no `/auth/quiz/conta` → o teste da sessão que falha vermelho;
- `and kind = 'completed'` tirado → `só viewed_pricing e started` vermelho;
- a lista de status trocada por `is not null` → `inactive`, `incomplete` e `incomplete_expired` vermelhos;
- `= any(pagos)` de volta para `<> 'free'` → `plano de valor estranho` vermelho;
- a perna `pix_charges` tirada → `pix pago com a janela vencida` vermelho;
- sem `tem_direito_hoje` → `carência com status incomplete` vermelho; `contas[:1]` → `duas contas, a 2ª em carência` vermelho;
- `registrar_t0` tirado do ramo `logado` → `test_logado_sem_t0_previo_grava_precos` vermelho; 'assinar' no lugar de 'precos' no `logado` → o mesmo teste vermelho;
- positivos: conta limpa dá False; `registrar_t0` que levanta não derruba a /precos nem o cadastro.
"""
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import db.remarketing as remarketing
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.quiz_signup as quiz_signup
from _dreno_pix_helpers import nova_cobranca
from conftest import _cleanup_user
from core.services import plan_service
from db.connection import get_conn
from db.plan_grants import upsert_grant
from test_quiz_conta import (  # noqa: F401 — `env` é fixture (autouse)
    COOKIES_DE_SESSAO, _conta_com_senha, _conta_quiz, _cookies, _email, _linha, _navegador,
    _register, _segurando_a_trava, _telefone, env,
)


def _sql(q, a=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        linhas = cur.fetchall() if cur.description else None
        conn.commit()
    return linhas


def _t0(uid):
    linhas = _sql("select t0, origem from remarketing_regua where user_id = %s", (uid,))
    return linhas[0] if linhas else None


def _n_regua():
    return _sql("select count(*) as n from remarketing_regua")[0]["n"]


def _logado(uid):
    c = TestClient(dashboard.app)
    c.cookies.set("auth_token", dashboard._make_jwt(uid, f"u{uid}@remarketing.example.com"))
    return c


@pytest.fixture()
def outro():
    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    yield uid
    _cleanup_user(uid)


# ── schema ───────────────────────────────────────────────────────────────────

def test_schema_checks_e_cascade(user_id):
    with pytest.raises(psycopg.errors.CheckViolation):
        remarketing.registrar_t0(user_id, "outra")
    remarketing.registrar_t0(user_id, "precos")
    _sql("insert into remarketing_envios (user_id, etapa, status) values (%s, 1, 'enviando')", (user_id,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        _sql("insert into remarketing_envios (user_id, etapa, status) values (%s, 1, 'enviado')", (user_id,))
    for etapa, status in ((0, "enviando"), (4, "enviando"), (2, "x")):
        with pytest.raises(psycopg.errors.CheckViolation):
            _sql("insert into remarketing_envios (user_id, etapa, status) values (%s, %s, %s)",
                 (user_id, etapa, status))
    _sql("delete from users where id = %s", (user_id,))
    assert _t0(user_id) is None
    assert _sql("select 1 from remarketing_envios where user_id = %s", (user_id,)) == []


# ── T0 na /precos ────────────────────────────────────────────────────────────

def test_dois_gets_logados_geram_uma_linha_com_o_t0_do_primeiro(user_id):
    c = _logado(user_id)
    assert c.get("/precos").status_code == 200
    primeiro = _t0(user_id)
    assert primeiro["origem"] == "precos"
    assert c.get("/precos").status_code == 200
    assert _t0(user_id) == primeiro
    assert _sql("select count(*) as n from remarketing_regua where user_id = %s", (user_id,))[0]["n"] == 1


def test_t0_da_assinar_nao_e_trocado_pela_precos(user_id):
    remarketing.registrar_t0(user_id, "assinar")
    antes = _t0(user_id)
    assert _logado(user_id).get("/precos").status_code == 200
    assert _t0(user_id) == antes and antes["origem"] == "assinar"  # a tupla inteira: t0 e origem


def test_precos_anonimo_e_cookie_invalido_nao_gravam(user_id):
    antes = _n_regua()
    assert TestClient(dashboard.app).get("/precos").status_code == 200
    c = TestClient(dashboard.app)
    c.cookies.set("auth_token", "lixo.lixo.lixo")
    assert c.get("/precos").status_code == 200
    assert TestClient(dashboard.app).get(f"/precos?uid={user_id}").status_code == 200
    assert _n_regua() == antes and _t0(user_id) is None


def test_registrar_t0_falhando_nao_derruba_a_precos(user_id, monkeypatch):
    def explode(*_a):
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(remarketing, "registrar_t0", explode)
    assert _logado(user_id).get("/precos").status_code == 200
    assert _t0(user_id) is None


# ── T0 na /assinar ───────────────────────────────────────────────────────────

def test_criada_grava_origem_assinar(env):
    email = _email()
    r = _conta_quiz(_navegador(), email)
    assert r.json()["estado"] == "criada", r.text
    uid = int(_linha(email)["user_id"])
    assert _t0(uid)["origem"] == "assinar"


def test_os_outros_estados_nao_gravam(env):
    antes = _n_regua()
    com_senha = _email()
    uid = _conta_com_senha(com_senha, _telefone())
    assert _conta_quiz(_navegador(), com_senha).json()["estado"] == "tem_conta"
    assert _t0(uid) is None

    pendente = _email()
    _register(pendente)
    assert _conta_quiz(_navegador(), pendente).json()["estado"] == "cadastro_pendente"

    ocupado = _email()
    dono, conn = _segurando_a_trava(ocupado)
    try:
        assert _conta_quiz(_navegador(), ocupado).json()["estado"] == "ocupado"
    finally:
        conn.rollback()
        dono.__exit__(None, None, None)
    assert _n_regua() == antes and _t0(uid) is None


def _logado_na_assinar(env):
    """Conta criada pela /assinar e o mesmo navegador de novo: a sessão já é dela (`logado`)."""
    email, client = _email(), _navegador()
    assert _conta_quiz(client, email).json()["estado"] == "criada"
    uid = int(_linha(email)["user_id"])
    _sql("delete from remarketing_regua where user_id = %s", (uid,))
    return uid, email, client


def test_logado_sem_t0_previo_grava_precos(env):
    """'precos' = só e-mail: o `logado` não grava o número digitado, e o guardado não passou pela frase."""
    uid, email, client = _logado_na_assinar(env)
    assert _conta_quiz(client, email).json()["estado"] == "logado"
    assert _t0(uid)["origem"] == "precos"


def test_logado_com_t0_da_precos_mantem_a_tupla(env):
    uid, email, client = _logado_na_assinar(env)
    remarketing.registrar_t0(uid, "precos")
    antes = _t0(uid)
    assert _conta_quiz(client, email).json()["estado"] == "logado"
    assert _t0(uid) == antes and antes["origem"] == "precos"


def test_sessao_que_falha_desfaz_a_conta_e_nao_deixa_t0(env, monkeypatch):
    def explode(*_a, **_kw):
        raise RuntimeError("banco caiu")

    antes = _n_regua()
    monkeypatch.setattr(dashboard, "create_session", explode)
    r = _conta_quiz(_navegador(), _email())
    assert r.status_code == 503 and not (_cookies(r) & COOKIES_DE_SESSAO)
    assert _n_regua() == antes


def test_registrar_t0_falhando_nao_derruba_o_cadastro(env, monkeypatch):
    def explode(*_a):
        raise RuntimeError("banco caiu")

    monkeypatch.setattr(quiz_signup, "registrar_t0", explode)
    email = _email()
    r = _conta_quiz(_navegador(), email)
    assert (r.status_code, r.json()["estado"]) == (200, "criada") and COOKIES_DE_SESSAO <= _cookies(r)
    assert _t0(int(_linha(email)["user_id"])) is None
    assert [(a[0], a[1]) for a, _ in env.eventos] == [("warning", "remarketing_t0_failed")]


# ── ja_comprou ───────────────────────────────────────────────────────────────

AGORA = datetime.now(timezone.utc)


def _grant(source, status="active", ends=AGORA + timedelta(days=30)):
    def f(uid):
        upsert_grant(uid, source, f"{source}:{uuid.uuid4().hex}", "pro", AGORA - timedelta(days=60), ends, 1)
        _sql("update plan_grants set status = %s where user_id = %s", (status, uid))
    return f


def _completed(uid):
    db.record_checkout_completed(uid, f"cs_{uuid.uuid4().hex}")


def _pix(status, janela_dias=(1, 30)):
    """Cobrança Pix do usuário SEM grant. `janela_dias` = (início atrás, fim à frente) em dias."""
    def f(uid):
        c = nova_cobranca(uid)
        _sql("update pix_charges set status = %s, access_starts_at = now() - make_interval(days => %s),"
             " access_expires_at = now() + make_interval(days => %s) where id = %s",
             (status, janela_dias[0], janela_dias[1], c["id"]))
    return f


def _conta(status, plan="free", expira=None, atraso_dias=None):
    """`auth_accounts` do usuário SEM nenhuma linha em `plan_grants`: o estado que o backfill
    de boot não cria (vigente com `plan_expires_at` nulo, vencido, carência)."""
    def f(uid):
        _sql("insert into auth_accounts (user_id, email, email_hash, plan, plan_expires_at,"
             " last_payment_status, past_due_since) values (%s, %s, %s, %s, %s, %s,"
             " case when %s::int is null then null else now() - make_interval(days => %s::int) end)",
             (uid, _email(), uuid.uuid4().hex, plan, expira, status, atraso_dias, atraso_dias))
    return f


def _so_visitou(uid):
    db.record_pricing_viewed(uid)
    db.record_checkout_started(uid, f"cs_{uuid.uuid4().hex}")


def _allowlist(uid):
    plan_service._ACCESS_ALLOWLIST.add(uid)


FONTES = {
    "grant stripe vigente": _grant("stripe"),
    "grant admin vencido": _grant("admin", ends=AGORA - timedelta(days=1)),
    "grant legacy revogado": _grant("legacy", status="revoked"),
    "grant pix": _grant("pix"),
    "completed": _completed,
    "pix pago sem grant": _pix("paid"),
    "pix pago com a janela vencida e sem grant": _pix("paid", (400, -35)),
    "pix estornado": _pix("refunded", (400, -35)),
    "plano pago vigente sem grant": _conta("inactive", "pro", AGORA + timedelta(days=20)),
    "plano vitalício sem grant": _conta("inactive", "pro"),
    "plano vencido sem grant": _conta("inactive", "pro", AGORA - timedelta(days=10)),
    # status que só existem depois de haver assinatura (conta free, sem grant)
    **{f"status {x}": _conta(x) for x in ("grandfathered", "active", "trialing", "past_due", "unpaid", "canceled")},
    # renovação com 3DS pendente: `incomplete` não é da lista de status, só a carência o pega
    "carência com status incomplete": _conta("incomplete", atraso_dias=2),
    # `auth_accounts.user_id` não é unique: a 1ª linha sem direito não pode esconder a 2ª
    "duas contas, a 2ª em carência": lambda uid: (_conta("inactive")(uid), _conta("incomplete", atraso_dias=2)(uid)),
    "allowlist": _allowlist,
}


@pytest.fixture(autouse=True)
def _allowlist_intacta():
    guardada = set(plan_service._ACCESS_ALLOWLIST)
    yield
    plan_service._ACCESS_ALLOWLIST.clear()
    plan_service._ACCESS_ALLOWLIST.update(guardada)


def test_conta_limpa_nao_comprou(user_id):
    assert remarketing.ja_comprou(user_id) is False


@pytest.mark.parametrize("fonte", FONTES)
def test_cada_fonte_isolada_conta_e_nao_vaza_para_outro(fonte, user_id, outro):
    FONTES[fonte](outro)
    assert remarketing.ja_comprou(outro) is True
    assert remarketing.ja_comprou(user_id) is False  # o isolamento: a compra de B não é de A
    FONTES[fonte](user_id)
    assert remarketing.ja_comprou(user_id) is True


# Quem NÃO comprou: viu a /precos, abriu o checkout, ou tem status de tentativa sem pagamento.
NAO_COMPROU = {
    "só viewed_pricing e started": _so_visitou,
    "status inactive (conta nova)": _conta("inactive"),
    "status incomplete (3DS pendente)": _conta("incomplete"),
    "status incomplete_expired (nunca pagou)": _conta("incomplete_expired"),
    "plano de valor estranho": _conta("inactive", "xyz"),
    "pix pending": lambda uid: nova_cobranca(uid),
    "pix expired": _pix("expired", (400, -35)),
}


@pytest.mark.parametrize("caso", NAO_COMPROU)
def test_quem_nao_comprou_continua_false(caso, user_id):
    NAO_COMPROU[caso](user_id)
    assert remarketing.ja_comprou(user_id) is False
