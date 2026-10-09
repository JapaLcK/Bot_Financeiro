"""PL-04: "era esperado" e o alerta explicado do Xerife, com banco real.

Controles (CLAUDE.md §3): o grupo de "esperado" falha se a coluna deixar de ser lida no
candidato ou na referência, ou se a lápide for removida; os casos "dispara" e "desmarcado"
provam o caminho legítimo. O isolamento entre usuários tem teste próprio.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import db
from db import get_conn, marcar_lancamento_esperado
from core.services import piggy_agents as pa
from conftest import promote_to_pro, usuario_pagante
from test_xerife_deteccao import CINCO_100, _eventos, _lanca, _roda


@pytest.fixture
def uid():
    return usuario_pagante()


def _coluna(lid):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select esperado_em from launches where id = %s", (lid,))
            return cur.fetchone()["esperado_em"]


def _cenario_outlier(uid):
    """5 x R$ 100 + um outlier de R$ 1.800 no histórico e um gasto de R$ 480 hoje.
    Com o outlier a média é 383,33 (480 não dispara); sem ele é 100 (dispara)."""
    for i in range(5):
        _lanca(uid, 100, dias=10 + i)
    outlier = _lanca(uid, 1800, dias=20)
    alvo = _lanca(uid, 480, dias=0, horas=1)
    return outlier, alvo


def test_outlier_infla_a_media_e_o_alerta_nao_dispara(uid):
    _cenario_outlier(uid)
    _roda(uid)
    assert _eventos(uid) == []


def test_esperado_sai_da_media_e_o_alerta_dispara(uid):
    outlier, alvo = _cenario_outlier(uid)
    assert marcar_lancamento_esperado(uid, outlier, True) is True
    _roda(uid)
    (ev,) = _eventos(uid)
    p = ev["payload"]
    assert p["launch_id"] == alvo and p["media"] == 100.0
    assert p["explicacao"]["amostra"]["lancamentos"] == 5
    assert p["explicacao"]["amostra"]["esperados_fora"] == 1       # transparência: ficou de fora


def test_desmarcar_devolve_o_lancamento_a_media(uid):
    outlier, _ = _cenario_outlier(uid)
    marcar_lancamento_esperado(uid, outlier, True)
    assert marcar_lancamento_esperado(uid, outlier, False) is True
    assert _coluna(outlier) is None
    _roda(uid)
    assert _eventos(uid) == []


def test_marcar_antes_da_deteccao_nao_gera_evento(uid):
    for i in range(5):
        _lanca(uid, 100, dias=10 + i)
    alvo = _lanca(uid, 480, dias=0, horas=1)
    marcar_lancamento_esperado(uid, alvo, True)
    _roda(uid)
    assert _eventos(uid) == []


def test_marcar_depois_some_do_feed_da_fila_de_email_e_do_contador(uid):
    for i in range(5):
        _lanca(uid, 100, dias=10 + i)
    alvo = _lanca(uid, 480, dias=0, horas=1)
    _roda(uid)
    agent = db.get_agent(uid, "xerife")
    xerife = lambda: next(a for a in db.list_agents(uid) if a["kind"] == "xerife")      # noqa: E731
    assert len(_eventos(uid)) == 1 and xerife()["fired_30d"] == 1
    assert len(db.list_unemailed_events(agent["id"])) == 1

    assert marcar_lancamento_esperado(uid, alvo, True) is True

    assert _eventos(uid) == []
    assert db.list_unemailed_events(agent["id"]) == []
    assert xerife()["fired_30d"] == 0
    _roda(uid)                                                  # o detector não recria
    assert _eventos(uid) == []


def test_falha_no_passo_do_evento_desfaz_a_marcacao(uid, monkeypatch):
    """Atomicidade: o `update` de launches e a lápide/stale são UMA transação. Se o passo do
    evento levanta depois do update, nada fica pela metade (nem marcado com alerta vivo)."""
    from types import SimpleNamespace
    import db.anomalias as anom
    for i in range(5):
        _lanca(uid, 100, dias=10 + i)
    alvo = _lanca(uid, 480, dias=0, horas=1)
    _roda(uid)
    agent = db.get_agent(uid, "xerife")

    def boom(*a, **k):
        raise RuntimeError("falha no evento")
    monkeypatch.setattr(anom, "json", SimpleNamespace(dumps=boom))
    with pytest.raises(RuntimeError):
        marcar_lancamento_esperado(uid, alvo, True)
    monkeypatch.undo()

    assert _coluna(alvo) is None
    assert len(_eventos(uid)) == 1 and len(db.list_unemailed_events(agent["id"])) == 1


def test_marcar_preserva_emailed_at_e_seen_at_do_evento(uid):
    """O stale tira o evento do feed/fila sem apagar o histórico de entrega."""
    for i in range(5):
        _lanca(uid, 100, dias=10 + i)
    alvo = _lanca(uid, 480, dias=0, horas=1)
    _roda(uid)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update agent_events set emailed_at = now() - interval '1 hour', "
                        "seen_at = now() - interval '30 minutes' where user_id = %s", (uid,))
        conn.commit()
    marcar_lancamento_esperado(uid, alvo, True)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select emailed_at, seen_at, stale_at from agent_events where user_id = %s", (uid,))
            row = cur.fetchone()
    assert row["emailed_at"] is not None and row["seen_at"] is not None and row["stale_at"] is not None


def test_lapide_impede_o_detector_que_leu_antes_da_marcacao(uid):
    """O detector leu o lançamento ANTES da marcação e grava DEPOIS dela: a chave já está
    ocupada pela lápide, então o insert dele é no-op."""
    for i in range(5):
        _lanca(uid, 100, dias=10 + i)
    alvo = _lanca(uid, 480, dias=0, horas=1)
    agent = db.activate_agent(uid, "xerife", {})
    marcar_lancamento_esperado(uid, alvo, True)
    gravou = db.record_agent_event(agent["id"], uid, "xerife", f"anomalia:{alvo}", {"tipo": "anomalia"})
    assert gravou is False and _eventos(uid) == []


def test_marcar_duas_vezes_preserva_o_instante_original(uid):
    alvo = _lanca(uid, 480, dias=1)
    marcar_lancamento_esperado(uid, alvo, True)
    primeiro = _coluna(alvo)
    assert primeiro is not None
    assert marcar_lancamento_esperado(uid, alvo, True) is True
    assert _coluna(alvo) == primeiro


@pytest.mark.parametrize("kw", [{"tipo": "receita"}, {"interno": True}])
def test_so_despesa_nao_interna_pode_ser_esperada(uid, kw):
    lid = _lanca(uid, 100, dias=1, **kw)
    assert marcar_lancamento_esperado(uid, lid, True) is False
    assert _coluna(lid) is None


def test_lancamento_inexistente_devolve_false(uid):
    assert marcar_lancamento_esperado(uid, 2_000_000_000, True) is False


# ── isolamento entre usuários ────────────────────────────────────────────────

def test_esperado_de_um_usuario_nao_toca_o_outro(uid):
    outro = usuario_pagante()
    out_a, _ = _cenario_outlier(uid)
    out_b, alvo_b = _cenario_outlier(outro)

    # A não consegue marcar o lançamento de B (mesmo resultado de "não existe")
    assert marcar_lancamento_esperado(uid, out_b, True) is False
    assert _coluna(out_b) is None

    # A marca o DELE: o alerta de A dispara; o de B continua sem disparar
    marcar_lancamento_esperado(uid, out_a, True)
    _roda(uid)
    _roda(outro)
    assert len(_eventos(uid)) == 1
    assert _eventos(outro) == []
    assert _coluna(out_b) is None and _coluna(alvo_b) is None
    # o evento de A não aparece no feed de B
    assert all(e["payload"]["launch_id"] != alvo_b for e in db.list_agent_events(outro, 50))


def test_lapide_so_vale_para_o_agente_do_dono(uid):
    outro = usuario_pagante()
    db.activate_agent(outro, "xerife", {})
    alvo = _lanca(uid, 480, dias=1)
    db.activate_agent(uid, "xerife", {})
    marcar_lancamento_esperado(uid, alvo, True)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select count(*) as n from agent_events where user_id = %s", (outro,))
            assert cur.fetchone()["n"] == 0


# ── ponta a ponta: cron + hook + e-mail ──────────────────────────────────────

@pytest.fixture
def emails(monkeypatch):
    enviados: list[tuple] = []
    import core.services.email_service as es
    monkeypatch.setattr(es, "send_agent_report_email", lambda *a, **k: enviados.append(a) or True)
    return enviados


def _cenario_simples(uid):
    for i in range(5):
        _lanca(uid, 100, dias=10 + i)
    return _lanca(uid, 480, dias=0, horas=1)


def test_cron_hook_e_reentrega_geram_um_evento_e_um_email(uid, emails):
    alvo = _cenario_simples(uid)
    db.activate_agent(uid, "xerife", {})

    pa.run_all_agents_once()                                    # cron (inclui o tick de e-mail)
    pa.run_agents_for_user(uid)                                 # hook pós-sync, mesmo lançamento
    pa.run_all_agents_once()                                    # outro tick do cron
    pa.run_agent_emails_once(now=datetime.now(timezone.utc) + timedelta(hours=25))   # fora do teto de 24h

    (ev,) = _eventos(uid)
    assert ev["payload"]["launch_id"] == alvo
    meus = [a for a in emails if a[1] == uid]
    assert len(meus) == 1, "o mesmo gasto não pode gerar dois e-mails"
    corpo = meus[0][3]
    assert "R$ 100,00" in corpo and "R$ 380,00" in corpo and "5 lançamentos" in corpo


def test_historico_curto_dispara_com_a_flag_e_a_frase(uid):
    for i in range(5):
        _lanca(uid, 100, dias=2 + i)
    _lanca(uid, 100, dias=20)                                   # primeira despesa há 20 dias
    _lanca(uid, 480, dias=0, horas=1)
    _roda(uid)
    (ev,) = _eventos(uid)
    amostra = ev["payload"]["explicacao"]["amostra"]
    assert amostra["historico_incompleto"] is True and amostra["dias_historico"] == 20
    assert "Histórico ainda curto (20 de 90 dias)" in ev["payload"]["mensagem"]


def test_amostra_pequena_nao_dispara_e_e_contada(uid):
    for i in range(3):
        _lanca(uid, 100, dias=10 + i)
    _lanca(uid, 480, dias=0, horas=1)
    r = _roda(uid)
    assert _eventos(uid) == [] and r["suprimidos_amostra"] == 1 and r["fired"] == 0


def test_retorno_de_outro_usuario_nao_entra_na_contagem(uid):
    outro = usuario_pagante()
    for u in (uid, outro):
        for i in range(3):
            _lanca(u, 100, dias=10 + i)
        _lanca(u, 480, dias=0, horas=1)
    db.activate_agent(outro, "xerife", {})
    assert _roda(uid)["suprimidos_amostra"] == 1                # só o do próprio usuário


# ── plano ────────────────────────────────────────────────────────────────────

def test_essencial_com_agente_ativo_nao_grava_evento_e_o_lembrete_de_orcamento_segue(uid):
    from decimal import Decimal
    from core.budget_alerts import evaluate_after_expense
    from core.services.plan_service import agent_kind_allowed

    promote_to_pro(uid, plan="essencial")
    assert agent_kind_allowed(uid, "xerife") is False, "o usuário de teste não é Essencial"
    _cenario_simples(uid)
    r = _roda(uid)
    assert r["agents"] == 0 and r["fired"] == 0 and _eventos(uid) == []

    # controle positivo: o lembrete simples do Essencial (80/100/120% do orçamento) intacto
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("insert into category_budgets (user_id, categoria, budget) values (%s, %s, %s)",
                        (uid, "lazer", Decimal("100")))
        conn.commit()
    agora = datetime.now()
    db.add_launch_and_update_balance(uid, "despesa", 90, None, "t", categoria="lazer", criado_em=agora)
    assert evaluate_after_expense(uid, "lazer", 90, agora) is not None


# ── schema ───────────────────────────────────────────────────────────────────

def test_coluna_esperado_em_existe_nula_e_init_db_e_idempotente(uid):
    lid = _lanca(uid, 100, dias=1)
    db.init_db()
    db.init_db()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select data_type from information_schema.columns "
                        "where table_name = 'launches' and column_name = 'esperado_em'")
            assert cur.fetchone()["data_type"] == "timestamp with time zone"
    assert _coluna(lid) is None


# ── isolamento do histórico, suprimidos e janela ─────────────────────────────

def test_dias_de_historico_nao_dependem_de_outro_usuario(uid):
    outro = usuario_pagante()
    _lanca(outro, 100, dias=400)                                # B tem a 1ª despesa muito antiga
    for i in range(5):
        _lanca(uid, 100, dias=2 + i)
    _lanca(uid, 100, dias=20)                                   # a de A é de 20 dias
    _lanca(uid, 480, dias=0, horas=1)
    _roda(uid)
    (ev,) = _eventos(uid)
    amostra = ev["payload"]["explicacao"]["amostra"]
    assert amostra["dias_historico"] == 20 and amostra["historico_incompleto"] is True
    assert "Histórico ainda curto (20 de 90 dias)" in ev["payload"]["mensagem"]


def test_suprimidos_so_conta_o_que_teria_alertado(uid):
    for i in range(3):                                          # n = 3 nas duas categorias
        _lanca(uid, 100, dias=10 + i)
        _lanca(uid, 10, dias=10 + i, cat="Lazer")
    _lanca(uid, 100, dias=0, horas=1)                           # normal
    _lanca(uid, 40, dias=0, horas=1, cat="Lazer")               # 4x, mas abaixo de R$ 50
    assert _roda(uid)["suprimidos_amostra"] == 0
    _lanca(uid, 480, dias=0, horas=1)                           # este TERIA alertado
    assert _roda(uid)["suprimidos_amostra"] == 1


def test_janela_de_90_dias_nas_bordas_como_o_sql_antigo(uid):
    """`criado_em >= agora-90d` e `< agora-24h` (candidato: `>= agora-24h`), ao microssegundo."""
    agora = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    ini, fim, micro = agora - timedelta(days=90), agora - timedelta(hours=24), timedelta(microseconds=1)

    def em(valor, quando):
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("insert into launches (user_id, tipo, valor, categoria, criado_em) "
                            "values (%s, 'despesa', %s, %s, %s) returning id", (uid, valor, "Alimentação", quando))
                lid = cur.fetchone()["id"]
            conn.commit()
        return lid

    em(100, ini)                                                # dentro
    em(100, ini - micro)                                        # fora
    em(100, fim - micro)                                        # dentro
    cand = em(400, fim)                                         # candidato (fora do histórico)
    (row,) = db.listar_candidatos_xerife(uid, agora)
    assert row["id"] == cand and row["n"] == 2
