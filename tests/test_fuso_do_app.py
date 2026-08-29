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

Este arquivo é o único que PROVA o alinhamento; os outros o tomam como premissa
(ver o cabeçalho de tests/test_category_launches_query.py). Controle negativo de
cada caso está no comentário logo acima dele.

O que os casos leem do ambiente são DUAS coisas, e elas não andam juntas:

- o RELÓGIO (que dia é hoje): só os casos 3, 4, 5 e 7, que chamam `today_tz()` e
  combinam o resultado com `_SP` fixo. Os outros — 1, 2, 6 e 8-13 — usam
  instante aware LITERAL, o epoch, ou nenhuma data;
- o FUSO configurado: os casos 3, 4, 5, 6 e 7. Medido em 29/08/2026,
  `REPORT_TIMEZONE=Asia/Yangon` deixa esses CINCO vermelhos — no caso 6 isso é o
  controle negativo declarado dele, não um defeito.

Ler o dia corrente basta para o que 3, 4, 5 e 7 medem: a fronteira do dia contra
a JANELA que o produto abre, e essa janela é sempre "hoje" no fuso do app — um
dia fixo do passado trocaria o dia sem trocar a pergunta. Quem faz a mesma
pergunta com data literal é o caso 6 (31/08/2025 23:00 em São Paulo).
"""
from __future__ import annotations

import asyncio
import os
import pathlib
from time import tzset
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
    tzset()


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


def _tz_da_sessao_async_do_dashboard() -> str:
    import frontend.routes.shared as shared

    async def _ler():
        # Pool NOVO neste event loop e devolvido depois: o `_db_pool` global pode
        # ter sido aberto por outro teste, num loop já encerrado.
        anterior, shared._db_pool = shared._db_pool, None
        try:
            async with await shared.db_connect() as conn:
                cur = await conn.execute("select current_setting('TimeZone') as tz")
                return (await cur.fetchone())["tz"]
        finally:
            if shared._db_pool is not None:
                await shared._db_pool.close()
            shared._db_pool = anterior

    return asyncio.run(_ler())


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
        (pathlib.Path("/usr/share/zoneinfo") / "Asia" / "Tokyo").read_bytes()
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
        (pathlib.Path("/usr/share/zoneinfo") / "Europe" / "London").read_bytes()
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
# é tardio de propósito (`frontend/finance_bot_websocket_custom.py:1952-1963`, 1ª
# requisição; `:1632-1656`, 1 s depois do startup). Ou seja: ele roda com event
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


def _cliente():
    from fastapi.testclient import TestClient

    import frontend.finance_bot_websocket_custom as dashboard

    return TestClient(dashboard.app)

