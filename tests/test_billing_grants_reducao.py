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
    FakeStripeSubs, conta as _conta, grant as _grant, ler as _ler, set_customer,
)
from core.services import billing_access
from core.services.billing_access import recompute_entitlement
from db.plan_grants import upsert_grant


# ── 9c / 9d / 9e: a regra da redução (§4.1.1 B) ───────────────────────────────

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
    monkeypatch.setitem(__import__("sys").modules, "stripe", FakeStripeSubs(ativa=viva))

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
    monkeypatch.setitem(__import__("sys").modules, "stripe", FakeStripeSubs(erro=True))

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
    monkeypatch.setitem(__import__("sys").modules, "stripe", FakeStripeSubs(ativa=None))

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
    monkeypatch.setitem(__import__("sys").modules, "stripe", FakeStripeSubs(ativa=viva))

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
    monkeypatch.setitem(__import__("sys").modules, "stripe", FakeStripeSubs(ativa=viva))

    alertas: list[str] = []
    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda m: alertas.append(m) or True)

    assert recompute_entitlement(user_id, origem="varredura") is None
    assert _ler(user_id)["plan"] == "pro"
    assert len(alertas) == 1


def test_9l_celula10_legacy_revogado_nao_ressuscita_e_nao_reduz(user_id, monkeypatch):
    """O D4 por outra porta: o fallback do `legacy` não pode pegar um revogado.

    A pré-condição é a NORMAL, não a exótica: `_SUPERSEDE_LEGACY` revoga o
    `legacy` a todo upsert aplicado de grant `stripe`, então toda a base migrada
    fica assim assim que o primeiro `invoice.paid` entra. `list_grants` devolve
    revogados de propósito, e `upsert_grant` escreve `status='active'` zerando
    `revoked_reason` — sem filtrar por `status`, a varredura reanimava o grant
    morto, com o tier ANTERIOR à supersessão (quem baixou de tier recuperava o
    alto). A guarda de versão não segura: o reparo carimba `now()`.
    """
    from db.plan_grants import revoke_grant

    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro", agora + timedelta(days=330), "active")
    set_customer(user_id, f"cus_{user_id}")
    upsert_grant(user_id, "legacy", f"legacy:{user_id}", "pro_max",
                 agora - timedelta(days=395), agora - timedelta(days=30), 0)
    revoke_grant(user_id, "legacy", f"legacy:{user_id}", "superseded_by_stripe", 1_000)

    fim = agora + timedelta(days=330)
    viva = {"id": "sub_sem_grant", "status": "active",
            "current_period_end": int(fim.timestamp()),
            "items": {"data": [{"current_period_end": int(fim.timestamp())}]}}
    monkeypatch.setitem(__import__("sys").modules, "stripe", FakeStripeSubs(ativa=viva))

    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda m: True)

    recompute_entitlement(user_id, origem="varredura")

    g = _grant(user_id, "legacy")
    assert g["status"] == "revoked", (
        f"grant legacy REVOGADO foi ressuscitado pelo reparo: status={g['status']}, "
        f"ends_at={g['ends_at']}, revoked_reason={g['revoked_reason']}")
    assert g["revoked_reason"] == "superseded_by_stripe"
    assert g["ends_at"] < agora, "o reparo esticou um grant revogado"


def test_9m_POSITIVO_reparo_move_so_a_data_e_nao_o_tier(user_id, monkeypatch):
    """Célula 7. O reparo estica `ends_at` e deixa `plan_stored` INTACTO.

    `/billing/change-plan` troca o price mantendo o mesmo `sub_id`, então
    inferir o plano do price aqui concederia tier que ninguém comprou. O tier
    continua sendo o que o último EVENTO escreveu.
    """
    agora = datetime.now(timezone.utc)
    _conta(user_id, "essencial", agora + timedelta(days=330), "active")
    set_customer(user_id, f"cus_{user_id}")
    upsert_grant(user_id, "stripe", f"sub_9m_{user_id}", "essencial",
                 agora - timedelta(days=395), agora - timedelta(days=30), 1_000)

    fim = agora + timedelta(days=330)
    viva = {"id": f"sub_9m_{user_id}", "status": "active",
            "current_period_end": int(fim.timestamp()),
            "items": {"data": [{"price": {"id": "price_promax"},
                                "current_period_end": int(fim.timestamp())}]}}
    monkeypatch.setitem(__import__("sys").modules, "stripe", FakeStripeSubs(ativa=viva))

    recompute_entitlement(user_id, origem="varredura")

    g = _grant(user_id, "stripe")
    assert g["plan_stored"] == "essencial", "o reparo inventou tier a partir do price"
    assert g["ends_at"] > agora, "o reparo não moveu a data"
    assert _ler(user_id)["plan"] == "essencial"


def test_9n_celula8_grant_ja_cobre_o_que_o_stripe_promete(user_id, monkeypatch):
    """Grant ativo com `ends_at >= fim` e ainda assim a projeção quis reduzir:
    estado incoerente, então não decide nada — não escreve e alerta."""
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro", agora + timedelta(days=400), "active")
    set_customer(user_id, f"cus_{user_id}")
    upsert_grant(user_id, "stripe", f"sub_9n_{user_id}", "pro",
                 agora - timedelta(days=10), agora + timedelta(days=400), 1_000)

    fim = agora + timedelta(days=30)     # Stripe promete MENOS que o grant
    viva = {"id": f"sub_9n_{user_id}", "status": "active",
            "current_period_end": int(fim.timestamp()),
            "items": {"data": [{"current_period_end": int(fim.timestamp())}]}}
    monkeypatch.setitem(__import__("sys").modules, "stripe", FakeStripeSubs(ativa=viva))

    alertas: list[str] = []
    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda m: alertas.append(m) or True)

    # A projeção só quer reduzir se o grant não cobrir AGORA; força o cenário
    # revogando a cobertura vigente mas mantendo o grant ativo mais longo.
    from db.connection import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update plan_grants set starts_at = now() + interval '5 days'"
                        " where user_id=%s", (user_id,))
        conn.commit()

    antes = _grant(user_id, "stripe")["ends_at"]
    assert recompute_entitlement(user_id, origem="varredura") is None
    assert _ler(user_id)["plan"] == "pro", "escreveu numa célula que não decide"
    assert len(alertas) == 1
    # O que DISCRIMINA a célula 8: ela não escreve. Sem esta asserção o teste é
    # tautológico — deixar a célula cair no reparo ENCURTA o grant para o `fim`
    # que o Stripe promete, e o resultado observável (None, plano intacto,
    # 1 alerta) fica idêntico, porque o grant continua sem cobrir AGORA.
    assert _grant(user_id, "stripe")["ends_at"] == antes, (
        "a célula 8 escreveu: o grant foi encurtado para o período do Stripe")


def test_9o_celula4_assinatura_viva_sem_data_legivel_e_indisponivel(user_id, monkeypatch):
    """Viva mas sem `current_period_end`: `indisponivel`, NUNCA "não tem
    assinatura". Confundir os dois rebaixa quem está pagando."""
    agora = datetime.now(timezone.utc)
    _conta(user_id, "pro", agora + timedelta(days=330), "active")
    set_customer(user_id, f"cus_{user_id}")
    upsert_grant(user_id, "stripe", f"sub_9o_{user_id}", "pro",
                 agora - timedelta(days=395), agora - timedelta(days=30), 1_000)

    sem_data = {"id": f"sub_9o_{user_id}", "status": "active", "items": {"data": []}}
    monkeypatch.setitem(__import__("sys").modules, "stripe", FakeStripeSubs(ativa=sem_data))

    alertas: list[str] = []
    import core.services.admin_notify as admin_notify
    monkeypatch.setattr(admin_notify, "_send", lambda m: alertas.append(m) or True)

    assert recompute_entitlement(user_id, origem="varredura") is None
    assert _ler(user_id)["plan"] == "pro"
    assert len(alertas) == 1
