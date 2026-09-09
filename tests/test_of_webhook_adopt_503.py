"""G5f — o MECANISMO do desfazimento da reivindicação no 503 de `_grava_reconexao`.

Separado de `test_of_webhook_adopt_race.py` por assunto (e porque aquele estava a
346 linhas com teto de 350, `tests/test_max_lines_python.py`): lá ficam as
INTERCALAÇÕES de duas entregas concorrentes; aqui fica a pergunta que o
desfazimento faz — "alguma tentativa do prazo pode ter ESCRITO?" — e as duas
metades da resposta.

  • PRAZO INTEIRO — a marca é LATCHED, e por isso não é `causa is None`, que é só
    a da ÚLTIMA tentativa (`test_causa_e_a_da_ultima_tentativa`).
  • PRECISÃO (Codex #313, P1) — quem responde é a marca feita na linha ANTES do
    `save_pluggy_open_finance_item`, não o TIPO da exceção. `OperationalError`
    sai igual do commit ambíguo e de tudo que roda ANTES da escrita: o
    `psycopg.connect` dedicado do `pluggy_item_lock` (medido: host inalcançável
    → `psycopg.OperationalError` subindo do `with`), o `set_config` inicial
    (medido: backend derrubado no meio → `AdminShutdown`, subclasse de
    `OperationalError`) e as leituras das duas revalidações. Preservar a
    reivindicação nessas reconstruía o estado TERMINAL que o P0 fechou: zero
    conexões + rastro com dono, a 1ª guarda de `_adota_item_orfao` recusando a
    retentativa e o `scripts/adotar_items_of_orfaos.py` sem enxergar a linha
    (o filtro dele exclui rastro com dono).

CONTROLES do grupo (medidos, não deduzidos):
  • negativo (a PRECISÃO) — voltar o latch para "todo `OperationalError` é
    escrita incerta" (marcar no `except` de `_grava_reconexao` em vez de ler a
    marca) → vermelho em `test_infra_ANTES_da_escrita_nao_deixa_reivindicacao`
    (o rastro sobra e a retentativa é recusada: 0 conexões) e em
    `[pre-pre]`/`[pre-escrita]`... — o `[pre-pre]` é a linha do Codex;
  • negativo (o LATCH) — trocar `not escrita_tentada` por `causa is None` →
    vermelho em `[escrita-ocupado]`, a única linha que separa os dois gates;
  • negativo (o desfazimento inteiro) — `if False and ...` na condição →
    vermelho em `[so-lock]` e `[pre-pre]`;
  • positivo — `test_infra_DENTRO_da_escrita_preserva_a_reivindicacao` roda o
    `_salva_item_sob_lock` REAL com o lock REAL e exige que a reivindicação FIQUE:
    um conserto que apagasse sempre (o over-correct óbvio) fica vermelho nele —
    medido: alargar o `except (PoolTimeout, PoolClosed)` para
    `psycopg.OperationalError` deixa SÓ ele vermelho;
  • negativo (o POOL) — tirar o `except (PoolTimeout, PoolClosed)` do
    `_salva_item_sob_lock` → vermelho em
    `test_pool_esgotado_no_get_conn_nao_deixa_reivindicacao`, que é o membro mais
    provável da classe pré-escrita: o `get_conn` do `save_...` é a primeira linha
    com I/O dele, e o piso de 1 ms do `resto` manda `get_conn(timeout=0.001)`
    quando a espera do lock comeu o prazo.

  • negativo (o `pop()`) — trocar `escrita_tentada.pop()` por `.clear()` →
    vermelho em `test_pool_na_2a_tentativa_nao_apaga_a_marca_AMBIGUA_da_1a`, o
    único caso em que as duas tentativas marcam e só uma desfaz.

A invariante que sustenta esse discriminador — `get_conn` ser a ÚNICA aquisição
de pool do `save_pluggy_open_finance_item` — tem guarda própria aqui
(`test_o_save_tem_uma_unica_aquisicao_de_pool`): um segundo `get_conn` DEPOIS do
upsert tornaria `PoolTimeout` pós-escrita, e aí desfazer viraria adoção dupla.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from contextlib import contextmanager

import psycopg
import pytest
from fastapi import HTTPException
from psycopg_pool import PoolTimeout

import db
import frontend.routes.open_finance as of_routes
from db.open_finance import save_pluggy_open_finance_item
from test_of_item_ownership import SEGREDO, _item_remoto, eventos  # noqa: F401
from test_of_webhook_adopt_guards import _limpa_item, _mock_item, _registry, webhook_pluggy  # noqa: F401


@pytest.mark.parametrize("sequencia, sobra", [
    (["ocupado", "ocupado"], False),   # nenhuma passou do `if not locked`: desfaz
    (["escrita", "ocupado"], True),    # a 1ª pode ter COMMITADO — separa do `causa is None`
    (["ocupado", "escrita"], True),    # idem, e aqui `causa is None` seguraria também
    (["pre", "pre"], False),           # Codex #313: infra ANTES da escrita, desfaz
    (["pre", "escrita"], True),        # a 2ª chegou a escrever: ambíguo, preserva
], ids=["so-lock", "escrita-ocupado", "ocupado-escrita", "pre-pre", "pre-escrita"])
def test_desfazimento_do_503_olha_o_prazo_INTEIRO(user_id, monkeypatch, eventos,
                                                  sequencia, sobra):
    """A tabela dos dois eixos: QUANDO no prazo, e ONDE na função.

    `escrita` é o `OperationalError` que sobe de DENTRO do
    `save_pluggy_open_finance_item` (o stub marca a lista, como o código real faz
    na linha anterior à chamada); `pre` é o MESMO tipo de erro subindo do que roda
    antes dela — lock, `set_config`, revalidações — e aí a escrita provadamente
    não aconteceu. A distinção não existia: os dois eram "infra" e os dois
    preservavam.
    """
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 3000)
    item, chamadas = "z-prazo-" + "-".join(sequencia), []

    def _script(*a, escrita_tentada=None):
        acao = sequencia[min(len(chamadas), len(sequencia) - 1)]
        chamadas.append(acao)
        if acao == "escrita":
            escrita_tentada.append(True)    # a execução CHEGOU ao `save_...`
        if acao in ("escrita", "pre"):
            raise psycopg.errors.TooManyConnections("sorry, too many clients already")
        return None, False              # lock ocupado: a escrita nem foi TENTADA

    monkeypatch.setattr(of_routes, "_salva_item_sob_lock", _script)
    registro = db.register_item(user_id, provider_item_id=item, origin="webhook_adopt")
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(of_routes._grava_reconexao(
                user_id, {"id": item}, item, criar_usuario=False,
                adocao_registro_id=registro))
        assert exc.value.status_code == 503
        assert chamadas == sequencia, chamadas
        assert bool(_registry(item)) is sobra, f"{sequencia}: {_registry(item)}"
    finally:
        _limpa_item(item)


def test_infra_ANTES_da_escrita_nao_deixa_reivindicacao(user_id, monkeypatch, eventos,
                                                        webhook_pluggy):
    """Codex #313, P1 — e aqui o `_salva_item_sob_lock` é o REAL.

    O `pluggy_item_lock` estoura na ENTRADA, que é o que o `psycopg.connect`
    dedicado dele faz quando o banco recusa conexão (medido: host inalcançável →
    `psycopg.OperationalError` subindo do `with`; backend derrubado no meio do
    `set_config` → `AdminShutdown`). A escrita não foi tentada, então a
    reivindicação que o `register_item` acabou de commitar TEM de sumir — senão a
    retentativa do `item/created` bate na 1ª guarda (rastro com dono) e o usuário
    fica sem banco e sem saída pelo produto.

    O 2º ato é o que mede isso: com a infra de volta, a MESMA entrega adota.
    """
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 1000)
    real_lock, quebrado = of_routes.pluggy_item_lock, [True]

    @contextmanager
    def _lock_sem_banco(item_id, **kw):
        if quebrado[0]:
            raise psycopg.errors.TooManyConnections("sorry, too many clients already")
        with real_lock(item_id, **kw) as got:
            yield got

    monkeypatch.setattr(of_routes, "pluggy_item_lock", _lock_sem_banco)
    try:
        assert asyncio.run(of_routes._adota_item_orfao("z-pre-escrita", "item/created")) is None
        assert db.get_connections_by_item_id("z-pre-escrita") == []
        assert _registry("z-pre-escrita") == [], _registry("z-pre-escrita")

        quebrado[0] = False             # a infra voltou: a retentativa da Pluggy
        assert asyncio.run(
            of_routes._adota_item_orfao("z-pre-escrita", "item/created")) == user_id
        assert len(db.get_connections_by_item_id("z-pre-escrita")) == 1, \
            "a retentativa foi recusada: 0 conexões, o usuário sem banco e sem saída"
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-pre-escrita")


def test_infra_DENTRO_da_escrita_preserva_a_reivindicacao(user_id, monkeypatch, eventos,
                                                          webhook_pluggy):
    """O outro lado, com o MESMO caminho real: só o `save_...` falha.

    O lock é o de verdade e a marca é a do código (linha anterior à chamada), não
    a de um stub. Desfecho DESCONHECIDO — o upsert pode ter commitado e a resposta
    ter se perdido —, e apagar a reivindicação aqui soltaria uma segunda adoção
    por cima de uma conexão que existe. CONTROLE POSITIVO do grupo: um conserto
    que apagasse sempre deixa este teste vermelho.
    """
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 1000)

    def _escrita_estoura(*a, **kw):
        raise psycopg.errors.TooManyConnections("sorry, too many clients already")

    monkeypatch.setattr(of_routes, "save_pluggy_open_finance_item", _escrita_estoura)
    try:
        assert asyncio.run(of_routes._adota_item_orfao("z-na-escrita", "item/created")) is None
        assert db.get_connections_by_item_id("z-na-escrita") == []
        assert [r["origin"] for r in _registry("z-na-escrita")] == ["webhook_adopt"], \
            "a reivindicação sumiu num desfecho AMBÍGUO"
    finally:
        _limpa_item("z-na-escrita")


def test_pool_esgotado_no_get_conn_nao_deixa_reivindicacao(user_id, monkeypatch, eventos,
                                                           webhook_pluggy):
    """O membro MAIS PROVÁVEL da classe pré-escrita — e o que o tipo do erro
    esconde melhor.

    `PoolTimeout` é subclasse de `psycopg.OperationalError` (medido:
    PoolTimeout → OperationalError → DatabaseError), então até aqui ele saía do
    `save_...` com a marca já feita e a reivindicação FICAVA — rastro
    `webhook_adopt` com dono e ZERO conexões, o estado terminal que o P0 fechou.
    E não precisa de infra doente: com o piso de 1 ms do `resto`, uma espera longa
    pelo lock manda `get_conn(timeout=0.001)` — medido em 2026-09-08 no Postgres
    local SAUDÁVEL: pool frio → `PoolTimeout` (1,4 ms numa medição, 4,2 ms em
    outra; o tempo varia com a carga, o desfecho não), pool quente → ok.

    O 2º ato mede o que importa: com o pool de volta, a MESMA entrega adota.
    """
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 1000)
    real_save, sem_pool = of_routes.save_pluggy_open_finance_item, [True]

    def _save(*a, **kw):
        if sem_pool[0]:
            raise PoolTimeout("couldn't get a connection after 0.00 sec")
        return real_save(*a, **kw)

    monkeypatch.setattr(of_routes, "save_pluggy_open_finance_item", _save)
    try:
        assert asyncio.run(of_routes._adota_item_orfao("z-pool", "item/created")) is None
        assert db.get_connections_by_item_id("z-pool") == []
        assert _registry("z-pool") == [], _registry("z-pool")

        sem_pool[0] = False             # o pool respirou: a retentativa da Pluggy
        assert asyncio.run(of_routes._adota_item_orfao("z-pool", "item/created")) == user_id
        assert len(db.get_connections_by_item_id("z-pool")) == 1, \
            "a retentativa foi recusada: 0 conexões, o usuário sem banco e sem saída"
    finally:
        db.disconnect_open_finance_connection(user_id)
        _limpa_item("z-pool")


def test_pool_na_2a_tentativa_nao_apaga_a_marca_AMBIGUA_da_1a(user_id, monkeypatch,
                                                              eventos, webhook_pluggy):
    """Por que o desfazimento é `pop()` e não `clear()`.

    A lista é do PRAZO INTEIRO e as duas tentativas escrevem nela. Aqui a 1ª
    chega ao `save_...` e morre de commit AMBÍGUO (`TooManyConnections`, que o
    `except (PoolTimeout, PoolClosed)` não pega, então a marca dela FICA); a 2ª
    chega e leva `PoolTimeout`, provadamente pré-escrita. `pop()` desfaz só a
    marca DELA e a reivindicação continua — `clear()` levaria a da 1ª junto e
    apagaria uma reivindicação de desfecho desconhecido, soltando a segunda
    adoção por cima de uma conexão que pode existir: a ressurreição que é o P1.

    O `_salva_item_sob_lock` é o REAL, com o lock REAL — um caso na tabela
    parametrizada NÃO serviria, porque lá o stub substitui a função inteira e o
    `pop()` de produção nunca roda. Medido: `pop()` → `clear()` deixava o grupo
    OF inteiro VERDE (315 passed), com o comportamento certo e sem controle.

    O `assert` das DUAS chamadas é o que impede o teste de passar à toa: com uma
    tentativa só, `pop()` e `clear()` dão o mesmo resultado.
    """
    _mock_item(monkeypatch, user_id)
    monkeypatch.setattr(of_routes, "_RECONNECT_DEADLINE_MS", 2000)
    chamadas = []

    def _save(*a, **kw):
        chamadas.append(1)
        if len(chamadas) == 1:
            raise psycopg.errors.TooManyConnections("commit de desfecho DESCONHECIDO")
        raise PoolTimeout("couldn't get a connection after 0.00 sec")

    monkeypatch.setattr(of_routes, "save_pluggy_open_finance_item", _save)
    try:
        assert asyncio.run(of_routes._adota_item_orfao("z-pop", "item/created")) is None
        assert len(chamadas) == 2, f"as duas tentativas têm de rodar: {chamadas}"
        assert db.get_connections_by_item_id("z-pop") == []
        assert [r["origin"] for r in _registry("z-pop")] == ["webhook_adopt"], \
            "a marca AMBÍGUA da 1ª tentativa foi apagada pelo desfazimento da 2ª"
    finally:
        _limpa_item("z-pop")


def test_o_save_tem_uma_unica_aquisicao_de_pool():
    """A invariante que dá validade ao `except (PoolTimeout, PoolClosed)`.

    Se um dia aparecer um SEGUNDO `get_conn` no `save_pluggy_open_finance_item`,
    `PoolTimeout` deixa de provar "pré-escrita" — poderia vir depois de um upsert
    commitado, e aí desfazer a reivindicação solta uma segunda adoção por cima de
    uma conexão que existe. Este teste é o que faz esse dia aparecer em vermelho
    aqui, e não em produção (§0.7: a regra mora em dois arquivos, um teste
    compara os dois).

    `unparse().split('.')` e não `n.func.id`: a forma `modulo.get_conn(...)` é um
    `ast.Attribute` e o `.id` não existe nela — a guarda passava verde num
    segundo `get_conn` escrito assim.

    CEGO a uma classe, de propósito: ele lê o corpo do `save_...`, então uma
    função CHAMADA por ele que adquira o pool por dentro passa. Hoje o corpo tem
    um `with get_conn(...)` só, e o `ensure_user_tx` recebe o cursor pronto — se
    um dia entrar chamada nova aqui, a pergunta "ela pega conexão?" é de quem
    escreve, não deste teste.
    """
    arvore = ast.parse(textwrap.dedent(inspect.getsource(save_pluggy_open_finance_item)))
    aquisicoes = [n for n in ast.walk(arvore)
                  if isinstance(n, ast.Call)
                  and ast.unparse(n.func).split(".")[-1] == "get_conn"]
    assert len(aquisicoes) == 1, f"{len(aquisicoes)} aquisições de pool no save_"
