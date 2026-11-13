"""PL-06 B6, PR 1, rodada 2: a rota e os textos.

  • D2 (T36): só `{"insight_alert_email_enabled": false}` passa sem a perna do direito; o resto segue
    a regra normal. O 402 do GET traz a flag só para quem tem adesão.
  • D3 (T37): a orientação de religar no chat e no `/unsubscribe`, só para quem consegue religar.
  • D4 (T38): o texto da adesão com a hora e o fuso que o envio usa.
  • D5 (T39): a nota antes de desligar "Insights financeiros".
  • T27: validação antes de qualquer escrita e `StrictBool`; T28: o `init_db` do estado legado misto.
  • As 13 mutações que sobreviveram ao Tester (`mut1.log`), cada uma com o caso que a mata.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db
import db.reports
import db_support
import frontend.finance_bot_websocket_custom as dashboard
from _insights_adesao_apoio import (  # noqa: F401 — `ambiente` é fixture
    CSRF, aderir, ambiente, cliente, diga, eventos, flags, get, ok, patch, sql, unsub,
)
from conftest import usuario_pagante
from core.audit import EVENT_LABELS_PT_BR, AuditEvent
from core.services import insights_notificacao as notif
from db.privacy import build_user_export_zip
from db.reports import insight_aviso_aderiu, insight_email_permitido, set_engagement_opt_out, set_insight_email_opt_out

pytestmark = pytest.mark.usefixtures("ambiente")
DESLIGA = {"insight_alert_email_enabled": False}


def _cortado_com_adesao():
    """Aderiu e o plano venceu há 20 min sem escrita nenhuma: sem direito e com a adesão ainda VIVA (margem)."""
    uid = usuario_pagante("pro")
    aderir(uid)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local session_replication_role = replica")   # o tempo passou, ninguém escreveu
        cur.execute("update auth_accounts set plan_expires_at = now() - interval '20 minutes',"
                    " insight_aviso_coberto_ate = now() - interval '20 minutes', plan_selected_at = now()"
                    " where user_id = %s", (uid,))
        conn.commit()
    db_support.invalidate_auth_user_cache(uid)
    return uid


# ── D2 ────────────────────────────────────────────────────────────────────────

def test_D2_cortado_com_adesao_ve_a_flag_e_desliga_com_resposta_minima():
    uid = _cortado_com_adesao()
    r = cliente(uid).get(f"/settings/{uid}/notifications")
    assert r.status_code == 402 and r.json()["detail"] == {"error": "subscription_required",
                                                           "insight_alert_email_enabled": True,
                                                           "insight_alert_email_lapsed_notice": notif.AVISO_SEM_PLANO}
    r = patch(uid, **DESLIGA)
    # A curta é a do app (`app_aviso_desligado_curta.json`, lida pelo jest): mudar uma sem a outra deixa um dos dois vermelho.
    assert r.status_code == 200 and r.json() == json.loads((Path(__file__).parent / "fixtures" / "app_aviso_desligado_curta.json").read_text(encoding="utf-8"))
    assert not insight_aviso_aderiu(uid) and eventos(uid)[-1]["event"] == AuditEvent.INSIGHT_AVISO_RETIRADA
    assert eventos(uid)[-1]["ip"] and eventos(uid)[-1]["user_agent"]
    r = cliente(uid).get(f"/settings/{uid}/notifications")
    assert r.status_code == 402 and r.json()["detail"] == {"error": "subscription_required"}, "sem adesão, o 402 de sempre"


@pytest.mark.parametrize("corpo", [
    {"insight_alert_email_enabled": True},
    {"insight_alert_email_enabled": False, "tip_email_enabled": False},
    {"insight_alert_email_enabled": False, "tip_email_enabled": None},
    {"tip_email_enabled": False},
])
def test_D2_cortado_qualquer_outro_corpo_segue_402_e_nada_grava(corpo):
    uid = _cortado_com_adesao()
    r = patch(uid, **corpo)
    assert r.status_code == 402, r.text
    assert insight_aviso_aderiu(uid) and flags(uid)["t"] is False


def test_D2_sem_credencial_desliga_mas_nao_le():
    uid = usuario_pagante("pro")
    aderir(uid)
    c = cliente(uid)
    sql("update auth_accounts set password_hash = null, plan_selected_at = now() where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    assert c.get(f"/settings/{uid}/notifications").status_code == 403
    assert c.patch(f"/settings/{uid}/notifications", json={"tip_email_enabled": False}).status_code == 403
    r = c.patch(f"/settings/{uid}/notifications", json=DESLIGA)
    assert r.status_code == 200 and r.json() == {"ok": True, "insight_alert_email_enabled": False}


def test_D2_com_direito_desligar_devolve_a_resposta_completa():
    uid = usuario_pagante("pro")
    aderir(uid)
    d = ok(uid, **DESLIGA)
    assert d["insight_alert_email_enabled"] is False and "tip_email_enabled" in d


def test_D2_exclusao_sessao_csrf_dono_e_escolha_de_plano_seguem_valendo():
    uid, outro = _cortado_com_adesao(), usuario_pagante("pro")
    c = cliente(uid)
    assert c.patch(f"/settings/{outro}/notifications", json=DESLIGA).status_code == 403
    sem_csrf = cliente(uid)
    del sem_csrf.headers[dashboard.CSRF_HEADER_NAME]
    assert sem_csrf.patch(f"/settings/{uid}/notifications", json=DESLIGA).status_code == 403
    assert TestClient(dashboard.app).patch(f"/settings/{uid}/notifications", json=DESLIGA,
                                           headers={dashboard.CSRF_HEADER_NAME: CSRF}).status_code in (401, 403)
    sql("update auth_accounts set deletion_status = 'scheduled', deletion_scheduled_for = now() + interval '7 days'"
        " where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    assert c.patch(f"/settings/{uid}/notifications", json=DESLIGA).status_code == 403
    assert insight_aviso_aderiu(uid), "a conta em exclusão não grava; o portão já barra o envio"
    assert insight_email_permitido(uid) is False


def test_D2_quem_nunca_escolheu_plano_fica_no_402_de_escolha():
    uid = usuario_pagante("pro")
    c = cliente(uid)
    sql("update auth_accounts set plan = 'free', plan_selected_at = null where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    r = c.patch(f"/settings/{uid}/notifications", json=DESLIGA)
    assert r.status_code == 402 and r.json()["detail"]["error"] == "plan_selection_required"


# ── T27: validação antes da escrita, StrictBool ───────────────────────────────

@pytest.mark.parametrize("valor", ["false", 0, "true", 1])
def test_T27_consentimento_nao_se_converte(valor):
    uid = usuario_pagante("pro")
    assert patch(uid, insight_alert_email_enabled=valor).status_code == 422
    assert flags(uid)["aderiu"] is None


@pytest.mark.parametrize("extra", [{"whatsapp_updates_enabled": True}, {"daily_report_hour": 25},
                                   {"daily_report_minute": 99}])
def test_T27_telefone_ou_hora_invalidos_nao_gravam_nada(extra):
    uid = usuario_pagante("pro")
    sql("update auth_accounts set phone_e164 = null where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    mensal = get(uid)["monthly_report_enabled"]
    r = patch(uid, insight_alert_email_enabled=True, tip_email_enabled=False, monthly_report_enabled=not mensal, **extra)
    assert r.status_code == 400, r.text
    assert flags(uid)["aderiu"] is None and flags(uid)["t"] is False and get(uid)["monthly_report_enabled"] is mensal
    assert eventos(uid) == []


# ── D3 ────────────────────────────────────────────────────────────────────────

def test_D3_chat_orienta_quem_pode_religar_e_nao_promete_insights():
    uid = usuario_pagante("pro")
    aderir(uid)
    parou, voltou = diga(uid, "parar emails"), diga(uid, "reativar emails")
    assert notif.ORIENTACAO in parou and notif.ORIENTACAO in voltou
    assert "insights" not in voltou.lower() and "curiosidades" in voltou


@pytest.mark.parametrize("caso", ["essencial", "fora_da_env", "erro"])
def test_D3_sem_acesso_ao_interruptor_nao_orienta(monkeypatch, caso):
    uid = usuario_pagante("essencial" if caso == "essencial" else "pro")
    if caso == "fora_da_env":
        monkeypatch.setenv("INSIGHT_EMAIL_USERS", "")
    if caso == "erro":
        def _quebra(*a, **k):
            raise RuntimeError("banco fora")
        monkeypatch.setattr("core.services.plan_service.plan_gate_ok", _quebra)
        assert notif.orientacao_aviso(uid) == ""
        return
    assert notif.ORIENTACAO not in diga(uid, "reativar emails")
    assert notif.ORIENTACAO not in unsub(uid, "get").text


def test_D3_carencia_nao_orienta():
    uid = usuario_pagante("pro")
    sql("update auth_accounts set plan = 'free', last_payment_status = 'past_due', past_due_since = now(),"
        " plan_selected_at = now() where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    assert notif.orientacao_aviso(uid) == ""


def test_D3_pagina_do_unsubscribe_orienta_so_com_token_valido():
    uid = usuario_pagante("pro")
    pagina = unsub(uid, "get").text
    assert notif.ORIENTACAO in pagina and "dicas e insights" not in pagina
    assert "e-mails opcionais do Piggy, inclusive os dos agentes" in pagina
    r = TestClient(dashboard.app).get("/unsubscribe", params={"uid": uid, "token": "lixo"})
    assert r.status_code == 400 and notif.ORIENTACAO not in r.text


# ── D4 ────────────────────────────────────────────────────────────────────────

def test_D4_texto_segue_o_fuso_e_a_hora_do_envio(monkeypatch):
    monkeypatch.delenv("REPORT_TIMEZONE", raising=False)
    monkeypatch.delenv("TZ", raising=False)
    assert "programado para as 10h (horário de Brasília)" in notif.texto_adesao()
    monkeypatch.setenv("REPORT_TIMEZONE", "America/Manaus")
    assert "(America/Manaus)" in notif.texto_adesao() and "Brasília" not in notif.texto_adesao()
    monkeypatch.setattr(notif, "HORA_ENVIO", 11)
    assert "as 11h (America/Manaus)" in notif.texto_adesao()
    uid = usuario_pagante("pro")
    assert get(uid)["insight_alert_email_notice"] == notif.texto_adesao()


# ── D5 ────────────────────────────────────────────────────────────────────────

def test_D5_nota_antes_de_desligar_insights_so_com_adesao():
    uid = usuario_pagante("pro")
    assert get(uid)["insight_email_off_notice"] is None
    aderir(uid)
    assert get(uid)["insight_email_off_notice"] == notif.AVISO_AO_DESLIGAR_INSIGHTS
    d = ok(uid, insight_email_enabled=False)
    assert d["insight_alert_email_enabled"] is False and d["insight_email_off_notice"] is None


# ── T28: init_db do estado legado misto ───────────────────────────────────────

@pytest.mark.parametrize("tip,insight", [(False, True), (True, False), (False, False)])
def test_T28_init_db_alinha_cancelamento_legado(tip, insight):
    uid = usuario_pagante("pro")
    sql("update auth_accounts set engagement_opt_out = true, tip_email_opt_out = %s, insight_email_opt_out = %s"
        " where user_id = %s", (tip, insight, uid))
    db.init_db()
    f = flags(uid)
    assert (f["e"], f["t"], f["i"]) == (True, True, True)


# ── mutações que sobreviveram ao Tester (`mut1.log`) ─────────────────────────

def test_M01_M02_reativar_e_ligar_insights_nao_derrubam_a_adesao():
    uid = usuario_pagante("pro")
    aderir(uid)
    set_engagement_opt_out(uid, False)
    ok(uid, insight_email_enabled=True)
    assert insight_aviso_aderiu(uid)


@pytest.mark.parametrize("escritor", [set_engagement_opt_out, set_insight_email_opt_out])
def test_M03_M04_cancelar_sem_adesao_nao_audita(escritor):
    uid = usuario_pagante("pro")
    escritor(uid, True)
    assert eventos(uid) == []


def test_M05_sem_email_desliga():
    uid = usuario_pagante("pro")
    aderir(uid)
    c = cliente(uid)
    sql("update auth_accounts set email = '', email_enc = null, email_hash = null where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    assert c.patch(f"/settings/{uid}/notifications", json=DESLIGA).status_code == 200
    assert not insight_aviso_aderiu(uid)
    misto = {"insight_alert_email_enabled": False, "monthly_report_enabled": True}   # pela regra normal também
    assert c.patch(f"/settings/{uid}/notifications", json=misto).status_code == 200


def test_M07_disponivel_le_insights_no_modo_legado(monkeypatch):
    """Com o freio `PLANS_V2_ENABLED=0`, o Grátis tem `insights` e não tem `forecast`."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "0")
    uid = usuario_pagante("pro")
    sql("update auth_accounts set plan = 'free', plan_selected_at = now() where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    assert get(uid)["insight_alert_email_available"] is True


def test_M08_recusa_de_plano_nomeia_insights():
    uid = usuario_pagante("essencial")
    r = patch(uid, insight_alert_email_enabled=True)
    assert r.status_code == 403 and r.json()["detail"] == {"error": "pro_required", "feature": "insights"}


def test_M09_retirada_pelo_interruptor_tem_ip_e_user_agent():
    uid = usuario_pagante("pro")
    aderir(uid)
    ok(uid, **DESLIGA)
    e = eventos(uid)[-1]
    assert e["event"] == AuditEvent.INSIGHT_AVISO_RETIRADA and e["ip"] and e["user_agent"]


def test_M10_retirada_falhou_os_flags_nao_ficam(monkeypatch):
    uid = usuario_pagante("pro")
    aderir(uid)

    def _quebra(cur, user_id):
        raise RuntimeError("caiu")
    monkeypatch.setattr(db.reports, "_retira_aviso", _quebra)
    with pytest.raises(RuntimeError):
        set_engagement_opt_out(uid, True)
    f = flags(uid)
    assert (f["e"], f["t"], f["i"]) == (False, False, False) and f["aderiu"] is not None


def test_M14_fora_da_env_a_adesao_gravada_aparece(monkeypatch):
    uid = usuario_pagante("pro")
    aderir(uid)
    monkeypatch.setenv("INSIGHT_EMAIL_USERS", "")
    d = get(uid)
    assert d["insight_alert_email_enabled"] is True and d["insight_alert_email_notice"] is None


def test_M17_exportacao_tem_as_colunas_exatas_sem_pii_cifrada():
    uid = usuario_pagante("pro")
    aderir(uid)
    linha = json.loads(zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid))).read("dados.json"))["dados"]["conta_login"][0]
    assert set(linha) == {
        "id", "user_id", "display_name", "email", "phone_e164", "phone_status", "phone_confirmed_at", "whatsapp_verified_at",
        "plan", "plan_expires_at", "created_at", "stripe_customer_id", "engagement_opt_out", "last_activity_at",
        "last_tip_sent_at", "tip_email_opt_out", "last_insight_sent_at", "insight_email_opt_out",
        "insight_aviso_aderiu_em", "insight_aviso_coberto_ate", "whatsapp_updates_opt_out",
        "last_reengagement_sent_at", "deletion_requested_at", "deletion_scheduled_for", "deletion_status",
        "deletion_processing_started_at", "dashboard_profile", "signup_quiz"}


@pytest.mark.parametrize("env", ["01", "0001", "1.0", " 1x"])
def test_M19_env_compara_texto(monkeypatch, env):
    monkeypatch.setenv("INSIGHT_EMAIL_USERS", env)
    assert notif.liberado(1) is False


def test_M20_todo_evento_tem_rotulo():
    eventos_ = {v for k, v in vars(AuditEvent).items() if k.isupper()}
    assert eventos_ <= set(EVENT_LABELS_PT_BR)
    assert EVENT_LABELS_PT_BR[AuditEvent.INSIGHT_AVISO_RETIRADA] == "Avisos do Piggy por e-mail desligados"


def test_permitido_sem_direito_real_mesmo_com_marca_em_dia():
    """§10: o portão exige o direito real. Carência: marca em dia, `plan_gate_ok` falso."""
    uid = usuario_pagante("pro")
    aderir(uid)
    sql("update auth_accounts set plan = 'free', last_payment_status = 'past_due', past_due_since = now()"
        " where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    assert insight_aviso_aderiu(uid) and flags(uid)["coberto"] > datetime.now(timezone.utc) + timedelta(days=6)
    assert insight_email_permitido(uid) is False


# ── rodada 3: selo de carência do servidor, chat "parar emails" ───────────────

def test_selo_de_carencia_vem_do_servidor_e_so_com_adesao():
    from pathlib import Path
    uid = usuario_pagante("pro")
    assert get(uid)["insight_alert_email_paused_notice"] is None
    aderir(uid)
    assert get(uid)["insight_alert_email_paused_notice"] is None, "com plano não está pausado"
    sql("update auth_accounts set plan = 'free', last_payment_status = 'past_due', past_due_since = now(),"
        " plan_selected_at = now() where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    assert get(uid)["insight_alert_email_paused_notice"] == notif.AVISO_PAUSADO
    raiz = Path(__file__).resolve().parent.parent
    for tela in ("frontend/settings.html", "webapp/src/dashboard/conta/Notificacoes.tsx"):
        assert notif.AVISO_PAUSADO not in (raiz / tela).read_text(encoding="utf-8"), f"{tela} guarda cópia do selo"


def test_chat_parar_emails_diz_o_alcance_igual_a_pagina():
    uid = usuario_pagante("pro")
    texto = diga(uid, "parar emails")
    assert "e-mails opcionais do Piggy, inclusive os dos agentes" in texto
    assert "e-mails opcionais do Piggy, inclusive os dos agentes" in unsub(uid, "get").text
