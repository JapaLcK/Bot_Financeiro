"""QUANDO a vaga do `_lock_slots()` volta: ANTES do WARNING, nos dois `except`
do `pluggy_items_lock` (`db/open_finance_state.py`).

TERCEIRO arquivo do trio, e existe porque o assunto é OUTRO. `test_of_items_lock_teto.py`
mede o que o `psycopg.connect` dedicado RECEBE; `test_of_items_lock_vaga.py` mede o
DESFECHO dos dois `except` (para onde vai o `got`, SE a vaga volta, se o operador
fica sabendo). Nenhum dos dois olha para a ORDEM — e foi essa a cegueira: o commit
que moveu o `logger.warning` para depois do `_lock_slots().release()` nos dois
caminhos passou com 8 testes verdes e a suíte inteira idêntica (8153 passed nas
duas colunas), porque `caplog` registra CONTEÚDO e não instante. Teste que fica
verde com e sem o conserto não mede nada (CLAUDE.md §3).

O QUE ESTÁ EM JOGO: o WARNING passa pelo `_DashboardHandler`
(`core/observability.py`), que grava com `psycopg.connect` + INSERT — 2006,3 ms com
o host inalcançável (o `connect_timeout=2` de `core/system_event_log.py`), que é
exatamente a situação em que estes dois `except` disparam. Logar com a vaga na mão
somava esses 2 s a UMA das 8 vagas do `_lock_slots()`, num caminho que existe para
não pendurar ninguém.

POR QUE ESPIÃO E NÃO CRONÔMETRO: a alternativa era cronometrar entrada → devolução
da vaga com o canal de log morto (`10.255.255.1`, `connect_timeout=2`) e exigir
menos de ~500 ms — é a medição que provou o problema (connect 4016,2 → 2007,0 ms;
AdminShutdown 2023,6 → 13,0 ms), mas custa ~2 s por caso e depende de a rede se
comportar como o teto de um roteador. Os espiões dão o mesmo veredito em
milissegundos e sem rede.

CLASSE CEGA, e ela é real: os espiões prendem a ordem das DUAS CHAMADAS, não a
posse da vaga. Uma reordenação que mantenha `release()` antes do `logger.warning()`
e ainda assim segure o slot por outro caminho — um `acquire()` novo entre os dois,
um segundo semáforo, o log movido para DENTRO de um `with` que reserva vaga — passa
aqui. Quem pega essa classe é o cronômetro descrito acima, ou a contagem de
`_vagas()` que o arquivo da vaga já faz.

CONTROLE NEGATIVO, rodado: mova o `logger.warning` de volta para ANTES do
`_lock_slots().release()` em QUALQUER um dos dois `except` → VERMELHO aqui, com
`['log', 'release'] != ['release', 'log']`. Um `except` de cada vez já basta: são
duas afirmações separadas. Os outros 8 testes do trio seguem verdes sob as duas
injeções — que é o que separa "fechou este furo" de "quebrou tudo".

CONTROLE POSITIVO: as duas metades exigem que o log AINDA SAIA (`"log" in ordem`,
via a igualdade da lista). Sem isso, este arquivo ficaria verde num código que
simplesmente apagou os dois WARNING — que é o 503 mudo que `935b2a7` fechou, pior
que o bug de ordem.
"""
from __future__ import annotations

import psycopg

from _of_items_lock_helpers import ITENS
from db import open_finance_state as ofs


def _espia(ordem: list[str], nome: str, alvo):
    def espiao(*a, **kw):
        ordem.append(nome)
        return alvo(*a, **kw)
    return espiao


def test_a_vaga_volta_antes_do_log_nos_dois_except():
    """Os dois `except` do `pluggy_items_lock`, cada um com o seu mecanismo REAL.

    Sem `monkeypatch` para os dois espiões de propósito: o semáforo do
    `_lock_slots()` e o `logger` do módulo são singletons de PROCESSO (`lru_cache` e
    `getLogger`), e o `undo` do monkeypatch restaura escrevendo no `__dict__` da
    INSTÂNCIA — deixaria os dois carregando um atributo de instância pelo resto da
    sessão. `del` no `finally` devolve o estado exato. O `psycopg.connect` é
    atributo de módulo e não tem esse problema, mas fica no mesmo `try` para a
    ordem de desmonte ser uma só."""
    ordem: list[str] = []
    sem = ofs._lock_slots()
    real_connect, real_release, real_warning = (
        psycopg.connect, sem.release, ofs.logger.warning)

    sem.release = _espia(ordem, "release", real_release)
    ofs.logger.warning = _espia(ordem, "log", real_warning)
    try:
        # 1º `except`, o do `connect`: banco inalcançável / credencial inválida.
        # `OperationalError` é o que `role "ninguem" does not exist` levanta de
        # verdade (medido no arquivo da vaga) — aqui basta o TIPO, porque o ramo é
        # escolhido por ele.
        def morre(url, **kw):
            raise psycopg.OperationalError('FATAL:  role "ninguem" does not exist')

        psycopg.connect = morre
        with ofs.pluggy_items_lock(ITENS) as got:
            assert got is False, "connect morto tem de virar 503, não 500"
        assert ordem == ["release", "log"], (
            f"except do connect: {ordem} — o WARNING passa pelo _DashboardHandler "
            "(2006,3 ms com o host inalcançável) e não pode ser pago com a vaga do "
            "_lock_slots() na mão"
        )

        # 2º `except`, o de DENTRO, mecanismo REAL: `pg_terminate_backend` na
        # conexão dedicada entre o `connect` e o `set_config`. O statement seguinte
        # levanta `AdminShutdown` (57P01) — não rotineiro, logo COM log — e o
        # `close()` na conexão terminada não levanta, então o `finally` fecha limpo.
        # Injetar a exceção daria o mesmo ramo; o mecanismo real prova de quebra que
        # o `close()` do `finally` não entra na frente do `release()`.
        ordem.clear()

        def mata_o_backend(url, **kw):
            conn = real_connect(url, **kw)
            pid = conn.execute("select pg_backend_pid()").fetchone()[0]
            with real_connect(url, autocommit=True) as carrasco:
                carrasco.execute("select pg_terminate_backend(%s)", (pid,))
            return conn

        psycopg.connect = mata_o_backend
        with ofs.pluggy_items_lock(ITENS) as got:
            assert got is False, "AdminShutdown tem de virar 503, não 500"
        assert ordem == ["release", "log"], (
            f"except de dentro: {ordem} — o log do não rotineiro mora no `finally`, "
            "DEPOIS do release(), e é essa ordem que o `nao_rotineiro` existe para "
            "permitir"
        )
    finally:
        psycopg.connect = real_connect
        del sem.release, ofs.logger.warning
