"""
Corrida do `/ai/chat`: dois POSTs simultâneos do mesmo usuário confirmando a
MESMA pendência.

`POST /ai/chat` é `async def` com `asyncio.to_thread` — nada serializa duas
requisições do mesmo user (o rate limit limita taxa, não simultaneidade). Antes
do CAS, `runner._chat_inner` lia a pendência, EXECUTAVA e só então limpava:
as duas execuções revertiam os mesmos lançamentos e o saldo ia de 700,00 a
-700,00, com o bot dizendo "apaguei" nas duas.

A corrida aqui é DETERMINÍSTICA, não sorte de escalonamento: um `Barrier(2)`
embrulha `db.ai_get_pending_action` (só no teste — nenhuma linha de produção
ganha hook), garantindo que as duas threads leiam a MESMA linha antes de
qualquer uma consumir.

Controles positivos do grupo vivem em `tests/test_delete_all_launches.py`
(`test_delete_all_launches_confirmacao_sim_executa` / `..._nao_cancela`).
"""

import threading

import db
from db import add_launch_and_update_balance, get_balance
from core.services.ai_chat import chat


def _bal(uid: int) -> float:
    return round(float(get_balance(uid)), 2)


def _corrida(user_id, monkeypatch):
    """Semeia 700,00 + pending, dispara dois `chat(uid, "sim")` sincronizados.

    Devolve (respostas, execucoes) — `execucoes` conta chamadas reais ao
    `delete_all_launches_and_rollback`.
    """
    add_launch_and_update_balance(user_id, "receita", 1000, None, "seed")
    add_launch_and_update_balance(user_id, "despesa", 300, "aluguel", "paguei 300 aluguel")
    assert _bal(user_id) == 700.0

    db.ai_set_pending_action(
        user_id, "delete_all_launches", {}, "apagar TODOS os seus lançamentos"
    )

    barrier = threading.Barrier(2, timeout=10)
    real_get = db.ai_get_pending_action

    def get_sincronizado(uid):
        pending = real_get(uid)
        barrier.wait()  # ambas leram a mesma linha antes de qualquer consumo
        return pending

    monkeypatch.setattr(db, "ai_get_pending_action", get_sincronizado)

    real_delete = db.delete_all_launches_and_rollback
    lock = threading.Lock()
    execucoes = []

    def delete_espiao(uid):
        with lock:
            execucoes.append(uid)
        return real_delete(uid)

    monkeypatch.setattr(db, "delete_all_launches_and_rollback", delete_espiao)

    respostas: list[str] = []
    resp_lock = threading.Lock()

    def rodar():
        r = chat(user_id, "sim", monthly_limit=1000, platform="whatsapp")
        with resp_lock:
            respostas.append(r)

    threads = [threading.Thread(target=rodar) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads), "thread travou (pool de DB pequeno?)"

    monkeypatch.setattr(db, "ai_get_pending_action", real_get)
    return respostas, len(execucoes)


def test_dois_sim_simultaneos_executam_uma_vez(user_id: int, monkeypatch):
    respostas, execucoes = _corrida(user_id, monkeypatch)

    assert execucoes == 1, f"a tool rodou {execucoes}x — a confirmação foi executada em dobro"
    assert _bal(user_id) == 0.0, "saldo revertido duas vezes"
    assert db.count_launches(user_id) == 0
    assert db.ai_get_pending_action(user_id) is None

    apagou = [r for r in respostas if "apaguei" in r.lower()]
    ja_foi = [r for r in respostas if "já foi processada" in r.lower()]
    assert len(apagou) == 1, f"esperava 1 'apaguei', veio {respostas!r}"
    assert len(ja_foi) == 1, f"esperava 1 'já foi processada', veio {respostas!r}"


def test_controle_negativo_sem_cas_executa_duas_vezes(user_id: int, monkeypatch):
    """Desliga SÓ o CAS (o compare-and-swap sempre vence) e mede o bug de volta.

    Sem isso, o teste acima poderia estar verde por escalonamento e não pelo
    conserto.
    """
    monkeypatch.setattr(db, "ai_consume_pending_action", lambda uid, p: True)
    _, execucoes = _corrida(user_id, monkeypatch)
    assert execucoes == 2, "controle negativo não reproduziu a corrida — o teste não mede nada"
