"""O histórico de cada especialista aplica o tema antes do limite do feed."""
from datetime import datetime, timedelta, timezone

import pytest
from psycopg.types.json import Jsonb

import db
from core.services import agent_chat as chat


@pytest.mark.parametrize("kind", ["xerife", "carteiro", "reporter", "cofre"])
def test_snapshot_encontra_20_eventos_proprios_apos_feed_global_cheio(user_id, kind):
    own_agent = db.activate_agent(user_id, kind)["id"]
    other_agent = db.activate_agent(user_id, "detetive")["id"]
    other_user = user_id + 1
    db.ensure_user(other_user)
    foreign_agent = db.activate_agent(other_user, kind)["id"]
    base = datetime(2026, 9, 13, tzinfo=timezone.utc)

    rows = [
        (own_agent, user_id, kind, f"own-{i}", base - timedelta(hours=1) + timedelta(seconds=i),
         Jsonb({"source": "own", "index": i}), None,
         base if i == 24 else None, base if i == 24 else None)
        for i in range(25)
    ] + [
        (other_agent, user_id, "detetive", f"other-{i}", base + timedelta(seconds=i),
         Jsonb({"source": "other", "index": i}), None, None, None)
        for i in range(105)
    ] + [
        (foreign_agent, other_user, kind, "foreign", base + timedelta(days=1),
         Jsonb({"source": "foreign"}), None, None, None),
        (own_agent, user_id, kind, "stale", base + timedelta(days=1),
         Jsonb({"source": "stale"}), base, None, None),
    ]
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            "insert into agent_events "
            "(agent_id, user_id, kind, dedupe_key, fired_at, payload, stale_at, seen_at, suppressed_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)", rows)
        conn.commit()

    # A listagem sem tema mantém o contrato do feed: tipos misturados e limite
    # posicional. Há mais de 100 eventos de outro tema na frente dos próprios.
    global_feed = db.list_agent_events(user_id, 100)
    assert len(global_feed) == 100
    assert all(event["kind"] == "detetive" for event in global_feed)
    assert [event["payload"]["index"] for event in global_feed] == list(range(104, 4, -1))

    events = chat.execute_read(user_id, kind, "consultar_dados_do_agente", {})["alertas"]
    assert len(events) == 20
    assert all(event["kind"] == kind and event["payload"]["source"] == "own" for event in events)
    assert [event["payload"]["index"] for event in events] == list(range(24, 4, -1))
    assert events[0]["seen_at"] == base  # lido e suprimido para e-mail ainda é histórico visível
    assert all(event["seen_at"] is None for event in events[1:])
    assert db.list_agent_events(user_id, 20, kind=kind) == events
    assert db.list_agent_events(user_id, 100) == global_feed  # consulta não marca o feed como lido
