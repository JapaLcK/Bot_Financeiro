"""O espelho do logging (`_DashboardHandler`, `core/observability.py`) fora da thread de quem loga.

Quem loga paga o `prepare` e um `put_nowait`; uma thread do processo (o ouvinte) grava com o
mesmo `_DashboardHandler.emit`. Antes, todo `warning()`/`error()` pagava um connect + INSERT na
própria thread, e no processo web isso parava o event loop (medido em 2026-11-11 com a tabela
travada e teto de 2 s: 2006,6 ms; com a fila, 0,3 ms; o grupo 1 de `tests/test_log_fila_painel.py`
remede). Um processo, uma fila, um ouvinte (`instala`).

**Nada de `logging.<nível>()` nem `getLogger` aqui** (`tests/test_log_fila_painel.py`): o
desfecho de falha é `print` no stderr, como em `core/system_event_log.py`, senão a fila se
realimenta. A direção do import é única: `core.observability` importa daqui, não o contrário.

Na saída: o `atexit(para)` drena com prazo; no web quem drena é o fim do lifespan
(`esvazia_na_saida`), porque o uvicorn re-levanta o SIGTERM e o `atexit` não roda.

O que pode não chegar ao banco sem o chamador saber: fila cheia (descarta o novo, com `print`),
saída que estoura `_PRAZO_SAIDA_S` (com `print`) e processo morto por sinal fora do lifespan do
web (SIGKILL/OOM, ou SIGTERM num cron, que não tem handler; sem `print`). O stderr
(`StreamHandler` do root) não passa pela fila e continua levando tudo.
"""
from __future__ import annotations

import atexit
import copy
import logging.handlers
import queue
import sys
import threading
import time
import traceback

from core.pg_text import causa_segura
from core.system_event_log import _database_url

_MAX = 1000            # eventos esperando; acima disso descarta o novo
_PRAZO_SAIDA_S = 5.0   # drenagem na saída do processo
_local = threading.local()
_fila: FilaDoPainel | None = None
_ouvinte: _Ouvinte | None = None
_ligando = threading.Lock()
_parado = False


def _fora_do_ouvinte(record: logging.LogRecord) -> bool:
    """Descarta o record nascido na thread do ouvinte: o `warning` do `psycopg` ao gravar
    reabriria o ciclo (medido em 2026-11-11: 1 warning → 8 a 9 conexões em 2,5 s; grupo 3 do teste). O `_reentrou` de
    `core/system_event_log.py` é por thread e não vê a volta pela fila. O stderr ainda o leva."""
    return not getattr(_local, "ouvinte", False)


class FilaDoPainel(logging.handlers.QueueHandler):
    def __init__(self, fila: queue.Queue) -> None:
        super().__init__(fila)
        self.descartados = 0
        self._cheia = False
        self.addFilter(_fora_do_ouvinte)

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        """O da stdlib chama `self.format`, que põe a pilha na `message` (desfaz a #1331).
        Aqui a pilha vai para `exc_text`, com a MESMA fórmula do `_DashboardHandler.emit`, que a
        grava em `details.traceback`. Os tetos de tamanho continuam só no `emit`."""
        r = copy.copy(record)
        r.msg, r.args = record.getMessage(), None
        if record.exc_info:
            r.exc_text = "".join(traceback.format_exception(*record.exc_info))
        r.exc_info = r.stack_info = None
        return r

    def enqueue(self, record: logging.LogRecord) -> None:
        """Nunca bloqueia: cheia, descarta o novo e avisa uma vez por rajada. A rajada acaba quando
        um evento entra na fila VAZIA (o ouvinte alcançou); rearmar a cada vaga dava um aviso por
        evento num fluxo contínuo (`test_fluxo_continuo_com_a_fila_cheia_avisa_uma_vez`). O total
        sai no aviso de saída do processo (`_avisa_na_saida`)."""
        if not _database_url():  # o `emit` não gravaria nada (`log_system_event_sync` volta cedo)
            return
        if self is _fila:  # só a fila do processo liga o ouvinte do processo (os testes trazem o seu)
            _liga_ouvinte()
        try:
            self.queue.put_nowait(record)
            if self.queue.qsize() <= 1:
                self._cheia = False
        except queue.Full:
            self.descartados += 1
            if not self._cheia:
                self._cheia = True
                print(f"[observability] fila do painel cheia: evento descartado "
                      f"({self.descartados} no total)", file=sys.stderr)


class _Ouvinte(logging.handlers.QueueListener):
    def handle(self, record: logging.LogRecord) -> None:
        """Exceção no `emit` mataria a thread em silêncio, e a fila encheria.

        `emit` direto, sem o `Handler.handle` e o lock dele: só esta thread usa o espelho, e COM o
        lock, preso por um INSERT travado, o `logging.shutdown` (que roda depois do `para`) esperava
        a fila inteira, um teto por evento, e a saída estourava o prazo
        (`test_saida_com_a_tabela_travada_respeita_o_prazo_e_avisa` fica vermelho)."""
        _local.ouvinte = True
        for h in self.handlers:
            try:
                if record.levelno >= h.level and h.filter(record):
                    h.emit(record)
            except Exception as exc:  # noqa: BLE001
                try:
                    print("[observability] espelho falhou: " + causa_segura(exc), file=sys.stderr)
                except Exception:  # noqa: BLE001 - stderr fechado não pode matar o ouvinte
                    pass


def _liga_ouvinte() -> None:
    """A thread nasce no 1º evento, não no import: o harness hermético (`harness_support/
    safety_guards.py`, sem `DATABASE_URL`) proíbe criar thread, e processo que nunca loga não paga
    uma. Depois do `para` não religa (no fim do interpretador nem dá)."""
    if _ouvinte._thread is None and not _parado:
        with _ligando:
            if _ouvinte._thread is None and not _parado:
                try:
                    _ouvinte.start()
                except BaseException:
                    _ouvinte._thread = None  # o `start` da stdlib guarda a thread antes de ligá-la
                    raise


def instala(espelho: logging.Handler) -> FilaDoPainel:
    """A fila do processo, criada na 1ª chamada; as seguintes devolvem a mesma (um ouvinte só)."""
    global _fila, _ouvinte
    if _fila is None:
        _fila = FilaDoPainel(queue.Queue(_MAX))
        _ouvinte = _Ouvinte(_fila.queue, espelho, respect_handler_level=True)
        atexit.register(para)
    return _fila


def esvazia(prazo_s: float) -> bool:
    """Espera tudo o que entrou ser gravado. `False` no prazo; nunca pendura (o `join` pendura)."""
    if _fila is None:
        return True
    q = _fila.queue
    with q.all_tasks_done:
        return q.all_tasks_done.wait_for(lambda: not q.unfinished_tasks, prazo_s)


def _avisa_na_saida(nao_gravados: int) -> None:
    """Uma linha na saída, se algo se perdeu: o que ficou na fila e o total descartado por fila
    cheia desde o início do processo (durante o funcionamento sai só um aviso por rajada)."""
    descartados = _fila.descartados if _fila is not None else 0
    if nao_gravados or descartados:
        print(f"[observability] fila do painel: {nao_gravados} evento(s) não gravado(s) na saída; "
              f"{descartados} descartado(s) por fila cheia desde o início", file=sys.stderr)


def esvazia_na_saida() -> None:
    """Fim do lifespan do processo web. O uvicorn re-levanta o SIGTERM depois do shutdown
    (`uvicorn/server.py`, `signal.raise_signal` com o handler padrão): o processo morre pelo sinal
    e o `atexit(para)` não roda. Esvazia sem parar: a suíte entra e sai do lifespan várias vezes."""
    _avisa_na_saida(0 if esvazia(_PRAZO_SAIDA_S) else _fila.queue.unfinished_tasks)


def para(prazo_s: float | None = None) -> None:
    """Para o ouvinte drenando a fila, com prazo (o `stop` da stdlib não tem). Idempotente."""
    global _parado
    _parado = True
    prazo_s = _PRAZO_SAIDA_S if prazo_s is None else prazo_s
    t = _ouvinte._thread if _ouvinte is not None else None
    if t is None:
        return
    fim = time.monotonic() + prazo_s
    try:
        _fila.queue.put(_ouvinte._sentinel, timeout=prazo_s)
        sentinela = 1
    except queue.Full:
        sentinela = 0
    t.join(max(0.0, fim - time.monotonic()))
    if t.is_alive():
        _avisa_na_saida(_fila.queue.unfinished_tasks - sentinela)
    else:
        _ouvinte._thread = None
        _avisa_na_saida(0)
