"""Login do leitor, versão da credencial, refresh, /me reduzido, isolamento e WebSocket (Postgres real)."""
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import conta_exclusao, csrf, emails, limpa_rate_limits, sessao_de, sql  # noqa: F401
from _apoio_leitura import SENHA_LEITOR, leitor_de, usuario_novo
from conftest import promote_to_pro
from core.crypto import hash_pii_optional
from db import reauth_codes
from frontend.routes.shared import payload_de_sessao


@pytest.fixture
def conta(monkeypatch, user_id):
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    promote_to_pro(user_id, plan="pro_max")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    monkeypatch.setenv("ACESSO_LEITURA_USER_IDS", str(user_id))
    return user_id


def _login(usuario, senha, client=None):
    client = client or TestClient(dashboard.app)
    return client.post("/auth/leitura/login", json={"usuario": usuario, "senha": senha}, headers=csrf(client))


def _zera(usuario):
    limpa_rate_limits("leitura-login", usuario)


# ── 6. login ─────────────────────────────────────────────────────────────────

def test_login_do_leitor_nao_pede_mfa_mesmo_com_mfa_do_dono(monkeypatch):
    monkeypatch.setenv("MFA_ENCRYPTION_KEY", Fernet.generate_key().decode())
    c = conta_exclusao("P", mfa=True)
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(c.uid))
    assert db.get_mfa_status(c.uid)["enabled"]   # controle: o dono TEM MFA
    usuario = usuario_novo()
    db.definir_credencial_leitura(c.uid, usuario, SENHA_LEITOR)
    _zera(usuario)
    r = _login(usuario, SENHA_LEITOR)
    assert r.status_code == 200, r.text
    assert "mfa_required" not in r.json() and r.json()["somente_leitura"] is True
    cookie = r.cookies.get(dashboard.AUTH_COOKIE_NAME) or r.cookies.get("auth_token")
    p = payload_de_sessao(cookie)
    assert p["ro"] is True and p["user_id"] == c.uid
    assert dashboard._decode_jwt(cookie)["email"] == ""  # o e-mail do dono não vai no token
    # e a sessão gravada é de leitura, na versão atual
    jti = p["jti"]
    from core.sessions import get_active_session
    assert get_active_session(jti)["somente_leitura"] is True


def _sessoes(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from auth_sessions where user_id = %s", (uid,))
        return cur.fetchone()["n"]


def test_dono_fora_do_rollout_v2_recusa_o_leitor_sem_emitir_sessao(conta, monkeypatch):
    monkeypatch.delenv("DASHBOARD_V2_TODOS", raising=False)
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "0")
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    usuario = usuario_novo()
    db.definir_credencial_leitura(conta, usuario, SENHA_LEITOR)
    _zera(usuario)
    antes = _sessoes(conta)
    r = _login(usuario, SENHA_LEITOR)
    assert r.status_code == 403 and r.json()["detail"]["error"] == "leitura_indisponivel", r.text
    assert not r.cookies and _sessoes(conta) == antes
    # a senha errada segue igual a antes: a elegibilidade só é checada depois da senha válida
    _zera(usuario)
    assert (_login(usuario, "senha-errada-123").status_code, _login("nao.existe.aqui", "x" * 12).status_code) == (401, 401)


def test_dono_no_rollout_v2_entra_e_o_destino_e_sempre_o_painel(conta):
    usuario = usuario_novo()
    db.definir_credencial_leitura(conta, usuario, SENHA_LEITOR)
    _zera(usuario)
    r = _login(usuario, SENHA_LEITOR)
    assert r.status_code == 200, r.text
    assert r.json()["dashboard_url"].endswith("/painel")


def _eventos_login(uid=None):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select user_id, email, email_enc, success, failure_reason, ip_address from auth_login_events "
                    "where failure_reason like 'leitura%%' and (user_id = %s or user_id is null) "
                    "order by id desc limit 5", (uid,))
        return cur.fetchall()


def test_login_de_leitor_deixa_rastro_sem_email_e_sem_alerta_ao_dono(conta, monkeypatch):
    from core.audit import is_known_login_ip
    leitor = leitor_de(conta)
    dono_email = db.get_auth_user(conta)["email"]
    c = TestClient(dashboard.app)
    r = _login(leitor["usuario"], leitor["senha"], c)
    assert r.status_code == 200
    _zera(leitor["usuario"])
    assert _login(leitor["usuario"], "senha-errada-9", c).status_code == 401
    [falha, ok] = _eventos_login(conta)[:2]
    assert (ok["user_id"], ok["success"], ok["failure_reason"]) == (conta, True, "leitura_login")
    assert (falha["user_id"], falha["success"], falha["failure_reason"]) == (None, False, "leitura_invalid_credentials")
    for e in (ok, falha):
        assert e["email"] is None and e["email_enc"] is None and e["ip_address"]
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from auth_login_events where email = %s", (dono_email,))
        assert cur.fetchone()["n"] == 0
        cur.execute("select count(*) as n from audit_events where user_id = %s", (conta,))
        assert cur.fetchone()["n"] == 0  # nenhum "login de novo IP" para o dono
    assert not is_known_login_ip(conta, ok["ip_address"])  # o IP do leitor não vira "conhecido" do dono


def test_senha_errada_e_usuario_inexistente_respondem_igual(conta):
    usuario = usuario_novo()
    db.definir_credencial_leitura(conta, usuario, SENHA_LEITOR)
    _zera(usuario); _zera("nao.existe.aqui")
    a = _login(usuario, "senha-errada-123")
    b = _login("nao.existe.aqui", "senha-errada-123")
    assert (a.status_code, a.json()) == (b.status_code, b.json()) == (401, {"detail": "Usuário ou senha incorretos."})


def test_login_tem_rate_limit(conta):
    usuario = usuario_novo()
    db.definir_credencial_leitura(conta, usuario, SENHA_LEITOR)
    _zera(usuario)
    codigos = [_login(usuario, "errada-errada").status_code for _ in range(7)]
    assert codigos[:5] == [401] * 5 and 429 in codigos[5:]
    _zera(usuario)


def test_conta_agendada_para_exclusao_nao_entra(conta):
    usuario = usuario_novo()
    db.definir_credencial_leitura(conta, usuario, SENHA_LEITOR)
    sql("update auth_accounts set deletion_status = 'processing', deletion_scheduled_for = now() where user_id = %s", conta)
    _zera(usuario)
    assert _login(usuario, SENHA_LEITOR).status_code == 403


def test_regras_do_usuario_e_da_senha(conta):
    for ruim in ("a@b.com", "ab", "Com Espaço", "-comeca.com.hifen", "x" * 41, ""):
        with pytest.raises(db.LeituraInvalida):
            db.definir_credencial_leitura(conta, ruim, SENHA_LEITOR)
    with pytest.raises(db.LeituraInvalida):
        db.definir_credencial_leitura(conta, usuario_novo(), "curta")
    with pytest.raises(db.LeituraInvalida):   # 73 bytes: o bcrypt estouraria ValueError
        db.definir_credencial_leitura(conta, usuario_novo(), "a" * 73)
    assert db.obter_credencial_leitura(conta) == {"ativo": False, "usuario": None}
    assert db.verificar_login_leitura("qualquer.um", "a" * 200) is None  # sem exceção


def test_usuario_duplicado_em_outra_caixa_e_409(conta, monkeypatch):
    outro = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(outro)
    nome = usuario_novo()
    db.definir_credencial_leitura(conta, nome, SENHA_LEITOR)
    with pytest.raises(db.UsernameEmUso):
        db.definir_credencial_leitura(outro, nome.upper(), SENHA_LEITOR)
    from conftest import _cleanup_user
    _cleanup_user(outro)


# ── endpoints do dono ────────────────────────────────────────────────────────

class _Fresco:
    """Cliente sem cookie jar entre chamadas: o Bearer não é credencial ambiente, então sem CSRF."""
    def request(self, m, url, **kw):
        return TestClient(dashboard.app).request(m, url, **kw)

    def get(self, url, **kw): return self.request("GET", url, **kw)
    def put(self, url, **kw): return self.request("PUT", url, **kw)
    def delete(self, url, **kw): return self.request("DELETE", url, **kw)
    def post(self, url, **kw): return self.request("POST", url, **kw)


def _dono(uid):
    return _Fresco(), {"Authorization": f"Bearer {sessao_de(uid)['access']}", "Content-Type": "application/json"}


def _cod(uid, finalidade=reauth_codes.LEITURA):
    return reauth_codes.emitir(uid, finalidade)


def _put(c, h, uid, usuario, senha, codigo=None):
    return c.put("/api/v2/conta/leitura", headers=h,
                 json={"usuario": usuario, "senha": senha, "codigo": _cod(uid) if codigo is None else codigo})


def _del(c, h, uid, codigo=None):
    return c.delete("/api/v2/conta/leitura", headers=h, json={"codigo": _cod(uid) if codigo is None else codigo})


def test_endpoints_do_dono(conta):
    c, h = _dono(conta)
    assert c.get("/api/v2/conta/leitura", headers=h).json() == {"ativo": False, "usuario": None}
    nome = usuario_novo()
    r = _put(c, h, conta, nome, SENHA_LEITOR)
    assert r.status_code == 200 and r.json() == {"ativo": True, "usuario": nome}
    assert "senha" not in c.get("/api/app/conta/leitura", headers=h).text and "hash" not in r.text
    assert _put(c, h, conta, "a@b.com", SENHA_LEITOR).status_code == 422
    assert _put(c, h, conta, nome + "x", "a" * 73).status_code == 422
    r = _del(c, h, conta)
    assert r.status_code == 200 and r.json() == {"ativo": False, "usuario": nome}


# ── freio: acesso_leitura no /me e recusa do PUT ──────────────────────────────

def _me(c, h):
    return c.get("/api/v2/me", headers=h).json()["acesso_leitura"]


def test_me_acesso_leitura_so_para_allowlist_e_nunca_para_leitor(conta, monkeypatch):
    c, h = _dono(conta)
    assert _me(c, h) is True
    assert _me(c, leitor_de(conta)["bearer"]) is False  # leitor nunca vê, mesmo com o dono na lista
    monkeypatch.setenv("ACESSO_LEITURA_USER_IDS", "")
    assert _me(c, h) is False
    monkeypatch.setenv("ACESSO_LEITURA_USER_IDS", f"1, {conta}x, {conta + 1}")
    assert _me(c, h) is False
    monkeypatch.setenv("ACESSO_LEITURA_USER_IDS", f"1, {conta}")
    assert _me(c, h) is True


@pytest.mark.parametrize("fora", ["allowlist", "rollout"])
def test_put_fora_da_allowlist_ou_do_rollout_recusa_sem_gastar_codigo(conta, monkeypatch, fora):
    c, h = _dono(conta)
    codigo = _cod(conta)
    if fora == "allowlist":
        monkeypatch.setenv("ACESSO_LEITURA_USER_IDS", "")
    else:
        monkeypatch.delenv("DASHBOARD_V2_TODOS", raising=False)
        monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", "0")
        monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    r = _put(c, h, conta, usuario_novo(), SENHA_LEITOR, codigo=codigo)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "acesso_leitura_indisponivel")
    assert db.obter_credencial_leitura(conta)["ativo"] is False
    monkeypatch.setenv("ACESSO_LEITURA_USER_IDS", str(conta))  # controle positivo: o MESMO código ainda vale (não foi gasto)
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(conta))
    assert _put(c, h, conta, usuario_novo(), SENHA_LEITOR, codigo=codigo).status_code == 200
    monkeypatch.setenv("ACESSO_LEITURA_USER_IDS", "")  # desativar segue liberado fora da lista
    assert _del(c, h, conta).status_code == 200 and c.get("/api/v2/conta/leitura", headers=h).status_code == 200


# ── reautenticação por código de e-mail (PUT/DELETE) ─────────────────────────

def _grava(c, h, uid, codigo):
    r = _put(c, h, uid, usuario_novo(), SENHA_LEITOR, codigo=codigo)
    return r.status_code, db.obter_credencial_leitura(uid)["usuario"]


def test_pedir_codigo_manda_email_ao_dono_e_o_codigo_vale_para_gravar(conta, emails):
    c, h = _dono(conta)
    r = c.post("/api/v2/conta/leitura/codigo", headers=h)
    assert r.status_code == 200 and "codigo" not in r.text.lower().replace("email_mascarado", "")
    [(para, codigo)] = emails.codigos
    assert para == db.get_auth_user(conta)["email"] and len(codigo) == 6
    assert _grava(c, h, conta, codigo)[0] == 200


def test_sem_codigo_ou_errado_recusa_e_nao_grava(conta):
    c, h = _dono(conta)
    _cod(conta)
    for codigo in ("", "000000" if _cod(conta) != "000000" else "111111"):
        status, usuario = _grava(c, h, conta, codigo)
        assert status == 400 and usuario is None, codigo
    r = c.put("/api/v2/conta/leitura", headers=h, json={"usuario": usuario_novo(), "senha": SENHA_LEITOR})
    assert r.status_code == 422 and db.obter_credencial_leitura(conta)["usuario"] is None  # corpo sem `codigo`


def test_codigo_de_outra_finalidade_e_recusado(conta):
    c, h = _dono(conta)
    assert _grava(c, h, conta, _cod(conta, reauth_codes.EXCLUIR)) == (400, None)


def test_codigo_e_de_uso_unico(conta):
    c, h = _dono(conta)
    codigo = _cod(conta)
    assert _grava(c, h, conta, codigo)[0] == 200
    antes = db.obter_credencial_leitura(conta)
    r = c.delete("/api/v2/conta/leitura", headers=h, json={"codigo": codigo})
    assert r.status_code == 400 and db.obter_credencial_leitura(conta) == antes


def test_codigo_vencido_e_recusado(conta):
    c, h = _dono(conta)
    codigo = _cod(conta)
    sql("update account_reauth_codes set expires_at = now() - interval '1 minute' where user_id = %s", conta)
    assert _grava(c, h, conta, codigo) == (400, None)


def test_codigo_de_um_dono_nao_vale_para_outro(conta):
    outra = conta_exclusao("P")
    c, h = _dono(conta)
    assert _grava(c, h, conta, _cod(outra.uid)) == (400, None)


def test_delete_sem_codigo_e_recusado_e_mantem_a_credencial(conta):
    c, h = _dono(conta)
    assert _grava(c, h, conta, _cod(conta))[0] == 200
    assert c.delete("/api/v2/conta/leitura", headers=h, json={"codigo": "xxxxxx"}).status_code == 400
    assert db.obter_credencial_leitura(conta)["ativo"] is True


def test_leitor_nao_pede_codigo(conta, emails):
    r = TestClient(dashboard.app).post("/api/v2/conta/leitura/codigo", headers=leitor_de(conta)["bearer"])
    assert r.status_code == 403 and emails.codigos == []


def test_put_com_usuario_de_outra_conta_da_409(conta):
    outra = conta_exclusao("P")
    nome = usuario_novo()
    db.definir_credencial_leitura(outra.uid, nome, SENHA_LEITOR)
    c, h = _dono(conta)
    r = _put(c, h, conta, nome.upper(), SENHA_LEITOR)
    assert r.status_code == 409


def test_leitor_nao_alcanca_a_gestao_da_credencial(conta):
    leitor = leitor_de(conta)
    c = TestClient(dashboard.app)
    for m in ("GET", "PUT", "DELETE"):
        r = c.request(m, "/api/v2/conta/leitura", headers=leitor["bearer"], json={"usuario": "xxxx", "senha": "yyyyyyyy"})
        assert r.status_code == 403, m


# ── 3. versão e revogação ────────────────────────────────────────────────────

def _estado(_c, leitor):
    c = _Fresco()
    me = c.get("/api/v2/me", headers=leitor["bearer"]).status_code
    ref = c.post("/auth/refresh", headers={"Authorization": f"Bearer {leitor['refresh']}",
                                           "Content-Type": "application/json"}).status_code
    return me, ref


def test_leitor_vivo_le_e_renova(conta):
    assert _estado(TestClient(dashboard.app), leitor_de(conta)) == (200, 200)


def test_nova_senha_derruba_o_leitor_antigo_em_get_e_refresh(conta):
    antigo = leitor_de(conta)
    c, h = _dono(conta)
    assert _put(c, h, conta, antigo["usuario"], "outra-senha-9").status_code == 200
    assert _estado(TestClient(dashboard.app), antigo) == (401, 401)


def test_desativar_derruba_o_leitor_e_bloqueia_o_login(conta):
    antigo = leitor_de(conta)
    c, h = _dono(conta)
    assert _del(c, h, conta).status_code == 200
    assert _estado(TestClient(dashboard.app), antigo) == (401, 401)
    _zera(antigo["usuario"])
    assert _login(antigo["usuario"], antigo["senha"]).status_code == 401


def test_versao_velha_na_linha_derruba_mesmo_sem_revogacao(conta):
    """A checagem de versão vale por si (SQL), não só pela revogação feita na troca."""
    antigo = leitor_de(conta)
    sql("update credenciais_leitura set versao = versao + 1 where user_id = %s", conta)
    assert _estado(TestClient(dashboard.app), antigo) == (401, 401)


def test_o_dono_segue_vivo_quando_a_credencial_muda(conta):
    dono = sessao_de(conta)
    leitor_de(conta)
    c, h = _dono(conta)
    _put(c, h, conta, usuario_novo(), "outra-senha-9")
    assert TestClient(dashboard.app).get("/api/v2/me", headers={"Authorization": f"Bearer {dono['access']}"}).status_code == 200


def test_troca_de_senha_do_dono_derruba_o_leitor(conta):
    """Pela rota real `/auth/reset-password` (o ponto de troca de senha), não pelas revogações soltas."""
    leitor = leitor_de(conta)
    email = db.get_auth_user(conta)["email"]
    sql("update auth_accounts set email_hash = %s where user_id = %s", hash_pii_optional(email, kind="email"), conta)
    token = db.create_password_reset_token(email)
    c = TestClient(dashboard.app)
    r = c.post("/auth/reset-password", headers=csrf(c), json={"token": token, "new_password": "senha-nova-456"})
    assert r.status_code == 200, r.text
    assert _estado(TestClient(dashboard.app), leitor) == (401, 401)


# ── 7. refresh preserva ro ───────────────────────────────────────────────────

def test_refresh_do_leitor_continua_leitor(conta):
    leitor = leitor_de(conta)
    c = TestClient(dashboard.app)
    r = c.post("/auth/refresh", headers={"Authorization": f"Bearer {leitor['refresh']}", dashboard.APP_CLIENT_HEADER: "app",
                                         "Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert payload_de_sessao(corpo["access_token"])["ro"] is True
    assert payload_de_sessao(corpo["dashboard_token"])["ro"] is True
    assert dashboard._decode_jwt(corpo["access_token"])["email"] == ""
    # o novo access ainda é barrado em escrita
    h = {"Authorization": f"Bearer {corpo['access_token']}"}
    assert c.post(f"/launches/{conta}", headers=h, json={}).status_code == 403
    assert c.get("/api/v2/me", headers=h).status_code == 200


def test_refresh_do_dono_continua_dono(conta):
    dono = sessao_de(conta)
    r = TestClient(dashboard.app).post("/auth/refresh", headers={"Authorization": f"Bearer {dono['refresh']}",
                                       dashboard.APP_CLIENT_HEADER: "app", "Content-Type": "application/json"})
    assert r.status_code == 200 and payload_de_sessao(r.json()["access_token"])["ro"] is False


def _renova(refresh):
    return TestClient(dashboard.app).post("/auth/refresh", headers={
        "Authorization": f"Bearer {refresh}", dashboard.APP_CLIENT_HEADER: "app", "Content-Type": "application/json"})


def test_replay_de_refresh_do_leitor_derruba_so_os_leitores(conta):
    from core.sessions import get_active_session
    dono, leitor = sessao_de(conta), leitor_de(conta)
    novo = _renova(leitor["refresh"])
    assert novo.status_code == 200
    assert _renova(leitor["refresh"]).status_code == 401           # replay do refresh já usado
    assert _renova(novo.json()["refresh_token"]).status_code == 401  # o leitor inteiro caiu
    assert get_active_session(leitor["jti"]) is None
    # o dono segue renovando e com a sessão viva
    assert get_active_session(dono["jti"]) is not None
    assert _renova(dono["refresh"]).status_code == 200


def test_replay_de_refresh_do_dono_continua_derrubando_tudo_do_usuario(conta):
    from core.sessions import get_active_session
    dono, outra, leitor = sessao_de(conta), sessao_de(conta), leitor_de(conta)
    assert _renova(dono["refresh"]).status_code == 200
    assert _renova(dono["refresh"]).status_code == 401              # replay do dono
    assert _renova(outra["refresh"]).status_code == 401
    assert _renova(leitor["refresh"]).status_code == 401
    assert get_active_session(dono["jti"]) is None


def test_token_de_dono_com_jti_de_leitor_nao_vale(conta):
    """Mesmo jti, marcas diferentes: a linha manda (sem `ro` no token = 401, sem escalar para dono)."""
    leitor = leitor_de(conta)
    sem_ro = dashboard.make_dashboard_token(conta, hours=1, jti=leitor["jti"])
    c = TestClient(dashboard.app)
    assert c.get("/api/v2/me", headers={"Authorization": f"Bearer {sem_ro}"}).status_code == 401


def test_token_ro_sem_jti_nao_vale(conta):
    import jwt
    from token_utils import _require_jwt_secret
    from datetime import datetime, timedelta, timezone
    t = jwt.encode({"sub": str(conta), "type": "dashboard", "ro": True,
                    "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, _require_jwt_secret(), algorithm="HS256")
    assert TestClient(dashboard.app).get("/api/v2/me", headers={"Authorization": f"Bearer {t}"}).status_code == 401


# ── 9. /me sem dado pessoal ──────────────────────────────────────────────────

def test_me_do_leitor_nao_leva_dado_pessoal(conta):
    sql("update auth_accounts set display_name = 'Fulana Dona', phone_e164 = '+5511999990000' where user_id = %s", conta)
    leitor = leitor_de(conta)
    c = TestClient(dashboard.app)
    dono_me = c.get("/auth/me", headers={"Authorization": f"Bearer {sessao_de(conta)['access']}"}).json()
    assert dono_me["email"] and dono_me.get("phone_e164")           # controle: o dono vê o seu
    r = c.get("/auth/me", headers=leitor["bearer"])
    assert r.status_code == 200
    me = r.json()
    texto = r.text
    assert me["somente_leitura"] is True and me["user_id"] == conta
    assert dono_me["email"] not in texto and "Fulana" not in texto and "999990000" not in texto
    assert not {"email", "phone_e164", "display_name", "stripe_customer_id"} & set(me)
    v2 = c.get("/api/v2/me", headers=leitor["bearer"])
    assert v2.status_code == 200 and v2.json()["familia"] is None and v2.json()["afiliado"] is False


# ── 4. isolamento ────────────────────────────────────────────────────────────

def test_leitor_de_a_nao_le_dados_de_b(conta):
    b = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(b)
    promote_to_pro(b, plan="pro_max")
    leitor = leitor_de(conta)
    c = TestClient(dashboard.app)
    for path in (f"/data/{b}", f"/history/{b}", f"/budgets/{b}", f"/cards/{b}/summary", f"/analytics/{b}/kpis"):
        r = c.get(path, headers=leitor["bearer"])
        assert r.status_code in (401, 403), (path, r.status_code)
    assert c.get(f"/data/{conta}", headers=leitor["bearer"]).status_code == 200   # controle: os dados do PRÓPRIO dono
    from conftest import _cleanup_user
    _cleanup_user(b)


# ── 5. WebSocket ─────────────────────────────────────────────────────────────

def _conta_launches(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from launches where user_id = %s", (uid,))
        return cur.fetchone()["n"]


def test_ws_do_leitor_le_e_fecha_4403_em_tipo_de_escrita(conta):
    leitor = leitor_de(conta)
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, leitor["dashboard"])
    antes = _conta_launches(conta)
    with c.websocket_connect(f"/ws/{conta}") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        ws.send_json({"type": "refresh"})
        assert ws.receive_json()["type"] == "update"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"
        ws.send_text("gastei 50 no mercado")                       # texto livre: tipo = o próprio texto
        ws.send_json({"type": "ping"})   # sem a trava o socket seguiria vivo e responderia: falha rápida, não trava
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4403
    for tipo in ("chat", "launch", "add_launch", "delete", ""):
        with c.websocket_connect(f"/ws/{conta}") as ws:
            ws.receive_json()
            ws.send_json({"type": tipo, "texto": "gastei 50 no mercado", "valor": 50})
            ws.send_json({"type": "ping"})
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_json()
            assert e.value.code == 4403, tipo
    assert _conta_launches(conta) == antes


def test_ws_do_dono_com_tipo_desconhecido_segue_aberto(conta):
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(conta)["dashboard"])
    with c.websocket_connect(f"/ws/{conta}") as ws:
        ws.receive_json()
        ws.send_json({"type": "chat"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"


def test_ws_do_leitor_com_credencial_trocada_nao_abre(conta):
    leitor = leitor_de(conta)
    db.definir_credencial_leitura(conta, usuario_novo(), "outra-senha-9")
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, leitor["dashboard"])
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(f"/ws/{conta}") as ws:
            ws.receive_json()


def test_ws_aberto_do_leitor_fecha_4401_quando_a_credencial_e_desativada(conta):
    leitor = leitor_de(conta)
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, leitor["dashboard"])
    with c.websocket_connect(f"/ws/{conta}") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "pong"          # controle positivo: vivo, serve
        db.desativar_credencial_leitura(conta)
        ws.send_json({"type": "refresh"})
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4401


# ── e-mail por finalidade, teto somado, 422 sem gastar o código ──────────────

def test_email_de_leitura_tem_o_texto_de_visualizacao_e_o_de_exclusao_o_de_exclusao(monkeypatch):
    import core.services.email_service as es
    enviados = []
    monkeypatch.setattr(es, "send_email", lambda **kw: enviados.append(kw) or True)
    es.send_account_reauth_code_email("a@example.com", "123456", reauth_codes.LEITURA)
    es.send_account_reauth_code_email("a@example.com", "123456", reauth_codes.EXCLUIR)
    leitura, exclusao = enviados
    for kw in (leitura, exclusao):
        assert "123456" in kw["text_body"] and "123456" not in kw["subject"]
    assert "visualização" in leitura["text_body"] and "excluir a conta" not in leitura["text_body"]
    assert "excluir a conta" in exclusao["text_body"] and "visualização" not in exclusao["text_body"]


def test_pedir_codigo_de_leitura_passa_a_finalidade_de_leitura(conta, emails):
    _dono(conta)[0].post("/api/v2/conta/leitura/codigo", headers=_dono(conta)[1])
    assert emails.finalidades == [reauth_codes.LEITURA]


def _pede(c, h, rota):
    from frontend.routes import shared
    shared.limiter.reset()   # o teto por IP (5/h) não é o assunto; o por conta é
    return c.post(rota, headers=h, json={}).status_code


@pytest.mark.parametrize("antes, depois", [
    ("/api/v2/conta/leitura/codigo", "/auth/account/reauth-code"),
    ("/auth/account/reauth-code", "/api/v2/conta/leitura/codigo"),
])
def test_teto_de_3_por_hora_soma_leitura_e_exclusao(emails, antes, depois):
    conta = conta_exclusao()
    c, h = _Fresco(), {"Authorization": f"Bearer {sessao_de(conta.uid)['access']}"}
    assert [_pede(c, h, antes) for _ in range(3)] == [200] * 3
    assert _pede(c, h, depois) == 429


@pytest.mark.parametrize("prefixo", ["/api/v2", "/api/app"])
def test_pedir_codigo_de_leitura_tem_teto_por_ip_nos_dois_namespaces(emails, prefixo):
    from frontend.routes import shared
    shared.limiter.reset()
    c = _Fresco()
    codigos = []
    for _ in range(6):   # contas diferentes: só o teto por IP (5/h) pode barrar
        h = {"Authorization": f"Bearer {sessao_de(conta_exclusao().uid)['access']}"}
        codigos.append(c.post(prefixo + "/conta/leitura/codigo", headers=h, json={}).status_code)
    assert codigos == [200] * 5 + [429]


def test_422_por_usuario_ou_senha_nao_gasta_o_codigo(conta):
    c, h = _dono(conta)
    codigo = _cod(conta)
    assert _put(c, h, conta, usuario_novo(), "curta", codigo=codigo).status_code == 422
    assert _put(c, h, conta, "a@b.com", SENHA_LEITOR, codigo=codigo).status_code == 422
    assert _put(c, h, conta, usuario_novo(), SENHA_LEITOR, codigo=codigo).status_code == 200
