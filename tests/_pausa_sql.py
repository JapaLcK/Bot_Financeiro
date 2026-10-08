"""Pausa uma thread depois de um statement SQL, para forçar a intercalação de duas transações.

Hook de `psycopg.Cursor.execute`: a thread do papel `papel` para logo DEPOIS do primeiro
statement que satisfaz `quando(sql_em_minusculas)` — ou seja, já com os locks que ele
tomou — e só segue quando a OUTRA operação estiver esperando um lock (ou terminar).
Sem `sleep` de sincronização. Com a ordem de lock certa a segunda trava na entrada e a
primeira segue sozinha; com a ordem invertida as duas chegam ao ponto e o ciclo fecha.

Não toca no `_Corrida` de tests/test_lock_ordem_caixinha.py (outro mecanismo: pausa por
função, não por statement). Sem prefixo `test_`: o pytest não coleta este arquivo.
"""
import threading
import time

import psycopg

from tests._espera_lock import _esperar_backend_travado


class PausaSql:
    def __init__(self, monkeypatch, papel: str, quando):
        self.papel, self.quando = papel, quando
        self.pausou = threading.Event()      # o primeiro chegou ao ponto (ou terminou sem chegar)
        self.casou = False                   # o `quando` casou de verdade
        self.outro_travou = False            # a outra operação ficou esperando lock
        self._fim_do_outro = threading.Event()
        self._tl = threading.local()
        original = psycopg.Cursor.execute

        def execute(cur, query, *a, **k):
            r = original(cur, query, *a, **k)
            if getattr(self._tl, "papel", None) == self.papel and not self.pausou.is_set() \
                    and self.quando(str(query).lower()):
                self.casou = True
                self.pausou.set()
                fim = time.monotonic() + 15
                while not self._fim_do_outro.is_set() and time.monotonic() < fim:
                    if _esperar_backend_travado(0.2):
                        self.outro_travou = True
                        break
            return r
        monkeypatch.setattr(psycopg.Cursor, "execute", execute)

    def roda(self, primeiro, segundo, espera=60):
        """`primeiro` roda na thread do papel e pausa; `segundo` roda inteiro enquanto isso.
        Devolve (resultado ou exceção do primeiro, idem do segundo)."""
        def captura(fn):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 — quem julga é o teste
                return exc

        def run_primeiro():
            self._tl.papel = self.papel
            try:
                return captura(primeiro)
            finally:
                self.pausou.set()

        def run_segundo():
            assert self.pausou.wait(20), "o primeiro nunca chegou ao ponto de pausa"
            try:
                return captura(segundo)
            finally:
                self._fim_do_outro.set()

        saidas = {}
        ts = [threading.Thread(target=lambda: saidas.__setitem__(0, run_primeiro())),
              threading.Thread(target=lambda: saidas.__setitem__(1, run_segundo()))]
        [t.start() for t in ts]
        [t.join(espera) for t in ts]
        if any(t.is_alive() for t in ts):
            # Hang que o Postgres não vê (uma ponta do ciclo está em Python): derruba os backends
            # presos para a falha sair AQUI, e não no teardown pendurado até o timeout do CI.
            from db import get_conn
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("select pg_terminate_backend(pid) from pg_stat_activity where "
                            "datname = current_database() and pid <> pg_backend_pid() and state <> 'idle'")
                conn.commit()
            [t.join(15) for t in ts]
            raise AssertionError("hang: uma das operações não terminou")
        return saidas[0], saidas[1]


def sem_deadlock(*resultados):
    assert not [r for r in resultados if isinstance(r, psycopg.errors.DeadlockDetected)], resultados

