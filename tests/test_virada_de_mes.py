"""A VIRADA DO MÊS: o UTC cru usado como CALENDÁRIO, não como instante.

O #180 alinhou processo × app × sessão do Postgres (`utils_date.align_process_tz`,
provado em tests/test_fuso_do_app.py). Sobrou um QUARTO referencial que ninguém
alinhou: `datetime.now(timezone.utc).year/.month` chamado à mão para decidir
"que mês é hoje". Entre 00:00 e 03:00 UTC do dia 1º ele já está no mês seguinte
enquanto os outros três do backend ainda estão no anterior.

Os cinco referenciais:

| # | referencial            | como se lê                                   | alinhado pelo #180? |
|---|------------------------|----------------------------------------------|---------------------|
| 1 | processo               | `date.today()`, `datetime.now()` naive       | sim (`TZ` + `tzset`) |
| 2 | app                    | `now_tz()` / `today_tz()` / `_tz()`          | sim, é a fonte      |
| 3 | sessão do Postgres     | `date_part`, promoção `date`→`timestamptz`   | sim (`PGTZ`)        |
| 4 | UTC cru como CALENDÁRIO| `datetime.now(timezone.utc).year/.month`     | **NÃO — issue #215**|
| 5 | navegador              | `new Date()` (o `NOW` de frontend/dashboard.js)| fora do backend   |

Estados × eventos (fuso do app = America/Sao_Paulo):

| evento                  | 31/08 20:00-03 | 31/08 21:13-03      | 01/09 00:30-03 |
|                         | (23:00Z)       | (01/09 00:13Z)      | (03:30Z)       |
|-------------------------|----------------|---------------------|----------------|
| ref. 1/2/3 dizem        | agosto         | agosto              | setembro       |
| ref. 4 dizia (o defeito)| agosto         | **setembro** ← erro | setembro       |

A célula errada é UMA só, e ela bate em dois lugares.

NO CI: 5 vermelhos em tests/test_budget_category_accent.py — e eles vêm de DOIS
mecanismos, não de um. Só um é chamada de relógio: em `get_financial_data` (o
monólito) o `datetime.now(timezone.utc)` era chamado à mão. Em
`evaluate_after_expense` (core/budget_alerts.py) NÃO há chamada de relógio
nenhuma — o instante chega como ARGUMENTO, e o defeito é ler `.year/.month` dele
no calendário de QUEM CHAMOU. MEDIDO revertendo um site de cada vez, com os
referenciais 1 e 2 congelados em 31/08 21:13-03 e o 4 no relógio real (já em
setembro): só `evaluate_after_expense` revertido dá 3 vermelhos, só
`get_financial_data` dá 4, os dois juntos dão 5. Ou seja, dos 5: 1 cai só por
`evaluate_after_expense`, 2 caem só por `get_financial_data`, 2 caem pelos dois.

EM PRODUÇÃO: o `isCurrentViewData` de frontend/dashboard.js DESCARTA o payload
cujo mês diverge do mês do NAVEGADOR. O descarte tem duas saídas, e só uma
prende a tela (HIPÓTESE lida no código, sem navegador):

| caminho                              | o descarte faz                      | efeito                 |
|--------------------------------------|-------------------------------------|------------------------|
| `snapshot`/`month_data` do WebSocket | `return` cru, ANTES do `stopSpin()` | o spinner fica de pé   |
| `fetch` de mês (HTTP)                | `stopSpin()` e `setLaunchesLoading(false)`, e SÓ ENTÃO `return` | sem spinner preso; a tela só não atualiza |

Ou seja: "trava no spinner" vale para o snapshot do WebSocket, NÃO para o
dashboard inteiro.

RESÍDUO — FECHADO, e este parágrafo é o registro de que foi. Quando este
arquivo nasceu, o conserto trocava "servidor em UTC" por "servidor no fuso do
APP": casava com o navegador de quem está NO fuso do app e com mais ninguém, e
para os outros o descarte do `isCurrentViewData` continuava idêntico:

| usuário          | instante       | servidor (SP) | navegador | era        | hoje   |
|------------------|----------------|---------------|-----------|------------|--------|
| São Paulo        | 31/08 21:13-03 | agosto        | agosto    | consertado | idem   |
| Manaus (−04)     | 31/08 23:30-04 | setembro      | agosto    | descartava | adota  |
| Rio Branco (−05) | 31/08 22:30-05 | setembro      | agosto    | descartava | adota  |
| Lisboa (+01)     | 01/09 01:00+01 | agosto        | setembro  | descartava | adota  |

O conserto do cliente entrou no `c8e3bbe` (Onda 1 de perf), integrado no merge
`85ea2a4` — posterior ao `8ea113a` deste arquivo, e é por isso que a versão
original desta tabela dizia "ainda descarta" para as três últimas linhas. Ele
NÃO é o que este parágrafo previa ("não descartar antes do `stopSpin()`"): no
branch `snapshot` do `ws.onmessage`, se o usuário ainda não navegou de mês à
mão, o mês do snapshot é ADOTADO — o servidor é a fonte da verdade do mês —, e
o teto do `btn-next` (`latestKnownMonth`) avança junto. `month_data` fica de
fora: é resposta a `get_month` explícito e tem mesmo que ser descartado.

Provado com navegador, não por leitura:
`tests/frontend/snapshot_virada_de_mes.test.mjs` sintetiza a divergência num
contexto `Asia/Tokyo` (não depende do dia real) e traz os dois controles — o
negativo (`userNavigatedMonth` ligado volta a descartar) e o positivo (mês
corrente segue o caminho de sempre). Rodar:

    NODE_PATH=$(npm root -g) node --test tests/frontend/snapshot_virada_de_mes.test.mjs

Sobra deste caminho, medido e deixado de fora de propósito: o
`restoreSnapshotFromSession` roda ANTES do `connect()` e lê a chave da sessão
por `viewYear/viewMonth` (relógio do DISPOSITIVO), enquanto o
`persistSnapshotToSession` gravou com o mês do SERVIDOR. Na janela a chave não
casa e a troca /home ↔ /app perde o paint instantâneo até o WS chegar. É
otimização perdida por ~3 h/mês, não tela presa.

RESÍDUO, 2ª parte — `is_current_month` tem um TERCEIRO consumidor. Além do
`isCurrentViewData`, o render de frontend/dashboard.js deriva
`const hist = d.is_current_month !== undefined ? !d.is_current_month : …`, e é o
`hist` que decide se o saldo dos bancos do Open Finance entra no "Saldo
consolidado" (`const hasBanks = !hist && ofBankCount > 0;` e
`const ofBank = hist ? 0 : …`). O conserto muda esse campo num caso, e as TRÊS
condições têm de valer juntas para alcançá-lo:

  1. navegador À FRENTE do fuso do app (Lisboa +01 contra São Paulo −03), E
  2. dentro da janela de 3h do dia 1º, E
  3. navegando EXPLICITAMENTE para o mês do navegador — o `year`/`month` vai na
     requisição. Sem eles o servidor responde o próprio mês e o cliente ADOTA
     esse mês (tabela acima) em vez de cair aqui.

Aí o servidor responde `is_current_month: false` onde antes respondia `true`, e
o saldo dos bancos conectados SOME do consolidado. Não é dinheiro errado — nada
é gravado, é exibição, e a carga seguinte fora da janela volta ao normal. Para
quem está NO fuso do app o conserto MELHORA esse mesmo caminho, pelo mesmo
mecanismo ao contrário.

FECHADO pela condição 3, que era a única alcançável pela interface. O teto do
`btn-next` (`latestKnownMonth`) era `Math.max`, e `Math.max` só sabe SUBIR: ele
cobria a divergência em que o servidor está À FRENTE (Manaus −04) e deixava a
metade oposta (Lisboa +01) com o teto no mês do NAVEGADOR — botão aberto para um
mês que o servidor ainda não começou. Virou atribuição: o teto é o mês do
servidor nas duas direções, que é a regra já declarada no próprio bloco de
adoção ("o servidor é a fonte da verdade do mês"). Com o botão desabilitado, e
sendo `changeMonth` chamado só por `btn-prev`/`btn-next` (os demais `fetchMonthHttp`
usam `viewYear`/`viewMonth`), não há caminho de UI até o pedido explícito.
Coberto por `tests/frontend/snapshot_virada_de_mes.test.mjs`, com o negativo
(repondo o `Math.max`, o caso novo fica vermelho) e o positivo (o caso do mês
SEGUINTE prova que o teto continua subindo quando é o servidor que está à frente).

O que NÃO mudou: o `hist` continua derivado de `!is_current_month`, ou seja,
segue confundindo "não é o mês corrente" com "é passado". Um mês FUTURO
alcançado por outro caminho ainda renderizaria com os bancos zerados. Não há
caminho assim hoje; se algum aparecer, o conserto é no `hist`, não no teto.

EXCEÇÃO CONHECIDA E DELIBERADA — sobra 1 instância da classe em produção, não 0.
`auth_account_export_download` (o monólito) monta o nome do ZIP com `%Y%m%d` de
um `datetime.now(timezone.utc)`: export às 21:13 de São Paulo sai com o dia
SEGUINTE no nome do arquivo. Fica como está de propósito — o e-mail que
acompanha formata o MESMO instante como `"%d/%m/%Y %H:%M UTC"`, com todas as
letras, então ali UTC é declarado e não acidental, e o efeito é cosmético (nome
de arquivo), sem tocar em dado. A varredura foi por `ast`, não por grep:
variável que recebe `datetime.now(timezone.utc)` e depois é lida como CALENDÁRIO
(`.year`, `.month`, `.date()`, `strftime`, f-string com `%` no format spec),
fora de `tests/` — 2 usos, de 1 site só, e mais nenhum; o encadeado sem variável
(`datetime.now(timezone.utc).month`) foi conferido à parte e dá zero. Registrado
aqui para quem varrer a classe de novo não reabrir a discussão do zero.

O instante âncora é FIXO no passado (`2026-09-01T00:13Z`) — o minuto exato do run
vermelho do CI. Pelo relógio de parede esta prova só seria reproduzível em 3h por
mês; congelada, ela roda sempre.

Não há `freezegun` nem `time-machine` no requirements.txt. O congelamento é
`monkeypatch.setattr(<módulo>, "now_tz", ...)` na costura do import de nível de
módulo, e a semente vai ao banco com `criado_em` explícito.

Controle negativo de cada caso está no docstring dele. O caso 4 é POSITIVO de
propósito e, contra ESTE diff, é INERTE: nenhuma reversão do que está aqui o
derruba. O que ele guarda é uma HIPÓTESE DERRUBADA, nomeada para não ser
reaberta — o conserto REJEITADO de alinhar a sessão do Postgres em UTC
(`PGTZ=UTC`) em vez de pôr o servidor no fuso do app. Naquele caminho o
`date_part` (referencial 3) passaria a divergir do `date.today()` (referencial
1), os casos 1-3 continuariam verdes e este cairia.

COBERTURA, com todas as letras. O diff mexe em 8 linhas de relógio; os casos
abaixo provam 6 delas, com controle negativo medido em cada um. Ficam SEM
cobertura própria, de propósito, DUAS — e pela mesma razão:

| linha sem cobertura                                | por que fica de fora |
|----------------------------------------------------|----------------------|
| `export_email`: `now = now_tz()` → `y = year or now.year` | o único chamador do `/export/{user_id}` em frontend/dashboard.js manda `year` e `month` SEMPRE |
| handler `get_month` do WebSocket: `payload.get("year", now.year)` | os dois `ws.send({type:"get_month"…})` de frontend/dashboard.js mandam `year` e `month` SEMPRE |

Nos dois é o DEFAULT que o conserto troca, e o default só é lido por chamada
direta à API/ao socket sem parâmetro — que a UI nunca faz. São mudança da mesma
classe, pedida por ser a classe inteira, não conserto de bug alcançável. Cobrir
`export_email` ainda custaria usuário Pro, build de CSV/XLSX/PDF (reportlab) e
mock de envio de e-mail. Se a UI um dia parar de mandar o mês, isto vira teste.

E isso não é suposição: revertendo as DUAS para `datetime.now(timezone.utc)` a
suíte inteira dá `5312 passed, 1 xfailed`, número e lista IDÊNTICOS aos da
árvore íntegra. Elas não são cobertas por nada, aqui nem em lugar nenhum — que é
exatamente o motivo de estarem escritas nesta tabela em vez de subentendidas.

Sem número de linha de propósito: os arquivos citados aqui mudam a cada merge e
a tabela de estágios de frontend/routes/open_finance.py já apodreceu 3× por
isso. Nome de função é greppável e não escorrega.
"""
from __future__ import annotations

import asyncio
import json
import os
import time as time_module
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import db.budgets
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.shared as shared
import utils_date
from core.budget_alerts import evaluate_after_expense
from db.accounts import add_launch_and_update_balance
from db.connection import close_pool
from db.budgets import (
    get_budgets_status_for_month,
    sum_spent_in_category_this_month,
    upsert_budget,
)

_SP = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(autouse=True)
def _fuso_de_sao_paulo():
    """PINA o fuso do app em São Paulo — os instantes âncora são de lá.

    Sem isto o arquivo inteiro depende do `REPORT_TIMEZONE` da máquina, que é
    knob suportado (.env.example) e fuso brasileiro plausível no ambiente de um
    dev. MEDIDO sem a fixture, nos 5 casos deste arquivo: `America/Manaus` deixa
    1 vermelho e `UTC`/`Europe/Lisbon`/`Asia/Yangon` deixam 4 — a mesma
    fragilidade que tests/test_fuso_do_app.py documenta no cabeçalho dele.

    As TRÊS metades são carregadas, cada uma medida com as outras ligadas:

    - `align_process_tz` — sem ele, pinar `REPORT_TIMEZONE` deixa o app em São
      Paulo com processo e sessão do Postgres no fuso ANTIGO (o que o import de
      utils_date escreveu em `TZ`/`PGTZ`), que é a divergência que estes casos
      existem para medir: 1 vermelho em `America/Manaus`, 5 em `UTC` e em
      `Asia/Yangon`;
    - o pool SÍNCRONO (`close_pool`, db/connection.py) — `PGTZ` é lido pela libpq
      em CONEXÃO NOVA, então a conexão já aberta no pool guarda o fuso velho: sem
      ele, 1 vermelho (o caso 4, que é justamente o que passa pela sessão) em
      `Asia/Yangon`;
    - o pool ASYNC do dashboard (`shared._db_pool`, frontend/routes/shared.py) —
      são DOIS clientes libpq, e os casos 1, 2 e 5 vão pelo async (`db_connect`
      dentro de `get_financial_data`), não pelo síncrono. Fechar só o síncrono
      esconde o defeito atrás de ORDEM: o arquivo passa sozinho e quebra depois de
      qualquer arquivo que já tenha aberto o pool async. MEDIDO com só o síncrono
      fechado, em `pytest tests/test_categoria_vazia_donut_e_lista.py
      tests/test_virada_de_mes.py` (ordem alfabética, a que a suíte usa): 3
      vermelhos em `Asia/Yangon`, `UTC`, `Europe/Lisbon` e `TZ=UTC PGTZ=UTC`, 1
      em `America/Manaus` — e 0 com o arquivo sozinho, que é o que faz esse tipo
      de furo passar batido.

    O pool async é REABERTO na hora (`asyncio.run(shared._get_db_pool())`) em vez
    de deixado em `None`: se o primeiro uso do pool cair dentro do portal anyio do
    `TestClient` (caso 2), o pool nasce naquele event loop e o `__exit__` do
    cliente PENDURA para sempre em `asyncio.runners._cancel_all_tasks` — a mesma
    armadilha que o comentário do `_get_db_pool` documenta para um callback de
    reset. MEDIDO: com `_db_pool = None` no setup, o caso 2 trava (dump do
    faulthandler em `starlette/testclient.py:132` → `start_blocking_portal`).
    Fechar o pool ANTIGO não é opção: ele foi aberto num loop já encerrado, e
    `asyncio.run(pool.close())` cai em `CancelledError` no teardown.

    Restauração à mão pelas mesmas razões do `_restaura_fuso` de
    tests/test_fuso_do_app.py: quem escreve `TZ`/`PGTZ` é `os.environ` direto,
    fora do alcance do monkeypatch, e deixar o processo num fuso inventado
    contamina os arquivos seguintes da suíte. O teardown refaz os pools pelo
    mesmo motivo, e o critério é a baseline do vizinho — o que ele dá SOZINHO.
    RE-MEDIDO em 01/09/2026 DEPOIS do merge da `main` (o vizinho passou de 21
    para 28 funções / 43 casos), com `REPORT_TIMEZONE=<zona> pytest
    tests/test_virada_de_mes.py tests/test_fuso_do_app.py`:

    | medição                          | Yangon | UTC | Lisbon | Manaus |
    |----------------------------------|--------|-----|--------|--------|
    | vizinho SOZINHO (a baseline)     |   6    |  6  |   6    |   2    |
    | juntos, com o teardown refazendo |   6    |  6  |   6    |   2    |
    | juntos, SEM refazer              |   4    |  4  |   4    |   2    |

    Os números anteriores (5/5/5/1 e 3/3/3/1) tinham UM vermelho a menos em cada
    coluna: `test_sem_tzset_o_boot_continua_subindo`, que cai em TODAS as quatro
    zonas. Ele NÃO veio do merge — a `main` pura (ed9bb71) dá as mesmas listas de
    6 e de 2 nomes, medido em 01/09/2026. É baseline do vizinho, não regressão.

    Com o teardown, batem as LISTAS de nomes, não só as contagens. Sem ele o
    estrago é dos DOIS lados, e é por isso que a contagem de Manaus engana ao
    ficar igual: este arquivo deixaria a conexão em São Paulo no pool e pintaria
    de VERDE 3 vermelhos legítimos do vizinho em Yangon/UTC/Lisbon
    (`test_a_janela_de_hoje_nao_engole_as_23h_de_ontem`,
    `test_o_resumo_do_periodo_ve_o_gasto_das_23h`,
    `test_a_conversa_responde_pelo_dia_de_sao_paulo`) e 1 em Manaus (o primeiro
    deles) — e ainda INVENTARIA, nos quatro, um vermelho que o vizinho não tem
    sozinho: `test_as_tres_portas_do_banco_estao_no_fuso_do_app`. Em Manaus os
    dois se cancelam na contagem (2 → 2) e só a LISTA mostra a troca.
    """
    antes = {k: os.environ.get(k) for k in ("TZ", "PGTZ", "REPORT_TIMEZONE")}

    def _refaz_os_dois_pools():
        close_pool()                          # pool SÍNCRONO (db/connection.py)
        shared._db_pool = None                # pool ASYNC (frontend/routes/shared.py)
        asyncio.run(shared._get_db_pool())

    os.environ["REPORT_TIMEZONE"] = "America/Sao_Paulo"
    try:
        utils_date.align_process_tz()
    except Exception:
        pass              # mesmo tratamento do import de utils_date
    _refaz_os_dois_pools()
    yield
    for k, v in antes.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    # `tzset()` cru, NÃO `align_process_tz()`, pela razão que o `_restaura_fuso`
    # de tests/test_fuso_do_app.py escreve: reaplicar o conserto a cada teardown
    # o deixaria de pé para os arquivos seguintes e viraria tautologia por efeito
    # de ordem. `tzset()` só relê o `TZ` que as linhas acima restauraram.
    # Guardado porque `time.tzset` é POSIX e não existe no Windows.
    #
    # MEDIDO com o #180 revertido nos DOIS call sites (utils_date.py, ao final do
    # módulo, e config/env.py, dentro de `load_app_env`) e `TZ=UTC`:
    # `test_as_tres_portas_do_banco_estao_no_fuso_do_app` fica VERMELHO nas duas
    # ordens, com listas de 26 vermelhos idênticas. Reverter só o call site de
    # utils_date.py mede outra coisa: ali o `load_app_env` ainda escreve `PGTZ`
    # DEPOIS de a primeira conexão síncrona existir, então o vermelho do vizinho
    # vem de uma conexão obsoleta — e some para quem quer que feche o pool, este
    # arquivo ou outro. Quem move essa agulha é o `close_pool`, não o
    # `align_process_tz`; e não fechar custa os vermelhos legítimos do vizinho
    # que a tabela do docstring da fixture mede acima — 3 em Yangon/UTC/Lisbon e
    # 1 em Manaus (o vizinho vai de 6 para 4, e de 2 para 2 com a lista trocada).
    # (Os 26 desta medição foram reconferidos em 01/09/2026: as
    # duas ordens e o vizinho sozinho dão listas idênticas, de 26 nomes.)
    tzset = getattr(time_module, "tzset", None)
    if tzset is not None:
        tzset()
    _refaz_os_dois_pools()


# 2026-09-01T00:13Z == 2026-08-31 21:13-03. O minuto do run vermelho do CI.
ANCORA_UTC = datetime(2026, 9, 1, 0, 13, tzinfo=timezone.utc)
ANCORA_SP = ANCORA_UTC.astimezone(_SP)          # 2026-08-31 21:13-03
DEPOIS_DA_VIRADA_SP = datetime(2026, 9, 1, 0, 30, tzinfo=_SP)   # 03:30Z


def _congela(monkeypatch, instante_sp: datetime) -> None:
    """Congela o relógio do APP (ref. 2) na costura do monólito.

    `dashboard.now_tz` é import de nível de módulo justamente para ser
    substituível aqui — com o import tardio de antes não haveria onde pegar.
    """
    monkeypatch.setattr(dashboard, "now_tz", lambda: instante_sp)


def _gasto(user_id: int, categoria: str, valor: float, quando: datetime) -> None:
    add_launch_and_update_balance(
        user_id, "despesa", valor, "teste", "teste",
        categoria=categoria, criado_em=quando,
    )


def _snapshot(user_id: int) -> dict:
    return asyncio.run(dashboard.get_financial_data(user_id))


def _fatias(data: dict) -> dict[str, float]:
    return {c["categoria"]: c["total"] for c in data["expense_categories"]}


# ── 1. o donut na virada ────────────────────────────────────────────────────

def test_donut_na_virada_fica_no_mes_do_usuario(user_id, monkeypatch):
    """Controle negativo: revertendo `now = now_tz()` para
    `datetime.now(timezone.utc)` em `get_financial_data`
    (frontend/finance_bot_websocket_custom.py), o mês volta a 9 e
    `expense_categories` sai vazia.

    Segundo controle, do OUTRO relógio da mesma função: `get_financial_data`
    chama `history_earliest_date` (core/services/plan_service.py), que no tier
    Grátis corta a janela no dia 1 do mês. Revertendo o `now` que ela passa —
    `history_earliest_date(user_id)` sem argumento —, o corte volta a
    2026-09-01 e o donut sai vazio. MEDIDO: 2 vermelhos, este e o caso 2."""
    _congela(monkeypatch, ANCORA_SP)
    _gasto(user_id, "cafe", 42.0, ANCORA_SP)

    data = _snapshot(user_id)

    assert (data["year"], data["month"]) == (2026, 8)
    assert _fatias(data) == {"cafe": 42.0}


# ── 2. o caminho de produção (WebSocket) ────────────────────────────────────

def test_websocket_abre_o_snapshot_no_mes_do_usuario(user_id, monkeypatch):
    """O canal REAL do dashboard. Sem este caso, consertar só o
    `get_financial_data` deixaria o `manager.connect`/snapshot inicial abrindo
    em setembro — e o `isCurrentViewData` (frontend/dashboard.js) compara o
    `year/month` do payload com o do navegador, então o snapshot seria
    DESCARTADO e a tela ficaria no spinner, com o teste do caso 1 passando
    mesmo assim.

    Controle negativo: revertendo `now = now_tz()` para
    `datetime.now(timezone.utc)` no `websocket_endpoint`
    (frontend/finance_bot_websocket_custom.py), o payload sai com
    `month == 9`."""
    _congela(monkeypatch, ANCORA_SP)
    _gasto(user_id, "cafe", 42.0, ANCORA_SP)

    client = TestClient(dashboard.app)
    client.cookies.set(
        dashboard.DASHBOARD_COOKIE_NAME,
        dashboard.make_dashboard_token(user_id, hours=1),
    )
    with client.websocket_connect(f"/ws/{user_id}") as ws:
        msg = json.loads(ws.receive_text())
        # o mês GRAVADO na conexão pelo `manager.connect` — é ele que o
        # "refresh" reusa (`manager.get_month`) em toda atualização ao vivo
        guardado = list(dashboard.manager.active[user_id].values())

    assert msg["type"] == "snapshot"
    assert (msg["data"]["year"], msg["data"]["month"]) == (2026, 8)
    assert _fatias(msg["data"]) == {"cafe": 42.0}
    assert guardado == [{"year": 2026, "month": 8}]


# ── 2b. o FALLBACK do get_month (conexão não registrada) ────────────────────

def test_get_month_sem_conexao_registrada_cai_no_mes_do_usuario(monkeypatch):
    """O caso 2 prova o mês GRAVADO pelo `manager.connect` (o `guardado`); esta é
    a OUTRA linha de `ConnectionManager.get_month` — o fallback de quando não há
    nada gravado —, e antes deste caso ela não era exercitada por nada.

    MEDIDO na suíte inteira, revertendo SÓ ela para `datetime.now(timezone.utc)`:
    `1 failed, 5311 passed` — e o único vermelho é este teste. Ou seja, entre
    5312 casos ele é o único que discrimina essa linha.

    Ela dispara quando o ws não está mais em `manager.active`: desconexão que
    corre com o `get_month` do refresh. Não precisa de banco nem de usuário — é
    estado em memória, e é por isso que cobri-la sai por 3 linhas.

    Controle negativo: revertendo `now = now_tz()` para
    `datetime.now(timezone.utc)` em `ConnectionManager.get_month`
    (frontend/finance_bot_websocket_custom.py), o retorno vira (2026, 9)."""
    _congela(monkeypatch, ANCORA_SP)
    manager = dashboard.ConnectionManager()      # `active` vazio força o fallback

    assert manager.get_month(object(), 12345) == (2026, 8)


# ── 3. o alerta de orçamento na virada ──────────────────────────────────────

def test_alerta_de_orcamento_na_virada(user_id):
    """O caller manda o INSTANTE (`add_from_entities`, core/handlers/launches.py);
    quem decide o mês é `evaluate_after_expense`. Com `criado_em.year/.month`
    cru, um instante UTC procurava setembro enquanto o SQL (`date_part`, sessão
    em SP) somava agosto: zero gasto, alerta nenhum.

    FRONTEIRA, não dado de produção: hoje o único caller manda naive do processo
    ou aware do fuso do app, e quem entrega UTC-aware é a suíte
    (tests/test_budget_category_accent.py). O que se mede aqui é a função ficar
    correta para qualquer fuso que lhe entreguem.

    Controle negativo: revertendo `dia = day_tz(criado_em)` para
    `year, month = criado_em.year, criado_em.month` em `evaluate_after_expense`
    (core/budget_alerts.py), o retorno volta a None."""
    upsert_budget(user_id, "cafe da manha", 100.0)
    _gasto(user_id, "cafe da manha", 90.0, ANCORA_SP)

    alerta = evaluate_after_expense(user_id, "cafe da manha", 90.0, ANCORA_UTC)

    assert alerta is not None
    assert (alerta.threshold, alerta.spent) == (80, 90.0)


# ── 4. controle POSITIVO: o lado que já estava certo ────────────────────────

def test_orcamento_mensal_continua_em_agosto(user_id, monkeypatch):
    """POSITIVO, não negativo. `sum_spent_in_category_this_month` e `_parse_ym`
    (db/budgets.py) leem o mês no referencial 1 (processo, já alinhado pelo
    #180) e sempre estiveram certos.

    Contra ESTE diff o caso é INERTE — nenhuma reversão do que está no PR o
    derruba, e os controles negativos dos casos 1-3 já mostravam isso. O que ele
    guarda é a hipótese REJEITADA: alinhar a sessão do Postgres em UTC
    (`PGTZ=UTC`) em vez de pôr o servidor no fuso do app. Naquele caminho o
    `date_part` (ref. 3) divergiria do `date.today()` (ref. 1), os casos 1-3
    continuariam verdes e ESTE cairia. Ele existe para essa hipótese não voltar.

    `date.today()` é o referencial 1 e não passa por `now_tz` — congelá-lo exige
    substituir o próprio `date` do módulo (subclasse, para que `date(...)`
    continue construindo normalmente)."""
    class _DataCongelada(date):
        @classmethod
        def today(cls):
            return ANCORA_SP.date()          # 2026-08-31

    monkeypatch.setattr(db.budgets, "date", _DataCongelada)

    upsert_budget(user_id, "cafe", 100.0)
    _gasto(user_id, "cafe", 42.0, ANCORA_SP)

    assert sum_spent_in_category_this_month(user_id, "cafe") == 42.0

    status = get_budgets_status_for_month(user_id)
    assert status["month"] == "2026-08"
    linha = [b for b in status["budgets"] if b["categoria"] == "cafe"]
    assert len(linha) == 1 and linha[0]["spent"] == 42.0


# ── 5. depois da virada LOCAL: a janela não pode só alargar ─────────────────

def test_depois_da_virada_local_o_mes_anterior_fica_fora(user_id, monkeypatch):
    """01/09 00:30-03 (03:30Z): agora TODOS os cinco referenciais dizem setembro.
    O gasto de 31/08 21:13-03 tem de ficar FORA e o de 01/09 00:05-03, dentro.

    Este é o caso que um conserto preguiçoso não sobrevive: somar 3h ao corte
    (ou alargar a janela para pegar o mês anterior) passa nos casos 1 e 3 e cai
    aqui, porque o gasto de agosto voltaria a aparecer no donut de setembro."""
    _congela(monkeypatch, DEPOIS_DA_VIRADA_SP)
    _gasto(user_id, "cafe", 42.0, ANCORA_SP)                                  # agosto
    _gasto(user_id, "carne", 13.0, datetime(2026, 9, 1, 0, 5, tzinfo=_SP))    # setembro

    data = _snapshot(user_id)

    assert (data["year"], data["month"]) == (2026, 9)
    assert _fatias(data) == {"carne": 13.0}
