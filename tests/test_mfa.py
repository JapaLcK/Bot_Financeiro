"""
tests/test_mfa.py — Cobertura do MFA TOTP.

Cobre:
- Setup de secret (com confirmacao por senha)
- Verificacao do primeiro codigo e ativacao
- Geracao e consumo de backup codes
- Login em duas etapas: senha → challenge → codigo TOTP
- Disable do MFA (exige senha + codigo)
- Idempotencia e protecao contra re-ativacao
- Onboarding: quem gasta o convite de UMA VEZ SO da /home, e o script que o
  devolve a quem o perdeu sem poder aceitar (bloco no fim do arquivo)
"""
import os
import uuid

import pyotp
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

# Garante MFA_ENCRYPTION_KEY antes de importar o modulo (que cacheia Fernet)
os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard
from scripts import reset_mfa_onboarding_sem_senha


def _csrf_headers(client: TestClient) -> dict:
    token = "test-csrf-token"
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, token)
    return {dashboard.CSRF_HEADER_NAME: token}


def _register_user(client: TestClient, email: str, password: str) -> int:
    """Cria conta e confirma email — replica fluxo real."""
    user = db.register_auth_user(email, password)
    return int(user["user_id"])


def _login(client: TestClient, email: str, password: str) -> dict:
    resp = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=_csrf_headers(client),
    )
    return {"status": resp.status_code, "body": resp.json()}


def test_status_returns_disabled_for_new_user(user_id):
    status = db.get_mfa_status(user_id)
    assert status["enabled"] is False
    assert status["backup_codes_remaining"] == 0


def test_setup_secret_creates_pending_record(user_id):
    result = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    assert "secret" in result
    assert result["uri"].startswith("otpauth://")
    status = db.get_mfa_status(user_id)
    assert status["enabled"] is False
    assert status["has_pending_secret"] is True


def test_verify_and_enable_with_valid_code_returns_backup_codes(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    backup_codes = db.mfa_verify_and_enable(user_id, code)

    assert len(backup_codes) == 10
    assert all(len(c) == 11 and "-" in c for c in backup_codes)  # XXXXX-XXXXX
    status = db.get_mfa_status(user_id)
    assert status["enabled"] is True
    assert status["backup_codes_remaining"] == 10


def test_verify_and_enable_rejects_invalid_code(user_id):
    db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    import pytest
    with pytest.raises(ValueError, match="MFA_CODE_INVALID"):
        db.mfa_verify_and_enable(user_id, "000000")


def test_setup_blocks_when_already_enabled(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    db.mfa_verify_and_enable(user_id, code)

    import pytest
    with pytest.raises(ValueError, match="MFA_ALREADY_ENABLED"):
        db.mfa_setup_secret(user_id, f"user{user_id}@test.com")


def test_verify_totp_with_valid_code_returns_true(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    db.mfa_verify_and_enable(user_id, code)

    later_code = pyotp.TOTP(setup["secret"]).now()
    assert db.mfa_verify_totp(user_id, later_code) is True


def test_verify_totp_rejects_invalid_format(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    db.mfa_verify_and_enable(user_id, code)

    assert db.mfa_verify_totp(user_id, "abc") is False
    assert db.mfa_verify_totp(user_id, "12345") is False
    assert db.mfa_verify_totp(user_id, "") is False


def test_consume_backup_code_marks_as_used(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    backup_codes = db.mfa_verify_and_enable(user_id, code)

    first = backup_codes[0]
    assert db.mfa_consume_backup_code(user_id, first) is True
    # Segundo uso falha
    assert db.mfa_consume_backup_code(user_id, first) is False
    # Outro ainda funciona
    assert db.mfa_consume_backup_code(user_id, backup_codes[1]) is True

    status = db.get_mfa_status(user_id)
    assert status["backup_codes_remaining"] == 8


def test_regenerate_backup_codes_invalidates_old(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    old_codes = db.mfa_verify_and_enable(user_id, code)

    new_codes = db.mfa_regenerate_backup_codes(user_id)
    assert len(new_codes) == 10
    assert set(old_codes).isdisjoint(new_codes)
    # Codigo antigo nao funciona mais
    assert db.mfa_consume_backup_code(user_id, old_codes[0]) is False
    # Novo funciona
    assert db.mfa_consume_backup_code(user_id, new_codes[0]) is True


def test_disable_mfa_removes_all_state(user_id):
    setup = db.mfa_setup_secret(user_id, f"user{user_id}@test.com")
    code = pyotp.TOTP(setup["secret"]).now()
    db.mfa_verify_and_enable(user_id, code)

    db.disable_mfa(user_id)
    status = db.get_mfa_status(user_id)
    assert status["enabled"] is False
    assert status["has_pending_secret"] is False
    assert status["backup_codes_remaining"] == 0


def test_login_challenge_consume_returns_user_id(user_id):
    token = db.mfa_create_login_challenge(user_id)
    assert isinstance(token, str)
    assert len(token) > 20
    consumed = db.mfa_consume_login_challenge(token)
    assert consumed == user_id
    # Single-use
    assert db.mfa_consume_login_challenge(token) is None


def test_login_challenge_with_invalid_token_returns_none():
    assert db.mfa_consume_login_challenge("nonexistent") is None
    assert db.mfa_consume_login_challenge("") is None


# ── Endpoint tests ──────────────────────────────────────────────────────

def test_login_without_mfa_works_normally(user_id):
    """Sanity: usuario sem MFA segue fluxo padrao."""
    email = f"mfa-test-{user_id}@test.com"
    password = "senha-forte-123"
    db.register_auth_user(email, password)

    client = TestClient(dashboard.app)
    resp = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "mfa_required" not in body or body["mfa_required"] is False
    assert "user_id" in body


def test_login_with_mfa_returns_challenge(user_id):
    email = f"mfa-test-{user_id}@test.com"
    password = "senha-forte-123"
    user = db.register_auth_user(email, password)
    real_user_id = int(user["user_id"])

    setup = db.mfa_setup_secret(real_user_id, email)
    code = pyotp.TOTP(setup["secret"]).now()
    db.mfa_verify_and_enable(real_user_id, code)

    client = TestClient(dashboard.app)
    resp = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mfa_required"] is True
    assert "mfa_challenge" in body
    assert "user_id" not in body  # ainda nao logou


def test_mfa_verify_login_completes_login(user_id):
    email = f"mfa-test-{user_id}@test.com"
    password = "senha-forte-123"
    user = db.register_auth_user(email, password)
    real_user_id = int(user["user_id"])

    setup = db.mfa_setup_secret(real_user_id, email)
    secret = setup["secret"]
    db.mfa_verify_and_enable(real_user_id, pyotp.TOTP(secret).now())

    client = TestClient(dashboard.app)
    login_resp = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=_csrf_headers(client),
    )
    challenge = login_resp.json()["mfa_challenge"]

    code = pyotp.TOTP(secret).now()
    verify_resp = client.post(
        "/auth/mfa/verify-login",
        json={"challenge": challenge, "code": code, "use_backup": False},
        headers=_csrf_headers(client),
    )
    assert verify_resp.status_code == 200
    body = verify_resp.json()
    assert body["user_id"] == real_user_id
    assert body["email"] == email


def test_mfa_verify_login_rejects_wrong_code(user_id):
    email = f"mfa-test-{user_id}@test.com"
    password = "senha-forte-123"
    user = db.register_auth_user(email, password)
    real_user_id = int(user["user_id"])

    setup = db.mfa_setup_secret(real_user_id, email)
    db.mfa_verify_and_enable(real_user_id, pyotp.TOTP(setup["secret"]).now())

    client = TestClient(dashboard.app)
    login_resp = client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=_csrf_headers(client),
    )
    challenge = login_resp.json()["mfa_challenge"]

    verify_resp = client.post(
        "/auth/mfa/verify-login",
        json={"challenge": challenge, "code": "000000", "use_backup": False},
        headers=_csrf_headers(client),
    )
    assert verify_resp.status_code == 400


# ──────────────────────────────────────────────────────────────────────────────
# Onboarding de MFA — o convite de UMA VEZ SÓ e quem pode queimá-lo
#
# O overlay da /home é convidado para TODO usuário sem MFA, inclusive conta
# só-Google (`password_hash` NULL): desde o #232 o settings oferece "Definir
# senha" a ela, então o convite leva a um lugar útil. Quem queima o convite,
# gravando `mfa_onboarding_shown_at`:
#
#   "Agora não" (e o Esc, que roteia pelo dismiss)  → marca. Frontend; a medição
#       está em `tests/frontend/modal_keys.test.mjs`, não aqui.
#   ATIVAR o MFA de verdade (`verify_and_enable`)   → marca. É um FATO.
#   clicar "Ativar agora" e abandonar o setup       → NÃO marca, e o convite
#       volta. Era o bug: o clique gravava uma INTENÇÃO.
#   errar o código e desistir                       → NÃO marca. É a forma mais
#       comum de abandonar, e por isso a marcação fica no FIM de
#       `verify_and_enable`: ela depende do sucesso, não da tentativa.
#
# `not mfa_enabled` NÃO substitui a marcação da ativação: `disable_mfa` apaga a
# linha de `user_mfa`, o left join volta a `enabled = NULL` e sem o `shown_at`
# gravado o convite reapareceria para sempre em quem desligou o MFA de propósito.
#
# CONTROLES NEGATIVOS DO GRUPO — resultados MEDIDOS em 2026-09-02 com
#   PYTEST_DB_ISOLATION=1 .venv/bin/python -m pytest tests/test_mfa.py -q
# (as letras são deste bloco; remeça antes de reusar — §2 do CLAUDE.md):
#
# A) o convite não pode voltar a ser negado a conta sem senha: pondo
#    `and db.auth_account_has_password(user_id)` no return de
#    `should_show_mfa_onboarding`  →  1 failed, 22 passed (o arquivo inteiro: 23 testes)
#      VERMELHO: test_auth_validate_convida_conta_so_google_ao_mfa
#
# B) alvo do script: removendo `and mfa_onboarding_shown_at is not null` do
#    `_ALVO`  →  1 failed, 22 passed
#      VERMELHO: test_script_repara_convite_queimado_e_e_idempotente, no
#                `== antes + 1` (mediu `assert 2 == (2 + 1)`). Antes do delta
#                exato este controle dava 0 failed: metade do `_ALVO` podia
#                sumir sem uma linha vermelha.
#
# C) marcação na ATIVAÇÃO: trocando o `mark_mfa_onboarding_shown(user_id)` do
#    fim de `verify_and_enable` por `pass`  →  1 failed, 22 passed
#      VERMELHO: test_desligar_o_mfa_nao_re_arma_o_convite, em
#                `should_show_mfa_onboarding ... assert True is False` — o nag
#                permanente reproduzido.
#
# F) cauda advisory: tirando o try/except de volta da chamada em
#    `verify_and_enable`  →  1 failed, 22 passed
#      VERMELHO: test_cauda_do_onboarding_nao_pode_derrubar_a_ativacao, com a
#                `RuntimeError` subindo — que no endpoint viraria 500 com o MFA
#                já ligado e os 10 códigos de backup perdidos.
#
# G) marcação depende do SUCESSO: movendo a chamada para o INÍCIO de
#    `verify_and_enable` (refatoração plausível: "marca a tentativa"). O número
#    depende de o try/except viajar junto — as duas variantes foram medidas:
#
#    G1) move o BLOCO inteiro (try/except junto)  →  1 failed, 22 passed
#          VERMELHO: test_codigo_errado_no_setup_nao_gasta_o_convite
#                    ("tentativa falha gastou o convite").
#    G2) move só a CHAMADA, guarda apagada        →  2 failed, 21 passed
#          VERMELHO: os dois. Além do acima, cai
#                    test_cauda_do_onboarding_nao_pode_derrubar_a_ativacao: o
#                    monkeypatch dele passa a disparar ANTES da guarda, e a
#                    RuntimeError sobe. É a soma de G com F, não um vermelho novo.
#
#    Antes de test_codigo_errado_no_setup_nao_gasta_o_convite existir, G2 passava
#    sem uma linha vermelha (21/21, medido na árvore da rodada 2, quando o grupo
#    tinha 21 testes): ele media "marcou quando concluiu" e não media "não marcou
#    quando falhou".
#
# CONTROLE POSITIVO, o mesmo em todos: fica VERDE o
# test_onboarding_aparece_para_conta_com_senha. Prova que a regra não virou
# "convida todo mundo para sempre" — com senha, o convite é de uma vez só.
#
# ponytail: `reparar(apply=True)` e um UPDATE SEM filtro de usuario (o script e
# global de proposito), entao estes testes so rodam sob PYTEST_DB_ISOLATION=1
# (default e CI), onde o banco e um `pytest_<uuid>` vazio. Com
# PYTEST_DB_ISOLATION=0 eles zeram `mfa_onboarding_shown_at` de TODA conta sem
# senha do banco apontado. Se um dia precisarem rodar sem isolamento, ai sim
# passe um filtro de user_id ao `reparar()`.
# ──────────────────────────────────────────────────────────────────────────────

def _seed() -> int:
    return uuid.uuid4().int % 100_000_000


def _conta_so_google(seed: int) -> tuple[int, str]:
    """Conta criada pelo FLUXO REAL do Google — é ele quem grava password_hash NULL."""
    email = f"google-mfa-{seed}@test.com"
    token = db.create_pending_google_signup(f"sub-google-mfa-{seed}", email, "Fulano")
    result = db.consume_pending_google_signup(token, "Fulano", f"119{seed:08d}")
    return int(result["user_id"]), email


def _queimar_convite(user_id: int) -> None:
    """Grava `mfa_onboarding_shown_at` por SQL cru — o mesmo estado que o
    "Agora não" produz, e exatamente o que o script existe para reparar. Direto
    no banco para não depender do endpoint nem de qual botão gravou."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update auth_accounts set mfa_onboarding_shown_at = now() where user_id = %s",
            (user_id,),
        )
        conn.commit()


def _shown_at(user_id: int):
    """Lê a coluna crua — é ela que o convite queima, e o que não pode ser tocado."""
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select mfa_onboarding_shown_at from auth_accounts where user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    return row["mfa_onboarding_shown_at"] if row else None


def test_onboarding_aparece_para_conta_com_senha(user_id):
    """Controle positivo: quem TEM senha continua sendo convidado, e o convite
    continua sendo de uma vez só."""
    uid = int(db.register_auth_user(f"mfa-onb-{user_id}@test.com", "senha-forte-123")["user_id"])

    assert db.auth_account_has_password(uid) is True
    assert db.should_show_mfa_onboarding(uid) is True

    db.mark_mfa_onboarding_shown(uid)

    assert db.should_show_mfa_onboarding(uid) is False
    assert db.auth_account_has_password(uid) is True


def test_desligar_o_mfa_nao_re_arma_o_convite(user_id):
    """A conversa que `not mfa_enabled` sozinho NÃO cobre: ativar e desligar
    depois. `disable_mfa` apaga a linha de `user_mfa`, o left join volta a
    `enabled = NULL` e — se a ativação não tivesse marcado nada — o overlay
    voltaria em TODA carga da /home, para sempre, em cima de quem já decidiu."""
    email = f"mfa-onb-off-{user_id}@test.com"
    uid = int(db.register_auth_user(email, "senha-forte-123")["user_id"])
    assert db.should_show_mfa_onboarding(uid) is True

    secret = db.mfa_setup_secret(uid, email)["secret"]
    db.mfa_verify_and_enable(uid, pyotp.TOTP(secret).now())
    assert db.should_show_mfa_onboarding(uid) is False

    db.disable_mfa(uid)

    assert db.get_mfa_status(uid)["enabled"] is False   # pré-condição: desligou mesmo
    # A asserção que discrimina, e ela vem ANTES da do mecanismo de propósito:
    # tirando a marcação da ativação, quem estoura é esta — a linha que descreve
    # o que o usuário sente — e não um detalhe de qual coluna guarda o quê.
    assert db.should_show_mfa_onboarding(uid) is False, \
        "quem ativou e desligou o MFA voltou a ser convidado, em toda carga da /home"
    assert _shown_at(uid) is not None      # o mecanismo: quem segura é o fato gravado


def test_codigo_errado_no_setup_nao_gasta_o_convite(user_id):
    """A outra metade do "grava um FATO, não uma INTENÇÃO": a forma mais comum de
    abandonar o setup não é fechar a aba no QR, é errar o código e desistir. A
    marcação tem de depender do SUCESSO de `verify_and_enable`, não da tentativa
    — senão mover a chamada para o início dela (refatoração plausível: "marca a
    tentativa") queimaria o convite com código errado, sem uma linha vermelha."""
    email = f"mfa-onb-err-{user_id}@test.com"
    uid = int(db.register_auth_user(email, "senha-forte-123")["user_id"])
    db.mfa_setup_secret(uid, email)

    import pytest   # local, como nos vizinhos deste arquivo
    with pytest.raises(ValueError, match="MFA_CODE_INVALID"):
        db.mfa_verify_and_enable(uid, "000000")

    assert _shown_at(uid) is None, "tentativa falha gastou o convite"
    assert db.should_show_mfa_onboarding(uid) is True


def test_cauda_do_onboarding_nao_pode_derrubar_a_ativacao(user_id, monkeypatch):
    """O commit que liga o MFA já aconteceu quando a marcação roda, e os 10
    códigos de backup só existem em texto puro no `return` — se a cauda propagar,
    `/auth/mfa/enable` (que só trata ValueError) devolve 500 com o MFA LIGADO e os
    códigos perdidos, e a retentativa dá MFA_ALREADY_ENABLED. Escrita nova depois
    do ponto sem volta: a classe do `mark_bill_paid` (CLAUDE.md §1)."""
    email = f"mfa-onb-cauda-{user_id}@test.com"
    uid = int(db.register_auth_user(email, "senha-forte-123")["user_id"])
    secret = db.mfa_setup_secret(uid, email)["secret"]

    import db.mfa as mfa_mod
    def explode(_uid):
        raise RuntimeError("banco fora no meio da cauda")
    monkeypatch.setattr(mfa_mod, "mark_mfa_onboarding_shown", explode)

    codigos = db.mfa_verify_and_enable(uid, pyotp.TOTP(secret).now())

    assert len(codigos) == 10, "os códigos de backup se perderam com a cauda quebrada"
    assert db.get_mfa_status(uid)["enabled"] is True
    assert _shown_at(uid) is None      # a marcação falhou mesmo: o nag volta, e tudo bem


def test_auth_validate_convida_conta_so_google_ao_mfa():
    """Anti-regressão contra repor o gate de senha na leitura: conta só-Google É
    convidada. Vai pelo endpoint que a /home realmente lê (home.html:1516 usa o
    `validateData` do /auth/validate, não o /auth/me)."""
    uid, _ = _conta_so_google(_seed())
    assert db.auth_account_has_password(uid) is False
    client = TestClient(dashboard.app)
    client.cookies.set("dashboard_token", dashboard.make_dashboard_token(uid, hours=1))

    resp = client.get("/auth/validate")

    assert resp.status_code == 200
    assert resp.json()["show_mfa_onboarding"] is True


def test_script_repara_convite_queimado_e_e_idempotente():
    uid, _ = _conta_so_google(_seed())
    intacto, _ = _conta_so_google(_seed())   # sem senha, mas convite NUNCA queimado
    antes = reset_mfa_onboarding_sem_senha.reparar()

    _queimar_convite(uid)
    assert _shown_at(uid) is not None
    assert _shown_at(intacto) is None

    # Delta EXATO de +1, e é ele que discrimina o `mfa_onboarding_shown_at is
    # not null` do `_ALVO`: sem essa metade do predicado a conta `intacto` já
    # entraria na contagem ANTES da queima e o delta seria 0. Com `>= 1` no
    # lugar, apagar metade do predicado passa verde.
    assert reset_mfa_onboarding_sem_senha.reparar() == antes + 1
    assert _shown_at(uid) is not None        # dry-run não escreve

    assert reset_mfa_onboarding_sem_senha.reparar(apply=True) >= 1
    assert _shown_at(uid) is None

    # idempotência: a segunda passada não muda mais esta linha
    reset_mfa_onboarding_sem_senha.reparar(apply=True)
    assert _shown_at(uid) is None


def test_script_nao_toca_quem_tem_senha_nem_convite_intacto(user_id):
    # (a) conta COM senha que já queimou o convite: não dá para saber se dispensou
    #     ou abandonou o setup, e por isso ela fica fora do alvo do script
    com_senha = int(
        db.register_auth_user(f"mfa-script-{user_id}@test.com", "senha-forte-123")["user_id"]
    )
    db.mark_mfa_onboarding_shown(com_senha)
    antes = _shown_at(com_senha)
    assert antes is not None

    # (b) conta só-Google que nunca viu o convite: já está NULL, nada a fazer
    so_google, _ = _conta_so_google(_seed())
    assert _shown_at(so_google) is None

    reset_mfa_onboarding_sem_senha.reparar(apply=True)

    assert _shown_at(com_senha) == antes
    assert _shown_at(so_google) is None
    assert db.should_show_mfa_onboarding(com_senha) is False
