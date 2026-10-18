"""Exclusão de conta (S2): o DELETE cancela a assinatura de cartão na Stripe, sem reembolso.

Banco e rotas são os de verdade; a Stripe é um fake COM ESTADO (`Loja`): `cancel`
muda o status, `list` lê o estado, e cancelar assinatura presa a um schedule sem
soltá-lo antes é recusado, como a API. Matriz do plano S2 (a)-(k).

Checkout aberto (`checkout.Session`, com estado) e dois customers: l, l2, m, n.

Controle do grupo (CLAUDE.md §3), medido: tirar `cancelar_assinaturas` de
`excluir_conta_marcada` -> a, b, c, d, e, f, h2, k vermelhos; webhook sem `MARCA_EXCLUSAO` -> j;
sem o `release` -> d; sem expirar checkout -> l, m, n; `customers[:1]` -> n; sem relistar -> m; `return []` por `raise` na listagem de sessões -> i4; expire que falha por `raise` -> i5; positivos: l2, h, i (T3/T4), j (sem marca, o e-mail sai).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import stripe
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _billing_grants_helpers import garantir_system_event_logs
from _apoio_auth_app import SENHA_EXCLUSAO as SENHA, bearer, conta_exclusao, emails, sql  # noqa: F401
from core.services import email_service as es
from core.services import stripe_exclusao
from core.services.stripe_exclusao import MARCA_EXCLUSAO
from db import reauth_codes
from test_cartao_depois_do_pix import _deleted, _post, _sub, cena  # noqa: F401
from test_conta_exclusao_imediata import _completo, _del, _existe, _um, c, contas  # noqa: F401


class Loja:
    """Subscription e SubscriptionSchedule do SDK, com estado."""

    def __init__(self):
        self.subs, self.sessoes, self.eventos, self.consultas = {}, {}, [], []
        self.fora = self.cancel_falha = self.cancel_noop = self.expire_falha = False

    def sessao(self, sid, customer, status="open"):
        self.sessoes[sid] = {"id": sid, "customer": customer, "status": status}

    def nova(self, sub_id, customer, status="active", *, schedule=None, dono=None, fim=False):
        meta = {} if dono is None else {"finbot_user_id": str(dono)}
        self.subs[sub_id] = {"id": sub_id, "customer": customer, "status": status, "metadata": meta,
                             "schedule": schedule, "cancel_at_period_end": fim}

    def canceladas(self):
        return [e for e in self.eventos if e[0] == "cancel"]

    def instalar(self, monkeypatch):
        loja = self

        class Subscription:
            @staticmethod
            def list(customer=None, status=None, limit=None):
                if not stripe.api_key:   # como o SDK real: sem chave, nem sai do processo
                    raise RuntimeError("No API key provided")
                loja.consultas.append(customer)
                if loja.fora:
                    raise RuntimeError("stripe fora")
                return {"data": [s for s in loja.subs.values() if s["customer"] == customer
                                 and status in ("all", s["status"])][:limit]}

            @staticmethod
            def cancel(sub_id, **kw):
                if loja.cancel_falha:
                    raise RuntimeError("cancel recusado")
                loja.eventos.append(("cancel", sub_id, kw))
                if loja.subs[sub_id]["schedule"]:
                    raise stripe.error.InvalidRequestError("preso a um schedule", "subscription")
                if not loja.cancel_noop:
                    loja.subs[sub_id]["status"] = "canceled"

        class SubscriptionSchedule:
            @staticmethod
            def release(schedule_id):
                loja.eventos.append(("release", schedule_id))
                for s in loja.subs.values():
                    if s["schedule"] == schedule_id:
                        s["schedule"] = None

        class Session:
            @staticmethod
            def list(customer=None, status=None, limit=None):
                loja.consultas.append(customer)
                if loja.fora:
                    raise RuntimeError("stripe fora")
                return {"data": [s for s in loja.sessoes.values() if s["customer"] == customer
                                 and s["status"] == status][:limit]}

            @staticmethod
            def expire(sid):
                if loja.expire_falha:
                    raise RuntimeError("expire recusado")
                if loja.sessoes[sid]["status"] != "open":
                    raise stripe.error.InvalidRequestError("sessao nao esta aberta", "session")
                loja.eventos.append(("expire", sid))
                loja.sessoes[sid]["status"] = "expired"

        monkeypatch.setattr(stripe, "checkout", SimpleNamespace(Session=Session))
        monkeypatch.setattr(stripe, "Subscription", Subscription)
        monkeypatch.setattr(stripe, "SubscriptionSchedule", SubscriptionSchedule)
        monkeypatch.setattr(stripe, "api_key", "")
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
        monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_x")


@pytest.fixture
def loja(monkeypatch):
    lj = Loja()
    lj.instalar(monkeypatch)
    return lj


def _conta(contas, customer):
    conta = conta_exclusao()
    contas.append(conta.uid)
    if customer:
        sql("update auth_accounts set stripe_customer_id = %s where user_id = %s", customer, conta.uid)
    return conta


def _status(uid):
    return _um("select deletion_status from auth_accounts where user_id = %s", uid)["deletion_status"]


def _deleting(r):
    assert r.status_code == 200 and (r.json()["status"], r.json()["deleted"]) == ("deleting", False), r.text


def test_a_viva_com_renovacao_e_cancelada_sem_reembolso_e_a_conta_some(c, contas, loja):
    conta = _conta(contas, "cus_a")
    loja.nova("sub_a", "cus_a")
    r = _del(c, conta, _completo(conta))
    assert r.status_code == 200 and r.json()["status"] == "deleted", r.text
    assert loja.subs["sub_a"]["status"] == "canceled"
    assert loja.canceladas() == [("cancel", "sub_a", {"cancellation_details": {"comment": MARCA_EXCLUSAO}})]
    assert not _existe(conta.uid)


def test_b_cancel_at_period_end_tambem_e_cancelada(c, contas, loja):
    conta = _conta(contas, "cus_b")
    loja.nova("sub_b", "cus_b", fim=True)
    assert _del(c, conta, _completo(conta)).json()["status"] == "deleted"
    assert loja.subs["sub_b"]["status"] == "canceled"


def test_c_todas_as_vivas_incluindo_unpaid_e_as_mortas_nao_sao_tocadas(c, contas, loja):
    conta = _conta(contas, "cus_c")
    loja.nova("sub_1", "cus_c")
    loja.nova("sub_2", "cus_c", "unpaid")
    loja.nova("sub_3", "cus_c", "past_due")
    loja.nova("sub_velha", "cus_c", "canceled")
    assert _del(c, conta, _completo(conta)).json()["status"] == "deleted"
    assert sorted(e[1] for e in loja.canceladas()) == ["sub_1", "sub_2", "sub_3"]


def test_d_com_schedule_solta_antes_de_cancelar(c, contas, loja):
    conta = _conta(contas, "cus_d")
    loja.nova("sub_d", "cus_d", schedule="sub_sched_d")
    assert _del(c, conta, _completo(conta)).json()["status"] == "deleted"
    assert [e[0] for e in loja.eventos] == ["release", "cancel"]
    assert loja.eventos[0] == ("release", "sub_sched_d")


@pytest.mark.parametrize("modo", ["cancel_falha", "cancel_noop"])
def test_e_cancel_que_falha_ou_nao_pega_vira_deleting_e_o_job_refaz(c, contas, loja, modo):
    garantir_system_event_logs()
    conta = _conta(contas, "cus_e")
    loja.nova("sub_e", "cus_e")
    setattr(loja, modo, True)
    h = bearer(conta.uid)
    _deleting(_del(c, conta, _completo(conta), headers=h))
    assert _existe(conta.uid) and _status(conta.uid) == "scheduled"
    assert _um("select deletion_scheduled_for <= now() as v from auth_accounts where user_id = %s",
               conta.uid)["v"]
    assert c.get("/auth/me", headers=h).status_code == 401           # sessões revogadas
    assert _um("select count(*) as n from system_event_logs where event_type ="
               " 'account_deletion_stripe_cancel_failed' and user_id = %s", conta.uid)["n"] == 1

    setattr(loja, modo, False)   # Stripe de volta: o positivo
    (res,) = [r for r in db.process_due_account_deletions() if r["user_id"] == conta.uid]
    assert res["deleted"] and not _existe(conta.uid)
    assert loja.subs["sub_e"]["status"] == "canceled"


def test_f_stripe_ok_e_banco_falha_o_job_termina_sem_segundo_cancel(c, contas, loja, monkeypatch):
    import db.privacy as privacy
    conta = _conta(contas, "cus_f")
    loja.nova("sub_f", "cus_f")
    real = privacy.delete_user_data
    monkeypatch.setattr(privacy, "delete_user_data",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("deadlock simulado")))
    _deleting(_del(c, conta, _completo(conta)))
    assert len(loja.canceladas()) == 1 and _existe(conta.uid)
    monkeypatch.setattr(privacy, "delete_user_data", real)
    db.process_due_account_deletions()
    assert not _existe(conta.uid) and len(loja.canceladas()) == 1


def test_g_ja_em_andamento_nao_fala_com_a_stripe(c, contas, loja):
    conta = _conta(contas, "cus_g")
    loja.nova("sub_g", "cus_g")
    sql("update auth_accounts set deletion_status = 'processing', deletion_scheduled_for = now(),"
        " deletion_processing_started_at = now() where user_id = %s", conta.uid)
    r = _del(c, conta, {"password": "qualquer", "code": "000000"}, headers=bearer(conta.uid))
    assert r.json()["status"] == "deleting"
    assert loja.canceladas() == [] and loja.subs["sub_g"]["status"] == "active"


def test_h_isolamento_so_o_customer_da_propria_conta(c, contas, loja):
    a, b = _conta(contas, "cus_A"), _conta(contas, "cus_B")
    loja.nova("sub_A", "cus_A", dono=a.uid)
    loja.nova("sub_B", "cus_B", dono=b.uid)
    assert _del(c, b, _completo(b)).json()["status"] == "deleted"
    assert set(loja.consultas) == {"cus_B"}
    assert loja.subs["sub_A"]["status"] == "active" and _existe(a.uid)


def test_h2_assinatura_de_outro_usuario_no_customer_nao_e_cancelada_nem_a_conta_apagada(c, contas, loja):
    a, b = _conta(contas, "cus_A2"), _conta(contas, "cus_B2")
    loja.nova("sub_minha", "cus_B2", dono=b.uid)
    loja.nova("sub_alheia", "cus_B2", dono=a.uid)
    _deleting(_del(c, b, _completo(b)))
    assert loja.canceladas() == [] and _existe(b.uid) and _status(b.uid) == "scheduled"


def test_i_stripe_fora_do_ar_503_nada_consumido_e_sem_customer_nao_consulta(c, contas, loja):
    conta = _conta(contas, "cus_i")
    loja.nova("sub_i", "cus_i")
    loja.fora = True
    r = _del(c, conta, _completo(conta))
    assert r.status_code == 503 and r.json()["detail"]["code"] == "stripe_indisponivel"
    assert _existe(conta.uid) and loja.canceladas() == [] and _status(conta.uid) is None
    sem = _conta(contas, None)
    consultas = len(loja.consultas)
    assert _del(c, sem, _completo(sem)).json()["status"] == "deleted"
    assert len(loja.consultas) == consultas


def test_i2_sem_chave_e_com_customer_levanta(contas, loja, monkeypatch):
    conta = _conta(contas, "cus_i2")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    with pytest.raises(RuntimeError):
        stripe_exclusao.cancelar_assinaturas(conta.uid)


def test_i3_customer_apagado_no_stripe_e_sem_assinatura(contas, loja, monkeypatch):
    conta = _conta(contas, "cus_apagado")

    def _lista(**kw):
        raise stripe.error.InvalidRequestError("No such customer: 'cus_apagado'", "customer")
    monkeypatch.setattr(stripe.Subscription, "list", staticmethod(_lista))
    stripe_exclusao.cancelar_assinaturas(conta.uid)   # não levanta


def test_i4_customer_apagado_na_listagem_de_checkouts_e_nada_a_expirar(c, contas, loja, monkeypatch):
    conta = _conta(contas, "cus_i4")
    def _lista(**kw):
        raise stripe.error.InvalidRequestError("No such customer: 'cus_i4'", "customer")
    monkeypatch.setattr(stripe.checkout.Session, "list", staticmethod(_lista))
    assert _del(c, conta, _completo(conta)).json()["status"] == "deleted"


def test_i5_expire_que_falha_porque_a_sessao_virou_paga_a_relistagem_decide(c, contas, loja, monkeypatch):
    conta = _conta(contas, "cus_i5")
    loja.sessao("cs_i5", "cus_i5")
    def _expire(sid):   # como a API: a sessão acabou de virar complete e a assinatura nasceu
        loja.sessoes[sid]["status"] = "complete"
        loja.nova("sub_i5", "cus_i5")
        raise stripe.error.InvalidRequestError("Only open sessions can be expired", "session")
    monkeypatch.setattr(stripe.checkout.Session, "expire", staticmethod(_expire))
    assert _del(c, conta, _completo(conta)).json()["status"] == "deleted"
    assert loja.canceladas() == [("cancel", "sub_i5", {"cancellation_details": {"comment": MARCA_EXCLUSAO}})]


def test_j_webhook_deleted_da_exclusao_nao_manda_email_e_sem_a_marca_manda(cena):
    c = cena
    _sub(c.fake, "sub_j1", "canceled")
    assert _post(c.client, c.fake, _deleted(c.uid, "sub_j1", MARCA_EXCLUSAO)).status_code == 200
    assert (c.vistos["canceled"], c.vistos["notify_canceled"]) == ([], [])
    _sub(c.fake, "sub_j2", "canceled")
    assert _post(c.client, c.fake, _deleted(c.uid, "sub_j2")).status_code == 200
    assert len(c.vistos["canceled"]) == 1 and len(c.vistos["notify_canceled"]) == 1


def test_k_conta_scheduled_antiga_no_job_cancela_antes_de_apagar(contas, loja):
    conta = _conta(contas, "cus_k")
    loja.nova("sub_k", "cus_k")
    sql("update auth_accounts set deletion_status = 'scheduled', deletion_requested_at = now(),"
        " deletion_scheduled_for = now() - interval '1 hour' where user_id = %s", conta.uid)
    (res,) = [r for r in db.process_due_account_deletions() if r["user_id"] == conta.uid]
    assert res["deleted"] and not _existe(conta.uid)
    assert loja.subs["sub_k"]["status"] == "canceled"


def test_l_checkout_aberto_e_expirado_antes_de_cancelar_e_o_de_outro_customer_fica(c, contas, loja):
    conta = _conta(contas, "cus_l")
    loja.nova("sub_l", "cus_l")
    loja.sessao("cs_l", "cus_l")
    loja.sessao("cs_outro", "cus_outro")
    assert _del(c, conta, _completo(conta)).json()["status"] == "deleted"
    assert [e[:2] for e in loja.eventos] == [("expire", "cs_l"), ("cancel", "sub_l")]
    assert loja.sessoes["cs_l"]["status"] == "expired" and loja.sessoes["cs_outro"]["status"] == "open"


def test_l2_checkout_complete_ou_expired_nao_e_tocado(c, contas, loja):
    conta = _conta(contas, "cus_l2")
    loja.sessao("cs_paga", "cus_l2", "complete")
    loja.sessao("cs_velha", "cus_l2", "expired")
    assert _del(c, conta, _completo(conta)).json()["status"] == "deleted"
    assert loja.eventos == []
    assert (loja.sessoes["cs_paga"]["status"], loja.sessoes["cs_velha"]["status"]) == ("complete", "expired")


def test_m_expire_que_falha_vira_deleting_e_o_job_refaz(c, contas, loja):
    garantir_system_event_logs()
    conta = _conta(contas, "cus_m")
    loja.nova("sub_m", "cus_m")
    loja.sessao("cs_m", "cus_m")
    loja.expire_falha = True
    _deleting(_del(c, conta, _completo(conta)))
    assert _existe(conta.uid) and _status(conta.uid) == "scheduled"
    assert loja.sessoes["cs_m"]["status"] == "open"

    loja.expire_falha = False   # Stripe de volta: o positivo
    (res,) = [r for r in db.process_due_account_deletions() if r["user_id"] == conta.uid]
    assert res["deleted"] and not _existe(conta.uid)
    assert loja.sessoes["cs_m"]["status"] == "expired" and loja.subs["sub_m"]["status"] == "canceled"


def test_n_dois_customers_da_mesma_conta_tem_checkout_e_assinatura_tratados(c, contas, loja):
    conta = _conta(contas, "cus_n1")
    sql("insert into auth_accounts (user_id, email, password_hash, stripe_customer_id)"
        " values (%s, %s, 'x', 'cus_n2')", conta.uid, f"n2-{conta.uid}@example.com")
    for n in ("1", "2"):
        loja.nova(f"sub_n{n}", f"cus_n{n}")
        loja.sessao(f"cs_n{n}", f"cus_n{n}")
    assert _del(c, conta, _completo(conta)).json()["status"] == "deleted"
    assert sorted(e[1] for e in loja.canceladas()) == ["sub_n1", "sub_n2"]
    assert {s["status"] for s in loja.sessoes.values()} == {"expired"}


def test_email_de_conta_excluida_avisa_do_cartao(monkeypatch):
    enviados = []
    monkeypatch.setattr(es, "send_email", lambda **kw: enviados.append(kw) or True)
    es.send_account_deletion_completed_email("x@example.com")
    assert "assinatura no cartão, ela foi cancelada" in enviados[0]["text_body"]
    assert "assinatura no cartão, ela foi cancelada" in enviados[0]["html_body"]
