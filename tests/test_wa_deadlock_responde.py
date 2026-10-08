"""#262: deadlock do banco no meio de uma mensagem do WhatsApp não vira silêncio.

O DeadlockDetected sobe de `db.update_last_activity`, chamado cedo em `handle_incoming`
(antes do roteamento, então a mensagem "apaga o último" só serve de entrada); quem responde é o
`except Exception` dele ("Ocorreu um erro interno") e, se ele deixar passar, o de
`process_message`. Os dois precisam sumir para o teste ficar vermelho.

É teste de CARACTERIZAÇÃO: passa também na `main`, porque esses `except` já existiam.
Ele prende o comportamento atual (a #262 estava obsoleta) contra quem o remover.
"""
import psycopg

import db
from adapters.whatsapp import wa_runtime
from tests.test_category_normalization import _msg, _wa


def test_o_usuario_recebe_resposta_quando_o_db_levanta_deadlock(pro_small_uid, monkeypatch):
    db.add_launch_and_update_balance(pro_small_uid, "despesa", 10, None, "x", "mercado")
    respostas = _wa(monkeypatch, pro_small_uid)

    def deadlock(*a, **k):
        raise psycopg.errors.DeadlockDetected("deadlock detected")
    monkeypatch.setattr(db, "update_last_activity", deadlock)

    wa_runtime.process_message(_msg("apaga o último", pro_small_uid))

    assert len(respostas) == 1 and "erro interno" in respostas[0], respostas
