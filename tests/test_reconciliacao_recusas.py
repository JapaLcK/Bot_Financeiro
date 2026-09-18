"""Recusa por saldo insuficiente, no dashboard, com receita pendente de reconciliação.

Decisão do dono (plano aprovado, item 6): tirar os `**` (negrito Markdown) das
recusas do DASHBOARD — ele mostra o texto cru, sem render de Markdown. O
WhatsApp (core/handlers/pockets.py) continua com negrito, por isso não é
tocado aqui.

Cenário comum às três rotas: receita de R$ 100 pendente de reconciliação —
tela mostra R$ 100,00, mas o disponível é R$ 0,00 (a entrada não confirmada
não autoriza gasto, CLAUDE.md do domínio). `funding.carteira_txt(100, 0)` é a
MESMA função que compõe a frase em qualquer recusa (fonte única).

Controle negativo: a mensagem antiga ("Saldo insuficiente na conta.", sem o
número) não bate com `carteira_txt(100, 0) in detail` — reverter qualquer um
dos três pontos derruba o teste dele.
Controle positivo: valor coberto continua devolvendo 200.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import db
from core.services import funding

from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401 (fixtures)
from tests.test_fusao_of_superficies_da_carteira import _Req, sem_autorizacao  # noqa: F401
from tests.test_reconciliacao_resumo_e_guardas import _receita_100


def test_caixinha_deposito_recusa_com_carteira_txt_sem_negrito(uid_pro, ia_fora, sem_autorizacao):
    from frontend.routes import pockets as rota

    _receita_100(uid_pro)
    db.create_pocket(uid_pro, "viagem")

    with pytest.raises(HTTPException) as e:
        asyncio.run(rota.pocket_deposit_route(
            _Req(), uid_pro, "viagem", rota.PocketMovePayload(amount=50)))

    assert e.value.status_code == 400
    assert funding.carteira_txt(100, 0) in e.value.detail
    assert "**" not in e.value.detail


def test_aporte_investimento_recusa_com_carteira_txt_sem_negrito(uid_pro, ia_fora, sem_autorizacao):
    import frontend.finance_bot_websocket_custom as mono

    _receita_100(uid_pro)
    db.create_investment_db(uid_pro, "Reserva", 1.0, "cdi", "seed")

    with pytest.raises(HTTPException) as e:
        asyncio.run(mono.deposit_investment_route(
            _Req(), uid_pro, mono.InvestmentMovementPayload(name="Reserva", amount=50)))

    assert e.value.status_code == 400
    assert funding.carteira_txt(100, 0) in e.value.detail
    assert "**" not in e.value.detail


def test_criar_investimento_com_aporte_inicial_recusa_com_carteira_txt_sem_negrito(
        uid_pro, ia_fora, sem_autorizacao):
    import frontend.finance_bot_websocket_custom as mono

    _receita_100(uid_pro)

    with pytest.raises(HTTPException) as e:
        asyncio.run(mono.create_investment_route(
            _Req(), uid_pro,
            mono.InvestmentCreatePayload(name="CDB", rate=1.0, period="cdi", initial_amount=50)))

    assert e.value.status_code == 400
    assert funding.carteira_txt(100, 0) in e.value.detail
    assert "**" not in e.value.detail


def test_valor_coberto_continua_autorizando(uid_pro, ia_fora, sem_autorizacao):
    """Positivo: sem pendência, a caixinha aceita o depósito normalmente (200)."""
    from frontend.routes import pockets as rota

    db.add_launch_and_update_balance(uid_pro, "receita", 100, None, "seed")
    db.create_pocket(uid_pro, "viagem")

    corpo = asyncio.run(rota.pocket_deposit_route(
        _Req(), uid_pro, "viagem", rota.PocketMovePayload(amount=50)))

    assert corpo["ok"] is True
