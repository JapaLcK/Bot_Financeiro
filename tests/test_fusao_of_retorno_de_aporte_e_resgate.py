"""O saldo que aporte e resgate DEVOLVEM fala a mesma base da guarda que autorizou.

O APONTAMENTO (Codex, PR #443): `investment_deposit_from_account` autoriza
contra `accounts.balance + merged_wallet_delta` e devolvia o `new_acc` CRU.
Com cru 50 + fundido 50, um aporte de 80 passava e a resposta dizia -30 com a
Carteira exibindo 20.

A MESMA assimetria estava nas quatro funções — aporte e resgate, de
investimento E de caixinha — e as quatro chegam a JSON: `account_balance` nas
rotas do dashboard (`frontend/finance_bot_websocket_custom.py` e
`frontend/routes/pockets.py`), além do adaptador do Discord. O conserto é na
FONTE (`carteira_exibida` depois do commit), e o handler de caixinha, que
corrigia no formatador, voltou a usar o valor que já chega corrigido.

Controle negativo: devolver o cru deixa os quatro casos com fusão vermelhos.
Positivo: sem fusão, o retorno é o de antes (cru == exibido).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import db
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, ia_fora, manda, of_tx_pendente, saldo_bruto, sincroniza, tx,
    uid_pro,
)


def _funde_cinquenta(uid: int) -> None:
    """Carteira exibida 100,00 = cru 50,00 + os 50,00 do gasto fundido (com a
    confirmação do usuário — lançamento manual nunca funde em silêncio)."""
    hoje = today_tz()
    conexao = conecta_banco(uid, "1000.00")
    db.add_launch_and_update_balance(uid, "receita", 100, None, "seed")
    manda(uid, "gastei 50 no mercado em dinheiro")
    sincroniza(conexao, uid, "950.00", [tx(uid, "-50.00", hoje, "MERCADO")])
    rep = db.import_open_finance_launches(uid, conexao)
    assert rep["pending"] == 1 and rep["auto_merged"] == 0, rep
    db.confirm_reconciliation(uid, of_tx_pendente(uid))
    assert saldo_bruto(uid) == Decimal("50"), "a correção é de LEITURA"


def _exibido(uid: int) -> float:
    return float(db.get_consolidated_balance(uid)["manual"])


def _tres_fontes_concordam(uid: int, devolvido) -> None:
    """O retorno == `carteira_exibida` == `get_consolidated_balance()["manual"]`."""
    from db.accounts import carteira_exibida
    assert float(devolvido) == pytest.approx(_exibido(uid))
    assert float(devolvido) == pytest.approx(float(carteira_exibida(uid)))


# ── investimento ───────────────────────────────────────────────────────────

def test_aporte_em_investimento_devolve_a_carteira_exibida(uid_pro, ia_fora):
    """O exemplo do apontamento: aporte de 80 contra exibido 100 (cru 50)."""
    _funde_cinquenta(uid_pro)
    db.create_investment_db(uid_pro, "Reserva", 1.0, "cdi", "seed")

    _lid, conta, _inv, _canon = db.investment_deposit_from_account(
        uid_pro, "Reserva", 80, "aporte")

    assert float(conta) == pytest.approx(20.0), "sem o conserto: -30,00 (o cru)"
    assert saldo_bruto(uid_pro) == Decimal("-30"), "o débito foi gravado de verdade"
    _tres_fontes_concordam(uid_pro, conta)


def test_resgate_de_investimento_devolve_a_carteira_exibida(uid_pro, ia_fora):
    _funde_cinquenta(uid_pro)
    db.create_investment_db(uid_pro, "Reserva", 1.0, "cdi", "seed")
    db.investment_deposit_from_account(uid_pro, "Reserva", 80, "aporte")

    _lid, conta, _inv, _canon, _taxes, _dest = db.investment_withdraw_to_account(
        uid_pro, "Reserva", 30, "resgate")

    # exibido 20 + 30 resgatados (recém-aplicado: sem IR/IOF relevante)
    assert float(conta) == pytest.approx(_exibido(uid_pro))
    assert float(conta) - float(saldo_bruto(uid_pro)) == pytest.approx(50.0), \
        "sem o conserto a diferença é 0: o retorno é o cru"
    _tres_fontes_concordam(uid_pro, conta)


# ── caixinha ───────────────────────────────────────────────────────────────

def test_aporte_em_caixinha_devolve_a_carteira_exibida(uid_pro, ia_fora):
    _funde_cinquenta(uid_pro)
    db.create_pocket(uid_pro, "Viagem")

    _lid, conta, _pkt, _canon = db.pocket_deposit_from_account(
        uid_pro, "Viagem", 80, "aporte")

    assert float(conta) == pytest.approx(20.0), "sem o conserto: -30,00 (o cru)"
    _tres_fontes_concordam(uid_pro, conta)


def test_resgate_de_caixinha_devolve_a_carteira_exibida(uid_pro, ia_fora):
    _funde_cinquenta(uid_pro)
    db.create_pocket(uid_pro, "Viagem")
    db.pocket_deposit_from_account(uid_pro, "Viagem", 80, "aporte")

    _lid, conta, _pkt, _canon, _taxes, _dest = db.pocket_withdraw_to_account(
        uid_pro, "Viagem", 30, "resgate")

    assert float(conta) - float(saldo_bruto(uid_pro)) == pytest.approx(50.0), \
        "sem o conserto a diferença é 0: o retorno é o cru"
    _tres_fontes_concordam(uid_pro, conta)


# ── o contrato de JSON do dashboard ────────────────────────────────────────

def test_rota_de_aporte_em_caixinha_devolve_account_balance_exibido(uid_pro, ia_fora, monkeypatch):
    """Onde o apontamento vira contrato: `account_balance` no JSON da rota."""
    import asyncio
    from frontend.routes import pockets as rota, shared

    monkeypatch.setattr(shared, "authorize_dashboard_access", lambda *a, **k: None)
    _funde_cinquenta(uid_pro)
    db.create_pocket(uid_pro, "Viagem")

    corpo = asyncio.run(rota.pocket_deposit_route(
        object(), uid_pro, "Viagem", rota.PocketMovePayload(amount=80)))

    assert float(corpo["account_balance"]) == pytest.approx(20.0), \
        "sem o conserto: -30,00 no JSON"


# ── POSITIVO: sem fusão nada muda ──────────────────────────────────────────

def test_sem_fusao_o_retorno_e_o_mesmo_de_antes(uid_pro, ia_fora):
    """Sem banco, cru == exibido: o conserto não pode mexer no caminho comum."""
    db.add_launch_and_update_balance(uid_pro, "receita", 100, None, "seed")
    db.create_investment_db(uid_pro, "Reserva", 1.0, "cdi", "seed")
    db.create_pocket(uid_pro, "Viagem")

    _l, conta_inv, _i, _c = db.investment_deposit_from_account(uid_pro, "Reserva", 30, "aporte")
    assert float(conta_inv) == pytest.approx(70.0)

    _l, conta_pkt, _p, _c = db.pocket_deposit_from_account(uid_pro, "Viagem", 20, "aporte")
    assert float(conta_pkt) == pytest.approx(50.0)
    assert float(conta_pkt) == pytest.approx(float(saldo_bruto(uid_pro))), \
        "sem fusão, exibido tem de ser igual ao cru"
