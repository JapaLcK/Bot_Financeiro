"""PL-06 B6, PR 2: a tabela de dedupe (§2.3) e a cobertura de `bill_due` por outro canal (§2.2, §17.3).

WhatsApp pela EVIDÊNCIA (`reminder_last_sent_on = hoje`, gravada pelo `_bill_reminder_tick` real com `send_template`
falso); Carteiro pela PREVISÃO (ativo, e-mail ligado, D-3 e D-0). Item coberto é marcado como avisado, sem e-mail.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

import db
from _insights_adesao_apoio import sql
from _insights_envio_apoio import as_, avisos, envio  # noqa: F401 — `envio` é fixture
from adapters.whatsapp import wa_app
from core import ai_patterns
from core.services import insights_detectores as detectores
from core.services import insights_notificacao as notif
from core.services import piggy_agents
from db.agents import activate_agent, pause_agent
from db.bills import create_boleto
from test_insights_contrato import _conta_of, _mes_menos
from utils_date import now_tz

T0 = datetime(2026, 1, 1)


# ── T3. A tabela 2.3, na função pura ────────────────────────────────────────────────────────────────────────

def _it(sev, tipo="budget_warning", valor=None):
    return {"key": "k", "type": tipo, "severity": sev, "explicacao": {"atual": {"valor": valor}}}


def _e(notif_sev=None, disp_sev=None, disp_valor=None):
    return {"k": {"notificado_em": notif_sev and T0, "notificado_severidade": notif_sev,
                  "dispensado_em": disp_sev and T0, "dispensado_severidade": disp_sev,
                  "dispensado_valor": disp_valor}}


OF = "of_bill_increase"


@pytest.mark.parametrize("caso, item, estado, vai", [
    (1, _it("warning"), _e(), True),
    (2, _it("warning"), _e("warning"), False),
    (3, _it("critical"), _e("warning"), True),
    (4, _it("warning"), _e("critical"), False),
    (5, _it("critical"), _e("critical"), False),
    (6, _it("warning", OF, 120), _e("warning"), False),
    (7, _it("warning", "month_projection"), _e("warning"), False),
    (8, _it("warning"), _e(disp_sev="warning"), False),
    (9, _it("critical"), _e(disp_sev="warning"), True),
    (10, _it("critical"), _e("warning", "warning"), True),
    (11, _it("warning", OF, 120), _e(disp_sev="warning", disp_valor=Decimal(100)), True),
    (12, _it("warning", OF, 120), _e("warning", "warning", Decimal(100)), False),
    ("info", _it("info"), _e(), False),
    ("positivo", _it("info", "positive_result"), _e(), False),
    ("sem linha", _it("warning"), {}, False),
])
def test_t3_tabela_do_dedupe(caso, item, estado, vai):
    novos, avisados = notif.a_enviar([item], estado, set())
    assert (novos == [item], avisados) == (vai, [])


def test_t3_coberto_vai_para_avisados_e_chave_repetida_conta_uma_vez():
    a, b = _it("warning"), _it("critical")
    assert notif.a_enviar([b, a], _e(), {"k"}) == ([], [b])
    assert notif.a_enviar([b, a], _e(), set()) == ([b], [])


# ── T25. Valor sem gravidade não repete, pelo tique ───────────────────────────────────────────────────────────

def test_t25_conta_do_of_que_subiu_de_valor_sem_subir_gravidade_nao_repete(envio):
    uid = envio.usuario("pro_max")
    hoje = date.today()
    _conta_of(uid, _mes_menos(hoje, 2), 100)
    _conta_of(uid, _mes_menos(hoje, 1), 130)
    envio.tique()
    (m,) = envio.de(uid)
    assert "Enel Luz subiu 30%" in m["text"]
    _conta_of(uid, _mes_menos(hoje, 1).replace(day=20), 160)  # mesmo fato, mesmo mês, valor maior
    (it,) = [i for i in ai_patterns.generate_ai_insights(uid, force=True) if i["type"] == OF]
    assert it["severity"] == "warning" and "60%" in it["title"]
    envio.tique(dias=1)
    assert len(envio.de(uid)) == 1


# ── T12. Carteiro: cobre D-3 e D-0 só se vai mandar e-mail ────────────────────────────────────────────────────

def _contas(uid, hoje):
    return {d: create_boleto(uid, f"Conta {d:+d}", 50, hoje + timedelta(days=d))["id"] for d in (5, 3, 2, 0, -1)}


def _titulos(m):
    return sorted(l.split(" ", 2)[1] for l in m["text"].splitlines() if l.startswith("Conta "))


@pytest.mark.parametrize("carteiro", ["ativo", "pausado", "sem_email", "agentes_desligados", "freio_da_ui",
                                      "sem_energia", "sem_carteiro"])
def test_t12_carteiro_cobre_d3_e_d0_so_quando_fala(envio, carteiro):
    uid = envio.usuario()
    _contas(uid, now_tz().date())
    if carteiro != "sem_carteiro":
        activate_agent(uid, "carteiro", config={"email_enabled": carteiro != "sem_email"})
    if carteiro == "pausado":
        pause_agent(uid, "carteiro")
    if carteiro == "agentes_desligados":
        envio.mp.setattr(piggy_agents, "AGENTS_ENABLED", False)
    if carteiro == "freio_da_ui":  # AGENTS_UI_ENABLED=0: só os testers do beta; o laço dos agentes não manda
        envio.mp.setenv("AGENTS_UI_ENABLED", "0")
    if carteiro == "sem_energia":  # o tier sem agentes: `agent_kind_allowed` falso, o laço dos agentes não manda
        from core.services import plan_service
        envio.mp.setattr(plan_service, "agents_energy_budget", lambda u: 0)
    envio.tique()
    (m,) = envio.de(uid)
    if carteiro == "ativo":
        assert _titulos(m) == ["+2", "-1"], "D-3 e D-0 o Carteiro manda; a vencida e o D-2 vão por e-mail"
    else:
        assert _titulos(m) == ["+0", "+2", "+3", "-1"], "o D-5 é info e fica no painel"
    assert len(avisos(uid)) == 4, "coberto também fica marcado"


# ── T22. WhatsApp: a evidência do lembrete real ───────────────────────────────────────────────────────────────

@pytest.fixture
def lembrete(monkeypatch):
    """O `_bill_reminder_tick` real às 9h30 de `dia`; a população é só a do teste e o template é falso."""
    import db.bills as bills

    def rodar(uid, dia, falha=False):
        enviados = []

        def send_template(to, *a, **k):
            if falha:
                raise RuntimeError("Meta recusou")
            enviados.append(to)

        monkeypatch.setenv("WA_BILL_REMINDER_TEMPLATE_NAME", "lembrete_conta")
        monkeypatch.setattr(wa_app, "now_tz", lambda: as_(9, 30) + timedelta(days=(dia - now_tz().date()).days))
        monkeypatch.setattr(wa_app, "send_template", send_template)
        monkeypatch.setattr(bills, "list_users_with_pending_bills", lambda: [uid])
        wa_app._bill_reminder_tick()
        return enviados

    return rodar


def _numero(uid):
    sql("insert into user_identities (provider, external_id, user_id) values ('whatsapp', %s, %s)",
        (f"55119{uuid.uuid4().int % 10**8:08d}", uid))


def test_t22_lembrete_aceito_cobre_e_nao_repete_no_dia_seguinte(envio, lembrete, monkeypatch):
    uid = envio.usuario()
    _numero(uid)
    hoje = now_tz().date()
    luz = create_boleto(uid, "Luz", 50, hoje + timedelta(days=3))["id"]
    assert lembrete(uid, hoje)
    envio.tique()
    assert envio.de(uid) == [] and avisos(uid) == {f"bill_due:{luz}:{hoje + timedelta(days=3)}": "warning"}
    monkeypatch.setattr(detectores, "now_tz", lambda: now_tz() + timedelta(days=1))  # D-2: warning, já avisado
    envio.tique(dias=1)
    assert envio.de(uid) == []


@pytest.mark.parametrize("caso", ["template_falhou", "sem_numero", "criada_em_d2"])
def test_t22_sem_evidencia_vai_por_email(envio, lembrete, caso):
    uid = envio.usuario()
    if caso != "sem_numero":
        _numero(uid)
    hoje = now_tz().date()
    create_boleto(uid, "Luz", 50, hoje + timedelta(days=2 if caso == "criada_em_d2" else 3))
    lembrete(uid, hoje, falha=caso == "template_falhou")
    envio.tique()
    assert len(envio.de(uid)) == 1 and "Luz" in envio.de(uid)[0]["text"]


def test_t22_lembrete_com_opt_out_do_whatsapp_ainda_cobre_issue_1434(envio, lembrete):
    """#1434 aberta: o lembrete sai mesmo com "Atualizações do Piggy" desligadas. A B6 não repete por e-mail."""
    uid = envio.usuario()
    _numero(uid)
    db.set_whatsapp_updates_opt_out(uid, True)
    create_boleto(uid, "Luz", 50, now_tz().date() + timedelta(days=3))
    assert lembrete(uid, now_tz().date())
    envio.tique()
    assert envio.de(uid) == []


def test_t22_chave_coberta_e_a_do_detector_nas_duas_formas(envio, lembrete):
    uid = envio.usuario()
    _numero(uid)
    hoje = now_tz().date()
    create_boleto(uid, "Antes", 50, hoje + timedelta(days=3))
    create_boleto(uid, "Vencida", 50, hoje - timedelta(days=1))
    assert len(lembrete(uid, hoje)) == 2
    itens = [i for i in ai_patterns.generate_ai_insights(uid, force=True) if i["type"] == "bill_due"]
    assert any(i["key"].endswith(f":vencida:{hoje - timedelta(days=1)}") for i in itens)
    assert notif.chaves_cobertas(uid, hoje, itens) == {i["key"] for i in itens}


# ── §19. A cobertura é marcada ANTES do e-mail, em todo caminho; a piora posterior ainda avisa ──────────────────

def _cobre(envio, lembrete, uid, canal, hoje):
    if canal == "whatsapp":
        _numero(uid)
        assert lembrete(uid, hoje)
    else:
        activate_agent(uid, "carteiro", config={"email_enabled": True})


@pytest.mark.parametrize("canal", ["whatsapp", "carteiro"])
@pytest.mark.parametrize("pendencia", ["sai_as_11h", "nunca_sai"])
def test_19_coberto_em_dia_de_email_falho_nao_volta_por_email_no_d2(envio, lembrete, monkeypatch, canal, pendencia):
    """Cenário do Tester r1 (achado 1): a Luz em D-3 é coberta pelo outro canal no mesmo dia em que o e-mail das
    outras novidades é recusado. Sem a marca antes do envio, ela voltava por e-mail em D-2."""
    from _insights_envio_apoio import gasto
    uid = envio.usuario()
    hoje = now_tz().date()
    luz = create_boleto(uid, "Luz", 50, hoje + timedelta(days=3))["id"]
    chave_luz = f"bill_due:{luz}:{hoje + timedelta(days=3)}"
    gasto(uid)  # uma novidade, para o e-mail existir e falhar
    _cobre(envio, lembrete, uid, canal, hoje)
    envio.recusar.add(envio.de_email(uid))
    envio.tique()
    assert envio.de(uid) == [] and avisos(uid) == {chave_luz: "warning"}, "coberta marcada mesmo com o Resend recusando"
    if pendencia == "sai_as_11h":
        envio.recusar.clear()
    for h in range(11, 21):
        envio.tique(h, 5)
    envio.recusar.clear()
    monkeypatch.setattr(detectores, "now_tz", lambda: now_tz() + timedelta(days=1))  # D-2: warning, canais calados
    envio.tique(dias=1)
    assert not any("Luz" in m["text"] for m in envio.de(uid)), "já avisada em D-3 pelo outro canal"
    assert avisos(uid)[chave_luz] == "warning"


def test_19_piora_depois_da_cobertura_ainda_avisa(envio, lembrete, monkeypatch):
    """A marca guarda a gravidade e a ocorrência: D-0 (critical, mesma chave) e a vencida (outra chave) voltam."""
    from _insights_envio_apoio import gasto
    uid = envio.usuario()
    hoje = now_tz().date()
    create_boleto(uid, "Luz", 50, hoje + timedelta(days=3))
    gasto(uid)
    _cobre(envio, lembrete, uid, "whatsapp", hoje)
    envio.recusar.add(envio.de_email(uid))
    envio.tique()  # D-3: coberta, e-mail recusado
    envio.recusar.clear()
    for dias, texto in ((3, "vence hoje"), (4, "venceu ontem")):
        monkeypatch.setattr(detectores, "now_tz", lambda d=dias: now_tz() + timedelta(days=d))
        envio.tique(dias=dias)
        assert texto in envio.de(uid)[-1]["text"], texto


def test_19_bloqueado_entre_o_claim_e_o_envio_marca_a_cobertura(envio, lembrete, monkeypatch):
    from _insights_adesao_apoio import ok
    from _insights_envio_apoio import gasto
    uid = envio.usuario()
    hoje = now_tz().date()
    luz = create_boleto(uid, "Luz", 50, hoje + timedelta(days=3))["id"]
    gasto(uid)
    _cobre(envio, lembrete, uid, "whatsapp", hoje)
    real = ai_patterns.generate_ai_insights
    monkeypatch.setattr(ai_patterns, "generate_ai_insights",
                        lambda u, **k: (real(u, **k), ok(uid, insight_alert_email_enabled=False))[0])
    assert envio.tique()["bloqueados"] == 1
    assert envio.de(uid) == [] and avisos(uid) == {f"bill_due:{luz}:{hoje + timedelta(days=3)}": "warning"}


# ── O lembrete do WhatsApp roda antes da rodada das 10h ───────────────────────────────────────────────────────

@pytest.mark.parametrize("env", [None, "8", "9"])
def test_lembrete_do_whatsapp_sai_antes_da_hora_do_email(monkeypatch, env):
    """A evidência do WhatsApp (`reminder_last_sent_on = hoje`) só existe na rodada das 10h se o lembrete roda antes:
    padrão 9h, e produção sem `WA_BILL_REMINDER_HOUR` (F4 do plano). LIMITE declarado: um lembrete que sai depois
    das 10h (hora configurada para 10 ou mais, ou o app fora às 9h) não vira cobertura naquele dia, e a conta pode
    ir pelos dois canais; um reenvio tardio do lembrete fica fora da B6."""
    from core.services.recurring_charger import bill_reminder_hour
    if env is None:
        monkeypatch.delenv("WA_BILL_REMINDER_HOUR", raising=False)
    else:
        monkeypatch.setenv("WA_BILL_REMINDER_HOUR", env)
    assert bill_reminder_hour() < notif.HORA_ENVIO
