"""Funil v3, PR 3 — o job que entrega o e-book (`core/services/ebook_entrega.py`).

E. `entregar_pendentes()` com banco real, Stripe falso (`list_line_items`) e o
   `send_email` espiado; E12 é a conversa inteira, a partir do webhook.
F. `conta_sem_credencial` (a prova do e-mail: senha, Google ou Apple).
G. o `_ebook_worker` do lifespan chama o job (sonda de processo real).
H. `send_ebook_email`.

Controles (rodados, não previstos — ver o relato do PR):
  · o job ignorando `conta_sem_credencial`        → E1 vermelho;
  · sem `reivindicada_ate < now()` no `reivindicar` → E8 vermelho;
  · `fechar` antes de olhar o retorno do envio    → E6 vermelho;
  · claim fixo de 10 min (sem o backoff)          → E7b vermelho;
  · `conta_sem_credencial` ignorando identidades  → F "google"/"apple" vermelhos;
  · sem o `create_task(_ebook_worker…)`           → G vermelho.
  Positivo: E2 (quem tem senha recebe, uma vez só).
"""
from __future__ import annotations

import sys
import uuid
from types import SimpleNamespace

import pytest

from _billing_grants_helpers import garantir_system_event_logs
from _lifespan_probe import sondar
from core.crypto import encrypt_pii_optional, hash_pii_optional
from core.services import email_service as es
from core.services.ebook_entrega import entregar_pendentes
from db import ensure_user
from db.connection import get_conn
from db.ebook_entregas import abertas, registrar, reivindicar
from db.google_auth import conta_sem_credencial
from tests._helpers_pii import insert_auth_account_pii

_PRECO = "price_ebook_foto"


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


def _sql(q, a=()):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(q, a)
        return cur.fetchall() if cur.description else None


def _conta(senha=None, identidade=None) -> tuple[int, str]:
    uid = int(uuid.uuid4().int % 10_000_000_000)
    ensure_user(uid)
    email = f"eb-{uid}@t.com"
    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(cur, uid, email, password_hash=senha)
        if identidade:
            cur.execute("insert into auth_identities (user_id, provider, provider_sub)"
                        " values (%s, %s, %s)", (uid, identidade, uuid.uuid4().hex))
    return uid, email


def _url(uid):
    return f"https://drive.test/uc?id={uid}&export=download"


@pytest.fixture
def mundo(monkeypatch):
    """Stripe falso com `sessoes[sid] = [price ids]`, envios em `enviados`."""
    import db_support
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_job")
    m = SimpleNamespace(sessoes={}, chamadas=[], enviados=[], envio_ok=True, explode=False)

    def _list_line_items(sid, api_key=None):
        m.chamadas.append((sid, api_key))
        if m.explode:
            raise RuntimeError("stripe fora")
        return {"data": [{"price": {"id": p}} for p in m.sessoes[sid]]}

    def _send_email(to, subject, html_body, text_body, **kw):
        m.enviados.append((to, subject, html_body, text_body))
        return m.envio_ok

    monkeypatch.setitem(sys.modules, "stripe", SimpleNamespace(
        checkout=SimpleNamespace(Session=SimpleNamespace(list_line_items=_list_line_items))))
    monkeypatch.setattr(es, "send_email", _send_email)
    m.pendencia = lambda uid, sid, url="_": (m.sessoes.setdefault(sid, ["price_plano", _PRECO]),
                                             registrar(uid, sid, _PRECO, _url(uid) if url == "_" else url))
    m.linha = lambda uid, sid: _sql("select resultado, fechada_em, tentativas, reivindicada_ate > now() as presa,"
                                    " extract(epoch from reivindicada_ate - now()) / 60 as janela"
                                    " from ebook_entregas where user_id = %s and session_id = %s",
                                    (uid, sid))[0]
    m.para = lambda email: [e for e in m.enviados if e[0] == email and "e-book" in e[1]]
    m.invalida = db_support.invalidate_auth_user_cache
    return m


def _senha(uid):
    _sql("update auth_accounts set password_hash = 'hash' where user_id = %s", (uid,))


# ── E. o job ─────────────────────────────────────────────────────────────────

def test_e1_e2_sem_credencial_espera_com_senha_envia_uma_vez(mundo):
    uid, email = _conta()
    mundo.pendencia(uid, "cs_e1")
    entregar_pendentes()
    assert mundo.para(email) == [] and mundo.chamadas == []
    assert (mundo.linha(uid, "cs_e1")["fechada_em"], mundo.linha(uid, "cs_e1")["tentativas"]) == (None, 0)

    _senha(uid)
    assert entregar_pendentes() >= 1
    ((_, assunto, html, texto),) = mundo.para(email)
    assert assunto == "📘 Seu e-book do PigBank chegou"
    assert _url(uid) in texto and _url(uid).replace("&", "&amp;") in html
    assert mundo.linha(uid, "cs_e1")["resultado"] == "enviado"
    entregar_pendentes()
    assert len(mundo.para(email)) == 1


def test_e3_email_trocado_recebe_no_novo(mundo):
    uid, antigo = _conta()
    mundo.pendencia(uid, "cs_e3")
    novo = f"novo-{uid}@t.com"
    _sql("update auth_accounts set email = %s, email_hash = %s, email_enc = %s where user_id = %s",
         (novo, hash_pii_optional(novo, kind="email"), encrypt_pii_optional(novo), uid))
    mundo.invalida(uid)
    _senha(uid)
    entregar_pendentes()
    assert (len(mundo.para(novo)), mundo.para(antigo)) == (1, [])


@pytest.mark.parametrize("provider", ["google", "apple"])
def test_e4_so_identidade_social_envia(mundo, provider):
    uid, email = _conta(identidade=provider)
    mundo.pendencia(uid, "cs_e4")
    entregar_pendentes()
    assert len(mundo.para(email)) == 1


def test_e5_sessao_sem_o_ebook_fecha_nao_comprou(mundo):
    uid, email = _conta(senha="hash")
    mundo.sessoes["cs_e5"] = ["price_plano"]
    mundo.pendencia(uid, "cs_e5")
    entregar_pendentes()
    assert mundo.para(email) == [] and mundo.linha(uid, "cs_e5")["resultado"] == "nao_comprou"


def test_e6_envio_falho_deixa_aberta_e_o_claim_vencido_reenvia(mundo):
    uid, email = _conta(senha="hash")
    mundo.pendencia(uid, "cs_e6")
    mundo.envio_ok = False
    entregar_pendentes()
    linha = mundo.linha(uid, "cs_e6")
    assert (linha["fechada_em"], linha["presa"]) == (None, True)
    mundo.envio_ok = True
    entregar_pendentes()
    assert len(mundo.para(email)) == 1                 # o claim segura até vencer
    _sql("update ebook_entregas set reivindicada_ate = now() - interval '1 minute'"
         " where user_id = %s", (uid,))
    entregar_pendentes()
    assert len(mundo.para(email)) == 2 and mundo.linha(uid, "cs_e6")["resultado"] == "enviado"


def test_e7_stripe_fora_nao_envia_nem_fecha(mundo):
    uid, email = _conta(senha="hash")
    mundo.pendencia(uid, "cs_e7")
    mundo.explode = True
    entregar_pendentes()
    assert mundo.para(email) == [] and mundo.linha(uid, "cs_e7")["fechada_em"] is None


def test_e7b_falha_permanente_espaca_o_claim_ate_um_dia_e_nao_fecha(mundo):
    """Sem o backoff, a linha morta chamaria o Stripe e logaria a cada 5 min."""
    uid, _ = _conta(senha="hash")
    mundo.pendencia(uid, "cs_e7b")
    mundo.explode = True
    for n, esperado in enumerate([10, 20, 40, 80, 160, 320, 640, 1280, 1440, 1440], 1):
        entregar_pendentes()
        entregar_pendentes()                           # dentro da janela: nada
        linha = mundo.linha(uid, "cs_e7b")
        assert (linha["fechada_em"], linha["tentativas"]) == (None, n)
        assert esperado - 1 < float(linha["janela"]) <= esperado, (n, linha["janela"])
        _sql("update ebook_entregas set reivindicada_ate = now() - interval '1 minute'"
             " where user_id = %s", (uid,))            # o relógio passa a janela
    assert len([c for c in mundo.chamadas if c[0] == "cs_e7b"]) == 10


def test_e8_claim_e_atomico_e_expira(mundo):
    uid, _ = _conta()
    mundo.pendencia(uid, "cs_e8")
    assert reivindicar(uid, "cs_e8") == {"ebook_price": _PRECO, "ebook_url": _url(uid)}
    assert reivindicar(uid, "cs_e8") is None
    _sql("update ebook_entregas set reivindicada_ate = now() - interval '1 minute'"
         " where user_id = %s", (uid,))
    assert reivindicar(uid, "cs_e8") is not None


def test_e9_sem_url_fica_fora_da_varredura(mundo):
    uid, email = _conta(senha="hash")
    mundo.pendencia(uid, "cs_e9", url=None)
    assert (uid, "cs_e9") not in abertas()
    entregar_pendentes()
    assert mundo.para(email) == []


def test_e10_isolamento_entre_usuarios(mundo):
    a, email_a = _conta()
    b, email_b = _conta(senha="hash")
    mundo.pendencia(a, "cs_a")
    mundo.pendencia(b, "cs_b")
    entregar_pendentes()
    assert mundo.para(email_a) == []
    ((_, _, _, texto),) = mundo.para(email_b)
    assert _url(b) in texto and _url(a) not in texto
    _sql("update ebook_entregas set fechada_em = null where user_id = %s", (b,))
    assert reivindicar(a, "cs_b") is None


def test_e11_sem_chave_do_stripe_nao_toca_em_nada(mundo, monkeypatch):
    uid, email = _conta(senha="hash")
    mundo.pendencia(uid, "cs_e11")
    monkeypatch.delenv("STRIPE_SECRET_KEY")
    assert entregar_pendentes() == 0
    linha = mundo.linha(uid, "cs_e11")
    assert (mundo.chamadas, mundo.para(email), linha["fechada_em"], linha["presa"]) == ([], [], None, None)


def test_e12_a_conversa_webhook_senha_job_reentrega(mundo, user_id, monkeypatch):
    from test_billing_webhook_lifecycle import _cleanup_trial, _fake_sub, _post, _setup
    from test_ebook_webhook import _checkout, _espioes
    uid, client, fake = _setup(monkeypatch, f"eb-e12-{user_id}")
    lista = mundo.sessoes.setdefault("cs_eb_1", ["price_plano", _PRECO])
    fake.checkout = SimpleNamespace(Session=SimpleNamespace(
        list_line_items=lambda sid, api_key=None: {"data": [{"price": {"id": p}} for p in lista]}))
    _espioes(monkeypatch)
    _sql("update auth_accounts set password_hash = null where user_id = %s", (uid,))
    email = f"wh-eb-e12-{user_id}@t.com"
    try:
        evento = _checkout(uid, url=_url(uid))
        assert _post(client, fake, evento, subs={"sub_eb": _fake_sub("trialing")}).status_code == 200
        entregar_pendentes()
        assert mundo.para(email) == []
        _senha(uid)
        entregar_pendentes()
        assert len(mundo.para(email)) == 1
        assert _post(client, fake, evento).status_code == 200
        entregar_pendentes()
        assert len(mundo.para(email)) == 1
        assert _sql("select count(*) as n from ebook_entregas where user_id = %s", (uid,))[0]["n"] == 1
    finally:
        _cleanup_trial(uid)


# ── F. conta_sem_credencial ──────────────────────────────────────────────────

@pytest.mark.parametrize("senha,identidade,esperado", [
    (None, None, True), ("hash", None, False), ("", None, True),
    (None, "google", False), (None, "apple", False),
], ids=["nada", "senha", "senha-vazia", "google", "apple"])
def test_f_conta_sem_credencial(senha, identidade, esperado):
    uid, _ = _conta(senha=senha, identidade=identidade)
    assert conta_sem_credencial(uid) is esperado


def test_f_sem_auth_accounts_nao_conta_como_sem_credencial(user_id):
    # False: a função é também o gate do PR 4 e a guarda do bot, que roda para
    # o só-WhatsApp (sem auth_accounts). O job segue sem enviar nesse estado
    # porque `get_auth_user` não acha e-mail; e o estado não nasce, porque a
    # pendência exige o checkout da /assinar, que exige conta.
    assert conta_sem_credencial(user_id) is False


# ── G. o lifespan chama o job ────────────────────────────────────────────────

def test_g_processo_do_app_liga_o_job_do_ebook():
    resultado, diagnostico = sondar({"ebook": "core.services.ebook_entrega:entregar_pendentes"})
    assert resultado == {"ebook": True, "do_disco": []}, diagnostico


# ── H. o e-mail ──────────────────────────────────────────────────────────────

def test_h_send_ebook_email(monkeypatch):
    vistos = []
    monkeypatch.setattr(es, "send_email", lambda **kw: vistos.append(kw) or "ret")
    url = "https://drive.test/uc?id=1&export=download"
    assert es.send_ebook_email("a@b.com", url) == "ret"
    assert es.send_ebook_email("a@b.com", url, "https://painel.test/") == "ret"
    html, texto = vistos[1]["html_body"], vistos[1]["text_body"]
    assert 'href="https://drive.test/uc?id=1&amp;export=download"' in html
    assert url in texto and "https://painel.test/app" in texto
    assert vistos[0]["to"] == "a@b.com"
