"""Auto-indicação (#1506): o indicado que compartilha telefone, chave Pix ou cartão com o afiliado não gera comissão.

Postgres real; cartão pelo `/billing/webhook` real (fixture `cobrar`), Pix por `record_commission_for_pix`
(o que o dreno chama). Só a Stripe do fingerprint é falsa (`afiliado_cartao.cliente_stripe` e
`cobrancas_da_fatura`). Bloqueio = zero linhas em `affiliate_commissions`, `first_charge_ref` gravado e o
evento `affiliate_commission_blocked`, sem valor de PII em `details`.

Controles negativos (à mão, §3 do `CLAUDE.md`; o relato do PR traz o que ficou vermelho):
  V1  `compartilha_identificador` devolvendo None   → os casos telefone_* e pix_* geram comissão.
  V2  `cartao_compartilhado` ignorado                → o caso cartao gera comissão.
  V3  `cartao_do_afiliado` sem a fatura da cobrança  → idem.
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import core.services.afiliado_cartao as cartao
from _billing_grants_helpers import garantir_system_event_logs
from core.crypto import encrypt_pii, hash_pii
from db.affiliates import (
    record_commission_for_pix,
    set_affiliate_pix_key,
)
from db.connection import get_conn
from test_afiliado_comissao_primeira import _afiliado, _comissoes, _marca
from test_afiliado_pagantes import _indicado, cobrar  # noqa: F401

AGORA = datetime(2026, 11, 9, tzinfo=timezone.utc)


def _novo_fone():
    """Único por teste: `phone_hash` é unique e o banco da suíte persiste entre execuções."""
    return f"55119{uuid.uuid4().int % 10**8:08d}"


@pytest.fixture(autouse=True)
def _logs():
    garantir_system_event_logs()


def _fone_auth(uid, fone):
    with get_conn() as conn:
        conn.execute("update auth_accounts set phone_hash = %s where user_id = %s",
                     (hash_pii(fone, kind="phone"), uid))
        conn.commit()


def _fone_whatsapp(uid, fone):
    with get_conn() as conn:
        conn.execute("insert into user_identities(provider, external_id, user_id, external_id_hash, external_id_enc)"
                     " values ('whatsapp', %s, %s, %s, %s)",
                     (fone, uid, hash_pii(fone, kind="external_id"), encrypt_pii(fone)))
        conn.commit()


def _segundo_customer(uid):
    """`auth_accounts` não é unique por `user_id`: o afiliado pode ter mais de uma linha com customer."""
    with get_conn() as conn:
        conn.execute("insert into auth_accounts (user_id, email, password_hash, plan, stripe_customer_id)"
                     " values (%s, %s, 'x', 'free', %s)",
                     (uid, f"seg-{uuid.uuid4().hex[:8]}@t.local", f"cus_{uuid.uuid4().hex[:10]}"))
        conn.commit()


def _pix(c, chave):
    set_affiliate_pix_key(c.aff["id"], hash_pii(chave), encrypt_pii(chave))


def _customer(uid):
    with get_conn() as conn:
        conn.execute("update auth_accounts set stripe_customer_id = %s where user_id = %s",
                     (f"cus_{uuid.uuid4().hex[:10]}", uid))
        conn.commit()


@pytest.fixture
def stripe_cartoes(monkeypatch):
    """`cartoes`: fingerprints salvos no customer do afiliado; `pagou`: o da cobrança da fatura; `falha`."""
    f = SimpleNamespace(cartoes=["fp_afiliado"], por_customer={}, pagina=100, pagou="fp_indicado", falha=None,
                        consultas=0, vistos=[])

    def listar(params):
        f.consultas += 1
        f.vistos.append(params["customer"])
        if f.falha:
            raise f.falha
        todos = [{"id": f"pm_{i}", "card": {"fingerprint": x}}
                 for i, x in enumerate(f.por_customer.get(params["customer"], f.cartoes))]
        ids = [x["id"] for x in todos]
        ini = ids.index(params["starting_after"]) + 1 if "starting_after" in params else 0
        fatia = todos[ini:ini + f.pagina]
        return {"data": fatia, "has_more": ini + f.pagina < len(todos)}

    monkeypatch.setattr(cartao, "cliente_stripe", lambda k: SimpleNamespace(
        v1=SimpleNamespace(payment_methods=SimpleNamespace(list=listar))))
    monkeypatch.setattr(cartao, "cobrancas_da_fatura", lambda inv, k: iter(
        [{"payment_method_details": {"card": {"fingerprint": f.pagou}}}]))
    return f


def _eventos(tipo, uid):
    with get_conn() as conn:
        return [r["details"] for r in conn.execute(
            "select details from system_event_logs where event_type = %s and user_id = %s", (tipo, uid)).fetchall()]


def _bloqueada(cobrar, ind, motivo):
    f1 = cobrar(ind.uid)
    assert (_comissoes(ind.uid), _marca(ind.uid)) == ([], f1)
    assert [d["motivo"] for d in _eventos("affiliate_commission_blocked", ind.uid)] == [motivo]
    cobrar(ind.uid)  # a 2ª fatura paga também não gera: o marcador fecha
    assert _comissoes(ind.uid) == []


def _gera(cobrar, ind):
    f1 = cobrar(ind.uid)
    assert [(x["stripe_invoice_id"], x["amount_cents"]) for x in _comissoes(ind.uid)] == [(f1, 6000)]
    assert _eventos("affiliate_commission_blocked", ind.uid) == []


def _cenario(bps=1000):
    c = _afiliado(bps)  # 10% de 60_000 = 6000
    return c, _indicado(c)


# ── Negativos pelo webhook (cartão) ──────────────────────────────────────────

def test_telefone_igual_afiliado_auth_e_indicado_whatsapp(cobrar, stripe_cartoes):
    c, ind = _cenario()
    fone = _novo_fone()
    _fone_auth(c.uid, fone)
    _fone_whatsapp(ind.uid, fone)
    _bloqueada(cobrar, ind, "telefone")


def test_telefone_igual_afiliado_whatsapp_e_indicado_auth(cobrar, stripe_cartoes):
    c, ind = _cenario()
    fone = _novo_fone()
    _fone_whatsapp(c.uid, fone)
    _fone_auth(ind.uid, fone)
    _bloqueada(cobrar, ind, "telefone")


def test_pix_do_afiliado_e_telefone_do_indicado_em_outro_formato(cobrar, stripe_cartoes):
    c, ind = _cenario()
    fone = _novo_fone()
    _fone_auth(ind.uid, fone)
    _pix(c, f"+{fone[:2]} ({fone[2:4]}) {fone[4:9]}-{fone[9:]}")
    _bloqueada(cobrar, ind, "pix")


def test_pix_do_afiliado_e_email_do_indicado_em_maiusculas(cobrar, stripe_cartoes):
    c, ind = _cenario()
    _pix(c, ind.email.upper())
    _bloqueada(cobrar, ind, "pix")


def test_pix_do_afiliado_e_pix_do_indicado_afiliado(cobrar, stripe_cartoes):
    c, ind = _cenario()
    from db.affiliates import create_affiliate
    create_affiliate(ind.uid, commission_bps=1000)
    _pix(c, "123.456.789-01")
    with get_conn() as conn:
        aff_ind = conn.execute("select id from affiliates where user_id = %s", (ind.uid,)).fetchone()["id"]
    set_affiliate_pix_key(aff_ind, hash_pii("12345678901"), encrypt_pii("12345678901"))
    _bloqueada(cobrar, ind, "pix")


def _afiliado_com_chave(ind, chave_ind):
    """Indicado que também é afiliado, com a chave Pix `chave_ind`."""
    from db.affiliates import create_affiliate
    aff = create_affiliate(ind.uid, commission_bps=1000)
    set_affiliate_pix_key(aff["id"], hash_pii(chave_ind), encrypt_pii(chave_ind))


def test_pix_aleatoria_igual_em_caixas_diferentes(cobrar, stripe_cartoes):
    c, ind = _cenario()
    _pix(c, "AbC-Chave-ALEATORIA-123")
    _afiliado_com_chave(ind, "abc-chave-aleatoria-123")
    _bloqueada(cobrar, ind, "pix")


def test_pix_telefone_formatado_igual_ao_telefone_puro_do_indicado_afiliado(cobrar, stripe_cartoes):
    c, ind = _cenario()
    _pix(c, "(11) 98765-4321")
    _afiliado_com_chave(ind, "+5511987654321")
    _bloqueada(cobrar, ind, "pix")


def test_pix_cpfs_diferentes_nao_bloqueiam(cobrar, stripe_cartoes):
    c, ind = _cenario()
    _pix(c, "123.456.789-01")
    _afiliado_com_chave(ind, "987.654.321-00")
    _gera(cobrar, ind)


@pytest.mark.parametrize("do_afiliado, do_indicado", [
    ("11987654321", "551187654321"),   # a chave tem o 9, o telefone do indicado não
    ("1187654321", "5511987654321"),   # o inverso
])
def test_pix_variante_do_nono_digito_bloqueia(cobrar, stripe_cartoes, do_afiliado, do_indicado):
    c, ind = _cenario()
    _fone_auth(ind.uid, do_indicado)
    _pix(c, do_afiliado)
    _bloqueada(cobrar, ind, "pix")


def test_pix_numero_de_fixo_de_outro_dono_nao_bloqueia(cobrar, stripe_cartoes):
    """"55119"+L com L em 0-5: `phone_lookup_candidates` tira o 9 e cai no FIXO "5511"+L, de outro dono (#909)."""
    c, ind = _cenario()
    _fone_auth(ind.uid, "551101234567")
    _pix(c, "5511901234567")
    _gera(cobrar, ind)


def test_cartao_do_afiliado_em_segunda_conta_de_customer(cobrar, stripe_cartoes):
    c, ind = _cenario()
    _customer(c.uid)
    _segundo_customer(c.uid)
    with get_conn() as conn:
        a, b = [r["stripe_customer_id"] for r in conn.execute(
            "select stripe_customer_id from auth_accounts where user_id = %s order by id", (c.uid,)).fetchall()]
    stripe_cartoes.por_customer = {a: ["fp_outro"], b: ["fp_afiliado"]}
    stripe_cartoes.pagou = "fp_afiliado"
    _bloqueada(cobrar, ind, "cartao")


def test_todos_os_customers_do_afiliado_sao_consultados(cobrar, stripe_cartoes):
    c, ind = _cenario()
    _customer(c.uid)
    _segundo_customer(c.uid)
    _gera(cobrar, ind)  # nenhum cartão bate; a ordem das linhas não pode decidir
    assert len(set(stripe_cartoes.vistos)) == 2


def test_cartao_do_afiliado_alem_da_primeira_pagina(cobrar, stripe_cartoes):
    c, ind = _cenario()
    _customer(c.uid)
    stripe_cartoes.pagina = 2
    stripe_cartoes.cartoes = ["fp_1", "fp_2", "fp_3", "fp_afiliado"]
    stripe_cartoes.pagou = "fp_afiliado"
    _bloqueada(cobrar, ind, "cartao")


def test_cartao_com_fingerprint_igual(cobrar, stripe_cartoes):
    c, ind = _cenario()
    _customer(c.uid)
    stripe_cartoes.pagou = "fp_afiliado"
    _bloqueada(cobrar, ind, "cartao")


# ── Positivos ────────────────────────────────────────────────────────────────

def test_identificadores_diferentes_geram(cobrar, stripe_cartoes):
    c, ind = _cenario()
    _customer(c.uid)
    _fone_auth(c.uid, _novo_fone())
    _fone_auth(ind.uid, _novo_fone())
    _pix(c, "outra@pessoa.com")
    _gera(cobrar, ind)
    assert stripe_cartoes.consultas == 1  # o cartão foi mesmo conferido


def test_afiliado_sem_chave_e_sem_cartao_gera(cobrar, stripe_cartoes):
    c, ind = _cenario()
    _gera(cobrar, ind)
    assert stripe_cartoes.consultas == 0  # sem customer: sem HTTP


# ── Falha da Stripe: fail-closed ─────────────────────────────────────────────

def test_falha_da_stripe_no_fingerprint_nao_gera_nem_marca(cobrar, stripe_cartoes):
    c, ind = _cenario()
    _customer(c.uid)
    stripe_cartoes.falha = RuntimeError("stripe fora")
    cobrar(ind.uid)
    assert (_comissoes(ind.uid), _marca(ind.uid)) == ([], None)
    assert len(_eventos("affiliate_commission_failed", ind.uid)) == 1


# ── Pix (o dreno chama `record_commission_for_pix`) ──────────────────────────

def _cobranca_pix(ind):
    return {"user_id": ind.uid, "external_reference": f"pix:{uuid.uuid4().int % 10**9}", "amount_cents": 19900,
            "paid_at": AGORA}


def test_pix_telefone_igual_bloqueia_e_marca(stripe_cartoes):
    c, ind = _cenario()
    fone = _novo_fone()
    _fone_auth(c.uid, fone)
    _fone_whatsapp(ind.uid, fone)
    pix = _cobranca_pix(ind)
    assert record_commission_for_pix(pix, 12) is None
    assert (_comissoes(ind.uid), _marca(ind.uid)) == ([], pix["external_reference"])
    assert [d["motivo"] for d in _eventos("affiliate_commission_blocked", ind.uid)] == ["telefone"]
    assert record_commission_for_pix(_cobranca_pix(ind), 12) is None


def test_pix_identificadores_diferentes_geram():
    c, ind = _cenario()
    _fone_auth(c.uid, _novo_fone())
    _fone_auth(ind.uid, _novo_fone())
    _pix(c, "chave-aleatoria-xyz")
    assert record_commission_for_pix(_cobranca_pix(ind), 12)["amount_cents"] > 0


# ── PII ──────────────────────────────────────────────────────────────────────

def test_decifragem_auditada_e_log_sem_pii(cobrar, stripe_cartoes, monkeypatch):
    monkeypatch.setenv("PII_AUDIT_DISABLED", "0")  # a suíte desliga a auditoria por padrão
    c, ind = _cenario()
    chave = ind.email.upper()
    _pix(c, chave)
    _bloqueada(cobrar, ind, "pix")
    with get_conn() as conn:
        n = conn.execute("select count(*) as n from pii_access_log where purpose = 'afiliado_vinculo'"
                         " and subject_user_id = %s and field = 'pix_key'", (c.uid,)).fetchone()["n"]
    assert n >= 1
    detalhes = str(_eventos("affiliate_commission_blocked", ind.uid))
    assert not any(x.lower() in detalhes.lower() for x in (chave, ind.email))
