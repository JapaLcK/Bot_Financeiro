"""PROCESSO × APP × SESSÃO DO POSTGRES: um fuso só, ou a fronteira do dia mente.

O Postgres promove um `timestamp` NAIVE a `timestamptz` — e resolve `::date`,
`date_trunc` e `date_part` — pelo fuso da SESSÃO. As dezenas de cortes de janela do tipo
`criado_em < datetime.combine(fim + 1 dia, 00:00)` (db/accounts.py, db/plans.py,
core/budget_alerts.py, forecast, admin) mandam esse naive. Com a sessão em UTC —
produção no Railway e CI — um gasto das 23:00 em São Paulo caía FORA do dia em
que o usuário o fez. A ESCRITA de `criado_em` sempre esteve certa (aware); o
defeito é de LEITURA.

Consertar só a sessão não bastava: `date.today()` fica no fuso do PROCESSO, e
processo em UTC contra app em São Paulo é a MESMA divergência por outra porta.
`utils_date.align_process_tz` fecha as duas de uma vez, escrevendo `TZ` (processo)
e `PGTZ` (toda conexão libpq) com o mesmo nome e chamando `tzset()`.

Este arquivo PROVA o alinhamento dos três referenciais acima; os outros o tomam
como premissa (ver o cabeçalho de tests/test_category_launches_query.py). Existe
um QUARTO referencial fora do alcance de `align_process_tz` — o UTC cru chamado à
mão como CALENDÁRIO (`datetime.now(timezone.utc).year/.month`) — e quem o prova é
tests/test_virada_de_mes.py, com instante congelado na virada do mês. Controle
negativo de cada caso está no comentário logo acima dele.

O que os casos leem do ambiente são DUAS coisas, e elas não andam juntas:

- o RELÓGIO (que dia é hoje): os casos 3, 4, 5 e 7, que chamam `today_tz()` e
  combinam o resultado com `_SP` fixo, mais o 18.5 (`date.today()`, para o
  rótulo do mês) e o 18.7 (`datetime.now`, para o dia corrente em dois fusos).
  Os outros — 1, 2, 6, 8-13 e 18.1-18.4/18.6 — usam instante aware LITERAL, o
  epoch, ou nenhuma data;
- o FUSO configurado: os casos 3, 4, 5, 6 e 7, mais a seção 18 inteira. Medido
  em 29/08/2026, `REPORT_TIMEZONE=Asia/Yangon` deixa os CINCO primeiros
  vermelhos — no caso 6 isso é o controle negativo declarado dele, não um
  defeito. A seção 18 seta o fuso ela mesma, por caso, e não depende do
  ambiente.

Ler o dia corrente basta para o que 3, 4, 5 e 7 medem: a fronteira do dia contra
a JANELA que o produto abre, e essa janela é sempre "hoje" no fuso do app — um
dia fixo do passado trocaria o dia sem trocar a pergunta. Quem faz a mesma
pergunta com data literal é o caso 6 (31/08/2025 23:00 em São Paulo).

A seção 18 (issue #179) é de outra natureza: guarda de ACOPLAMENTO, não de
fronteira do dia. Leia o cabeçalho dela antes de mexer nos casos.
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import time as time_module
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import db
import db_support
import utils_date
from token_utils import make_dashboard_token
from utils_date import _tz, day_tz, today_tz, tz_name

_SP = ZoneInfo("America/Sao_Paulo")


@pytest.fixture(autouse=True)
def _restaura_fuso():
    """`load_app_env` escreve DIRETO em `os.environ` (não pelo monkeypatch).

    Sem restaurar à mão, o primeiro teste que carrega um `.env` de `tmp_path`
    deixaria o processo inteiro num fuso inventado e contaminaria os arquivos
    seguintes da suíte — a classe de bug que o §3 do CLAUDE.md chama de
    dependência de ordem.
    """
    antes = {k: os.environ.get(k) for k in ("TZ", "PGTZ", "REPORT_TIMEZONE", "APP_ENV")}
    original = utils_date._TZ_ENV_ORIGINAL
    yield
    for k, v in antes.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    utils_date._TZ_ENV_ORIGINAL = original
    # `tzset()` cru, NÃO `align_process_tz()`: chamar o conserto aqui o
    # reaplicaria a cada teardown e deixaria `test_o_processo_roda_no_fuso_do_app`
    # verde mesmo com o conserto revertido — teste tautológico por efeito de
    # ordem. `tzset()` só relê o `TZ` que a linha acima acabou de restaurar.
    #
    # Acesso GUARDADO, igual ao que `align_process_tz` faz: `time.tzset` é POSIX
    # e não existe no Windows, que o `docs/readme.md:18` lista como suportado.
    # Como `from time import tzset` no topo levantava na COLETA, o arquivo
    # inteiro morria lá — inclusive os dois casos que existem para provar que o
    # Windows está tratado. Consertar a produção e esquecer o teste é a mesma
    # instância-em-vez-de-categoria de sempre (Codex #180, P2).
    tzset = getattr(time_module, "tzset", None)
    if tzset is not None:
        tzset()


def _tzif(*partes: str) -> bytes:
    """Bytes de um TZif real, achado pelo `TZPATH` do próprio Python.

    `/usr/share/zoneinfo` é layout de Unix e não existe no Windows — que o
    `docs/readme.md:18` lista como suportado —, nem numa imagem slim sem banco
    de fusos do sistema. O `zoneinfo.TZPATH` é onde o Python de verdade procura,
    então é a única fonte portátil (Codex #180, P2).

    `skip` e não falha: sem banco de fusos no disco não há como montar o cenário
    "o `ZoneInfo` resolve e a libc não", e um ERROR ali acusaria o ambiente, não
    o código.
    """
    import zoneinfo

    for raiz in zoneinfo.TZPATH:
        alvo = pathlib.Path(raiz).joinpath(*partes)
        if alvo.is_file():
            return alvo.read_bytes()
    pytest.skip(f"sem TZif de {'/'.join(partes)} no TZPATH: {zoneinfo.TZPATH}")


# ── 1. As três portas do banco ───────────────────────────────────────────────
# Controle NEGATIVO: apague `os.environ["PGTZ"] = name` de
# `utils_date.align_process_tz` → as 3 portas voltam ao default do SERVIDOR e
# este caso fica vermelho (medido em 29/08/2026: as três devolvem
# `America/New_York` nesta máquina; no Railway e no CI, `Etc/UTC`).
#
# No arquivo INTEIRO esse mesmo corte dá números diferentes, e o honesto é o da
# produção: 11 vermelhos com a sessão em UTC (`PGTZ=UTC`, que é o que produção e
# CI têm) e 7 na sessão local (−04), dos quais 5 são só `KeyError: 'PGTZ'` nos
# casos 8, 9, 10 e 14 — leitura da própria variável apagada, não medição de
# produto (medido em 29/08/2026, com o `os.environ["PGTZ"] = name` removido).
#
# As três existem porque são três clientes libpq diferentes e nenhum deles tem
# uma linha de código sobre fuso: pool SÍNCRONO (db/connection.py), pool ASYNC do
# dashboard (frontend/routes/shared.py) e conexão avulsa do admin
# (core/admin_dashboard.py). É `PGTZ` que as cobre de uma vez — foi por isso que
# o conserto não entrou em nenhum dos três arquivos.

def _tz_da_sessao_sincrona() -> str:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select current_setting('TimeZone') as tz")
        return cur.fetchone()["tz"]


def _no_pool_do_dashboard(chamada):
    """Roda uma corrotina do monólito com pool próprio DESTE event loop.

    O pool novo é aberto num `await` SOZINHO, antes da corrotina — mesmo passo
    que o `_fuso_de_sao_paulo` (tests/test_virada_de_mes.py) dá com
    `asyncio.run(shared._get_db_pool())` — e não é detalhe de estilo: são DOIS
    globais de `frontend/routes/shared.py` presos a um event loop, o `_db_pool`
    e o `_db_pool_lock`. Zerar só o pool basta enquanto a corrotina abre UMA
    conexão por vez (caso 1); quebra no `get_financial_data` (18.6), que abre
    várias em `asyncio.gather` — aí duas `_q` chamam `_get_db_pool` com o pool
    ainda em `None`, o `asyncio.Lock` fica CONTENDIDO e só então ele resolve o
    loop (`asyncio.locks.Lock.acquire` só chama `_get_loop()` no ramo com
    espera; o ramo sem contenção nunca liga o lock a loop nenhum). Abrindo o
    pool antes, o gather encontra `_db_pool` já preenchido e nem chega no lock,
    que assim nunca se prende a loop nenhum — nem ao daqui.

    O que a contenção causa: o `_LoopBoundMixin` guarda o loop da PRIMEIRA vez,
    o `asyncio.run` seguinte estoura `RuntimeError: is bound to a different
    event loop` DENTRO do gather, a `_q` que ganhou o lock fica pendurada em
    `pool.open()`, o pool novo nunca chega a `shared._db_pool` (logo o `finally`
    não fecha nada) e os workers dele sobrevivem ao teste — o `asyncio.run`
    seguinte PENDURA PARA SEMPRE em `asyncio.runners._cancel_all_tasks`, a mesma
    armadilha que o docstring do `_fuso_de_sao_paulo` descreve para o portal do
    TestClient.

    MEDIDO em 01/09/2026, com a linha do `_get_db_pool()` removida e pytest de 2
    node IDs (18.6 × `test_lista_encontra_o_que_o_donut_mostra`): trava nas DUAS
    ordens. Com ela, passam nas duas ordens os 4 arquivos que rodam
    `asyncio.run(get_financial_data(...))` (`grep -rn get_financial_data
    tests/`) pareados com este, e `pytest -k "fuso or donut"` termina.

    Prender o lock a ESTE loop e devolvê-lo ao módulo não resolve: MEDIDO com
    `shared._db_pool_lock = asyncio.Lock()` no lugar desta linha, os 4 vizinhos
    passam quando rodam ANTES e travam quando rodam DEPOIS — o veneno só troca
    de direção, porque o gather daqui prende o lock novo ao loop daqui.
    """
    import frontend.routes.shared as shared

    async def _ler():
        anterior, shared._db_pool = shared._db_pool, None
        try:
            await shared._get_db_pool()
            return await chamada()
        finally:
            if shared._db_pool is not None:
                await shared._db_pool.close()
            shared._db_pool = anterior

    return asyncio.run(_ler())


def _tz_da_sessao_async_do_dashboard() -> str:
    import frontend.routes.shared as shared

    async def _ler():
        async with await shared.db_connect() as conn:
            cur = await conn.execute("select current_setting('TimeZone') as tz")
            return (await cur.fetchone())["tz"]

    return _no_pool_do_dashboard(_ler)


def _tz_da_sessao_do_admin() -> str:
    from core.admin_dashboard import db_connect

    async def _ler():
        conn = await db_connect()
        try:
            cur = await conn.execute("select current_setting('TimeZone') as tz")
            return (await cur.fetchone())["tz"]
        finally:
            await conn.close()

    return asyncio.run(_ler())


def test_as_tres_portas_do_banco_estao_no_fuso_do_app():
    esperado = tz_name()
    assert _tz_da_sessao_sincrona() == esperado
    assert _tz_da_sessao_async_do_dashboard() == esperado
    assert _tz_da_sessao_do_admin() == esperado


# ── 2. O processo, na configuração DEFAULT ───────────────────────────────────
# Sem monkeypatch e sem reload de propósito: é o único caso que mede o que
# acontece de verdade quando ninguém configura nada, que é o caminho da produção
# e do CI. O epoch não depende do relógio.
#
# Controle NEGATIVO: apague `os.environ["TZ"] = name` de `align_process_tz` →
# vermelho (o processo volta ao fuso do sistema; no CI, UTC contra o -03 do app).
# Medido em 29/08/2026 (`REPORT_TIMEZONE=America/Sao_Paulo TZ=Etc/UTC PGTZ=UTC`):
# com a guarda de offsets do caso 13 no lugar, esse corte não fica só neste caso —
# a divergência que ele cria é a que a guarda existe para pegar, então
# `load_app_env` recusa o boot e os 16 casos do arquivo erram de uma vez com
# `SystemExit`. É vermelho mais alto, não menos.

def test_o_processo_roda_no_fuso_do_app():
    naive = datetime.fromtimestamp(0)
    no_fuso_do_app = datetime.fromtimestamp(0, _tz()).replace(tzinfo=None)
    assert naive == no_fuso_do_app, (naive, no_fuso_do_app, tz_name())
    # e `date.today()` (52 sites de produção) concorda com `today_tz()`
    assert date.today() == today_tz()


# ── 3/4. A fronteira do dia, nas duas portas de leitura ──────────────────────
# Controle NEGATIVO do caso 3: reverta o conserto (tire a chamada de
# `align_process_tz` do import de utils_date) e rode com `PGTZ=UTC` → vermelho
# nas duas portas: 23:00 em São Paulo é 02:00Z do dia SEGUINTE, e o corte
# `criado_em < combine(hoje + 1 dia, 00:00)` lido em UTC exclui o lançamento.
# O caso 4 é o controle POSITIVO do par: um conserto que só ALARGASSE a janela
# (por exemplo somando 3h no corte) passaria no 3 e ficaria vermelho aqui,
# porque puxaria as 23:00 de ONTEM para dentro de hoje.

def _gasto(uid: int, quando: datetime, alvo: str, valor: float = 50) -> None:
    db.add_launch_and_update_balance(
        uid, "despesa", valor, alvo, None, categoria="mercado", criado_em=quando,
    )


def test_gasto_das_23h_conta_no_dia_de_sao_paulo(pro_user_id):
    hoje = today_tz()
    _gasto(pro_user_id, datetime.combine(hoje, time(23, 0), tzinfo=_SP), "23h de hoje")

    rows, resumo = db.list_launches_by_category(
        pro_user_id, "mercado", start_date=hoje, end_date=hoje,
    )
    assert resumo["n_total"] == 1, (resumo, [r["criado_em"] for r in rows])
    assert rows[0]["data"] == hoje, rows[0]

    # a MESMA janela pela rota do dashboard (o que a Distribuição do mês abre)
    resp = _cliente().get(
        f"/categories/{pro_user_id}/launches?categoria=mercado"
        f"&from={hoje.isoformat()}&to={hoje.isoformat()}",
        headers={"Authorization": f"Bearer {make_dashboard_token(pro_user_id, hours=1)}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["resumo"]["n_total"] == 1, resp.json()["resumo"]


def test_a_janela_de_hoje_nao_engole_as_23h_de_ontem(pro_user_id):
    hoje = today_tz()
    ontem = hoje - timedelta(days=1)
    _gasto(pro_user_id, datetime.combine(ontem, time(23, 0), tzinfo=_SP), "23h de ontem")
    _gasto(pro_user_id, datetime.combine(hoje, time(0, 30), tzinfo=_SP), "00h30 de hoje")

    rows, resumo = db.list_launches_by_category(
        pro_user_id, "mercado", start_date=hoje, end_date=hoje,
    )
    assert resumo["n_total"] == 1, (resumo, [r["descricao"] for r in rows])
    assert rows[0]["descricao"] == "00h30 de hoje", rows[0]


# ── 5. A outra porta do mesmo corte ──────────────────────────────────────────
# `get_summary_by_period_impl` (db_support.py:56-57) monta o MESMO
# `datetime.combine(fim + 1 dia, 00:00)` naive, e é ele que o saldo do mês e o
# relatório diário leem. Controle negativo idêntico ao do caso 3.

def test_o_resumo_do_periodo_ve_o_gasto_das_23h(pro_user_id):
    hoje = today_tz()
    _gasto(pro_user_id, datetime.combine(hoje, time(23, 0), tzinfo=_SP), "23h", valor=50)
    # A função crua, e não o `db.get_summary_by_period` que a embrulha
    # (db/accounts.py:379): é aqui que o `datetime.combine` naive é montado.
    resumo = db_support.get_summary_by_period_impl(
        db.get_conn, db.ensure_user, pro_user_id, hoje, hoje,
    )
    assert resumo["despesa"] == 50.0, resumo


# ── 6. Âncora LITERAL, sem depender da configuração do ambiente ──────────────
# Os casos acima perguntam "o app concorda consigo mesmo?" — passariam se TODAS
# as pontas fossem para o fuso ERRADO juntas. Este pergunta outra coisa: um
# instante escrito à mão em `America/Sao_Paulo` cai no dia de São Paulo.
#
# Controle NEGATIVO: rode com `TZ=Etc/UTC` (sem `REPORT_TIMEZONE`) → vermelho,
# porque aí o fuso do app É UTC e 2025-08-31 23:00 -03 é 2025-09-01 02:00Z.
# Data fixa no passado: independe do relógio e da data de execução.

def test_31_de_agosto_as_23h_em_sao_paulo_e_agosto(pro_user_id):
    dia = date(2025, 8, 31)
    _gasto(pro_user_id, datetime(2025, 8, 31, 23, 0, tzinfo=_SP), "ultimo dia do mes")

    rows, resumo = db.list_launches_by_category(
        pro_user_id, "mercado", start_date=dia, end_date=dia,
    )
    assert resumo["n_total"] == 1, resumo
    assert rows[0]["data"] == dia, rows[0]
    # e o resumo do MÊS de agosto, que é onde o usuário procura
    assert db.get_summary_by_period(
        pro_user_id, date(2025, 8, 1), date(2025, 8, 31),
    )["despesa"] == 50.0


# ── 7. A conversa inteira, pelo handle_incoming ──────────────────────────────
# "Rode a conversa, não a função" (§3): as 4 perguntas da issue #178, na mesma
# sessão, com estado real no banco. Cobre uma porta que nenhum caso acima toca —
# `get_top_expense_categories`, que o "quanto gastei em X" usa.
# Controle NEGATIVO idêntico ao do caso 3 (sem o alinhamento e com `PGTZ=UTC`,
# "quanto gastei hoje" responde o gasto de ONTEM e "quanto gastei ontem" diz que
# não houve gasto — é exatamente a tabela da issue).
#
# uid < 2 bilhões de propósito: `_normalize_user_id` (core/handle_incoming.py:106)
# REMAPEIA id grande, e semear no uid da fixture faria o handler ler OUTRO
# usuário — falso vermelho que não tem nada a ver com fuso.

def _uid_que_o_handler_resolve() -> int:
    import uuid

    from conftest import promote_to_pro

    uid = int(uuid.uuid4().int % 1_000_000_000) + 1
    db.ensure_user(uid)
    return promote_to_pro(uid)


def test_a_conversa_responde_pelo_dia_de_sao_paulo():
    from core.handle_incoming import handle_incoming
    from core.types import IncomingMessage

    uid = _uid_que_o_handler_resolve()
    hoje = today_tz()
    _gasto(uid, datetime.combine(hoje, time(23, 0), tzinfo=_SP), "mercado hoje", valor=50)
    _gasto(uid, datetime.combine(hoje - timedelta(days=1), time(23, 0), tzinfo=_SP),
           "mercado ontem", valor=90)

    def fala(texto: str) -> str:
        out = handle_incoming(IncomingMessage(platform="discord", user_id=uid, text=texto))
        assert out, f"sem resposta para {texto!r}"
        return out[0].text

    hoje_txt = fala("quanto gastei hoje")
    assert "50" in hoje_txt and "90" not in hoje_txt, hoje_txt

    ontem_txt = fala("quanto gastei ontem")
    assert "90" in ontem_txt, ontem_txt

    # JANELA ROLANTE, e não "meus gastos do mes". A troca não é de estilo: o
    # `"140" in ...` sobre a resposta do "do mes" era TAUTOLÓGICO. Medido em
    # 29/08/2026 com `today_tz()` fixado em 2026-08-01 (dia 1, o pior caso), a
    # resposta foi:
    #
    #     📅 Período: 01/08/2026 a 29/08/2026
    #     🏦 Saldo atual: R$ -140,00      ← é DAQUI que o "140" vinha
    #     📉 Gastos do mês: R$ 50,00      ← o número da janela, que ninguém lia
    #
    # "meus gastos do mes" nem passa pelo `spend_query`: cai no resumo mensal, que
    # imprime o SALDO da conta — e saldo não depende de janela nenhuma, então o
    # assert ficava VERDE com o mês certo, com o mês errado e com o fuso quebrado.
    # (O caso 7 rodou verde com `today_tz()` no dia 1 mesmo com o total do mês em
    # R$ 50,00; a bomba-relógio de fronteira de mês era o sintoma, a medição vazia
    # era a causa.)
    #
    # "últimos N dias" (`parse_period_from_text`, utils_date.py:396-399) é rolante:
    # sempre ontem+hoje, em qualquer dia do mês, e a resposta traz o TOTAL da
    # janela ("Você gastou R$ 140,00 nos últimos 2 dias"), não o saldo.
    # Controle NEGATIVO deste assert: troque 2 por 1 → vermelho (R$ 50,00 —
    # medido). O de cima não tinha controle negativo possível.
    dois_dias_txt = fala("quanto gastei nos ultimos 2 dias")
    assert "140" in dois_dias_txt, dois_dias_txt

    cat_txt = fala("quanto gastei em mercado hoje")
    assert "50" in cat_txt and "90" not in cat_txt, cat_txt


# ── 8/9/10. O `.env`, que é por onde o buraco voltava ────────────────────────
# `utils_date` é importado ANTES de o `.env` ser lido. Sem a chamada no fim de
# `load_app_env`, um `REPORT_TIMEZONE` vindo de ARQUIVO faria o app e o banco
# seguirem o arquivo enquanto o PROCESSO ficava no default — a MESMA divergência
# que este PR fecha, por um canal suportado, e justamente a variável que a
# mensagem de erro do boot ensina o operador a usar.
#
# `load_app_env` de VERDADE, sem reload de módulo: reload esconderia justamente o
# caminho quebrado (o import só acontece uma vez na vida do processo).

def _com_env(tmp_path, monkeypatch, conteudo: str) -> None:
    """Escreve um `.env` de mentira e roda o `load_app_env` de verdade nele."""
    import config.env as cfg

    (tmp_path / ".env").write_text(conteudo, encoding="utf-8")
    monkeypatch.setattr(cfg, "ROOT_DIR", tmp_path)
    cfg.load_app_env()


# Controle NEGATIVO do caso 8: apague o `utils_date.align_process_tz()` do fim de
# `load_app_env` → vermelho (o app vai para Tóquio e o processo fica onde estava).
def test_report_timezone_do_arquivo_move_o_processo_tambem(tmp_path, monkeypatch):
    monkeypatch.delenv("REPORT_TIMEZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(utils_date, "_TZ_ENV_ORIGINAL", None)

    _com_env(tmp_path, monkeypatch, "REPORT_TIMEZONE=Asia/Tokyo\n")

    assert tz_name() == "Asia/Tokyo"
    # PGTZ, e não `current_setting` de uma conexão: `PGTZ` vale para conexão
    # NOVA, e o pool síncrono deste processo já está aberto desde antes. Na
    # produção não existe esse "antes" — `load_app_env` roda no boot, antes da
    # primeira conexão. Quem prova que `PGTZ` chega à sessão é o caso 1.
    assert os.environ["PGTZ"] == "Asia/Tokyo"
    # o PROCESSO foi junto: é este assert que o controle negativo derruba
    assert datetime.fromtimestamp(0) == datetime.fromtimestamp(
        0, ZoneInfo("Asia/Tokyo")).replace(tzinfo=None)


# Controle NEGATIVO do caso 9: apague o `os.environ.pop("TZ", None)` de
# `load_app_env` → vermelho. Sem ele o `TZ` que `align_process_tz` escreveu no
# import faz o `setdefault` achar que já há valor, e `TZ` no `.env` — documentado
# em `.env.example` — vira um no-op silencioso.
def test_tz_do_arquivo_continua_valendo(tmp_path, monkeypatch):
    monkeypatch.delenv("REPORT_TIMEZONE", raising=False)
    # O estado REAL da produção no momento em que `load_app_env` roda: `TZ` já
    # está no ambiente porque o import de `utils_date` o escreveu, e
    # `_TZ_ENV_ORIGINAL is None` diz que não foi o operador quem o pôs. Apagar o
    # `TZ` aqui (em vez de simulá-lo) desarmaria o controle negativo — medido: com
    # `delenv` o teste passa mesmo sem o `pop` em `load_app_env`.
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    monkeypatch.setattr(utils_date, "_TZ_ENV_ORIGINAL", None)

    _com_env(tmp_path, monkeypatch, "TZ=Asia/Tokyo\n")

    # Sem o `pop`, o `setdefault` acha o `TZ` que o import escreveu e o arquivo
    # não é lido: `tz_name()` volta America/Sao_Paulo e este assert fica vermelho.
    assert tz_name() == "Asia/Tokyo"
    assert os.environ["PGTZ"] == "Asia/Tokyo"
    # O PROCESSO foi junto — o mesmo assert do caso 8, e pela mesma razão: os
    # dois de cima releem o ambiente que `load_app_env` acabou de escrever, e
    # sozinhos não distinguem "o app mudou de fuso" de "as três pontas mudaram
    # juntas", que é o invariante deste PR. Não tem controle negativo PRÓPRIO, e
    # não dá para ter: qualquer sabotagem que separe processo de app cai na
    # guarda de offsets (caso 13) e vira `SystemExit` no boot antes de chegar
    # aqui — medido. Ele fica porque a afirmação é sobre o produto, não sobre o
    # que derruba o teste.
    assert datetime.fromtimestamp(0) == datetime.fromtimestamp(
        0, ZoneInfo("Asia/Tokyo")).replace(tzinfo=None)


# Controle POSITIVO dos dois acima: o `pop` não pode roubar a precedência de quem
# configurou o ambiente DE VERDADE (Railway, docker, shell). Sem este caso, um
# `pop` incondicional passaria nos dois de cima e quebraria a produção calado.
def test_o_ambiente_real_vence_o_arquivo(tmp_path, monkeypatch):
    # `TZ` posto pelo OPERADOR (Railway/docker/shell), e não pelo import — é o
    # que `_TZ_ENV_ORIGINAL` distingue. Um `pop` incondicional apagaria este `TZ`
    # e o arquivo passaria a mandar: `tz_name()` viraria Europe/Lisbon e este
    # teste ficaria vermelho, que é exatamente o que ele existe para impedir.
    monkeypatch.delenv("REPORT_TIMEZONE", raising=False)
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    monkeypatch.setattr(utils_date, "_TZ_ENV_ORIGINAL", "Asia/Tokyo")

    _com_env(tmp_path, monkeypatch, "TZ=Europe/Lisbon\n")

    assert os.environ["TZ"] == "Asia/Tokyo"
    assert tz_name() == "Asia/Tokyo"
    assert os.environ["PGTZ"] == "Asia/Tokyo"


# Controle POSITIVO do 8 e NEGATIVO da frase de precedência do `.env.example`:
# o caso 10 acima cobre só `TZ` × `TZ`, que é justamente a combinação em que o
# ambiente ganha — ela não contradiz nada. A que contradiz é esta: `REPORT_TIMEZONE`
# de ARQUIVO contra `TZ` do ambiente REAL. `_tz()` (utils_date.py:13) lê
# `REPORT_TIMEZONE` primeiro, então o ARQUIVO ganha do ambiente aqui, e
# `align_process_tz` ainda sobrescreve o `TZ` real. Sem este caso, o `.env.example`
# podia continuar dizendo "o ambiente real ganha" sem nada ficar vermelho.
#
# Controle NEGATIVO: inverta a ordem em `_tz()` (`os.getenv("TZ") or
# os.getenv("REPORT_TIMEZONE")`) → os três asserts caem para Europe/Lisbon.
def test_report_timezone_do_arquivo_vence_o_tz_do_ambiente(tmp_path, monkeypatch):
    monkeypatch.delenv("REPORT_TIMEZONE", raising=False)
    # `TZ` do OPERADOR (Railway/docker/shell) — o mesmo cenário do caso 10, e a
    # única diferença é o arquivo trazer `REPORT_TIMEZONE` em vez de `TZ`.
    monkeypatch.setenv("TZ", "Europe/Lisbon")
    monkeypatch.setattr(utils_date, "_TZ_ENV_ORIGINAL", "Europe/Lisbon")

    _com_env(tmp_path, monkeypatch, "REPORT_TIMEZONE=Asia/Tokyo\n")

    assert tz_name() == "Asia/Tokyo"
    assert os.environ["PGTZ"] == "Asia/Tokyo"
    # o `TZ` que o operador pôs foi SOBRESCRITO — é o que a frase do
    # `.env.example` precisa dizer, e o que o "o ambiente real ganha" negava
    assert os.environ["TZ"] == "Asia/Tokyo"


# ── 11. Fuso inválido morre no BOOT, não na primeira query ───────────────────
# O nome vai para `PGTZ`, e um nome inválido derruba a conexão DEPOIS de o health
# check passar: o deploy sobe verde e as queries penduram. Controle NEGATIVO:
# apague o `try/except` + `sys.exit(1)` do fim de `load_app_env` → o primeiro
# assert fica vermelho (sobe `ZoneInfoNotFoundError` crua em vez de `SystemExit`).
# O segundo é o POSITIVO: sem ele, um `sys.exit(1)` incondicional passaria.

def test_fuso_invalido_recusa_o_boot(tmp_path, monkeypatch):
    monkeypatch.delenv("REPORT_TIMEZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(utils_date, "_TZ_ENV_ORIGINAL", None)

    with pytest.raises(SystemExit) as exc:
        _com_env(tmp_path, monkeypatch, "REPORT_TIMEZONE=Nao/Existe\n")
    assert exc.value.code == 1


def test_fuso_valido_nao_recusa_o_boot(tmp_path, monkeypatch):
    monkeypatch.delenv("REPORT_TIMEZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(utils_date, "_TZ_ENV_ORIGINAL", None)

    _com_env(tmp_path, monkeypatch, "REPORT_TIMEZONE=Europe/Lisbon\n")
    assert tz_name() == "Europe/Lisbon"


# ── 12. O unitário do dia de parede ──────────────────────────────────────────
# `REPORT_TIMEZONE` explícito porque a afirmação é sobre São Paulo, não sobre a
# configuração de quem roda. 10/03 00:00Z é 09/03 21:00 em São Paulo.

def test_day_tz_devolve_o_dia_de_parede(monkeypatch):
    monkeypatch.setenv("REPORT_TIMEZONE", "America/Sao_Paulo")
    assert day_tz(datetime(2026, 3, 10, 0, 0, tzinfo=timezone.utc)) == date(2026, 3, 9)


# ── 13. `tzset()` falha CALADO — a guarda que pega isso ──────────────────────
# `tzset()` não levanta quando a libc não conhece o nome: ela cai para UTC sem
# avisar. Com `ZoneInfo` lendo de OUTRO banco de fusos que a libc não tem,
# `align_process_tz` retornava com sucesso e o processo ficava em UTC enquanto o
# app e o `PGTZ` iam para o fuso pedido — o invariante do arquivo inteiro
# quebrado em silêncio, e nenhum dos 12 casos acima pegava.
#
# Hoje isso é inalcançável em produção porque `tzdata` (o banco de fusos em pip)
# NÃO está no requirements.txt e o do sistema existe; vira alcançável no dia em
# que ele entrar, direto ou transitivo. O banco falso reproduz exatamente essa
# divergência.
#
# Controle NEGATIVO: apague o bloco `if agora.astimezone().utcoffset() != ...`
# de `utils_date.align_process_tz` → vermelho (a função devolve "Fake/Zone" com
# o processo em UTC, sem levantar).
# Controle POSITIVO na configuração NORMAL: os casos 8, 10 e 11 chamam
# `align_process_tz` por dentro do `load_app_env` com fusos válidos e continuam
# verdes — a guarda não dá falso positivo.

def test_libc_que_nao_conhece_o_fuso_nao_passa_calada(tmp_path, monkeypatch):
    import zoneinfo

    # Um TZif VÁLIDO sob um nome que a libc não tem: `ZoneInfo("Fake/Zone")`
    # resolve (+09), `tzset()` com TZ=Fake/Zone cai para UTC. Copiado de um
    # arquivo real para não depender de gerar TZif à mão.
    (tmp_path / "Fake").mkdir()
    (tmp_path / "Fake" / "Zone").write_bytes(
        _tzif("Asia", "Tokyo")
    )
    monkeypatch.setenv("REPORT_TIMEZONE", "Fake/Zone")
    zoneinfo.reset_tzpath(to=[str(tmp_path)])
    zoneinfo.ZoneInfo.clear_cache()
    try:
        # a premissa do caso, medida aqui e não presumida
        assert utils_date._tz().key == "Fake/Zone"
        with pytest.raises(RuntimeError, match="libc"):
            utils_date.align_process_tz()
    finally:
        zoneinfo.reset_tzpath()
        zoneinfo.ZoneInfo.clear_cache()


# O caso do P2 do Codex (#180): a zona cujo offset COINCIDE com o do fallback no
# instante do boot. `Europe/London` no inverno é +00:00, igual ao UTC em que a
# libc cai — uma guarda que olhasse só "agora" passaria em janeiro e o app
# divergiria em março, já em produção. Este caso é o que exige os TRÊS instantes.
#
# CONTROLE NEGATIVO: reduzir a guarda de `align_process_tz` a um instante só
# (`agora`) deixa ESTE caso vermelho e o de cima (Tóquio) verde — é a diferença
# exata entre as duas versões da guarda.

def test_libc_que_diverge_so_no_verao_tambem_e_pega(tmp_path, monkeypatch):
    import zoneinfo

    (tmp_path / "Fake").mkdir()
    (tmp_path / "Fake" / "Zone").write_bytes(
        _tzif("Europe", "London")
    )
    monkeypatch.setenv("REPORT_TIMEZONE", "Fake/Zone")
    zoneinfo.reset_tzpath(to=[str(tmp_path)])
    zoneinfo.ZoneInfo.clear_cache()
    try:
        assert utils_date._tz().key == "Fake/Zone"
        # O BOOT acontece no INVERNO — é o que torna o caso discriminante. Sem
        # fixar isto o teste passa por acidente de calendário: rodando em
        # agosto, Londres já diverge no instante presente e a guarda de um
        # instante só bastaria. (Medido: com `_agora` livre, reduzir a guarda a
        # um instante deixava este caso VERDE — teste tautológico.)
        janeiro = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
        monkeypatch.setattr(utils_date, "_agora", lambda: janeiro)

        # A premissa: em janeiro o app dá +00:00, e o fallback da libc é UTC —
        # logo os dois lados CONCORDAM no instante do boot, e só um instante de
        # outra estação pode acusar a divergência. (O lado do processo só vira
        # UTC depois do `tzset()` lá dentro; por isso não dá para afirmá-lo
        # aqui, e afirmar antes era o que quebrava este caso.)
        assert janeiro.astimezone(utils_date._tz()).utcoffset() == timedelta(0)

        with pytest.raises(RuntimeError, match="libc"):
            utils_date.align_process_tz()
    finally:
        zoneinfo.reset_tzpath()
        zoneinfo.ZoneInfo.clear_cache()


# ── 14. `load_app_env` roda em processo JÁ SERVINDO: `TZ` nunca some ─────────
# `adapters/whatsapp/wa_app.py:39` chama `load_app_env()` no import, e esse import
# é tardio de propósito: no monólito ele mora dentro dos wrappers lazy do webhook
# (`_wa_verify`/`_wa_webhook`, 1ª requisição) e dos wrappers de background do
# `lifespan` (1 s depois do startup). Ou seja: ele roda com event
# loop, threadpool e WebSockets ativos. A glibc relê `TZ` a cada `localtime()`,
# então qualquer instante em que `TZ` não esteja no ambiente é uma janela em que
# uma thread concorrente chamando `date.today()` cai no fuso do contêiner (UTC no
# Railway) — o bug deste PR de volta por microssegundos.
#
# Controle NEGATIVO: troque a atribuição de `config/env.py` de volta pelo
# `os.environ.pop("TZ", None)` → vermelho (`removidas == ["TZ"]`).
# Controle POSITIVO: os asserts de baixo são os do caso 8 — provam que matar o
# `pop` não custou a precedência que ele existia para dar ao arquivo.

def test_load_app_env_nunca_deixa_o_ambiente_sem_tz(tmp_path, monkeypatch):
    from collections.abc import MutableMapping

    real = os.environ
    removidas: list[str] = []

    class _Vigia(MutableMapping):
        """`os.environ` de verdade + registro de toda REMOÇÃO de chave.

        Delega TUDO ao objeto real, que é quem chama `putenv` — sem isso o
        `tzset()` de `align_process_tz` não enxergaria o `TZ` novo e o teste
        mediria outra coisa. `pop`/`popitem`/`clear` da `MutableMapping` passam
        todos por `__delitem__`, então espiar só aqui basta.
        """

        def __getitem__(self, k):
            return real[k]

        def __setitem__(self, k, v):
            real[k] = v

        def __delitem__(self, k):
            removidas.append(k)
            del real[k]

        def __iter__(self):
            return iter(real)

        def __len__(self):
            return len(real)

    monkeypatch.delenv("REPORT_TIMEZONE", raising=False)
    # o estado REAL da produção: `TZ` no ambiente posto pelo IMPORT de
    # `utils_date`, não pelo operador — é o único caso em que o código mexe em `TZ`
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    monkeypatch.setattr(utils_date, "_TZ_ENV_ORIGINAL", None)
    # `os.getenv` resolve o global `environ` na CHAMADA, então o patch alcança
    # `config.env`, `utils_date` e qualquer outro leitor durante o `load_app_env`.
    monkeypatch.setattr(os, "environ", _Vigia())

    _com_env(tmp_path, monkeypatch, "REPORT_TIMEZONE=Asia/Tokyo\n")

    assert removidas == [], removidas
    assert real["TZ"] == "Asia/Tokyo"
    assert real["PGTZ"] == "Asia/Tokyo"


# ── 16. A MÁQUINA INTEIRA: 16 combinações × as três pontas ───────────────────
# Cinco rodadas de revisão bateram no mesmo subsistema, cada uma consertando UMA
# transição: `pop` (janela sem `TZ`), atribuição do `TZ` do arquivo (janela com o
# `TZ` errado quando o `REPORT_TIMEZONE` manda), Windows, DST. O §4 do CLAUDE.md
# manda parar de remendar e enumerar estados × eventos. Isto é a enumeração.
#
# Entradas: `REPORT_TIMEZONE` e `TZ`, cada um podendo vir do ambiente REAL ou do
# `.env`, presentes ou ausentes — 2^4 = 16 estados. Precedência documentada:
# `REPORT_TIMEZONE` ganha de `TZ`; dentro de cada um, ambiente real ganha do
# arquivo.
#
# O invariante afirmado em CADA um dos 16: processo, app e sessão do Postgres
# valem o MESMO fuso, e é o efetivo da tabela.
#
# CONTROLE NEGATIVO: trocar a resolução de `config/env.py` de volta por
# `os.environ["TZ"] = merged["TZ"]` deixa vermelhos os estados em que o
# `REPORT_TIMEZONE` do arquivo convive com um `TZ` (de qualquer origem).
# CONTROLE POSITIVO: os estados sem nada setado continuam em America/Sao_Paulo —
# sem eles, um conserto que fixasse tudo num fuso passaria.

_SP_DEFAULT = "America/Sao_Paulo"


@pytest.mark.parametrize("r_env, r_file, t_env, t_file, efetivo", [
    # R real manda, sempre
    ("Asia/Tokyo", None, None, None, "Asia/Tokyo"),
    ("Asia/Tokyo", "Europe/Lisbon", None, None, "Asia/Tokyo"),
    ("Asia/Tokyo", None, "Europe/Lisbon", None, "Asia/Tokyo"),
    ("Asia/Tokyo", None, None, "Europe/Lisbon", "Asia/Tokyo"),
    ("Asia/Tokyo", "Europe/Lisbon", "Etc/GMT+12", "Pacific/Kiritimati", "Asia/Tokyo"),
    ("Asia/Tokyo", "Europe/Lisbon", "Etc/GMT+12", None, "Asia/Tokyo"),
    ("Asia/Tokyo", "Europe/Lisbon", None, "Etc/GMT+12", "Asia/Tokyo"),
    ("Asia/Tokyo", None, "Europe/Lisbon", "Etc/GMT+12", "Asia/Tokyo"),
    # sem R real: o R do ARQUIVO ganha de qualquer TZ
    (None, "Europe/Lisbon", None, None, "Europe/Lisbon"),
    (None, "Europe/Lisbon", "Asia/Tokyo", None, "Europe/Lisbon"),
    (None, "Europe/Lisbon", None, "Asia/Tokyo", "Europe/Lisbon"),
    (None, "Europe/Lisbon", "Asia/Tokyo", "Etc/GMT+12", "Europe/Lisbon"),
    # sem R nenhum: TZ real ganha do TZ de arquivo
    (None, None, "Asia/Tokyo", None, "Asia/Tokyo"),
    (None, None, "Asia/Tokyo", "Europe/Lisbon", "Asia/Tokyo"),
    (None, None, None, "Europe/Lisbon", "Europe/Lisbon"),
    # nada em lugar nenhum: o default do produto (controle POSITIVO)
    (None, None, None, None, _SP_DEFAULT),
])
def test_as_tres_pontas_batem_nos_16_estados(
        tmp_path, monkeypatch, r_env, r_file, t_env, t_file, efetivo):
    monkeypatch.delenv("REPORT_TIMEZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    if r_env:
        monkeypatch.setenv("REPORT_TIMEZONE", r_env)
    if t_env:
        monkeypatch.setenv("TZ", t_env)
    # o que o ambiente REAL trazia — é o que `utils_date` captura no import
    monkeypatch.setattr(utils_date, "_TZ_ENV_ORIGINAL", t_env)

    linhas = ""
    if r_file:
        linhas += f"REPORT_TIMEZONE={r_file}\n"
    if t_file:
        linhas += f"TZ={t_file}\n"
    _com_env(tmp_path, monkeypatch, linhas)

    assert utils_date.tz_name() == efetivo, "o APP"
    assert os.environ["PGTZ"] == efetivo, "a SESSÃO do Postgres"
    assert os.environ["TZ"] == efetivo, "o PROCESSO"
    # e o processo de verdade, não só a variável
    epoch = datetime.fromtimestamp(0, timezone.utc)
    assert epoch.astimezone().utcoffset() == epoch.astimezone(ZoneInfo(efetivo)).utcoffset()


# ── 17. Nenhuma escrita INTERMEDIÁRIA de `TZ` é observável com valor errado ──
# É a generalização do caso 14: ele provava que `TZ` nunca some; este prova que
# `TZ` nunca vale outra coisa. O apontamento que o motivou: com
# `REPORT_TIMEZONE` (precedência maior) setado e um `TZ` no `.env`, a versão
# anterior escrevia o `TZ` do arquivo por um instante — e uma thread concorrente
# chamando `date.today()` nesse instante lia o dia errado (Codex #180, P2).
#
# CONTROLE NEGATIVO: repor `os.environ["TZ"] = merged["TZ"]` antes do
# `setdefault` deixa este caso vermelho, com o valor intruso na mensagem.

def test_tz_nunca_assume_valor_intermediario_errado(tmp_path, monkeypatch):
    from collections.abc import MutableMapping

    real = os.environ
    escritas: list[str] = []

    class _Vigia(MutableMapping):
        def __getitem__(self, k):
            return real[k]

        def __setitem__(self, k, v):
            if k == "TZ":
                escritas.append(v)
            real[k] = v

        def __delitem__(self, k):
            if k == "TZ":
                escritas.append("<APAGADO>")
            del real[k]

        def __iter__(self):
            return iter(real)

        def __len__(self):
            return len(real)

    # O SETUP É O CAMINHO DO DEFEITO, e errar nele foi o que quase deixou este
    # teste medir nada: a escrita intermediária da versão anterior só acontecia
    # com `_TZ_ENV_ORIGINAL is None` — ou seja, `TZ` AUSENTE do ambiente real.
    # Com `TZ` presente, aquele ramo nem executava e o controle negativo dava
    # verde (medido).
    monkeypatch.setenv("REPORT_TIMEZONE", "Asia/Tokyo")
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(utils_date, "_TZ_ENV_ORIGINAL", None)
    monkeypatch.setattr(os, "environ", _Vigia())

    # o `.env` traz um `TZ` de precedência MENOR: ele não pode ser observado
    _com_env(tmp_path, monkeypatch, "TZ=Europe/Lisbon\n")

    assert set(escritas) <= {"Asia/Tokyo"}, (
        f"`TZ` passou por valor intermediário observável: {escritas}")
    assert real["TZ"] == "Asia/Tokyo"


# ── 15. Windows: sem `time.tzset`, o boot não pode morrer ───────────────────
# `time.tzset` é POSIX. O `docs/readme.md` lista Windows como sistema suportado,
# e sem guarda o `ImportError` subia até o `except` de `load_app_env` e virava
# `sys.exit(1)`: NENHUM entrypoint subiria lá, com a mensagem de "fuso inválido"
# — que seria mentira, porque o `ZoneInfo` resolveu o nome. Apontado como P1
# pelo Codex no #180.
#
# CONTROLE NEGATIVO: tirar o `if tzset is None: return name` de
# `align_process_tz` deixa os DOIS casos abaixo vermelhos — o 1º com
# `ImportError`/`AttributeError`, o 2º com `SystemExit`.
# CONTROLE POSITIVO: `test_o_processo_roda_no_fuso_do_app` (caso 2) continua
# verde no Unix, provando que a guarda não desligou o alinhamento onde ele vale.

def test_sem_tzset_o_alinhamento_nao_levanta(monkeypatch):
    """No Windows a SESSÃO ainda é alinhada; o processo, não — e é assumido."""
    monkeypatch.delattr(utils_date.time_module, "tzset", raising=False)

    nome = utils_date.align_process_tz()

    assert nome == utils_date.tz_name()
    assert os.environ["PGTZ"] == nome, "a metade que conserta a #178 tem de valer"


def test_sem_tzset_o_boot_continua_subindo(tmp_path, monkeypatch):
    """O que o P1 quebrava: `load_app_env` matando o processo com exit(1)."""
    monkeypatch.delattr(utils_date.time_module, "tzset", raising=False)
    monkeypatch.setattr(utils_date, "_TZ_ENV_ORIGINAL", None)

    _com_env(tmp_path, monkeypatch, "REPORT_TIMEZONE=America/Sao_Paulo\n")

    assert utils_date.tz_name() == "America/Sao_Paulo"


# ── 18. FONTE ÚNICA: 5 dos 9 lugares que liam o fuso por fora do `utils_date` ─
# Issue #179. São NOVE sites ao todo; CINCO estão fechados nesta árvore e
# QUATRO ficam registrados como dívida no próprio local, com o motivo:
# `billing_commands.py`
# (offset BRT cravado, só exibição), `email_service.py` (idem, e o alias `_tz`
# local colide com o nome da fonte única), `dashboard.js` (`APP_TZ` cravado no
# cliente; fechar é mudar o CONTRATO da API) e `proactive_ai_scheduler.py`
# (`PROACTIVE_AI_HOUR_UTC=3` "= 0h BRT" — o único que não é display: é a hora em
# que o bot FALA com o usuário). Os cinco fechados: `get_spending_trend`
# (db/accounts.py) e três pontos do monólito — `get_financial_data`,
# `get_daily_expenses_window` e `daily_expenses_window` — vêm DESTE PR; o
# quinto, `history_earliest_date` (core/services/plan_service.py), veio do #166
# (8ea113a) pela `main` e subsome a metade que este PR tinha ali: lá a linha é
# `day_tz(now or datetime.now(timezone.utc))`. Os casos 18.1–18.3 continuam
# aqui porque a `main` não cobre essa função (o `test_virada_de_mes.py` só a
# toca de raspão) — e continuam discriminando o `day_tz` do mesmo jeito.
#
# Sem número de linha de propósito: cite símbolo greppável. Uma tabela de
# referências de linha já apodreceu 3× no #166, e este merge sozinho deslocou
# todas as do monólito em +1.
#
# LEIA ISTO ANTES DOS CASOS: quatro dos cinco pontos fechados NÃO mudam um
# byte de saída em produção. Estes casos são GUARDA DE ACOPLAMENTO (§0.7) —
# "quem precisa de fuso lê `utils_date`, e só ele" —, não prova de bug
# consertado. Só o 18.5 cobre um defeito que o usuário via.
#
# Medido no boot real (31/08/2026): `load_app_env` escreve
# `os.environ["TZ"] = <fuso efetivo>` (config/env.py:103) ANTES de qualquer
# leitura — a constante `TZ` do monólito saía de um `os.getenv` logo depois do
# `load_app_env()`, e `plan_service` só é chamado bem depois.
# Com `REPORT_TIMEZONE=Asia/Tokyo TZ=Etc/UTC`, a leitura ANTIGA
# (`os.getenv("TZ")`) e a NOVA (`tz_name()`) devolvem as duas `Asia/Tokyo`.
# É essa medição do boot — não a contraprova abaixo — que sustenta o "não é
# verdade que `REPORT_TIMEZONE` sozinho passou a mover as três pontas": ele já
# movia todas menos uma.
#
# Contraprova em duas colunas com `REPORT_TIMEZONE=Pacific/Kiritimati`, suíte
# CHEIA, medida em 01/09/2026 na árvore já mergeada com a `main` e1b4633:
# `main` e branch dão os MESMOS 15 vermelhos, com listas idênticas por NOME nos
# dois sentidos (`comm` vazio dos dois lados). Zero vermelho novo, e zero
# vermelho da `main` virando verde — os 7 casos novos desta seção somam-se aos
# verdes (5.329 → 5.336) sem trocar a cor de nenhum caso antigo. Uma das quatro
# execuções deu 16 na `main`:
# `test_routes_categories.py::test_nota_alvo_e_criado_em_saem_na_resposta`
# oscila entre execuções nas DUAS árvores (é a oscilação de baseline que o
# CLAUDE.md §3 descreve), então o número estável é 15.
#
# O ponto com efeito observável é UM: o literal `'America/Sao_Paulo'` cravado
# em `get_spending_trend` (db/accounts.py), que ignorava as duas variáveis
# (caso 18.5). Os outros quatro — `history_earliest_date` (18.1/18.2),
# `get_financial_data` (18.6), `get_daily_expenses_window` (18.4) e
# `daily_expenses_window` (18.7) — valem pelo dia em que o
# alinhamento do boot mudar de ordem ou alguém setar `TZ` depois dele.
#
# Como eles conseguem discriminar, então: o `TZ` é setado À MÃO depois do
# import, o que desfaz a reescrita do `load_app_env` e recria uma divergência
# entre as duas variáveis. O `.env.example:31-32` traz as duas PRESENTES E
# IGUAIS, então essa divergência é montada aqui, não herdada — o que o Railway
# tem de fato NÃO foi verificado por ninguém. Sem essa reescrita todo caso aqui
# ficaria tautológico — e é por isso que cada um importa o módulo ANTES do
# monkeypatch.
#
# Instante LITERAL em 18.1, 18.2, 18.3, 18.4 e 18.6. Leem o RELÓGIO o 18.5
# (`date.today()`, para o rótulo do mês) e o 18.7 (`datetime.now`, para o dia
# corrente em dois fusos) — os comentários de cada um dizem por quê.
#
# CONTROLE contra hardcode (rodada 3, medido em 31/08/2026; RE-MEDIDO em
# 02/09/2026 na árvore mergeada): SEIS dos sete casos ficam VERMELHOS se o
# ponto de produção correspondente cravar `ZoneInfo("America/Sao_Paulo")` — por
# isso o fuso ESPERADO de todos eles é um fuso que NÃO é São Paulo. O sétimo
# (18.7) fica vermelho em 17 das 24 horas do dia, pela razão que o comentário
# dele mede; o controle dele que vale a qualquer hora é a coluna da direita. A
# matriz, por implementação:
#
#   caso            árvore atual       hardcode SP   leitura antiga `TZ`
#   18.1/18.2       verde              VERMELHO      VERMELHO
#   18.3            verde              VERMELHO      verde (é o positivo)
#   18.4            verde              VERMELHO      VERMELHO
#   18.5            verde              VERMELHO      VERMELHO
#   18.6            verde              VERMELHO      VERMELHO
#   18.7            verde              VERMELHO (*)  VERMELHO
#
#   (*) só entre 07:00 e 24:00 de São Paulo — ver o comentário do 18.7.
#
# O 18.3 verde na coluna do meio é o CONTROLE POSITIVO do grupo: ele afirma que
# sem `REPORT_TIMEZONE` o `TZ` continua valendo. Um conserto que passasse a
# ignorar o `TZ` derrubaria só ele.

def _limites_do_tier(monkeypatch, tier: str) -> None:
    """Fixa os limites do plano sem tocar no banco (padrão de
    tests/test_plan_tiers.py:250-269, que patcha a leitura do usuário)."""
    from core.services import plan_service

    monkeypatch.setattr(
        plan_service, "get_user_limits", lambda uid: plan_service.limits_for_tier(tier)
    )


def _historico_desde(tier: str, monkeypatch) -> date:
    from core.services import plan_service

    _limites_do_tier(monkeypatch, tier)
    # 01:00Z de 01/09 = 22:00 de 31/08 em São Paulo: o dia (e o mês) divergem.
    return plan_service.history_earliest_date(1, datetime(2026, 9, 1, 1, 0, tzinfo=timezone.utc))


# 18.1/18.2 — O par divergente é `REPORT_TIMEZONE=Pacific/Kiritimati` (UTC+14)
# contra `TZ=Etc/GMT+12` (UTC−12), e NENHUM dos dois é São Paulo de propósito:
# no instante do teste (01:00Z de 01/09) Kiritimati está em 01/09 enquanto São
# Paulo (−3) e Etc/GMT+12 estão os DOIS em 31/08. Isso dá três respostas
# distintas e permite discriminar as três implementações de uma vez.
#
# O ALVO destes dois casos é o `day_tz(...)` que o #166 pôs em
# `history_earliest_date` (core/services/plan_service.py) — não o `_tz()` que
# este branch tinha ali antes do merge. Controle NEGATIVO RE-MEDIDO na árvore
# mergeada (01/09/2026), duas variantes, trocando essa linha por:
#   - leitura ANTIGA, `.astimezone(ZoneInfo(os.getenv("TZ",
#     "America/Sao_Paulo"))).date()` → grátis 2026-08-01, essencial
#     2026-06-02. Os dois vermelhos (18.3 fica verde: é o positivo).
#   - hardcode, `.astimezone(ZoneInfo("America/Sao_Paulo")).date()` → os mesmos
#     2026-08-01 e 2026-06-02, e o 18.3 também vermelho. Três vermelhos.
# Com a árvore como está: 3 passed.
# O que 18.1/18.2 provam, então, é que a linha lê `REPORT_TIMEZONE` — não só
# que ela deixou de ler `TZ`. Em produção as duas leituras dão o mesmo, porque
# o boot alinha `TZ` (o cabeçalho acima); a divergência aqui é montada à mão.

def test_o_historico_do_gratis_corta_pelo_report_timezone(monkeypatch):
    monkeypatch.setenv("REPORT_TIMEZONE", "Pacific/Kiritimati")  # UTC+14, sem DST
    monkeypatch.setenv("TZ", "Etc/GMT+12")                       # UTC-12, sem DST
    assert _historico_desde("free", monkeypatch) == date(2026, 9, 1)


def test_os_90_dias_do_essencial_contam_do_dia_local(monkeypatch):
    monkeypatch.setenv("REPORT_TIMEZONE", "Pacific/Kiritimati")
    monkeypatch.setenv("TZ", "Etc/GMT+12")
    assert _historico_desde("essencial", monkeypatch) == date(2026, 6, 3)


# 18.3 — Controle POSITIVO do par: com `REPORT_TIMEZONE` AUSENTE o `TZ` continua
# valendo, isto é, o conserto não trocou uma variável pela outra nem cravou nada.
# O `TZ` aqui também NÃO é São Paulo, e é isso que separa este caso do que ele
# era na rodada 2: com `TZ=America/Sao_Paulo` ele passava sob um hardcode de São
# Paulo e não excluía implementação nenhuma. Com Kiritimati:
#   - hardcode `ZoneInfo("America/Sao_Paulo")` → 2026-08-01 / 2026-06-02, VERMELHO;
#   - leitura ANTIGA `os.getenv("TZ")` → 2026-09-01 / 2026-06-03, VERDE — que é
#     exatamente a afirmação positiva: sem `REPORT_TIMEZONE`, nada muda.

def test_sem_report_timezone_o_historico_segue_o_tz(monkeypatch):
    monkeypatch.delenv("REPORT_TIMEZONE", raising=False)
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    assert _historico_desde("free", monkeypatch) == date(2026, 9, 1)
    assert _historico_desde("essencial", monkeypatch) == date(2026, 6, 3)


# 18.4 — A janela diária do gráfico (`AT TIME ZONE` interpolado no SQL do
# monólito). Ramo `start_date` de propósito: o outro é `NOW() - INTERVAL`, que
# depende do relógio. Guarda de acoplamento, como o cabeçalho explica.
#
# O par divergente é o mesmo de 18.1/18.2 e pela mesma razão: no instante do
# lançamento (02:00Z de 15/08) Kiritimati está em 15/08 enquanto São Paulo e
# Etc/GMT+12 estão os dois em 14/08.
#
# Controle NEGATIVO, duas variantes, medidas em 31/08/2026 nos dois
# `AT TIME ZONE '{tz_name()}'` de `get_daily_expenses_window`
# (frontend/finance_bot_websocket_custom.py):
#   - leitura ANTIGA, o `{tz_name()}` de volta para
#     `{os.getenv("TZ", "America/Sao_Paulo")}` → o dia sai 2026-08-14, VERMELHO;
#   - hardcode, `AT TIME ZONE 'America/Sao_Paulo'` → 2026-08-14, VERMELHO.
# A constante `TZ` do módulo NÃO serve de controle: era avaliada no import e
# congelava o valor (é justamente por isso que ela saiu).

def _janela_diaria(uid: int, start_date: date) -> list[dict]:
    import frontend.finance_bot_websocket_custom as dashboard

    return _no_pool_do_dashboard(
        lambda: dashboard.get_daily_expenses_window(uid, start_date=start_date)
    )


def test_a_janela_diaria_do_grafico_segue_o_report_timezone(pro_user_id, monkeypatch):
    # Importar ANTES do monkeypatch: `load_app_env()` roda no import do monólito
    # e reescreve `TZ` e `REPORT_TIMEZONE` (config/env.py:98,103). Importado
    # depois, ele apagaria a divergência e o caso passaria até revertido —
    # medido em 31/08/2026, foi exatamente o que aconteceu na primeira versão.
    import frontend.finance_bot_websocket_custom  # noqa: F401

    # 02:00Z de 15/08 = 16:00 de 15/08 em Kiritimati, 23:00 de 14/08 em São
    # Paulo e 14:00 de 14/08 em Etc/GMT+12.
    _gasto(pro_user_id, datetime(2026, 8, 15, 2, 0, tzinfo=timezone.utc), "02:00Z de 15/08")
    monkeypatch.setenv("REPORT_TIMEZONE", "Pacific/Kiritimati")  # UTC+14, sem DST
    monkeypatch.setenv("TZ", "Etc/GMT+12")                       # UTC-12, sem DST

    dados = _janela_diaria(pro_user_id, date(2026, 8, 1))
    assert [d["date"] for d in dados] == ["2026-08-15"], dados


# 18.5 — `get_spending_trend`, o ÚNICO ponto que ignorava as duas variáveis
# (literal 'America/Sao_Paulo' × 2) e portanto o único cuja correção muda a
# saída de produção quando `REPORT_TIMEZONE` está setado. Por isso o divergente
# aqui é o `REPORT_TIMEZONE`, e não o `TZ`: contra um literal qualquer um dos
# dois serve de controle, e o `REPORT_TIMEZONE` é o que tem precedência.
#
# O instante é literal (23:00 do último dia do mês passado, em São Paulo = dia 1
# do mês corrente, 02:00Z); o mês CORRENTE entra só para a janela de `months=2`
# do próprio `get_spending_trend` cobrir o lançamento em qualquer data de
# execução — a afirmação é sobre o RÓTULO, não sobre o relógio.
#
# Controle NEGATIVO (medido em 31/08/2026): volte os dois `tz_name()` de
# `get_spending_trend` (db/accounts.py) para `"America/Sao_Paulo"` → o rótulo
# cai no mês PASSADO e
# o caso fica vermelho.

def test_a_tendencia_mensal_segue_o_report_timezone(pro_user_id, monkeypatch):
    hoje = date.today()
    ultimo_do_mes_passado = date(hoje.year, hoje.month, 1) - timedelta(days=1)
    _gasto(
        pro_user_id,
        datetime.combine(ultimo_do_mes_passado, time(23, 0), tzinfo=_SP),
        "23h do ultimo dia do mes passado",
    )
    monkeypatch.setenv("TZ", "America/Sao_Paulo")
    monkeypatch.setenv("REPORT_TIMEZONE", "Etc/UTC")

    linhas = db.get_spending_trend(pro_user_id, months=2)
    assert len(linhas) == 1, linhas
    assert (linhas[0]["year"], linhas[0]["month"]) == (hoje.year, hoje.month), linhas


# 18.6 — O gráfico de barras "gastos por dia do mês" (`get_financial_data`, no
# monólito). É o outro `AT TIME ZONE` interpolado, e o que a rodada 1 deixou
# SEM teste: revertendo só este ponto, os 41 casos deste arquivo continuavam
# verdes, e com sabotagem bruta (`AT TIME ZONE 'Etc/GMT+12'`) os 6 arquivos que
# citam a função davam 103 passed / 0 failed.
#
# Mesmo par divergente e mesmo instante do 18.4. Controle NEGATIVO, duas
# variantes, medidas em 31/08/2026 no `AT TIME ZONE` dessa função:
#   - leitura ANTIGA, `{os.getenv("TZ", "America/Sao_Paulo")}` → o dia sai 14,
#     VERMELHO;
#   - hardcode, `America/Sao_Paulo` → o dia sai 14, VERMELHO.

def test_o_grafico_de_gastos_por_dia_segue_o_report_timezone(pro_user_id, monkeypatch):
    import frontend.finance_bot_websocket_custom as dashboard  # antes do monkeypatch (18.4)

    # 02:00Z de 15/08 = 16:00 de 15/08 em Kiritimati (14/08 em SP e em GMT+12).
    _gasto(pro_user_id, datetime(2026, 8, 15, 2, 0, tzinfo=timezone.utc), "02:00Z de 15/08")
    monkeypatch.setenv("REPORT_TIMEZONE", "Pacific/Kiritimati")  # UTC+14, sem DST
    monkeypatch.setenv("TZ", "Etc/GMT+12")                       # UTC-12, sem DST

    dados = _no_pool_do_dashboard(
        lambda: dashboard.get_financial_data(pro_user_id, year=2026, month=8)
    )["daily_expenses"]
    assert [d["day"] for d in dados] == [15], dados


# 18.7 — O início da janela rolante da rota `/expenses/daily/{uid}`
# (`daily_expenses_window`, no monólito), o outro ponto que a rodada 1 deixou
# sem teste.
#
# Este caso lê o RELÓGIO (o 18.5 também, para outra pergunta), porque o que ele
# pergunta é "que dia é hoje no fuso do app?" — e a resposta só diverge entre
# dois fusos que
# estejam em datas diferentes AGORA. Por isso o par não é São Paulo × UTC (que
# só divergem em 3h de cada 24) e sim +14 × −12: 26 horas de distância, ou seja,
# datas SEMPRE diferentes, a qualquer hora de execução. O esperado é calculado
# aqui com `ZoneInfo` cru, não com `today_tz()`, senão o caso afirmaria a função
# que está medindo.
#
# `get_daily_expenses_window` é substituída de propósito: o que se mede é o
# ARGUMENTO que a rota calcula, não o SQL (esse é o 18.4).
#
# Controle NEGATIVO, duas variantes, medidas em 31/08/2026 no
# `local_today = today_tz()` dessa rota:
#   - leitura ANTIGA, `datetime.now(ZoneInfo(os.getenv("TZ",
#     "America/Sao_Paulo"))).date()` → `start_date` sai um ou dois dias mais
#     cedo, VERMELHO;
#   - hardcode, `datetime.now(ZoneInfo("America/Sao_Paulo")).date()` →
#     VERMELHO em 17 das 24 horas, VERDE nas outras 7. SP (−3) e Kiritimati
#     (+14) estão a 17 horas de distância, não a 26: eles COMPARTILHAM a data
#     enquanto o relógio de SP marca entre 00:00 e 07:00. MEDIDO em 02/09/2026
#     às 00:06 de SP (mesma data nos dois): este foi o ÚNICO dos sete casos a
#     passar sob o hardcode. Quem discrimina a QUALQUER hora é a leitura antiga
#     acima — `TZ=Etc/GMT+12` está a 26h de Kiritimati, datas sempre diferentes
#     —, e é ela o controle negativo declarado deste caso.

def test_a_janela_rolante_comeca_no_dia_local(pro_user_id, monkeypatch):
    import frontend.finance_bot_websocket_custom as dashboard  # antes do monkeypatch (18.4)

    monkeypatch.setenv("REPORT_TIMEZONE", "Pacific/Kiritimati")  # UTC+14, sem DST
    monkeypatch.setenv("TZ", "Etc/GMT+12")                       # UTC-12, sem DST

    capturado = {}

    async def _fake(user_id, days, start_date=None):
        capturado.update(user_id=user_id, days=days, start_date=start_date)
        return []

    monkeypatch.setattr(dashboard, "get_daily_expenses_window", _fake)

    resp = _cliente().get(
        f"/expenses/daily/{pro_user_id}?days=7",
        headers={"Authorization": f"Bearer {make_dashboard_token(pro_user_id, hours=1)}"},
    )
    assert resp.status_code == 200, resp.text
    hoje_la = datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    assert capturado["start_date"] == hoje_la - timedelta(days=7), (capturado, hoje_la)


def _cliente():
    from fastapi.testclient import TestClient

    import frontend.finance_bot_websocket_custom as dashboard

    return TestClient(dashboard.app)

