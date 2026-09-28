"""`POST /auth/quiz/conta` (a /assinar cria a conta) e o `_sessao_de_conta_nova`.

Rotas e banco reais, pelo TestClient. O que mais importa aqui: e-mail que já
tem conta não muda NADA nela e ninguém ganha sessão (G1).

A classe fechada aqui: nenhum dos três criadores de conta (register/verify,
Google/Apple, /assinar) põe sessão numa conta que a própria requisição não
criou, e a /assinar não toca no código de quem está no meio do register.

Controles (medidos no PR 1 do funil v3):
- sem a checagem do código pendente → `test_ataque_register_*` e a corrida
  com o confirm vermelhos;
- `inserir_conta_nova` com `do update` no lugar de `do nothing` →
  `test_inverso_*` e `test_google_na_corrida_*` vermelhos (a corrida real às vezes);
- sem a trava (só a do register, ou nenhuma) → `test_trava_*` vermelho;
- a /assinar esperando a trava (`esperar=True`) → `test_rajada_*` vermelho;
- o balde por e-mail de volta ao `register` → `test_teto_da_assinar_*` vermelho;
- a checagem de conta existente em `criar_conta_sem_codigo` é redundante com o
  `do nothing`: só as duas juntas desligadas deixam G1 vermelho;
- positivo: `test_email_novo_*` cria a conta e loga.
"""
import threading
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import core.services.email_service as email_service
import core.services.meta_capi as meta_capi
import db
import db.google_auth as db_google_auth
import db.signup_quiz as db_signup_quiz
import db_support
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.quiz_signup as quiz_signup
from core.crypto import hash_pii_optional
from frontend.routes import shared
from utils_phone import normalize_phone_e164

NOME = "Fulana Assinar"
QUIZ = "v1.dividas.acdbd"
CSRF = "csrf-assinar"
COOKIES_DE_SESSAO = {"auth_token", "dashboard_token", "refresh_token"}


def _email() -> str:
    return f"quiz-conta-{uuid.uuid4().hex[:10]}@example.com"


def _telefone() -> str:
    return f"55659{uuid.uuid4().int % 100_000_000:08d}"


def _limpa_baldes():
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("delete from auth_rate_limits where bucket = 'quiz' or identifier = 'ip:testclient'"
                    " or identifier like 'email:quiz-conta-%%'")
        conn.commit()


@pytest.fixture(autouse=True)
def env(monkeypatch):
    boas_vindas, capi, eventos = [], [], []
    monkeypatch.setattr(email_service, "send_welcome_email", lambda to, *a, **k: boas_vindas.append(to) or True)
    monkeypatch.setattr(meta_capi, "capi_configured", lambda: True)
    monkeypatch.setattr(meta_capi, "send_event", lambda **kw: capi.append(kw))

    async def _evento(*a, **kw):
        eventos.append((a, kw))

    monkeypatch.setattr(quiz_signup, "log_system_event", _evento)
    shared.limiter.reset()
    _limpa_baldes()
    yield SimpleNamespace(boas_vindas=boas_vindas, capi=capi, eventos=eventos)
    _limpa_baldes()


def _navegador(cookie_quiz: str | None = None) -> TestClient:
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF)
    if cookie_quiz:
        client.cookies.set("quiz_result", cookie_quiz)
    return client


def _conta_quiz(client: TestClient, email: str, **extra):
    corpo = {"email": email, "nome": NOME, "whatsapp": _telefone(), "aceitou_termos": True, **extra}
    return client.post("/auth/quiz/conta", headers={dashboard.CSRF_HEADER_NAME: CSRF}, json=corpo)


def _linha(email: str) -> dict | None:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select user_id, password_hash, phone_e164, phone_hash, display_name, dashboard_profile,"
                    " signup_quiz, signup_source from auth_accounts where email_hash = %s",
                    (hash_pii_optional(email, kind="email"),))
        linha = cur.fetchone()
        conn.commit()
    return linha


def _contagens(email: str, user_id: int) -> tuple[int, int, int]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from auth_accounts where email_hash = %s",
                    (hash_pii_optional(email, kind="email"),))
        contas = cur.fetchone()["n"]
        cur.execute("select count(*) as n from auth_sessions where user_id = %s", (user_id,))
        sessoes = cur.fetchone()["n"]
        cur.execute("select count(*) as n from email_verification_codes where email_hash = %s",
                    (hash_pii_optional(email, kind="email"),))
        codigos = cur.fetchone()["n"]
        conn.commit()
    return contas, sessoes, codigos


def _cookies(r) -> set[str]:
    return {c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")}


def _conta_com_senha(email: str, telefone: str) -> int:
    db.confirm_email_verification(email, db.create_email_verification(email, "senha-forte-123", telefone,
                                                                       display_name="Dona Original"))
    return int(_linha(email)["user_id"])


# ── Positivo: e-mail novo ────────────────────────────────────────────────────

def test_email_novo_cria_conta_sem_senha_logada_e_grava_o_quiz(env):
    email, tel = _email(), _telefone()
    r = _conta_quiz(_navegador(QUIZ), email.upper(), whatsapp=tel)
    assert r.status_code == 200, r.text
    corpo = r.json()
    conta = _linha(email)
    assert corpo == {"estado": "criada", "user_id": conta["user_id"]}
    assert COOKIES_DE_SESSAO <= _cookies(r)
    assert any(c.startswith('quiz_result=""') for c in r.headers.get_list("set-cookie")), "quiz_result não apagado"
    assert conta["password_hash"] is None
    assert (conta["phone_e164"], conta["display_name"]) == (normalize_phone_e164(tel), NOME)
    assert (conta["dashboard_profile"], conta["signup_quiz"]["versao"]) == ("dividas", 1)
    assert conta["signup_source"] == "web"
    assert env.boas_vindas == [email]
    assert [(e["event_name"], e["email"], e["event_source_url"]) for e in env.capi] == [
        ("CompleteRegistration", email, f"{shared.DASHBOARD_URL}/assinar")]


def test_link_code_falha_depois_do_commit_conta_nasce_logada_sem_pii(env, monkeypatch, caplog, capsys):
    """O `create_link_code` roda depois do commit da conta: se ele lançasse até a rota,
    ela respondia 503 sem sessão e o retry caía em `tem_conta` — conta sem senha presa."""
    email, tel = _email(), _telefone()
    chamadas = []

    def explode(user_id, minutes_valid=15):
        chamadas.append(user_id)
        raise Exception(f"Failing row ({email}, {tel})")

    monkeypatch.setattr(db_signup_quiz, "create_link_code", explode)
    client = _navegador()
    r = _conta_quiz(client, email, whatsapp=tel)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "criada"
    assert COOKIES_DE_SESSAO <= _cookies(r)
    assert _linha(email)["password_hash"] is None
    assert len(chamadas) == 1
    assert env.boas_vindas == []
    saida = caplog.text + "".join(capsys.readouterr()) + r.text
    assert email not in saida and tel not in saida

    r2 = _conta_quiz(client, email, whatsapp=tel)
    assert (r2.status_code, r2.json()) == (200, {"estado": "logado"})
    assert _contagens(email, _linha(email)["user_id"])[0] == 1


def _sessoes_e_refresh(user_id: int) -> tuple[int, int]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from auth_sessions where user_id = %s", (user_id,))
        sessoes = cur.fetchone()["n"]
        cur.execute("select count(*) as n from auth_refresh_tokens where user_id = %s", (user_id,))
        refresh = cur.fetchone()["n"]
        conn.commit()
    return sessoes, refresh


@pytest.mark.parametrize("onde", [
    "create_session", "create_refresh_token",
    # Nenhuma `_apply_*` lança em produção (try/except interno); o patch só simula uma
    # falha depois de os cookies estarem no `response`: o 503 não pode levá-los.
    pytest.param("_apply_referral_attribution", id="depois_dos_cookies"),
])
def test_sessao_falha_depois_do_commit_desfaz_a_conta_e_o_retry_loga(env, monkeypatch, onde):
    """A conta sem senha já está commitada quando a sessão falha: sem desfazer, a rota
    dava 500 sem sessão e o retry caía em `tem_conta` — conta presa."""
    import core.refresh_tokens as refresh_tokens
    alvo = refresh_tokens if onde == "create_refresh_token" else dashboard

    def explode(*_a, **_kw):
        raise RuntimeError("banco caiu")

    email, client = _email(), _navegador()
    uid = int(db.get_or_create_canonical_user("email", email))
    with monkeypatch.context() as m:
        m.setattr(alvo, onde, explode)
        r = _conta_quiz(client, email)
    assert r.status_code == 503, r.text
    assert not (_cookies(r) & COOKIES_DE_SESSAO), r.headers.get_list("set-cookie")
    assert _linha(email) is None
    assert _sessoes_e_refresh(uid) == (0, 0)

    r = _conta_quiz(client, email)
    assert (r.status_code, r.json()["estado"]) == (200, "criada"), r.text
    assert COOKIES_DE_SESSAO <= _cookies(r)
    assert _linha(email)["password_hash"] is None and int(_linha(email)["user_id"]) == uid


def test_sessao_e_desfazer_falham_503_sem_pii(env, monkeypatch, caplog, capsys):
    email, tel = _email(), _telefone()

    def explode(*_a, **_kw):
        raise Exception(f"Failing row ({email}, {tel})")

    monkeypatch.setattr(dashboard, "create_session", explode)
    monkeypatch.setattr(quiz_signup, "desfazer_conta_sem_codigo", explode)
    r = _conta_quiz(_navegador(), email, whatsapp=tel)
    assert r.status_code == 503, r.text
    assert not (_cookies(r) & COOKIES_DE_SESSAO)
    assert [a[2].split(":")[0] for a, _ in env.eventos] == ["sessao", "desfazer"]
    saida = caplog.text + "".join(capsys.readouterr()) + str(env.eventos) + r.text
    assert email not in saida and tel not in saida


def _conta_id(email: str) -> int:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from auth_accounts where email_hash = %s", (hash_pii_optional(email, kind="email"),))
        conta_id = int(cur.fetchone()["id"])
        conn.commit()
    return conta_id


def _conta_sem_senha(email: str) -> tuple[int, int]:
    r = db_signup_quiz.criar_conta_sem_codigo(email, None, None, "web")
    assert r["conta_id"] == _conta_id(email)
    return int(r["user_id"]), r["conta_id"]


@pytest.mark.parametrize("conta", ["com_senha", "plano_pago", "com_expiracao", "conta_id_de_outra"])
def test_desfazer_so_apaga_a_propria_conta_sem_senha_e_sem_plano(env, conta):
    email, outra = _email(), _email()
    if conta == "com_senha":
        uid = _conta_com_senha(email, _telefone())
        conta_id = _conta_id(email)
    else:
        uid, conta_id = _conta_sem_senha(email)
    if conta == "conta_id_de_outra":  # o id existe, sem senha e sem plano, mas é de outro usuário
        _, conta_id = _conta_sem_senha(outra)
    elif conta != "com_senha":
        muda = {"plano_pago": "plan = 'plus'",
                "com_expiracao": "plan_expires_at = now() + interval '30 days'"}[conta]
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute(f"update auth_accounts set {muda} where user_id = %s", (uid,))
            conn.commit()
    antes = (_linha(email), _linha(outra))
    assert db_signup_quiz.desfazer_conta_sem_codigo(uid, conta_id) is False
    assert (_linha(email), _linha(outra)) == antes


def test_desfazer_apaga_mesmo_quando_a_sessao_demora_a_falhar(env):
    """Sem corte de tempo: a sessão pode falhar minutos depois (fila do pool)."""
    email = _email()
    uid, conta_id = _conta_sem_senha(email)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set created_at = now() - interval '2 minutes' where id = %s",
                    (conta_id,))
        conn.commit()
    assert db_signup_quiz.desfazer_conta_sem_codigo(uid, conta_id) is True
    assert _linha(email) is None


def test_desfazer_apaga_so_a_conta_e_as_sessoes_do_proprio_user_id(env):
    """Positivo e isolamento: a recém-criada some com as sessões; a vizinha, também
    recém-criada e logada, fica inteira."""
    email, vizinha = _email(), _email()
    assert _conta_quiz(_navegador(), email).json()["estado"] == "criada"
    assert _conta_quiz(_navegador(), vizinha).json()["estado"] == "criada"
    uid, uid_vizinha = int(_linha(email)["user_id"]), int(_linha(vizinha)["user_id"])
    antes_vizinha = (_linha(vizinha), _sessoes_e_refresh(uid_vizinha))
    assert _sessoes_e_refresh(uid) == (1, 1)

    assert db_signup_quiz.desfazer_conta_sem_codigo(uid, _conta_id(email)) is True
    assert _linha(email) is None and _sessoes_e_refresh(uid) == (0, 0)
    assert (_linha(vizinha), _sessoes_e_refresh(uid_vizinha)) == antes_vizinha


def test_conversa_conta_me_e_checkout(env, monkeypatch):
    """A conta nova anda sozinha até o checkout: sem senha, sem plano, 200 no Stripe."""
    from tests.test_billing_checkout import _patch_stripe
    client = _navegador()
    assert _conta_quiz(client, _email()).status_code == 200
    me = client.get("/auth/me")
    assert me.status_code == 200, me.text
    assert (me.json()["has_password"], me.json()["needs_plan_selection"]) == (False, True)
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_m")
    _patch_stripe(monkeypatch)
    r = client.post("/billing/create-checkout", headers={dashboard.CSRF_HEADER_NAME: CSRF}, json={"plan": "plus"})
    assert r.status_code == 200, r.text
    assert r.json()["checkout_url"].startswith("https://checkout.stripe.com/")


# ── G1: e-mail que já tem conta ──────────────────────────────────────────────

@pytest.mark.parametrize("jar", ["vazio", "sessao_de_outra_conta"])
def test_g1_email_existente_nao_muda_nada_e_nao_ganha_sessao(env, jar):
    email, tel = _email(), _telefone()
    uid = _conta_com_senha(email, tel)
    client = _navegador(QUIZ)
    if jar == "sessao_de_outra_conta":
        assert _conta_quiz(client, _email()).status_code == 200
    antes, contagens = _linha(email), _contagens(email, uid)
    env.boas_vindas.clear(), env.capi.clear()

    r = _conta_quiz(client, f"  {email.upper()} ", whatsapp=_telefone(), nome="Invasor")
    assert (r.status_code, r.json()) == (200, {"estado": "tem_conta"})
    assert not (_cookies(r) & COOKIES_DE_SESSAO), r.headers.get_list("set-cookie")
    assert _linha(email) == antes
    assert antes["password_hash"] is not None and antes["phone_e164"] == normalize_phone_e164(tel)
    assert _contagens(email, uid) == contagens
    assert env.boas_vindas == [] and env.capi == []


def test_g1_conta_sem_senha_tambem_e_tem_conta(env):
    email = _email()
    assert _conta_quiz(_navegador(), email).status_code == 200
    antes = _linha(email)
    r = _conta_quiz(_navegador(), email, whatsapp=_telefone())
    assert (r.status_code, r.json()) == (200, {"estado": "tem_conta"})
    assert not (_cookies(r) & COOKIES_DE_SESSAO)
    assert _linha(email) == antes


def test_mesmo_email_com_a_sessao_dele_responde_logado_sem_segunda_conta(env):
    email, client = _email(), _navegador()
    assert _conta_quiz(client, email).json()["estado"] == "criada"
    uid = int(_linha(email)["user_id"])
    contagens = _contagens(email, uid)
    r = _conta_quiz(client, email)
    assert (r.status_code, r.json()) == (200, {"estado": "logado"})
    assert not (_cookies(r) & COOKIES_DE_SESSAO)
    assert _contagens(email, uid) == contagens and contagens[0] == 1


# ── Telefone, validação, CSRF, veneno ────────────────────────────────────────

def test_telefone_de_outra_conta_nasce_sem_telefone(env):
    tel = _telefone()
    dono = _email()
    _conta_com_senha(dono, tel)
    livre = _conta_quiz(_navegador(), _email())
    email = _email()
    r = _conta_quiz(_navegador(), email, whatsapp=tel)
    assert r.status_code == 200, r.text
    assert set(r.json()) == set(livre.json()) and r.json()["estado"] == "criada"
    assert (_linha(email)["phone_e164"], _linha(email)["phone_hash"]) == (None, None)
    assert _linha(dono)["phone_e164"] == normalize_phone_e164(tel)


def test_nome_placeholder_do_xquiz_vira_none(env):
    email = _email()
    assert _conta_quiz(_navegador(), email, nome="{{nome}}").status_code == 200
    assert _linha(email)["display_name"] is None


@pytest.mark.parametrize("troca", [
    {"aceitou_termos": False},
    {"aceitou_termos": None},  # None = campo ausente
    {"email": "sem-arroba.example.com"},
    {"email": "a b@x.com"},
    {"whatsapp": "123"},
    {"whatsapp": None},
], ids=["termos_false", "termos_ausente", "email_sem_arroba", "email_com_espaco", "whatsapp_curto",
        "whatsapp_ausente"])
def test_entrada_invalida_400_sem_conta(env, troca):
    corpo = {"email": _email(), "nome": NOME, "whatsapp": _telefone(), "aceitou_termos": True, **troca}
    corpo = {k: v for k, v in corpo.items() if v is not None}
    r = _navegador().post("/auth/quiz/conta", headers={dashboard.CSRF_HEADER_NAME: CSRF}, json=corpo)
    assert r.status_code == 400, r.text
    assert _linha(corpo["email"]) is None


def test_sem_header_de_csrf_403_sem_conta(env):
    email = _email()
    r = _navegador().post("/auth/quiz/conta", json={"email": email, "nome": NOME, "whatsapp": _telefone(),
                                                    "aceitou_termos": True})
    assert r.status_code == 403
    assert _linha(email) is None


def test_corpo_com_nul_recusado_sem_conta(env):
    email = _email()
    r = _conta_quiz(_navegador(), email, nome="Ful\x00ana")
    assert r.status_code == 422, r.text
    assert _linha(email) is None


# ── Limites ──────────────────────────────────────────────────────────────────

def test_decima_primeira_do_mesmo_ip_429(env):
    """O balde já com 9: a 10ª passa, a 11ª não (limite exato, sem criar 10 contas)."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("insert into auth_rate_limits (bucket, identifier, window_started_at, attempts, updated_at)"
                    " values ('quiz', 'ip:testclient', now(), 9, now())")
        conn.commit()
    assert _conta_quiz(_navegador(), _email()).status_code == 200
    email = _email()
    r = _conta_quiz(_navegador(), email)
    assert r.status_code == 429
    assert _linha(email) is None


def test_quarta_do_mesmo_email_429_mesmo_com_conta_existente(env):
    """Enumeração: 3/h por e-mail, contando os `tem_conta`."""
    email = _email()
    _conta_com_senha(email, _telefone())
    estados = [_conta_quiz(_navegador(), email) for _ in range(4)]
    assert [r.status_code for r in estados] == [200, 200, 200, 429]


def test_teto_da_assinar_nao_gasta_o_do_register_da_vitima(env, monkeypatch):
    """Positivo junto: a 4ª chamada da /assinar com o mesmo e-mail ainda é 429."""
    monkeypatch.setattr(email_service, "send_verification_email", lambda *a: True)
    monkeypatch.setattr(email_service, "send_account_exists_notice", lambda *a: True)
    email = _email()
    assert [_conta_quiz(_navegador(), email).status_code for _ in range(3)] == [200, 200, 200]
    r = _navegador().post("/auth/register", headers={dashboard.CSRF_HEADER_NAME: CSRF},
                          json={"email": email, "password": "senha-da-vitima-1", "phone": _telefone()})
    assert r.status_code == 200, r.text
    assert _conta_quiz(_navegador(), email).status_code == 429


# ── Corrida e falha ──────────────────────────────────────────────────────────

def test_falha_de_banco_503_sem_pii_no_log(env, monkeypatch, capsys, caplog):
    email, tel = _email(), _telefone()

    def explode(*_a, **_kw):
        raise Exception(f"Failing row contains ({email}, {tel}, {NOME})")

    monkeypatch.setattr(quiz_signup, "criar_conta_sem_codigo", explode)
    r = _conta_quiz(_navegador(), email, whatsapp=tel)
    assert r.status_code == 503
    assert len(env.eventos) == 1
    texto = repr(env.eventos) + r.text + capsys.readouterr().out + caplog.text
    assert [s for s in (email, tel, NOME) if s in texto] == [], texto


# ── Cadastro de outra pessoa em andamento, corridas e o caminho inverso ─────

def _register(email: str, senha: str = "senha-da-vitima-1") -> str:
    """A vítima começa o /auth/register: só o código pendente, com senha. Devolve o código."""
    codigo = db.create_email_verification(email, senha, _telefone(), display_name="Vitima")
    assert _linha(email) is None
    return codigo


def _verifica(email: str, codigo: str):
    return _navegador().post("/auth/verify-email", headers={dashboard.CSRF_HEADER_NAME: CSRF},
                             json={"email": email, "code": codigo})


def _google(email: str, headers_extra: dict | None = None):
    token = db.create_pending_google_signup(f"sub-{email}", email, "Fulana")
    return _navegador().post(
        "/auth/google/complete-signup", headers={dashboard.CSRF_HEADER_NAME: CSRF, **(headers_extra or {})},
        json={"token": token, "name": "Fulana", "phone": _telefone(), "accepted_terms": True},
    )


def test_ataque_register_pendente_depois_quiz_nao_cria_nem_toca_no_codigo(env):
    email = _email()
    codigo = _register(email)

    r = _conta_quiz(_navegador(QUIZ), email, nome="Atacante")
    assert (r.status_code, r.json()) == (200, {"estado": "cadastro_pendente"})
    assert not (_cookies(r) & COOKIES_DE_SESSAO)
    assert _linha(email) is None and env.capi == [] and env.boas_vindas == []

    r = _verifica(email, codigo)  # a vítima confirma o PRÓPRIO código
    assert r.status_code == 200, r.text
    conta = _linha(email)
    assert conta["password_hash"] is not None and conta["display_name"] == "Vitima"
    r = _navegador().post("/auth/login", headers={dashboard.CSRF_HEADER_NAME: CSRF},
                          json={"email": email, "password": "senha-da-vitima-1"})
    assert r.status_code == 200, r.text


def test_codigo_de_register_vencido_nao_segura_a_assinar(env):
    email = _email()
    _register(email)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update email_verification_codes set expires_at = now() - interval '1 minute'"
                    " where email_hash = %s", (hash_pii_optional(email, kind="email"),))
        conn.commit()
    assert _conta_quiz(_navegador(), email).json()["estado"] == "criada"


@pytest.mark.parametrize("quem_cria", ["google", "assinar"])
def test_inverso_codigo_de_register_nao_entra_na_conta_que_nasceu_depois(env, quem_cria):
    """O código saiu com o e-mail livre, e a conta nasceu depois por outro caminho.
    Antes, o `do update` do confirm punha a senha nela e dava sessão aos dois."""
    email = _email()
    if quem_cria == "google":
        codigo = _register(email)
        assert _google(email).status_code == 200
    else:  # o código sem senha do webhook do XQuiz (#640) não segura a /assinar
        codigo = db.create_email_verification(email, None, _telefone())
        assert _conta_quiz(_navegador(), email).json()["estado"] == "criada"
    antes = _linha(email)

    r = _verifica(email, codigo)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == db_support.EMAIL_JA_TEM_CONTA
    assert not (_cookies(r) & COOKIES_DE_SESSAO)
    assert _linha(email) == antes and antes["password_hash"] is None


def test_google_na_corrida_nao_entra_na_conta_da_assinar(env, monkeypatch):
    """A busca do Google (G5) não viu a conta — a corrida, sem thread — e o INSERT
    recusa: sem identidade Google ligada e sem sessão."""
    email = _email()
    assert _conta_quiz(_navegador(), email).json()["estado"] == "criada"
    antes = _linha(email)
    monkeypatch.setattr(db_google_auth, "find_user_id_by_email", lambda e: None)
    r = _google(email)
    assert r.status_code == 400, r.text
    assert not (_cookies(r) & COOKIES_DE_SESSAO)
    assert _linha(email) == antes
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from auth_identities where user_id = %s", (antes["user_id"],))
        assert cur.fetchone()["n"] == 0
        conn.commit()


def test_trava_register_que_chega_no_meio_da_assinar_espera_e_ve_a_conta(env, monkeypatch):
    """A /assinar já conferiu que não há código pendente e segura a trava; o
    register grava o código nesse meio. Sem a trava do lado do register, o código
    nasce para uma conta que já existe (e a vítima só descobre ao confirmar)."""
    email = _email()
    dentro, solta = threading.Event(), threading.Event()
    real = db_support.telefone_livre

    def _pausa(cur, telefone):  # só a 1ª chamada, que é a da /assinar, sob a trava
        if not dentro.is_set():
            dentro.set()
            assert solta.wait(20)
        return real(cur, telefone)

    monkeypatch.setattr(db_support, "telefone_livre", _pausa)
    quiz, register = {}, {}
    t_quiz = threading.Thread(target=lambda: quiz.update(r=_conta_quiz(_navegador(), email)))
    t_quiz.start()
    assert dentro.wait(20)

    def _registra():
        try:
            register["codigo"] = db.create_email_verification(email, "senha-da-vitima-1", _telefone())
        except db_support.AccountAlreadyExistsError as exc:
            register["recusa"] = exc.reason

    t_reg = threading.Thread(target=_registra)
    t_reg.start()
    t_reg.join(1.0)
    solta.set()
    t_quiz.join(30), t_reg.join(30)
    assert quiz["r"].json()["estado"] == "criada"
    assert register == {"recusa": "email_google"}, register


@pytest.mark.parametrize("rodada", range(3))
@pytest.mark.parametrize("cenario", ["duas_assinar_e_google", "assinar_e_confirm_do_register"])
def test_corrida_real_so_quem_criou_tem_sessao(env, cenario, rodada):
    """Navegadores ao mesmo tempo, num event loop só (o pool async é de um
    loop; TestClient por thread dá PoolTimeout que não existe em produção). O
    trabalho de banco das rotas roda em `to_thread`, então as três gravações
    correm de verdade em paralelo. Nasce UMA conta, e só a requisição que a
    criou sai com sessão. Sem controle negativo próprio (a janela não é
    determinística): quem prova a trava é o `test_trava_*`."""
    import asyncio
    import json

    email = _email()
    corpo = {"email": email, "nome": NOME, "whatsapp": _telefone(), "aceitou_termos": True}
    if cenario == "duas_assinar_e_google":
        token = db.create_pending_google_signup(f"sub-{email}", email, "Fulana")
        pedidos = {
            "assinar1": ("/auth/quiz/conta", corpo),
            "assinar2": ("/auth/quiz/conta", {**corpo, "whatsapp": _telefone()}),
            "google": ("/auth/google/complete-signup",
                       {"token": token, "name": "Fulana", "phone": _telefone(), "accepted_terms": True}),
        }
    else:
        pedidos = {
            "assinar": ("/auth/quiz/conta", corpo),
            "verify": ("/auth/verify-email", {"email": email, "code": _register(email)}),
        }

    async def _post(caminho, dados):  # ASGI cru: o conftest bloqueia o httpx async
        corpo_b = json.dumps(dados).encode()
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": caminho, "raw_path": caminho.encode(), "query_string": b"",
            "root_path": "", "client": ("testclient", 50000), "server": ("testserver", 80),
            "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                        (b"content-length", str(len(corpo_b)).encode()),
                        (b"cookie", f"{dashboard.CSRF_COOKIE_NAME}={CSRF}".encode()),
                        (dashboard.CSRF_HEADER_NAME.lower().encode(), CSRF.encode())],
        }
        msgs = []

        async def receive():
            return {"type": "http.request", "body": corpo_b, "more_body": False}

        async def send(msg):
            msgs.append(msg)

        await dashboard.app(scope, receive, send)
        cookies = {v.decode().split("=", 1)[0] for k, v in msgs[0]["headers"] if k == b"set-cookie"}
        texto = b"".join(m.get("body", b"") for m in msgs[1:]).decode()
        return msgs[0]["status"], cookies, texto

    async def _todos():
        return await asyncio.gather(*(_post(*p) for p in pedidos.values()))

    respostas = dict(zip(pedidos, asyncio.run(_todos())))
    uid = int(_linha(email)["user_id"])
    com_sessao = [n for n, (_, cookies, _) in respostas.items() if cookies & COOKIES_DE_SESSAO]
    assert len(com_sessao) == 1, respostas
    if cenario != "duas_assinar_e_google":  # o dono do código ganha, sempre
        assert com_sessao == ["verify"] and _linha(email)["password_hash"] is not None, respostas
    assert all(st in (200, 400, 409) for st, _, _ in respostas.values()), respostas
    assert _contagens(email, uid)[:2] == (1, 1)


def _segurando_a_trava(email: str):
    """Outra transação com a trava do e-mail (um pedido em voo)."""
    dono = db.get_conn()
    conn = dono.__enter__()
    with conn.cursor() as cur:
        db_support.trava_email(cur, hash_pii_optional(email, kind="email"))
    return dono, conn


def test_trava_ocupada_a_rota_responde_409_ocupado_sem_esperar(env):
    email = _email()
    dono, conn = _segurando_a_trava(email)
    resposta = {}
    try:  # em thread: se a rota esperasse a trava, o teste travaria em vez de falhar
        t = threading.Thread(target=lambda: resposta.update(r=_conta_quiz(_navegador(), email)))
        t.start()
        t.join(10)
    finally:
        conn.rollback()
        dono.__exit__(None, None, None)
    t.join(30)
    assert "r" in resposta
    r = resposta["r"]
    assert (r.status_code, r.json()) == (409, {"estado": "ocupado"})
    assert not (_cookies(r) & COOKIES_DE_SESSAO) and _linha(email) is None
    assert _conta_quiz(_navegador(), email).json()["estado"] == "criada"  # solta a trava, passa


def test_rajada_no_mesmo_email_nao_segura_o_pool(env):
    """8 pedidos (o tamanho do pool síncrono) com a trava ocupada voltam na hora
    com `ocupado`, e uma consulta sem relação nenhuma pega conexão. Esperando a
    trava, cada um segurava uma conexão e o `select 1` tomava PoolTimeout."""
    import time
    email = _email()
    estados, consulta = [], []

    def _pede():
        inicio = time.monotonic()
        estados.append((db_signup_quiz.criar_conta_sem_codigo(email, None, None, "web")["estado"],
                        time.monotonic() - inicio))

    def _select_1():
        try:
            with db.get_conn(timeout=3) as conn:
                consulta.append(conn.execute("select 1 as um").fetchone()["um"])
        except Exception as exc:
            consulta.append(type(exc).__name__)

    dono, conn = _segurando_a_trava(email)
    try:
        pedidos = [threading.Thread(target=_pede) for _ in range(8)]
        [t.start() for t in pedidos]
        time.sleep(0.5)
        t_sel = threading.Thread(target=_select_1)
        t_sel.start()
        t_sel.join(10)
    finally:
        conn.rollback()
        dono.__exit__(None, None, None)
    [t.join(60) for t in pedidos]
    assert consulta == [1], consulta
    assert [e for e, _ in estados] == ["ocupado"] * 8, estados
    assert max(t for _, t in estados) < 5, estados
    assert _linha(email) is None


# ── A extração: os outros dois chamadores do `_sessao_de_conta_nova` ─────────

def test_verify_email_continua_logando_gravando_quiz_e_capi_do_cadastro(env, monkeypatch):
    codigos = []
    monkeypatch.setattr(email_service, "send_verification_email", lambda to, code: codigos.append(code) or True)
    email, client = _email(), _navegador(QUIZ)
    headers = {dashboard.CSRF_HEADER_NAME: CSRF}
    r = client.post("/auth/register", headers=headers,
                    json={"email": email, "password": "senha-forte-123", "phone": _telefone(), "name": NOME})
    assert r.status_code == 200, r.text
    r = client.post("/auth/verify-email", headers=headers, json={"email": email, "code": codigos[-1]})
    assert r.status_code == 200, r.text
    assert COOKIES_DE_SESSAO <= _cookies(r)
    assert _linha(email)["dashboard_profile"] == "dividas"
    assert [(e["event_name"], e["event_source_url"]) for e in env.capi] == [
        ("CompleteRegistration", f"{shared.DASHBOARD_URL}/cadastro")]


def test_complete_signup_google_continua_logando_gravando_quiz_e_capi(env):
    email = _email()
    token = db.create_pending_google_signup(f"sub-{email}", email, "Fulana")
    r = _navegador(QUIZ).post(
        "/auth/google/complete-signup", headers={dashboard.CSRF_HEADER_NAME: CSRF},
        json={"token": token, "name": "Fulana", "phone": _telefone(), "accepted_terms": True},
    )
    assert r.status_code == 200, r.text
    assert COOKIES_DE_SESSAO <= _cookies(r)
    assert _linha(email)["dashboard_profile"] == "dividas"
    assert [(e["event_name"], e["email"], e["event_source_url"]) for e in env.capi] == [
        ("CompleteRegistration", email, f"{shared.DASHBOARD_URL}/completar-cadastro")]
