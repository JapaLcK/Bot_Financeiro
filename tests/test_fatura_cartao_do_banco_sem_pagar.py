"""Cartão do banco (Open Finance) não tem pagamento manual de fatura (B2, 2b).

A recusa mora em `db.cards.pay_bill_amount` (`CartaoDoBanco`, com `for share` no cartão) e vale em todo
canal: WhatsApp, IA e /app. O cartão nasce pelo sync de verdade (fixture `rodar`); o par manual usa
`create_card`. A conversa vai pelo `handle_incoming` (`diga`).

CONTROLES NEGATIVOS (rodados na entrega, ver o relato):
  - checagem tirada de `pay_bill_amount` -> os dois TOCTOU, o WhatsApp, o "usar saldo", o /app e o `due <= 0`;
  - checagem tirada de `_pay_bill_validate` -> `test_ia_no_cartao_do_banco_recusa_sem_pendencia`;
  - `and not do_banco` tirado do atalho de `_handle_pay_bill_command` -> `test_whatsapp_pagar_por_mes_com_banco_e_manual_pergunta_e_nao_paga`;
  - `for share` trocado por select simples -> `test_lock_do_cartao_segura_a_adocao_ate_o_commit`.
"""
from __future__ import annotations

import threading
from datetime import date

import psycopg
import pytest

import db
import db.cards as cards
from core.services.ai_chat import runner
from core.services.ai_chat.tools import get_tool
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401 (fixtures)
from tests._of_cash_helpers import q
from tests._pendencia_credito_helpers import diga
from tests.test_ai_chat_fatura_do_periodo import fatura
from tests.test_manual_launches_carteira_piggy import _dashboard_client
from tests.test_of_cartao_sinal import CARTAO
from tests.test_of_fatura_quitada_pelo_banco import F0, P0, fat, rodar  # noqa: F401 (fixture)
from utils_date import billing_period_for_close_day, today_tz

NOME = "Nubank Mastercard"   # o nome do cartão que o sync do `rodar` cria/adota
FRASE = db.MSG_FATURA_DO_BANCO
FECHA = date.fromisoformat(CARTAO["creditData"]["balanceCloseDate"]).day   # o dia que o sync dá ao cartão


def sincroniza(uid, rodar):
    """Sync com a "bill-1" aberta no período ANTERIOR ao de hoje, a fatura que `fatura_a_pagar` acha.
    O período da fatura no Pig sai da data da compra e do dia de fechamento do cartão (fixo): com a
    compra a 35 dias de hoje ela caía dois períodos atrás do dia 21 ao 24/25, e o "pagar" respondia
    "nenhuma fatura aberta" antes de chegar à recusa."""
    inicio, _ = billing_period_for_close_day(today_tz(), FECHA)
    fecha = (today_tz() - inicio).days + 1          # o último dia do período anterior
    return rodar(uid, [P0, ("p1", 120, "bill-1", fecha + 5)], [F0, fat("bill-1", 120, fecha=fecha)])


def recusou(r):
    """A frase da recusa, e não embrulhada no "Erro ao pagar fatura: ..." genérico (que a contém)."""
    return FRASE in r and "Erro" not in r and "Não consegui" not in r


def pagamentos(uid):
    return q("select count(*) as n from launches where user_id=%s and categoria='pagamento_fatura'",
             (uid,), True)[0]["n"]


def pago(uid):
    return q("select coalesce(sum(paid_amount),0) as p from credit_bills where user_id=%s", (uid,), True)[0]["p"]


def banco(uid, rodar):
    """Cartão do banco pelo sync de verdade, com uma fatura aberta."""
    sincroniza(uid, rodar)
    cid = q("select id from credit_cards where user_id=%s and open_finance_account_id is not null",
            (uid,), True)[0]["id"]
    bill = q("select id from credit_bills where user_id=%s and card_id=%s order by period_start desc limit 1",
             (uid, cid), True)[0]["id"]
    return cid, bill


def manual_com_duas_faturas(uid):
    c = db.create_card(uid, NOME, 20, 28)
    c = c["id"] if isinstance(c, dict) else c
    return c, fatura(uid, c, -1, 200), fatura(uid, c, 0, 140)


# ── cartão do banco: a conversa ──────────────────────────────────────────────

def test_whatsapp_pagar_fatura_do_banco_recusa_e_nao_grava(uid_pro, rodar, ia_fora):
    banco(uid_pro, rodar)
    antes = (pago(uid_pro), db.get_balance(uid_pro))
    r = diga(uid_pro, "pagar fatura nubank mastercard")
    assert recusou(r), r
    assert (pagamentos(uid_pro), pago(uid_pro), db.get_balance(uid_pro)) == (0, antes[0], antes[1])


def test_whatsapp_usar_saldo_no_banco_recusa_e_nao_grava(uid_pro, rodar, ia_fora):
    banco(uid_pro, rodar)
    antes = (pago(uid_pro), db.get_balance(uid_pro))
    r = diga(uid_pro, "usar saldo para pagar nubank mastercard")
    assert recusou(r), r
    assert (pagamentos(uid_pro), pago(uid_pro), db.get_balance(uid_pro)) == (0, antes[0], antes[1])


def test_whatsapp_pagar_fatura_no_cartao_manual_continua_pagando(uid_pro, ia_fora):
    """Controle positivo: o conserto restringe, e o cartão manual segue pagando e diz a fatura."""
    c = db.create_card(uid_pro, "Nubank", 10, 17)
    db.add_launch_and_update_balance(uid_pro, "receita", 1000.0, None, "seed")
    fatura(uid_pro, c, 0, 140)
    r = diga(uid_pro, "pagar fatura nubank")
    assert "Pagamento registrado" in r and "Nubank —" in r, r
    assert pagamentos(uid_pro) == 1


MESES = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro"]


def manual_no_mes_do_banco(uid, bill):
    """Cartão manual com uma fatura aberta no MESMO período da fatura do banco; devolve o nome do mês."""
    ps, pe = (q("select period_start, period_end from credit_bills where id=%s", (bill,), True)[0][k]
              for k in ("period_start", "period_end"))
    c = db.create_card(uid, "Manual", 10, 17)
    q("insert into credit_bills (user_id, card_id, period_start, period_end, status, total, paid_amount)"
      " values (%s,%s,%s,%s,'open',140,0)", (uid, c, ps, pe))
    return MESES[pe.month - 1]


def test_whatsapp_pagar_por_mes_com_banco_e_manual_pergunta_e_nao_paga(uid_pro, rodar, ia_fora):
    """O filtro tira a fatura do banco e sobra 1 manual: antes pagava o Manual direto, sem perguntar."""
    _, bill = banco(uid_pro, rodar)
    db.add_launch_and_update_balance(uid_pro, "receita", 1000.0, None, "seed")
    mes = manual_no_mes_do_banco(uid_pro, bill)
    r = diga(uid_pro, f"pagar fatura {mes}")
    assert "Qual você quer pagar" in r and "Pagamento registrado" not in r, r
    assert pagamentos(uid_pro) == 0


def test_whatsapp_pagar_por_mes_so_manual_com_uma_fatura_paga_direto(uid_pro, ia_fora):
    """Controle positivo: sem cartão do banco descartado, a única fatura do mês é paga direto."""
    c = db.create_card(uid_pro, "Manual", 10, 17)
    db.add_launch_and_update_balance(uid_pro, "receita", 1000.0, None, "seed")
    fatura(uid_pro, c, 0, 140)
    mes = MESES[q("select period_end from credit_bills where user_id=%s", (uid_pro,), True)[0]["period_end"].month - 1]
    r = diga(uid_pro, f"pagar fatura {mes}")
    assert "Pagamento registrado" in r, r
    assert pagamentos(uid_pro) == 1


# ── TOCTOU: a adoção pelo banco entre a pergunta e o "sim" ───────────────────

def test_whatsapp_adocao_entre_a_pergunta_e_o_sim_recusa(uid_pro, rodar, ia_fora):
    manual_com_duas_faturas(uid_pro)
    assert "Qual você quer pagar" in diga(uid_pro, "pagar fatura nubank mastercard")
    sincroniza(uid_pro, rodar)          # o sync adota o cartão pelo nome
    assert q("select 1 from credit_cards where user_id=%s and open_finance_account_id is not null",
             (uid_pro,), True)
    r = diga(uid_pro, "1")
    assert recusou(r), r
    assert pagamentos(uid_pro) == 0


def test_ia_adocao_entre_a_pergunta_e_o_sim_recusa(uid_pro, rodar):
    manual_com_duas_faturas(uid_pro)
    runner._dispatch_tool(uid_pro, "pay_bill", {"card_name": NOME, "amount": 50})
    pend = db.ai_get_pending_action(uid_pro)
    assert pend and pend["tool_name"] == "pay_bill"
    sincroniza(uid_pro, rodar)
    r = get_tool("pay_bill").execute(uid_pro, pend["tool_args"])
    assert recusou(r), r
    assert pagamentos(uid_pro) == 0


def test_ia_no_cartao_do_banco_recusa_sem_pendencia(uid_pro, rodar):
    banco(uid_pro, rodar)
    hist, msg = runner._dispatch_tool(uid_pro, "pay_bill", {"card_name": NOME, "amount": 50})
    assert '"validation_failed"' in hist and FRASE in (msg or ""), (hist, msg)
    assert db.ai_get_pending_action(uid_pro) is None


# ── /app ─────────────────────────────────────────────────────────────────────

def test_app_rota_409_no_banco_e_200_no_manual(uid_pro, rodar):
    cid, bill = banco(uid_pro, rodar)
    client, headers = _dashboard_client(uid_pro, "banco@t.com")
    r = client.post(f"/bills/{uid_pro}/{bill}/pay", json={"amount": 10}, headers=headers)
    assert r.status_code == 409 and r.json()["detail"] == FRASE, r.text
    assert pagamentos(uid_pro) == 0
    # o frontend esconde o botão por este campo
    assert client.get(f"/bills/{uid_pro}/{bill}").json()["bill"]["aviso_pagamento"] == FRASE
    assert all(b["aviso_pagamento"] == FRASE for b in client.get(f"/bills/{uid_pro}").json()["bills"])

    db.add_launch_and_update_balance(uid_pro, "receita", 1000.0, None, "seed")
    m = db.create_card(uid_pro, "Manual", 10, 17)
    mb = fatura(uid_pro, m, 0, 140)
    r = client.post(f"/bills/{uid_pro}/{mb}/pay", json={"amount": 10}, headers=headers)
    assert r.status_code == 200, r.text
    assert client.get(f"/bills/{uid_pro}/{mb}").json()["bill"]["aviso_pagamento"] is None


# ── a recusa vem antes de qualquer escrita, inclusive o ramo `due <= 0` ──────

def test_recusa_antes_do_ramo_due_zero_que_escreve(uid_pro, rodar):
    cid, bill = banco(uid_pro, rodar)
    q("update credit_bills set paid_amount=total, status='open' where id=%s", (bill,))
    with pytest.raises(db.CartaoDoBanco):
        db.pay_bill_amount(uid_pro, cid, NOME, None, bill)
    assert q("select status from credit_bills where id=%s", (bill,), True)[0]["status"] == "open"


# ── isolamento ───────────────────────────────────────────────────────────────

def test_cartao_de_outro_usuario_devolve_none_e_nao_toca(uid_pro, rodar):
    cid, bill = banco(uid_pro, rodar)
    outro = uid_pro + 1
    db.ensure_user(outro)
    assert db.pay_bill_amount(outro, cid, NOME, 10, bill) is None
    assert pagamentos(uid_pro) == pagamentos(outro) == 0


# ── lock: o `for share` segura a adoção até o commit ─────────────────────────

def test_lock_do_cartao_segura_a_adocao_ate_o_commit(uid_pro, rodar, monkeypatch):
    sincroniza(uid_pro, rodar)           # cria a conta OF (a do banco é livre)
    conta = db.list_bank_accounts(uid_pro)[0]["id"]
    db.add_launch_and_update_balance(uid_pro, "receita", 1000.0, None, "seed")
    c = db.create_card(uid_pro, "Manual", 10, 17)
    bill = fatura(uid_pro, c, 0, 140)
    no_meio, solta = threading.Event(), threading.Event()
    original = cards.add_launch_and_update_balance

    def pausa(*a, **k):
        no_meio.set()
        assert solta.wait(10)
        return original(*a, **k)

    monkeypatch.setattr(cards, "add_launch_and_update_balance", pausa)
    out = []
    t = threading.Thread(target=lambda: out.append(db.pay_bill_amount(uid_pro, c, "Manual", 50, bill)))
    t.start()
    try:
        assert no_meio.wait(10)
        with pytest.raises(psycopg.errors.LockNotAvailable):
            with db.get_conn() as conn, conn.cursor() as cur:
                cur.execute("set local lock_timeout='400ms'")
                cur.execute("update credit_cards set open_finance_account_id=%s where id=%s", (conta, c))
    finally:
        solta.set()
        t.join(10)
    assert out and out[0]["paid"] == 50
