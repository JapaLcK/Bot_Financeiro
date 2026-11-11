"""PL-06 B6, D1: perder o direito a `insights` depois da carência retira a adesão (gatilho do banco).

O gatilho mora em `db/insight_aviso_direito.py`. Aqui se mede:

  • DIFERENCIAL (T30): matriz plano × validade × status × relógio de atraso. A adesão fica se e só se
    `plan_gate_ok(uid, "insights") or carencia_aberta(...)`, a regra Python, na mesma linha. Nas duas
    direções: aderir numa linha da matriz (passo 2) e chegar a ela vindo de uma adesão (passo 5).
  • VOLTA SEM VARREDURA (T31): o relógio passou além da margem sem escrita nenhuma; o pagamento chega
    pelo webhook real, pelo Pix real, pelo admin ou por SQL cru: a adesão some, 1 audit `direito`.
  • CARÊNCIA na ordem real do `_materializar_assinatura` (status ANTES do plano, T32), RENOVAÇÃO gravada
    pela varredura depois do vencimento (T33), PERDA por escrita (T34), VITALÍCIO e bordas (T35).
  • a trava do `*` (T40).

"O relógio passou" sem escrita se simula com `session_replication_role = replica` numa transação só:
o gatilho não roda nela, que é exatamente o que acontece quando o tempo passa sem ninguém escrever.
Pede superusuário no Postgres de teste (o da suíte e o do CI são).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db
import db_support
from _billing_grants_helpers import (
    FakeStripeSubs, conta as _conta, evt_checkout, evt_deleted, evt_paid, garantir_system_event_logs,
    set_customer,
)
from _insights_adesao_apoio import aderir, ambiente, eventos, flags, sql  # noqa: F401 — `ambiente` é fixture
from conftest import promote_to_pro, usuario_pagante
from core.services import insights_notificacao
from core.services.billing_dunning import carencia_aberta
from core.services.plan_service import plan_gate_ok
from db.plan_grants import upsert_grant
from db.reports import insight_aviso_aderiu, insight_email_permitido, set_insight_aviso

pytestmark = pytest.mark.usefixtures("ambiente")
RAIZ = Path(__file__).resolve().parent.parent


def _agora():
    return datetime.now(timezone.utc)


def _relogio_passou(uid, *, expira_ha=None, atraso_ha=None, coberto_ate=None):
    """O tempo passou SEM escrita: move as datas com o gatilho desligado nesta transação."""
    sets, args = [], []
    for col, val in (("plan_expires_at", expira_ha), ("past_due_since", atraso_ha)):
        if val is not None:
            sets.append(f"{col} = %s")
            args.append(_agora() - val)
    sets.append("insight_aviso_coberto_ate = %s")
    args.append(coberto_ate)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local session_replication_role = replica")
        cur.execute(f"update auth_accounts set {', '.join(sets)} where user_id = %s", (*args, uid))
        conn.commit()
    db_support.invalidate_auth_user_cache(uid)


def _retirada_por_direito(uid):
    ev = [e for e in eventos(uid) if (e["details"] or {}).get("motivo") == "direito"]
    return len(ev)


def _assinante(uid, dias=1):
    """Conta com plano Plus por `dias` e adesão gravada pela rota."""
    _conta(uid, "pro", _agora() + timedelta(days=dias), "active")
    sql("update auth_accounts set plan_selected_at = now() where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    aderir(uid)


# ── T30: diferencial SQL × Python ─────────────────────────────────────────────

_PLANOS = ("free", "essencial", "pro", "pro_max", "PRO")
_VALIDADES = (None, timedelta(days=30), -timedelta(days=3))
_STATUS = ("active", "trialing", "past_due", "unpaid", "canceled", "inactive", "", "PAST_DUE")
_ATRASOS = (None, timedelta(days=2), timedelta(days=8))
_CELULAS = [(p, v, s, a) for p in _PLANOS for v in _VALIDADES for s in _STATUS for a in _ATRASOS]


def _escreve(uid, plano, validade, status, atraso):
    sql("update auth_accounts set plan = %s, plan_expires_at = %s, last_payment_status = %s,"
        " past_due_since = %s where user_id = %s",
        (plano, None if validade is None else _agora() + validade, status,
         None if atraso is None else _agora() - atraso, uid))
    db_support.invalidate_auth_user_cache(uid)


def _cobertura_py(plano, expira, status, desde):
    """A cobertura em Python, das MESMAS constantes que o `ddl()` usa (`pb_insight_cobertura` em SQL)."""
    from core.services.billing_dunning import DUNNING_GRACE_DAYS, PAST_DUE_PAYMENT_STATUSES
    from core.services.insights_notificacao import planos_com_insights
    pernas = []
    if (plano or "").lower() in planos_com_insights():
        pernas.append(expira or datetime.max.replace(tzinfo=timezone.utc))
    if desde is not None and (status or "").lower() in PAST_DUE_PAYMENT_STATUSES:
        pernas.append(desde + timedelta(days=DUNNING_GRACE_DAYS))
    return max(pernas) if pernas else None


@pytest.mark.parametrize("plano", _PLANOS)
def test_T30_gatilho_e_regra_python_concordam_em_toda_a_matriz(plano):
    """Três lados: a regra real (`plan_gate_ok or carencia_aberta`), a cobertura em Python e o gatilho.

    Aderir numa linha (passo 2) pega se e só se há direito. Chegar à linha já aderido (uma escrita, vindo de
    vitalício): com direito fica; sem cobertura nenhuma é perda natural agora (fica, marca = instante da
    perda), inclusive Essencial vigente (o gatilho não infere downgrade, §15); cobertura vencida além da
    margem retira.
    """
    from core.services.insights_notificacao import MARGEM_DIREITO
    divergencias = []
    for p, v, s, a in [c for c in _CELULAS if c[0] == plano]:
        uid = usuario_pagante("pro")
        _escreve(uid, p, v, s, a)
        linha, agora = db.get_auth_user(uid), _agora()
        direito = plan_gate_ok(uid, "insights") or carencia_aberta(linha["past_due_since"], s, agora)
        c = _cobertura_py(p, linha["plan_expires_at"], s, linha["past_due_since"])
        assert direito == (c is not None and c >= agora), ((p, v, s, a), "cobertura em Python diverge da regra real")
        aderiu = set_insight_aviso(uid, True)                        # passo 2: aderir nesta linha
        _escreve(uid, "pro", None, "active", None)                   # volta a vitalício e adere
        set_insight_aviso(uid, True)
        assert insight_aviso_aderiu(uid)
        _escreve(uid, p, v, s, a)                                    # chegar à linha aderido
        ficou = insight_aviso_aderiu(uid)
        esperado = (direito, direito or c is None or c >= agora - MARGEM_DIREITO)
        if (aderiu, ficou) != esperado:
            divergencias.append(((p, v, s, a), esperado, aderiu, ficou))
    assert divergencias == []


def test_T30_positivo_celulas_pagas_ficam_e_a_matriz_tem_os_dois_lados():
    uid = usuario_pagante("pro")
    aderir(uid)
    _escreve(uid, "pro_max", timedelta(days=30), "active", None)
    assert insight_aviso_aderiu(uid) and insight_email_permitido(uid)
    _escreve(uid, "essencial", timedelta(days=30), "active", None)
    assert insight_aviso_aderiu(uid) and not insight_email_permitido(uid), "Essencial por escrita crua é perda natural (§15)"


# ── T31: volta sem varredura, por todos os escritores ────────────────────────

@pytest.fixture
def webhook(monkeypatch):
    from test_billing_webhook_lifecycle import _cleanup_trial, _post, _setup
    garantir_system_event_logs()
    criados = []

    def novo(sufixo):
        uid, client, fake = _setup(monkeypatch, f"b6-{sufixo}")
        criados.append(uid)
        return uid, (lambda evento, subs=None: _post(client, fake, evento, subs=subs))

    yield novo
    for uid in criados:
        _cleanup_trial(uid)


def _sub(dias=30, status="active"):
    from test_billing_webhook_lifecycle import _fake_sub
    return _fake_sub(status, days=dias)


def _assinou_pelo_webhook(webhook, sufixo):
    uid, post = webhook(sufixo)
    t = int(_agora().timestamp())
    assert post(evt_checkout(uid, "sub_b6", t, f"cs_{uid}"), subs={"sub_b6": _sub()}).status_code == 200
    assert db.get_auth_user(uid)["plan"] == "pro"
    aderir(uid)
    return uid, post, t


@pytest.mark.parametrize("horas,fica", [(3, False), (1.25, False), (0.5, True)])   # 1h15: mata a margem dobrada
def test_T31_invoice_paid_depois_da_lacuna(webhook, horas, fica):
    uid, post, t = _assinou_pelo_webhook(webhook, f"paid{horas}")
    ate = _agora() - timedelta(hours=horas)
    _relogio_passou(uid, expira_ha=timedelta(hours=horas), coberto_ate=ate)
    assert post(evt_paid(uid, "sub_b6", t + 10), subs={"sub_b6": _sub()}).status_code == 200
    assert db.get_auth_user(uid)["plan_expires_at"] > _agora(), "o pagamento devolveu o plano"
    assert insight_aviso_aderiu(uid) is fica
    assert insight_email_permitido(uid) is fica
    assert _retirada_por_direito(uid) == (0 if fica else 1)


def test_T31_pix_depois_da_lacuna(user_id):
    from core.services.billing_access import recompute_entitlement
    garantir_system_event_logs()
    _assinante(user_id)
    _relogio_passou(user_id, expira_ha=timedelta(days=2), coberto_ate=_agora() - timedelta(days=2))
    upsert_grant(user_id, "pix", f"pix:{user_id}", "pro", _agora() - timedelta(minutes=1),
                 _agora() + timedelta(days=30), 1, f"evt_pix_b6_{user_id}")
    assert recompute_entitlement(user_id) is not None
    assert db.get_auth_user(user_id)["plan_expires_at"] > _agora()
    assert not insight_aviso_aderiu(user_id) and _retirada_por_direito(user_id) == 1


@pytest.mark.parametrize("escritor", ["admin", "sql_cru"])
def test_T31_admin_e_sql_cru_depois_da_lacuna(user_id, escritor):
    from core.admin_dashboard import set_account_plan
    _assinante(user_id)
    _relogio_passou(user_id, expira_ha=timedelta(hours=2), coberto_ate=_agora() - timedelta(hours=2))
    if escritor == "admin":
        set_account_plan("pro", 12, user_id=user_id)
    else:
        promote_to_pro(user_id)
    assert not insight_aviso_aderiu(user_id) and _retirada_por_direito(user_id) == 1


# ── T32: carência, na ordem real do webhook ──────────────────────────────────

@pytest.mark.parametrize("dias,fica", [(5, True), (8, False)])
def test_T32_pagou_na_carencia(webhook, dias, fica):
    from test_billing_dunning_webhook import _INVOICE_SUB, _failed
    uid, post, t = _assinou_pelo_webhook(webhook, f"car{dias}")
    assert post(_failed(uid, f"evt_b6_f{dias}", t + 5), subs={_INVOICE_SUB: _sub(status="past_due")}).status_code == 200
    assert db.get_auth_user(uid)["last_payment_status"] == "past_due" and insight_aviso_aderiu(uid)
    assert insight_email_permitido(uid) is True, "plano ainda vigente: em atraso o aviso segue saindo"
    _relogio_passou(uid, expira_ha=timedelta(days=dias), atraso_ha=timedelta(days=dias),
                    coberto_ate=_agora() - timedelta(days=dias) + timedelta(days=7))
    assert insight_email_permitido(uid) is False, "sem direito real o portão fecha, com ou sem carência"
    assert post(evt_paid(uid, "sub_b6", t + 20), subs={"sub_b6": _sub()}).status_code == 200
    assert db.get_auth_user(uid)["last_payment_status"] == "active"
    assert insight_aviso_aderiu(uid) is fica
    assert _retirada_por_direito(uid) == (0 if fica else 1)


def test_T32_falha_com_plano_vencido_dentro_da_margem_fica(webhook):
    from test_billing_dunning_webhook import _INVOICE_SUB, _failed
    uid, post, t = _assinou_pelo_webhook(webhook, "carmargem")
    _relogio_passou(uid, expira_ha=timedelta(minutes=30), coberto_ate=_agora() - timedelta(minutes=30))
    assert post(_failed(uid, "evt_b6_fm", t + 5), subs={_INVOICE_SUB: _sub(status="past_due")}).status_code == 200
    assert insight_aviso_aderiu(uid), "a carência começou antes da margem acabar"
    assert flags(uid)["coberto"] > _agora() + timedelta(days=6)


# ── T33: renovação gravada pela varredura depois do vencimento ───────────────

def test_T33_varredura_estica_depois_do_vencimento_e_a_adesao_fica(user_id, monkeypatch):
    from core.services.billing_access import recompute_entitlement
    _assinante(user_id)
    set_customer(user_id, f"cus_{user_id}")
    agora = _agora()
    upsert_grant(user_id, "stripe", f"sub_ren_{user_id}", "pro", agora - timedelta(days=30),
                 agora - timedelta(minutes=10), 1_000)
    _relogio_passou(user_id, expira_ha=timedelta(minutes=10), coberto_ate=agora - timedelta(minutes=10))
    fim = agora + timedelta(days=30)
    viva = {"id": f"sub_ren_{user_id}", "status": "active", "current_period_end": int(fim.timestamp()),
            "items": {"data": [{"current_period_end": int(fim.timestamp())}]}}
    monkeypatch.setitem(__import__("sys").modules, "stripe", FakeStripeSubs(ativa=viva))
    recompute_entitlement(user_id, origem="varredura")
    assert db.get_auth_user(user_id)["plan_expires_at"] > agora + timedelta(days=29)
    assert insight_aviso_aderiu(user_id), "renovação gravada depois do vencimento derrubou o assinante"
    assert flags(user_id)["coberto"] > agora + timedelta(days=29), "a marca não avançou"


# ── T34: perda por escrita, no prazo ──────────────────────────────────────────

def test_T34_downgrade_e_encurtar_e_upgrade(user_id):
    from core.admin_dashboard import set_account_plan
    _assinante(user_id, dias=400)
    set_account_plan("pro_max", 12, user_id=user_id)
    assert insight_aviso_aderiu(user_id), "Plus → Pro mantém"
    set_account_plan("pro", 1, user_id=user_id)
    assert flags(user_id)["coberto"] < _agora() + timedelta(days=40), "encurtar move a marca para trás"
    set_account_plan("essencial", 12, user_id=user_id)
    assert not insight_aviso_aderiu(user_id)
    assert [e["details"] for e in eventos(user_id) if e["event"] == "insight_aviso_retirada"] == [{"motivo": "admin"}]


def test_T34_subscription_deleted_sem_reason_e_expiracao_natural(webhook):
    """Sem `cancellation_details.reason` o `deleted` cai no default: perda natural, adesão guardada até 1 h, portão fechado."""
    uid, post, t = _assinou_pelo_webhook(webhook, "del")
    assert post(evt_deleted(uid, "sub_b6", t + 10)).status_code == 200
    assert db.get_auth_user(uid)["plan"] == "free"
    assert insight_aviso_aderiu(uid) and not insight_email_permitido(uid) and _retirada_por_direito(uid) == 0


def test_T34_admin_para_free_retira(user_id):
    from core.admin_dashboard import set_account_plan
    _assinante(user_id)
    set_account_plan("free", user_id=user_id)
    assert not insight_aviso_aderiu(user_id)


# ── T35: vitalício e bordas ───────────────────────────────────────────────────

def test_T35_vitalicio_fica_sem_marca_e_escrita_so_de_status_nao_mexe():
    uid = usuario_pagante("pro")
    aderir(uid)
    assert flags(uid)["coberto"] is None
    sql("update auth_accounts set last_payment_status = 'canceled' where user_id = %s", (uid,))
    assert insight_aviso_aderiu(uid) and flags(uid)["coberto"] is None


def test_T35_sem_adesao_o_gatilho_nao_age():
    uid = usuario_pagante("pro")
    sql("update auth_accounts set plan = 'free' where user_id = %s", (uid,))
    assert flags(uid)["coberto"] is None and eventos(uid) == []


def test_T35_marca_vencida_sem_escrita_fecha_o_portao_e_a_adesao_morre(user_id):
    """Sem escrita, a coluna fica gravada, mas a adesão já não está VIVA (critério único) e o portão fecha."""
    _assinante(user_id)
    _relogio_passou(user_id, expira_ha=timedelta(hours=3), coberto_ate=_agora() - timedelta(hours=3))
    assert flags(user_id)["aderiu"] is not None
    assert insight_aviso_aderiu(user_id) is False and insight_email_permitido(user_id) is False


def test_T35_margem_nao_vale_para_o_portao(user_id):
    _assinante(user_id)
    _relogio_passou(user_id, expira_ha=timedelta(minutes=10), coberto_ate=_agora() - timedelta(minutes=10))
    assert insight_aviso_aderiu(user_id) and insight_email_permitido(user_id) is False


def test_T35_com_o_freio_o_portao_ainda_le_a_marca_sem_margem(user_id, monkeypatch):
    """`PLANS_V2_ENABLED=0` faz `plan_gate_ok(insights)` valer para todos; quem segura é a marca, sem margem."""
    _assinante(user_id)
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    assert insight_email_permitido(user_id) is True
    _relogio_passou(user_id, expira_ha=timedelta(minutes=10), coberto_ate=_agora() - timedelta(minutes=10))
    assert insight_email_permitido(user_id) is False


def test_T35_com_o_freio_o_gatilho_recusa_adesao_sem_tier_e_nada_e_auditado(monkeypatch):
    """Limite declarado: com o freio o Grátis passa no `plan_gate_ok`, mas o gatilho segue por tier."""
    monkeypatch.setenv("PLANS_V2_ENABLED", "0")
    monkeypatch.setenv("ACCESS_GATE_ENABLED", "0")
    uid = usuario_pagante("pro")
    sql("update auth_accounts set plan = 'free', plan_selected_at = now() where user_id = %s", (uid,))
    db_support.invalidate_auth_user_cache(uid)
    from _insights_adesao_apoio import patch
    assert patch(uid, insight_alert_email_enabled=True).status_code == 409
    assert not insight_aviso_aderiu(uid) and eventos(uid) == []


def test_T35_auditoria_na_mesma_transacao(user_id):
    _assinante(user_id)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update auth_accounts set plan = 'free' where user_id = %s", (user_id,))
        conn.rollback()
    assert insight_aviso_aderiu(user_id) and eventos(user_id)[-1]["event"] == "insight_aviso_adesao"


# ── T40: a trava do `*` ───────────────────────────────────────────────────────

def test_T40_asterisco_trancado_e_ids_liberados(monkeypatch):
    monkeypatch.setattr(insights_notificacao, "AVISO_ABERTO_A_TODOS", False)
    monkeypatch.setenv("INSIGHT_EMAIL_USERS", "*")
    assert insights_notificacao.liberado(7) is False
    monkeypatch.setenv("INSIGHT_EMAIL_USERS", "*,7")
    assert insights_notificacao.liberado(7) is True and insights_notificacao.liberado(8) is False


def test_T40_virar_a_constante_exige_o_laco_e_o_app():
    """Fica vermelho se alguém virar `AVISO_ABERTO_A_TODOS` antes do PR 2 (laço) e do PR 3 (app)."""
    import importlib
    modulo = importlib.reload(insights_notificacao)
    if modulo.AVISO_ABERTO_A_TODOS:
        assert hasattr(modulo, "run_insight_emails_once"), "o laço de envio (PR 2) não existe"
        schema = (RAIZ / "app" / "src" / "api" / "schemas" / "conta.ts").read_text(encoding="utf-8")
        assert "insight_email_off_notice" in schema, "o app nativo (PR 3) não lê a nota de D5"
