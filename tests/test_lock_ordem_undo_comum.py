"""O undo de lançamento comum toma `_lock_user` (ordem do mutex); o de pagamento de fatura NÃO.

Sem o mutex, `delete_launch_and_rollback` ia linha do lançamento → `update accounts`, a ordem
inversa do reset e do merge. O pagamento de fatura (`efeitos.bill_id`) mantém fatura → conta:
`pay_bill_amount` segura a fatura enquanto OUTRA conexão pede `accounts`, e o undo com mutex
viraria hang (o Postgres não vê o ciclo: uma ponta está em Python). Esses pares do cartão estão
aqui só para NÃO regredir. A pausa é por statement (tests/_pausa_sql.py).
"""
import re
from datetime import date

import pytest

import db
import db.privacy as privacy
from db.users import merge_users
from tests._pausa_sql import PausaSql, sem_deadlock


def _launch_com_saldo(uid):
    db.add_launch_and_update_balance(uid, "receita", 1000, "salario", "x")
    return db.add_launch_and_update_balance(uid, "despesa", 10, None, "x", "mercado")[0]


def _linha_travada(q):  # undo: o `for update` da linha do lançamento
    return "from launches" in q and "for update" in q


@pytest.mark.parametrize("undo_primeiro", [True, False], ids=["undo_primeiro", "reset_primeiro"])
def test_undo_comum_e_reset_nao_dao_deadlock(user_id, monkeypatch, undo_primeiro):
    lid = _launch_com_saldo(user_id)
    monkeypatch.setattr(privacy, "verify_user_password", lambda *a, **k: True)
    undo = lambda: db.delete_launch_and_rollback(user_id, lid)
    reset = lambda: privacy.reset_user_data(user_id, "x")
    if undo_primeiro:
        pausa = PausaSql(monkeypatch, "a", _linha_travada)
        r_undo, r_reset = pausa.roda(undo, reset)
    else:
        pausa = PausaSql(monkeypatch, "a", lambda q: "update accounts set balance = 0" in q)
        r_reset, r_undo = pausa.roda(reset, undo)
    sem_deadlock(r_undo, r_reset)
    assert pausa.casou and pausa.outro_travou, "a segunda operação devia esperar a primeira"
    assert not isinstance(r_reset, Exception), r_reset
    assert r_undo is None or isinstance(r_undo, LookupError), r_undo  # reset antes: a linha já não existe


@pytest.mark.parametrize("undo_primeiro", [True, False], ids=["undo_primeiro", "merge_primeiro"])
def test_undo_comum_e_merge_da_origem_nao_dao_deadlock(user_id, monkeypatch, undo_primeiro):
    destino = user_id + 1
    db.ensure_user(destino)
    lid = _launch_com_saldo(user_id)
    undo = lambda: db.delete_launch_and_rollback(user_id, lid)
    merge = lambda: merge_users(user_id, destino)
    if undo_primeiro:
        pausa = PausaSql(monkeypatch, "a", _linha_travada)
        r_undo, r_merge = pausa.roda(undo, merge)
    else:
        pausa = PausaSql(monkeypatch, "a", lambda q: "update launches set user_id" in q)
        r_merge, r_undo = pausa.roda(merge, undo)
    sem_deadlock(r_undo, r_merge)
    assert pausa.casou and pausa.outro_travou
    assert r_merge is None, r_merge
    assert r_undo is None or isinstance(r_undo, LookupError), r_undo  # merge antes: a linha mudou de dono


def _paga_fatura(uid):
    db.add_launch_and_update_balance(uid, "receita", 5000, "salario", "x")
    card = db.create_card(uid, "Nubank", closing_day=10, due_day=17)
    db.add_credit_purchase(uid, card, 100, "outros", "compra", date.today())
    return card, db.pay_bill_amount(uid, card, "Nubank", 30.0)["launch_id"]


def _bill_travada(q):
    return "from credit_bills" in q and "for update" in q


@pytest.mark.parametrize("comum_primeiro", [True, False], ids=["comum_primeiro", "fatura_primeiro"])
def test_undo_comum_e_undo_de_pagamento_de_fatura_nao_dao_deadlock(user_id, monkeypatch, comum_primeiro):
    _, pgto = _paga_fatura(user_id)
    comum = db.add_launch_and_update_balance(user_id, "despesa", 10, None, "x", "mercado")[0]
    undo_comum = lambda: db.delete_launch_and_rollback(user_id, comum)
    undo_fatura = lambda: db.delete_launch_and_rollback(user_id, pgto)
    if comum_primeiro:
        pausa = PausaSql(monkeypatch, "a", lambda q: "from accounts" in q and "for update" in q)  # já com o mutex
        r_comum, r_fatura = pausa.roda(undo_comum, undo_fatura, espera=30)
    else:
        pausa = PausaSql(monkeypatch, "a", lambda q: "update credit_bills" in q)  # já com a fatura
        r_fatura, r_comum = pausa.roda(undo_fatura, undo_comum, espera=30)
    sem_deadlock(r_comum, r_fatura)
    # Com a fatura primeiro o comum nem encontra conflito (o mutex ainda está livre): controle.
    assert pausa.casou and (pausa.outro_travou or not comum_primeiro)
    assert r_comum is None and r_fatura is None, (r_comum, r_fatura)


@pytest.mark.parametrize("pagar_primeiro", [True, False], ids=["pagar_primeiro", "undo_primeiro"])
def test_pagar_fatura_e_desfazer_pagamento_continuam_sem_hang(user_id, monkeypatch, pagar_primeiro):
    """NÃO PODE REGREDIR: o mutex no undo de fatura trocaria isto por espera sem fim."""
    card, pgto = _paga_fatura(user_id)
    paga = lambda: db.pay_bill_amount(user_id, card, "Nubank", 20.0)
    undo = lambda: db.delete_launch_and_rollback(user_id, pgto)
    if pagar_primeiro:
        pausa = PausaSql(monkeypatch, "a", _bill_travada)
        r_paga, r_undo = pausa.roda(paga, undo, espera=30)
    else:
        pausa = PausaSql(monkeypatch, "a", lambda q: "update credit_bills" in q)
        r_undo, r_paga = pausa.roda(undo, paga, espera=30)
    sem_deadlock(r_paga, r_undo)
    assert pausa.casou and pausa.outro_travou
    assert r_undo is None and isinstance(r_paga, dict) and "launch_id" in r_paga, (r_paga, r_undo)


@pytest.mark.parametrize("sim_primeiro", [True, False], ids=["sim_primeiro", "reset_primeiro"])
def test_conversa_apagar_o_lancamento_e_recomecar_do_zero_ao_mesmo_tempo(pro_small_uid, monkeypatch, sim_primeiro):
    """A causa original da #262, de ponta a ponta com banco real: "gastei", "apagar #N", "sim" pelo
    `handle_incoming` × `reset_user_data` em outra thread. O usuário recebe resposta e o reset
    conclui, sem DeadlockDetected, nas duas ordens. (Só o roteamento e a Meta não são reais.)"""
    from tests.test_pending_rollback import _diga

    uid = pro_small_uid
    db.add_launch_and_update_balance(uid, "receita", 1000, None, "seed")
    n = re.search(r"#(\d+)", _diga(uid, "gastei 50 no mercado"))
    assert n and "sim" in _diga(uid, f"apagar #{n.group(1)}").lower()
    monkeypatch.setattr(privacy, "verify_user_password", lambda *a, **k: True)
    sim = lambda: _diga(uid, "sim")
    reset = lambda: privacy.reset_user_data(uid, "x")
    if sim_primeiro:
        pausa = PausaSql(monkeypatch, "a", _linha_travada)
        r_sim, r_reset = pausa.roda(sim, reset)
    else:
        pausa = PausaSql(monkeypatch, "a", lambda q: "update accounts set balance = 0" in q)
        r_reset, r_sim = pausa.roda(reset, sim)
    sem_deadlock(r_sim, r_reset)
    assert pausa.casou and pausa.outro_travou
    assert isinstance(r_sim, str), r_sim  # o usuário recebeu resposta (o erro vira texto, não exceção)
    assert not isinstance(r_reset, Exception), r_reset
    # Reset antes: ele apagou a pendência (e o lançamento), então o "sim" cai em "não entendi" —
    # resposta de qualquer jeito, que é o que a #262 pede.
    assert r_sim.strip() and ("apagado e saldo revertido" in r_sim or not sim_primeiro), r_sim
    assert db.count_launches(uid) == 0


def test_conversa_apagar_o_lancamento_sem_concorrencia_continua_igual(pro_small_uid):
    """POSITIVO: o mutex não muda o caminho feliz."""
    from tests.test_pending_rollback import _diga

    uid = pro_small_uid
    db.add_launch_and_update_balance(uid, "receita", 1000, None, "seed")
    n = re.search(r"#(\d+)", _diga(uid, "gastei 50 no mercado"))
    assert db.get_balance(uid) == 950
    assert "sim" in _diga(uid, f"apagar #{n.group(1)}").lower()
    assert "apagado e saldo revertido" in _diga(uid, "sim")
    assert db.get_balance(uid) == 1000


def test_duas_reversoes_do_mesmo_pagamento_de_fatura_nao_devolvem_o_dobro(user_id, monkeypatch):
    """O pagamento de fatura NÃO toma o mutex: quem serializa duas reversões é o `for update` da
    linha do lançamento. Sem ele, as duas somam +30 (saldo +60). Os testes de
    tests/test_ai_chat_concurrent_confirm.py medem o mesmo para lançamento comum, mas lá o
    mutex já serializa antes e o `for update` deixou de ser exercitado."""
    _, pgto = _paga_fatura(user_id)
    antes = db.get_balance(user_id)
    undo = lambda: db.delete_launch_and_rollback(user_id, pgto)
    pausa = PausaSql(monkeypatch, "a", lambda q: "update credit_bills" in q)  # 1ª já com a fatura e a linha
    a, b = pausa.roda(undo, undo, espera=30)
    sem_deadlock(a, b)
    assert pausa.casou and pausa.outro_travou, "a 2ª reversão devia esperar a linha da 1ª"
    assert sorted(type(r).__name__ for r in (a, b)) == ["LookupError", "NoneType"], (a, b)
    assert db.get_balance(user_id) == antes + 30, "devolveu o dobro (ou nada)"


def test_pagamento_de_fatura_ligado_ao_banco_depois_do_preview_recusa_mudou_durante(user_id, monkeypatch):
    """O ramo `mudou_durante` só sobrou para o que NÃO toma o mutex: o pagamento de fatura. Uma
    ligação com o banco (`match_launch_id`) commitada entre o preview e o `for update` faz o undo
    recusar em vez de apagar sem o mutex que passou a ser preciso."""
    from tests._of_cash_helpers import conecta, dia, sync, tx
    from db.accounts import LaunchUnsafeRollback
    from db.bank_movements import _lock_user

    _, pgto = _paga_fatura(user_id)
    monkeypatch.setenv("OF_CASH_ENABLED", "0")
    sync(conecta(user_id, f"item-{user_id}"), user_id, [tx("t1", -200, dia(10))])

    def liga():
        with db.get_conn() as conn, conn.cursor() as cur:
            _lock_user(cur, user_id)
            cur.execute("update open_finance_transactions set match_launch_id=%s "
                        "where provider_transaction_id='t1'", (pgto,))
            conn.commit()
    pausa = PausaSql(monkeypatch, "a", lambda q: "update open_finance_transactions set match_launch_id" in q)
    r_liga, r_undo = pausa.roda(liga, lambda: db.delete_launch_and_rollback(user_id, pgto), espera=30)
    assert r_liga is None, r_liga
    assert isinstance(r_undo, LaunchUnsafeRollback) and r_undo.motivo == "mudou_durante", r_undo
