"""PL-06 B6, PR 1: adesão explícita ao aviso de insight por e-mail.

Postgres real, passando pelas rotas reais (`PATCH /settings/{uid}/notifications`,
`/unsubscribe`, `/settings/reset`) e pela conversa (`handle_incoming`). Nada de envio
aqui: ele é o PR 2. `insight_email_permitido` é o portão que o PR 2 vai ler.

Decisões do dono que este arquivo prende: P4 (c) só quem optou, P6 (a) o reset limpa,
P7 (b) cancelar retira e nada religa sem o interruptor novo.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

import db
import db.reports
import db_support
import frontend.finance_bot_websocket_custom as dashboard
from _insights_adesao_apoio import (  # noqa: F401 — `ambiente` é fixture
    aderir as _aderir, ambiente, cliente as _cliente, diga as _diga, eventos as _eventos,
    flags as _flags, get as _get, ok as _ok, patch as _patch, sql as _sql, unsub as _unsub,
)
from conftest import promote_to_pro, usuario_pagante
from core.audit import AuditEvent
from core.services.insights_notificacao import liberado, texto_adesao
from db.privacy import build_user_export_zip, delete_user_data
from db.reports import insight_aviso_aderiu, insight_email_permitido

RAIZ = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.usefixtures("ambiente")


# ── adesão explícita ─────────────────────────────────────────────────────────

def test_interruptor_antigo_ligado_nao_e_adesao():
    uid = usuario_pagante("pro")
    d = _get(uid)
    assert d["insight_email_enabled"] is True and d["tip_email_enabled"] is True
    assert d["insight_alert_email_enabled"] is False and d["insight_alert_email_available"] is True
    assert insight_email_permitido(uid) is False


def test_aderir_grava_data_e_um_audit_e_repetir_nao_reescreve():
    uid = usuario_pagante("pro")
    assert _get(uid)["insight_alert_email_notice"] == texto_adesao()
    _aderir(uid)
    antes = _flags(uid)["aderiu"]
    assert antes is not None
    _ok(uid, insight_alert_email_enabled=True)
    assert _flags(uid)["aderiu"] == antes
    eventos = _eventos(uid)
    assert [e["event"] for e in eventos] == [AuditEvent.INSIGHT_AVISO_ADESAO]
    assert eventos[0]["ip"] and eventos[0]["user_agent"]
    atividade = _cliente(uid).get(f"/settings/{uid}/activity").json()["events"]
    assert "Avisos do Piggy por e-mail ligados" in [e["event_label"] for e in atividade]


def test_desligar_o_interruptor_retira_e_audita_so_quando_muda():
    uid = usuario_pagante("pro")
    _aderir(uid)
    assert _ok(uid, insight_alert_email_enabled=False)["insight_alert_email_enabled"] is False
    _ok(uid, insight_alert_email_enabled=False)
    assert _flags(uid)["aderiu"] is None and insight_email_permitido(uid) is False
    assert [e["event"] for e in _eventos(uid)] == [AuditEvent.INSIGHT_AVISO_ADESAO, AuditEvent.INSIGHT_AVISO_RETIRADA]
    assert _flags(uid)["i"] is False, "desligar só o aviso não mexe nos outros e-mails"


# ── recusas: nada escrito ────────────────────────────────────────────────────

def test_fora_da_env_a_tela_some_e_a_rota_recusa_sem_escrever(monkeypatch):
    uid, vizinho = usuario_pagante("pro"), usuario_pagante("pro")
    monkeypatch.setenv("INSIGHT_EMAIL_USERS", str(vizinho))
    assert _get(uid)["insight_alert_email_notice"] is None
    r = _patch(uid, insight_alert_email_enabled=True, tip_email_enabled=False)
    assert r.status_code == 403, r.text
    assert _flags(uid)["aderiu"] is None and _flags(uid)["t"] is False
    assert _get(vizinho)["insight_alert_email_notice"] == texto_adesao()
    _aderir(vizinho)


def test_env_vazia_esconde_de_todos(monkeypatch):
    uid = usuario_pagante("pro")
    monkeypatch.setenv("INSIGHT_EMAIL_USERS", "")
    assert _get(uid)["insight_alert_email_notice"] is None
    assert _patch(uid, insight_alert_email_enabled=True).status_code == 403


@pytest.mark.parametrize("valor,esperado", [
    (None, False), ("", False), ("   ", False), (",", False), ("abc", False), ("1234", False), ("123", True),
    ("7, 123 ,9", True), ("*", True), ("7,*", True),
])
def test_liberado_le_a_env(monkeypatch, valor, esperado):
    monkeypatch.delenv("INSIGHT_EMAIL_USERS", raising=False)
    if valor is not None:
        monkeypatch.setenv("INSIGHT_EMAIL_USERS", valor)
    assert liberado(123) is esperado


def test_plano_sem_insights_recusa_e_plus_liga():
    uid = usuario_pagante("essencial")
    assert _get(uid)["insight_alert_email_available"] is False
    r = _patch(uid, insight_alert_email_enabled=True)
    assert r.status_code == 403 and r.json()["detail"]["error"] == "pro_required", r.text
    assert _flags(uid)["aderiu"] is None
    promote_to_pro(uid, "pro")
    _aderir(uid)


def test_sem_email_recusa():
    uid = usuario_pagante("pro")
    cliente = _cliente(uid)
    _sql("update auth_accounts set email = '', email_enc = null, email_hash = null where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    assert cliente.patch(f"/settings/{uid}/notifications", json={"insight_alert_email_enabled": True}).status_code == 400
    assert _flags(uid)["aderiu"] is None


@pytest.mark.parametrize("corte", [{"insight_email_enabled": False}, {"engagement_email_enabled": False}])
def test_com_opt_out_aderir_e_409_e_o_resto_do_corpo_nao_grava(corte):
    uid = usuario_pagante("pro")
    _ok(uid, **corte)
    mensal = _get(uid)["monthly_report_enabled"]
    r = _patch(uid, insight_alert_email_enabled=True, monthly_report_enabled=not mensal)
    assert r.status_code == 409, r.text
    assert _flags(uid)["aderiu"] is None and _get(uid)["monthly_report_enabled"] is mensal


def test_corte_no_mesmo_corpo_vence_a_adesao():
    uid = usuario_pagante("pro")
    assert _ok(uid, insight_alert_email_enabled=True, insight_email_enabled=False)["insight_alert_email_enabled"] is False
    assert _flags(uid)["aderiu"] is None


def test_sem_csrf_e_uid_alheio_nao_escrevem():
    a, b = usuario_pagante("pro"), usuario_pagante("pro")
    c = _cliente(a)
    assert c.patch(f"/settings/{b}/notifications", json={"insight_alert_email_enabled": True}).status_code == 403
    del c.headers[dashboard.CSRF_HEADER_NAME]
    assert c.patch(f"/settings/{a}/notifications", json={"insight_alert_email_enabled": True}).status_code == 403
    assert _flags(a)["aderiu"] is None and _flags(b)["aderiu"] is None


def test_carencia_pausa_e_desligar_pode_mesmo_fora_da_env(monkeypatch):
    uid = usuario_pagante("pro")
    _aderir(uid)
    _em_carencia_sem_plano(uid)
    d = _get(uid)
    assert d["insight_alert_email_enabled"] is True and d["insight_alert_email_available"] is False
    assert insight_email_permitido(uid) is False, "em carência a adesão fica e o aviso não sai"
    monkeypatch.setenv("INSIGHT_EMAIL_USERS", "")
    # Corpo misto: pela regra NORMAL (não pela exceção do D2), desligar não pede plano nem env.
    d = _ok(uid, insight_alert_email_enabled=False, monthly_report_enabled=True)
    assert d["insight_alert_email_enabled"] is False and not insight_aviso_aderiu(uid)


def _em_carencia_sem_plano(uid):
    """Cartão recusado há pouco, com `plan='free'` gravado pela varredura (célula 3): carência aberta."""
    _sql("update auth_accounts set plan = 'free', plan_expires_at = null, last_payment_status = 'past_due', plan_selected_at = now(),"
         " past_due_since = now() where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)


# ── cancelar retira; nada religa ─────────────────────────────────────────────

CORTES = {
    "post_unsubscribe": lambda uid: _unsub(uid, "post"),
    "get_unsubscribe": lambda uid: _unsub(uid, "get"),
    "chat_parar_emails": lambda uid: _diga(uid, "parar emails"),
    "patch_engagement": lambda uid: _ok(uid, engagement_email_enabled=False),
    "patch_insights": lambda uid: _ok(uid, insight_email_enabled=False),
}


@pytest.mark.parametrize("corte", CORTES)
def test_todo_cancelamento_retira_a_adesao(corte):
    uid = usuario_pagante("pro")
    vizinho = usuario_pagante("pro")
    _aderir(uid)
    _aderir(vizinho)
    CORTES[corte](uid)
    assert _flags(uid)["aderiu"] is None and insight_email_permitido(uid) is False
    assert _eventos(uid)[-1]["event"] == AuditEvent.INSIGHT_AVISO_RETIRADA
    assert insight_email_permitido(vizinho) is True, "o cancelamento de um não toca no outro"


@pytest.mark.parametrize("metodo", ["post", "get"])
def test_unsubscribe_grava_os_tres_flags_e_outro_interruptor_nao_desfaz(metodo):
    uid = usuario_pagante("pro")
    _aderir(uid)
    _unsub(uid, metodo)
    f = _flags(uid)
    assert (f["e"], f["t"], f["i"]) == (True, True, True)
    d = _ok(uid, tip_email_enabled=True)   # o `sync_engagement_opt_out` roda aqui
    assert d["tip_email_enabled"] is True
    assert d["insight_email_enabled"] is False and _flags(uid)["i"] is True
    assert d["insight_alert_email_enabled"] is False and insight_email_permitido(uid) is False


REATIVACOES = {
    "chat_reativar_emails": lambda uid: _diga(uid, "reativar emails"),
    "chat_quero_emails": lambda uid: _diga(uid, "quero emails"),
    "patch_engagement": lambda uid: _ok(uid, engagement_email_enabled=True),
    "patch_insights": lambda uid: _ok(uid, tip_email_enabled=True, insight_email_enabled=True),
}


@pytest.mark.parametrize("volta", REATIVACOES)
def test_reativar_os_emails_nao_religa_o_aviso_e_o_interruptor_religa(volta):
    uid = usuario_pagante("pro")
    _aderir(uid)
    _unsub(uid)
    REATIVACOES[volta](uid)
    f = _flags(uid)
    assert (f["e"], f["i"]) == (False, False), "o caminho tem de ter religado os e-mails"
    assert f["aderiu"] is None and insight_email_permitido(uid) is False
    assert _get(uid)["insight_alert_email_enabled"] is False
    _aderir(uid)   # a única volta: a ação explícita no interruptor novo


def test_reativar_o_plano_e_o_boot_nao_religam():
    uid = usuario_pagante("pro")
    _aderir(uid)
    _unsub(uid)
    promote_to_pro(uid, "essencial")
    promote_to_pro(uid, "pro")
    db.init_db()
    assert _flags(uid)["aderiu"] is None and insight_email_permitido(uid) is False


def test_conversa_gasto_parar_e_reativar_nao_religa():
    uid = usuario_pagante("pro")
    _aderir(uid)
    assert "50" in _diga(uid, "gastei 50 no mercado")
    assert "não vai mais receber" in _diga(uid, "parar emails")
    assert "20" in _diga(uid, "gastei 20 no uber")
    assert "voltará a receber" in _diga(uid, "reativar emails")
    assert _flags(uid)["aderiu"] is None and insight_email_permitido(uid) is False


# ── ciclo de vida da conta ───────────────────────────────────────────────────

def test_recomecar_do_zero_limpa_a_adesao():
    from db.users import _hash_password
    uid = usuario_pagante("pro")
    _sql("update auth_accounts set password_hash = %s where user_id = %s", (_hash_password("Senha-B6-forte!9"), uid))
    db_support.invalidate_auth_user_cache(uid)
    _aderir(uid)
    r = _cliente(uid).post("/settings/reset", json={"password": "Senha-B6-forte!9"})
    assert r.status_code == 200, r.text
    assert _flags(uid)["aderiu"] is None and insight_email_permitido(uid) is False
    assert _get(uid)["insight_email_enabled"] is True, "o reset não mexe nas outras preferências de e-mail"


def test_merge_login_da_origem_leva_a_adesao_para_o_destino_sem_login(user_id):
    origem = usuario_pagante("pro")
    _aderir(origem)
    _em_carencia_sem_plano(origem)   # o merge recusa origem com plano pago; em carência a adesão fica
    db.merge_users(origem, user_id)
    assert insight_aviso_aderiu(user_id) is True
    assert not _sql("select 1 from auth_accounts where user_id = %s", (origem,))


def test_merge_origem_sem_login_nao_mexe_na_adesao_do_destino(user_id):
    destino = usuario_pagante("pro")
    _aderir(destino)
    db.merge_users(user_id, destino)
    assert insight_email_permitido(destino) is True


def test_merge_dois_logins_recusa_e_cada_um_fica_com_a_sua():
    origem, destino = usuario_pagante("pro"), usuario_pagante("pro")
    _aderir(origem)
    _em_carencia_sem_plano(origem)
    with pytest.raises(db.MergeRefused):
        db.merge_users(origem, destino)
    assert insight_aviso_aderiu(origem) is True and insight_aviso_aderiu(destino) is False


def test_exportacao_traz_a_adesao():
    uid = usuario_pagante("pro")
    _aderir(uid)
    dados = json.loads(zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid))).read("dados.json"))["dados"]
    assert dados["conta_login"][0]["insight_aviso_aderiu_em"]


def test_exclusao_leva_a_adesao():
    uid = usuario_pagante("pro")
    _aderir(uid)
    delete_user_data(uid)
    assert not _sql("select 1 from auth_accounts where user_id = %s", (uid,))
    assert insight_email_permitido(uid) is False and insight_aviso_aderiu(uid) is False


# ── a leitura estrita do PR 2 ────────────────────────────────────────────────

def test_permitido_conta_ausente_e_false():
    assert insight_email_permitido(987_654_321_012) is False


def test_permitido_erro_de_banco_sobe(monkeypatch):
    def _caiu():
        raise RuntimeError("banco fora")
    monkeypatch.setattr(db.reports, "get_conn", _caiu)
    with pytest.raises(RuntimeError):
        insight_email_permitido(1)


@pytest.mark.parametrize("sql_bloqueio", [
    "insight_email_opt_out = true",
    "engagement_opt_out = true",
    "deletion_status = 'scheduled'",
    "deletion_status = 'processing'",
])
def test_permitido_cada_bloqueio_sozinho_barra_e_soltar_volta(sql_bloqueio):
    uid = usuario_pagante("pro")
    _aderir(uid)
    _sql(f"update auth_accounts set {sql_bloqueio} where user_id = %s", (uid,))
    assert insight_email_permitido(uid) is False
    _sql("update auth_accounts set insight_email_opt_out = false, engagement_opt_out = false,"
         " deletion_status = null where user_id = %s", (uid,))
    assert insight_email_permitido(uid) is True


# ── uma fonte de verdade ─────────────────────────────────────────────────────

def test_texto_diz_canal_adesao_opcional_e_como_parar_e_so_o_servidor_tem():
    for trecho in ("Opcional", "No máximo um e-mail por dia, programado para as 10h", "somente quando houver novidade",
                   "conta a vencer", "Cancelar inscrição", "dicas de uso, Insights financeiros, estes avisos e os e-mails dos agentes",
                   "segurança e de cobrança continuam", "desligue aqui"):
        assert trecho in texto_adesao()
    for tela in ("frontend/settings.html", "webapp/src/dashboard/conta/Notificacoes.tsx",
                 "webapp/src/dashboard/conta/api.ts"):
        assert "Cancelar inscrição" not in (RAIZ / tela).read_text(encoding="utf-8"), tela
