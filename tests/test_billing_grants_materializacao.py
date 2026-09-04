"""
tests/test_billing_grants_materializacao.py — §4.1.1 e §4.1.2 do
docs/plano_pix_anual_asaas.md: materialização obrigatória do grant e regra da
redução. Casos 9a–9e, 9g e 9h do §16.

Terceiro arquivo, e não um apêndice dos outros dois: o corte pedido punha 9a–9e
+ 9g + 9h dentro de `..._projecao.py`, o que levava aquele arquivo a ~600
linhas — o dobro do teto de 350 que motivou o corte. "Um arquivo por assunto"
continua valendo: o assunto aqui é a decisão v6 (o grant não pode ficar para
trás, e ninguém é rebaixado sem confirmação), que não é nem backfill nem
projeção pura.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _billing_grants_helpers import (
    conta as _conta, evt_checkout as _evt_checkout, evt_deleted as _evt_deleted,
    evt_paid as _evt_paid, garantir_system_event_logs, grant as _grant,
    ler as _ler, set_customer, sub_stripe as _sub,
)
from core.services import billing_access
from core.services.billing_access import recompute_entitlement
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


# ── 9c / 9d / 9e: a regra da redução (§4.1.1 B) ───────────────────────────────

class _FakeStripeSubs:
    """Só o `Subscription.list` que o `_find_active_subscription` usa."""

    def __init__(self, ativa=None, erro=False):
        self._ativa, self._erro = ativa, erro
        outer = self

        class _S:
            @staticmethod
            def list(customer=None, status=None, limit=None):
                if outer._erro:
                    raise RuntimeError("stripe fora do ar")
                if status == "active" and outer._ativa:
                    return {"data": [outer._ativa]}
                return {"data": []}

        self.Subscription = _S


def _cenario_grant_defasado(uid: int, dias_restantes: int = 330):
    """Grant congelado no período anterior + auth_accounts fresco.

    É o estado que "materialização falhou" e "assinatura acabou" produzem
    IGUAL — e é por isso que só o Stripe pode separar os dois.
    """
    agora = datetime.now(timezone.utc)
    _conta(uid, "pro", agora + timedelta(days=dias_restantes), "active")
    set_customer(uid, f"cus_{uid}")
    upsert_grant(uid, "stripe", f"sub_def_{uid}", "pro",
                 agora - timedelta(days=395), agora - timedelta(days=30), 1_000)
    return agora


def test_9c_varredura_repara_o_grant_defasado_e_NAO_rebaixa_o_pagante(user_id, monkeypatch):
    """BLOQUEADOR 3 — a classe que derrubava pagante (`pro/2027-07-01 → free/None`).

    Assinatura VIVA no Stripe: a varredura consulta, repara o grant a partir
    dela, e em nenhum instante a conta fica `free`.
    """
    agora = _cenario_grant_defasado(user_id)
    fim = agora + timedelta(days=330)
    viva = {"id": f"sub_def_{user_id}", "status": "active",
            "current_period_end": int(fim.timestamp()),
            "items": {"data": [{"current_period_end": int(fim.timestamp())}]}}
    monkeypatch.setitem(__import__("sys").modules, "stripe", _FakeStripeSubs(ativa=viva))

    resultado = recompute_entitlement(user_id, origem="varredura")

    assert _ler(user_id)["plan"] == "pro", "pagante foi rebaixado pela varredura"
    assert resultado and resultado["plan"] == "pro"
    g = _grant(user_id, "stripe")
    assert g["ends_at"] > agora, "o grant defasado não foi reparado"
    assert g["plan_stored"] == "pro", "o reparo não pode inventar tier"


def test_9d_stripe_indisponivel_nao_escreve_nada_e_alerta(user_id, monkeypatch):
    """BLOQUEADOR 3, variante: rebaixar por timeout de rede é perda de produto
    de quem pagou. Não reduz, loga, alerta."""
    _cenario_grant_defasado(user_id)
    monkeypatch.setitem(__import__("sys").modules, "stripe", _FakeStripeSubs(erro=True))

    alertas: list[str] = []
    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda m: alertas.append(m) or True)

    assert recompute_entitlement(user_id, origem="varredura") is None
    assert _ler(user_id)["plan"] == "pro", "rebaixou sem o Stripe confirmar"
    assert len(alertas) == 1


def test_9e_POSITIVO_assinatura_encerrada_de_fato_REDUZ(user_id, monkeypatch):
    """BLOQUEADOR 3, positivo. Sem ele o grupo passaria num código que NUNCA
    rebaixa — pior que o bug, porque quem cancelou continuaria com o produto."""
    _cenario_grant_defasado(user_id)
    monkeypatch.setitem(__import__("sys").modules, "stripe", _FakeStripeSubs(ativa=None))

    assert recompute_entitlement(user_id, origem="varredura") == {
        "plan": "free", "plan_expires_at": None}
    assert _ler(user_id)["plan"] == "free"


def test_9e_reducao_por_EVENTO_nao_consulta_o_stripe(user_id, monkeypatch):
    """A outra metade da regra: evento É autoridade e reduz direto. Se o
    caminho de evento consultasse o Stripe, um cancelamento durante uma queda
    do Stripe ficaria sem efeito."""
    _cenario_grant_defasado(user_id)

    def _explode(*a, **k):
        raise AssertionError("o caminho de EVENTO não pode consultar o Stripe")

    monkeypatch.setattr(billing_access, "_reparar_grant_pelo_stripe", _explode)
    assert recompute_entitlement(user_id) == {"plan": "free", "plan_expires_at": None}


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
