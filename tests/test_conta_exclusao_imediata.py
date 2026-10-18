"""Exclusão IMEDIATA de conta (S1): frase, senha + código, MFA, guardas, retentativa.

`DELETE /auth/account` apaga no ato. Banco e rotas são os de verdade (TestClient +
Postgres); só o e-mail e a Stripe são falsos. Matriz do plano: F (frase), P (senha),
M (MFA), L (teto), E (export), T (Stripe), D (retentativa/em andamento), R1 (sessões),
mais a conversa inteira (pedir código -> excluir).

Controles do grupo (CLAUDE.md §3), cada um medido tirando o conserto e vendo vermelho:
  frase: sem a checagem -> F1; depois da credencial -> F3/F4; positivo F2.
  P-B: aceitar só a senha -> P3; conferir o código antes da senha -> P2; positivo P1.
  MFA: sem o bloco -> M1/M3; `mfa_consume_backup_code` (transação própria) -> M6;
       MFA antes da senha -> M5; positivos M2/M4.
  exclusão imediata: não chamar `excluir_conta_marcada` -> P1 e R1.
  em andamento: sem o retorno antecipado -> D2. retry: sem o `_restore` -> D1.
  revogação: sem os dois UPDATE -> D1 (sessão ainda viva); P2 é o positivo.
  export pendente: sem a guarda -> E1; E5: sem `consume_data_export_token` -> E5.
  Stripe: sem a guarda -> T1; falha como "sem assinatura" -> T3; positivos T2/T4.
  teto: sem `_check_persistent_rate_limit` -> L1.
"""
from __future__ import annotations

import os
import uuid

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

os.environ.setdefault("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())

import db  # noqa: E402
import frontend.finance_bot_websocket_custom as dashboard  # noqa: E402
from _apoio_auth_app import (  # noqa: E402,F401
    SENHA_EXCLUSAO as SENHA, bearer, conta_exclusao, emails, sessao_de, sql,
)
from db import reauth_codes  # noqa: E402
from db.connection import get_conn  # noqa: E402
from db.privacy import PASSWORD_NOT_SET_MSG  # noqa: E402
from frontend.routes import shared  # noqa: E402
from test_merge_users_orfaos import _tabelas_user_id  # noqa: E402

EXC = reauth_codes.EXCLUIR


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


def _nova(contas, tipo="P", mfa=False):
    conta = conta_exclusao(tipo, mfa)
    contas.append(conta.uid)
    return conta


def _del(c, conta, corpo=None, *, frase="EXCLUIR", headers=None):
    """DELETE com sessão Bearer nova. O slowapi (5/min por IP) é zerado a cada
    chamada: o assunto aqui são os tetos por conta, não o do IP."""
    shared.limiter.reset()
    c.cookies.clear()   # um GET anterior planta o cookie de CSRF; o app nativo não tem jar
    corpo = dict(corpo or {})
    if frase is not None:
        corpo["confirmacao"] = frase
    return c.request("DELETE", "/auth/account", headers=headers or bearer(conta.uid), json=corpo)


def _completo(conta, **extra):
    """Credenciais certas do modo da conta (código novo emitido agora)."""
    corpo = {"code": reauth_codes.emitir(conta.uid, EXC)}
    if sql_tem_senha(conta.uid):
        corpo["password"] = SENHA
    if conta.segredo:
        corpo["totp"] = pyotp.TOTP(conta.segredo).now()
    return {**corpo, **extra}


def sql_tem_senha(uid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select password_hash from auth_accounts where user_id = %s", (uid,))
        row = cur.fetchone()
    return bool(row and row["password_hash"])


def _um(q, *a):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        return cur.fetchone()


def _existe(uid):
    return _um("select 1 as x from users where id = %s", uid) is not None


def _codigo(uid):
    return _um("select attempts, used_at from account_reauth_codes where user_id = %s", uid)


def _backups_livres(uid):
    return _um("select count(*) as n from user_mfa_backup_codes where user_id = %s and used_at is null",
               uid)["n"]


def _bucket(uid, bucket):
    return _um("select attempts from auth_rate_limits where bucket = %s and identifier = %s",
               bucket, f"user:{uid}")


# ── F: a frase EXCLUIR, antes de tudo ────────────────────────────────────────

@pytest.mark.parametrize("frase", [None, "", "excluir conta", "SIM"])
def test_f1_frase_ausente_ou_errada_recusa_sem_gastar_nada(c, contas, frase):
    conta = _nova(contas, mfa=True)
    corpo = _completo(conta, totp=conta.backups[0])   # um backup REAL: o "intacto" abaixo mede ele
    r = _del(c, conta, corpo, frase=frase)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "confirmacao_invalida", r.text
    assert "www-authenticate" not in r.headers
    assert _codigo(conta.uid)["attempts"] == 0 and _codigo(conta.uid)["used_at"] is None   # F4
    assert _backups_livres(conta.uid) == 10                                                    # F3
    assert _bucket(conta.uid, "account-delete") is None
    assert _existe(conta.uid)


@pytest.mark.parametrize("frase", ["Excluir", " excluir ", "EXCLUÍR"])
def test_f2_variantes_da_frase_servem(c, contas, frase):
    conta = _nova(contas)
    r = _del(c, conta, _completo(conta), frase=frase)
    assert r.status_code == 200 and r.json()["status"] == "deleted", r.text


def test_f5_frase_errada_nao_conta_no_teto_por_conta(c, contas):
    conta = _nova(contas)
    for _ in range(11):
        assert _del(c, conta, {}, frase="nao").status_code == 400
    assert _del(c, conta, _completo(conta)).status_code == 200


# ── P: senha + código ────────────────────────────────────────────────────────

def test_p1_conversa_inteira_pede_codigo_exclui_e_tudo_some(c, emails, contas):
    conta = _nova(contas)
    sa1, sa2 = sessao_de(conta.uid), sessao_de(conta.uid)
    db.add_launch_and_update_balance(conta.uid, "despesa", 25, None, "mercado")
    h = {"Authorization": f"Bearer {sa1['access']}"}

    r = c.post("/auth/account/reauth-code", headers=h, json={})
    assert r.status_code == 200, r.text
    (_, code), = emails.codigos
    r = _del(c, conta, {"password": SENHA, "code": code}, headers=h)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert (corpo["status"], corpo["deleted"], corpo["grace_days"]) == ("deleted", True, 0)
    assert corpo["message"] == "Pronto, sua conta foi excluída."
    assert emails.excluida == [conta.email]

    assert not _existe(conta.uid)
    sobra = {}
    for t in _tabelas_user_id():
        n = _um(f"select count(*) as n from {t} where user_id = %s", conta.uid)["n"]
        if n:
            sobra[t] = n
    assert sobra == {}

    # R1: nenhuma das sessões nem dos refresh tokens volta a funcionar
    c.cookies.clear()
    for s in (sa1, sa2):
        me = c.get("/auth/me", headers={"Authorization": f"Bearer {s['access']}"})
        assert me.status_code == 401 and "www-authenticate" in me.headers
    c.cookies.clear()
    rt = c.post("/auth/refresh", json={}, headers={"Authorization": f"Bearer {sa2['refresh']}",
                                                    dashboard.APP_CLIENT_HEADER: "app"})
    assert rt.status_code == 401, rt.text
    c.cookies.clear()
    assert c.post("/auth/logout", json={}, headers=h).status_code == 200
    c.cookies.clear()
    c.cookies.clear()
    login = c.post("/auth/login", json={"email": conta.email, "password": SENHA})
    assert login.status_code == 401


def test_p2_senha_errada_nao_gasta_o_codigo_nem_derruba_a_sessao(c, contas):
    conta = _nova(contas)
    h = bearer(conta.uid)
    code = reauth_codes.emitir(conta.uid, EXC)
    r = _del(c, conta, {"password": "errada", "code": code}, headers=h)
    assert r.status_code == 401 and r.json()["detail"] == "Senha incorreta."
    assert "www-authenticate" not in r.headers
    assert _codigo(conta.uid)["attempts"] == 0
    assert c.get("/auth/me", headers=h).status_code == 200            # sessão viva
    assert _del(c, conta, {"password": SENHA, "code": code}, headers=h).status_code == 200   # P1


def test_p3_p4_p5_faltando_senha_ou_codigo(c, contas):
    conta = _nova(contas)
    code = reauth_codes.emitir(conta.uid, EXC)
    r = _del(c, conta, {"password": SENHA})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "codigo_obrigatorio"
    for corpo in ({"code": code}, {}, {"password": "", "code": code}):
        r = _del(c, conta, corpo)
        assert r.status_code == 400 and r.json()["detail"] == "Informe sua senha para confirmar a exclusão."
    assert _codigo(conta.uid)["attempts"] == 0 and _existe(conta.uid)


def test_s_sem_credencial_continua_409_mesmo_com_codigo(c, contas):
    conta = _nova(contas, "S")
    r = _del(c, conta, {"password": "x", "code": "000000"})
    assert r.status_code == 409 and r.json()["detail"] == PASSWORD_NOT_SET_MSG
    assert _existe(conta.uid)


# ── M: MFA ───────────────────────────────────────────────────────────────────

def test_m1_m3_sem_totp_ou_com_totp_errado_nada_e_consumido(c, contas):
    conta = _nova(contas, mfa=True)
    code = reauth_codes.emitir(conta.uid, EXC)
    r = _del(c, conta, {"password": SENHA, "code": code})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "mfa_obrigatorio"
    errado = "000000" if pyotp.TOTP(conta.segredo).now() != "000000" else "111111"
    r = _del(c, conta, {"password": SENHA, "code": code, "totp": errado})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "mfa_invalido"
    assert "www-authenticate" not in r.headers
    assert _codigo(conta.uid)["used_at"] is None and _backups_livres(conta.uid) == 10   # rollback
    assert _existe(conta.uid)


def test_m2_totp_certo_exclui(c, contas):
    conta = _nova(contas, mfa=True)
    assert _del(c, conta, _completo(conta)).status_code == 200


def test_m4_backup_exclui(c, contas):
    conta = _nova(contas, mfa=True)
    corpo = _completo(conta, totp=conta.backups[0])
    assert _del(c, conta, corpo).status_code == 200 and not _existe(conta.uid)


def test_m5_senha_errada_nao_queima_o_backup(c, contas):
    conta = _nova(contas, mfa=True)
    r = _del(c, conta, _completo(conta, password="errada", totp=conta.backups[0]))
    assert r.status_code == 401 and r.json()["detail"] == "Senha incorreta."
    assert _backups_livres(conta.uid) == 10
    # a senha vem ANTES do MFA: com os dois errados, quem responde é a senha
    r = _del(c, conta, _completo(conta, password="errada", totp="000000"))
    assert r.status_code == 401 and r.json()["detail"] == "Senha incorreta."


def test_m6_falha_depois_do_mfa_devolve_backup_e_codigo(c, contas, monkeypatch):
    conta = _nova(contas, mfa=True)
    import db.privacy as privacy

    def _estoura(*a, **k):
        raise RuntimeError("falha forçada depois do MFA")
    monkeypatch.setattr(privacy, "_table_exists", _estoura)   # roda depois da marca, antes do commit
    r = _del(c, conta, _completo(conta, totp=conta.backups[0]))
    assert r.status_code == 500
    assert _backups_livres(conta.uid) == 10
    assert _codigo(conta.uid)["used_at"] is None
    assert _um("select deletion_status from auth_accounts where user_id = %s", conta.uid)["deletion_status"] is None


# ── L: teto por conta ────────────────────────────────────────────────────────

def test_l1_11a_tentativa_na_hora_leva_429(c, contas):
    conta = _nova(contas)
    h = bearer(conta.uid)
    codigos = [_del(c, conta, {"password": "errada", "code": "000000"}, headers=h).status_code
               for _ in range(10)]
    assert codigos == [401] * 10                      # as 10 primeiras passam do teto
    r = _del(c, conta, {"password": "errada", "code": "000000"}, headers=h)
    assert r.status_code == 429 and "retry-after" in r.headers


# ── E: export pendente ───────────────────────────────────────────────────────

def _exporta(c, conta, emails, **corpo):
    shared.limiter.reset()
    c.cookies.clear()
    return c.post("/auth/account/export", headers=bearer(conta.uid), json=corpo)


def test_e1_e2_link_pendente_bloqueia_e_baixado_libera(c, emails, contas):
    conta = _nova(contas, mfa=True)
    assert _exporta(c, conta, emails, password=SENHA).status_code == 200
    corpo = _completo(conta, totp=conta.backups[0])   # backup real: "nada gasto" mede ele
    r = _del(c, conta, corpo)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "export_pendente"
    assert _codigo(conta.uid)["used_at"] is None and _backups_livres(conta.uid) == 10   # nada gasto
    token = emails.exports[-1].rsplit("/", 1)[1]
    assert c.get(f"/auth/account/export/download/{token}").status_code == 200
    assert _del(c, conta, corpo).status_code == 200


def test_e3_e5_envio_falho_nao_deixa_link_pendente(c, emails, contas):
    conta = _nova(contas)
    emails.falha = True
    assert _exporta(c, conta, emails, password=SENHA).status_code == 500
    emails.falha = False
    r = _exporta(c, conta, emails, password=SENHA)                    # E5: não é 429
    assert r.status_code == 200, r.text
    token = emails.exports[-1].rsplit("/", 1)[1]
    c.get(f"/auth/account/export/download/{token}")
    assert _del(c, conta, _completo(conta)).status_code == 200


def test_e4_export_por_modo_da_conta(c, emails, contas):
    p, g, s, pm = (_nova(contas, "P"), _nova(contas, "G"), _nova(contas, "S"), _nova(contas, "P", True))
    r = _exporta(c, p, emails, password="errada")
    assert r.status_code == 401
    r = _exporta(c, p, emails, password=SENHA)
    assert r.status_code == 200
    assert r.json()["email_mascarado"] == f"{p.email[0]}•••@example.com" and p.email not in r.text
    assert _exporta(c, g, emails).status_code == 200                   # sem senha, só o e-mail da conta
    assert _exporta(c, s, emails, password=SENHA).status_code == 409
    assert _exporta(c, pm, emails, password=SENHA).status_code == 200  # export não pede TOTP


# ── T: Stripe ────────────────────────────────────────────────────────────────

def _com_stripe(conta, monkeypatch, resultado):
    sql("update auth_accounts set stripe_customer_id = 'cus_teste' where user_id = %s", conta.uid)
    chamadas = []

    def _acha(stripe, customer):
        chamadas.append(customer)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado
    monkeypatch.setattr(dashboard, "_find_active_subscription", _acha)
    return chamadas


def test_t1_assinatura_viva_com_renovacao_recusa(c, contas, monkeypatch):
    conta = _nova(contas)
    _com_stripe(conta, monkeypatch, {"id": "sub_1", "cancel_at_period_end": False})
    r = _del(c, conta, _completo(conta))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "assinatura_ativa"
    assert _existe(conta.uid) and _codigo(conta.uid)["used_at"] is None


def test_t2_renovacao_cancelada_exclui(c, contas, monkeypatch):
    conta = _nova(contas)
    _com_stripe(conta, monkeypatch, {"id": "sub_1", "cancel_at_period_end": True})
    assert _del(c, conta, _completo(conta)).status_code == 200


def test_t3_stripe_fora_do_ar_recusa_com_503_e_nao_apaga(c, contas, monkeypatch):
    conta = _nova(contas)
    _com_stripe(conta, monkeypatch, RuntimeError("stripe fora"))
    r = _del(c, conta, _completo(conta))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "stripe_indisponivel"
    assert _existe(conta.uid)


def test_t4_sem_customer_nao_consulta_a_stripe(c, contas, monkeypatch):
    conta = _nova(contas)
    chamadas = _com_stripe(conta, monkeypatch, RuntimeError("não devia chamar"))
    sql("update auth_accounts set stripe_customer_id = null where user_id = %s", conta.uid)
    assert _del(c, conta, _completo(conta)).status_code == 200 and chamadas == []


# ── D: retentativa e em andamento ────────────────────────────────────────────

def test_d1_exclusao_que_falha_vira_deleting_e_o_job_termina(c, emails, contas, monkeypatch):
    import db.privacy as privacy
    conta = _nova(contas)
    sessao = sessao_de(conta.uid)
    h = {"Authorization": f"Bearer {sessao['access']}"}
    real = privacy.delete_user_data
    monkeypatch.setattr(privacy, "delete_user_data",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("deadlock simulado")))
    r = _del(c, conta, _completo(conta), headers=h)
    assert r.status_code == 200 and (r.json()["status"], r.json()["deleted"]) == ("deleting", False), r.text
    assert emails.excluida == []
    row = _um("select deletion_status, deletion_scheduled_for <= now() as vencida from auth_accounts"
              " where user_id = %s", conta.uid)
    assert (row["deletion_status"], row["vencida"]) == ("scheduled", True)
    assert c.get("/auth/me", headers=h).status_code == 401           # sessões revogadas
    for tabela in ("auth_sessions", "auth_refresh_tokens"):
        assert _um(f"select count(*) as n from {tabela} where user_id = %s and revoked_at is null",
                   conta.uid)["n"] == 0
    c.cookies.clear()
    rt = c.post("/auth/refresh", json={}, headers={"Authorization": f"Bearer {sessao['refresh']}",
                                                    dashboard.APP_CLIENT_HEADER: "app"})
    assert rt.status_code == 401, rt.text

    monkeypatch.setattr(privacy, "delete_user_data", real)
    from scripts.account_deletion_job import run
    assert run(limit=5) == 0
    assert not _existe(conta.uid) and emails.excluida == [conta.email]


def test_d2_ja_em_andamento_responde_deleting_sem_conferir_nem_apagar(c, contas, monkeypatch):
    import db.privacy as privacy
    conta = _nova(contas)
    sql("update auth_accounts set deletion_status = 'processing', deletion_scheduled_for = now(),"
        " deletion_processing_started_at = now() where user_id = %s", conta.uid)
    code = reauth_codes.emitir(conta.uid, EXC)
    chamou = []
    monkeypatch.setattr(privacy, "delete_user_data", lambda *a, **k: chamou.append(1))
    r = _del(c, conta, {"password": "qualquer", "code": "000000"}, headers=bearer(conta.uid))
    assert r.status_code == 200 and r.json()["status"] == "deleting"
    assert chamou == [] and _codigo(conta.uid)["attempts"] == 0 and code


def test_d3_processing_velho_o_job_reivindica_e_apaga(contas):
    conta = _nova(contas)
    sql("update auth_accounts set deletion_status = 'processing', deletion_scheduled_for = now() - interval '3 hours',"
        " deletion_processing_started_at = now() - interval '3 hours' where user_id = %s", conta.uid)
    resultados = db.process_due_account_deletions(limit=10, stale_after_minutes=120)
    assert any(r["user_id"] == conta.uid and r["deleted"] for r in resultados)
    assert not _existe(conta.uid)


def test_d4_conta_agendada_antes_do_deploy_aceita_o_codigo_e_apaga_mas_a_rota_do_codigo_da_403(c, contas):
    conta = _nova(contas)
    sql("update auth_accounts set deletion_status = 'scheduled', deletion_requested_at = now(),"
        " deletion_scheduled_for = now() + interval '5 days' where user_id = %s", conta.uid)
    # a rota que EMITE o código recusa conta já agendada (403); o DELETE, com um código válido, apaga
    shared.limiter.reset()
    c.cookies.clear()
    assert c.post("/auth/account/reauth-code", headers=bearer(conta.uid), json={}).status_code == 403
    r = _del(c, conta, _completo(conta))
    assert r.status_code == 200 and r.json()["status"] == "deleted", r.text
    assert not _existe(conta.uid)


# ── C2: só o 401 de SESSÃO leva WWW-Authenticate ─────────────────────────────

def test_c2_401_de_sessao_leva_www_authenticate_e_os_de_credencial_nao(c, contas):
    conta = _nova(contas, mfa=True)
    sem_sessao = _del(c, conta, {}, headers={"Authorization": "Bearer nao-e-jwt"})
    assert sem_sessao.status_code == 401 and "www-authenticate" in sem_sessao.headers
    code = reauth_codes.emitir(conta.uid, EXC)
    for corpo in ({"password": "errada", "code": code},
                  {"password": SENHA, "code": "000000"},
                  {"password": SENHA, "code": code, "totp": "000000"}):
        r = _del(c, conta, corpo)
        assert r.status_code == 401 and "www-authenticate" not in r.headers, r.text


# ── a conversa pelo navegador: cookie + CSRF, como o site ────────────────────

def test_site_cookie_e_csrf_pedir_codigo_e_excluir(emails, contas):
    conta = _nova(contas)
    s = sessao_de(conta.uid)
    c = TestClient(dashboard.app, raise_server_exceptions=False)
    c.cookies.set(dashboard.AUTH_COOKIE_NAME, s["access"])
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, s["dashboard"])
    c.cookies.set(dashboard.CSRF_COOKIE_NAME, "t")
    h = {dashboard.CSRF_HEADER_NAME: "t"}
    assert c.post("/auth/account/reauth-code", json={}).status_code == 403          # sem CSRF
    assert c.post("/auth/account/reauth-code", json={}, headers=h).status_code == 200
    r = c.request("DELETE", "/auth/account", headers=h,
                  json={"password": SENHA, "code": emails.codigos[-1][1], "confirmacao": "EXCLUIR"})
    assert r.status_code == 200 and r.json()["status"] == "deleted", r.text
    assert "auth_token=" in r.headers["set-cookie"]   # cookies limpos na resposta
