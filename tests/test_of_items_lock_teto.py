"""Teto da conexão DEDICADA do `pluggy_items_lock` (`db/open_finance_state.py`).

O RISCO TEM CLIENTE ESPERANDO, e é isso que separa este connect do irmão
singular sem `budget_ms` (que continua sem teto, por decisão declarada na
docstring dele: "o sync não tem cliente esperando"). Os dois chamadores daqui
são `db/privacy.py reset_user_data` e `frontend/routes/open_finance.py
_disconnect_sob_lock`, e este último roda por `asyncio.to_thread` DENTRO da rota,
sem try/except. Sem `connect_timeout`, um banco que aceita o socket e não
responde pendurava a thread para SEMPRE — e com ela uma vaga do `_lock_slots()`,
que nunca mais volta (issue #429).

NENHUM NÚMERO NOVO: `connect_timeout` e `statement_timeout` saem os dois de
`_lock_wait_ms()` (`OF_SYNC_LOCK_WAIT_MS`, default 15000), que já era a fonte de
verdade do `lock_timeout` desta função. Por isso os testes afirmam contra
`_lock_wait_ms()` e não contra literais: literal aqui viraria a segunda cópia do
número (CLAUDE.md §0.7).

MECANISMO: espiã no `psycopg.connect` REAL, interceptando a chamada do call site
— não lendo o texto do arquivo. Um teste que fizesse `read_text()` + `in` ficaria
verde com o kwarg escrito e nunca executado.

CONTROLES NEGATIVOS, DOIS, porque são dois consertos independentes:
  • tire o `options=` (ou o `connect_timeout=`) do `psycopg.connect` de
    `pluggy_items_lock` → VERMELHO: `test_kwargs_do_teto_chegam_no_connect_real`;
  • ponha o `set_config` de volta FORA do `try` dos advisory locks (o desfecho da
    Q4) → VERMELHO: `test_set_config_cortado_devolve_got_False_e_nao_vaza_a_vaga`,
    com o `QueryCanceled` escapando em vez de virar `got=False`.
Cada injeção deixa os OUTROS testes verdes, que é o que separa "fechou este
furo" de "quebrou tudo".

CONTROLE POSITIVO: `test_locks_livres_continuam_sendo_adquiridos`. A mudança
RESTRINGE (passa a cancelar statement), então sem ele este arquivo passaria num
`pluggy_items_lock` que nunca adquire nada — e "reset/disconnect sempre 503" é
pior que o bug.

CLASSE CEGA, declarada e NÃO coberta aqui: servidor que aceita o socket e nunca
responde. É a mesma do furo (b) de `core/system_event_log.py:40-46` — quem
aplica o `statement_timeout` é o SERVIDOR, então um servidor que não processa não
tem quem cancele, e o `connect_timeout` já passou. Reproduzir exigiria um socket
falso falando o protocolo do Postgres; este ambiente não o tem.
"""
from __future__ import annotations

import psycopg
import pytest

from db.open_finance_state import _lock_wait_ms, pluggy_items_lock

# Ids de laboratório. Advisory lock é de SESSÃO e some no `conn.close()`: este
# arquivo não grava linha nenhuma, então não há limpeza a fazer (e nunca um
# `delete ... like`, CLAUDE.md §0).
ITENS = ["teste_teto_items_lock_a", "teste_teto_items_lock_b"]


@pytest.fixture(autouse=True)
def sem_env_de_espera(monkeypatch):
    """A env é o knob sob teste: quem quiser um valor, faz `setenv` explícito."""
    monkeypatch.delenv("OF_SYNC_LOCK_WAIT_MS", raising=False)


def _espia_connect(monkeypatch) -> list[dict]:
    vistos: list[dict] = []
    real = psycopg.connect

    def espia(url, **kw):
        vistos.append(kw)
        return real(url, **kw)

    monkeypatch.setattr(psycopg, "connect", espia)
    return vistos


def test_kwargs_do_teto_chegam_no_connect_real(monkeypatch):
    """Os dois tetos chegam ao `psycopg.connect` DO CALL SITE, e no valor que sai
    de `_lock_wait_ms()`.

    `connect_timeout` é em SEGUNDOS INTEIROS (o libpq trata 0 como "sem limite",
    daí o piso de 1 que o código aplica); `statement_timeout` é em ms. São os dois
    o mesmo prazo — e o `lock_timeout` do `set_config` também, o que torna
    indeterminado qual corta primeiro. Não importa: `LockNotAvailable` e
    `QueryCanceled` caem no mesmo `except` e produzem o mesmo `got=False`."""
    vistos = _espia_connect(monkeypatch)
    with pluggy_items_lock(ITENS) as got:
        assert got is True
    assert vistos, "o connect nem foi chamado — o teste não mediu nada"
    assert vistos[0].get("connect_timeout") == max(1, _lock_wait_ms() // 1000)
    assert vistos[0].get("options") == f"-c statement_timeout={_lock_wait_ms()}ms"


def test_locks_livres_continuam_sendo_adquiridos():
    """CONTROLE POSITIVO: com as N chaves livres, adquire TODAS e devolve `True`.

    Sem ele, um teto apertado demais (ou um `except` largo demais) faria o reset
    de conta e o disconnect responderem 503 para sempre, com o arquivo inteiro
    verde. A lista vazia é o outro caminho contratado ("nada a serializar")."""
    with pluggy_items_lock(ITENS) as got:
        assert got is True
    with pluggy_items_lock([]) as got:
        assert got is True


class _CortaOPrimeiroExecute:
    """Conexão REAL com o PRIMEIRO `execute` cortado por `QueryCanceled`.

    O corte é INJETADO porque o caminho real é uma CORRIDA, não um fato: com
    `OF_SYNC_LOCK_WAIT_MS=1` o `set_config` cabe folgado no 1ms na maioria das
    vezes — medido neste ambiente, 20 tentativas, 20 completaram e ZERO foram
    canceladas (uma medição isolada anterior deu `QueryCanceled` 57014, que é o
    que torna o caminho alcançável e o teste-por-relógio flaky). Teste que só
    falha às vezes é pior que teste nenhum, então o que se injeta é a EXCEÇÃO —
    real, do tipo real (`psycopg.errors.QueryCanceled`, sqlstate 57014) — e o que
    se mede é o RAMO: para onde o cancelamento do `set_config` vai.

    Só o primeiro: os `pg_advisory_lock` seguintes continuariam reais, e não
    chegam a ser chamados porque o `except` já desviou."""

    def __init__(self, conn):
        self._conn = conn
        self._primeiro = True

    def execute(self, *args, **kwargs):
        if self._primeiro:
            self._primeiro = False
            raise psycopg.errors.QueryCanceled(
                "canceling statement due to statement timeout")
        return self._conn.execute(*args, **kwargs)

    def close(self):
        self._conn.close()


def test_set_config_cortado_devolve_got_False_e_nao_vaza_a_vaga(monkeypatch):
    """Q4: o `set_config` é o PRIMEIRO statement desta conexão e agora roda sob o
    `statement_timeout` do `options` — logo ele PODE ser cancelado. Ele estava
    FORA do `except` dos advisory locks, então esse cancelamento subia como
    exceção: 500 na rota do disconnect (o `asyncio.to_thread` de
    `frontend/routes/open_finance.py:2075-2078` não tem try/except), enquanto a
    docstring da função promete 503 "tente de novo" — que é o que o resto dela
    entrega. Agora os dois statements dividem o mesmo `except` e o mesmo desfecho.

    Duas metades, e a segunda é o que um `try` mal posto quebraria sem ninguém
    ver: o `finally` tem de rodar mesmo assim, senão a vaga do `_lock_slots()`
    vaza e o teto de conexões dedicadas afrouxa para sempre (o mesmo estrago que
    o `release()` duplo do irmão singular já custou). A 2ª aquisição prova isso.

    Negativo: ponha o `set_config` de volta FORA do `try` — este teste fica
    vermelho com `QueryCanceled` escapando, em vez de `got=False`."""
    real = psycopg.connect
    monkeypatch.setattr(
        psycopg, "connect",
        lambda url, **kw: _CortaOPrimeiroExecute(real(url, **kw)))

    with pluggy_items_lock(ITENS) as got:
        assert got is False, (
            "o cancelamento do set_config não virou got=False — o chamador "
            "precisa do 503 'tente de novo', não de um 500"
        )

    monkeypatch.undo()
    with pluggy_items_lock(ITENS) as got:
        assert got is True, "a vaga do _lock_slots() não voltou: o finally não rodou"
