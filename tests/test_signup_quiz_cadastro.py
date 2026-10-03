"""Cookie do quiz → cadastro real → colunas na conta. Rota real, banco real.

Os dois caminhos que criam conta (`/auth/verify-email` e
`/auth/google/complete-signup`), o cookie adulterado, o log sem dado do quiz e a
/q sem rastreio. Controles negativos medidos no PR.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

import db
import db.signup_quiz as signup_quiz
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.shared as shared
from core.crypto import hash_pii_optional

COMPLETO = {"versao": 1, "respostas": {
    "renda": "salario", "fim_do_mes": "zero_a_zero", "cartao": "perdeu_a_conta",
    "mil_reais": "reserva", "objetivo": "sair_das_dividas",
}}
# O que não pode aparecer em log nenhum: perfil, letras e cada opção escolhida.
SEGREDOS = ("dividas", "acdbd", *COMPLETO["respostas"].values())


def _cliente(cookie: str | None) -> tuple[TestClient, dict]:
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")
    if cookie is not None:
        client.cookies.set("quiz_result", cookie)
    return client, {dashboard.CSRF_HEADER_NAME: "test-csrf-token"}


def _email() -> str:
    return f"quiz-e2e-{uuid.uuid4().hex[:10]}@example.com"


def _telefone() -> str:
    return f"55659{uuid.uuid4().int % 100_000_000:08d}"


def _por_email(cookie: str | None):
    email = _email()
    code6 = db.create_email_verification(email, "senha12345", _telefone())
    client, headers = _cliente(cookie)
    r = client.post("/auth/verify-email", headers=headers, json={"email": email, "code": code6})
    assert r.status_code == 200, r.text
    return r, email


def _por_google(cookie: str | None):
    email = _email()
    token = db.create_pending_google_signup(f"sub-{email}", email, "Fulana")
    client, headers = _cliente(cookie)
    r = client.post("/auth/google/complete-signup", headers=headers,
                    json={"token": token, "name": "Fulana", "phone": _telefone(), "accepted_terms": True})
    assert r.status_code == 200, r.text
    return r, email


def _colunas(email: str) -> tuple:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select dashboard_profile, signup_quiz from auth_accounts where email_hash = %s",
                    (hash_pii_optional(email, kind="email"),))
        linha = cur.fetchone()
        conn.commit()
    assert linha is not None, "a conta não foi criada"
    return linha["dashboard_profile"], linha["signup_quiz"]


def _cookie_apagado(r) -> bool:
    return any(h.startswith("quiz_result=") and "max-age=0" in h.lower()
               for h in r.headers.get_list("set-cookie"))


@pytest.mark.parametrize("cadastro", [_por_email, _por_google], ids=["email", "google"])
def test_cookie_do_quiz_vira_colunas_na_conta_e_e_apagado(cadastro):
    r, email = cadastro("v1.dividas.acdbd")
    assert _colunas(email) == ("dividas", COMPLETO)
    assert _cookie_apagado(r)


def test_perfil_valido_com_letras_ruins_grava_parcial():
    r, email = _por_email("v1.dividas.zzzzz")
    assert _colunas(email) == ("dividas", {"versao": 1, "respostas": None})


# "" e NUL ficam no teste do parse: o httpx não manda cookie vazio/NUL do jeito do navegador.
@pytest.mark.parametrize("valor", [
    "v1.admin.acdbd", "V1.DIVIDAS", "v2.dividas.acdbd", "v1.dividas.acdbd.x",
    pytest.param("v1.dividas." + "a" * 5000, id="longo"),
])
def test_cookie_adulterado_nao_grava_nem_quebra_o_cadastro(valor):
    r, email = _por_email(valor)
    assert _colunas(email) == (None, None)
    assert _cookie_apagado(r)


def test_sem_cookie_o_cadastro_segue_e_nada_e_gravado():
    """Controle positivo: o caminho de sempre, sem quiz, fica intacto."""
    r, email = _por_email(None)
    assert _colunas(email) == (None, None)
    assert not _cookie_apagado(r)


def test_log_do_sucesso_nao_leva_perfil_nem_respostas(monkeypatch, capsys, caplog):
    eventos = []

    async def gravador(*args, **kwargs):
        eventos.append((args, kwargs))

    monkeypatch.setattr(dashboard, "log_system_event", gravador)
    _por_email("v1.dividas.acdbd")
    do_quiz = [e for e in eventos if "quiz_result_recorded" in e[0]]
    assert len(do_quiz) == 1
    texto = repr(eventos) + capsys.readouterr().out + caplog.text
    assert not [s for s in SEGREDOS if s in texto]


def test_erro_na_gravacao_nao_vaza_a_linha_nem_quebra_o_cadastro(monkeypatch, capsys):
    def explode(*_a):
        # O que o CheckViolation do Postgres traz de verdade: a linha inteira.
        raise Exception("Failing row contains (123, fulana@example.com, dividas, acdbd)")

    monkeypatch.setattr(signup_quiz, "record_signup_quiz", explode)
    r, email = _por_email("v1.dividas.acdbd")
    saida = capsys.readouterr().out
    assert "[quiz] gravacao falhou" in saida
    assert "dividas" not in saida and "fulana" not in saida
    assert _colunas(email) == (None, None)
    assert _cookie_apagado(r)


# ── /q: sem Pixel, GA4 nem Clarity ───────────────────────────────────────────

RASTREIO = ("connect.facebook.net", "googletagmanager", "clarity.ms")


def test_q_nao_carrega_rastreio(monkeypatch):
    monkeypatch.setattr(shared, "META_PIXEL_ID", "123456789")
    monkeypatch.setattr(shared, "GA4_MEASUREMENT_ID", "G-TESTE00000")
    monkeypatch.setattr(shared, "CLARITY_PROJECT_ID", "clarityteste")
    client = TestClient(dashboard.app)

    q = client.get("/q")
    assert q.status_code == 200 and "/quiz-resultado.js?v=" in q.text
    assert [h for h in RASTREIO if h in q.text] == []

    # Positivo: com os mesmos ids, as páginas vizinhas levam as tags — o teste mede.
    cadastro = client.get("/cadastro").text
    assert "connect.facebook.net" in cadastro and "googletagmanager" in cadastro
    assert "clarity.ms" in client.get("/blog").text

    js = client.get("/quiz-resultado.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
