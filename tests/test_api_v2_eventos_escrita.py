"""Toda escrita financeira avisa o `/painel`: trigger `pb_aviso_escrita` + LISTEN.

Postgres real, a tarefa `escutar_banco` rodando no loop do teste e a escrita
numa thread de verdade (`asyncio.to_thread`), como o WhatsApp faz. `avisar` é
espiado para saber QUEM foi avisado; nos casos de stream, o aviso é lido do SSE.

"Nada chegou" não é medido com sono: a `barreira` manda um NOTIFY de sentinela
depois da escrita, e o Postgres entrega na ordem dos commits — quando a
sentinela chega, o que a escrita tinha de avisar já chegou.

Controle positivo: o dono recebe (conversa, OF, plano, merge). Negativos:
leitura, rollback, outra coluna de `auth_accounts` e o stream do vizinho.
"""
import asyncio
import contextlib
import time
import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import libera, sessao_de
from _apoio_sse import Pedido
from api.v2 import eventos
from conftest import promote_to_pro
from db.schema import TABELAS_QUE_AVISAM
from tests._fusao_of_helpers import ia_fora, manda, uid_pro  # noqa: F401 (fixtures)
from tests.test_manual_launches_carteira_piggy import _connect_fake_bank

SENTINELA = -1
TUDO = b'data: {"recurso":"tudo"}\n\n'

# Têm `user_id` e NÃO avisam. Fora por decisão do dono (2026-09-30): pendências
# e controles de envio, `pix_*`, auth/usuário (o plano avisa por trigger à parte,
# ver `test_plano_avisa_e_login_nao`), `financial_spaces`. O resto não é dado
# financeiro que o `/painel` mostre (IA, agentes, afiliados, sessão, logs).
NAO_AVISAM = {
    "pending_actions", "budget_alert_sent", "recurring_suggestion_dismissed",
    "ofx_imports", "open_finance_item_registry", "pix_charges", "financial_spaces",
    "auth_accounts", "auth_identities", "auth_login_events", "auth_refresh_tokens",
    "auth_sessions", "dashboard_sessions", "data_export_tokens", "link_codes",
    "mfa_login_challenges", "password_reset_tokens", "platform_onboarding_tokens",
    "user_identities", "user_mfa", "user_mfa_backup_codes", "plan_grants", "plan_trials",
    "push_tokens", "daily_report_prefs", "checkout_funnel_events", "ebook_entregas", "affiliates",
    "stripe_email_pendente", "remarketing_regua", "remarketing_envios", "guia_painel", "agents", "agent_events", "ai_fallback_log", "ai_messages", "ai_pending_actions",
    "ai_proactive_cache", "audit_events", "system_event_logs",
}


def _sql(query, *args):
    with db.get_conn() as conn:
        cur = conn.execute(query, args)
        rows = cur.fetchall() if cur.description else None
        conn.commit()
    return rows


async def _ate(cond, prazo=10.0):
    fim = asyncio.get_running_loop().time() + prazo
    while not cond():
        assert asyncio.get_running_loop().time() < fim, "não chegou no prazo"
        await asyncio.sleep(0.01)


async def barreira(avisados):
    n = avisados.count(SENTINELA)
    await asyncio.to_thread(_sql, "select pg_notify('pb_escrita', %s)", str(SENTINELA))
    await _ate(lambda: avisados.count(SENTINELA) > n)


@contextlib.asynccontextmanager
async def ouvindo(monkeypatch):
    """Sobe `escutar_banco` e só entrega depois do LISTEN (a sentinela inscrita
    recebe o `tudo` de reconexão). Devolve a lista de uids avisados."""
    avisados = []
    real = eventos.avisar

    def espia(uid, recurso):
        avisados.append(uid)
        real(uid, recurso)

    monkeypatch.setattr(eventos, "avisar", espia)
    eventos._inscritos[SENTINELA] = set()
    tarefa = asyncio.create_task(eventos.escutar_banco())
    try:
        await _ate(lambda: SENTINELA in avisados)
        yield avisados
    finally:
        tarefa.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarefa
        eventos._inscritos.pop(SENTINELA, None)


def pedido(uid):
    cookie = {dashboard.DASHBOARD_COOKIE_NAME: sessao_de(uid)["dashboard"]}
    return Pedido(dashboard.app, "/api/v2/eventos", cookies=cookie)


async def esvazia(p):
    """Lê o que já estiver no stream; devolve quantos avisos vieram."""
    n = 0
    with contextlib.suppress(TimeoutError):
        while True:
            assert await p.ler(prazo=1) == TUDO
            n += 1
    return n


def novo_uid():
    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    return uid


def launches(uid):
    return _sql("select count(*) n from launches where user_id = %s", uid)[0]["n"]


# ── 1. Conversa pelo WhatsApp ────────────────────────────────────────────────

@pytest.mark.parametrize("texto", [
    "gastei 50 no mercado",
    "gastei 12,90 no açaí da esquina",
    "gastei 20 no uber e 15 na padaria",
])
def test_conversa_avisa_o_dono_e_leitura_nao(uid_pro, ia_fora, monkeypatch, texto):
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    libera(monkeypatch, uid_pro)

    async def cena():
        p = await pedido(uid_pro).abrir()
        async with ouvindo(monkeypatch) as avisados:
            assert await p.ler() == TUDO  # o de reconexão, logo depois do LISTEN
            antes = launches(uid_pro)
            await asyncio.to_thread(manda, uid_pro, texto)
            gravou = launches(uid_pro) - antes
            await barreira(avisados)
            no_stream = await esvazia(p)
            depois_do_gasto = avisados.count(uid_pro)

            resposta = await asyncio.to_thread(manda, uid_pro, "saldo")
            await barreira(avisados)
            with pytest.raises(TimeoutError):
                await p.ler(prazo=1)
            leitura = avisados.count(uid_pro) - depois_do_gasto
        await p.fechar()
        return gravou, no_stream, leitura, resposta, set(avisados)

    gravou, no_stream, leitura, resposta, quem = asyncio.run(cena())
    assert gravou >= 1, "a conversa não gravou: o teste não mede aviso de escrita"
    assert no_stream >= 1
    assert leitura == 0, f"'saldo' avisou ({resposta!r})"
    assert quem <= {uid_pro, SENTINELA}


# ── 2. Isolamento e troca de dono ────────────────────────────────────────────

def test_escrita_de_a_nao_chega_ao_stream_de_b(monkeypatch, user_id):
    a, b = user_id, novo_uid()
    promote_to_pro(a)
    promote_to_pro(b)
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{b}")

    async def cena():
        pa, pb = await pedido(a).abrir(), await pedido(b).abrir()
        async with ouvindo(monkeypatch) as avisados:
            assert await pa.ler() == TUDO and await pb.ler() == TUDO  # os de reconexão
            del avisados[:]
            await asyncio.to_thread(db.add_launch_and_update_balance, a, "receita", 10, None, "x")
            await barreira(avisados)
            recebeu_a = await pa.ler()
            with pytest.raises(TimeoutError):
                await pb.ler(prazo=1)
        await pa.fechar()
        await pb.fechar()
        return recebeu_a, avisados.count(b)

    recebeu_a, avisos_b = asyncio.run(cena())
    assert recebeu_a == TUDO
    assert avisos_b == 0


def test_troca_de_dono_avisa_os_dois(monkeypatch, user_id):
    origem, destino = novo_uid(), user_id
    db.add_launch_and_update_balance(origem, "receita", 10, None, "da origem")

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            await asyncio.to_thread(db.merge_users, origem, destino)
            await barreira(avisados)
            merge = (avisados.count(origem), avisados.count(destino))
            # O UPDATE cru isola o trigger: o merge também apaga a origem.
            solto_id = (await asyncio.to_thread(
                db.add_launch_and_update_balance, destino, "receita", 5, None, "solto"))[0]
            outro = novo_uid()
            del avisados[:]
            await asyncio.to_thread(
                _sql, "update launches set user_id = %s where id = %s", outro, solto_id)
            await barreira(avisados)
            return merge, (avisados.count(destino), avisados.count(outro))

    merge, update = asyncio.run(cena())
    assert all(merge), merge
    assert all(update), update


# ── 3. Rollback não avisa ────────────────────────────────────────────────────

def test_escrita_que_estoura_nao_avisa_nem_grava(monkeypatch, user_id):
    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
                await asyncio.to_thread(
                    db.create_investment_db, user_id, "CDB Estouro", 1.0, "yearly",
                    initial_amount=100)
            await barreira(avisados)
            estouro = avisados.count(user_id)
            await asyncio.to_thread(db.create_investment_db, user_id, "CDB Ok", 1.0, "yearly")
            await barreira(avisados)
            return estouro, avisados.count(user_id)

    estouro, depois = asyncio.run(cena())
    assert estouro == 0
    assert depois >= 1, "controle positivo: a escrita que commita avisa"
    nomes = [r["name"] for r in _sql("select name from investments where user_id = %s", user_id)]
    assert nomes == ["CDB Ok"]


# ── 4. Filha do Open Finance ─────────────────────────────────────────────────

def test_transacao_de_open_finance_avisa_o_dono_da_conexao(monkeypatch, user_id):
    _connect_fake_bank(user_id)
    conta = db.list_bank_accounts(user_id)[0]["id"]

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            await asyncio.to_thread(
                _sql,
                "insert into open_finance_transactions (account_id, provider_transaction_id,"
                " description, amount, transaction_date) values (%s, 'tx-aviso', 'Mercado', -10, %s)",
                conta, date(2026, 9, 1))
            await barreira(avisados)
            return set(avisados)

    assert asyncio.run(cena()) == {SENTINELA, user_id}


# ── 5. Plano ─────────────────────────────────────────────────────────────────

def test_plano_avisa_e_login_nao(monkeypatch, user_id):
    promote_to_pro(user_id)

    async def cena():
        async with ouvindo(monkeypatch) as avisados:
            await asyncio.to_thread(
                _sql, "update auth_accounts set last_activity_at = now() where user_id = %s", user_id)
            await barreira(avisados)
            login = avisados.count(user_id)
            await asyncio.to_thread(
                _sql, "update auth_accounts set plan = 'essencial' where user_id = %s", user_id)
            await barreira(avisados)
            return login, avisados.count(user_id)

    login, plano = asyncio.run(cena())
    assert login == 0
    assert plano == 1


# ── 6. Enumeração: a lista do schema contra o banco ─────────────────────────

def test_toda_tabela_com_user_id_esta_classificada():
    com_user_id = {r["table_name"] for r in _sql(
        "select c.table_name from information_schema.columns c"
        " join information_schema.tables t using (table_schema, table_name)"
        " where c.table_schema = current_schema() and c.column_name = 'user_id'"
        " and t.table_type = 'BASE TABLE'")}
    avisam_por_user_id = {t for t, dono in TABELAS_QUE_AVISAM.items() if dono is None}
    assert com_user_id - avisam_por_user_id - NAO_AVISAM == set(), "tabela nova sem decisão"
    # `system_event_logs` nasce no startup do app (`core/admin_dashboard.py`), não no schema.
    assert NAO_AVISAM - com_user_id <= {"system_event_logs"}, "NAO_AVISAM apodreceu"
    assert avisam_por_user_id - com_user_id == set()
    assert not NAO_AVISAM & set(TABELAS_QUE_AVISAM)


def test_toda_tabela_da_lista_tem_o_trigger():
    def com(nome):
        return {r["t"] for r in _sql(
            "select tgrelid::regclass::text t from pg_trigger where tgname = %s", nome)}

    assert com("trg_pb_aviso_escrita") == set(TABELAS_QUE_AVISAM)
    assert com("trg_pb_aviso_plano") == {"auth_accounts"}


# ── 7. Queda do LISTEN ───────────────────────────────────────────────────────

def test_queda_do_listen_avisa_os_abertos_e_volta(monkeypatch, user_id):
    promote_to_pro(user_id)
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    libera(monkeypatch, user_id)
    monkeypatch.setattr(eventos, "BATIMENTO_S", 0.2)

    def derruba():
        return _sql(
            "select pg_terminate_backend(pid) ok from pg_stat_activity"
            " where application_name = 'pb_escrita_listen' and datname = current_database()")

    async def cena():
        p = await pedido(user_id).abrir()
        async with ouvindo(monkeypatch) as avisados:
            assert await p.ler() == TUDO
            assert [r["ok"] for r in await asyncio.to_thread(derruba)] == [True]
            await _ate(lambda: avisados.count(SENTINELA) >= 2)  # reconectou
            refeito = await p.ler()
            await asyncio.to_thread(db.add_launch_and_update_balance, user_id, "receita", 1, None, "x")
            await barreira(avisados)
            depois = await p.ler()
        await p.fechar()
        return refeito, depois

    assert asyncio.run(cena()) == (TUDO, TUDO)


def test_lifespan_sobe_o_ouvinte_sem_as_tarefas_de_fundo_e_fecha_ao_sair(monkeypatch):
    """O `escutar_banco` não é job: sobe também com `RUN_BACKGROUND_TASKS=0`
    (`dashboard_dev`), e a conexão fecha no teardown (o conftest derruba o banco)."""

    monkeypatch.setattr(dashboard, "RUN_BACKGROUND_TASKS", False)

    def ouvintes():
        return _sql("select count(*) n from pg_stat_activity where application_name ="
                    " 'pb_escrita_listen' and datname = current_database()")[0]["n"]

    def espera(n):
        for _ in range(200):
            if ouvintes() == n:
                return n
            time.sleep(0.05)
        return ouvintes()

    with TestClient(dashboard.app):
        durante = espera(1)
    assert (durante, espera(0)) == (1, 0)


class _ConnFalsa:
    """Aceita o `connect` e o LISTEN; fica de pé `batimentos` batimentos sem aviso
    e depois cai no `notifies`, como um proxy que derruba a sessão."""

    def __init__(self, batimentos=0):
        self.batimentos = batimentos

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql):
        pass

    async def notifies(self, timeout):
        if self.batimentos <= 0:
            raise OSError("derrubada")
        self.batimentos -= 1
        return
        yield  # gerador assíncrono: um batimento sem aviso


def _relogio_falso(monkeypatch, esperas, ate):
    relogio = [0.0]

    async def dorme(s):
        esperas.append(s)
        relogio[0] += s
        if relogio[0] > ate:
            raise asyncio.CancelledError

    monkeypatch.setattr(eventos, "asyncio", SimpleNamespace(sleep=dorme))
    monkeypatch.setattr(eventos, "time", SimpleNamespace(monotonic=lambda: relogio[0]))


async def _recusa(*a, **kw):
    raise OSError("recusada")


async def _derruba_depois_do_listen(*a, **kw):
    return _ConnFalsa()


@pytest.mark.parametrize("connect", [_recusa, _derruba_depois_do_listen])
def test_listen_caido_para_sempre_reloga_a_cada_intervalo(monkeypatch, capsys, connect):
    """Queda sem volta: loga na 1ª falha, cala até `RELOG_CAIDO_S` e loga de novo.
    Espera 1,2,4…60 → falhas em t=0,1,3,7,15,31,63,123,183,243. Vale também quando
    o connect e o LISTEN passam e a sessão cai logo depois: sem um batimento de pé,
    é a mesma queda (apontamento do Codex no #691)."""
    esperas = []
    _relogio_falso(monkeypatch, esperas, ate=250)
    monkeypatch.setattr(eventos.psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(eventos, "RELOG_CAIDO_S", 100.0)
    with pytest.raises(asyncio.CancelledError):
        eventos.escutar_banco().send(None)  # `dorme` nunca suspende: roda sem loop
    assert esperas == [1, 2, 4, 8, 16, 32, 60, 60, 60, 60]
    assert [linha.split(":")[0] for linha in capsys.readouterr().err.splitlines()] == [
        "[eventos] LISTEN pb_escrita caiu",
        "[eventos] LISTEN pb_escrita segue caído há 123 s",
        "[eventos] LISTEN pb_escrita segue caído há 243 s",
    ]


def test_um_batimento_de_pe_encerra_a_queda(monkeypatch, capsys):
    """Cai → volta e passa um batimento → cai: a 2ª queda é nova (loga "caiu" e
    a espera recomeça de 1). A 3ª, logo depois do LISTEN, segue a 2ª."""
    conexoes = iter([_ConnFalsa(0), _ConnFalsa(1), _ConnFalsa(0)])

    async def connect(*a, **kw):
        try:
            return next(conexoes)
        except StopIteration:
            raise asyncio.CancelledError from None

    esperas = []
    _relogio_falso(monkeypatch, esperas, ate=1000)
    monkeypatch.setattr(eventos.psycopg.AsyncConnection, "connect", connect)
    with pytest.raises(asyncio.CancelledError):
        eventos.escutar_banco().send(None)
    assert esperas == [1, 1, 2]
    assert [linha.split(":")[0] for linha in capsys.readouterr().err.splitlines()] == [
        "[eventos] LISTEN pb_escrita caiu",
        "[eventos] LISTEN pb_escrita caiu",
    ]


# ── 8. Sem ninguém escutando, a escrita grava ───────────────────────────────

def test_sem_listener_a_escrita_grava(user_id):
    db.add_launch_and_update_balance(user_id, "receita", 7, None, "sem ouvinte")
    assert launches(user_id) == 1
