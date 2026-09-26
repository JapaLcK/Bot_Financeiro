"""Q40/Q7 — conta a pagar com banco conectado: a FORMA vem antes do valor.

Pelo banco, a conta só é marcada como paga (o débito chega pelo Open Finance,
sem lançamento). Em dinheiro, debita a Carteira como sempre. Sem banco
conectado, cada entrada faz exatamente o que fazia (decisão A2).

Estado real no banco; a conversa passa pelo `handle_incoming` (`manda()`) e o
botão "✅ Já paguei" pelo `process_message` real do WhatsApp.
"""
from __future__ import annotations

import threading

import pytest

import db
import db.bills as B
from conftest import promote_to_pro, usuario_pagante
from tests._fusao_of_helpers import ia_fora, manda  # noqa: F401 (fixture)
from tests.test_bill_amount_pending import (_manda_texto_no_wa, _monta_conta_variavel,
                                            _toca_ja_paguei)
from tests.test_manual_launches_carteira_piggy import _connect_fake_bank, _dashboard_client
from utils_date import today_tz


def _q(sql, params):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        conn.commit()
    return row


def manuais(uid) -> int:
    return _q("select count(*) n from launches where user_id=%s "
              "and coalesce(source,'manual') <> 'open_finance'", (uid,))["n"]


def carteira(uid) -> float:
    return float(db.get_consolidated_balance(uid)["manual"] or 0)


def conta_fixa(uid, nome="Luz", valor=120.0):
    return B.create_boleto(uid, nome, valor, today_tz(), category="moradia")


def pendencia(uid):
    p = db.get_pending_action(uid)
    return p["action_type"] if p else None


@pytest.fixture
def com_of():
    uid = usuario_pagante()
    _connect_fake_bank(uid)
    return uid


@pytest.fixture
def sem_of():
    return usuario_pagante()


# ── mark_bill_paid(metodo) ───────────────────────────────────────────────────

def test_banco_marca_paga_sem_lancamento_e_sem_debito(sem_of):
    conta = conta_fixa(sem_of)
    antes = carteira(sem_of)
    paga = B.mark_bill_paid(sem_of, conta["id"], None, metodo="banco")
    assert paga["status"] == "paid" and paga["launch_id"] is None
    assert paga["paid_amount"] is None
    assert manuais(sem_of) == 0 and carteira(sem_of) == pytest.approx(antes)


def test_carteira_cria_um_lancamento_e_debita(sem_of):
    conta = conta_fixa(sem_of)
    antes = carteira(sem_of)
    paga = B.mark_bill_paid(sem_of, conta["id"], None, metodo="carteira")
    assert paga["status"] == "paid" and paga["launch_id"] is not None
    assert manuais(sem_of) == 1 and carteira(sem_of) == pytest.approx(antes - 120)


def test_sem_metodo_e_type_error(sem_of):
    conta = conta_fixa(sem_of)
    with pytest.raises(TypeError):
        B.mark_bill_paid(sem_of, conta["id"], None)  # noqa — é o que se mede
    assert B.get_bill(sem_of, conta["id"])["status"] == "pending"


def test_metodo_invalido_nao_reserva(sem_of):
    conta = conta_fixa(sem_of)
    with pytest.raises(ValueError, match="METODO_INVALIDO"):
        B.mark_bill_paid(sem_of, conta["id"], None, metodo="pix")
    assert B.get_bill(sem_of, conta["id"])["status"] == "pending"


def test_banco_e_carteira_em_paralelo_so_um_vence(sem_of):
    conta = conta_fixa(sem_of)
    barreira = threading.Barrier(2)
    res: dict[str, object] = {}

    def _paga(metodo):
        barreira.wait()
        res[metodo] = B.mark_bill_paid(sem_of, conta["id"], None, metodo=metodo)

    ts = [threading.Thread(target=_paga, args=(m,)) for m in ("banco", "carteira")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    vencedores = [m for m, r in res.items() if r is not None]
    assert len(vencedores) == 1, res
    assert manuais(sem_of) == (1 if vencedores == ["carteira"] else 0)


def test_banco_conta_variavel_sem_valor_paga_com_valor_nulo(sem_of):
    conta = _monta_conta_variavel(sem_of)
    paga = B.mark_bill_paid(sem_of, conta["id"], None, metodo="banco")
    assert paga["status"] == "paid" and paga["paid_amount"] is None
    assert manuais(sem_of) == 0


# ── entradas, com banco conectado ────────────────────────────────────────────

def test_texto_conta_fixa_pergunta_forma_e_pix_paga_sem_lancamento(com_of, ia_fora):
    conta = conta_fixa(com_of)
    r = manda(com_of, "paguei a luz")
    assert "pelo banco" in r and "dinheiro vivo" in r, r
    assert B.get_bill(com_of, conta["id"])["status"] == "pending"
    r = manda(com_of, "pix")
    assert "pelo banco" in r, r
    assert B.get_bill(com_of, conta["id"])["status"] == "paid"
    assert manuais(com_of) == 0


def test_texto_conta_variavel_pergunta_forma_antes_do_valor(com_of, ia_fora):
    conta = _monta_conta_variavel(com_of)
    r = manda(com_of, "paguei a luz")
    assert "Quanto veio" not in r and "pelo banco" in r, r
    assert pendencia(com_of) == "payment_method_choice"
    manda(com_of, "pix")
    paga = B.get_bill(com_of, conta["id"])
    assert paga["status"] == "paid" and paga["paid_amount"] is None
    assert manuais(com_of) == 0


def test_texto_conta_variavel_dinheiro_pede_o_valor_e_debita(com_of, ia_fora):
    _monta_conta_variavel(com_of)
    manda(com_of, "paguei a luz")
    r = manda(com_of, "dinheiro")
    assert "Quanto veio" in r, r
    assert pendencia(com_of) == "bill_amount_expected"
    manda(com_of, "132")
    assert manuais(com_of) == 1
    assert float(_q("select valor from launches where user_id=%s", (com_of,))["valor"]) == 132.0


def test_texto_paguei_a_luz_no_pix_paga_direto(com_of, ia_fora):
    conta = conta_fixa(com_of)
    manda(com_of, "paguei a luz no pix")
    assert B.get_bill(com_of, conta["id"])["status"] == "paid"
    assert manuais(com_of) == 0 and pendencia(com_of) is None


def test_botao_ja_paguei_pergunta_a_forma(monkeypatch, com_of, ia_fora):
    conta = conta_fixa(com_of)
    r = _toca_ja_paguei(monkeypatch, com_of, conta["id"])
    assert r and "pelo banco" in r[0], r
    assert pendencia(com_of) == "payment_method_choice"
    assert B.get_bill(com_of, conta["id"])["status"] == "pending"
    manda(com_of, "banco")
    assert B.get_bill(com_of, conta["id"])["status"] == "paid"
    assert manuais(com_of) == 0


def test_porta_4_de_antes_do_deploy_pergunta_a_forma_com_o_valor(monkeypatch, com_of, ia_fora):
    conta = _monta_conta_variavel(com_of)
    db.set_pending_action(com_of, "bill_pay_amount", {"bill_id": conta["id"], "name": "Luz"})
    r = _manda_texto_no_wa(monkeypatch, com_of, "132")
    assert r and "pelo banco" in r[0], r
    p = db.get_pending_action(com_of)
    assert p["action_type"] == "payment_method_choice" and p["payload"]["amount"] == 132.0
    manda(com_of, "dinheiro")
    assert manuais(com_of) == 1
    assert B.get_bill(com_of, conta["id"])["paid_amount"] == 132.0


@pytest.mark.parametrize("forma,pagou,lancamentos", [
    ("banco", True, 0), ("dinheiro", True, 1), (None, False, 0),
])
def test_tool_mark_bill_paid(com_of, forma, pagou, lancamentos):
    from core.services.ai_chat.tools.bills import _pay_bill_execute
    conta = conta_fixa(com_of)
    args = {"name": "luz"} | ({"forma_pagamento": forma} if forma else {})
    r = _pay_bill_execute(com_of, args)
    assert (B.get_bill(com_of, conta["id"])["status"] == "paid") is pagou, r
    assert manuais(com_of) == lancamentos
    if not forma:
        assert "Pergunte ao usuário" in r, r


def _cliente(uid):
    promote_to_pro(uid, "pro_max")
    client, headers = _dashboard_client(uid, f"q40-{uid}@t.com")
    promote_to_pro(uid, "pro_max")  # o _dashboard_client rebaixa para 'pro'
    return client, headers


@pytest.mark.parametrize("corpo,status,lancamentos", [
    ({"metodo": "banco"}, 200, 0), ({"metodo": "dinheiro"}, 200, 1), ({}, 400, 0),
])
def test_rota_http_com_of(com_of, corpo, status, lancamentos):
    conta = conta_fixa(com_of)
    client, headers = _cliente(com_of)
    r = client.post(f"/recurring-bills/{com_of}/{conta['id']}/pay", json=corpo, headers=headers)
    assert r.status_code == status, r.text
    assert manuais(com_of) == lancamentos
    assert (B.get_bill(com_of, conta["id"])["status"] == "paid") is (status == 200)


def test_get_contas_diz_se_exige_forma(com_of, sem_of):
    for uid, esperado in ((com_of, True), (sem_of, False)):
        client, headers = _cliente(uid)
        r = client.get(f"/recurring-bills/{uid}", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["exige_forma_pagamento"] is esperado


# ── sem banco conectado: tudo como antes ─────────────────────────────────────

def test_sem_of_texto_paga_na_carteira(sem_of, ia_fora):
    conta = conta_fixa(sem_of)
    r = manda(sem_of, "paguei a luz")
    assert "Conta paga" in r and "lançado" in r, r
    assert B.get_bill(sem_of, conta["id"])["launch_id"] is not None
    assert manuais(sem_of) == 1


def test_sem_of_botao_variavel_pede_valor_e_porta_4_paga(monkeypatch, sem_of):
    conta = _monta_conta_variavel(sem_of)
    r = _toca_ja_paguei(monkeypatch, sem_of, conta["id"])
    assert "Quanto veio" in r[0], r
    assert pendencia(sem_of) == "bill_pay_amount"
    _manda_texto_no_wa(monkeypatch, sem_of, "132")
    assert manuais(sem_of) == 1
    assert B.get_bill(sem_of, conta["id"])["paid_amount"] == 132.0


def test_sem_of_tool_sem_forma_paga_na_carteira(sem_of):
    from core.services.ai_chat.tools.bills import _pay_bill_execute
    conta = conta_fixa(sem_of)
    r = _pay_bill_execute(sem_of, {"name": "luz"})
    assert "Conta paga" in r, r
    assert manuais(sem_of) == 1 and B.get_bill(sem_of, conta["id"])["status"] == "paid"


def test_sem_of_rota_sem_metodo_paga_na_carteira(sem_of):
    conta = conta_fixa(sem_of)
    client, headers = _cliente(sem_of)
    r = client.post(f"/recurring-bills/{sem_of}/{conta['id']}/pay", json={"amount": 120},
                    headers=headers)
    assert r.status_code == 200, r.text
    assert manuais(sem_of) == 1
