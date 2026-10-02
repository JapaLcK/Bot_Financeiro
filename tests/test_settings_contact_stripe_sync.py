"""Funil v3, PR 4b — a troca de e-mail em /settings chega ao cliente do Stripe.

A conversa: PATCH /settings/{uid}/security/contact pelo endpoint (banco real), e
depois o job `sincronizar_pendentes()`. Stripe = módulo real com só o
`Customer.modify` trocado (a hierarquia de exceções é a de verdade).

Controles (rodados pelo Coder; o resto fica para o Tester):
  · sem o INSERT da rota                        → S1 vermelho;
  · fechar em qualquer exceção                  → S3 vermelho;
  · `on conflict do nothing`                    → S5a e S5b vermelhos;
  · sem `p.user_id = %s` no claim               → S6 vermelho.
  Positivo: S2 (o que não muda o e-mail continua 200 e não pendura).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import stripe

import db
from _billing_grants_helpers import garantir_system_event_logs
from _lifespan_probe import sondar
from conftest import _cleanup_user, promote_to_pro
from core.services.stripe_email_sync import sincronizar_pendentes
from db import ensure_user
from db.connection import get_conn
from db.stripe_email_pendente import reivindicar
from tests._helpers_pii import insert_auth_account_pii
from tests.test_settings_contact_pii_sync import _client_for


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


def _sql(q, a=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        return cur.fetchall() if cur.description else None


def _linhas(uid):
    return _sql("select versao, tentativas, coalesce(reivindicada_ate > now(), false) as presa"
                " from stripe_email_pendente where user_id = %s", (uid,))


def _vence(uid):
    _sql("update stripe_email_pendente set reivindicada_ate = now() - interval '1 minute'"
         " where user_id = %s", (uid,))


@pytest.fixture
def stripe_fake(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_job")
    m = SimpleNamespace(chamadas=[], erro=None, durante=None)

    def _modify(customer, **kw):
        m.chamadas.append((customer, kw.get("email"), kw.get("api_key")))
        if m.durante:
            durante, m.durante = m.durante, None
            durante()
        if m.erro:
            raise m.erro
        return {"id": customer}

    monkeypatch.setattr(stripe.Customer, "modify", _modify)
    m.de = lambda cus: [e for c, e, _ in m.chamadas if c == cus]
    return m


@pytest.fixture
def conta():
    criadas = []

    def _nova(customer="_"):
        uid = int(uuid.uuid4().int % 10_000_000_000)
        ensure_user(uid)
        criadas.append(uid)
        email = f"se-{uid}@t.local"
        cus = f"cus_{uid}" if customer == "_" else customer
        with get_conn() as conn, conn.cursor() as cur:
            insert_auth_account_pii(cur, uid, email)
            cur.execute("update auth_accounts set stripe_customer_id = %s where user_id = %s", (cus, uid))
        promote_to_pro(uid)
        client, headers = _client_for(uid, email)
        return SimpleNamespace(
            uid=uid, email=email, cus=cus,
            novo=lambda tag: f"se-{tag}-{uid}@t.local",
            patch=lambda **body: client.patch(f"/settings/{uid}/security/contact",
                                              json=body, headers=headers),
        )

    yield _nova
    for uid in criadas:
        _cleanup_user(uid)


def _email_atual(uid):
    return _sql("select email from auth_accounts where user_id = %s", (uid,))[0]["email"]


# ── S1/S2: a rota pendura só a troca de e-mail de quem tem cliente ───────────

def test_troca_de_email_chama_modify_com_o_novo(conta, stripe_fake):
    c = conta()
    b = c.novo("b")
    assert c.patch(email=b).status_code == 200
    assert len(_linhas(c.uid)) == 1
    sincronizar_pendentes()
    assert stripe_fake.chamadas.count((c.cus, b, "sk_test_job")) == 1
    assert stripe_fake.de(c.cus) == [b]
    assert _linhas(c.uid) == []


@pytest.mark.parametrize("caso", ["so_telefone", "so_nome", "mesmo_email_outra_caixa", "sem_cliente"])
def test_sem_mudanca_de_email_nao_pendura(conta, stripe_fake, caso):
    c = conta(customer=None) if caso == "sem_cliente" else conta()
    body = {
        "so_telefone": {"phone": "+55 65 99908-8732"},
        "so_nome": {"display_name": "Fulano"},
        "mesmo_email_outra_caixa": {"email": c.email.upper()},
        "sem_cliente": {"email": c.novo("b")},
    }[caso]
    assert c.patch(**body).status_code == 200
    assert _linhas(c.uid) == []
    sincronizar_pendentes()
    assert stripe_fake.de(c.cus) == []


# ── S3/S4: falha do Stripe ───────────────────────────────────────────────────

@pytest.mark.parametrize("erro", [
    stripe.APIConnectionError("rede fora"),
    stripe.RateLimitError("devagar"),
])
def test_stripe_fora_nao_desfaz_a_troca_e_a_retentativa_fecha(conta, stripe_fake, erro):
    c = conta()
    b = c.novo("b")
    stripe_fake.erro = erro
    assert c.patch(email=b).status_code == 200
    sincronizar_pendentes()
    assert _email_atual(c.uid) == b
    assert [(r["tentativas"], r["presa"]) for r in _linhas(c.uid)] == [(1, True)]

    sincronizar_pendentes()                 # dentro da janela: não chama de novo
    assert stripe_fake.de(c.cus) == [b]

    stripe_fake.erro = None
    _vence(c.uid)
    sincronizar_pendentes()
    assert stripe_fake.de(c.cus) == [b, b]
    assert _linhas(c.uid) == []


def test_cliente_apagado_fecha_sem_retentar(conta, stripe_fake):
    c = conta()
    b = c.novo("b")
    stripe_fake.erro = stripe.InvalidRequestError("No such customer", "customer", code="resource_missing")
    assert c.patch(email=b).status_code == 200
    sincronizar_pendentes()
    assert _linhas(c.uid) == []
    logs = _sql("select details::text as d from system_event_logs"
                " where event_type = 'stripe_email_sync_recusado' and user_id = %s", (c.uid,))
    assert len(logs) == 1
    assert "resource_missing" in logs[0]["d"] and b not in logs[0]["d"] and c.email not in logs[0]["d"]
    sincronizar_pendentes()
    assert stripe_fake.de(c.cus) == [b]


# ── S5: A → B → C termina em C ───────────────────────────────────────────────

def test_a_b_c_com_stripe_fora_termina_em_c(conta, stripe_fake):
    c = conta()
    b, cc = c.novo("b"), c.novo("c")
    stripe_fake.erro = stripe.APIConnectionError("rede fora")
    assert c.patch(email=b).status_code == 200
    sincronizar_pendentes()
    assert c.patch(email=cc).status_code == 200
    # Uma linha só, versão nova e o backoff recomeça — mas o claim continua.
    assert [(r["versao"], r["tentativas"], r["presa"]) for r in _linhas(c.uid)] == [(2, 0, True)]

    stripe_fake.erro = None
    _vence(c.uid)
    sincronizar_pendentes()
    assert stripe_fake.de(c.cus) == [b, cc]
    assert _linhas(c.uid) == []


def test_c_durante_o_envio_de_b_termina_em_c(conta, stripe_fake):
    c = conta()
    b, cc = c.novo("b"), c.novo("c")
    assert c.patch(email=b).status_code == 200
    no_meio = []

    def _troca_no_meio():
        assert c.patch(email=cc).status_code == 200
        # Outra rodada com B ainda em voo não pode mandar C (B chegaria depois).
        sincronizar_pendentes()
        no_meio.extend(stripe_fake.de(c.cus))

    stripe_fake.durante = _troca_no_meio
    sincronizar_pendentes()
    assert no_meio == [b]
    assert [r["presa"] for r in _linhas(c.uid)] == [False]   # o fechar(v1) soltou o claim

    sincronizar_pendentes()
    assert stripe_fake.de(c.cus) == [b, cc]
    assert _linhas(c.uid) == []


# ── S6/S7: isolamento e o 409 ────────────────────────────────────────────────

def test_isolamento_entre_usuarios(conta, stripe_fake):
    u1, u2, u3 = conta(), conta(), conta()
    e1, e2 = u1.novo("n"), u2.novo("n")
    assert u1.patch(email=e1).status_code == 200
    assert u2.patch(email=e2).status_code == 200
    assert u3.patch(phone="+55 65 99908-8733").status_code == 200
    assert _linhas(u3.uid) == []
    sincronizar_pendentes()
    assert (stripe_fake.de(u1.cus), stripe_fake.de(u2.cus), stripe_fake.de(u3.cus)) == ([e1], [e2], [])
    assert _linhas(u1.uid) == _linhas(u2.uid) == []


def test_email_de_outra_conta_da_409_sem_pendencia(conta, stripe_fake):
    c, outra = conta(), conta()
    assert c.patch(email=outra.email).status_code == 409
    assert _linhas(c.uid) == []
    sincronizar_pendentes()
    assert stripe_fake.de(c.cus) == []


# ── S8/S9: o claim e a chave ─────────────────────────────────────────────────

def test_claim_atomico_e_expira(conta, stripe_fake):
    c = conta()
    assert c.patch(email=c.novo("b")).status_code == 200
    assert reivindicar(c.uid)["stripe_customer_id"] == c.cus
    assert reivindicar(c.uid) is None
    _vence(c.uid)
    assert reivindicar(c.uid) is not None


def test_sem_chave_nao_toca_em_nada(conta, stripe_fake, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY")
    c = conta()
    assert c.patch(email=c.novo("b")).status_code == 200
    assert sincronizar_pendentes() == 0
    assert [r["tentativas"] for r in _linhas(c.uid)] == [0]
    assert stripe_fake.chamadas == []


# ── S10/S11: o processo liga o job e a migração cria a tabela ────────────────

def test_processo_do_app_liga_o_job():
    resultado, diagnostico = sondar({"stripe_email": "core.services.stripe_email_sync:sincronizar_pendentes"})
    assert resultado == {"stripe_email": True, "do_disco": []}, diagnostico


def test_migracao_cria_a_tabela_e_e_idempotente():
    _sql("drop table stripe_email_pendente")      # o banco de antes do PR 4b
    db.init_db()
    colunas = {r["column_name"] for r in _sql(
        "select column_name from information_schema.columns"
        " where table_schema = current_schema() and table_name = 'stripe_email_pendente'")}
    assert colunas == {"user_id", "versao", "tentativas", "reivindicada_ate", "criada_em"}
    db.init_db()


# ── S12: duas linhas de auth_accounts no mesmo user_id ───────────────────────
# Não há unique em `auth_accounts.user_id` (db/signup_quiz.py). O UPDATE do e-mail
# da rota grava o mesmo e-mail nas duas linhas e bate no unique de `email`: o 409
# é anterior ao PR 4b. Com as duas linhas com cliente, o INSERT sem DISTINCT
# inseria a mesma PK duas vezes (CardinalityViolation → 500) antes desse 409.

@pytest.mark.parametrize("cliente_na_segunda", [False, True])
def test_duas_linhas_do_mesmo_user_id(conta, stripe_fake, cliente_na_segunda):
    c = conta()
    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(cur, c.uid, c.novo("dupla"))
        if cliente_na_segunda:
            cur.execute("update auth_accounts set stripe_customer_id = %s"
                        " where user_id = %s and stripe_customer_id is null", (f"cus2_{c.uid}", c.uid))
    assert c.patch(email=c.novo("b")).status_code == 409
    assert _linhas(c.uid) == []
    sincronizar_pendentes()
    assert stripe_fake.chamadas == []
