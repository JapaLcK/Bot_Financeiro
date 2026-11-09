"""PR-9b da fatia D (#657, D4): `print` sem `str(exc)` nos laços e tarefas de fundo fora do monólito
(agentes, contas a pagar, autopay, downsell do trial, reprojeção de grants, poda de tabelas).

Cada laço falha no 1º e no 2º item (`FALHA_NO`): o print leva o id do item DA VEZ, a causa
(`motivo=… sqlstate=… status=…`) e nunca o texto do erro, e o outro item é processado. O desfecho
(contagem, retorno, o que foi marcado) é o positivo: um conserto que calasse o laço passaria só
pela ausência. `_erro_real` é um 22P02 de verdade, porque um erro da classe 23 já chega redigido
por `db/connection.py`.

Os dois pares que a guarda NÃO vê (`run_agents_for_user` devolve o erro no `out`, `run_table_cleanup`
o põe em `errors`, que vai ao painel) têm caso próprio: o teste é a única rede deles.

CLASSE CEGA: a forma da linha no log do Railway só se vê em produção.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

import core.observability as observability
import core.reports.reports_daily as reports_daily
import core.services.billing_access as ba
import core.services.email_service as es
import core.services.piggy_agents as pa
import core.services.plan_service as ps
import core.services.recurring_charger as rc
import core.services.table_cleanup as tc
import core.services.trial_downsell as td
import db
import db.bills
import db.plans
import db.recurring
from _log_dono_helpers import FALHA_NO, _ALHEIO, _erro_real, _linhas, _marca, _uma
from core.admin_dashboard import ensure_admin_tables
from core.pg_text import causa_segura
from test_of_log_dono_coluna import _levanta
from test_piggy_agents import _arm_agg_email

CAUSA = "motivo=InvalidTextRepresentation sqlstate=22P02 status=None"
# `id`/`agent_id` diferentes do `user_id`: com os dois iguais, um print com o campo trocado passaria.
_OUTRO_ID = 1000
_DETECTOR = {"xerife": "_xerife_detect_for_user", "reporter": "_reporter_run_for_user",
             "carteiro": "_carteiro_detect_for_user", "detetive": "_detetive_detect_for_user",
             "cofre": "_cofre_detect_for_user", "barao": "_barao_detect_for_user",
             "faria_limer": "_faria_limer_detect_for_user"}


def _so_no(falha, erro, ok):
    """Levanta `erro` quando o 1º argumento (id ou dict com `user_id`/`id`) é o da vez."""
    def _f(alvo, *a, **k):
        chave = alvo.get("user_id", alvo.get("id")) if isinstance(alvo, dict) else alvo
        if chave == falha:
            raise erro
        return ok
    return _f


def _agente(kind):
    def roda(mp, uids, falha, erro):
        mp.setattr(pa, "AGENTS_ENABLED", True)
        mp.setattr(db, "list_users_with_active_agents", lambda k: [{"user_id": u, "config": {}} for u in uids])
        mp.setattr(ps, "agents_ui_enabled", lambda *a, **k: True)
        mp.setattr(ps, "agent_kind_allowed", lambda *a, **k: True)
        mp.setattr(pa, _DETECTOR[kind], _so_no(falha, erro, (1, 0) if kind == "xerife" else 1))
        mp.setattr(pa, "_detetive_duplicate_detect_for_user", lambda agent, today: 0)
        r = getattr(pa, f"run_{kind}_once")(today=date(2026, 11, 1))  # dia 1: dentro da janela do repórter
        assert r["agents"] == 2 and r["published" if kind == "reporter" else "fired"] == 1, r
        return lambda u: f"[agents] {kind} user={u}: "
    return roda


def _emails(mp, uids, falha, erro):
    enviados = []
    mp.setattr(db, "list_agents_pending_email", lambda: [
        {"agent_id": u + _OUTRO_ID, "user_id": u, "kind": "reporter", "config": {}, "last_emailed_at": None}
        for u in uids])
    mp.setattr(db, "list_unemailed_events", lambda aid: [
        {"id": aid, "payload": {"titulo": "T", "mensagem": "M"}, "fired_at": None}])
    mp.setattr(ps, "agents_ui_enabled", lambda *a, **k: True)
    mp.setattr(ps, "agent_kind_allowed", lambda *a, **k: True)
    mp.setattr(reports_daily, "filtrar_por_acesso", lambda ids: ids)
    mp.setattr(db, "get_auth_user", lambda uid: {"engagement_opt_out": False})
    mp.setattr(db, "get_user_email", _so_no(falha, erro, "u@e.com"))
    mp.setattr(db, "claim_agent_events_for_email", lambda evs: [e["id"] for e in evs])
    mp.setattr(db, "unclaim_agent_events", lambda ids: len(ids))
    mp.setattr(db, "touch_agent_emailed", lambda aid: None)
    mp.setattr(es, "send_agent_report_email", lambda to, uid, *a, **k: enviados.append(uid) or True)
    assert pa.run_agent_emails_once(now=datetime(2026, 11, 1, 12, tzinfo=timezone.utc)) == {"ok": True, "sent": 1}
    assert enviados == [u for u in uids if u != falha]
    return lambda u: f"[agents] email reporter user={u}: "


def _contas(mp, uids, falha, erro):
    mp.setattr(db.bills, "list_active_manual_recurrings", lambda user_id=None: [
        {"id": u + _OUTRO_ID, "user_id": u, "amount": 10} for u in uids])
    mp.setattr(rc, "_cycle_due_date", lambda rec, today: date(2026, 11, 5))
    mp.setattr(db.bills, "ensure_bill_instance", _so_no(falha + _OUTRO_ID, erro, None))
    assert rc.sync_manual_bills_once(date(2026, 11, 1)) == 1
    return lambda u: f"[bills] falhou gerar instancia rec={u + _OUTRO_ID}: "


def _autopay(mp, uids, falha, erro):
    mp.setattr(db.recurring, "list_active_autopay_recurrings", lambda: [
        {"id": u + _OUTRO_ID, "user_id": u, "amount": 10} for u in uids])
    mp.setattr(rc, "_vence_hoje", lambda rec, today: True)
    mp.setattr(db.recurring, "ensure_autopay_notice", _so_no(falha + _OUTRO_ID, erro, 1))
    assert rc.sync_autopay_notices_once(date(2026, 11, 1)) == 1
    return lambda u: f"[autopay_notice] falhou rec={u + _OUTRO_ID}: "


def _downsell(mp, uids, falha, erro):
    marcados = []
    mp.setattr(ps, "plans_v2_enabled", lambda: True)
    mp.setattr(db.plans, "list_trial_downsell_candidates", lambda: list(uids))
    mp.setattr(ps, "get_plan_tier", _so_no(falha, erro, "free"))
    mp.setattr(db, "get_auth_user", lambda uid: {"email": "u@e.com"})
    mp.setattr(db.plans, "mark_trial_downsell_sent", marcados.append)
    mp.setattr(es, "send_trial_downsell_email", lambda to, url: True)
    assert td.send_trial_downsell_emails() == {"ok": True, "disabled": False, "checked": 2, "sent": 1}
    assert marcados == [u for u in uids if u != falha], "a falha não pode marcar"
    return lambda u: f"[trial_downsell] user={u}: "


def _reprojecao(mp, uids, falha, erro):
    mp.setattr(ba, "users_com_grant_na_janela", lambda desde: list(uids))
    mp.setattr(ba, "recompute_entitlement", _so_no(falha, erro, None))
    assert ba.reprojetar_grants_recentes(datetime(2026, 11, 1, tzinfo=timezone.utc)) == 2
    return lambda u: f"[billing_access] reprojecao falhou user={u}: "


def _poda(mp, uids, falha, erro):
    mp.setattr(tc, "_cleanups", lambda: [(f"t{u}", _levanta(erro) if u == falha else (lambda: 1)) for u in uids])
    r = tc.run_table_cleanup()
    assert r["removed"] == {f"t{u}": 1 for u in uids if u != falha}, r
    assert r["errors"] == [{"table": f"t{falha}", "error": CAUSA}], r  # o par que a guarda não vê
    return lambda u: f"[cleanup_job] t{u}: FALHOU — "


@FALHA_NO
@pytest.mark.parametrize("roda, fluxo", [
    *[(_agente(k), "err") for k in _DETECTOR],
    (_emails, "err"), (_contas, "err"), (_autopay, "err"), (_downsell, "err"),
    (_reprojecao, "out"), (_poda, "err"),
], ids=[*_DETECTOR, "emails", "contas", "autopay", "downsell", "reprojecao", "poda"])
def test_laco_imprime_a_causa_do_item_da_vez_e_segue(monkeypatch, capsys, roda, fluxo, falha_no):
    erro = _erro_real()
    assert str(_ALHEIO) in str(erro), "pré-condição: o texto cru traz o dado do usuário"
    uids = [657_001, 657_002]
    falha, outro = uids[falha_no - 1], uids[2 - falha_no]
    prefixo = roda(monkeypatch, uids, falha, erro)  # o prefixo do print de cada item
    saida = capsys.readouterr()
    assert f"{prefixo(falha)}{CAUSA}\n" in getattr(saida, fluxo), saida
    assert prefixo(outro) not in saida.out + saida.err, "o print é do item da vez"
    assert str(_ALHEIO) not in saida.out + saida.err, saida


def test_falha_do_sinal_de_sync_imprime_a_causa_e_o_email_sai(monkeypatch, capsys):
    erro, consultados = _erro_real(), []
    now = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

    def _sinal(uid, mins):
        consultados.append(uid)
        raise erro

    sent, _ = _arm_agg_email(monkeypatch, kind="barao", fired_at=now - timedelta(hours=6),
                             sync_recente=False, now=now, synced=_sinal)
    assert len(sent) == 1, "o sinal é otimização: a falha não segura o e-mail"
    assert len(consultados) == 1 and consultados[0] != 9, "o `agent_id` do `_arm` é 9: tem de diferir do uid"
    saida = capsys.readouterr()
    assert f"[agents] sync-quiet check barao user={consultados[0]}: {CAUSA}\n" in saida.err, saida
    assert str(_ALHEIO) not in saida.out + saida.err


def test_run_for_user_devolve_e_imprime_a_causa(monkeypatch, capsys):
    erro = _erro_real()
    monkeypatch.setattr(pa, "AGENTS_ENABLED", True)
    monkeypatch.setattr(pa, "run_xerife_once", _levanta(erro))
    monkeypatch.setattr(pa, "run_detetive_once", lambda user_id: {"ok": True, "fired": 0})
    monkeypatch.setattr(pa, "run_cofre_once", lambda user_id: {"ok": True, "fired": 0})
    out = pa.run_agents_for_user(657_001, "of_sync")
    assert out == {"ok": True, "xerife": {"ok": False, "error": causa_segura(erro)},
                   "detetive": {"ok": True, "fired": 0}, "cofre": {"ok": True, "fired": 0}}
    saida = capsys.readouterr()
    assert f"[agents] run_for_user(657001, of_sync) xerife: {CAUSA}\n" in saida.err, saida
    assert str(_ALHEIO) not in saida.out + saida.err + str(out)


def _sono_ate(mp, ultimo):
    """Troca o `asyncio.sleep` (o laço o lê do módulo na hora): registra as esperas e corta o laço na de `ultimo` s.
    Chegar a ela prova que a volta seguiu depois da falha."""
    esperas = []

    async def dorme(s):
        esperas.append(s)
        if s == ultimo:
            raise asyncio.CancelledError

    mp.setattr(asyncio, "sleep", dorme)
    return esperas


def test_tick_dos_agentes_imprime_a_causa_e_dorme(monkeypatch, capsys):
    erro = _erro_real()
    monkeypatch.setattr(pa, "AGENTS_ENABLED", True)
    monkeypatch.setattr(pa, "run_all_agents_once", _levanta(erro))
    esperas = _sono_ate(monkeypatch, pa.AGENTS_INTERVAL_SEC)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pa.run_agents_loop())
    assert esperas == [5, pa.AGENTS_INTERVAL_SEC]
    saida = capsys.readouterr()
    assert f"[agents] tick falhou: {CAUSA}\n" in saida.err, saida
    assert str(_ALHEIO) not in saida.out + saida.err


def test_topo_do_cobrador_imprime_causa_e_quadros_sem_a_linha_final(monkeypatch, capsys):
    """`falha_de_tarefa` (P3 b): a causa e os quadros da pilha, sem a linha final do psycopg."""
    import core.services.declaracao_lembrete as declaracao
    # Um erro para cada passo: o mesmo objeto relançado acumula os quadros da 1ª volta.
    monkeypatch.setattr(rc, "sync_manual_bills_once", _levanta(_erro_real()))
    monkeypatch.setattr(rc, "sync_autopay_notices_once", _levanta(_erro_real()))
    monkeypatch.setattr(rc, "notify_autopay_notices_whatsapp_once", lambda agora: 0)
    monkeypatch.setattr(declaracao, "notify_declaracoes_whatsapp_once", lambda agora: 0)
    esperas = _sono_ate(monkeypatch, 3600)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(rc.run_recurring_charger_loop())
    assert esperas == [5, 3600], "a volta tem de seguir até a espera de 1 h"
    saida = capsys.readouterr()
    for tag in ("[bills]", "[autopay_notice]"):
        assert f"{tag} erro: {CAUSA}\n" in saida.err, saida.err
    assert saida.err.count(", in run_recurring_charger_loop\n") == 2, saida.err
    assert str(_ALHEIO) not in saida.out + saida.err, saida.err


@pytest.mark.parametrize("roda, prefixo", [
    (lambda: ba._observar(657_001, "t657", "m", "alerta"), "[billing_access] observabilidade falhou user=657001: "),
    (lambda: ba._alertar_sem_grants(657_001, "pro", None),
     "[billing_access] alerta projecao_sem_grants falhou user=657001: "),
], ids=["observar", "alertar_sem_grants"])
def test_observabilidade_da_cobranca_imprime_a_causa_no_stdout(monkeypatch, capsys, roda, prefixo):
    erro = _erro_real()
    monkeypatch.setattr(observability, "log_system_event_sync", _levanta(erro))
    assert roda() is None, "observabilidade nunca derruba a cobrança"
    saida = capsys.readouterr()
    assert f"{prefixo}{CAUSA}\n" in saida.out, saida
    assert str(_ALHEIO) not in saida.out + saida.err


def test_poda_que_falha_grava_a_causa_no_painel(monkeypatch, capsys):
    """`scripts/cleanup_job.run` de ponta a ponta: `errors` vai a `message` e a `details` da linha
    `cleanup_job` de `system_event_logs`, que o painel admin mostra."""
    from scripts import cleanup_job
    asyncio.run(ensure_admin_tables())  # `system_event_logs` não vem de db/schema.py
    erro = _erro_real()
    tabela = f"t657_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(tc, "_cleanups", lambda: [(tabela, _levanta(erro))])
    marca = _marca()
    assert cleanup_job.run() == 1
    linha = _uma(_linhas(marca, "message like %s", (f"%{tabela}%",)), "cleanup_job", "core.services.table_cleanup")
    assert linha["details"]["errors"] == [{"table": tabela, "error": CAUSA}], linha
    assert str(_ALHEIO) not in linha["message"] + str(linha["details"]), linha
    saida = capsys.readouterr()
    assert f"[cleanup_job] {tabela}: FALHOU — {CAUSA}\n" in saida.err, saida
    assert str(_ALHEIO) not in saida.out + saida.err
