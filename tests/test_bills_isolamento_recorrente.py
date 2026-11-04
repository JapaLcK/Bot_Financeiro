"""#1316: contas a pagar amarram o usuário também no recorrente.

A FK `bill_instances.recurring_id` é de coluna única, então o banco aceita uma instância do usuário A
apontando para o recorrente do usuário B. Nenhuma leitura pode trazer o nome de B, e a marcação do
lembrete não pode tocar na conta de outro usuário.

Controle negativo (medido à mão no PR): tirar `and r.user_id = b.user_id` de `get_bill`,
`list_due_bill_reminders` ou do Carteiro deixa o caso correspondente vermelho; tirar `user_id=%s` do
update de `mark_bill_reminder_sent` deixa `test_marcacao_nao_toca_conta_alheia` vermelho.
Positivo: `test_instancia_sadia_continua_com_o_nome` (o join amarrado ainda traz o nome do dono).
"""
from __future__ import annotations

from datetime import date, timedelta

import db
from conftest import usuario_pagante
from core.services import piggy_agents
from db.bills import ensure_bill_instance, get_bill, list_due_bill_reminders, mark_bill_reminder_sent
from db.recurring import create_recurring_expense

HOJE = date(2026, 10, 28)


def _recorrente(uid, nome):
    return create_recurring_expense(uid, nome, 100, "casa", 10, "account",
                                    payment_mode="manual")["id"]


def _instancia(uid, recurring_id, due):
    ensure_bill_instance(recurring_id, uid, due, 100)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select id from bill_instances where user_id=%s and recurring_id=%s and due_date=%s",
                    (uid, recurring_id, due))
        return cur.fetchone()["id"]


def _nome_carteiro(uid):
    vistos = []
    orig = db.record_agent_event
    db.record_agent_event = lambda *a, **k: vistos.append(k["payload"]["nome"]) or True
    try:
        piggy_agents._carteiro_detect_for_user({"user_id": uid, "agent_id": 0}, HOJE)
    finally:
        db.record_agent_event = orig
    return vistos


def _corrompida():
    a, b = usuario_pagante(), usuario_pagante()
    rec_b = _recorrente(b, "SEGREDO_DE_B")
    bill = _instancia(a, rec_b, HOJE + timedelta(days=3))
    return a, b, bill


def test_get_bill_nao_traz_recorrente_alheio():
    a, _, bill = _corrompida()
    assert "SEGREDO_DE_B" not in str(get_bill(a, bill))


def test_lembrete_nao_traz_recorrente_alheio():
    a, _, _ = _corrompida()
    assert "SEGREDO_DE_B" not in str(list_due_bill_reminders(a, HOJE))


def test_carteiro_nao_traz_recorrente_alheio():
    a, _, _ = _corrompida()
    assert "SEGREDO_DE_B" not in _nome_carteiro(a)


def test_instancia_sadia_continua_com_o_nome():
    a = usuario_pagante()
    bill = _instancia(a, _recorrente(a, "Luz"), HOJE + timedelta(days=3))
    assert get_bill(a, bill)["name"] == "Luz"
    assert "Luz" in _nome_carteiro(a)


def test_marcacao_nao_toca_conta_alheia():
    a, b = usuario_pagante(), usuario_pagante()
    bill_b = _instancia(b, _recorrente(b, "Agua"), HOJE + timedelta(days=3))
    mark_bill_reminder_sent(a, bill_b, HOJE)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select reminder_last_sent_on from bill_instances where id=%s", (bill_b,))
        assert cur.fetchone()["reminder_last_sent_on"] is None
    mark_bill_reminder_sent(b, bill_b, HOJE)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select reminder_last_sent_on from bill_instances where id=%s", (bill_b,))
        assert cur.fetchone()["reminder_last_sent_on"] == HOJE
