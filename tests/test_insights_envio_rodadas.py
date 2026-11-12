"""PL-06 B6, PR 2: rodadas, claim do dia, pendências e falhas do envio do aviso de insight (§2.1, §2.4, §17.2).

A regra que mais pesa aqui (§18): a tentativa pendente só reenvia as chaves guardadas no corte das 10h; novidade
posterior fica para amanhã. Postgres real, Resend falso.
"""
from __future__ import annotations

import threading
from datetime import timedelta, timezone

import pytest

from _insights_adesao_apoio import diga
from _insights_envio_apoio import as_, avisos, envio, gasto, prefs  # noqa: F401 — `envio` é fixture
from core import ai_patterns
from core.services import email_service
from core.services import insights_notificacao as notif
from db import insight_estado
from db.bills import create_boleto, mark_bill_paid
from utils_date import now_tz


# ── T16. As rodadas no fuso do app ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("h, m, esperado", [
    (9, 59, None), (10, 0, "diaria"), (10, 59, "diaria"), (11, 0, "pendentes"), (11, 20, None),
    (20, 14, "pendentes"), (21, 0, None), (3, 0, None),
])
def test_t16_rodada_pela_hora_local(envio, h, m, esperado):
    envio.liberar(1)
    assert (envio.tique(h, m) or {}).get("rodada") == esperado
    # o mesmo instante dado em UTC: a hora que vale é a do fuso do app, não a do relógio do servidor
    assert (notif.run_insight_emails_once(as_(h, m).astimezone(timezone.utc)) or {}).get("rodada") == esperado


def test_t16_dez_horas_envia(envio):
    uid = envio.usuario()
    gasto(uid)
    envio.tique(9, 59)
    assert envio.de(uid) == [] and prefs(uid)["d"] is None, "antes das 10h nem o claim acontece"
    envio.tique(10, 0)
    assert len(envio.de(uid)) == 1


# ── T10. Envio falho: nada marcado, chaves guardadas, próxima rodada; um usuário não segura a fila ─────────────

@pytest.mark.parametrize("ordem", ["falho_primeiro", "falho_depois"])
def test_t10_resend_recusa_um_e_o_outro_recebe(envio, ordem):
    a, b = envio.usuario(), envio.usuario()
    falho, ok = (min(a, b), max(a, b)) if ordem == "falho_primeiro" else (max(a, b), min(a, b))
    cat = gasto(falho)
    gasto(ok)
    envio.recusar.add(envio.de_email(falho))
    envio.tique()
    assert envio.de(falho) == [] and len(envio.de(ok)) == 1
    assert avisos(falho) == {} and prefs(falho)["p"] == [f"budget_warning:{cat}:{now_tz():%Y-%m}"]
    assert prefs(ok)["p"] is None
    envio.tique(11, 5)  # ainda recusado: guarda de novo
    assert prefs(falho)["p"] and avisos(falho) == {}
    envio.recusar.clear()
    envio.tique(12, 5)
    assert len(envio.de(falho)) == 1 and prefs(falho)["p"] is None and avisos(falho)
    envio.tique(13, 5)
    envio.tique(dias=1)
    assert len(envio.de(falho)) == 1 and len(envio.de(ok)) == 1


def test_t10_excecao_no_envio_guarda_a_pendencia(envio):
    uid = envio.usuario()
    gasto(uid)

    def explode(*a, **k):
        raise RuntimeError("boom")

    envio.mp.setattr(email_service, "send_agent_report_email", explode)
    assert envio.tique()["falhas"] == 1
    assert prefs(uid)["p"] and avisos(uid) == {}


def test_t10_pendencia_que_nao_sai_ate_as_21h_morre_no_claim_de_amanha(envio):
    uid = envio.usuario()
    gasto(uid)
    envio.recusar.add(envio.de_email(uid))
    for h in (10, *range(11, 21)):
        envio.tique(h, 5)
    envio.tique(21, 5)
    assert envio.de(uid) == [] and prefs(uid)["p"]
    envio.recusar.clear()
    envio.tique(dias=1)
    assert len(envio.de(uid)) == 1 and prefs(uid)["p"] is None


# ── T23. Rodada tardia: novidade depois das 10h fica para amanhã ─────────────────────────────────────────────

def test_t23_processado_as_10h_nao_volta_a_tarde_nem_gera_insights(envio):
    uid = envio.usuario()
    gasto(uid)
    envio.tique()
    gerou = []
    real = ai_patterns.generate_ai_insights
    envio.mp.setattr(ai_patterns, "generate_ai_insights", lambda u, **k: gerou.append(u) or real(u, **k))
    create_boleto(uid, "Internet", 120, now_tz().date() + timedelta(days=1))  # fato novo às 14h
    envio.tique(15, 5)
    assert len(envio.de(uid)) == 1 and uid not in gerou
    envio.tique(dias=1)
    assert len(envio.de(uid)) == 2 and "Internet" in envio.de(uid)[1]["text"]


def test_t23_pendente_reenvia_so_a_chave_guardada(envio):
    uid = envio.usuario()
    cat = gasto(uid)
    envio.recusar.add(envio.de_email(uid))
    envio.tique()
    envio.recusar.clear()
    create_boleto(uid, "Internet", 120, now_tz().date() + timedelta(days=1))  # fato novo depois do corte
    diga(uid, f"gastei 30 no {cat}")  # e a chave guardada piorou: vai com o texto de agora (2.3, caso 22)
    envio.tique(11, 5)
    (m,) = envio.de(uid)
    assert "Internet" not in m["text"] and "estourou" in m["text"]
    envio.tique(dias=1)
    assert "Internet" in envio.de(uid)[1]["text"] and "estourou" not in envio.de(uid)[1]["text"]


def test_t23_pendente_pago_antes_da_tentativa_some(envio):
    uid = envio.usuario()
    luz = create_boleto(uid, "Luz", 180, now_tz().date() + timedelta(days=1))["id"]
    envio.recusar.add(envio.de_email(uid))
    envio.tique()
    envio.recusar.clear()
    mark_bill_paid(uid, luz, metodo="banco")
    assert envio.tique(11, 5)["enviados"] == 0
    assert envio.de(uid) == [] and prefs(uid)["p"] is None


def test_t23_aderiu_as_14h_comeca_amanha(envio):
    uid = envio.usuario(aderir_=False)
    gasto(uid)
    envio.tique()
    from _insights_adesao_apoio import aderir
    aderir(uid)
    envio.tique(14, 5)
    assert envio.de(uid) == [] and prefs(uid)["d"] is None
    envio.tique(dias=1)
    assert len(envio.de(uid)) == 1


# ── T4. Réplicas concorrentes: um e-mail, um stamp ───────────────────────────────────────────────────────────

def _duas_replicas(envio, nome, h):
    """Duas voltas em paralelo. Uma barreira segura as duas depois de enumerar, antes do claim (ou da pendência); a
    outra, antes de ler o estado de envio, para que nenhuma veja o stamp da outra (sem ela, a trava de geração por
    processo serializa as duas e o dedupe por gravidade esconderia um claim furado). Com o claim certo só uma chega
    à segunda barreira, que desiste em 3 s e segue."""
    barreira, real = threading.Barrier(2, timeout=20), getattr(insight_estado, nome)
    envio.mp.setattr(insight_estado, nome, lambda dia: (real(dia), barreira.wait())[0])
    estado, real_estado = threading.Barrier(2), insight_estado.estado_de_envio

    def junta(uid, chaves):
        try:
            estado.wait(timeout=3)
        except threading.BrokenBarrierError:
            pass
        return real_estado(uid, chaves)

    envio.mp.setattr(insight_estado, "estado_de_envio", junta)
    erros = []
    voltas = [threading.Thread(target=lambda: erros.append(envio.tique(h, 5)["falhas"])) for _ in range(2)]
    for t in voltas:
        t.start()
    for t in voltas:
        t.join(30)
    envio.mp.setattr(insight_estado, nome, real)
    envio.mp.setattr(insight_estado, "estado_de_envio", real_estado)
    return erros


def test_t4_duas_replicas_no_claim_mandam_um_email(envio):
    uid = envio.usuario()
    gasto(uid)
    assert _duas_replicas(envio, "list_users_for_insight_email", 10) == [0, 0]
    assert len(envio.de(uid)) == 1 and len(avisos(uid)) == 1


def test_t4_duas_replicas_na_pendencia_mandam_um_email(envio):
    uid = envio.usuario()
    gasto(uid)
    envio.recusar.add(envio.de_email(uid))
    envio.tique()
    envio.recusar.clear()
    assert _duas_replicas(envio, "list_users_insight_email_pendente", 11) == [0, 0]
    assert len(envio.de(uid)) == 1


# ── T11 e T5: falha depois do envio repete amanhã; falha antes de montar devolve o dia ─────────────────────────

def test_t11_marcar_falha_depois_do_envio_repete_amanha_e_nao_hoje(envio):
    uid = envio.usuario()
    gasto(uid)
    real = insight_estado.marcar_notificados

    def explode(*a):
        raise RuntimeError("banco fora")

    envio.mp.setattr(insight_estado, "marcar_notificados", explode)
    envio.tique()
    envio.mp.setattr(insight_estado, "marcar_notificados", real)
    envio.tique(10, 50)
    envio.tique(11, 5)
    assert len(envio.de(uid)) == 1, "o dia foi consumido"
    envio.tique(dias=1)
    assert len(envio.de(uid)) == 2, "um repetido amanhã, aceito (2.4): perder o aviso seria pior"


def test_t5_estado_que_levanta_devolve_o_dia_e_a_hora_das_10_tenta_de_novo(envio):
    uid = envio.usuario()
    gasto(uid)
    real = insight_estado.estado_de_envio

    def explode(*a):
        raise RuntimeError("banco fora")

    envio.mp.setattr(insight_estado, "estado_de_envio", explode)
    assert envio.tique()["falhas"] == 1
    assert envio.de(uid) == [] and prefs(uid)["d"] is None
    envio.mp.setattr(insight_estado, "estado_de_envio", real)
    envio.tique(10, 20)
    assert len(envio.de(uid)) == 1


def test_t10_falha_ao_montar_na_rodada_de_pendentes_guarda_de_novo(envio):
    uid = envio.usuario()
    cat = gasto(uid)
    envio.recusar.add(envio.de_email(uid))
    envio.tique()
    envio.recusar.clear()
    real = insight_estado.estado_de_envio

    def explode(*a):
        raise RuntimeError("banco fora")

    envio.mp.setattr(insight_estado, "estado_de_envio", explode)
    assert envio.tique(11, 5)["falhas"] == 1
    assert prefs(uid)["p"] == [f"budget_warning:{cat}:{now_tz():%Y-%m}"], "a pendência volta para a linha"
    envio.mp.setattr(insight_estado, "estado_de_envio", real)
    envio.tique(12, 5)
    assert len(envio.de(uid)) == 1


def test_t4_pegar_pendente_com_a_linha_travada_so_um_pega(envio):
    """Determinístico: uma 3ª transação trava a linha; as duas réplicas param nela e só então ela solta. Sem o
    `for update` da CTE as duas leem as chaves do mesmo retrato e as duas as devolvem."""
    import db as _db
    uid = envio.usuario()
    dia = now_tz().date()
    assert insight_estado.reivindicar_dia(uid, dia)
    insight_estado.guardar_pendente(uid, dia, ["k1", "k2"])
    out: list[list[str]] = []
    with _db.get_conn() as trava:
        trava.execute("select 1 from daily_report_prefs where user_id = %s for update", (uid,))
        voltas = [threading.Thread(target=lambda: out.append(insight_estado.pegar_pendente(uid, dia))) for _ in range(2)]
        for t in voltas:
            t.start()
        for _ in range(200):  # até as duas estarem esperando por lock (no UPDATE ou no FOR UPDATE)
            with _db.get_conn() as c:
                n = c.execute("select count(*) as n from pg_stat_activity where wait_event_type = 'Lock'"
                              " and datname = current_database() and query like %s",
                              ("%insight_email_pendente%",)).fetchone()["n"]
            if n >= 2:
                break
            threading.Event().wait(0.05)
        trava.rollback()
    for t in voltas:
        t.join(20)
    assert sorted(map(len, out)) == [0, 2]
