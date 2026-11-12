"""PL-06 B6, PR 2: o envio diário do aviso de insight por e-mail. A conversa, o conteúdo e o log.

Postgres real; gastos pelo `handle_incoming`, adesão pelo `PATCH /settings`, dispensa pelo `PUT /insights/{uid}/estado`.
Só o Resend é falso (`_insights_envio_apoio`). Rodadas, pendências e falhas: `test_insights_envio_rodadas.py`;
portão e isolamento: `test_insights_envio_portao.py`; cobertura de `bill_due` e a tabela 2.3: `test_insights_envio_cobertura.py`.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import timedelta

import pytest

import db
from _insights_adesao_apoio import diga, sql
from _insights_envio_apoio import avisos, envio, gasto, prefs  # noqa: F401 — `envio` é fixture
from core import ai_patterns
from core.services import insights_notificacao as notif
from db import insight_estado
from db.bills import create_boleto, mark_bill_paid
from test_insights_estado import _put
from utils_date import now_tz


def _itens(params):
    """(título, mensagem) de cada item do corpo html, na ordem."""
    return re.findall(r"<p><strong>(.*?)</strong><br/>(.*?)</p>", params["html"])


# ── T1. A conversa: 1 e-mail, nada mais no dia, nada no dia seguinte sem novidade ──────────────────────────

def test_t1_gasto_na_conversa_vira_um_email_e_nao_repete(envio):
    uid = envio.usuario()
    cat = gasto(uid)
    assert envio.tique()["enviados"] == 1
    (m,) = envio.de(uid)
    (feed,) = ai_patterns.generate_ai_insights(uid, force=True)
    assert m["subject"] == notif.ASSUNTO and _itens(m) == [(feed["title"], feed["message"])]
    assert avisos(uid) == {f"budget_warning:{cat}:{now_tz():%Y-%m}": "warning"}
    envio.tique(10, 50)
    envio.tique(dias=1)
    assert len(envio.de(uid)) == 1, "a mesma chave na mesma gravidade não volta (condição 4)"


# ── T2. Subiu a gravidade: vai de novo, e grava a nova ───────────────────────────────────────────────────────

def test_t2_estourou_o_orcamento_avisa_de_novo_no_dia_seguinte(envio):
    uid = envio.usuario()
    cat = gasto(uid)
    envio.tique()
    diga(uid, f"gastei 5 no {cat}")  # 95 %: segue warning, valor não conta
    envio.tique(dias=1)
    assert len(envio.de(uid)) == 1
    diga(uid, f"gastei 30 no {cat}")  # 125 %: critical
    envio.tique(dias=2)
    assert len(envio.de(uid)) == 2 and "estourou" in _itens(envio.de(uid)[1])[0][0]
    assert set(avisos(uid).values()) == {"critical"}


# ── T5. Dispensado não vai; dispensado que piorou volta (Q7b (i)) ───────────────────────────────────────────

def test_t5_dispensado_nao_vai_e_volta_quando_piora(envio):
    uid, vizinho = envio.usuario(), envio.usuario()
    cat = gasto(uid)
    gasto(vizinho)
    (it,) = ai_patterns.generate_ai_insights(uid, force=True)
    assert _put(uid, chave=it["key"], dispensado=True, severidade="warning").status_code == 200
    envio.tique()
    assert envio.de(uid) == [] and len(envio.de(vizinho)) == 1, "o dispensado não vai; o vizinho recebe"
    diga(uid, f"gastei 30 no {cat}")
    envio.tique(dias=1)
    assert len(envio.de(uid)) == 1 and avisos(uid) == {it["key"]: "critical"}


def test_t5_dispensa_vale_mesmo_quando_a_leitura_do_painel_falha_aberta(envio):
    """`dispensadas` (a leitura do feed) é fail-open: devolve {} no erro e o feed traz o dispensado. O e-mail não."""
    from db import insight_estado as est
    uid = envio.usuario()
    gasto(uid)
    (it,) = ai_patterns.generate_ai_insights(uid, force=True)
    assert _put(uid, chave=it["key"], dispensado=True, severidade="warning").status_code == 200
    envio.mp.setattr(est, "dispensadas", lambda *a: {})
    assert [i["key"] for i in ai_patterns.generate_ai_insights(uid, force=True)] == [it["key"]], "o feed falha aberto"
    envio.tique()
    assert envio.de(uid) == [] and avisos(uid) == {}


# ── T9. O envio lê dado fresco, não o cache ─────────────────────────────────────────────────────────────────

def test_t9_conta_paga_depois_do_cache_nao_e_citada(envio):
    uid = envio.usuario()
    gasto(uid)
    luz = create_boleto(uid, "Luz", 180, now_tz().date() + timedelta(days=2))["id"]
    assert any(i["title"].startswith("Luz") for i in ai_patterns.generate_ai_insights(uid)), "a luz está no cache"
    mark_bill_paid(uid, luz, metodo="banco")  # o ouvinte do `pb_escrita` não roda aqui: o cache segue velho
    envio.tique()
    (m,) = envio.de(uid)
    assert [t for t, _ in _itens(m)] == ["Mercado tá apertando"], "a conta paga não sai; o orçamento sai"


# ── T13. Plano: Essencial não adere; o Pro não leva `impacto`/`acoes` no corpo ─────────────────────────────────

def test_t13_pro_recebe_so_titulo_e_mensagem(envio):
    uid = envio.usuario("pro_max")
    gasto(uid)
    (feed,) = ai_patterns.generate_ai_insights(uid, force=True)
    assert feed["acoes"] and feed["impacto"], "o Pro tem as duas no feed"
    envio.tique()
    (m,) = envio.de(uid)
    assert _itens(m) == [(feed["title"], feed["message"])]
    assert "Ajustar orçamento" not in m["html"] + m["text"]


def test_t13_essencial_nao_adere_e_nao_recebe(envio):
    uid = envio.usuario("essencial", aderir_=False)
    from _insights_adesao_apoio import patch
    assert patch(uid, insight_alert_email_enabled=True).status_code == 403
    gasto(uid)
    envio.tique()
    assert envio.de(uid) == []


# ── T14. Assunto fixo no log, nada do usuário em `system_event_logs`, WARNING com dono ───────────────────────

def test_t14_assunto_fixo_e_log_sem_dado_do_usuario(envio, caplog):
    from core.admin_dashboard import ensure_admin_tables
    asyncio.run(ensure_admin_tables())
    uid = envio.usuario()
    create_boleto(uid, "Clínica Dra Ana Paula", 432.17, now_tz().date() + timedelta(days=1))
    antes = sql("select coalesce(max(id), 0) m from system_event_logs")[0]["m"]
    envio.tique()
    (m,) = envio.de(uid)
    assert "Clínica Dra Ana Paula" in m["html"] and m["subject"] == notif.ASSUNTO
    linhas = sql("select event_type, message, details::text d from system_event_logs where id > %s", (antes,))
    (enviado,) = [r for r in linhas if r["event_type"] == "email_sent" and notif.ASSUNTO in r["d"]]
    texto = " ".join(f"{r['message']} {r['d']}" for r in linhas)
    for segredo in ("Clínica", "Ana Paula", "432", "bill_due:"):
        assert segredo not in texto, segredo
    assert any(r["event_type"] == "insight_email_run" for r in linhas)

    def explode(user_id, chaves):
        raise RuntimeError(f"DETAIL: Key (chave)=({chaves[0]}) já existe")

    vizinho = envio.usuario()
    gasto(vizinho)
    envio.mp.setattr(insight_estado, "estado_de_envio", explode)
    with caplog.at_level(logging.WARNING, logger=notif.logger.name):
        envio.tique(dias=1)
    (rec,) = [r for r in caplog.records if r.name == notif.logger.name and r.user_id == vizinho]
    assert "motivo=RuntimeError" in rec.getMessage()
    assert "budget_warning" not in rec.getMessage() and "DETAIL" not in rec.getMessage() and not rec.exc_info


# ── T15. Escape no html, texto legível, link e alcance do cancelamento ──────────────────────────────────────

def test_t15_nome_com_marcacao_sai_escapado_e_o_rodape_diz_o_alcance(envio):
    uid = envio.usuario()
    create_boleto(uid, 'Luz "Casa" & Cia', 90, now_tz().date() + timedelta(days=1))
    envio.tique()
    (m,) = envio.de(uid)
    assert "Luz &quot;Casa&quot; &amp; Cia" in m["html"] and '"Casa" & Cia' not in m["html"]
    assert 'Luz "Casa" & Cia' in m["text"]
    assert "/settings?view=notifications" in m["html"] and "/settings?view=notifications" in m["text"]
    assert notif.ALCANCE_CANCELAR in m["text"] and notif.ALCANCE_CANCELAR in notif.texto_adesao()
    assert html.escape(notif.ALCANCE_CANCELAR) in m["html"]
    assert f">{notif.RODAPE_CANCELAR}</a>" in m["html"] and "Não quer mais estes avisos?" not in m["html"]
    assert m["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_t15_categoria_com_marcacao_na_mensagem_sai_escapada(envio):
    uid = envio.usuario()
    db.upsert_budget(uid, 'pão & "cia"', 100)
    db.add_launch_and_update_balance(uid, "despesa", 90, "x", None, categoria='pão & "cia"')
    envio.tique()
    (m,) = envio.de(uid)
    (titulo, mensagem), = _itens(m)
    assert "pão &amp; &quot;cia&quot;" in mensagem and '& "cia"' not in m["html"]
    assert 'orçamento de pão & "cia"' in m["text"]


@pytest.mark.parametrize("kind", ["carteiro", ""])
def test_t15_email_dos_agentes_segue_com_o_rodape_de_sempre(kind):
    from core.services.email_service import _piggy_html
    assert "Não quer mais estes avisos?" in _piggy_html("t", "c", "https://x/unsub", agent_kind=kind)


# ── T18. Ciclo de vida: a exportação traz o dia, a pendência e o aviso; o reset apaga ───────────────────────────

def test_t18_exportacao_traz_e_reset_apaga_o_estado_do_envio(envio):
    import io
    import json
    import zipfile

    import db_support
    from _insights_adesao_apoio import cliente
    from db.privacy import build_user_export_zip
    from db.users import _hash_password
    uid = envio.usuario()
    gasto(uid)
    envio.recusar.add(envio.de_email(uid))
    envio.tique()
    envio.recusar.clear()
    create_boleto(uid, "Luz", 50, now_tz().date() + timedelta(days=1))
    envio.tique(dias=1)
    dados = json.loads(zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid))).read("dados.json"))["dados"]
    (p,) = dados["preferencias_resumo_diario"]
    assert p["last_insight_email_date"] and "insight_email_pendente" in p
    assert any(e["notificado_severidade"] for e in dados["insights_estado"])
    sql("update auth_accounts set password_hash = %s where user_id = %s", (_hash_password("Senha-B6-forte!9"), uid))
    db_support.invalidate_auth_user_cache(uid)
    assert cliente(uid).post("/settings/reset", json={"password": "Senha-B6-forte!9"}).status_code == 200
    assert prefs(uid) == {"d": None, "p": None} and avisos(uid) == {}
