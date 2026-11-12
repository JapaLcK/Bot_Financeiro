"""PL-06 B6, PR 2: o portão do envio do aviso de insight (§17.1) e o isolamento entre usuários (§2.7).

`liberado` → `filtrar_por_acesso` → `insight_email_permitido` antes do claim, e de novo antes de enviar. O enumerador
usa a MESMA constante SQL do portão (`db.reports.PERMITIDO_SQL`, T41). Postgres real, rotas reais, Resend falso.
"""
from __future__ import annotations

import pytest

import db
import db_support
from _insights_adesao_apoio import diga, ok, patch, sql, unsub
from _insights_envio_apoio import avisos, envio, gasto, prefs  # noqa: F401 — `envio` é fixture
from conftest import promote_to_pro
from core import ai_patterns
from core.services import insights_notificacao as notif
from db import insight_estado
from db.reports import insight_email_permitido


# ── T20, T21. Só quem aderiu; interruptor antigo e adesão morta não bastam ────────────────────────────────────

def test_t20_adesao_explicita_liga_e_desliga_o_envio(envio):
    aderiu, nao = envio.usuario(), envio.usuario(aderir_=False)
    gasto(aderiu)
    gasto(nao)
    envio.tique()
    assert len(envio.de(aderiu)) == 1 and envio.de(nao) == []
    assert prefs(nao)["d"] is None, "sem adesão nem o claim acontece"
    ok(nao, insight_alert_email_enabled=True)
    ok(aderiu, insight_alert_email_enabled=False)
    envio.tique(dias=1)
    assert len(envio.de(nao)) == 1 and len(envio.de(aderiu)) == 1


def test_t21_interruptor_antigo_ligado_sem_adesao_nao_recebe(envio):
    uid = envio.usuario("pro_max", aderir_=False)
    assert notif.liberado(uid) and ok(uid, insight_email_enabled=True)["insight_email_enabled"] is True
    gasto(uid)
    envio.tique()
    assert envio.de(uid) == [] and uid not in insight_estado.list_users_for_insight_email(None)


def test_t21_adesao_morta_sem_escrita_nao_recebe(envio):
    uid, vizinho = envio.usuario(), envio.usuario()
    gasto(uid)
    gasto(vizinho)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local session_replication_role = replica")  # o tempo passou, ninguém escreveu
        cur.execute("update auth_accounts set plan_expires_at = now() - interval '3 hours',"
                    " insight_aviso_coberto_ate = now() - interval '3 hours' where user_id = %s", (uid,))
        conn.commit()
    db_support.invalidate_auth_user_cache(uid)
    assert sql("select insight_aviso_aderiu_em from auth_accounts where user_id = %s", (uid,))[0]["insight_aviso_aderiu_em"]
    envio.tique()
    assert envio.de(uid) == [] and uid not in insight_estado.list_users_for_insight_email(None)
    assert len(envio.de(vizinho)) == 1


# ── T6. Downgrade não consome o dia; quem volta pelo plano recebe na próxima rodada diária ──────────────────────

def test_t6_downgrade_nao_consome_o_dia_e_a_volta_espera_amanha(envio):
    uid, plus = envio.usuario(), envio.usuario()
    gasto(uid)
    gasto(plus)
    promote_to_pro(uid, "essencial")  # escrita crua: perda natural, a adesão fica pela margem
    assert notif.liberado(uid) and not insight_email_permitido(uid)
    envio.tique()
    assert envio.de(uid) == [] and prefs(uid)["d"] is None and len(envio.de(plus)) == 1
    promote_to_pro(uid, "pro")  # volta dentro da margem: a adesão segue
    envio.tique(14, 5)
    assert envio.de(uid) == []
    envio.tique(dias=1)
    assert len(envio.de(uid)) == 1


# ── T7. Bloqueios pelos caminhos reais, entre a lista e o portão, e entre o portão e o envio ────────────────────

def _bloqueios(envio, uid, como):
    if como == "interruptor":
        ok(uid, insight_alert_email_enabled=False)
    elif como == "insights_financeiros":
        ok(uid, insight_email_enabled=False)
    elif como == "parar_emails":
        diga(uid, "parar emails")
    elif como == "unsubscribe":
        unsub(uid)
    elif como == "exclusao":
        sql("update auth_accounts set deletion_status = 'scheduled' where user_id = %s", (uid,))
    elif como == "plano":
        promote_to_pro(uid, "essencial")
    elif como == "env":  # tirado de `INSIGHT_EMAIL_USERS` no meio da volta: o portão do banco segue aberto
        envio.liberados.remove(uid)
        envio.liberar()
        return
    assert not insight_email_permitido(uid), como


@pytest.mark.parametrize("como", ["interruptor", "insights_financeiros", "parar_emails", "unsubscribe", "exclusao", "plano",
                                  "env"])
@pytest.mark.parametrize("quando", ["depois_da_lista", "depois_do_portao"])
def test_t7_bloqueio_no_meio_da_volta_nao_envia(envio, como, quando):
    uid, vizinho = envio.usuario(), envio.usuario()
    gasto(uid)
    gasto(vizinho)
    if quando == "depois_da_lista":
        real = insight_estado.list_users_for_insight_email
        envio.mp.setattr(insight_estado, "list_users_for_insight_email",
                         lambda dia: (real(dia), _bloqueios(envio, uid, como))[0])
    else:  # já passou pelo portão e pelo claim: a geração roda e o bloqueio chega antes do envio
        real = ai_patterns.generate_ai_insights
        envio.mp.setattr(ai_patterns, "generate_ai_insights",
                         lambda u, **k: (real(u, **k), u == uid and _bloqueios(envio, uid, como))[0])
    envio.tique()
    assert envio.de(uid) == [] and avisos(uid) == {}
    assert len(envio.de(vizinho)) == 1, "o vizinho recebe"
    assert (prefs(uid)["d"] is None) is (quando == "depois_da_lista"), "o dia só é consumido depois do claim"


def test_t7_cortado_pelo_acesso_nao_recebe_e_nao_consome_o_dia(envio):
    """`filtrar_por_acesso` (o corte do app, `has_app_access`) antes do claim, além do portão do aviso."""
    from core.reports import reports_daily
    uid, vizinho = envio.usuario(), envio.usuario()
    gasto(uid)
    gasto(vizinho)
    real = reports_daily.filtrar_por_acesso
    envio.mp.setattr(reports_daily, "filtrar_por_acesso", lambda ids: [u for u in real(ids) if u != uid])
    envio.tique()
    assert envio.de(uid) == [] and prefs(uid)["d"] is None and len(envio.de(vizinho)) == 1


# ── T24. Conta em exclusão ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["scheduled", "processing"])
def test_t24_conta_em_exclusao_fora_e_cancelar_devolve(envio, status):
    uid = envio.usuario()
    gasto(uid)
    sql("update auth_accounts set deletion_status = %s where user_id = %s", (status, uid))
    assert uid not in insight_estado.list_users_for_insight_email(None)
    envio.tique()
    assert envio.de(uid) == []
    sql("update auth_accounts set deletion_status = null where user_id = %s", (uid,))
    envio.tique(10, 20)
    assert len(envio.de(uid)) == 1


# ── T8. Isolamento: a MESMA chave em dois usuários ──────────────────────────────────────────────────────────

def test_t8_mesma_chave_em_dois_usuarios_nao_se_mistura(envio):
    a, b = envio.usuario(), envio.usuario()
    cat = gasto(a, "gastei 91 no mercado")
    assert gasto(b, "gastei 97 no mercado") == cat
    chave = ai_patterns.generate_ai_insights(a, force=True)[0]["key"]
    assert [i["key"] for i in ai_patterns.generate_ai_insights(b, force=True)] == [chave], "B abriu o painel: tem a linha"
    envio.liberados.remove(b)
    envio.liberar()
    envio.tique()
    assert avisos(a) == {chave: "warning"} and avisos(b) == {}, "o stamp de A não toca B"
    assert prefs(b)["d"] is None
    envio.recusar.add(envio.de_email(b))
    envio.liberar(b)
    envio.tique(10, 20)
    assert prefs(b)["p"] == [chave] and prefs(a)["p"] is None, "a pendência de B é só de B"
    envio.recusar.clear()
    envio.tique(11, 5)
    (ma,), (mb,) = envio.de(a), envio.de(b)
    assert "91%" in ma["text"] and "97%" not in ma["text"]
    assert "97%" in mb["text"] and "91%" not in mb["text"]


# ── T41. Um dialeto: o enumerador e o portão concordam ───────────────────────────────────────────────────────

def test_t41_enumerador_e_portao_vem_da_mesma_constante_e_concordam(envio):
    import inspect

    from db import reports
    assert "PERMITIDO_SQL" in inspect.getsource(reports.insight_email_permitido)
    for fn in (insight_estado.list_users_for_insight_email, insight_estado.list_users_insight_email_pendente):
        assert "PERMITIDO_SQL" in inspect.getsource(fn)
    contas = {nome: envio.usuario() for nome in ("viva", "morta", "engajamento", "insight", "exclusao", "carencia",
                                                   "sem_adesao")}
    u = contas
    sql("update auth_accounts set engagement_opt_out = true where user_id = %s", (u["engajamento"],))
    sql("update auth_accounts set insight_email_opt_out = true where user_id = %s", (u["insight"],))
    sql("update auth_accounts set deletion_status = 'scheduled' where user_id = %s", (u["exclusao"],))
    ok(u["sem_adesao"], insight_alert_email_enabled=False)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("set local session_replication_role = replica")
        cur.execute("update auth_accounts set insight_aviso_aderiu_em = now() where user_id = %s", (u["insight"],))
        cur.execute("update auth_accounts set insight_aviso_aderiu_em = now() where user_id = %s", (u["engajamento"],))
        cur.execute("update auth_accounts set plan_expires_at = now() - interval '3 hours',"
                    " insight_aviso_coberto_ate = now() - interval '3 hours' where user_id = %s", (u["morta"],))
        cur.execute("update auth_accounts set last_payment_status = 'past_due', past_due_since = now(),"
                    " plan = 'free', plan_expires_at = null where user_id = %s", (u["carencia"],))
        conn.commit()
    for uid in u.values():
        db_support.invalidate_auth_user_cache(uid)
    lista = set(insight_estado.list_users_for_insight_email(None))
    no_sql = {n for n, uid in u.items() if uid in lista}
    assert no_sql == {"viva", "carencia"}, "carência passa no SQL; o `plan_gate_ok` em Python a barra"
    assert {n for n, uid in u.items() if insight_email_permitido(uid)} == {"viva"}


# ── T42. `liberado` no laço ─────────────────────────────────────────────────────────────────────────────────

def test_t42_env_vazia_nao_consulta_o_banco_e_a_trava_do_asterisco_vale(envio):
    uid = envio.usuario()
    gasto(uid)

    def proibido(*a):
        raise AssertionError("a env vazia não pode consultar o banco")

    real = insight_estado.list_users_for_insight_email
    envio.mp.setenv("INSIGHT_EMAIL_USERS", "")
    envio.mp.setattr(insight_estado, "list_users_for_insight_email", proibido)
    assert envio.tique() is None
    envio.mp.setattr(insight_estado, "list_users_for_insight_email", real)
    envio.mp.setenv("INSIGHT_EMAIL_USERS", "*")
    assert notif.AVISO_ABERTO_A_TODOS is False
    envio.tique()
    assert envio.de(uid) == [] and prefs(uid)["d"] is None, "`*` com a trava fechada não envia a ninguém"
    envio.mp.setenv("INSIGHT_EMAIL_USERS", f"*,{uid}")
    envio.tique()
    assert len(envio.de(uid)) == 1
