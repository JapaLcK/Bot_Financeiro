"""Vários produtos por compra (PR 1 do plano de extras) — entrega de N produtos.

D. `da_metadata` (a foto da sessão → [(preço, url)]).
M. a migração da PK de `ebook_entregas` para (user_id, session_id, ebook_price).
X. o webhook grava uma linha por produto, num statement só.
Z. o job entrega por linha (`mundo` de `tests/test_ebook_entrega.py`).

Controles (rodados — ver o relato do PR):
  · sem `limit=100` no `list_line_items`           → Z6 vermelho;
  · `fechar` filtrando só por (user_id, session_id) → Z3 vermelho;
  · PK de 2 colunas no `create table` e sem a migração → X1 vermelho.
  Positivo: B1/B2/E1–E12 de hoje seguem verdes.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from stripe import StripeObject   # o real, antes de o `mundo` trocar `sys.modules["stripe"]`

from _billing_grants_helpers import garantir_system_event_logs
from core.services import email_service as es
from core.services.ebook_entrega import entregar_pendentes
from core.services.extras_assinar import da_metadata
from db.ebook_entregas import abertas, registrar
from db.schema import init_db
from test_ebook_entrega import _conta, _nome, _pagina, _senha, _sql, _url, mundo  # noqa: F401

_P = [f"price_x{n}" for n in range(1, 11)]


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


def _itens(uid, k):
    return [(p, f"{_url(uid)}&p={p}") for p in _P[:k]]


def _linhas(uid):
    return {r["ebook_price"]: r for r in _sql(
        "select ebook_price, ebook_url, resultado, fechada_em, tentativas,"
        " reivindicada_ate > now() as presa from ebook_entregas where user_id = %s", (uid,))}


def _pk():
    (r,) = _sql("select array_agg(a.attname::text order by k.ord) as cols"
                " from pg_constraint c, unnest(c.conkey) with ordinality k(attnum, ord)"
                " join pg_attribute a on a.attnum = k.attnum"
                " where c.conrelid = 'ebook_entregas'::regclass and c.contype = 'p'"
                " and a.attrelid = c.conrelid")
    return r["cols"]


# ── D. da_metadata ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("meta,esperado", [
    ({"ebook_price": "p1", "ebook_url": "u1", "origem": "assinar"}, [("p1", "u1")]),
    ({**{"ebook_price": "p1", "ebook_url": "u1"},
      **{f"ebook_{n}_price": f"p{n}" for n in range(2, 11)},
      **{f"ebook_{n}_url": f"u{n}" for n in range(2, 11)}},
     [(f"p{n}", f"u{n}") for n in range(1, 11)]),
    ({"ebook_price": "p1", "ebook_url": "u1", "ebook_3_price": "p3", "ebook_3_url": "u3"},
     [("p1", "u1"), ("p3", "u3")]),
    ({"ebook_2_price": "p2", "ebook_2_url": "u2"}, [("p2", "u2")]),
    ({"ebook_price": "p1", "ebook_2_price": "p2", "ebook_2_url": ""}, [("p1", None), ("p2", None)]),
    ({"ebook_url": "u1", "ebook_11_price": "p11"}, []),
    ({}, []), (None, []),
], ids=["slot1", "1a10", "buraco", "sem-slot1", "sem-url", "url-sem-preco-e-slot11", "vazio", "none"])
def test_d_da_metadata(meta, esperado):
    assert da_metadata(meta) == esperado
    if meta is not None:
        assert da_metadata(StripeObject.construct_from(meta, "k")) == esperado


# ── M. a migração da PK ──────────────────────────────────────────────────────

def test_m1_migracao_roda_duas_vezes_e_a_pk_termina_com_tres_colunas():
    # O SQL de rollback do docs/CLAUDE.md, para partir do estado de antes.
    _sql("delete from ebook_entregas e using ebook_entregas o where e.user_id = o.user_id"
         " and e.session_id = o.session_id and e.ebook_price > o.ebook_price")
    _sql("alter table ebook_entregas drop constraint ebook_entregas_pkey,"
         " add primary key (user_id, session_id)")
    try:
        assert _pk() == ["user_id", "session_id"]
        init_db()
        init_db()
        assert _pk() == ["user_id", "session_id", "ebook_price"]
    finally:
        init_db()


def test_m2_linha_da_pk_velha_e_entregue_depois_de_migrar(mundo):
    uid, email = _conta(senha="hash")
    _sql("drop table ebook_entregas")
    try:
        _sql("""create table ebook_entregas (
          user_id bigint not null references users(id) on delete cascade,
          session_id text not null, ebook_price text not null, ebook_url text,
          criada_em timestamptz not null default now(), reivindicada_ate timestamptz,
          tentativas int not null default 0, fechada_em timestamptz,
          resultado text check (resultado in ('enviado', 'nao_comprou')),
          constraint estranha_pk primary key (user_id, session_id))""")
        _sql("insert into ebook_entregas (user_id, session_id, ebook_price, ebook_url)"
             " values (%s, %s, %s, %s) on conflict (user_id, session_id) do nothing",
             (uid, "cs_m2", _P[0], _url(uid)))
        assert _pk() == ["user_id", "session_id"]
        init_db()
        assert _pk() == ["user_id", "session_id", "ebook_price"]
        mundo.sessoes["cs_m2"] = ["price_plano", _P[0]]
        entregar_pendentes()
        ((_, assunto, _, texto),) = mundo.para(email)
        assert assunto == f"📘 Chegou: {_nome(_P[0])}" and _url(uid) in texto
        assert _linhas(uid)[_P[0]]["resultado"] == "enviado"
    finally:
        init_db()


# ── X. o webhook grava N linhas ──────────────────────────────────────────────

def _checkout_n(uid, itens):
    from test_ebook_webhook import _checkout
    evento = _checkout(uid, url=itens[0][1])
    meta = evento["data"]["object"]["metadata"]
    meta["ebook_price"] = itens[0][0]
    for n, (p, u) in enumerate(itens[1:], 2):
        meta[f"ebook_{n}_price"] = p
        if u:
            meta[f"ebook_{n}_url"] = u
    return evento


@pytest.fixture
def webhook(user_id, monkeypatch):
    from test_billing_webhook_lifecycle import _cleanup_trial, _setup
    from test_ebook_webhook import _espioes
    uid, client, fake = _setup(monkeypatch, f"eb-n-{user_id}")
    _espioes(monkeypatch)
    yield uid, client, fake
    _cleanup_trial(uid)


def test_x1_n_linhas_e_reentrega_nao_duplica(webhook):
    from test_billing_webhook_lifecycle import _fake_sub, _post
    uid, client, fake = webhook
    itens = _itens(uid, 3)
    for _ in range(2):
        r = _post(client, fake, _checkout_n(uid, itens), subs={"sub_eb": _fake_sub("trialing")})
        assert r.status_code == 200, r.text
    assert {p: r["ebook_url"] for p, r in _linhas(uid).items()} == dict(itens)
    (n,) = _sql("select count(*) as n from ebook_entregas where user_id = %s", (uid,))
    assert n["n"] == 3


def test_x2_insert_atomico_falha_no_meio_nao_grava_nada():
    uid, _ = _conta()
    with pytest.raises(Exception):
        registrar(uid, "cs_x2", [(_P[0], "u"), (None, "u"), (_P[2], "u")])
    assert _linhas(uid) == {}


def test_x3_item_sem_url_grava_null_e_loga_por_item(webhook):
    from test_billing_webhook_lifecycle import _fake_sub, _post
    uid, client, fake = webhook
    itens = [(_P[0], _url(uid)), (_P[1], None), (_P[2], None)]
    assert _post(client, fake, _checkout_n(uid, itens), subs={"sub_eb": _fake_sub("trialing")}).status_code == 200
    assert {p: r["ebook_url"] for p, r in _linhas(uid).items()} == dict(itens)
    logs = _sql("select details from system_event_logs where event_type = 'ebook_sem_url'"
                " and user_id = %s", (uid,))
    assert sorted(d["details"]["ebook_price"] for d in logs) == [_P[1], _P[2]]


# ── Z. o job entrega por linha ───────────────────────────────────────────────

def test_z1_n_emails_cada_um_com_o_seu_nome(mundo):
    uid, email = _conta(senha="hash")
    itens = _itens(uid, 3)
    mundo.sessoes["cs_z1"] = ["price_plano", *_P[:3]]
    registrar(uid, "cs_z1", itens)
    assert entregar_pendentes() >= 3
    vistos = {assunto: (html, texto) for _, assunto, html, texto in mundo.para(email)}
    assert set(vistos) == {f"📘 Chegou: {_nome(p)}" for p in _P[:3]}
    for p, u in itens:
        html, texto = vistos[f"📘 Chegou: {_nome(p)}"]
        assert u in texto and f"<b>{_nome(p)}</b>" in html
    assert {r["resultado"] for r in _linhas(uid).values()} == {"enviado"}


def test_z2_parte_comprada_fecha_o_resto_nao_comprou(mundo):
    uid, email = _conta(senha="hash")
    mundo.sessoes["cs_z2"] = ["price_plano", _P[0], _P[2]]
    registrar(uid, "cs_z2", _itens(uid, 3))
    entregar_pendentes()
    assert len(mundo.para(email)) == 2
    assert {p: r["resultado"] for p, r in _linhas(uid).items()} == {
        _P[0]: "enviado", _P[1]: "nao_comprou", _P[2]: "enviado"}


def test_z3_falha_de_envio_de_um_item_nao_segura_os_outros(mundo, monkeypatch):
    uid, email = _conta(senha="hash")
    mundo.sessoes["cs_z3"] = ["price_plano", *_P[:3]]
    registrar(uid, "cs_z3", _itens(uid, 3))
    falha = {_nome(_P[1])}
    original = es.send_email

    def _send(to, subject, html_body, text_body, **kw):
        ok = original(to, subject, html_body, text_body, **kw)
        return ok and not any(n in subject for n in falha)
    monkeypatch.setattr(es, "send_email", _send)

    entregar_pendentes()
    linhas = _linhas(uid)
    assert (linhas[_P[0]]["resultado"], linhas[_P[2]]["resultado"]) == ("enviado", "enviado")
    assert (linhas[_P[1]]["fechada_em"], linhas[_P[1]]["presa"], linhas[_P[1]]["tentativas"]) == (None, True, 1)

    falha.clear()
    _sql("update ebook_entregas set reivindicada_ate = now() - interval '1 minute'"
         " where user_id = %s and fechada_em is null", (uid,))
    antes = len(mundo.para(email))
    entregar_pendentes()
    novos = mundo.para(email)[antes:]
    assert [a for _, a, _, _ in novos] == [f"📘 Chegou: {_nome(_P[1])}"]
    assert _linhas(uid)[_P[1]]["resultado"] == "enviado"


def test_z4_item_sem_url_fica_fora_e_os_outros_entregam(mundo):
    uid, email = _conta(senha="hash")
    mundo.sessoes["cs_z4"] = ["price_plano", *_P[:3]]
    registrar(uid, "cs_z4", [(_P[0], _url(uid)), (_P[1], None), (_P[2], _url(uid))])
    assert (uid, "cs_z4", _P[1]) not in abertas()
    entregar_pendentes()
    assert sorted(a for _, a, _, _ in mundo.para(email)) == sorted(
        f"📘 Chegou: {_nome(p)}" for p in (_P[0], _P[2]))
    assert _linhas(uid)[_P[1]]["fechada_em"] is None


def test_z5_a_conversa_webhook_com_tres_extras_senha_job_reentrega(mundo, user_id, monkeypatch):
    from test_billing_webhook_lifecycle import _cleanup_trial, _fake_sub, _post, _setup
    from test_ebook_webhook import _espioes
    uid, client, fake = _setup(monkeypatch, f"eb-z5-{user_id}")
    lista = ["price_plano", *_P[:3]]
    fake.checkout = SimpleNamespace(Session=SimpleNamespace(
        list_line_items=lambda sid, api_key=None, limit=10: _pagina(lista, limit)))
    _espioes(monkeypatch)
    _sql("update auth_accounts set password_hash = null where user_id = %s", (uid,))
    email = f"wh-eb-z5-{user_id}@t.com"
    try:
        evento = _checkout_n(uid, _itens(uid, 3))
        assert _post(client, fake, evento, subs={"sub_eb": _fake_sub("trialing")}).status_code == 200
        entregar_pendentes()
        assert mundo.para(email) == []
        _senha(uid)
        entregar_pendentes()
        assert len(mundo.para(email)) == 3
        assert _post(client, fake, evento).status_code == 200
        entregar_pendentes()
        assert len(mundo.para(email)) == 3
        assert len(_linhas(uid)) == 3
    finally:
        _cleanup_trial(uid)


def test_z6_plano_mais_dez_extras_o_decimo_primeiro_item_e_visto(mundo):
    uid, email = _conta(senha="hash")
    mundo.sessoes["cs_z6"] = ["price_plano", *_P]
    registrar(uid, "cs_z6", _itens(uid, 10))
    entregar_pendentes()
    assert len(mundo.para(email)) == 10
    assert {r["resultado"] for r in _linhas(uid).values()} == {"enviado"}


def test_z7_isolamento_mesma_sessao_e_preco_em_dois_usuarios(mundo):
    """Regra dura (CLAUDE.md §0): `reivindicar`/`fechar` filtram por `user_id`."""
    (a, email_a), (b, email_b) = _conta(senha="hash"), _conta(senha="hash")
    mundo.sessoes["cs_z7"] = ["price_plano", _P[0]]
    registrar(a, "cs_z7", [(_P[0], _url(a))])
    registrar(b, "cs_z7", [(_P[0], _url(b))])
    entregar_pendentes()
    ((_, _, _, texto_a),) = mundo.para(email_a)
    ((_, _, _, texto_b),) = mundo.para(email_b)
    assert _url(a) in texto_a and _url(b) not in texto_a
    assert _url(b) in texto_b and _url(a) not in texto_b
    assert (_linhas(a)[_P[0]]["resultado"], _linhas(b)[_P[0]]["resultado"]) == ("enviado", "enviado")


@pytest.mark.parametrize("linha", [
    {"price": {"id": _P[0]}, "description": None},
    {"price": {"id": _P[0]}},
    # StripeObject real (SDK v8+): sem `.get`, que levanta AttributeError.
    StripeObject.construct_from({"price": {"id": _P[0]}, "description": None}, "k"),
    StripeObject.construct_from({"price": {"id": _P[0]}}, "k"),
], ids=["description-none", "sem-description", "stripeobject-none", "stripeobject-sem"])
def test_z8_sem_nome_do_produto_vira_seu_ebook(mundo, monkeypatch, linha):
    uid, email = _conta(senha="hash")
    monkeypatch.setattr(sys.modules["stripe"].checkout.Session, "list_line_items",
                        lambda sid, api_key=None, limit=10: {"data": [linha]})
    registrar(uid, "cs_z8", [(_P[0], _url(uid))])
    entregar_pendentes()
    ((_, assunto, html, texto),) = mundo.para(email)
    assert assunto == "📘 Chegou: seu e-book"
    assert "<b>seu e-book</b>" in html and "liberada: seu e-book." in texto


# ── H. o nome no e-mail ──────────────────────────────────────────────────────

def test_h_nome_vazio_vira_seu_ebook_e_nome_do_stripe_e_escapado(monkeypatch):
    vistos = []
    monkeypatch.setattr(es, "send_email", lambda **kw: vistos.append(kw) or True)
    for vazio in ("", "   "):
        vistos.clear()
        es.send_ebook_email("a@b.com", "https://x.test/a.pdf", nome=vazio)
        assert vistos[0]["subject"] == "📘 Chegou: seu e-book", repr(vazio)
        assert "<b>seu e-book</b>" in vistos[0]["html_body"]
    vistos.clear()

    es.send_ebook_email("a@b.com", "https://x.test/a.pdf", nome='Livro <b>&"x')
    html = vistos[0]["html_body"]
    esc = "Livro &lt;b&gt;&amp;&quot;x"
    assert f"<title>Chegou: {esc}</title>" in html and f"<b>{esc}</b>" in html
    assert '<b>&"x' not in html
    assert vistos[0]["subject"] == '📘 Chegou: Livro <b>&"x'
