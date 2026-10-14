"""Tools de escrita do chat da IA: falha DEPOIS do commit nunca vira "tenta de novo".

O dinheiro já andou quando a mensagem de sucesso é montada. Se a montagem levantava
dentro do mesmo `try` da gravação, a resposta era "Não consegui … Tenta de novo" (ou
"Não achei", no caso de KeyError/IndexError) e o usuário repetia o pedido — em dobro.
O WhatsApp já protege isso (core/handlers/pockets.py, "DEPOIS do commit").

A gravação (`db.*`) é trocada por um stub que CONTA as chamadas: o que se mede é a
resposta ao usuário e que o db foi tocado uma vez só. Não prova o fluxo ponta a ponta
com banco real (o commit aqui é do stub).
"""
from datetime import date

import pytest

import db
# Importado ANTES dos stubs: `core.handlers.credit` faz `from db import get_card_by_id` no
# topo, e a 1ª importação sob o monkeypatch o prenderia ao stub para o resto da sessão.
import core.handlers.credit  # noqa: F401
from core.services import funding
from core.services.ai_chat.tools import cards as t_cards
from core.services.ai_chat.tools import investments as t_inv
from core.services.ai_chat.tools import pockets as t_pockets

PROIBIDO = ("tenta de novo", "Tenta de novo", "Não achei", "Não consegui")


def _ok(msg: str):
    assert msg.startswith("✅"), msg
    assert not any(p in msg for p in PROIBIDO), msg


def _boom(*_a, **_k):
    raise RuntimeError("falha na montagem")


@pytest.fixture
def carteira(monkeypatch):
    monkeypatch.setattr(
        funding, "resolve_deterministic", lambda uid, amount: {"source": {"kind": funding.CARTEIRA}}
    )


# ── caixinha: depósito ───────────────────────────────────────────────────────

def _stub_deposito(monkeypatch, *, meta=None):
    chamadas = []

    def dep(uid, nome, amount, **kw):
        chamadas.append(amount)
        return 1, 0, 0.01, "Viagem"

    monkeypatch.setattr(db, "pocket_deposit_from_account", dep)
    monkeypatch.setattr(
        db, "list_pockets",
        lambda uid, accrue=True: [{"name": "Viagem", "target_amount": meta, "target_date": None}],
    )
    return chamadas


def test_deposito_de_1_centavo_numa_meta_grande_nao_pede_para_repetir(monkeypatch, carteira):
    """Caso REAL: `_pace_line` projetava 1000/0,01 = 100 mil meses e `date + timedelta`
    estourava OverflowError, com o depósito já commitado. A projeção sem sentido sai,
    a linha de progresso (que não depende dela) fica."""
    chamadas = _stub_deposito(monkeypatch, meta=1000)

    msg = t_pockets._pocket_deposit_execute(1, {"pocket_name": "Viagem", "amount": 0.01})

    _ok(msg)
    assert "Depositado R$ 0,01" in msg and "📊 Agora" in msg
    assert "Mantendo esse ritmo" not in msg
    assert chamadas == [0.01]


def test_pace_line_limite_de_100_anos():
    hoje = date(2026, 10, 13)
    assert t_pockets._pace_line(0.01, 1000.0, hoje) == ""  # era OverflowError
    assert t_pockets._pace_line(100.0, 1000.0, hoje).startswith("⏳ Mantendo esse ritmo")


def test_deposito_followup_levantando_keyerror_nao_vira_nao_achei(monkeypatch, carteira):
    _stub_deposito(monkeypatch)
    monkeypatch.setattr(t_pockets, "_deposit_followup_lines", lambda *a: {}["x"])

    _ok(t_pockets._pocket_deposit_execute(1, {"pocket_name": "Viagem", "amount": 50}))


def test_deposito_via_banco_mantem_a_nota_quando_o_followup_falha(monkeypatch):
    _stub_deposito(monkeypatch)
    monkeypatch.setattr(
        funding, "resolve_deterministic",
        lambda uid, amount: {"source": {"kind": funding.BANK, "label": "Nubank"}},
    )
    monkeypatch.setattr(t_pockets, "_deposit_followup_lines", _boom)

    msg = t_pockets._pocket_deposit_execute(1, {"pocket_name": "Viagem", "amount": 50})

    _ok(msg)
    assert "saindo do Nubank" in msg and funding.nota_sync() in msg


def test_deposito_controle_positivo_antes_do_commit_continua_recusando(monkeypatch, carteira):
    """O que falha NA gravação segue com a mensagem de antes (nada se moveu)."""
    monkeypatch.setattr(db, "pocket_deposit_from_account", lambda *a, **k: (_ for _ in ()).throw(LookupError()))
    assert "Não achei a caixinha" in t_pockets._pocket_deposit_execute(1, {"pocket_name": "X", "amount": 5})

    monkeypatch.setattr(db, "pocket_deposit_from_account", lambda *a, **k: _boom())
    assert "Tenta de novo" in t_pockets._pocket_deposit_execute(1, {"pocket_name": "X", "amount": 5})


# ── caixinha: saque ──────────────────────────────────────────────────────────

def test_saque_caixinha_taxes_ilegivel_nao_pede_para_repetir(monkeypatch):
    chamadas = []

    def saque(*a, **k):
        chamadas.append(1)
        return 1, 0, 0, "Viagem", {"gross": None}, None  # float(None) levanta pós-commit

    monkeypatch.setattr(db, "pocket_withdraw_to_account", saque)

    msg = t_pockets._pocket_withdraw_execute(1, {"pocket_name": "Viagem", "amount": 10})

    _ok(msg)
    assert chamadas == [1]


class _TaxesQueLevantaKeyError(dict):
    def get(self, *a, **k):
        raise KeyError("gross")


def test_saque_caixinha_keyerror_pos_commit_nao_vira_nao_achei(monkeypatch):
    monkeypatch.setattr(
        db, "pocket_withdraw_to_account",
        lambda *a, **k: (1, 0, 0, "Viagem", _TaxesQueLevantaKeyError(gross=1), None),
    )

    _ok(t_pockets._pocket_withdraw_execute(1, {"pocket_name": "Viagem", "amount": 10}))


def test_saque_caixinha_controle_positivo(monkeypatch):
    taxes = {"gross": 10.0, "ir": 0.5, "iof": 0.0}
    monkeypatch.setattr(db, "pocket_withdraw_to_account", lambda *a, **k: (1, 0, 0, "Viagem", taxes, None))
    msg = t_pockets._pocket_withdraw_execute(1, {"pocket_name": "Viagem", "amount": 10})
    assert msg == '✅ Sacado R$ 10.00 da caixinha "Viagem" — IR/IOF R$ 0.50.'
    assert t_pockets._pocket_withdraw_execute(1, {"pocket_name": "Viagem", "withdraw_all": True}) == \
        '✅ Caixinha "Viagem" esvaziada: sacado R$ 10.00 — IR/IOF R$ 0.50.'

    monkeypatch.setattr(db, "pocket_withdraw_to_account", lambda *a, **k: (_ for _ in ()).throw(LookupError()))
    assert "Não achei a caixinha" in t_pockets._pocket_withdraw_execute(1, {"pocket_name": "X", "amount": 1})

    monkeypatch.setattr(db, "pocket_withdraw_to_account", lambda *a, **k: _boom())
    assert "Tenta de novo" in t_pockets._pocket_withdraw_execute(1, {"pocket_name": "X", "amount": 1})


# ── investimento ─────────────────────────────────────────────────────────────

def test_aporte_investimento_via_banco_mantem_a_nota_de_sync(monkeypatch):
    monkeypatch.setattr(
        funding, "resolve_deterministic",
        lambda uid, amount: {"source": {"kind": funding.BANK, "label": "Nubank"}},
    )
    monkeypatch.setattr(db, "investment_deposit_from_account", lambda *a, **k: None)

    msg = t_inv._investment_deposit_execute(1, {"name": "CDB", "amount": 100})

    _ok(msg)
    assert "saindo do Nubank" in msg and funding.nota_sync() in msg


def test_aporte_investimento_controle_positivo(monkeypatch, carteira):
    monkeypatch.setattr(db, "investment_deposit_from_account", lambda *a, **k: None)
    assert t_inv._investment_deposit_execute(1, {"name": "CDB", "amount": 100}) == \
        '✅ Aporte de R$ 100.00 no "CDB" registrado.'

    monkeypatch.setattr(db, "investment_deposit_from_account", lambda *a, **k: (_ for _ in ()).throw(LookupError()))
    assert "Não achei o investimento" in t_inv._investment_deposit_execute(1, {"name": "CDB", "amount": 1})

    monkeypatch.setattr(db, "investment_deposit_from_account", lambda *a, **k: _boom())
    assert "Tenta de novo" in t_inv._investment_deposit_execute(1, {"name": "CDB", "amount": 1})


def test_resgate_investimento_taxes_ilegivel_nao_pede_para_repetir(monkeypatch):
    chamadas = []

    def resgate(*a, **k):
        chamadas.append(1)
        return 1, 0, 0, "CDB", {"gross": None}, None

    monkeypatch.setattr(db, "investment_withdraw_to_account", resgate)

    _ok(t_inv._investment_withdraw_execute(1, {"name": "CDB", "amount": 10}))
    assert chamadas == [1]


def test_resgate_investimento_controle_positivo(monkeypatch):
    taxes = {"gross": 10.0, "ir": 0.0, "iof": 0.0}
    monkeypatch.setattr(db, "investment_withdraw_to_account", lambda *a, **k: (1, 0, 0, "CDB", taxes, None))
    assert t_inv._investment_withdraw_execute(1, {"name": "CDB", "amount": 10}) == \
        '✅ Resgate de R$ 10.00 do "CDB".'

    taxes = {"gross": 100.0, "ir": 1.5, "iof": 0.5}
    monkeypatch.setattr(db, "investment_withdraw_to_account", lambda *a, **k: (1, 0, 0, "CDB", taxes, None))
    assert t_inv._investment_withdraw_execute(1, {"name": "CDB", "amount": 100}) == \
        '✅ Resgate de R$ 100.00 do "CDB" — IR/IOF R$ 2.00.'
    assert t_inv._investment_withdraw_execute(1, {"name": "CDB", "withdraw_all": True}) == \
        '✅ Investimento "CDB" zerado: resgatado R$ 100.00 — IR/IOF R$ 2.00.'

    monkeypatch.setattr(db, "investment_withdraw_to_account", lambda *a, **k: (_ for _ in ()).throw(LookupError()))
    assert "Não achei o investimento" in t_inv._investment_withdraw_execute(1, {"name": "X", "amount": 1})

    monkeypatch.setattr(db, "investment_withdraw_to_account", lambda *a, **k: _boom())
    assert "Tenta de novo" in t_inv._investment_withdraw_execute(1, {"name": "X", "amount": 1})


# ── fatura do cartão ─────────────────────────────────────────────────────────

def _stub_fatura(monkeypatch, res):
    chamadas = []
    monkeypatch.setattr(t_cards, "_resolve_card_id", lambda uid, nome: 7)
    monkeypatch.setattr(db, "get_card_by_id", lambda uid, cid: {"name": "Nubank"})
    monkeypatch.setattr(db, "fatura_a_pagar", lambda *a, **k: 99)

    def pagar(*a, **k):
        chamadas.append(1)
        return res

    monkeypatch.setattr(db, "pay_bill_amount", pagar)
    return chamadas


def test_pagar_fatura_rotulo_quebrado_nao_pede_para_repetir(monkeypatch):
    chamadas = _stub_fatura(monkeypatch, {"paid": 50.0, "period_end": None})  # None.month levanta

    msg = t_cards._pay_bill_execute(1, {"card_name": "Nubank", "amount": 50})

    _ok(msg)
    assert chamadas == [1]


def test_pagar_fatura_controle_positivo(monkeypatch):
    _stub_fatura(monkeypatch, {"paid": 50.0, "period_end": date(2026, 10, 10)})
    msg = t_cards._pay_bill_execute(1, {"card_name": "Nubank", "amount": 50})
    assert msg.startswith("✅ Pagamento de R$ 50.00 da fatura Nubank")

    monkeypatch.setattr(db, "pay_bill_amount", lambda *a, **k: _boom())
    assert "Tenta de novo" in t_cards._pay_bill_execute(1, {"card_name": "Nubank", "amount": 50})


# ── a leitura pós-commit DENTRO dos helpers do db ────────────────────────────

def test_carteira_exibida_com_fallback_nao_le_nada_se_a_leitura_falha(monkeypatch):
    """Os helpers `db.pocket_*`/`db.investment_*`/`db.pay_bill_amount` chamam
    `carteira_exibida(user_id, new_acc)` DEPOIS do commit. Com o consolidado E o saldo cru
    falhando (banco fora), nada pode escapar nem reler: o fallback é devolvido."""
    from db import accounts, open_finance

    monkeypatch.setattr(open_finance, "get_consolidated_balance", _boom)
    monkeypatch.setattr(accounts, "get_balance", _boom)

    assert accounts.carteira_exibida(1, 5) == 5
