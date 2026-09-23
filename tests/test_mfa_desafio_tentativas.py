"""
tests/test_mfa_desafio_tentativas.py — o desafio do MFA sobrevive a um código
errado, mas não a mais de 5.

O defeito: `/auth/mfa/verify-login` consumia o desafio ANTES de conferir o
código, então errar um dígito e mandar o certo dava "Sessão MFA expirada".
Agora cada tentativa é reservada num UPDATE atômico (teto de 5 por desafio) e o
desafio só é consumido com o código certo.

Estados do desafio: V vivo, X esgotado, U consumido, E vencido, I inexistente.
Tudo pela conversa HTTP, com Postgres real (CLAUDE.md §3).
"""
import os
import threading
import time
import uuid

import pyotp
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db
import frontend.finance_bot_websocket_custom as dashboard
from db.connection import get_conn
from frontend.routes.shared import limiter
from _apoio_auth_app import csrf as _csrf_headers, limpa_rate_limits

EXPIRADA = "Sessão MFA expirada. Faça login novamente."
MUITAS = "Muitas tentativas. Faça login novamente."
SENHA = "senha-forte-123"


def _conta_com_mfa() -> dict:
    email = f"mfa-tent-{uuid.uuid4().hex[:10]}@test.com"
    uid = int(db.register_auth_user(email, SENHA)["user_id"])
    secret = db.mfa_setup_secret(uid, email)["secret"]
    backups = db.mfa_verify_and_enable(uid, pyotp.TOTP(secret).now())
    return {"uid": uid, "email": email, "secret": secret, "backups": backups}


def _desafio(client: TestClient, conta: dict) -> str:
    limpa_rate_limits("login", conta["email"])  # o teto de login mora no banco
    resp = client.post(
        "/auth/login",
        json={"email": conta["email"], "password": SENHA},
        headers=_csrf_headers(client),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["mfa_challenge"]


def _verify(client: TestClient, challenge: str, code: str, backup: bool = False):
    # O teto de 5/min por IP da rota mascararia o teto por desafio com um 429.
    limiter._storage.reset()
    return client.post(
        "/auth/mfa/verify-login",
        json={"challenge": challenge, "code": code, "use_backup": backup},
        headers=_csrf_headers(client),
    )


def _certo(conta: dict) -> str:
    return pyotp.TOTP(conta["secret"]).now()


def _errado(conta: dict) -> str:
    """Um código fora da janela de ±30s do servidor (nunca o certo por acaso)."""
    t = pyotp.TOTP(conta["secret"])
    validos = {t.at(time.time() + d) for d in (-60, -30, 0, 30, 60)}
    return next(c for c in ("000000", "111111", "222222", "333333") if c not in validos)


def _recusa(resp, code: str, texto: str) -> None:
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body == {"detail": texto, "code": code}
    assert isinstance(body["detail"], str)  # o site mostra `d.detail` como texto
    assert "Accept" in resp.headers.get("vary", "")  # como o 400 via HTTPException


def _attempts(challenge: str) -> int:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select attempts from mfa_login_challenges where token = %s", (challenge,))
        return int(cur.fetchone()["attempts"])


# ── 1. V + errado → V(n+1); V + certo → U ─────────────────────────────────────

def test_errar_um_digito_nao_queima_o_desafio():
    conta = _conta_com_mfa()
    client = TestClient(dashboard.app)
    ch = _desafio(client, conta)

    _recusa(_verify(client, ch, _errado(conta)), "mfa_code_invalid", "Código inválido.")
    assert _attempts(ch) == 1

    # Espaço nas pontas é o que o teclado do celular cola; o servidor faz strip.
    resp = _verify(client, ch, f"  {_certo(conta)} ")
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == conta["uid"]


# ── 2. teto: a 5ª errada esgota (X), e em X nem o certo passa ─────────────────

def test_quinta_tentativa_errada_esgota_o_desafio():
    conta = _conta_com_mfa()
    client = TestClient(dashboard.app)
    ch = _desafio(client, conta)

    for _ in range(4):
        _recusa(_verify(client, ch, _errado(conta)), "mfa_code_invalid", "Código inválido.")
    _recusa(_verify(client, ch, _errado(conta)), "mfa_challenge_expired", MUITAS)
    assert _attempts(ch) == 5

    _recusa(_verify(client, ch, _certo(conta)), "mfa_challenge_expired", EXPIRADA)
    assert _attempts(ch) == 5  # X não incrementa


def test_certo_na_quinta_tentativa_ainda_passa():
    """O consumo não olha `attempts`: a 5ª tentativa, se certa, entra."""
    conta = _conta_com_mfa()
    client = TestClient(dashboard.app)
    ch = _desafio(client, conta)

    for _ in range(4):
        _recusa(_verify(client, ch, _errado(conta)), "mfa_code_invalid", "Código inválido.")
    resp = _verify(client, ch, _certo(conta))
    assert resp.status_code == 200, resp.text


# ── 3. uso único: U + qualquer → expirada ─────────────────────────────────────

def test_desafio_consumido_nao_entra_de_novo():
    conta = _conta_com_mfa()
    client = TestClient(dashboard.app)
    ch = _desafio(client, conta)

    assert _verify(client, ch, _certo(conta)).status_code == 200
    _recusa(_verify(client, ch, _certo(conta)), "mfa_challenge_expired", EXPIRADA)
    assert _attempts(ch) == 1  # U não incrementa


# ── 4. corrida certo × certo: exatamente UM consumo ──────────────────────────

def test_dois_consumos_certos_simultaneos_so_um_ganha():
    """No banco, com a barreira colada na chamada: pela pilha HTTP os dois
    consumos quase nunca se sobrepunham e o teste passava sem o `for update`.
    Backup × backup usa códigos DIFERENTES: sem a trava, os dois seriam gastos."""
    conta = _conta_com_mfa()
    for rodada in range(5):
        for backup in (False, True):
            ch = db.mfa_create_login_challenge(conta["uid"])
            codes = conta["backups"][2 * rodada:2 * rodada + 2] if backup else [_certo(conta)] * 2
            barreira, res = threading.Barrier(2), []

            def _consome(code):
                barreira.wait(timeout=10)
                res.append(db.mfa_consume_login_challenge_with_code(ch, code, backup))

            threads = [threading.Thread(target=_consome, args=(c,)) for c in codes]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
            assert sorted(res, key=repr) == [None, True], (rodada, backup, res)
    assert db.get_mfa_status(conta["uid"])["backup_codes_remaining"] == 5


# ── 5. corrida errado × errado: nunca passa do teto ───────────────────────────

def test_reservas_simultaneas_nunca_passam_de_cinco():
    """No nível do banco: por HTTP o 429 da rota mascararia a contagem."""
    conta = _conta_com_mfa()
    ch = db.mfa_create_login_challenge(conta["uid"])
    barreira = threading.Barrier(10)
    reservas = []

    def _reserva():
        barreira.wait(timeout=10)
        reservas.append(db.mfa_reserve_login_challenge_attempt(ch))

    threads = [threading.Thread(target=_reserva) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    ok = [r for r in reservas if r]
    assert len(reservas) == 10
    assert len(ok) == 5
    assert sorted(r["restantes"] for r in ok) == [0, 1, 2, 3, 4]
    assert all(r["user_id"] == conta["uid"] for r in ok)
    assert _attempts(ch) == 5


# ── 6. backup: errado não gasta código, certo gasta exatamente um ─────────────

def test_backup_errado_depois_certo_gasta_um_codigo():
    conta = _conta_com_mfa()
    client = TestClient(dashboard.app)
    ch = _desafio(client, conta)
    assert db.get_mfa_status(conta["uid"])["backup_codes_remaining"] == 10

    # `0` não existe no alfabeto dos códigos de backup: nunca é válido.
    _recusa(_verify(client, ch, "00000-00000", backup=True), "mfa_code_invalid", "Código inválido.")
    assert db.get_mfa_status(conta["uid"])["backup_codes_remaining"] == 10

    # Minúscula e espaço: como o usuário copia do papel.
    resp = _verify(client, ch, f" {conta['backups'][0].lower()} ", backup=True)
    assert resp.status_code == 200, resp.text
    assert db.get_mfa_status(conta["uid"])["backup_codes_remaining"] == 9


def test_backup_que_perde_para_o_totp_nao_e_gasto(monkeypatch):
    """O backup já reservou quando o TOTP consome o desafio: nada pode ser gasto.

    Ordem forçada, não sorteada: com o backup conferido fora da transação do
    desafio, este é o caminho que dava 400 E gastava o código.
    """
    conta = _conta_com_mfa()
    client = TestClient(dashboard.app)
    ch = _desafio(client, conta)
    original = db.mfa_reserve_login_challenge_attempt
    backup_reservou, totp_terminou = threading.Event(), threading.Event()

    def _reserva(token):
        r = original(token)
        if not backup_reservou.is_set():  # a 1ª reserva é a do backup
            backup_reservou.set()
            totp_terminou.wait(timeout=10)
        return r

    monkeypatch.setattr(db, "mfa_reserve_login_challenge_attempt", _reserva)
    resps = {}
    t = threading.Thread(target=lambda: resps.update(
        backup=_verify(TestClient(dashboard.app), ch, conta["backups"][0], backup=True)))
    t.start()
    assert backup_reservou.wait(timeout=10)
    resps["totp"] = _verify(client, ch, _certo(conta))
    totp_terminou.set()
    t.join(timeout=30)

    assert resps["totp"].status_code == 200, resps["totp"].text
    _recusa(resps["backup"], "mfa_challenge_expired", EXPIRADA)
    assert db.get_mfa_status(conta["uid"])["backup_codes_remaining"] == 10


def test_teto_por_conta_vale_entre_desafios_e_nao_vaza_para_outra():
    """5/min por conta no banco: trocar de desafio (e de IP) não renova o chute."""
    a, b = _conta_com_mfa(), _conta_com_mfa()
    client = TestClient(dashboard.app)
    for _ in range(5):
        _recusa(_verify(client, _desafio(client, a), _errado(a)), "mfa_code_invalid", "Código inválido.")

    ch = _desafio(client, a)
    resp = _verify(client, ch, _certo(a))  # nem o certo passa com o teto estourado
    assert resp.status_code == 429, resp.text
    assert resp.json() == {"detail": dashboard.RATE_LIMIT_DETAIL}
    assert "set-cookie" not in resp.headers
    assert _attempts(ch) == 1  # a tentativa contou no desafio

    resp = _verify(client, _desafio(client, b), _certo(b))
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == b["uid"]


# ── 7. isolamento: o código de B não abre o desafio de A ──────────────────────

def test_codigo_de_outro_usuario_nao_abre_o_desafio():
    a, b = _conta_com_mfa(), _conta_com_mfa()
    client = TestClient(dashboard.app)
    ch_a = _desafio(client, a)

    resp = _verify(client, ch_a, _certo(b))
    _recusa(resp, "mfa_code_invalid", "Código inválido.")
    assert "set-cookie" not in resp.headers  # nenhuma sessão, nem de A nem de B

    resp = _verify(client, ch_a, _certo(a))
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == a["uid"]


# ── 8. E e I: vencido e inexistente → expirada, sem incrementar ───────────────

def test_desafio_vencido_responde_expirada_sem_contar():
    conta = _conta_com_mfa()
    client = TestClient(dashboard.app)
    ch = _desafio(client, conta)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "update mfa_login_challenges set expires_at = now() - interval '1 min' where token = %s",
            (ch,),
        )
        conn.commit()

    _recusa(_verify(client, ch, _certo(conta)), "mfa_challenge_expired", EXPIRADA)
    assert _attempts(ch) == 0


def test_desafio_vazio_ou_inexistente_responde_expirada():
    client = TestClient(dashboard.app)
    _recusa(_verify(client, "", "123456"), "mfa_challenge_expired", EXPIRADA)
    _recusa(_verify(client, "nao-existe", "123456"), "mfa_challenge_expired", EXPIRADA)
