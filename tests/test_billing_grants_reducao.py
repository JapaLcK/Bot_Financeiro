"""
tests/test_billing_grants_reducao.py — a REGRA DA REDUÇÃO do §4.1.1 B do
docs/plano_pix_anual_asaas.md: quem pode tirar acesso, e sob qual confirmação.

Casos 9c, 9d, 9e do §16, mais o assinante só-`legacy` (B-2) e os dois lados do
teto de carência do `past_due` (B-3).

Arquivo próprio porque é assunto próprio: `..._materializacao.py` cobre o grant
ENTRAR (§4.1.1 A e §4.1.2); aqui é o grant SAIR.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from _billing_grants_helpers import (
    conta as _conta, grant as _grant, ler as _ler, set_customer,
)
from core.services import billing_access
from core.services.billing_access import recompute_entitlement
from db.plan_grants import upsert_grant


# ── 9c / 9d / 9e: a regra da redução (§4.1.1 B) ───────────────────────────────

class _FakeStripeSubs:
    """Só o `Subscription.list` que o `_find_active_subscription` usa.

    `status_viva` importa: o `_find_active_subscription` percorre a escada
    active > trialing > past_due e devolve a PRIMEIRA que casar, então uma
    assinatura `past_due` só aparece se a busca por `past_due` a devolver.
    """

    def __init__(self, ativa=None, erro=False, status_viva="active"):
        self._ativa, self._erro, self._status = ativa, erro, status_viva
        outer = self

        class _S:
            @staticmethod
            def list(customer=None, status=None, limit=None):
                if outer._erro:
                    raise RuntimeError("stripe fora do ar")
                if status == outer._status and outer._ativa:
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


def test_B2_assinatura_VIVA_com_so_grant_legacy_nao_rebaixa(user_id, monkeypatch):
    """O Stripe confirma assinatura VIVA e o único grant é `legacy` — que é
    exatamente o que o backfill cria para toda a base pagante do deploy.

    O código reduzia: `alvo is None` (nenhum grant `stripe` casando o `sub_id`)
    virava veredito `reduz`, com o gateway dizendo o contrário. Assinatura viva
    nunca pode resultar em redução.
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro", agora + timedelta(days=330), "active")
    set_customer(user_id, f"cus_{user_id}")
    upsert_grant(user_id, "legacy", f"legacy:{user_id}", "pro",
                 agora - timedelta(days=395), agora - timedelta(days=30), 0)

    fim = agora + timedelta(days=330)
    viva = {"id": "sub_que_nao_tem_grant", "status": "active",
            "current_period_end": int(fim.timestamp()),
            "items": {"data": [{"current_period_end": int(fim.timestamp())}]}}
    monkeypatch.setitem(__import__("sys").modules, "stripe", _FakeStripeSubs(ativa=viva))

    resultado = recompute_entitlement(user_id, origem="varredura")

    assert _ler(user_id)["plan"] == "pro", "pagante com assinatura VIVA foi rebaixado"
    assert resultado is None or resultado["plan"] != "free"
    g = _grant(user_id, "legacy")
    assert g["ends_at"] > agora, "o legacy defasado não foi reparado a partir do Stripe"


def test_B2_assinatura_viva_sem_grant_nenhum_nao_reduz(user_id, monkeypatch):
    """Sem grant algum para esticar, a varredura não decide nada: alerta e não
    escreve. Reduzir aqui tiraria acesso de quem o Stripe diz que está pagando."""
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro", agora + timedelta(days=330), "active")
    set_customer(user_id, f"cus_{user_id}")
    upsert_grant(user_id, "admin", f"admin:{user_id}", "pro",
                 agora - timedelta(days=40), agora - timedelta(days=1), 1)

    fim = agora + timedelta(days=330)
    viva = {"id": "sub_orfa", "status": "active",
            "current_period_end": int(fim.timestamp()),
            "items": {"data": [{"current_period_end": int(fim.timestamp())}]}}
    monkeypatch.setitem(__import__("sys").modules, "stripe", _FakeStripeSubs(ativa=viva))

    alertas: list[str] = []
    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda m: alertas.append(m) or True)

    assert recompute_entitlement(user_id, origem="varredura") is None
    assert _ler(user_id)["plan"] == "pro"
    assert len(alertas) == 1


def _cenario_past_due(user_id: int, monkeypatch, dias_apos_o_fim: int):
    """Grant vencido há `dias_apos_o_fim` e assinatura `past_due` no Stripe.

    O `current_period_end` do `past_due` é o período JÁ VENCIDO — é assim que o
    Stripe representa "cobrança em atraso", e é por isso que não há o que
    reparar nesse estado.
    """
    agora = datetime.now(timezone.utc)
    fim_do_periodo = agora - timedelta(days=dias_apos_o_fim)
    _conta(user_id, "pro", agora + timedelta(days=30), "past_due")
    set_customer(user_id, f"cus_{user_id}")
    upsert_grant(user_id, "stripe", f"sub_pd_{user_id}", "pro",
                 agora - timedelta(days=395), fim_do_periodo, 1_000)

    atrasada = {"id": f"sub_pd_{user_id}", "status": "past_due",
                "current_period_end": int(fim_do_periodo.timestamp()),
                "items": {"data": [{"current_period_end": int(fim_do_periodo.timestamp())}]}}
    monkeypatch.setitem(__import__("sys").modules, "stripe",
                        _FakeStripeSubs(ativa=atrasada, status_viva="past_due"))


def test_B3_past_due_no_dia_14_SEGURA_o_acesso(user_id, monkeypatch):
    """Dentro da carência de 15 dias o acesso é mantido: durante o dunning a
    assinatura está viva e o cliente ainda pode pagar. Nada é escrito."""
    _cenario_past_due(user_id, monkeypatch, dias_apos_o_fim=14)

    alertas: list[str] = []
    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda m: alertas.append(m) or True)

    assert recompute_entitlement(user_id, origem="varredura") is None
    assert _ler(user_id)["plan"] == "pro", "cortou acesso dentro da carência"
    assert alertas == [], "dunning é estado esperado; alerta diário vira ruído"


def test_B3_past_due_no_dia_16_REDUZ(user_id, monkeypatch):
    """Passado o teto, reduz MESMO com a assinatura viva no Stripe. Sem isso a
    cauda é infinita: `past_due` conta como viva e o `current_period_end` dela
    nunca avança, então nunca haveria o que reparar."""
    _cenario_past_due(user_id, monkeypatch, dias_apos_o_fim=16)

    assert recompute_entitlement(user_id, origem="varredura") == {
        "plan": "free", "plan_expires_at": None}
    assert _ler(user_id)["plan"] == "free"
