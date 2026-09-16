"""Teto da conexão DEDICADA do `pluggy_items_lock` (`db/open_finance_state.py`).

IRMÃO: `tests/test_of_items_lock_vaga.py`, que ficou com a vaga do
`_lock_slots()` e o desfecho dos dois `except`. A divisão é por ASSUNTO (§0.5):
aqui só o que o `connect` recebe; lá o que acontece quando algo morre. O arquivo
único bateu no teto de 350 linhas de `tests/test_max_lines_python.py`, e encolher
prosa seria pagar o portão com a explicação que justifica cada número.

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

CONTROLES NEGATIVOS DESTE ARQUIVO, rodados e com o vermelho NOMEADO:
  • tire o `options=`/`connect_timeout=` do `psycopg.connect` de
    `pluggy_items_lock` → `test_kwargs_do_teto_chegam_no_connect_real` e
    `test_teto_abaixo_de_1000ms_...`.
Os outros cinco vermelhos do conserto (os dois `except`, os dois `finally` e o
filtro do log) são do IRMÃO, e estão nomeados no cabeçalho dele. Cada injeção
deixa os demais testes verdes, que é o que separa "fechou este furo" de
"quebrou tudo".

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

from _of_items_lock_helpers import ITENS
from db.open_finance_state import _lock_wait_ms, pluggy_items_lock


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
    o mesmo prazo — e o `lock_timeout` do `set_config` também. Qual corta primeiro
    NÃO é indeterminado: o `statement_timeout` conta desde o início do STATEMENT e
    o `lock_timeout` só desde o início da ESPERA, então com valores iguais o
    primeiro vence sempre e a contenção sai `QueryCanceled` (medido, 60/60) — o
    `lock_timeout` daqui é inerte. Não muda o desfecho: os dois caem no mesmo
    `except` e dão o mesmo `got=False`."""
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


def test_teto_abaixo_de_1000ms_afrouxa_o_connect_e_isso_e_o_piso_do_libpq(monkeypatch):
    """O `max(1, ...//1000)` INVERTE o orçamento abaixo de 1000ms, e o único caso
    coberto até aqui era o default 15000, onde a conta fecha.

    Medido com `OF_SYNC_LOCK_WAIT_MS=500`: `statement_timeout` = 500ms, o semáforo
    espera 0,5s, e o `connect_timeout` vai a **1 segundo** — 2× o prazo declarado.
    Com 100ms seria 10×.

    ponytail: o teto é do libpq, não nosso — `connect_timeout` é em SEGUNDOS
    INTEIROS e trata 0 como "SEM LIMITE". Baixar o piso para 0 trocaria um estouro
    de 2× por uma PENDURA INFINITA, que é exatamente a issue #429. O overshoot é
    limitado e conhecido: no máximo 1s a mais, qualquer que seja a env. Se um dia
    importar, o degrau seguinte é cronometrar o connect na thread e abortar — bem
    mais caro que o 1s que ele compra.

    Este teste não afirma que está CERTO: ele PRENDE o número, para que quem baixar
    a env descubra aqui e não em produção."""
    monkeypatch.setenv("OF_SYNC_LOCK_WAIT_MS", "500")
    assert _lock_wait_ms() == 500
    vistos = _espia_connect(monkeypatch)
    with pluggy_items_lock(ITENS) as got:
        assert got is True
    assert vistos[0]["options"] == "-c statement_timeout=500ms"
    assert vistos[0]["connect_timeout"] == 1, (
        "o piso do libpq deixa de valer 1s — se isto mudou, o overshoot do connect "
        "abaixo de 1000ms mudou junto"
    )
