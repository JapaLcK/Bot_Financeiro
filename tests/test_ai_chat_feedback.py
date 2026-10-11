"""👍👎 (`POST /ai/messages/{id}/feedback`): isolamento por usuário e borda estrita."""
import pytest

import db
from _ia_stream_helpers import conta, esgota_a_cota
from test_senha_obrigatoria import H


def _msg(uid, role="assistant"):
    return db.ai_append_message(uid, role, "texto")


def _nota(mid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select feedback from ai_messages where id=%s", (mid,))
        v = cur.fetchone()["feedback"]
        conn.commit()
    return v


def test_feedback_grava_e_null_limpa():
    uid, c = conta()
    mid = _msg(uid)
    r = c.post(f"/ai/messages/{mid}/feedback", json={"nota": -1}, headers=H)
    assert r.status_code == 200 and r.json() == {"ok": True, "nota": -1} and _nota(mid) == -1
    assert c.post(f"/ai/messages/{mid}/feedback", json={"nota": None}, headers=H).status_code == 200
    assert _nota(mid) is None


def test_feedback_o_mesmo_404_para_outro_usuario_inexistente_e_role_user():
    a, ca = conta()
    b, _ = conta()
    de_b, minha_user = _msg(b), _msg(a, "user")
    respostas = [ca.post(f"/ai/messages/{m}/feedback", json={"nota": 1}, headers=H)
                 for m in (de_b, 2**62, minha_user)]
    assert [r.status_code for r in respostas] == [404, 404, 404]
    assert len({r.text for r in respostas}) == 1
    assert _nota(de_b) is None and _nota(minha_user) is None


@pytest.mark.parametrize("corpo", [{"nota": 2}, {"nota": 0}, {}, {"nota": True}, {"nota": 1.0}, {"nota": "1"}])
def test_feedback_nota_invalida_422(corpo):
    uid, c = conta()
    mid = _msg(uid)
    assert c.post(f"/ai/messages/{mid}/feedback", json=corpo, headers=H).status_code == 422
    assert _nota(mid) is None


def test_feedback_gate_403():
    uid, c = conta()
    mid = _msg(uid)
    esgota_a_cota(uid)
    assert c.post(f"/ai/messages/{mid}/feedback", json={"nota": 1}, headers=H).status_code == 403
