"""Código de reautenticação por e-mail da exclusão de conta (S1).

Unidade (`emitir`/`confere_e_consome`), a rota `POST /auth/account/reauth-code` e os
casos do DELETE que dependem do código (conta Google, tentativas, isolamento A×B).
Banco e rotas são os de verdade; só o e-mail é capturado.

Controles (CLAUDE.md §3), cada um medido tirando o conserto e vendo o grupo vermelho:
  - tirar o `conn.commit()` antes do raise em `schedule_account_deletion` -> G2 e G4;
  - tirar `attempts < max` do UPDATE -> G4 ("5 errados + certo");
  - tirar a checagem de modo em `reauth-code` -> C1 (S);
  - positivo: G3, C1 (P e G) e o `ok` da unidade.
"""
from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db  # noqa: E402
import frontend.finance_bot_websocket_custom as dashboard  # noqa: E402
from _apoio_auth_app import SENHA_EXCLUSAO as SENHA, bearer, conta_exclusao, emails  # noqa: E402,F401
from db import reauth_codes  # noqa: E402
from db.connection import get_conn  # noqa: E402
from db.privacy import PASSWORD_NOT_SET_MSG  # noqa: E402

EXC = reauth_codes.EXCLUIR


def _linha(uid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select * from account_reauth_codes where user_id = %s", (uid,))
        return cur.fetchone()


def _confere(uid, code):
    with get_conn() as conn, conn.cursor() as cur:
        r = reauth_codes.confere_e_consome(cur, uid, EXC, code)
        conn.commit()
    return r


def _delete(c, conta, corpo, headers=None):
    from frontend.routes import shared
    shared.limiter.reset()   # 5/min por IP do slowapi: o assunto aqui são os tetos do código
    return c.request("DELETE", "/auth/account", headers=headers or bearer(conta.uid),
                     json={"confirmacao": "EXCLUIR", **corpo})


@pytest.fixture
def c(emails):
    return TestClient(dashboard.app, raise_server_exceptions=False)


@pytest.fixture
def contas():
    feitas = []
    yield feitas
    for uid in feitas:
        try:
            db.delete_user_data(uid)
        except Exception:  # noqa: BLE001 - já apagada pelo próprio teste
            pass


# ── unidade ──────────────────────────────────────────────────────────────────

def test_emitir_guarda_so_o_hash_e_o_ultimo_codigo_vale(contas):
    conta = conta_exclusao(); contas.append(conta.uid)
    a = reauth_codes.emitir(conta.uid, EXC)
    b = reauth_codes.emitir(conta.uid, EXC)
    assert len(a) == len(b) == 6 and a.isdigit()
    linha = _linha(conta.uid)
    assert a not in linha["code_hash"] and b not in linha["code_hash"] and len(linha["code_hash"]) == 64
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from account_reauth_codes where user_id = %s", (conta.uid,))
        assert cur.fetchone()["n"] == 1
    if a != b:   # 1 chance em 10^6 de serem iguais
        assert _confere(conta.uid, a) == "invalido"
    assert _confere(conta.uid, b) == "ok"


def test_confere_ok_marca_usado_e_nao_serve_de_novo(contas):
    conta = conta_exclusao(); contas.append(conta.uid)
    code = reauth_codes.emitir(conta.uid, EXC)
    assert _confere(conta.uid, f" {code[:3]} {code[3:]} ") == "ok"   # espaços são ignorados
    assert _linha(conta.uid)["used_at"] is not None
    assert _confere(conta.uid, code) == "expirado"


def test_confere_vencido_e_esgotado_sao_expirado(contas):
    conta = conta_exclusao(); contas.append(conta.uid)
    code = reauth_codes.emitir(conta.uid, EXC)
    db_sql = "update account_reauth_codes set expires_at = now() - interval '1 second' where user_id = %s"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(db_sql, (conta.uid,)); conn.commit()
    assert _confere(conta.uid, code) == "expirado"

    code = reauth_codes.emitir(conta.uid, EXC)
    errado = "000000" if code != "000000" else "111111"
    assert [_confere(conta.uid, errado) for _ in range(reauth_codes.MAX_TENTATIVAS)] == \
        ["invalido"] * reauth_codes.MAX_TENTATIVAS
    assert _confere(conta.uid, code) == "expirado"   # o certo depois do 5º erro não vale


@pytest.mark.parametrize("lixo", ["", "abc", "\x00123456", "١٢٣٤٥٦", "\ud800", "9" * 5000])
def test_confere_lixo_nunca_estoura(contas, lixo):
    conta = conta_exclusao(); contas.append(conta.uid)
    reauth_codes.emitir(conta.uid, EXC)
    assert _confere(conta.uid, lixo) == "invalido"


# ── POST /auth/account/reauth-code (C1) ──────────────────────────────────────

def test_c1_pedir_codigo_p_e_g_funciona_e_o_codigo_nunca_vai_na_resposta(c, emails, contas):
    for tipo in ("P", "G"):
        conta = conta_exclusao(tipo); contas.append(conta.uid)
        r = c.post("/auth/account/reauth-code", headers=bearer(conta.uid), json={})
        assert r.status_code == 200, (tipo, r.text)
        (para, code), = emails.codigos[-1:]
        assert para == conta.email
        assert code not in r.text and conta.email not in r.text
        assert r.json()["email_mascarado"] == f"{conta.email[0]}•••@example.com"
        assert r.json()["expires_in_minutes"] == 15
        assert code not in _linha(conta.uid)["code_hash"]


def test_c1_s_sem_credencial_recebe_409_e_nada_e_emitido(c, emails, contas):
    conta = conta_exclusao("S"); contas.append(conta.uid)
    r = c.post("/auth/account/reauth-code", headers=bearer(conta.uid), json={})
    assert r.status_code == 409 and r.json()["detail"] == PASSWORD_NOT_SET_MSG
    assert emails.codigos == [] and _linha(conta.uid) is None


def test_c1_conta_marcada_recebe_403_quarto_pedido_429_envio_falho_500(c, emails, contas):
    conta = conta_exclusao(); contas.append(conta.uid)
    h = bearer(conta.uid)
    emails.falha = True
    r = c.post("/auth/account/reauth-code", headers=h, json={})
    assert r.status_code == 500 and "código" in r.json()["detail"]
    emails.falha = False
    from frontend.routes import shared
    for _ in range(2):   # 3/h por conta (o 1º, que falhou, também contou)
        shared.limiter.reset()
        assert c.post("/auth/account/reauth-code", headers=h, json={}).status_code == 200
    shared.limiter.reset()
    assert c.post("/auth/account/reauth-code", headers=h, json={}).status_code == 429

    outra = conta_exclusao(); contas.append(outra.uid)
    from _apoio_auth_app import marca_exclusao   # a marca real: senha + código
    marca_exclusao(outra.uid, SENHA)
    shared.limiter.reset()
    assert c.post("/auth/account/reauth-code", headers=bearer(outra.uid), json={}).status_code == 403  # sessão NOVA: a revogação da marca já passou


def test_c1_sem_sessao_401_com_www_authenticate(c):
    r = c.post("/auth/account/reauth-code", json={})
    assert r.status_code == 401 and "www-authenticate" in r.headers


# ── DELETE com o código (G, conta Google sem senha) ──────────────────────────

def test_g1_g2_g3_sem_codigo_errado_certo(c, emails, contas):
    conta = conta_exclusao("G"); contas.append(conta.uid)
    r = _delete(c, conta, {})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "codigo_obrigatorio"

    code = reauth_codes.emitir(conta.uid, EXC)
    errado = "000000" if code != "000000" else "111111"
    r = _delete(c, conta, {"code": errado})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "codigo_invalido"
    assert "www-authenticate" not in r.headers          # recusa de credencial, não de sessão
    assert _linha(conta.uid)["attempts"] == 1           # a tentativa PERSISTIU apesar do erro

    r = _delete(c, conta, {"code": code})
    assert r.status_code == 200 and r.json()["status"] == "deleted", r.text
    assert emails.excluida == [conta.email]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select 1 from users where id = %s", (conta.uid,))
        assert cur.fetchone() is None


def test_g4_reusado_vencido_esgotado_e_antigo_sao_recusados(c, contas):
    conta = conta_exclusao("G"); contas.append(conta.uid)
    h = bearer(conta.uid)
    antigo = reauth_codes.emitir(conta.uid, EXC)
    novo = reauth_codes.emitir(conta.uid, EXC)
    if antigo != novo:
        r = _delete(c, conta, {"code": antigo}, h)
        assert (r.status_code, r.json()["detail"]["code"]) == (401, "codigo_invalido")
    # esgotado: o 5º erro fecha o código, e o certo depois dele já não vale
    errado = "000000" if novo != "000000" else "111111"
    for _ in range(reauth_codes.MAX_TENTATIVAS - (1 if antigo != novo else 0)):
        _delete(c, conta, {"code": errado}, h)
    r = _delete(c, conta, {"code": novo}, h)
    assert (r.status_code, r.json()["detail"]["code"]) == (401, "codigo_expirado")
    # vencido
    novo = reauth_codes.emitir(conta.uid, EXC)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update account_reauth_codes set expires_at = now() - interval '1 second'"
                    " where user_id = %s", (conta.uid,)); conn.commit()
    r = _delete(c, conta, {"code": novo}, h)
    assert (r.status_code, r.json()["detail"]["code"]) == (401, "codigo_expirado")
    # reusado: o certo vale uma vez só
    novo = reauth_codes.emitir(conta.uid, EXC)
    assert _delete(c, conta, {"code": novo}, h).status_code == 200
    assert _delete(c, conta, {"code": novo}, h).status_code == 401   # conta e sessão já não existem


@pytest.mark.parametrize("lixo", ["\x00123456", "١٢٣٤٥٦", "abc"])
def test_g5_codigo_lixo_da_401_nunca_500(c, contas, lixo):
    conta = conta_exclusao("G"); contas.append(conta.uid)
    reauth_codes.emitir(conta.uid, EXC)
    r = _delete(c, conta, {"code": lixo})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "codigo_invalido", r.text


# ── X: isolamento entre contas ───────────────────────────────────────────────

def test_x_codigo_de_uma_conta_nao_serve_na_outra(c, contas):
    a, b = conta_exclusao("G"), conta_exclusao("G")
    contas += [a.uid, b.uid]
    db.add_launch_and_update_balance(b.uid, "despesa", 10, None, "mercado")
    cod_a = reauth_codes.emitir(a.uid, EXC)
    r = _delete(c, b, {"code": cod_a})                    # B tenta com o código de A
    assert r.status_code == 401 and r.json()["detail"]["code"] == "codigo_expirado"
    assert _linha(a.uid)["attempts"] == 0 and _linha(a.uid)["used_at"] is None
    cod_b = reauth_codes.emitir(b.uid, EXC)
    assert _linha(a.uid)["code_hash"] != _linha(b.uid)["code_hash"] or cod_a == cod_b   # atrelado ao uid
    assert _delete(c, a, {"code": cod_a}).status_code == 200
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select 1 from users where id = %s", (b.uid,)); assert cur.fetchone()
        cur.execute("select 1 from launches where user_id = %s", (b.uid,)); assert cur.fetchone()
        cur.execute("select 1 from auth_sessions where user_id = %s and revoked_at is null", (b.uid,))
        assert cur.fetchone()
    assert _delete(c, b, {"code": cod_b}).status_code == 200
