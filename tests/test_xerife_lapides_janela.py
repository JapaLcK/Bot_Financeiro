"""R3/#913: regras não materializam todo o histórico em agent_events."""
from datetime import datetime, timedelta, timezone

import pytest

import db
from core.services.anomalia import JANELA_DIAS
from db.anomalias import _lapides_regras, alterar_regra_xerife
from conftest import usuario_pagante
from test_xerife_config_regras import _regra
from test_xerife_deteccao import _lanca


@pytest.fixture
def uid():
    user = usuario_pagante()
    db.activate_agent(user, "xerife")
    return user


def _eventos_com_lapides(uid):
    with db.get_conn() as conn:
        return {r["dedupe_key"]: r for r in conn.execute(
            "select * from agent_events where user_id = %s order by id", (uid,)
        ).fetchall()}


def test_criar_e_desfazer_regra_nao_materializa_historico_antigo_sem_evento(uid):
    recente = _lanca(uid, 480, dias=0, horas=1, alvo="mercado")
    na_janela = _lanca(uid, 480, dias=JANELA_DIAS-1, alvo="mercado")
    with db.get_conn() as conn:
        conn.execute(
            "insert into launches (user_id,tipo,valor,categoria,alvo,criado_em) "
            "select %s,'despesa',480,'Alimentação','mercado',now()-make_interval(days => %s+i) "
            "from generate_series(1,200) i", (uid,JANELA_DIAS),
        )
        conn.commit()
    regra = alterar_regra_xerife(uid, _regra())
    esperadas = {f"anomalia:{recente}", f"anomalia:{na_janela}"}
    assert set(_eventos_com_lapides(uid)) == esperadas
    # Importação histórica durante a regra também não ganha evento ao desfazer.
    _lanca(uid, 480, dias=JANELA_DIAS+10, alvo="mercado")
    alterar_regra_xerife(uid, regra_id=regra["id"])
    assert set(_eventos_com_lapides(uid)) == esperadas
    assert all(e["stale_at"] for e in _eventos_com_lapides(uid).values())


@pytest.mark.parametrize("ao_desfazer", [False, True])
@pytest.mark.parametrize("ja_enviado", [False, True])
def test_evento_existente_antigo_e_suprimido_sem_reescrever_payload_ou_carimbos(uid, ao_desfazer, ja_enviado):
    regra = alterar_regra_xerife(uid, _regra()) if ao_desfazer else None
    lid = _lanca(uid, 480, dias=0, horas=1, alvo="mercado")
    aid = db.get_agent(uid, "xerife")["id"]
    # Um alerta gravado antes de a despesa envelhecer (ou um evento legado importado).
    with db.get_conn() as conn:
        conn.execute(
            "insert into agent_events (agent_id,user_id,kind,dedupe_key,payload,fired_at,seen_at,emailed_at) "
            "values (%s,%s,'xerife',%s,'{\"tipo\":\"anomalia\",\"titulo\":\"original\"}'::jsonb, "
            "now()-interval '200 days',now()-interval '199 days',case when %s then now()-interval '198 days' end)",
            (aid,uid,f"anomalia:{lid}",ja_enviado),
        )
        conn.execute("update launches set criado_em = now()-make_interval(days => %s) "
                     "where user_id = %s and id = %s", (JANELA_DIAS+100,uid,lid))
        conn.commit()
    antes = _eventos_com_lapides(uid)[f"anomalia:{lid}"]
    if ao_desfazer:
        alterar_regra_xerife(uid, regra_id=regra["id"])
    else:
        alterar_regra_xerife(uid, _regra())
    depois = _eventos_com_lapides(uid)[f"anomalia:{lid}"]
    assert depois["stale_at"] is not None
    assert all(depois[k] == v for k,v in antes.items() if k != "stale_at")
    assert db.list_agent_events(uid) == [] and db.list_unemailed_events(aid) == []


def test_fronteira_janela_inclusiva_usa_relogio_da_mesma_transacao(uid):
    aid = db.get_agent(uid, "xerife")["id"]
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select id from agents where user_id = %s and id = %s for update", (uid,aid))
            cur.execute("select now() as agora")
            fim = cur.fetchone()["agora"]-timedelta(days=JANELA_DIAS)
            ids = []
            for quando in [fim-timedelta(microseconds=1),fim,fim+timedelta(microseconds=1)]:
                cur.execute("insert into launches (user_id,tipo,valor,categoria,alvo,criado_em) "
                            "values (%s,'despesa',480,'Alimentação','mercado',%s) returning id", (uid,quando))
                ids.append(cur.fetchone()["id"])
            _lapides_regras(cur,uid,aid,[_regra()])
        conn.commit()
    assert set(_eventos_com_lapides(uid)) == {f"anomalia:{lid}" for lid in ids[1:]}


def test_candidato_lido_antes_de_cruzar_24h_continua_protegido_apos_undo(uid):
    lid = _lanca(uid, 480, dias=0, horas=23, alvo="mercado")
    aid = db.get_agent(uid, "xerife")["id"]
    candidatos = db.listar_candidatos_xerife(uid, datetime.now(timezone.utc))
    assert [c["id"] for c in candidatos] == [lid]
    # O detector já leu o candidato. Durante uma pausa ele cruza a fronteira de 24h.
    # Reposicionar criado_em simula a intercalação sem esperar nem depender do relógio.
    with db.get_conn() as conn:
        conn.execute("update launches set criado_em = now()-interval '25 hours' "
                     "where user_id = %s and id = %s", (uid,lid))
        conn.commit()
    regra = alterar_regra_xerife(uid,_regra())
    alterar_regra_xerife(uid,regra_id=regra["id"])
    assert not db.record_agent_event(aid,uid,"xerife",f"anomalia:{lid}",
                                     {"tipo":"anomalia","launch_id":lid})
    assert db.list_agent_events(uid) == [] and db.list_unemailed_events(aid) == []


def test_filtro_preserva_isolamento_teto_tipo_e_limites_de_categoria(uid):
    outro = usuario_pagante()
    aid = db.get_agent(uid,"xerife")["id"]
    recente = _lanca(uid,500,dias=0,horas=1,alvo="mercado")
    _lanca(outro,480,dias=0,horas=1,alvo="mercado")
    _lanca(uid,500.01,dias=0,horas=1,alvo="mercado")
    _lanca(uid,480,dias=0,horas=1,alvo="mercado",tipo="receita")
    _lanca(uid,480,dias=0,horas=1,alvo="mercado",interno=True)
    _lanca(uid,480,dias=0,horas=1,alvo="outro")
    db.record_agent_event(aid,uid,"xerife","limite:alimentação:2026-10", {"tipo":"limite"})
    regra = alterar_regra_xerife(uid,_regra())
    alterar_regra_xerife(uid,regra_id=regra["id"])
    eventos = _eventos_com_lapides(uid)
    assert set(eventos) == {f"anomalia:{recente}","limite:alimentação:2026-10"}
    assert eventos[f"anomalia:{recente}"]["stale_at"] is not None
    assert eventos["limite:alimentação:2026-10"]["stale_at"] is None
    assert _eventos_com_lapides(outro) == {}
