"""#657 C3b (PR-8b do grupo D), conta e billing: a falha grava o dono na coluna `user_id` de
`system_event_logs`, só com tipo + sqlstate, sem `exc_info` e sem o texto cru (o `DETAIL` do
psycopg, a mensagem do Stripe ou do provedor de e-mail, que ecoa o endereço). E o desfecho de
hoje NÃO muda: o checkout que perde os extras ainda vende o plano, o 502/503/500 de hoje fica,
o que engole continua engolindo, e o `mark_bill_paid` relança a exceção ORIGINAL.

Chamada direta ou rota real, Postgres real, o `_DashboardHandler` do root. Nenhum site do C3 está
em laço por usuário (são por requisição ou por evento): o `FALHA_NO` não se aplica.
T-B1 Stripe; T-B2 webhook/Pix; T-B3 dinheiro; T-B4 o resto; T-B5 o `send_email` (P4).
Todo caso com dono roda A e B com exportação e exclusão reais (`_a_e_b`/`_isola_e_apaga`); o
T-B5 não tem dono (o `send_email` não recebe uid): a coluna fica nula de propósito.

CONTROLES (CLAUDE.md §3), medidos no PR, um por site (25): tirar o `extra` (ex.: `mark_bill_paid`,
o funil, o `cancel-change`) → o caso dele vermelho; `exc`/`str(exc)` de volta no texto (ex.: o
parcelado, o `change-plan`, o `details.erro` do admin e o par `_send`) → vermelho; `.exception`
de volta (ex.: a carteira, a IA) → vermelho; tirar o `as exc` (`NameError` no `except`) do
`mark_bill_paid` → vermelho (relança o `NameError`, não a original); `user_id` sem o `or None` no
admin → o caso de `user_id=0` vermelho (a FK derruba a linha); `to_log = to` → T-B5 vermelho.
Positivos: o `code` do Stripe aparece (P3) e o endereço MASCARADO aparece (P4), senão o grupo
passaria num código que parou de logar.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

import pytest
import stripe
from fastapi.testclient import TestClient
from resend.exceptions import raise_for_code_and_type

import core.admin_dashboard as admin
import core.observability as observability
import core.services.admin_notify as admin_notify
import core.services.email_service as email_service
import core.services.extras_assinar as extras_assinar
import db
import db.accounts as accounts
import db.checkout_funnel as funnel
import db.open_finance as open_finance
import db.rendimento_contratado as rendimento
import db.users as users
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from _log_dono_helpers import (  # noqa: F401 (banco_real é fixture autouse)
    _isola_e_apaga, _levanta, _limpa, _linhas, _marca, _marcador, _uid, _uma, banco_real)
from core.services.retomada import mascarar_email
from db.connection import get_conn
from db.signup_quiz import boas_vindas_da_conta
from frontend.routes import ai_chat
from test_account_deletion_pii_logs import _ERRO_ESPERADO, _FORMAS, _SEND_EMAIL_REAL, _texto_do_provedor
from test_billing_checkout import (  # noqa: F401 (`_reset_rate_limiter` é fixture autouse)
    _PRECOS, _FakeInvalidRequestError, _assinar_pronto, _auth_user_setup, _extras_env, _post,
    _reset_rate_limiter)
from test_category_launches_query import _cliente_logado
from test_log_dono_conta import MONOLITO, _a_e_b, _linha
from tests._patrimonio_helpers import conexao, posicao


def _app(uid: int) -> TestClient:
    c = TestClient(dashboard.app)
    c.headers.update({"Authorization": f"Bearer {sessao_de(uid)['access']}",
                      dashboard.APP_CLIENT_HEADER: "app", "Content-Type": "application/json"})
    return c


def _evento(marca: int, uid: int, event_type: str, source: str, *proibidos: str) -> dict:
    linha = _uma(_linhas(marca, "user_id = %s", (uid,)), event_type, source)
    _limpa(linha, *proibidos)
    return linha


# ── T-B1 Stripe ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code, esperado", [("resource_missing", "resource_missing"),
                                            (None, "_FakeInvalidRequestError")], ids=["com-code", "sem-code"])
def test_tb1_extras_recusados_logam_o_code_sem_a_mensagem(monkeypatch, capfd, code, esperado):
    """P3/P8: o `code` do Stripe, ou o tipo sem ele, vai no `details` (entrada em JUSTIFICADAS);
    a mensagem não, nem na linha nem no stderr."""
    clientes = {}

    def novo():
        uid, _, clientes[uid] = _auth_user_setup(uuid.uuid4().hex[:12])
        return uid

    fake, chamadas, mark = _assinar_pronto(monkeypatch), [], _marcador()
    _extras_env(monkeypatch, {1: ("price_x1", "https://pdf.test/1/segredo")})
    original = fake.checkout.Session.create

    def create(**kw):
        chamadas.append(kw)
        if "optional_items" in kw:
            raise _FakeInvalidRequestError(f"inactive price {mark}", param="optional_items[0][price]",
                                           code=code)
        return original(**kw)
    monkeypatch.setattr(fake.checkout.Session, "create", create)

    def caso(uid, marca):
        chamadas.clear()
        db.set_stripe_customer(uid, f"cus_{uid}")   # o fake cria sempre o mesmo `cus_test_123` (unique)
        assert _post(clientes[uid], _PRECOS).status_code == 200
        assert len(chamadas) == 2 and "optional_items" not in chamadas[1]
        linha = _evento(marca, uid, "ebook_oferta_recusada", "billing", mark, "pdf.test")
        assert linha["details"] == {"code": esperado, "precos": ["price_x1"]}, linha
        assert mark not in capfd.readouterr().err
        return linha
    _a_e_b(caso, novo)


def test_tb1_lock_do_checkout_falhando_responde_503(monkeypatch):
    clientes = {}

    def novo():
        uid, _, clientes[uid] = _auth_user_setup(uuid.uuid4().hex[:12])
        return uid

    def caso(uid, marca):
        mark = _marcador()
        _assinar_pronto(monkeypatch)
        monkeypatch.setattr(dashboard, "_billing_user_lock", _levanta(mark))
        assert _post(clientes[uid], _PRECOS).status_code == 503
        return _linha(marca, uid, "error", MONOLITO, "billing_checkout_lock_failed", mark)
    _a_e_b(caso, novo)


@pytest.mark.parametrize("erro, code", [
    (lambda m: stripe.error.InvalidRequestError(m, "price", code="resource_missing"), "resource_missing"),
    (lambda m: stripe.error.APIConnectionError(m), "APIConnectionError"),     # sem `code`: o tipo
], ids=["com-code", "sem-code"])
def test_tb1_extras_da_pagina_recusados_logam_o_code_ou_o_tipo(monkeypatch, capfd, erro, code):
    """P8: o `_recusa` de `core/services/extras_assinar.py` (página própria, Pix e bump)."""
    _extras_env(monkeypatch, {1: ("price_x1", "https://pdf.test/1/segredo")})

    def caso(uid, marca):
        mark = _marcador()

        def falha(*a, **k):
            raise erro(mark)
        monkeypatch.setattr(stripe.Price, "retrieve", falha)
        assert extras_assinar.ofertas_da_pagina(stripe, uid) == []
        linha = _evento(marca, uid, "ebook_oferta_recusada", "billing", mark, "pdf.test")
        assert linha["details"] == {"precos": ["price_x1"], "code": code}, linha
        assert mark not in capfd.readouterr().err
        return linha
    _a_e_b(caso)


@pytest.mark.parametrize("rota, metodo, prefixo", [
    ("/billing/change-plan", "retrieve", "change_plan_stripe_error"),
    ("/billing/cancel-change", "release", "cancel_change_stripe_error"),
], ids=["troca", "desfaz"])
def test_tb1_schedule_do_stripe_falhando_responde_502(monkeypatch, rota, metodo, prefixo):
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(dashboard, "_exigir_credencial", lambda uid: None)
    monkeypatch.setattr(dashboard, "_resolve_price_id", lambda plan, interval: "price_novo")
    monkeypatch.setattr(dashboard, "_find_active_subscription", lambda s, cust: {"id": "sub_x", "schedule": "sch_x"})
    monkeypatch.setattr(dashboard, "_sub_price_id", lambda sub: "price_velho")
    monkeypatch.setattr(dashboard, "_sub_period_end_ts", lambda sub: 2_000_000_000)

    def caso(uid, marca):
        mark, email = _marcador(), db.get_auth_user(uid)["email"]
        db.set_stripe_customer(uid, f"cus_{uid}")

        def falha(*a, **k):
            raise stripe.error.StripeError(f"{mark} {email}")
        monkeypatch.setattr(stripe.SubscriptionSchedule, metodo, falha)
        r = _app(uid).post(rota, json={"plan": "pro", "interval": "monthly"})
        assert r.status_code == 502, r.text
        return _linha(marca, uid, "error", MONOLITO, prefixo, mark, email,
                      causa="causa=StripeError sqlstate=None")
    _a_e_b(caso)


# ── T-B2 webhook/Pix: depois do grant, nunca levanta ────────────────────────

def test_tb2_founder_e_funil_com_o_banco_falhando_engolem(monkeypatch):
    def caso(uid, marca):
        mark, email = _marcador(), db.get_auth_user(uid)["email"]
        monkeypatch.setattr(observability, "recent_event_exists", _levanta(mark))
        assert email_service.send_founder_email_once(uid, email, "stripe", f"cs_{uid}") is False
        monkeypatch.setattr(funnel, "get_conn", _levanta(mark))
        assert db.record_checkout_started(uid, f"cs_{uid}") is None
        funil = _linha(marca, uid, "warning", "db.checkout_funnel", "checkout_funnel started falhou", mark)
        return funil, _linha(marca, uid, "error", "core.services.email_service", "E-mail do fundador falhou",
                      mark, email)
    _a_e_b(caso)


# ── T-B3 dinheiro ────────────────────────────────────────────────────────────

def test_tb3_carteira_com_o_consolidado_falhando_cai_no_saldo_cru(monkeypatch):
    def caso(uid, marca):
        mark = _marcador()
        db.add_launch_and_update_balance(uid, "receita", 37, "Espécie", None)   # saldo ≠ 0: `return 0` não passa
        monkeypatch.setattr(open_finance, "get_consolidated_balance", _levanta(mark))
        assert accounts.carteira_exibida(uid) == accounts.get_balance(uid) == 37
        return _linha(marca, uid, "error", "db.accounts", "carteira exibida falhou", mark)
    _a_e_b(caso)


# ── T-B4 o resto ─────────────────────────────────────────────────────────────

def test_tb4_cadastro_com_o_boas_vindas_falhando_cria_a_conta_sem_o_email_no_log(monkeypatch):
    marca, mark, ids = _marca(), _marcador(), {}
    emails = {}
    for _ in range(2):
        email = f"cad-{uuid.uuid4().hex[:12]}@exemplo.test"
        monkeypatch.setattr(email_service, "send_welcome_email", _levanta(f"{mark} {email}"))
        uid = int(db.register_auth_user(email, "senha-forte-123")["user_id"])
        emails[uid] = email
        ids[uid] = _linha(marca, uid, "warning", "db_support", "Falha ao enviar e-mail de boas-vindas",
                          mark, email)["id"]
    a, b = emails
    _isola_e_apaga(a, b, {ids[a]}, ids[b], marca)


def test_tb4_boas_vindas_e_vinculo_engolem(monkeypatch):
    def caso(uid, marca):
        mark, email, wa = _marcador(), db.get_auth_user(uid)["email"], f"5511{uid % 10**9:09d}"
        monkeypatch.setattr(email_service, "send_assinar_account_email", _levanta(f"{mark} {email}"))
        assert boas_vindas_da_conta(email, uid) is None
        quiz = _linha(marca, uid, "warning", "db.signup_quiz", "boas_vindas falhou", mark, email)
        users.bind_identity("whatsapp", wa, uid)
        monkeypatch.setattr(users, "_confirmar_telefone_do_whatsapp", _levanta(mark))
        assert users.link_platform_identity("whatsapp", wa, uid) == uid
        return quiz, _linha(marca, uid, "warning", "db.users", "link_platform_identity: marcador", mark, wa)
    _a_e_b(caso)


@pytest.mark.parametrize("onde", ["base", "calculo"])
def test_tb4_rendimento_sem_base_ou_calculo_mantem_os_itens(monkeypatch, onde):
    def caso(uid, marca):
        mark = _marcador()
        posicao(conexao(uid, f"i-{uid}"), "p", "1100")
        if onde == "base":   # o banco ecoa o valor na mensagem: `invalid input syntax … "MARC…"`
            monkeypatch.setattr(rendimento, "_BASE_SQL", f"select ('{mark}' || %(u)s)::int")
        else:
            monkeypatch.setattr(rendimento, "calcular", _levanta(mark))
        with get_conn() as conn, conn.cursor() as cur:
            r = rendimento.ler(cur, uid)
            conn.rollback()
        assert r["rendimentos"] is None and r["itens"], r
        if onde == "base":
            return _linha(marca, uid, "error", "db.rendimento_contratado", "rendimento: leitura da foto-base",
                          mark, causa="causa=InvalidTextRepresentation sqlstate=22P02")
        return _linha(marca, uid, "error", "db.rendimento_contratado", "rendimento: cálculo", mark)
    _a_e_b(caso)


def test_tb4_stream_da_ia_falhando_emite_o_evento_de_erro(monkeypatch):
    def caso(uid, marca):
        mark = _marcador()
        monkeypatch.setattr(ai_chat.streaming, "executar", _levanta(mark))

        async def consome():
            return [x async for x in ai_chat._fluxo(uid, f"oi {mark}", 10)]
        saida = asyncio.run(consome())
        assert any('"tipo": "erro"' in s and ai_chat.ERRO in s for s in saida), saida
        return _linha(marca, uid, "error", "frontend.routes.ai_chat", "ai_chat stream falhou", mark)
    _a_e_b(caso)


def test_tb4_pii_indecifravel_no_admin_sem_conta_grava_com_a_coluna_nula(monkeypatch):
    """`user_id=0` (login de e-mail desconhecido) não é `users.id`: a FK derrubaria a linha."""
    monkeypatch.setattr(admin, "decrypt_pii_optional", _levanta(_marcador()))
    campo, marca = f"campo-{uuid.uuid4().hex}", _marca()
    assert admin._decrypt_admin_field("ct", "fb", purpose="t", admin_user="adm", user_id=0, field=campo) == "fb"
    (linha,) = _linhas(marca, "user_id is null and source = %s and message like %s",
                       ("core.admin_dashboard", f"%{campo}%"))
    _limpa(linha)

    def caso(uid, marca):
        mark = _marcador()
        monkeypatch.setattr(admin, "decrypt_pii_optional", _levanta(mark))
        assert admin._decrypt_admin_field("ct", None, purpose="t", admin_user="adm", user_id=uid,
                                          field="email") == "⚠️ indecifrável"
        return _linha(marca, uid, "warning", "core.admin_dashboard", "PII indecifrável no admin", mark,
                      causa="")
    _a_e_b(caso)


def test_tb4_reparo_do_admin_sem_grant_loga_e_avisa_so_o_tipo(monkeypatch):
    avisos = []
    monkeypatch.setattr(admin_notify, "_send", avisos.append)

    def caso(uid, marca):
        mark = _marcador()
        monkeypatch.setattr(admin, "_gravar_grant_do_admin", _levanta(mark))
        assert admin.set_account_plan("pro", 1, user_id=uid)["plan"] == "pro"
        linha = _evento(marca, uid, "admin_grant_nao_gravado", "admin", mark)
        assert linha["details"]["erro"] == "UniqueViolation" and linha["details"]["sqlstate"] == "23505"
        return linha
    _a_e_b(caso)
    assert len(avisos) == 2, avisos


def test_tb4_aviso_do_reparo_ao_canal_do_admin_leva_so_o_tipo(monkeypatch):
    """O par do `admin_grant_nao_gravado` no MESMO `except`: o `_send` ao Slack/Discord."""
    avisos, mark, uid = [], _marcador(), _uid()
    monkeypatch.setattr(admin_notify, "_send", avisos.append)
    monkeypatch.setattr(admin, "_gravar_grant_do_admin", _levanta(mark))
    admin.set_account_plan("pro", 1, user_id=uid)
    assert avisos == [f"Reparo manual do admin na conta {uid} (pro) NAO virou grant: UniqueViolation"]


def test_tb4_aviso_do_download_da_exportacao_falhando_baixa_o_zip(monkeypatch):
    notificacoes, criar = [], asyncio.create_task
    monkeypatch.setattr(dashboard.asyncio, "create_task", lambda c, **k: (
        notificacoes.append(c) if c.__name__ == "_notify_completed" else criar(c, **k)))

    def caso(uid, marca):
        mark, email = _marcador(), db.get_auth_user(uid)["email"]
        monkeypatch.setattr(email_service, "send_data_export_completed_email", _levanta(f"{mark} {email}"))
        token, _ = db.create_data_export_token(uid)
        r = TestClient(dashboard.app).get(f"/auth/account/export/download/{token}")
        assert r.status_code == 200 and r.content[:2] == b"PK"
        asyncio.run(notificacoes.pop())
        return _linha(marca, uid, "warning", MONOLITO, "Falha ao enviar e-mail de confirmação de export",
                      mark, email, token)
    _a_e_b(caso)


def test_tb4_parcelado_falhando_responde_500_sem_a_nota(monkeypatch):
    monkeypatch.setattr(db, "get_card_by_id", lambda uid, cid: {"name": "Cartão", "of_sync_active": False})

    def caso(uid, marca):
        mark, nota = _marcador(), f"presente da dona zelia {uuid.uuid4().hex[:6]}"
        monkeypatch.setattr(db, "add_credit_purchase_installments", _levanta(f"{mark} {nota}"))
        c, h = _cliente_logado(uid)
        r = c.post(f"/launches/{uid}", headers=h, json={
            "tipo": "credito", "valor": 90, "card_id": 1, "parcelas": 3, "nota": nota, "categoria": "outros"})
        assert r.status_code == 500 and r.json()["detail"] == "Erro ao registrar parcelamento. Tente novamente."
        return _linha(marca, uid, "error", MONOLITO, "registrar_parcelamento", mark, nota)
    _a_e_b(caso)


@pytest.mark.parametrize("to", [["a@b.test"], 5, b"a@b.test", None], ids=["lista", "int", "bytes", "None"])
def test_tb5_send_email_com_to_que_nao_e_str_nao_levanta(monkeypatch, to):
    """"Nunca lança exceção": a máscara roda fora do `try`, e `to` sem tipo checado chega de fora."""
    class _Resend:
        class Emails:
            send = staticmethod(lambda params: {"id": "x"})
    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    monkeypatch.setattr(email_service, "send_email", _SEND_EMAIL_REAL)
    monkeypatch.setattr(email_service, "_get_resend", lambda: _Resend)
    assert email_service.send_email(to, "Assunto TB5 tipo", "<p>x</p>") is True


# ── T-B5 o `send_email` (P4): o endereço sai MASCARADO, o erro do provedor por lista branca ─

@pytest.mark.parametrize("forma", [None, *_FORMAS], ids=["sucesso", *_FORMAS])
def test_tb5_send_email_so_loga_o_endereco_mascarado(monkeypatch, caplog, forma):
    email = f"tb5{uuid.uuid4().hex[:10]}@Exemplo.test"
    local = email.split("@")[0]

    class _Resend:
        class Emails:
            @staticmethod
            def send(params):
                if forma == "sdk_validation_error":
                    raise_for_code_and_type(code=422, error_type="validation_error",
                                            message=_texto_do_provedor(forma, email))
                if forma:
                    raise RuntimeError(_texto_do_provedor(forma, email))

    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    monkeypatch.setattr(email_service, "send_email", _SEND_EMAIL_REAL)   # o conftest o troca
    monkeypatch.setattr(email_service, "_get_resend", lambda: _Resend)
    caplog.set_level(logging.INFO)
    marca = _marca()
    assert email_service.send_email(email, "Assunto TB5", "<p>x</p>") is (forma is None)

    evento = "email_failed" if forma else "email_sent"
    (linha,) = [x for x in _linhas(marca, "source = 'email_service' and details->>'subject' = 'Assunto TB5'")
                if x["event_type"] == evento]
    assert linha["user_id"] is None and linha["details"]["to"] == mascarar_email(email), linha
    assert mascarar_email(email) in linha["message"], "o operador ainda vê para quem foi (mascarado)"
    if forma:
        esperado = _ERRO_ESPERADO if forma == "sdk_validation_error" else "RuntimeError"
        assert linha["details"]["error"] == esperado, linha
    vistos = [str(x) for x in _linhas(marca)] + [r.getMessage() for r in caplog.records]
    assert not [v for v in vistos if local in v.lower()], vistos
