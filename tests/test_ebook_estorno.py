"""Estorno segura a entrega (PR 3 do plano de extras) — `_compra_estornada`.

R. o job confere a cobrança da compra antes de enviar (`mundo` de
   `tests/test_ebook_entrega.py`): qualquer estorno (D2) ou contestação (D3)
   fecha `estornado` todas as linhas ainda abertas da compra, sem e-mail.
M3. a migração da check de `resultado` (ganha 'estornado').

Controles (rodados — ver o relato do PR):
  · `_compra_estornada` sempre False          → R1/R2/R5 vermelhos;
  · ignorando `disputed`                      → R3 vermelho;
  · engolindo a exceção do Stripe e enviando  → R7 (as 3 chamadas) vermelho;
  · sem `limit=100` no `InvoicePayment.list`  → R10 vermelho;
  · forma inesperada lida pelo `_ler`         → R11 vermelho;
  · sem o filtro `status == "paid"`           → R12 vermelho;
  · `amount_refunded > 1`                     → R13 vermelho;
  · sem a migração da check                   → M3 vermelho.
  Positivo: E2, Z1 e R6 (compra sem estorno continua entregando).
"""
from __future__ import annotations

import pytest
from stripe import StripeObject

from _billing_grants_helpers import garantir_system_event_logs
from core.services import email_service as es
from core.services.ebook_entrega import entregar_pendentes
from db.ebook_entregas import registrar
from db.schema import init_db
from test_ebook_entrega import _conta, _senha, _sql, _url, mundo  # noqa: F401
from test_ebook_n_produtos import _P, _checkout_n, _itens, _linhas

_ESTORNO = {"total": {"refunded": 4480}, "parcial": {"refunded": 990}, "contestacao": {"disputed": True}}


@pytest.fixture(autouse=True)
def _event_logs():
    garantir_system_event_logs()


def _logs(uid):
    return [r["details"] for r in _sql(
        "select details from system_event_logs where event_type = 'ebook_entrega_estornada'"
        " and user_id = %s", (uid,))]


def _vence_claim(uid):
    _sql("update ebook_entregas set reivindicada_ate = now() - interval '1 minute'"
         " where user_id = %s and fechada_em is null", (uid,))


# ── R. o job segura a entrega ────────────────────────────────────────────────

@pytest.mark.parametrize("caso", ["total", "parcial", "contestacao"],
                         ids=["r1-total", "r2-parcial", "r3-contestacao"])
def test_r1_r2_r3_estorno_ou_contestacao_fecha_todas_as_linhas_sem_email(mundo, caso):
    uid, email = _conta(senha="hash")
    itens = _itens(uid, 3)
    mundo.sessoes["cs_r1"] = ["price_plano", *_P[:3]]
    mundo.cobranca["cs_r1"] = _ESTORNO[caso]
    registrar(uid, "cs_r1", itens)
    assert entregar_pendentes() == 0
    assert mundo.para(email) == []
    assert {p: r["resultado"] for p, r in _linhas(uid).items()} == {p: "estornado" for p in _P[:3]}
    logs = _logs(uid)
    assert sorted(d["ebook_price"] for d in logs) == _P[:3]
    assert all(d == {"session_id": "cs_r1", "ebook_price": d["ebook_price"]} for d in logs)
    assert not any("http" in str(d) for d in logs)

    registrar(uid, "cs_r1", itens)        # reentrega do webhook
    mundo.cobranca.pop("cs_r1")           # nem se o Stripe "esquecer" o estorno
    entregar_pendentes()
    assert mundo.para(email) == []
    assert {r["resultado"] for r in _linhas(uid).values()} == {"estornado"}


def test_r4_estorno_depois_da_entrega_nao_muda_o_enviado(mundo):
    uid, email = _conta(senha="hash")
    mundo.pendencia(uid, "cs_r4")
    entregar_pendentes()
    assert len(mundo.para(email)) == 1
    mundo.cobranca["cs_r4"] = _ESTORNO["total"]
    _vence_claim(uid)
    entregar_pendentes()
    assert len(mundo.para(email)) == 1
    assert mundo.linha(uid, "cs_r4")["resultado"] == "enviado" and _logs(uid) == []


def test_r5_a_conversa_webhook_estorno_senha_job(mundo, user_id, monkeypatch):
    from test_billing_webhook_lifecycle import _cleanup_trial, _fake_sub, _post, _setup
    from test_ebook_webhook import _espioes
    uid, client, fake = _setup(monkeypatch, f"eb-r5-{user_id}")
    mundo.sessoes["cs_eb_1"] = ["price_plano", *_P[:2]]
    mundo.no(fake)
    _espioes(monkeypatch)
    _sql("update auth_accounts set password_hash = null where user_id = %s", (uid,))
    email = f"wh-eb-r5-{user_id}@t.com"
    try:
        evento = _checkout_n(uid, _itens(uid, 2))
        assert _post(client, fake, evento, subs={"sub_eb": _fake_sub("trialing")}).status_code == 200
        entregar_pendentes()
        assert mundo.para(email) == [] and mundo.consultas == []
        mundo.cobranca["cs_eb_1"] = _ESTORNO["parcial"]     # estorno antes da senha
        _senha(uid)
        entregar_pendentes()
        assert mundo.para(email) == []
        assert {p: r["resultado"] for p, r in _linhas(uid).items()} == {p: "estornado" for p in _P[:2]}
    finally:
        _cleanup_trial(uid)


@pytest.mark.parametrize("cobranca", [{"pagamentos": []}, {"invoice": None}],
                         ids=["cupom-100", "sem-fatura"])
def test_r6_sem_pagamento_a_conferir_entrega(mundo, cobranca):
    uid, email = _conta(senha="hash")
    mundo.pendencia(uid, "cs_r6")
    mundo.cobranca["cs_r6"] = cobranca
    entregar_pendentes()
    assert len(mundo.para(email)) == 1 and mundo.linha(uid, "cs_r6")["resultado"] == "enviado"


def _segura_sem_fechar(mundo, uid, email, sid):
    """Nada enviado nem fechado; o claim fica preso (backoff) e a falha logada."""
    linha = mundo.linha(uid, sid)
    assert mundo.para(email) == []
    assert (linha["fechada_em"], linha["presa"], linha["tentativas"]) == (None, True, 1)
    assert _sql("select count(*) as n from system_event_logs where event_type = 'ebook_entrega_falhou'"
                " and user_id = %s", (uid,))[0]["n"] == 1


@pytest.mark.parametrize("etapa", ["retrieve", "list", "pi"])
def test_r7_stripe_falha_na_consulta_nao_envia_nem_fecha_e_depois_confere_de_novo(mundo, etapa):
    uid, email = _conta(senha="hash")
    mundo.pendencia(uid, "cs_r7")
    mundo.explode_cobranca = etapa
    entregar_pendentes()
    _segura_sem_fechar(mundo, uid, email, "cs_r7")
    mundo.explode_cobranca = ""
    entregar_pendentes()                  # dentro do backoff: nem consulta
    assert mundo.consultas == ["cs_r7"]
    _vence_claim(uid)
    entregar_pendentes()
    assert mundo.consultas == ["cs_r7", "cs_r7"]
    assert len(mundo.para(email)) == 1 and mundo.linha(uid, "cs_r7")["resultado"] == "enviado"


def test_r8_uma_entregue_outra_aberta_depois_estorno(mundo, monkeypatch):
    uid, email = _conta(senha="hash")
    mundo.sessoes["cs_r8"] = ["price_plano", *_P[:2]]
    registrar(uid, "cs_r8", _itens(uid, 2))
    original = es.send_email
    monkeypatch.setattr(es, "send_email", lambda to, subject, *a, **kw:
                        original(to, subject, *a, **kw) and _P[1] not in subject)
    entregar_pendentes()
    assert (_linhas(uid)[_P[0]]["resultado"], _linhas(uid)[_P[1]]["fechada_em"]) == ("enviado", None)
    mundo.cobranca["cs_r8"] = _ESTORNO["parcial"]
    _vence_claim(uid)
    antes = len(mundo.para(email))
    entregar_pendentes()
    assert len(mundo.para(email)) == antes
    assert {p: r["resultado"] for p, r in _linhas(uid).items()} == {_P[0]: "enviado", _P[1]: "estornado"}


def _pag(status, pi, tipo="payment_intent"):
    return {"status": status, "payment": {"type": tipo, "payment_intent": pi}}


def test_r10_pagamento_pago_depois_de_dez_nao_pagos_e_visto(mundo):
    """O padrão do Stripe é 10 por página: o 11º (o pago, estornado) ficaria de fora."""
    uid, email = _conta(senha="hash")
    mundo.pendencia(uid, "cs_r10")
    antes = [_pag(s, "pi_outro") for s in ("canceled", "open") * 5]
    mundo.cobranca["cs_r10"] = {**_ESTORNO["parcial"], "pagamentos": [*antes, _pag("paid", "pi_cs_r10")]}
    entregar_pendentes()
    assert mundo.para(email) == [] and mundo.linha(uid, "cs_r10")["resultado"] == "estornado"


@pytest.mark.parametrize("cobranca", [
    {"latest_charge": "ch_nao_expandida"}, {"latest_charge": None}, {"latest_charge": {}},
    {"pagamentos": [_pag("paid", None, tipo="charge")]}, {"pagamentos": [_pag("paid", None)]},
], ids=["charge-string", "charge-none", "charge-vazia", "tipo-charge", "sem-payment-intent"])
def test_r11_forma_inesperada_num_pagamento_pago_falha_fechado(mundo, cobranca):
    uid, email = _conta(senha="hash")
    mundo.pendencia(uid, "cs_r11")
    mundo.cobranca["cs_r11"] = cobranca
    entregar_pendentes()
    _segura_sem_fechar(mundo, uid, email, "cs_r11")


@pytest.mark.parametrize("status", ["canceled", "open"])
def test_r12_pagamento_nao_pago_nem_e_consultado(mundo, status):
    """Valores reais: open, paid, canceled (estornado continua "paid", medido)."""
    uid, email = _conta(senha="hash")
    mundo.pendencia(uid, "cs_r12")
    mundo.cobranca["cancelado"] = {"latest_charge": None}     # levantaria se consultado
    mundo.cobranca["cs_r12"] = {"pagamentos": [_pag(status, "pi_cancelado"), _pag("paid", "pi_cs_r12")]}
    entregar_pendentes()
    assert len(mundo.para(email)) == 1 and mundo.linha(uid, "cs_r12")["resultado"] == "enviado"


def test_r13_um_centavo_estornado_segura(mundo):
    uid, email = _conta(senha="hash")
    mundo.pendencia(uid, "cs_r13")
    mundo.cobranca["cs_r13"] = {"refunded": 1}
    entregar_pendentes()
    assert mundo.para(email) == [] and mundo.linha(uid, "cs_r13")["resultado"] == "estornado"


@pytest.mark.parametrize("caso,esperado", [
    ("total", "estornado"), ("contestacao", "estornado"), (None, "enviado"), ("expandida", "estornado"),
])
def test_r9_stripeobject_real_e_fatura_expandida(mundo, monkeypatch, caso, esperado):
    """O SDK devolve StripeObject (sem `.get`) e `invoice` pode vir expandida."""
    uid, _ = _conta(senha="hash")
    mundo.pendencia(uid, "cs_r9")
    mundo.cobranca["cs_r9"] = ({**_ESTORNO["total"], "invoice": {"id": "in_cs_r9"}} if caso == "expandida"
                               else _ESTORNO.get(caso, {}))
    s = mundo.stripe
    for alvo, nome in ((s.checkout.Session, "retrieve"), (s.InvoicePayment, "list"), (s.PaymentIntent, "retrieve")):
        fn = getattr(alvo, nome)
        monkeypatch.setattr(alvo, nome, lambda *a, _fn=fn, **kw: StripeObject.construct_from(_fn(*a, **kw), "k"))
    entregar_pendentes()
    assert mundo.linha(uid, "cs_r9")["resultado"] == esperado


# ── M3. a migração da check ──────────────────────────────────────────────────

def _check_resultado():
    return _sql("select conname, pg_get_constraintdef(oid) as def from pg_constraint"
                " where conrelid = 'ebook_entregas'::regclass and contype = 'c'"
                " and pg_get_constraintdef(oid) like '%%resultado%%'")


@pytest.mark.parametrize("nome", ["ebook_entregas_resultado_check", "estranha_check"])
def test_m3_migracao_da_check_roda_duas_vezes_e_aceita_estornado(nome):
    uid, _ = _conta()
    (atual,) = _check_resultado()
    _sql(f"alter table ebook_entregas drop constraint {atual['conname']},"
         f" add constraint {nome} check (resultado in ('enviado', 'nao_comprou'))")
    try:
        registrar(uid, "cs_m3", [(_P[0], "u")])
        with pytest.raises(Exception, match="check"):     # a check velha recusa
            _sql("update ebook_entregas set resultado = 'estornado' where user_id = %s", (uid,))
        init_db()
        init_db()
        (check,) = _check_resultado()
        assert check["conname"] == "ebook_entregas_resultado_check" and "estornado" in check["def"]
        _sql("update ebook_entregas set resultado = 'estornado' where user_id = %s", (uid,))
        assert _linhas(uid)[_P[0]]["resultado"] == "estornado"
    finally:
        init_db()
