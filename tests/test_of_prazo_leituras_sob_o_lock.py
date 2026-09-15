"""As LEITURAS sob o `pluggy_item_lock` respeitam o prazo da etapa 4.

DUAS METADES, e o nome do arquivo antigo (`..._aquisicoes_de_pool`) só cobria a
primeira: a espera do POOL e a QUERY parada numa linha/tabela travada. O
docstring de `_CursorComTeto` (`db/open_finance.py`) é a fonte da divisão, e a 1ª
versão deste grupo media só a metade 1 — com a tabela travada por 6 s numa 2ª
sessão e prazo de 1000 ms, a adoção custava 6,02 s (6×) com o advisory lock
retido, e numa das duas execuções GRAVOU depois de o prazo ter vencido. Quem
mede a metade 2 é o teste 4.

Codex #349, P2. Toda aquisição de pool que roda DENTRO do `_grava_reconexao`
esperava o default do pool (`DB_CONNECT_TIMEOUT`, 30s) com o advisory lock do
item na mão e um cliente HTTP do outro lado — o mesmo defeito que o prazo único
veio consertar, um degrau adiante. Duas ficavam fora dele:

  • `get_connections_by_item_id` — a leitura incondicional que alimenta as duas
    revalidações de dono (o achado do Codex);
  • `item_registry_origins` — a revalidação da ADOÇÃO (o irmão da mesma classe,
    e o que o teste 2 aqui existe para prender).

As duas escritas de desfazimento (`unregister_item`) ficam fora DE PROPÓSITO:
apertar o prazo delas é apertar a operação cujo FRACASSO cria o estado terminal
(rastro com dono + zero conexões). São `delete` por chave primária.

MECANISMO dos testes 1-3, sem sleep e sem depender de temporização: as duas
funções fazem `from .connection import get_conn` em `db/open_finance_state.py`,
então UM `monkeypatch.setattr(db.open_finance_state, "get_conn", ...)` intercepta
as duas. A discriminação é por VALOR do kwarg `timeout` — sem o conserto ele é
`None` (default do pool); com ele é um float na faixa do que sobrou do prazo.
Aquisição SEM orçamento (o rastro pré-lock de `_adota_item_orfao`, os
`unregister_item`) passa direto, e é isso que faz o `None` virar vermelho.
O nome da função que adquire vem do frame do chamador: sem separar as duas
leituras, o teste da 1ª passava com a 1ª CRUA e só a 2ª orçada — medido, o
controle da instância saiu verde antes disso.

MECANISMO do teste 4 (a metade 2): tabela travada de verdade numa 2ª sessão, sem
mock nenhum. Ele não usa o `fake` porque o que se mede ali é o Postgres cortando
o statement, não o valor de um kwarg.

CONTROLES do grupo (medidos, não deduzidos):
  • negativo da INSTÂNCIA — tirar o `budget_ms=` de `get_connections_by_item_id`
    em `_salva_item_sob_lock` → vermelho no teste 1, o que PRENDE o achado, e
    também nos testes 2 e 3, que espiam a mesma leitura (medido: 3 vermelhos; a
    1ª versão deste texto dizia 1);
  • negativo da CLASSE — consertar só aquela e deixar `item_registry_origins`
    cru → vermelho nos testes 2 e 3, e é o teste 2 que justifica o escopo;
  • negativo da METADE 2 — tirar o `_CursorComTeto` de qualquer uma das duas
    leituras → vermelho no caso correspondente do teste 4 (a leitura espera a
    trava sair e devolve resultado em vez de `QueryCanceled`);
  • positivo — o teste 3 exige que a adoção SIGA acontecendo com o orçamento
    apertado, e que o valor que chega seja o que SOBROU (nem o piso de 1 ms, nem
    o prazo cheio reiniciado). Sem ele, uma conversão que fixasse `0.001`
    passaria nos dois primeiros e quebraria toda adoção em produção.

CLASSE CEGA, declarada: os testes 1-3 medem o VALOR que chega ao `get_conn` e o
DESFECHO da adoção, não a espera real do `psycopg_pool` — um `get_conn` que
aceitasse `timeout` e o ignorasse passaria neles. Quem cobre esse lado é o
`db/connection.py:get_conn`, que só repassa o kwarg para `pool.connection()`.
O teste 4 não tem essa cegueira (mede o corte real), e em troca não mede o
`timeout` do pool: as duas metades precisam dos dois mecanismos.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time

import psycopg
import pytest
from psycopg_pool import PoolTimeout

import db
import db.open_finance_state as state
import frontend.routes.open_finance as of_routes
from test_of_item_ownership import SEGREDO, _item_remoto, eventos  # noqa: F401
from test_of_webhook_adopt_guards import _limpa_item, _mock_item, _registry, webhook_pluggy  # noqa: F401

# Prazo queimado pelo `fake` a cada aquisição orçada. Ver `_intercepta`.
_QUEIMA_S = 0.05


def _intercepta(monkeypatch, alvo: str | None) -> list[tuple[str, float]]:
    """Espia as aquisições de pool COM orçamento, por LEITURA, e devolve a lista.

    `alvo` é o NOME da função cuja aquisição orçada leva `PoolTimeout`; `None`
    só espia e delega. O nome sai do frame do chamador porque é ele que separa
    as DUAS leituras — a instância que o Codex apontou da irmã dela. Sem essa
    separação, o teste da 1ª leitura passava com a 1ª crua e SÓ a 2ª orçada
    (medido: controle da instância verde), e o grupo não prendia o achado.

    Aquisição SEM orçamento (`timeout is None`) delega e NÃO entra na conta: são
    as de fora do prazo — o rastro pré-lock de `_adota_item_orfao` e os
    `unregister_item` do desfazimento —, e o conserto não as toca. É isso que
    faz um `budget_ms` esquecido virar vermelho: a leitura cai nesse ramo.

    `_QUEIMA_S` existe pelo teste 3, e o valor não é decorativo: sem queimar
    prazo ENTRE as duas leituras, o orçamento da 1ª (999 ms) fica a 1 ms do teto
    da tentativa (1000 ms) e um `_folga_ms` que devolvesse `budget_ms` cru — o
    "cada etapa ganha o orçamento inteiro" que o Codex #166 P2 mandou consertar —
    passava VERDE em 3 de 3 execuções (medido). Com 50 ms queimados a margem sai
    de 1 ms para 50 ms e o mutante fica vermelho.
    """
    real = state.get_conn
    vistos: list[tuple[str, float]] = []

    def fake(timeout=None):
        quem = sys._getframe(1).f_code.co_name
        if timeout is None:
            return real()
        time.sleep(_QUEIMA_S)
        vistos.append((quem, timeout))
        if quem == alvo:
            raise PoolTimeout(f"couldn't get a connection after {timeout:.2f} sec")
        return real(timeout=timeout)

    monkeypatch.setattr(state, "get_conn", fake)
    return vistos


@pytest.fixture()
def prazo_s(monkeypatch):
    """`_RECONNECT_DEADLINE_MS = 2000`. Devolve o prazo INTEIRO em segundos.

    Teto por leitura é o prazo inteiro, não `prazo/tentativas`: o `restante_ms`
    de `_grava_reconexao` divide o que SOBROU pelas tentativas que ainda cabem,
    então a última recebe tudo (medido: 0,995 s na 1ª tentativa, 1,585 s na 2ª).
    Quem exige a divisão é o teste 3, que só tem UMA tentativa.
    """
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 2000)
    return 2.0


def test_leitura_sob_o_lock_estoura_com_pool_saturado_e_desfaz_a_reivindicacao(
        user_id, monkeypatch, eventos, webhook_pluggy, prazo_s):
    """Pool saturado na 1ª leitura orçada: 503 recuperável, e nada de rastro.

    Sem o conserto a leitura vai com `timeout=None`, o `fake` a DELEGA — a adoção
    acontece e as asserções ficam vermelhas. Com ele, o `PoolTimeout` sobe até o
    `except psycopg.OperationalError` de `_grava_reconexao` (é subclasse), as
    duas tentativas queimam, e o desfazimento tira a reivindicação porque
    nenhuma tentativa chegou à escrita.
    """
    item = "z-prazo-leitura"
    _mock_item(monkeypatch, user_id)
    vistos = _intercepta(monkeypatch, "get_connections_by_item_id")
    try:
        assert asyncio.run(of_routes._adota_item_orfao(item, "item/created")) is None
        assert db.get_connections_by_item_id(item) == []
        assert _registry(item) == [], _registry(item)
        # Uma por tentativa, e é ELA: a revalidação seguinte nem roda.
        assert [q for q, _ in vistos] == \
            ["get_connections_by_item_id"] * of_routes._RECONNECT_LOCK_ATTEMPTS, vistos
        assert all(0.001 < t <= prazo_s for _, t in vistos), vistos
    finally:
        _limpa_item(item)


def test_a_2a_leitura_da_adocao_tambem_respeita_o_orcamento(
        user_id, monkeypatch, eventos, webhook_pluggy, prazo_s):
    """O CONTROLE DA CLASSE: a revalidação da adoção é da mesma classe.

    O `fake` deixa passar a leitura de conexões e recusa SÓ a revalidação do
    rastro — recusa que só é possível se ela recebeu orçamento. Consertar apenas
    `get_connections_by_item_id` faz esta sair com `timeout=None`, o `fake` a
    delega, a adoção grava, e este teste fica vermelho (medido). É a linha que
    impede o escopo "corrigi o que o Codex apontou" de passar disfarçado de
    classe resolvida.
    """
    item = "z-prazo-registry"
    _mock_item(monkeypatch, user_id)
    vistos = _intercepta(monkeypatch, "item_registry_origins")
    try:
        assert asyncio.run(of_routes._adota_item_orfao(item, "item/created")) is None
        assert db.get_connections_by_item_id(item) == []
        assert _registry(item) == [], _registry(item)
        # 2 por tentativa: a leitura de conexões E a revalidação do rastro.
        assert [q for q, _ in vistos] == \
            ["get_connections_by_item_id", "item_registry_origins"] \
            * of_routes._RECONNECT_LOCK_ATTEMPTS, vistos
        assert all(0.001 < t <= prazo_s for _, t in vistos), vistos
    finally:
        _limpa_item(item)


def test_orcamento_da_leitura_e_o_que_SOBROU_e_a_adocao_segue(
        user_id, monkeypatch, eventos, webhook_pluggy, prazo_s):
    """CONTROLE POSITIVO — a mudança RESTRINGE, então o caminho legítimo precisa
    de prova própria.

    O `fake` só grava, QUEIMA `_QUEIMA_S` e delega. Três asserções separadas:
      • DESFECHO: a adoção devolve o dono e cria UMA conexão, com as duas
        leituras rodando sob teto;
      • PISO: cada teto é > 1 ms, ou seja não colapsou no `max(1, ...)`;
      • RELÓGIO ÚNICO: o teto da 2ª leitura é menor que o da 1ª por ao menos o
        que o `fake` queimou entre elas. É esta que discrimina — e a anterior
        ("< o orçamento da tentativa") NÃO discriminava: a margem era de 1 ms
        (999 contra 1000) e um `_folga_ms` que devolvesse `budget_ms` cru passava
        em 3 de 3 execuções. Medido com a queima: 0,995 → 0,943 no código real,
        0,999 → 0,999 no mutante. O limite superior fica como enquadramento —
        prova que o orçamento é o da TENTATIVA e não o prazo inteiro nem os 30 s
        do pool —, não como prova de encolhimento.
    """
    item = "z-prazo-positivo"
    _mock_item(monkeypatch, user_id)
    vistos = _intercepta(monkeypatch, None)
    try:
        assert asyncio.run(of_routes._adota_item_orfao(item, "item/created")) == user_id
        assert len(db.get_connections_by_item_id(item)) == 1
        assert [q for q, _ in vistos] == \
            ["get_connections_by_item_id", "item_registry_origins"], vistos
        teto = prazo_s / of_routes._RECONNECT_LOCK_ATTEMPTS   # 1ª tentativa
        for _, t in vistos:
            assert t is not None, vistos
            assert t > 0.001, f"colapsou no piso de 1 ms: {vistos}"
            assert t <= teto, f"não é o orçamento da tentativa: {vistos}"
        # O relógio é UM: o que o `fake` queimou entre as duas leituras sai do
        # teto da segunda. Margem de 45 ms contra os 50 ms queimados.
        assert vistos[1][1] <= vistos[0][1] - (_QUEIMA_S - 0.005), \
            f"o prazo reiniciou na 2ª leitura: {vistos}"
    finally:
        # `_limpa_item` apaga a conexão E o rastro; o `disconnect` vem antes por
        # ser o caminho de produção, e num `try` próprio para que um erro nele
        # não deixe o item vazando para os outros arquivos que usam esses ids.
        try:
            db.disconnect_open_finance_connection(user_id)
        finally:
            _limpa_item(item)


@pytest.mark.parametrize("le, tabela", [
    (lambda: state.get_connections_by_item_id("z-travado", budget_ms=300),
     "open_finance_connections"),
    (lambda: state.item_registry_origins("z-travado", budget_ms=300),
     "open_finance_item_registry"),
], ids=["conexoes", "registry"])
def test_leitura_orcada_desiste_da_tabela_travada_em_vez_de_esperar(le, tabela):
    """A METADE 2, medida sem mock: a QUERY travada também tem teto.

    A conexão vem do POOL, que não tem `statement_timeout` — o `set_config` do
    `pluggy_item_lock` é da conexão DEDICADA, outra sessão. Com só a metade 1
    (`get_conn(timeout=...)`) a leitura adquiria a conexão em 0,00 s e ficava
    parada na query: 6,02 s contra prazo de 1000 ms, o advisory lock do item
    retido em todo o excesso, e a adoção gravando depois de o cliente ter
    desistido (o Codex #166 P2 de novo, por outra porta). A porta em produção
    NÃO é hipotética: `db/schema.py` roda `alter table
    open_finance_connections add column if not exists ...` no `init_db`, que é
    ACCESS EXCLUSIVE, e I/O saturado é a outra.

    Os DOIS casos existem porque a metade 2 tem as mesmas duas leituras da
    metade 1: consertar uma e esquecer a irmã é o erro que este grupo inteiro já
    cometeu uma vez. NEGATIVO: tirar o `_CursorComTeto` da leitura do caso →
    ela espera a trava sair e DEVOLVE resultado, sem `QueryCanceled`.

    O `Timer` de 3 s solta a trava para que o negativo fique VERMELHO em vez de
    pendurar a suíte: sem teto, a espera pela trava é indefinida.
    """
    trava = psycopg.connect(os.environ["DATABASE_URL"])
    solta = threading.Timer(3.0, trava.rollback)
    solta.start()
    try:
        trava.execute(f"lock table {tabela} in access exclusive mode")
        t0 = time.monotonic()
        with pytest.raises(psycopg.errors.QueryCanceled):
            le()
        gasto = time.monotonic() - t0
        # 300 ms de orçamento; a folga é para o round-trip, não para esperar a
        # trava (que só sai em 3 s).
        assert gasto < 1.0, f"não cortou no orçamento: {gasto:.2f}s"
    finally:
        solta.cancel()
        trava.close()
