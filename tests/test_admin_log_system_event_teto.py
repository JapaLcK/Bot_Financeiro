"""Teto de EXECUÇÃO do gravador ASYNC: `core/admin_dashboard.log_system_event`.

Irmão de `tests/test_system_event_log_teto.py`, que faz o mesmo para as duas
funções SÍNCRONAS de `core/system_event_log.py`. As fixtures, o `tabela_travada`
e o `_limpa` são compartilhados de propósito (`tests/_system_event_log_helpers.py`):
duas cópias do mesmo aparelho é como dois arquivos passam a medir coisas
diferentes achando que medem a mesma (CLAUDE.md §0.7).

O QUE MUDOU (issue #429): `log_system_event` abria por `core/admin_dashboard.py
db_connect`, que tem `connect_timeout` e NÃO tem `options`. O `connect_timeout`
limita só o handshake, então com `system_event_logs` em `access exclusive` o
INSERT esperava o LOCK inteiro — e esse gravador é chamado de DENTRO do event
loop (ex.: `frontend/routes/open_finance.py`, etapa 4 da reconexão). Agora ele
abre conexão PRÓPRIA com `options=statement_timeout_options()`, o mesmo helper
do gravador síncrono.

ESCOPO, e ele é decisão e não esquecimento: `db_connect` continua SEM teto. Ele é
COMPARTILHADO entre DDL de boot (`ensure_admin_tables`), agregações do overview,
retenção diária e rotas do painel, e um teto único ali cortaria DDL, agregação e
purga.
`test_db_connect_do_painel_continua_sem_teto` é quem prende isso — sem ele um PR
futuro põe teto no painel sem discussão.

CONTROLE NEGATIVO, injeção nomeada: tire o `options=statement_timeout_options()`
do `psycopg.AsyncConnection.connect` de `core/admin_dashboard.log_system_event`.
VERMELHOS, TRÊS e todos daqui — `test_insert_async_com_tabela_travada_desiste_dentro_do_teto`
(medido na injeção: pendurou 3,00s num lock de 3s, contra <0,3s com o conserto),
`test_options_chega_no_connect_async` e o portão
`test_connect_do_log_system_event_tem_timeout_e_teto`. Injetado onde DISCRIMINA:
os três estavam verdes com o conserto, e os demais deste arquivo continuam
verdes (é o que separa "fechou o furo" de "quebrou tudo").

CONTROLE POSITIVO: a mudança RESTRINGE (passa a cancelar query), então sem
`test_tabela_livre_continua_gravando` este arquivo passaria num código que
simplesmente não grava nada — pior que o bug.

PORTÃO ESTRUTURAL: `test_connect_do_log_system_event_tem_timeout_e_teto`, irmão
do `tests/test_log_falha_traceback.py::test_todo_connect_do_system_event_log_tem_timeout_e_teto`.
Ele mira a FUNÇÃO, não o arquivo: um portão sobre `core/admin_dashboard.py`
inteiro ficaria vermelho pelo `db_connect`, que está sem teto por decisão.

CONFUNDIDOR: o mesmo dos irmãos — com a tabela travada, qualquer
`logger.warning()` da suíte paga a espera pelo `_DashboardHandler` do root. Daí
o `sem_dashboard_handler` nos testes de cronômetro.
"""
from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path

import psycopg

import core.admin_dashboard as admin_dashboard
from core.admin_dashboard import log_system_event

from _system_event_log_helpers import (  # noqa: F401  (fixtures autouse)
    TETO_PADRAO_OPTIONS,
    _limpa,
    _linhas,
    kwargs_com_valor,
    sem_dashboard_handler,
    sem_env_de_teto,
    tabela_travada,
    tabelas_admin,
)

EVENTO_ASYNC_TRAVADO = "teste_teto_async_travado"
EVENTO_ASYNC_LIVRE = "teste_teto_async_livre"


def test_insert_async_com_tabela_travada_desiste_dentro_do_teto(
        monkeypatch, capsys, sem_dashboard_handler):
    """ANTES (pelo `db_connect`, sem `options`): ~3,0s e a linha ACABA GRAVADA
    quando o lock cai. Com o teto de 300ms: desiste em ~0,3s e não grava.

    Não se afirma por exceção: o `except Exception` de `log_system_event` engole
    o `QueryCanceled` de propósito — perder o log não pode virar um segundo modo
    de falha em cima do incidente. O que se mede é tempo de PAREDE, a ausência da
    linha e o rastro no stderr."""
    monkeypatch.setenv("SYSTEM_EVENT_LOG_TIMEOUT_MS", "300")
    try:
        with tabela_travada(3.0):
            t0 = time.perf_counter()
            asyncio.run(log_system_event("warning", EVENTO_ASYNC_TRAVADO,
                                         "mensagem", source="teste"))
            gasto = time.perf_counter() - t0

        assert gasto < 1.5, f"pendurou {gasto:.2f}s no lock — teto de 300ms não pegou"
        assert _linhas(EVENTO_ASYNC_TRAVADO) == 0, \
            "gravou depois do lock cair: a chamada esperou o lock inteiro"
        assert "failed to record system event" in capsys.readouterr().err
    finally:
        _limpa(EVENTO_ASYNC_TRAVADO)


def test_tabela_livre_continua_gravando(user_id):
    """CONTROLE POSITIVO obrigatório: a mudança RESTRINGE. Sem este caso, o
    arquivo inteiro passaria num `log_system_event` que não grava nada — e o
    `except` que engole tudo tornaria isso invisível."""
    try:
        asyncio.run(log_system_event("warning", EVENTO_ASYNC_LIVRE, "mensagem",
                                     source="teste", user_id=user_id))
        assert _linhas(EVENTO_ASYNC_LIVRE) == 1
    finally:
        _limpa(EVENTO_ASYNC_LIVRE)


def test_options_chega_no_connect_async(monkeypatch, user_id):
    """O `options` não basta existir no helper: tem de chegar no connect ASYNC.
    Espiã na `AsyncConnection.connect`, no molde de
    `tests/test_system_event_log_config.py::test_options_chega_no_connect`."""
    vistos: list[dict] = []
    real = psycopg.AsyncConnection.connect

    async def espia(url, **kw):
        vistos.append(kw)
        return await real(url, **kw)

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", espia)
    try:
        asyncio.run(log_system_event("warning", EVENTO_ASYNC_LIVRE, "mensagem",
                                     source="teste", user_id=user_id))
        assert vistos, "o connect nem foi chamado — o teste não mediu nada"
        assert vistos[0].get("options") == TETO_PADRAO_OPTIONS, vistos
        assert vistos[0].get("connect_timeout") == admin_dashboard.DB_CONNECT_TIMEOUT
    finally:
        _limpa(EVENTO_ASYNC_LIVRE)


def test_db_connect_do_painel_continua_sem_teto():
    """DECISÃO DE ESCOPO presa em teste, não em prosa. `db_connect` é
    COMPARTILHADO — DDL de boot (`ensure_admin_tables`), agregações do overview,
    retenção diária, rotas do painel —, e um `statement_timeout` único ali
    cortaria DDL, agregação e purga, que são exatamente as operações longas e
    legítimas. Quem precisa de teto pede o seu, como o `log_system_event` faz.

    SEM CONTAGEM, de propósito (CLAUDE.md §2). A versão anterior desta docstring
    dizia "11 chamadores" em quatro arquivos, e o comando abaixo devolvia 51 — o
    número não era reproduzível por nada e envelhecia em silêncio. O que sustenta
    a decisão é a MISTURA de usos, que é qualitativa; se você precisar do número,
    meça na hora, e só o `core/admin_dashboard.py` é o escopo certo:
        grep -c "await db_connect()" core/admin_dashboard.py
    O `await` faz parte do comando, não é enfeite: `grep -c "db_connect()"` conta
    também a própria `async def db_connect()` e a linha de comentário que a cita —
    2 a mais que os CHAMADORES, que é o que a frase diz medir (o mesmo defeito do
    "11", em escala menor). Ele tem teto declarado: enxerga só a chamada escrita
    em UMA linha e com este nome, então um `await  db_connect()` ou um alias
    escapam — quem precisar de exatidão usa `ast`, como
    `tests/test_log_falha_user_id.py`.

    Sem este caso, um PR futuro (ou um apontamento de revisor) põe teto no painel
    sem discussão, e o efeito só aparece num boot que falha ou numa purga cortada
    pela metade."""
    assert "options" not in _kwargs_do_connect(
        _funcao(admin_dashboard, "db_connect")), (
        "db_connect ganhou `options`: isso põe teto em DDL de boot, nas "
        "agregações do overview e na retenção diária de uma vez. Se é para "
        "acontecer, é decisão de outro PR — e este teste é onde ela se discute."
    )


def _arvore_do_admin_dashboard() -> ast.Module:
    return ast.parse(Path(admin_dashboard.__file__).read_text(encoding="utf-8"))


def _funcao(modulo, nome: str) -> ast.AsyncFunctionDef:
    """O nó da função pelo NOME. Falha fechada se ela sumir ou for renomeada: um
    portão que não acha o alvo tem de ficar vermelho, nunca verde por vacuidade
    (é o mesmo motivo do `assert connects` do irmão em
    `tests/test_log_falha_traceback.py`)."""
    for no in ast.walk(_arvore_do_admin_dashboard()):
        if isinstance(no, (ast.AsyncFunctionDef, ast.FunctionDef)) and no.name == nome:
            return no
    raise AssertionError(f"{nome} não existe mais em core/admin_dashboard.py — "
                         "o portão está apontado para o lugar errado")


def _kwargs_do_connect(no_funcao) -> set[str]:
    """Kwargs que de fato CONFIGURAM algo em algum `*.connect(...)` de dentro da
    função.

    O critério "constante falsy não conta" NÃO é reescrito aqui: ele é o
    `kwargs_com_valor` compartilhado, que o portão irmão
    (`tests/test_log_falha_traceback.py`) usa também. Era a mesma regra em duas
    cópias — e duas cópias de um critério é como um portão passa a medir menos que
    o irmão sem ninguém notar (CLAUDE.md §0.1/§0.7). O que sobra aqui é o que de
    fato difere: varrer uma FUNÇÃO atrás dos `.connect(...)` dela."""
    encontrados: set[str] = set()
    for no in ast.walk(no_funcao):
        if isinstance(no, ast.Call) and getattr(no.func, "attr", None) == "connect":
            encontrados |= kwargs_com_valor(no)
    return encontrados


def test_connect_do_log_system_event_tem_timeout_e_teto():
    """A CLASSE, não a instância: qualquer connect que `log_system_event` venha a
    abrir precisa dos DOIS tetos, e cada um cobre o que o outro não cobre —
    `connect_timeout` (libpq) limita o HANDSHAKE, `options=-c statement_timeout=…`
    (servidor) limita a EXECUÇÃO e a espera de LOCK. Esta função é chamada de
    dentro do event loop, então sem o segundo a tabela travada pendura o loop.

    Apontado para a FUNÇÃO e não para o arquivo, de propósito: um portão sobre
    `core/admin_dashboard.py` inteiro ficaria vermelho pelo `db_connect`, que está
    sem `options` por DECISÃO (ver o teste acima), e o conserto "óbvio" para ele
    seria justamente o que a decisão recusa.

    ponytail: TETO DECLARADO, o mesmo do irmão — o portão casa pelo `.connect`
    do atributo, então `from psycopg import AsyncConnection as _c; _c.connect(url)`
    ESCAPA, e valor CALCULADO (`options=f"…"`) passa sem ser avaliado. Fechar
    exigiria seguir o import; nada nesta função usa essas formas."""
    kwargs = _kwargs_do_connect(_funcao(admin_dashboard, "log_system_event"))
    assert kwargs, ("nenhum `.connect(...)` dentro de log_system_event — o portão "
                    "passaria por vacuidade")
    faltando = {"connect_timeout", "options"} - kwargs
    assert not faltando, (
        f"connect sem teto em log_system_event: {sorted(faltando)} — sem "
        "`connect_timeout` trava o caller com banco inalcançável; sem `options` "
        "(statement_timeout) trava com a tabela travada, dentro do event loop"
    )
