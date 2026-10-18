"""Retomada do checkout abandonado, PR 4 (docs/plano-remarketing.md §3) — banco real.

Falso é só o mundo lá fora: o Stripe (`_patch_stripe`, `_pronto` do bump) e o Asaas
(`asaas_falso`). Cookie, token, dependência `comprador`, middleware de CSRF e as rotas
de compra rodam DE VERDADE, pelo HTTP.

T  o token: ida e volta, adulterado, vencido, forma do corpo, separação de domínio.
R  `GET /retomar`: os 4 destinos + cookie + `clicado_em`.
I  isolamento (quem vence): sessão B + cookie A = B; só cookie A = A com `rm_etapa`;
   cookie A + credencial de sessão INVÁLIDA = 401, nunca A; bump e poll de B com
   cookie A = 404; as outras rotas com só o cookie A = 401.
S  senha: 1 e-mail × 0, resposta igual, cookie expira, 3/h, CSRF.
M  máscara: nenhum corpo devolve e-mail inteiro.

Controles (rodados; ver o relato do PR):
  · `traz_sessao` sempre False (sem a guarda)       → I[invalida-*] vermelhos (A recebe 200);
  · rótulo igual ao do `agent_chat`                 → T separação vermelho;
  · `rm_etapa` fora do `_checkout_session_matches`  → I reuso vermelho;
  · sem o 409 `retomada_sem_migracao`               → I pix vermelho;
  · senha enviada para conta com credencial         → S "com senha" vermelho;
  · `marcar_clique` sem `etapa` no where            → R isolamento da etapa vermelho;
  · cookie = o token do LINK (sem `cookie=True`)    → T cookie×link, R feliz, I cookie-do-link e
    I cookie-vencido vermelhos (o do link vale 7 dias e abre as rotas de compra à mão).
  Positivos do grupo: sessão real de A responde fora do 401 nas rotas fechadas; cookie
  de A sozinho compra (200); bump do dono com o próprio cookie (200).
"""
from __future__ import annotations

import time

import pytest
from cryptography.fernet import InvalidToken
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.retomada as rota
from _pix_checkout_helpers import _linhas, asaas_falso, vendavel  # noqa: F401 - fixtures
from core.services import agent_chat, retomada
from core.services.email_service import make_unsub_url
from db.connection import get_conn
from test_billing_checkout import (  # noqa: F401 - `_reset_rate_limiter` é fixture autouse
    _CSRF_HEADERS, _CSRF_TOKEN, _patch_stripe, _reset_rate_limiter,
)
from test_checkout_bump import _SID, _bump, _pronto
from test_pix_extras_checkout import loja  # noqa: F401 - fixture
from token_utils import make_dashboard_token

_CPF = "52998224725"


def _sql(sql, params=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        linhas = cur.fetchall() if cur.description else []
        conn.commit()
    return [dict(r) for r in linhas]


def _conta(sufixo, senha=True):
    email = f"retomada-{sufixo}@dominio.com"
    uid = int(db.register_auth_user(email, "senha-forte-123")["user_id"])
    if not senha:
        _sql("update auth_accounts set password_hash = '' where user_id = %s", (uid,))
    return uid, email


def _cliente(retomada_de=None, etapa=1, *, auth=None, painel=None, bearer=None):
    """Navegador com o jar pedido. `auth`/`painel` aceitam um uid (sessão válida) ou texto."""
    c = TestClient(dashboard.app)
    c.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF_TOKEN)
    if retomada_de is not None:
        c.cookies.set(rota.COOKIE, retomada.emitir_token(retomada_de, etapa, cookie=True))
    if auth is not None:
        c.cookies.set(dashboard.AUTH_COOKIE_NAME,
                      dashboard._make_jwt(auth, "x@x.com") if isinstance(auth, int) else auth)
    if painel is not None:
        c.cookies.set("dashboard_token",
                      make_dashboard_token(painel, hours=1) if isinstance(painel, int) else painel)
    if bearer is not None:
        c.headers["Authorization"] = f"Bearer {bearer}"
    return c


def _stripe(monkeypatch):
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_xxx")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_mensal_abc")
    return _patch_stripe(monkeypatch)


def _checkout(c, **corpo):
    return c.post("/billing/create-checkout", json={"plan": "plus", **corpo}, headers=_CSRF_HEADERS)


# ── T. o token ──────────────────────────────────────────────────────────────

def test_t_ida_e_volta():
    assert retomada.ler_token(retomada.emitir_token(77, 2)) == (77, 2)


def test_t_adulterado_vencido_e_lixo_sao_none():
    tok = retomada.emitir_token(77, 1)
    trocado = tok[:-4] + ("AAAA" if tok[-4:] != "AAAA" else "BBBB")
    antigo = retomada._cipher().encrypt_at_time(
        b'{"u": 77, "e": 1}', int(time.time()) - retomada.TTL_S - 10).decode()
    for ruim in (trocado, antigo, "", None, "nao-e-fernet", "é", tok[:20]):
        assert retomada.ler_token(ruim) is None, ruim
    # o limite: pouco antes de vencer ainda vale (positivo do grupo)
    vivo = retomada._cipher().encrypt_at_time(
        b'{"u": 77, "e": 1}', int(time.time()) - retomada.TTL_S + 60).decode()
    assert retomada.ler_token(vivo) == (77, 1)


@pytest.mark.parametrize("corpo", [
    b'{"u": 77, "e": 4}', b'{"u": 77, "e": 0}', b'{"u": 77, "e": true}', b'{"u": "77", "e": 1}',
    b'{"u": true, "e": 1}', b'{"u": 77}', b'[77, 1]', b'"x"', b'nao json',
])
def test_t_forma_do_corpo_e_estrita(corpo):
    assert retomada.ler_token(retomada._cipher().encrypt(corpo).decode()) is None


def test_t_etapa_fora_de_1_a_3_nao_se_emite():
    for etapa in (0, 4, 9, True, "1"):
        with pytest.raises(ValueError):
            retomada.emitir_token(1, etapa)


def test_t_separacao_de_dominio():
    """Token de um uso do `JWT_SECRET` não abre o de outro, nos dois sentidos."""
    ctx = agent_chat.encode_context(77, "xerife", [])
    unsub = make_unsub_url(77, "a@b.com").split("token=")[1]
    jwt = dashboard._make_jwt(77, "a@b.com")
    for alheio in (ctx, unsub, jwt):
        assert retomada.ler_token(alheio) is None
    with pytest.raises(InvalidToken):
        agent_chat._cipher().decrypt(retomada.emitir_token(77, 1).encode())
    assert dashboard._decode_jwt(retomada.emitir_token(77, 1)) is None


def test_t_cookie_e_link_nao_se_aceitam_nos_dois_sentidos():
    link, cookie = retomada.emitir_token(77, 1), retomada.emitir_token(77, 1, cookie=True)
    assert retomada.ler_token(cookie, cookie=True) == (77, 1) and retomada.ler_token(link) == (77, 1)
    assert retomada.ler_token(link, cookie=True) is None and retomada.ler_token(cookie) is None


def test_t_cookie_vale_2h_no_servidor_e_o_link_7_dias():
    assert retomada.COOKIE_MAX_AGE == 2 * 3600 < retomada.TTL_S
    corpo = b'{"u": 77, "e": 1}'
    for idade, cookie_vale in ((retomada.COOKIE_MAX_AGE - 60, True), (retomada.COOKIE_MAX_AGE + 60, False)):
        tok = retomada._cipher(True).encrypt_at_time(corpo, int(time.time()) - idade).decode()
        assert (retomada.ler_token(tok, cookie=True) == (77, 1)) is cookie_vale
    velho_do_link = retomada._cipher().encrypt_at_time(corpo, int(time.time()) - 3 * 3600).decode()
    assert retomada.ler_token(velho_do_link) == (77, 1)   # o link segue valendo 7 dias


def test_t_controle_rotulo_igual_ao_do_agent_chat_nao_separa(monkeypatch):
    """O controle negativo EM PROCESSO: com o rótulo do `agent_chat` a decifração do outro
    domínio funciona, então o teste de cima mede a separação e não passa por acaso."""
    monkeypatch.setattr(retomada, "_ROTULO", b"pigbank:agent-chat:context:v1")
    agent_chat._cipher().decrypt(retomada.emitir_token(77, 1).encode())   # não levanta


def test_t_mascara():
    assert retomada.mascarar_email("lucas@dominio.com") == "l•••@dominio.com"
    assert retomada.mascarar_email("") == "•••@"


# ── R. GET /retomar ─────────────────────────────────────────────────────────

def _retomar(c, token, qs=""):
    return c.get(f"/retomar?t={token}{qs}", follow_redirects=False)


def _set_cookie(r):
    return [v for k, v in r.headers.multi_items() if k.lower() == "set-cookie"
            and v.startswith(rota.COOKIE + "=")]


def test_r_token_invalido_vai_a_precos_sem_cookie():
    r = _retomar(_cliente(), "lixo")
    assert (r.status_code, r.headers["location"]) == (303, "/precos") and _set_cookie(r) == []


def test_r_conta_inexistente_ou_em_exclusao_vai_a_precos_sem_cookie():
    inexistente = _retomar(_cliente(), retomada.emitir_token(2_000_000_000, 1))
    assert (inexistente.status_code, inexistente.headers["location"]) == (303, "/precos")
    uid, _ = _conta("r-exclusao")
    _sql("update auth_accounts set deletion_status = 'scheduled',"
         " deletion_scheduled_for = now() + interval '7 days' where user_id = %s", (uid,))
    r = _retomar(_cliente(), retomada.emitir_token(uid, 1))
    assert (r.status_code, r.headers["location"]) == (303, "/precos")
    assert _set_cookie(inexistente) == [] and _set_cookie(r) == []


def test_r_quem_ja_comprou_vai_ao_login_sem_cookie():
    uid, _ = _conta("r-comprou")
    _sql("update auth_accounts set last_payment_status = 'active' where user_id = %s", (uid,))
    r = _retomar(_cliente(), retomada.emitir_token(uid, 1))
    assert (r.status_code, r.headers["location"]) == (303, "/login") and _set_cookie(r) == []


@pytest.mark.parametrize("jar", [
    {"auth": "qualquer"}, {"painel": "qualquer"}, {"bearer": "qualquer"},
], ids=["cookie-de-acesso", "cookie-do-painel", "header"])
def test_r_pedido_com_sessao_nao_troca_de_comprador(jar):
    uid, _ = _conta("r-sessao")
    r = _retomar(_cliente(**jar), retomada.emitir_token(uid, 1))
    assert (r.status_code, r.headers["location"]) == (303, "/precos") and _set_cookie(r) == []


def test_r_feliz_grava_cookie_clique_e_repassa_so_utm(monkeypatch):
    uid, _ = _conta("r-feliz")
    outro, _ = _conta("r-feliz-outro")
    for u, e in ((uid, 1), (uid, 2), (outro, 2)):
        _sql("insert into remarketing_envios (user_id, etapa, status) values (%s, %s, 'enviado')", (u, e))
    monkeypatch.setattr(dashboard, "COOKIE_SECURE", True)
    tok = retomada.emitir_token(uid, 2)
    r = _retomar(_cliente(), tok, "&utm_source=wa&utm_campaign=rm+2&foo=1")
    assert r.status_code == 303
    assert r.headers["location"] == "/precos?utm_source=wa&utm_campaign=rm+2"
    assert tok not in r.headers["location"] and r.headers["cache-control"] == "no-store"
    cookie, = _set_cookie(r)
    for atributo in ("HttpOnly", "Secure", "SameSite=strict", "Max-Age=7200", "Path=/"):
        assert atributo.lower() in cookie.lower(), cookie
    # o `=` do final do Fernet faz o Starlette aspar o valor (RFC 6265 aceita; o parser desaspa)
    valor = cookie.split(";")[0].split("=", 1)[1].strip('"')
    assert valor != tok and retomada.ler_token(valor) is None            # o do link não é o do cookie
    assert retomada.ler_token(valor, cookie=True) == (uid, 2)
    marcados = {(x["user_id"], x["etapa"]): x["clicado_em"] for x in _sql(
        "select user_id, etapa, clicado_em from remarketing_envios where user_id in (%s, %s)",
        (uid, outro))}
    assert marcados[(uid, 2)] is not None
    assert marcados[(uid, 1)] is None and marcados[(outro, 2)] is None   # só a etapa do token, só o dono
    primeiro = marcados[(uid, 2)]
    _retomar(_cliente(), tok)
    assert _sql("select clicado_em from remarketing_envios where user_id = %s and etapa = 2",
                (uid,))[0]["clicado_em"] == primeiro                      # só se ainda nulo


def test_r_o_cookie_que_o_retomar_grava_volta_aceito_pelo_jar():
    """Ponta a ponta no jar de um navegador: /retomar grava, a rota seguinte lê."""
    uid, _ = _conta("r-jar")
    c = _cliente()
    _retomar(c, retomada.emitir_token(uid, 3))
    assert c.get("/billing/plans-config").json()["retomada"] == {"email": "r•••@dominio.com"}


def test_r_sem_utm_vai_a_precos_puro_e_pronto_e_inerte():
    uid, _ = _conta("r-puro")
    assert _retomar(_cliente(), retomada.emitir_token(uid, 1)).headers["location"] == "/precos"
    r = _cliente().get("/retomar/pronto", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login")


def test_r_clique_que_falha_nao_segura_a_compra(monkeypatch):
    import db.remarketing as rm
    uid, _ = _conta("r-falha")
    monkeypatch.setattr(rm, "marcar_clique", lambda *a: (_ for _ in ()).throw(RuntimeError("banco")))
    r = _retomar(_cliente(), retomada.emitir_token(uid, 1))
    assert r.status_code == 303 and len(_set_cookie(r)) == 1


def test_r_leitura_que_falha_vai_a_precos_sem_cookie_e_sem_clique(monkeypatch):
    import db.remarketing as rm
    uid, _ = _conta("r-leitura")
    _sql("insert into remarketing_envios (user_id, etapa, status) values (%s, 1, 'enviado')", (uid,))
    monkeypatch.setattr(rm, "ja_comprou", lambda *a: (_ for _ in ()).throw(RuntimeError("banco")))
    r = _retomar(_cliente(), retomada.emitir_token(uid, 1), "&utm_source=wa")
    assert (r.status_code, r.headers["location"]) == (303, "/precos?utm_source=wa")
    assert _set_cookie(r) == []
    assert _sql("select clicado_em from remarketing_envios where user_id = %s", (uid,))[0]["clicado_em"] is None


# ── I. isolamento: quem vence ───────────────────────────────────────────────

def test_i_so_o_cookie_de_a_compra_como_a_com_rm_etapa(monkeypatch):
    a, _ = _conta("i-a")
    fake = _stripe(monkeypatch)
    r = _checkout(_cliente(a, 2))
    assert r.status_code == 200, r.text
    k = fake.last_session_kwargs
    assert k["metadata"]["finbot_user_id"] == str(a) and k["metadata"]["rm_etapa"] == "2"
    assert k["subscription_data"]["metadata"]["rm_etapa"] == "2"          # a assinatura também
    assert k["success_url"] == f"{dashboard.DASHBOARD_URL}/retomar/pronto?sid={{CHECKOUT_SESSION_ID}}"
    assert fake.last_customer_kwargs["metadata"]["finbot_user_id"] == str(a)
    assert _sql("select count(*) as n from checkout_funnel_events where user_id = %s", (a,))[0]["n"] == 1


def test_i_retomada_embutida_volta_pela_pagina_da_retomada(monkeypatch):
    a, _ = _conta("i-emb")
    fake = _stripe(monkeypatch)
    monkeypatch.setattr(dashboard, "STRIPE_PUBLISHABLE_KEY", "pk_test_x")
    assert _checkout(_cliente(a), embutido=True, origem="assinar").status_code == 200
    k = fake.last_session_kwargs
    assert k["return_url"].endswith("/retomar/pronto?sid={CHECKOUT_SESSION_ID}")
    assert k["metadata"]["origem"] == "assinar"                           # `origem` segue a página


def test_i_sessao_de_b_mais_cookie_de_a_e_tudo_como_b(monkeypatch):
    a, _ = _conta("i-sa")
    b, _ = _conta("i-sb")
    fake = _stripe(monkeypatch)
    r = _checkout(_cliente(a, auth=b))
    assert r.status_code == 200, r.text
    k = fake.last_session_kwargs
    assert k["metadata"]["finbot_user_id"] == str(b) and "rm_etapa" not in k["metadata"]
    assert "rm_etapa" not in k["subscription_data"]["metadata"]
    assert "/home?upgrade=success" in k["success_url"]
    assert fake.last_customer_kwargs["metadata"]["finbot_user_id"] == str(b)


@pytest.mark.parametrize("jar", [
    {"auth": "jwt.invalido.x"}, {"painel": "jwt.invalido.x"}, {"bearer": "lixo"},
    {"auth": ""},
], ids=["invalida-acesso", "invalida-painel", "invalida-header", "invalida-vazio"])
def test_i_cookie_de_a_com_credencial_de_sessao_invalida_e_401_nunca_a(monkeypatch, jar):
    a, _ = _conta("i-inv")
    fake = _stripe(monkeypatch)
    r = _checkout(_cliente(a, **jar))
    assert r.status_code == 401, r.text
    assert fake.session_create_calls == 0 and fake.customer_create_calls == 0


def test_i_controle_sem_a_guarda_a_credencial_invalida_deixaria_o_cookie_passar(monkeypatch):
    """O controle negativo EM PROCESSO: sem `traz_sessao` o mesmo pedido vira compra de A."""
    a, _ = _conta("i-ctl")
    _stripe(monkeypatch)
    monkeypatch.setattr(rota, "traz_sessao", lambda request: False)
    # sem a guarda, o cookie de acesso inválido seria ignorado e o de retomada valeria
    assert _checkout(_cliente(a, auth="jwt.invalido.x")).status_code == 200


def test_i_cookie_vencido_adulterado_ou_ausente_e_401(monkeypatch):
    a, _ = _conta("i-ruim")
    fake = _stripe(monkeypatch)
    corpo = f'{{"u": {a}, "e": 1}}'.encode()
    antigo = retomada._cipher(True).encrypt_at_time(
        corpo, int(time.time()) - retomada.COOKIE_MAX_AGE - 10).decode()
    do_link = retomada.emitir_token(a, 1)   # válido por 7 dias, mas não é cookie
    for valor in (antigo, do_link, "lixo", retomada.emitir_token(a, 1, cookie=True)[:-3] + "xyz"):
        c = _cliente()
        c.cookies.set(rota.COOKIE, valor)
        assert _checkout(c).status_code == 401
    assert _checkout(_cliente()).status_code == 401
    assert fake.session_create_calls == 0


def test_i_token_do_link_posto_a_mao_no_cookie_nao_abre_nada(monkeypatch, vendavel, asaas_falso, loja,
                                                              reset_espiao):
    """M-1: o `/retomar` é a porta (e barra quem já comprou); o token do link no cookie à mão
    não contorna as guardas dele, e o cookie vence em 2 h no servidor."""
    a, _ = _conta("i-link", senha=False)
    fake = _stripe(monkeypatch)
    _sql("update auth_accounts set last_payment_status = 'active' where user_id = %s", (a,))
    c = _cliente()
    c.cookies.set(rota.COOKIE, retomada.emitir_token(a, 1))
    assert _checkout(c).status_code == 401 and _pix(c).status_code == 401
    assert c.get("/billing/plans-config").json()["retomada"] is None
    assert c.get("/billing/pix-extras").status_code == 401 and _senha(c).status_code == 401
    assert fake.session_create_calls == 0 and reset_espiao == [] and _linhas(a) == []
    # cookie de 3 h atrás (assinado com o rótulo certo): vencido no servidor
    c = _cliente()
    c.cookies.set(rota.COOKIE, retomada._cipher(True).encrypt_at_time(
        f'{{"u": {a}, "e": 1}}'.encode(), int(time.time()) - 3 * 3600).decode())
    assert _checkout(c).status_code == 401 and c.get("/billing/plans-config").json()["retomada"] is None
    # positivo do grupo: o caminho legítimo (/retomar -> compra) segue aberto para quem não comprou
    b, _ = _conta("i-link-ok")
    c = _cliente()
    assert _retomar(c, retomada.emitir_token(b, 1)).status_code == 303
    assert _checkout(c).status_code == 200


def test_i_conta_em_exclusao_nao_compra_pelo_cookie(monkeypatch):
    a, _ = _conta("i-del")
    fake = _stripe(monkeypatch)
    _sql("update auth_accounts set deletion_status = 'scheduled',"
         " deletion_scheduled_for = now() + interval '7 days' where user_id = %s", (a,))
    assert _checkout(_cliente(a)).status_code == 403
    assert fake.session_create_calls == 0


def test_i_csrf_vale_para_o_cookie_de_retomada(monkeypatch):
    a, _ = _conta("i-csrf")
    fake = _stripe(monkeypatch)
    r = _cliente(a).post("/billing/create-checkout", json={"plan": "plus"})   # sem o header
    assert r.status_code == 403 and fake.session_create_calls == 0


def test_i_sessao_de_retomada_e_a_logada_nao_se_reaproveitam(monkeypatch):
    a, _ = _conta("i-reuso")
    fake = _stripe(monkeypatch)
    assert _checkout(_cliente(a, 1)).status_code == 200 and fake.session_create_calls == 1
    assert _checkout(_cliente(a, 1)).status_code == 200
    assert (fake.session_create_calls, fake.session_expire_calls) == (1, 0)   # mesma etapa: reaproveita
    assert _checkout(_cliente(a, 2)).status_code == 200
    assert (fake.session_create_calls, fake.session_expire_calls) == (2, 1)   # outra etapa: nova
    assert _checkout(_cliente(auth=a)).status_code == 200
    assert (fake.session_create_calls, fake.session_expire_calls) == (3, 2)   # logado: nova
    assert _checkout(_cliente(auth=a)).status_code == 200
    assert (fake.session_create_calls, fake.session_expire_calls) == (3, 2)   # logado de novo: reaproveita
    assert _checkout(_cliente(a, 2)).status_code == 200
    assert (fake.session_create_calls, fake.session_expire_calls) == (4, 3)   # e a retomada não pega a logada


_FECHADAS = [
    ("GET", "/auth/me"), ("GET", "/auth/validate"), ("GET", "/billing/subscription"),
    ("POST", "/billing/change-plan"), ("POST", "/billing/cancel-change"),
    ("POST", "/billing/portal"), ("GET", "/settings/{uid}/security"),
    ("GET", "/api/v2/me"), ("GET", "/api/app/me"), ("GET", "/data/{uid}"),
    ("GET", "/history/{uid}"), ("POST", "/auth/dashboard-token"), ("POST", "/billing/select-free"),
]


@pytest.mark.parametrize("metodo,caminho", _FECHADAS)
def test_i_as_outras_rotas_nao_aceitam_o_cookie(monkeypatch, metodo, caminho):
    a, _ = _conta("i-fora")
    _stripe(monkeypatch)
    url = caminho.format(uid=a)
    r = _cliente(a).request(metodo, url, headers=_CSRF_HEADERS, json={} if metodo == "POST" else None)
    assert r.status_code == 401, (url, r.status_code, r.text[:200])
    # controle positivo: a SESSÃO real de A passa pela mesma rota (não é rota quebrada)
    ok = _cliente(auth=a, painel=a).request(
        metodo, url, headers=_CSRF_HEADERS, json={} if metodo == "POST" else None)
    assert ok.status_code != 401, (url, ok.status_code, ok.text[:200])


def test_i_bump_do_dono_com_o_proprio_cookie_e_de_b_com_cookie_de_a_e_404(monkeypatch):
    a, _ = _conta("i-bump-a")
    b, _ = _conta("i-bump-b")
    fake = _pronto(monkeypatch, b)
    alheio = _bump(_cliente(a), [1])
    assert alheio.status_code == 404 and fake.modifies == []
    assert _bump(_cliente(b), [1]).status_code == 200 and len(fake.modifies) == 1       # positivo
    assert _bump(_cliente(a, auth=b), [1]).status_code == 200                           # sessão de B vence
    assert _bump(_cliente(a, auth="jwt.invalido.x"), [1]).status_code == 401
    assert _bump(_cliente(), [1]).status_code == 401


def _pix(c, **extra):
    return c.post("/billing/pix/checkout", headers=_CSRF_HEADERS,
                  json={"plan": "pro", "cpf_cnpj": _CPF, **extra})


def test_i_pix_cookie_de_a_cobra_a_e_recusa_migracao(vendavel, asaas_falso):
    a, _ = _conta("i-pix-a")
    r = _pix(_cliente(a))
    assert r.status_code == 200, r.text
    assert len(_linhas(a)) == 1
    recusa = _pix(_cliente(a), confirm_cancel_stripe=True)
    assert recusa.status_code == 409 and recusa.json()["detail"] == {"error": "retomada_sem_migracao"}
    # a recusa é da RETOMADA: com sessão o pedido segue o caminho de sempre
    com_sessao = _pix(_cliente(a, painel=a), confirm_cancel_stripe=True)
    assert com_sessao.json().get("detail") != {"error": "retomada_sem_migracao"}


def test_i_pix_sessao_vence_e_credencial_invalida_e_401(vendavel, asaas_falso):
    a, _ = _conta("i-pix-sa")
    b, _ = _conta("i-pix-sb")
    assert _pix(_cliente(a, painel=b)).status_code == 200
    assert (len(_linhas(a)), len(_linhas(b))) == (0, 1)                    # cobrou B, não A
    assert _pix(_cliente(a, painel="jwt.invalido.x")).status_code == 401
    assert _pix(_cliente(a, auth=b)).status_code == 401                    # cookie de acesso não é o do painel
    assert len(_linhas(a)) == 0


def test_i_pix_poll_e_extras_so_do_dono(vendavel, asaas_falso, loja):
    a, _ = _conta("i-poll-a")
    b, _ = _conta("i-poll-b")
    assert _pix(_cliente(b), extras=["price_ca"]).status_code == 200
    token = _linhas(b)[0]["public_token"]
    assert _cliente(a).get(f"/billing/pix/{token}").status_code == 404
    assert _cliente(b).get(f"/billing/pix/{token}").status_code == 200     # positivo
    assert _cliente().get(f"/billing/pix/{token}").status_code == 401
    de_a = _cliente(a).get("/billing/pix-extras")
    assert de_a.status_code == 200 and de_a.json()["selecao"] == []
    assert _cliente(b).get("/billing/pix-extras").json()["selecao"] == ["price_ca"]


# ── M. plans-config: a máscara ──────────────────────────────────────────────

def test_m_plans_config_so_mascara_com_cookie_valido_e_sem_sessao():
    a, email = _conta("m-a")
    b, _ = _conta("m-b")
    sem_cookie = _cliente().get("/billing/plans-config")
    assert sem_cookie.json()["retomada"] is None and sem_cookie.headers["cache-control"] == "no-store"
    r = _cliente(a).get("/billing/plans-config")
    assert r.json()["retomada"] == {"email": "r•••@dominio.com"}
    assert email not in r.text and "retomada-m-a" not in r.text
    assert r.headers["cache-control"] == "no-store"
    for jar in ({"auth": b}, {"painel": b}, {"auth": "jwt.invalido.x"}, {"bearer": "x"}):
        assert _cliente(a, **jar).get("/billing/plans-config").json()["retomada"] is None, jar
    c = _cliente()
    c.cookies.set(rota.COOKIE, "lixo")
    assert c.get("/billing/plans-config").json()["retomada"] is None


def test_m_nenhum_corpo_do_modo_retomada_traz_email_inteiro_nem_nome(
        monkeypatch, vendavel, asaas_falso, loja, reset_espiao):
    a, email = _conta("m-corpos", senha=False)
    _sql("update auth_accounts set display_name = 'Fulana Silvestre' where user_id = %s", (a,))
    c = _cliente(a)
    pix = _pix(c, extras=["price_ca"])   # antes do `_stripe`: a `loja` usa o stripe real
    respostas = [c.get("/billing/plans-config"), pix,
                 c.get(f"/billing/pix/{_linhas(a)[0]['public_token']}"),
                 c.get("/billing/pix-extras"), _senha(c)]
    _stripe(monkeypatch)
    respostas.append(_checkout(c))
    for r in respostas:
        assert r.status_code == 200, (r.url, r.status_code, r.text[:200])
        for proibido in (email, email.split("@")[0], "Fulana", "Silvestre"):
            assert proibido not in r.text, (r.url, proibido)


def test_m_falha_na_mascara_nao_derruba_a_config(monkeypatch):
    import db.privacy as privacy
    a, _ = _conta("m-falha")
    monkeypatch.setattr(privacy, "get_user_email", lambda uid: (_ for _ in ()).throw(RuntimeError("banco")))
    r = _cliente(a).get("/billing/plans-config")
    assert r.status_code == 200 and r.json()["retomada"] is None


def test_m_conta_em_exclusao_nao_recebe_a_mascara():
    a, _ = _conta("m-del")
    _sql("update auth_accounts set deletion_status = 'scheduled',"
         " deletion_scheduled_for = now() + interval '7 days' where user_id = %s", (a,))
    assert _cliente(a).get("/billing/plans-config").json()["retomada"] is None


# ── S. senha ────────────────────────────────────────────────────────────────

@pytest.fixture()
def reset_espiao(monkeypatch):
    enviados = []
    monkeypatch.setattr(dashboard, "_reset_de_senha_em_segundo_plano", enviados.append)
    return enviados


def _senha(c):
    return c.post("/billing/retomada/senha", headers=_CSRF_HEADERS)


def test_s_conta_sem_credencial_recebe_um_email_e_com_senha_nenhum(reset_espiao):
    sem, email_sem = _conta("s-sem", senha=False)
    com, _ = _conta("s-com")
    r1 = _senha(_cliente(sem))
    assert (r1.status_code, reset_espiao) == (200, [email_sem])
    r2 = _senha(_cliente(com))
    assert reset_espiao == [email_sem]                                  # a conta com senha não recebe
    assert (r1.status_code, r1.json()) == (r2.status_code, r2.json())  # corpo e status iguais
    assert email_sem not in r1.text


def test_s_o_cookie_expira_na_resposta(reset_espiao):
    sem, _ = _conta("s-cookie", senha=False)
    apagado, = _set_cookie(_senha(_cliente(sem)))
    assert apagado.startswith(f'{rota.COOKIE}="";') and "max-age=0" in apagado.lower(), apagado


@pytest.mark.parametrize("jar", [None, {"auth": "x"}, {"painel": True}, {"bearer": "x"}],
                         ids=["sem-cookie", "com-acesso", "com-painel", "com-header"])
def test_s_exige_cookie_valido_e_nenhuma_sessao(reset_espiao, jar):
    sem, _ = _conta("s-401", senha=False)
    if jar is None:
        c = _cliente()
    else:
        c = _cliente(sem, **{k: (sem if v is True else v) for k, v in jar.items()})
    assert _senha(c).status_code == 401 and reset_espiao == []


def test_s_csrf_e_limite_de_3_por_hora(reset_espiao):
    sem, _ = _conta("s-limite", senha=False)
    assert _cliente(sem).post("/billing/retomada/senha").status_code == 403
    assert [_senha(_cliente(sem)).status_code for _ in range(4)] == [200, 200, 200, 429]


def test_s_conta_em_exclusao_nao_recebe(reset_espiao):
    sem, _ = _conta("s-del", senha=False)
    _sql("update auth_accounts set deletion_status = 'scheduled',"
         " deletion_scheduled_for = now() + interval '7 days' where user_id = %s", (sem,))
    assert _senha(_cliente(sem)).status_code == 403 and reset_espiao == []


def test_logout_expira_o_cookie_de_retomada():
    a, _ = _conta("lo")
    r = _cliente(a).post("/auth/logout", headers=_CSRF_HEADERS)
    assert r.status_code == 200
    apagado = _set_cookie(r)
    assert apagado and all(c.lower().startswith(f'{rota.COOKIE}="";') and "max-age=0" in c.lower()
                           and "path=/" in c.lower() and "httponly" in c.lower() for c in apagado), apagado


def _csrf_do_jar(c):
    vivos = [x.value for x in c.cookies.jar if x.name == dashboard.CSRF_COOKIE_NAME]
    return {dashboard.CSRF_HEADER_NAME: vivos[-1]}


def test_refresh_falho_nao_derruba_o_comprador_do_link(monkeypatch):
    """Cadeia real: /precos sem sessão dá 401 em /billing/subscription e /auth/me, o
    `auth-refresh.js` chama POST /auth/refresh (401) e o servidor limpa o jar. O cookie
    de retomada não pode sair daí: só logout e exclusão de conta o expiram."""
    uid, _ = _conta("rf")
    fake = _stripe(monkeypatch)
    c = TestClient(dashboard.app)   # jar vazio de verdade: o CSRF vem do servidor, como no navegador
    assert _retomar(c, retomada.emitir_token(uid, 1)).status_code == 303
    assert rota.COOKIE in c.cookies and dashboard.CSRF_COOKIE_NAME in c.cookies
    assert c.get("/billing/subscription").status_code == 401
    assert c.get("/auth/me").status_code == 401
    assert c.post("/auth/refresh", headers=_csrf_do_jar(c)).status_code == 401
    assert rota.COOKIE in c.cookies
    assert c.get("/billing/plans-config").json()["retomada"] == {"email": "r•••@dominio.com"}
    r = c.post("/billing/create-checkout", json={"plan": "plus"}, headers=_csrf_do_jar(c))
    assert r.status_code == 200, r.text
    assert fake.last_session_kwargs["metadata"]["finbot_user_id"] == str(uid)


def test_exclusao_de_conta_expira_o_cookie_de_retomada():
    a, _ = _conta("del")
    c = _cliente(a, auth=a, painel=a)
    from db.reauth_codes import EXCLUIR, emitir
    r = c.request("DELETE", "/auth/account", headers=_CSRF_HEADERS,
                  json={"password": "senha-forte-123", "code": emitir(a, EXCLUIR), "confirmacao": "EXCLUIR"})
    assert r.status_code == 200, r.text
    assert any(x.lower().startswith(f'{rota.COOKIE}="";') and "max-age=0" in x.lower()
               for x in _set_cookie(r)), _set_cookie(r)
