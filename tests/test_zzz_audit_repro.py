"""Repro de auditoria (temporário)."""
from datetime import datetime, timedelta, timezone


def _row(agent_id):
    from db import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id, suppressed_at, stale_at, emailed_at, payload, valor_impacto,"
                " fired_at from agent_events where agent_id=%s",
                (agent_id,),
            )
            return cur.fetchone()


def test_repro_suppressed_at_sobrevive_payload_identico(user_id):
    import db
    from db import get_conn
    import core.services.piggy_agents as pa

    ag = db.activate_agent(user_id, "barao")
    agent_id = ag["id"]
    key = "parado:2026-08"
    payload = {"tipo": "parado", "idle": 21500.0, "cdi": 10.5, "rende_mes": 188.13,
               "titulo": "x", "mensagem": "y"}

    # 1) tick 1: detector grava o retrato
    assert db.record_or_refresh_agent_event(
        agent_id, user_id, "barao", key, payload, valor_impacto=188.13) is True

    # 2) mesmo tick: opt-out do agente → suppress
    db.set_agent_email_enabled(user_id, "barao", False)
    pa.run_agent_emails_once()
    r = _row(agent_id)
    print("apos opt-out:", r["suppressed_at"], r["emailed_at"])
    assert r["suppressed_at"] is not None, "opt-out nao suprimiu"
    assert r["emailed_at"] is None

    # 3) usuario religa o e-mail 30min depois
    assert db.set_agent_email_enabled(user_id, "barao", True) is True

    # 4) ticks seguintes: dinheiro parado → payload IDENTICO
    for _ in range(3):
        changed = db.record_or_refresh_agent_event(
            agent_id, user_id, "barao", key, payload, valor_impacto=188.13)
        assert changed is False
    r = _row(agent_id)
    print("apos religar + reescritas identicas: suppressed_at =", r["suppressed_at"])

    # 5) consequencia: fora das duas filas
    print("list_unemailed_events:", db.list_unemailed_events(agent_id))
    print("pending:", [a for a in db.list_agents_pending_email() if a["agent_id"] == agent_id])

    # envelhece a linha pra vencer a carencia de 45min e roda o runner
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update agent_events set fired_at = now() - interval '3 hours'"
                        " where agent_id=%s", (agent_id,))
        conn.commit()
    out = pa.run_agent_emails_once()
    print("run_agent_emails_once:", out)

    assert r["suppressed_at"] is None, "BUG CONFIRMADO: suppressed_at sobreviveu"


def test_controle_payload_muda_limpa(user_id):
    import db
    from db import get_conn
    ag = db.activate_agent(user_id, "barao")
    agent_id = ag["id"]
    key = "parado:2026-08"
    db.record_or_refresh_agent_event(agent_id, user_id, "barao", key, {"v": 1}, valor_impacto=1.0)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update agent_events set suppressed_at=now() where agent_id=%s", (agent_id,))
        conn.commit()
    db.record_or_refresh_agent_event(agent_id, user_id, "barao", key, {"v": 2}, valor_impacto=2.0)
    assert _row(agent_id)["suppressed_at"] is None, "payload novo devia limpar"
    print("controle OK: payload novo limpa suppressed_at")
