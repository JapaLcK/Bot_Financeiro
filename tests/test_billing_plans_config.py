"""GET /billing/plans-config — os flags de disponibilidade por plano.

A /precos usa `<plano>_available` pra decidir se o botão vira "Indisponível".
O flag do Plus tem que seguir a MESMA resolução de price id do checkout
(`_resolve_price_id`, que aceita o fallback legado STRIPE_PRICE_ID_PRO): se os
dois divergirem, a página mostra "Indisponível" num plano cujo checkout
funciona — ou o contrário. Indisponível = NENHUM intervalo resolve, mensal ou
anual. Por isso cada caso compara o flag com o `_resolve_price_id`, e não com
um literal (CLAUDE.md §0.7).

A rota não tem auth e não toca banco: TestClient sem lifespan basta.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard

client = TestClient(dashboard.app)


def test_plus_available_existe_na_resposta():
    body = client.get("/billing/plans-config").json()
    assert "plus_available" in body, f"chave ausente: {sorted(body)}"
    assert isinstance(body["plus_available"], bool)


@pytest.mark.parametrize(
    "mensal, legado, esperado",
    [
        ("price_plus_mensal", "", True),          # env nova
        ("", "price_legacy_pro", True),           # SÓ a env legada: checkout funciona
        ("price_plus_mensal", "price_legacy_pro", True),
        ("", "", False),                          # nada configurado
    ],
)
def test_plus_available_segue_a_resolucao_de_price_id(monkeypatch, mensal, legado, esperado):
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", mensal)
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO", legado)
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "")  # casos só do mensal

    flag = client.get("/billing/plans-config").json()["plus_available"]
    assert flag is esperado
    # o flag não pode divergir de quem realmente decide se o checkout sai
    assert flag == bool(dashboard._resolve_price_id("plus", "monthly"))


def test_plus_available_com_so_o_anual_configurado(monkeypatch):
    """Deploy que configurou só STRIPE_PRICE_ID_PRO_ANUAL: o checkout anual sai
    (`_resolve_price_id("plus", "annual")`), então o Plus NÃO pode aparecer como
    indisponível — a /precos desabilitaria os dois botões antes de o cliente
    poder trocar de ciclo. Achado do Codex no PR #239."""
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO", "")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "price_plus_anual")

    assert dashboard._resolve_price_id("plus", "monthly") == ""  # só o anual resolve
    assert client.get("/billing/plans-config").json()["plus_available"] is True

    # Controle positivo: sem NENHUM intervalo configurado o flag volta a ser
    # False. Sem ele o grupo passaria numa implementação que devolve True sempre.
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_ANUAL", "")
    assert client.get("/billing/plans-config").json()["plus_available"] is False
