"""
tests/test_billing_grants_materializacao.py — §4.1.1 e §4.1.2 do
docs/plano_pix_anual_asaas.md: materialização obrigatória do grant e regra da
redução. Casos 9a, 9b, 9g e 9h do §16, mais o checkout sem `subscription` (B-1). A
regra da redução (9c–9e, B-2, B-3) mora em `test_billing_grants_reducao.py`.

O assunto aqui é o grant ENTRAR: materialização obrigatória e idempotência da
reentrega. O grant SAIR é o arquivo da redução.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _billing_grants_helpers import (
    conta as _conta, evt_checkout as _evt_checkout, evt_deleted as _evt_deleted,
    evt_paid as _evt_paid, garantir_system_event_logs, ler as _ler,
    sub_stripe as _sub,
)
from db.connection import get_conn
from db.plan_grants import list_grants, upsert_grant
from test_billing_webhook_lifecycle import _post, _setup


def _funil(uid: int, kind: str = "completed") -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from checkout_funnel_events"
                        " where user_id=%s and kind=%s", (uid, kind))
            return int(cur.fetchone()["n"])


# ── 9a / 9b: materialização obrigatória e reentrega (§4.1.1 A, §4.1.2) ────────

def test_9a_falha_ao_materializar_o_grant_devolve_5xx_e_nao_escreve_nada(user_id, monkeypatch):
    """BLOQUEADOR 1. Gravação crítica não engole exceção.

    Antes havia um `except`/`print` aqui e o efeito medido foi caro: o
    `update_user_plan` entrava, o `upsert_grant` estourava, o grant ficava
    parado no período anterior e a varredura seguinte rebaixava um PAGANTE.

    O grant é o PRIMEIRO efeito do ramo, então a falha dele significa que nada
    depois rodou — nem funil, nem e-mail, nem `plan_selected_at`.
    """
    uid, client, fake = _setup(monkeypatch, f"g9a-{user_id}")
    _conta(uid, "free", None, "inactive")

    import db.plan_grants as pg
    monkeypatch.setattr(pg, "upsert_grant",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("banco caiu")))

    r = _post(client, fake, _evt_paid(uid, "sub_9a", 1_800_000_000),
              subs={"sub_9a": _sub("active", "price_x", 30)})
    assert r.status_code >= 500, (
        f"falha de materializacao devolveu {r.status_code}; 2xx faria a Stripe "
        "considerar o evento entregue e nunca reentregar")

    row = _ler(uid)
    assert row["plan"] == "free", "auth_accounts foi escrito apesar da falha do grant"
    assert list_grants(uid) == []
    assert _funil(uid) == 0, "efeito posterior ao grant rodou mesmo com o grant falhando"


def test_9b_reentrega_do_mesmo_evento_conclui_sem_duplicar(user_id, monkeypatch):
    """BLOQUEADOR 2. Depois do 5xx a Stripe reentrega o evento INTEIRO.

    O retry tem de CONCLUIR: um grant, uma linha de funil, um e-mail.
    """
    uid, client, fake = _setup(monkeypatch, f"g9b-{user_id}")
    _conta(uid, "free", None, "inactive")

    # A dedup do `_fire_email` grava e lê `system_event_logs`; sem a tabela ela
    # é no-op silencioso e o teste mediria zero.
    garantir_system_event_logs()

    enviados: list[str] = []

    def send_pro_charged_email(*a, **k):      # __name__ é a CHAVE da dedup
        enviados.append("charged")

    import core.services.email_service as email_service
    monkeypatch.setattr(email_service, "send_pro_charged_email",
                        send_pro_charged_email, raising=False)

    evento = _evt_paid(uid, "sub_9b", 1_800_000_000)
    evento["data"]["object"]["amount_paid"] = 1990
    for _ in range(3):                       # entrega + duas reentregas
        r = _post(client, fake, evento, subs={"sub_9b": _sub("active", "price_x", 30)})
        assert r.status_code == 200, r.text

    grants = [g for g in list_grants(uid) if g["source"] == "stripe"]
    assert len(grants) == 1, "reentrega duplicou o grant"
    assert len(enviados) == 1, f"reentrega mandou {len(enviados)} e-mails"
    assert _ler(uid)["plan"] == "pro"


def test_9b_funil_nao_duplica_na_reentrega(user_id, monkeypatch):
    """BLOQUEADOR 2, a metade do funil: unique parcial `(session_id, kind)`.

    Isto já duplicava HOJE, sem relação com grants — qualquer 5xx do handler
    faz a Stripe repetir e o insert era incondicional.
    """
    uid, client, fake = _setup(monkeypatch, f"g9bf-{user_id}")
    _conta(uid, "free", None, "inactive")

    evento = _evt_checkout(uid, "sub_9bf", 1_800_000_000, "cs_9bf")
    for _ in range(3):
        r = _post(client, fake, evento, subs={"sub_9bf": _sub("active", "price_x", 30)})
        assert r.status_code == 200, r.text

    assert _funil(uid) == 1, "reentrega inflou a conversão do funil"


# ── 9g / 9h: identidade da assinatura ─────────────────────────────────────────

def test_9g_subscription_string_e_expandida_dao_a_MESMA_chave(user_id, monkeypatch):
    """BLOQUEADOR 5. O ramo do checkout lia `subscription` cru; o `invoice.paid`
    normaliza. Depois que a revogação passou a mirar `external_ref` exato, os
    dois lados têm de produzir a mesma string — senão o assinante fica com dois
    grants da mesma assinatura e o `deleted` só mata um."""
    uid, client, fake = _setup(monkeypatch, f"g9g-{user_id}")
    _conta(uid, "free", None, "inactive")
    subs = {"sub_9g": _sub("active", "price_x", 30)}

    r = _post(client, fake, _evt_checkout(uid, "sub_9g", 1_800_000_000, "cs_a"), subs=subs)
    assert r.status_code == 200, r.text
    r = _post(client, fake,
              _evt_checkout(uid, {"id": "sub_9g", "object": "subscription"},
                            1_800_000_100, "cs_b"), subs=subs)
    assert r.status_code == 200, r.text

    grants = [g for g in list_grants(uid) if g["source"] == "stripe"]
    assert len(grants) == 1, f"chaves diferentes geraram {len(grants)} grants"
    assert grants[0]["external_ref"] == "sub_9g"


def test_9h_deleted_da_assinatura_ANTIGA_nao_mata_a_NOVA_ja_paga(user_id, monkeypatch):
    """BLOQUEADOR 6. Duas assinaturas vivas; chega o `deleted` da antiga.

    Revogar "todos os grants de cartão" derrubava a recém-paga junto e mandava
    para `free` quem estava em dia — dinheiro recebido, acesso tirado.
    """
    uid, client, fake = _setup(monkeypatch, f"g9h-{user_id}")
    _post(client, fake, _evt_paid(uid, "sub_VELHA", 1_800_000_000),
          subs={"sub_VELHA": _sub("active", "price_x", 5)})
    _post(client, fake, _evt_paid(uid, "sub_NOVA", 1_800_000_100),
          subs={"sub_NOVA": _sub("active", "price_x", 365)})

    r = _post(client, fake, _evt_deleted(uid, "sub_VELHA", 1_800_000_200))
    assert r.status_code == 200, r.text

    por_ref = {g["external_ref"]: g["status"] for g in list_grants(uid)}
    assert por_ref["sub_VELHA"] == "revoked"
    assert por_ref["sub_NOVA"] == "active", "a assinatura recém-paga foi revogada junto"
    assert _ler(uid)["plan"] == "pro", "quem está em dia não pode cair para free"


def test_B1_checkout_SEM_subscription_nao_estoura(user_id, monkeypatch):
    """Sessão de checkout sem `subscription` (pagamento avulso).

    `_invoice_subscription_id` caía no fallback do `parent` lendo a variável
    LIVRE `invoice`, só ligada no ramo `invoice.paid` → UnboundLocalError → 500.
    A Stripe reentregaria por 3 dias e desistiria, e o ramo que loga "Checkout
    do Stripe concluido (sem subscription)" era inalcançável.
    """
    uid, client, fake = _setup(monkeypatch, f"gb1-{user_id}")
    _conta(uid, "free", None, "inactive")

    r = _post(client, fake, _evt_checkout(uid, None, 1_800_000_000, "cs_sem_sub"))

    assert r.status_code == 200, r.text
    assert list_grants(uid) == [], "sessão sem assinatura não pode virar grant"
    assert _funil(uid) == 1, "o ramo 'sem subscription' precisa continuar alcançável"


def test_acesso_volta_pelo_invoice_paid_apos_o_vencimento(user_id, monkeypatch):
    """Recuperação de pagamento devolve o acesso pelo FLUXO NORMAL.

    Sequência real: a cobrança atrasa, o período pago vence e a varredura
    rebaixa; depois o Stripe recupera a cobrança. O `invoice.paid` materializa o
    grant novo e a projeção devolve o acesso — sem caminho especial, sem
    carência, sem grant de `grace`. É o que torna o rebaixamento por vencimento
    reversível, e não tinha teste.
    """
    uid, client, fake = _setup(monkeypatch, f"gvolta-{user_id}")
    agora = datetime.now(timezone.utc)

    # Estado pós-vencimento: grant vencido e conta já rebaixada pela varredura.
    _conta(uid, "free", None, "past_due")
    upsert_grant(uid, "stripe", "sub_volta", "pro",
                 agora - timedelta(days=395), agora - timedelta(days=16), 1_000)
    assert _ler(uid)["plan"] == "free"

    # O Stripe recupera a cobrança.
    r = _post(client, fake, _evt_paid(uid, "sub_volta", 1_900_000_000),
              subs={"sub_volta": _sub("active", "price_x", 30)})
    assert r.status_code == 200, r.text

    row = _ler(uid)
    assert row["plan"] == "pro", "o acesso não voltou com o pagamento recuperado"
    assert row["plan_expires_at"] > agora
    assert row["last_payment_status"] == "active"
