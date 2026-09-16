"""A vaga do `_lock_slots()` e o DESFECHO dos dois `except` do
`pluggy_items_lock` (`db/open_finance_state.py`).

IRMÃO: `tests/test_of_items_lock_teto.py`, que ficou com o que o `psycopg.connect`
dedicado RECEBE (`connect_timeout`/`options`) e explica a divisão. Aqui fica o que
acontece quando algo MORRE: para onde vai o `got`, se a vaga volta, e se o
operador fica sabendo. Por isso quase todo teste daqui mede `_vagas()` além do
`got` — o porquê está em `test_close_que_estoura_no_finally_nao_vaza_a_vaga`.

A ASSIMETRIA DOS DOIS `except` é o que este arquivo prende: o de FORA (`connect`)
loga TUDO, o de DENTRO só o NÃO ROTINEIRO. A razão de cada lado está no comentário
do próprio `except`, em `db/open_finance_state.py` — aqui não se repete (§0.7).

CONTROLES NEGATIVOS DESTE ARQUIVO, rodados e com o vermelho NOMEADO:
  • ponha o `set_config` de volta FORA do `try` dos advisory locks (o desfecho da
    Q4) → TRÊS: `..._cortado_...` (com o `QueryCanceled` escapando em vez de virar
    `got=False`), `..._com_conexao_morta_...` e `..._nao_rotineiro_...`;
  • volte o `except` do `set_config` para a tupla dos três (`LockNotAvailable`,
    `QueryCanceled`, `DeadlockDetected`) → DOIS:
    `..._com_conexao_morta_...` e `..._nao_rotineiro_...` (o `AdminShutdown` e o
    `OperationalError` puro escapam);
  • volte o `except` do `connect` a propagar sem o 503, OU tire o
    `logger.warning(exc_info=True)` de dentro dele (503 MUDO: o usuário retenta
    para sempre e o operador não vê nada) →
    `test_connect_que_estoura_devolve_got_False_e_devolve_a_vaga`, 1ª e 3ª
    afirmações;
  • tire o `logger.warning` do não rotineiro (ele mora no `finally`, DEPOIS do
    `release()` — ver o comentário dele), OU tire o `if not isinstance` (aí todo
    `OperationalError` vira rastro) → duas injeções SEPARADAS, o mesmo vermelho
    `test_lock_nao_rotineiro_deixa_rastro_e_o_rotineiro_fica_mudo`, em metades
    DIFERENTES: a 1ª (0 registros WARNING+, contra 1) e a 2ª (1 registro, contra
    0). A 3ª discrimina o `isinstance` sozinha também, mas só se for RODADA
    isolada: o pytest aborta o teste na 2ª, que falha antes;
  • volte QUALQUER um dos dois `finally` para `conn.close(); release()` em
    sequência → `test_close_que_estoura_no_finally_nao_vaza_a_vaga` (ele cobre o
    plural E o singular, porque é a mesma classe nos dois).
O vermelho do `options=`/`connect_timeout=` é do IRMÃO. Cada injeção deixa os
demais testes verdes, que é o que separa "fechou este furo" de "quebrou tudo".

CONTROLE POSITIVO do log filtrado: as 2ª e 3ª metades de
`test_lock_nao_rotineiro_deixa_rastro_e_o_rotineiro_fica_mudo` — se elas não
exigissem ZERO registros, o arquivo passaria num código que loga TODO
`OperationalError`, e aí o filtro não estaria filtrando. O controle positivo de
"ainda adquire lock" é do irmão (`test_locks_livres_continuam_sendo_adquiridos`),
junto com a CLASSE CEGA (servidor que aceita o socket e nunca responde).
"""
from __future__ import annotations

import logging
import os
import traceback

import psycopg
import pytest

from _of_items_lock_helpers import ITENS
from db.open_finance_state import (_lock_key, _lock_slots, pluggy_item_lock,
                                   pluggy_items_lock)


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


def _vagas() -> int:
    """Vagas LIVRES do semáforo. Uma 2ª aquisição não serve para medir vazamento:
    o teto é 8, então perder UMA vaga deixa as sete seguintes verdes e o estrago só
    aparece na oitava. O contador é o único jeito de ver a perda na hora."""
    return _lock_slots()._value


def test_set_config_cortado_devolve_got_False_e_nao_vaza_a_vaga(monkeypatch):
    """Q4: o `set_config` é o PRIMEIRO statement desta conexão e agora roda sob o
    `statement_timeout` do `options` — logo ele PODE ser cancelado. Ele estava
    FORA do `except` dos advisory locks, então esse cancelamento subia como
    exceção: 500 na rota do disconnect (o `await asyncio.to_thread(
    _disconnect_sob_lock, ...)` de `open_finance_disconnect_route`, em
    `frontend/routes/open_finance.py`, não tem try/except — nome de construção e
    não número de linha, porque a versão anterior desta citação apontava para o
    `raise HTTPException(503)` de DENTRO do `_disconnect_sob_lock`, que é
    justamente o contrário do que a frase ilustra), enquanto a docstring da
    função promete 503 "tente de novo" — que é o que o resto dela entrega. Agora os dois statements dividem o mesmo `except` e o mesmo desfecho.

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


def test_lock_nao_rotineiro_deixa_rastro_e_o_rotineiro_fica_mudo(monkeypatch, caplog):
    """O `except` de DENTRO deixa de ser mudo — mas SÓ para o não rotineiro.

    1ª metade, NÃO ROTINEIRO, mecanismo REAL: `pg_terminate_backend` na conexão
    dedicada entre o `connect` e o `set_config`. Medido: o statement seguinte
    levanta `AdminShutdown` (57P01), subclasse de `OperationalError` e NENHUMA das
    três rotineiras, e `close()` na conexão terminada não levanta — o `finally`
    fecha limpo. É a classe de `DiskFull`/`TooManyConnections`/`ConnectionFailure`.

    2ª, ROTINEIRO pelo nome EXATO que o `isinstance` cita: `LockNotAvailable`
    injetado — o caminho real não produz esta classe de forma determinística.

    3ª, ROTINEIRO pelo MECANISMO real: outra sessão segurando a mesma chave.
    Medido com `OF_SYNC_LOCK_WAIT_MS=300`: sai `QueryCanceled` (57014), não
    `LockNotAvailable` — o `statement_timeout` conta desde o início do statement e
    o `lock_timeout` só desde o início da espera pelo lock — com os dois no mesmo
    valor, o primeiro corta SEMPRE antes (60/60, e não uma corrida). Os dois
    nomes são rotineiros de todo jeito, então o ZERO não depende disso.

    Negativo: tire o `logger.warning` → VERMELHO só na 1ª (0 registros WARNING+,
    contra 1). Tire o `if not isinstance` → VERMELHO na 2ª (e a 3ª discrimina
    sozinha, se isolada) — é o controle positivo do filtro. A 3ª não chega a
    rodar sob essa injeção porque o pytest aborta na 2ª; recortada num arquivo
    só, ela fica vermelha com `contenção REAL deixou rastro: 1`."""
    url = os.environ["DATABASE_URL"]
    real = psycopg.connect
    antes = _vagas()

    def mata_o_backend(u, **kw):
        conn = real(u, **kw)
        pid = conn.execute("select pg_backend_pid()").fetchone()[0]
        with real(u, autocommit=True) as carrasco:
            carrasco.execute("select pg_terminate_backend(%s)", (pid,))
        return conn

    monkeypatch.setattr(psycopg, "connect", mata_o_backend)
    with caplog.at_level(logging.WARNING, logger="db.open_finance_state"):
        with pluggy_items_lock(ITENS) as got:
            assert got is False, "AdminShutdown tem de virar 503, não 500"
    graves = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(graves) == 1, (
        f"o não rotineiro continua MUDO: {len(graves)} registros WARNING+")
    assert "AdminShutdown" in "".join(
        traceback.format_exception(*graves[0].exc_info)), "a causa não chegou ao log"
    assert _vagas() == antes, f"vaga vazou: {antes} -> {_vagas()}"

    caplog.clear()
    monkeypatch.setattr(
        psycopg, "connect",
        lambda u, **kw: _CortaOPrimeiroExecute(
            real(u, **kw),
            psycopg.errors.LockNotAvailable("canceling statement due to lock timeout")))
    with caplog.at_level(logging.WARNING, logger="db.open_finance_state"):
        with pluggy_items_lock(ITENS) as got:
            assert got is False
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == [], (
        "LockNotAvailable deixou rastro — o filtro não está filtrando, e cada sync "
        "contendido vira um WARNING")
    assert _vagas() == antes, f"vaga vazou: {antes} -> {_vagas()}"

    caplog.clear()
    monkeypatch.setattr(psycopg, "connect", real)
    monkeypatch.setenv("OF_SYNC_LOCK_WAIT_MS", "300")
    with real(url, autocommit=True) as outra:
        outra.execute("select pg_advisory_lock(hashtext(%s))", (_lock_key(ITENS[0]),))
        with caplog.at_level(logging.WARNING, logger="db.open_finance_state"):
            with pluggy_items_lock(ITENS) as got:
                assert got is False, "chave ocupada por outra sessão tem de virar 503"
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == [], (
        "contenção REAL deixou rastro — é o desfecho projetado desta função")
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
