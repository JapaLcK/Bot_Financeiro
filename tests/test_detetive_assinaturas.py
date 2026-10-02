"""Detetive lendo o Recurring Payments da Pluggy, sem rajada de alerta no deploy:
a 1ª busca de conexão que já existia (`recurring_seed_silent`) vira lápide.
Banco real; `run_detetive_once` de verdade."""
import uuid
from datetime import date

import pytest

import db
from _apoio_assinaturas import HOJE, conexao, conta, mensais, rp, semeia, tx
from conftest import promote_to_pro
from core.services.piggy_agents import run_detetive_once
from db.connection import get_conn
from db.of_recurring import consumir_silencio, descricoes_das_conexoes, marcar, salvar_recorrencias


def _q(sql, *args):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        rows = cur.fetchall() if cur.description else None
        conn.commit()
    return rows


@pytest.fixture
def uid(user_id):
    promote_to_pro(user_id)
    db.activate_agent(user_id, "detetive")
    return user_id


NF = mensais("nf", [-39.9] * 3)
SP = mensais("sp", [-21.9] * 3, ultima=date(2026, 9, 12))
YT = mensais("yt", [-24.9] * 3, ultima=date(2026, 9, 20))
DOIS = [rp("NETFLIX.COM", -39.9, NF), rp("Spotify", -21.9, SP)]
CONTA = [conta("acc-1", NF + SP + YT)]


def _silenciar(cid, fetched=True):
    _q("update open_finance_connections set recurring_seed_silent = true"
       + ("" if fetched else ", recurring_fetched_at = null") + " where id = %s", cid)


def _flag(cid):
    return _q("select recurring_seed_silent s from open_finance_connections where id=%s", cid)[0]["s"]


def _visiveis(uid):
    """O feed (`list_agent_events`, que já tira as lápides), pela chave do payload."""
    return sorted(f"rp:{e['payload']['merchant']}" for e in db.list_agent_events(uid, kind="detetive"))


def _lapides(uid):
    return _q("select count(*) n from agent_events where user_id=%s and stale_at is not null"
              " and dedupe_key like 'rp:%%'", uid)[0]["n"]


def _roda(uid):
    run_detetive_once(today=HOJE, user_id=uid)


def test_a_primeira_busca_silenciosa_vira_lapide_e_a_seguinte_alerta(uid):
    cid = semeia(uid, CONTA, DOIS)
    _silenciar(cid)
    _roda(uid)
    assert (_visiveis(uid), _lapides(uid), _flag(cid)) == ([], 2, False)

    salvar_recorrencias(cid, DOIS + [rp("YouTube Premium", -24.9, YT)])
    _roda(uid)
    assert _visiveis(uid) == ["rp:youtube premium"]


def test_b_conexao_nova_alerta_e_o_positivo(uid):
    cid = semeia(uid, CONTA, DOIS)
    assert _flag(cid) is False
    _roda(uid)
    assert (_visiveis(uid), _lapides(uid)) == (["rp:netflix", "rp:spotify"], 0)
    ev = {e["payload"]["merchant"]: e for e in db.list_agent_events(uid)}["netflix"]
    assert ev["payload"]["tipo"] == "assinatura" and ev["payload"]["valor"] == 39.9


def test_c_silencio_sem_busca_nao_e_consumido(uid):
    cid = conexao(uid)
    db.save_open_finance_sync(cid, CONTA)
    _silenciar(cid, fetched=False)
    _roda(uid)
    assert _flag(cid) is True

    salvar_recorrencias(cid, DOIS)
    _roda(uid)
    assert (_visiveis(uid), _lapides(uid), _flag(cid)) == ([], 2, False)


def test_d_ignorar_nao_gera_evento(uid):
    semeia(uid, CONTA, DOIS)
    marcar(uid, "netflix", "ignorar")
    _roda(uid)
    assert _visiveis(uid) == ["rp:spotify"]


def test_e_chave_ja_silenciada_em_conexao_nova_nao_alerta(uid):
    cid = semeia(uid, CONTA, DOIS)
    _silenciar(cid)
    _roda(uid)
    nf2 = mensais("nf2", [-39.9] * 3)
    semeia(uid, [conta("acc-2", nf2)], [rp("NETFLIX.COM", -39.9, nf2)])
    _roda(uid)
    assert _visiveis(uid) == []


# ── Silêncio cobre TODA chave da conexão, não só a que alerta hoje ──────────

VELHA = mensais("v", [-39.9] * 3, ultima=date(2026, 3, 5))


def test_f_cancelada_na_semente_que_volta_nao_alerta(uid):
    cid = semeia(uid, [conta("acc-1", VELHA + NF)], [rp("NETFLIX.COM", -39.9, VELHA)])
    _silenciar(cid)
    _roda(uid)
    salvar_recorrencias(cid, [rp("NETFLIX.COM", -39.9, VELHA + NF)])
    _roda(uid)
    assert _visiveis(uid) == []


def test_g_ignorada_na_semente_e_desfeita_nao_alerta(uid):
    cid = semeia(uid, CONTA, DOIS)
    marcar(uid, "netflix", "ignorar")
    _silenciar(cid)
    _roda(uid)
    marcar(uid, "netflix", "nenhuma")
    _roda(uid)
    assert _visiveis(uid) == []


def test_h_conexao_pausada_na_semente_nao_alerta_ao_voltar(uid):
    cid = semeia(uid, CONTA, DOIS)
    _silenciar(cid)
    _q("update open_finance_connections set status='PAUSED' where id=%s", cid)
    _roda(uid)
    assert (_flag(cid), _lapides(uid)) == (False, 2)
    _q("update open_finance_connections set status='UPDATED' where id=%s", cid)
    _roda(uid)
    assert _visiveis(uid) == []


def test_i_mesma_chave_em_silenciosa_e_normal_na_mesma_rodada(uid):
    cid = semeia(uid, [conta("acc-1", NF)], [rp("NETFLIX.COM", -39.9, NF)])
    _silenciar(cid)
    nf2 = mensais("nf2", [-39.9] * 3)
    semeia(uid, [conta("acc-2", nf2)], [rp("NETFLIX.COM", -39.9, nf2)])
    _roda(uid)
    assert _visiveis(uid) == []


def test_j_so_alerta_ativa(uid):
    semeia(uid, [conta("acc-1", VELHA + SP)], [rp("NETFLIX.COM", -39.9, VELHA), rp("Spotify", -21.9, SP)])
    _roda(uid)
    assert _visiveis(uid) == ["rp:spotify"]


def test_k_reajuste_nao_gera_alerta_novo(uid):
    txs = mensais("r", [-39.9, -39.9, -44.9], ultima=HOJE)  # as 2 primeiras já ativas
    cid = semeia(uid, [conta("acc-1", txs)], [rp("NETFLIX.COM", -39.9, txs[:2])])
    _roda(uid)
    salvar_recorrencias(cid, [rp("NETFLIX.COM", -41.57, txs)])
    _roda(uid)
    assert [e["payload"]["valor"] for e in db.list_agent_events(uid, kind="detetive")] == [39.9]


def test_l_mensagem_conta_meses_distintos(uid):
    txs = [tx("m1", -39.9, date(2026, 8, 5)), tx("m2", -39.9, date(2026, 8, 20)),
           tx("m3", -39.9, date(2026, 9, 5))]
    semeia(uid, [conta("acc-1", txs)], [rp("NETFLIX.COM", -39.9, txs)])
    _roda(uid)
    (ev,) = db.list_agent_events(uid, kind="detetive")
    assert "há 2 meses seguidos" in ev["payload"]["mensagem"]


# ── Ordem, isolamento e chave vazia no silêncio ─────────────────────────────

def test_m_queda_no_meio_das_lapides_nao_consome_o_silencio(uid, monkeypatch):
    cid = semeia(uid, CONTA, DOIS)
    _silenciar(cid)
    real, chamadas = db.record_agent_event, []

    def _cai_na_segunda(*a, **kw):
        chamadas.append(1)
        if len(chamadas) == 2:
            raise RuntimeError("queda no meio das lápides")
        return real(*a, **kw)
    monkeypatch.setattr(db, "record_agent_event", _cai_na_segunda)
    _roda(uid)  # o run_detetive_once engole a exceção por usuário
    assert (_lapides(uid), _flag(cid)) == (1, True)

    monkeypatch.setattr(db, "record_agent_event", real)
    _roda(uid)
    assert (_visiveis(uid), _lapides(uid), _flag(cid)) == ([], 2, False)


def test_n_silencio_so_le_e_consome_conexao_do_dono(user_id):
    b = int(uuid.uuid4().int % 10_000_000_000)  # o autouse do conftest apaga depois
    db.ensure_user(b)
    cid_b = semeia(b, CONTA, DOIS)
    _silenciar(cid_b)
    assert descricoes_das_conexoes(user_id, [cid_b]) == []
    consumir_silencio(user_id, [cid_b])
    assert _flag(cid_b) is True
    # Positivo: o dono lê e consome.
    assert sorted(descricoes_das_conexoes(b, [cid_b])) == ["NETFLIX.COM", "Spotify"]
    consumir_silencio(b, [cid_b])
    assert _flag(cid_b) is False


def test_o_descricao_sem_chave_nao_vira_lapide(uid):
    lixo = mensais("l", [-19.9] * 3)
    cid = semeia(uid, [conta("acc-1", lixo)], [rp("*** ---", -19.9, lixo)])
    _silenciar(cid)
    _roda(uid)
    n = _q("select count(*) n from agent_events where user_id=%s and dedupe_key like 'rp:%%'", uid)[0]["n"]
    assert (n, _flag(cid)) == (0, False)
