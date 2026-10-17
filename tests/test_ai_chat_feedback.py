"""👍👎 (`POST /ai/messages/{id}/feedback`): isolamento por usuário e borda estrita."""
import pytest

import db
from db.ai_feedback import MOTIVOS
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


def test_feedback_rate_limit_vale_para_qualquer_message_id():
    """Cada id num balde próprio tornaria o teto decorativo: a 61ª dá 429."""
    _, c = conta()
    codigos = [c.post(f"/ai/messages/{2**40 + i}/feedback", json={"nota": 1}, headers=H).status_code
               for i in range(61)]
    assert codigos[:60] == [404] * 60 and codigos[60] == 429


# --- motivo do 👎 e comentário do "Outro" (PR 4a) ---------------------------------------

def _estado(mid):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select feedback, feedback_motivo, feedback_texto from ai_messages where id=%s", (mid,))
        r = cur.fetchone()
        conn.commit()
    return r["feedback"], r["feedback_motivo"], r["feedback_texto"]


def _post(c, mid, corpo):
    return c.post(f"/ai/messages/{mid}/feedback", json=corpo, headers=H)


def test_motivo_grava_e_build_31_sem_motivo_segue_200():
    uid, c = conta()
    mid = _msg(uid)
    assert _post(c, mid, {"nota": -1, "motivo": "numero_errado"}).json() == {"ok": True, "nota": -1}
    assert _estado(mid) == (-1, "numero_errado", None)
    # build 31: só {nota} -> 200 e motivo NULL (também zera o que havia)
    assert _post(c, mid, {"nota": -1}).status_code == 200
    assert _estado(mid) == (-1, None, None)


def test_outro_com_texto_strip_e_limite_de_500():
    uid, c = conta()
    mid = _msg(uid)
    assert _post(c, mid, {"nota": -1, "motivo": "outro", "texto": "  x \n"}).status_code == 200
    assert _estado(mid) == (-1, "outro", "x")
    assert _post(c, mid, {"nota": -1, "motivo": "outro", "texto": "a" * 500}).status_code == 200
    assert _estado(mid)[2] == "a" * 500
    # texto só de espaços = sem texto
    assert _post(c, mid, {"nota": -1, "motivo": "outro", "texto": "   "}).status_code == 200
    assert _estado(mid) == (-1, "outro", None)


@pytest.mark.parametrize("troca", [{"nota": 1}, {"nota": None}, {"nota": -1}])
def test_trocar_de_estado_zera_motivo_e_texto(troca):
    uid, c = conta()
    mid = _msg(uid)
    assert _post(c, mid, {"nota": -1, "motivo": "outro", "texto": "errou a conta"}).status_code == 200
    assert _estado(mid) == (-1, "outro", "errou a conta")
    assert _post(c, mid, troca).status_code == 200
    assert _estado(mid) == (troca["nota"], None, None)


@pytest.mark.parametrize("corpo", [
    {"nota": 1, "motivo": "numero_errado"},
    {"nota": None, "motivo": "numero_errado"},
    {"nota": -1, "motivo": "xyz"},
    {"nota": -1, "motivo": 1},
    {"nota": -1, "motivo": True},
    {"nota": -1, "texto": "sem motivo"},
    {"nota": -1, "motivo": "numero_errado", "texto": "só com outro"},
    {"nota": -1, "motivo": "outro", "texto": "a" * 501},
    {"nota": -1, "motivo": "outro", "texto": 123},
    {"nota": -1, "motivo": "outro", "texto": ["x"]},
])
def test_corpo_invalido_422_sem_gravar_e_sem_eco(corpo):
    uid, c = conta()
    mid = _msg(uid)
    r = _post(c, mid, corpo)
    assert r.status_code == 422
    assert _estado(mid) == (None, None, None)
    if isinstance(corpo.get("texto"), str):
        assert corpo["texto"] not in r.text


def test_texto_com_nul_e_surrogate_nao_da_500():
    uid, c = conta()
    mid = _msg(uid)
    r = c.post(f"/ai/messages/{mid}/feedback", headers={**H, "Content-Type": "application/json"},
               content='{"nota":-1,"motivo":"outro","texto":"a\\u0000b"}')
    assert r.status_code == 200 and "\x00" not in _estado(mid)[2]
    r = c.post(f"/ai/messages/{mid}/feedback", headers={**H, "Content-Type": "application/json"},
               content='{"nota":-1,"motivo":"outro","texto":"a\\ud800b"}')
    assert r.status_code == 200  # nunca 500


def test_motivo_de_outro_usuario_404_e_nao_toca_a_linha():
    a, ca = conta()
    b, _ = conta()
    de_b = _msg(b)
    assert _post(ca, de_b, {"nota": -1, "motivo": "outro", "texto": "x"}).status_code == 404
    assert _estado(de_b) == (None, None, None)


def test_motivos_sao_o_contrato_com_o_app_e_a_tela_de_admin():
    assert MOTIVOS == {"numero_errado": "Número errado", "nao_entendeu": "Não entendeu",
                       "faltou_card": "Faltou card", "outro": "Outro"}


@pytest.mark.parametrize("motivo", ["numero_errado", "nao_entendeu", "faltou_card", "outro"])
def test_cada_motivo_do_contrato_grava(motivo):
    uid, c = conta()
    mid = _msg(uid)
    assert _post(c, mid, {"nota": -1, "motivo": motivo}).status_code == 200
    assert _estado(mid) == (-1, motivo, None)


def test_limite_de_500_conta_code_points_nao_bytes():
    uid, c = conta()
    mid = _msg(uid)
    assert _post(c, mid, {"nota": -1, "motivo": "outro", "texto": "🐷" * 500}).status_code == 200
    assert _estado(mid)[2] == "🐷" * 500
    assert _post(c, mid, {"nota": -1, "motivo": "outro", "texto": "🐷" * 501}).status_code == 422
    assert _estado(mid)[2] == "🐷" * 500  # o 422 não tocou na linha
