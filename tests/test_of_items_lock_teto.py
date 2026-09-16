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

CONTROLES NEGATIVOS, UM POR CONSERTO, cada um rodado e com o vermelho NOMEADO:
  • tire o `options=`/`connect_timeout=` do `psycopg.connect` de
    `pluggy_items_lock` → `test_kwargs_do_teto_chegam_no_connect_real` e
    `test_teto_abaixo_de_1000ms_...`;
  • ponha o `set_config` de volta FORA do `try` dos advisory locks (o desfecho da
    Q4) → `test_set_config_cortado_devolve_got_False_e_nao_vaza_a_vaga`, com o
    `QueryCanceled` escapando em vez de virar `got=False`;
  • volte o `except` do `set_config` para a tupla dos três (`LockNotAvailable`,
    `QueryCanceled`, `DeadlockDetected`) →
    `test_set_config_com_conexao_morta_devolve_got_False`;
  • volte o `except` do `connect` a propagar sem o 503, OU tire o
    `logger.warning(exc_info=True)` de dentro dele (503 MUDO: o usuário retenta
    para sempre e o operador não vê nada) →
    `test_connect_que_estoura_devolve_got_False_e_devolve_a_vaga`, 1ª e 3ª
    afirmações;
  • volte QUALQUER um dos dois `finally` para `conn.close(); release()` em
    sequência → `test_close_que_estoura_no_finally_nao_vaza_a_vaga` (ele cobre o
    plural E o singular, porque é a mesma classe nos dois).
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

import logging
import traceback

import psycopg
import pytest

from db.open_finance_state import (_lock_slots, _lock_wait_ms, pluggy_item_lock,
                                   pluggy_items_lock)

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
    chegam a ser chamados porque o `except` já desviou.

    O `erro` é parâmetro porque o cancelamento NÃO é o modo de morte provável
    deste statement (ver `test_set_config_com_conexao_morta_devolve_got_False`);
    parametrizar é mais barato que uma segunda classe idêntica (CLAUDE.md §0.1)."""

    def __init__(self, conn, erro: Exception | None = None):
        self._conn = conn
        self._erro = erro or psycopg.errors.QueryCanceled(
            "canceling statement due to statement timeout")
        self._primeiro = True

    def execute(self, *args, **kwargs):
        if self._primeiro:
            self._primeiro = False
            raise self._erro
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


def _vagas() -> int:
    """Vagas LIVRES do semáforo. Uma 2ª aquisição não serve para medir vazamento:
    o teto é 8, então perder UMA vaga deixa as sete seguintes verdes e o estrago só
    aparece na oitava. O contador é o único jeito de ver a perda na hora."""
    return _lock_slots()._value


def test_connect_que_estoura_devolve_got_False_e_devolve_a_vaga(monkeypatch, caplog):
    """SIMETRIA com o `set_config`: o `connect_timeout` novo trocou "pendura para
    sempre" por exceção, mas a exceção subia crua numa rota SEM try/except — 500
    numa função cuja docstring promete 503. Fechar o 500 do `set_config` e abrir o
    do `connect` no mesmo commit seria só mudar o furo de lugar.

    `OperationalError` (banco inalcançável / timeout de connect) → `got=False` →
    503 "tente de novo". Defeito nosso continua subindo: a 2ª metade prova que um
    `ProgrammingError` NÃO virou 503 silencioso — sem ela este teste passaria num
    `except Exception` que engole config quebrada e responde 503 para sempre.

    O 503 NÃO PODE SER MUDO, e é a 3ª afirmação: usuário/base inexistente também
    levanta `OperationalError` (medido), é defeito PERMANENTE, e os dois
    chamadores o traduzem em "sincronização em andamento, tente de novo" — o
    usuário retenta para sempre e o operador não tem NADA. Antes de `935b2a7`
    isso era um 500 com traceback; o `logger.warning(exc_info=True)` devolve a
    causa sem devolver o 500. `exc_info` e não só o tipo porque o `sqlstate` de
    falha de connect é `None` nos três casos.

    Negativo: troque o `except psycopg.OperationalError` do connect de volta por
    `except Exception: raise` → VERMELHO na 1ª metade, com o `OperationalError`
    escapando do `with`. Tire o `logger.warning` → VERMELHO só na 3ª (medido: 0
    registros WARNING+, contra 1 com ele)."""
    antes = _vagas()

    def morre(url, **kw):
        raise psycopg.OperationalError('FATAL:  role "ninguem" does not exist')

    monkeypatch.setattr(psycopg, "connect", morre)
    with caplog.at_level(logging.WARNING, logger="db.open_finance_state"):
        with pluggy_items_lock(ITENS) as got:
            assert got is False, "connect morto tem de virar 503, não 500"
    assert _vagas() == antes, f"vaga vazou: {antes} -> {_vagas()}"

    graves = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(graves) == 1, f"503 mudo: {len(graves)} registros WARNING+"
    assert graves[0].exc_info, "sem exc_info o operador não distingue as causas"
    assert 'role "ninguem"' in "".join(
        traceback.format_exception(*graves[0].exc_info)), "a causa não chegou ao log"

    def defeito(url, **kw):
        raise psycopg.ProgrammingError("invalid dsn")

    monkeypatch.setattr(psycopg, "connect", defeito)
    with pytest.raises(psycopg.ProgrammingError):
        with pluggy_items_lock(ITENS):
            pass
    assert _vagas() == antes, f"vaga vazou no caminho de defeito: {antes} -> {_vagas()}"


def test_set_config_com_conexao_morta_devolve_got_False(monkeypatch):
    """O furo que a Q4 fechou pela METADE. O `except` capturava só
    `LockNotAvailable`/`QueryCanceled`/`DeadlockDetected` — e o cancelamento por
    `statement_timeout` NÃO REPRODUZ: 0 de 300 tentativas com
    `OF_SYNC_LOCK_WAIT_MS=1`. O modo de morte PROVÁVEL daquele statement é a
    conexão morrer (servidor fechou o socket, restart, pgbouncer), que levanta
    `OperationalError` puro — e esse continuava subindo como 500 numa rota cuja
    docstring promete 503.

    O `except` passou a ser `psycopg.OperationalError`, o PAI comum dos três
    (medido: `.__mro__[1]` é `OperationalError` nos três), então cobre
    ESTRITAMENTE MAIS que a tupla — nenhum caso anterior se perdeu, e o que entrou
    é justo o provável.

    Negativo: volte o `except` para a tupla dos três → VERMELHO aqui, com o
    `OperationalError` escapando; o teste do `QueryCanceled` acima segue verde, que
    é o que prova que este caso é OUTRO."""
    antes = _vagas()
    real = psycopg.connect
    monkeypatch.setattr(
        psycopg, "connect",
        lambda url, **kw: _CortaOPrimeiroExecute(
            real(url, **kw),
            psycopg.OperationalError("server closed the connection unexpectedly")))

    with pluggy_items_lock(ITENS) as got:
        assert got is False, "conexão morta no set_config tem de virar 503, não 500"
    assert _vagas() == antes, f"vaga vazou: {antes} -> {_vagas()}"


def test_close_que_estoura_no_finally_nao_vaza_a_vaga(monkeypatch):
    """A classe que a issue #429 NOMEIA ("a vaga não volta nunca"), uma linha
    abaixo do conserto dela: o `finally` era `conn.close(); _lock_slots().release()`
    em sequência, então `close()` que levanta pulava o `release()` — 8 vagas viravam
    7, PERMANENTE, e cada ocorrência apertava o teto de conexões dedicadas até o
    disconnect parar de funcionar.

    Pré-existente, não é regressão do PR — e o irmão `pluggy_item_lock` tinha o
    padrão idêntico, consertado junto.

    Negativo: volte o `finally` para as duas chamadas em sequência → VERMELHO aqui,
    com a contagem caindo de N para N-1."""
    antes = _vagas()
    real = psycopg.connect

    class _CloseQueEstoura:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, *a, **kw):
            return self._conn.execute(*a, **kw)

        def close(self):
            self._conn.close()          # fecha DE VERDADE: nada de backend vazado
            raise psycopg.OperationalError("connection already closed")

    monkeypatch.setattr(psycopg, "connect",
                        lambda url, **kw: _CloseQueEstoura(real(url, **kw)))
    with pytest.raises(psycopg.OperationalError):
        with pluggy_items_lock(ITENS) as got:
            assert got is True
    assert _vagas() == antes, (
        f"a vaga do _lock_slots() não voltou: {antes} -> {_vagas()} — é a classe "
        "que a issue #429 nomeia"
    )

    # O IRMÃO singular, no MESMO teste de propósito: é a mesma classe, e foi
    # exatamente "consertar a instância e não a classe" que deixou o padrão vivo
    # aqui (CLAUDE.md §2). Um conserto sem este caso ficava sem controle negativo
    # nenhum — medido: reverter só o `finally` de `pluggy_item_lock` não deixava
    # UM teste vermelho.
    # Sem `budget_ms`: é o caminho que NÃO ganhou teto (decisão declarada na
    # docstring da função), e o vazamento de vaga independe disso.
    antes = _vagas()
    with pytest.raises(psycopg.OperationalError):
        with pluggy_item_lock(ITENS[0]) as got:
            assert got is True
    assert _vagas() == antes, (
        f"pluggy_item_lock vazou a vaga: {antes} -> {_vagas()}"
    )


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
