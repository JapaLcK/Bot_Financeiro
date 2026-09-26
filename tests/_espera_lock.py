"""Espera de lock entre backends, para teste de concorrência determinístico."""
import time

from db import get_conn


def _esperar_backend_travado(timeout: float = 15.0) -> bool:
    """Espera algum backend DESTE database ficar parado esperando um lock.

    É o que torna teste de corrida determinístico em vez de dependente de sleep:
    sem isto, uma thread lenta a começar faria o commit do primeiro acontecer
    ANTES do SELECT do segundo, e o caso passaria verde sem medir nada.
    """
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        with get_conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "select count(*) as n from pg_stat_activity "
                    "where datname = current_database() and wait_event_type = 'Lock'"
                )
                if cur.fetchone()["n"] > 0:
                    return True
        time.sleep(0.05)
    return False
