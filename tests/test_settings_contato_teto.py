"""#610: o PATCH /settings/{uid}/security/contact responde 409 quando o telefone
ou e-mail é de outra conta, e isso deixava enumerar contas sem limite.

D5(b): mantém o 409 e põe teto de 5 trocas de contato por hora por conta,
telefone e e-mail no mesmo balde. Conta toda troca (inclusive a que dá 409);
PATCH que não muda contato não conta. Rota real, banco real, CSRF.
"""
from __future__ import annotations

import uuid

import pytest

import frontend.finance_bot_websocket_custom as dashboard
from conftest import promote_to_pro
from db import ensure_user
from db.connection import get_conn
from frontend.routes.settings import CONTATO_EM_USO
from tests._helpers_pii import insert_auth_account_pii
from tests.test_senha_obrigatoria_whatsapp import _dono_do_numero, _gastos, _manda, enviadas  # noqa: F401
from tests.test_settings_contact_pii_sync import _auth_row, _client_for

TETO = 5


def _telefone() -> str:
    return "5511" + str(900000000 + uuid.uuid4().int % 99999999)


def _email() -> str:
    return f"teto-{uuid.uuid4().hex[:10]}@test.local"


def _conta(uid: int, phone: str | None = None):
    """Conta com login e plano; devolve (patch, email, phone)."""
    email = _email()
    with get_conn() as conn, conn.cursor() as cur:
        insert_auth_account_pii(cur, uid, email, phone=phone)
        conn.commit()
    promote_to_pro(uid)
    client, headers = _client_for(uid, email)

    def patch(**body):
        return client.patch(f"/settings/{uid}/security/contact", json=body, headers=headers)

    return patch, email, phone


def _outra_conta(phone: str | None = None):
    uid = int(uuid.uuid4().int % 900_000_000) + 1
    ensure_user(uid)
    return uid, *_conta(uid, phone=phone or _telefone())


def _tentativas(uid: int):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select attempts from auth_rate_limits where bucket = 'settings-contact'"
                    " and identifier = %s", (f"user:{uid}",))
        row = cur.fetchone()
    return row and row["attempts"]


def _assert_429(resp):
    assert resp.status_code == 429, resp.text
    assert resp.json()["detail"] == dashboard.RATE_LIMIT_DETAIL
    assert int(resp.headers["Retry-After"]) > 0


def _estoura(patch, alvo: dict):
    for i in range(TETO):
        resp = patch(**alvo)
        assert resp.status_code == 409, (i, resp.text)


def test_telefone_de_outra_conta_409_ate_5_e_429_na_sexta(user_id):
    patch, _, meu_tel = _conta(user_id, phone=_telefone())
    _, _, _, tel_de_b = _outra_conta()

    _estoura(patch, {"phone": tel_de_b})
    _assert_429(patch(phone=tel_de_b))

    # O balde é um só: depois do teto, nem e-mail livre passa, e nada é gravado.
    email_livre = _email()
    _assert_429(patch(email=email_livre))
    _assert_429(patch(phone=_telefone()))
    row = _auth_row(user_id)
    assert row["phone_e164"] == meu_tel
    assert row["email"] != email_livre


def test_email_de_outra_conta_409_ate_5_e_429_na_sexta(user_id):
    patch, meu_email, _ = _conta(user_id)
    _, _, email_de_b, _ = _outra_conta()

    _estoura(patch, {"email": email_de_b})
    _assert_429(patch(email=email_de_b))
    assert _auth_row(user_id)["email"] == meu_email


def test_troca_para_contato_livre_responde_200_e_grava(user_id):
    patch, _, _ = _conta(user_id, phone=_telefone())
    for _ in range(TETO):
        novo = _telefone()
        resp = patch(phone=novo)
        assert resp.status_code == 200, resp.text
        assert _auth_row(user_id)["phone_e164"] == novo
    # A troca legítima também conta: a 6ª da hora para no teto.
    _assert_429(patch(phone=_telefone()))


def test_patch_que_nao_muda_contato_nao_conta(user_id):
    meu_tel = _telefone()
    patch, meu_email, _ = _conta(user_id, phone=meu_tel)
    formatado = f"+{meu_tel[:2]} {meu_tel[2:4]} {meu_tel[4:]}"
    for _ in range(TETO + 3):
        resp = patch(phone=formatado, email=meu_email.upper(), display_name="Fulana")
        assert resp.status_code == 200, resp.text
    novo = _telefone()
    assert patch(phone=novo).status_code == 200
    assert _auth_row(user_id)["phone_e164"] == novo


def test_teto_de_uma_conta_nao_afeta_outra(user_id):
    patch_a, _, _ = _conta(user_id, phone=_telefone())
    uid_b, patch_b, _, tel_de_b = _outra_conta()

    _estoura(patch_a, {"phone": tel_de_b})
    _assert_429(patch_a(phone=tel_de_b))

    novo = _telefone()
    resp = patch_b(phone=novo)
    assert resp.status_code == 200, resp.text
    assert _auth_row(uid_b)["phone_e164"] == novo


def test_janela_vencida_libera_a_troca(user_id):
    patch, _, _ = _conta(user_id, phone=_telefone())
    _, _, _, tel_de_b = _outra_conta()
    _estoura(patch, {"phone": tel_de_b})
    _assert_429(patch(phone=tel_de_b))

    with get_conn() as conn:
        conn.execute(
            "update auth_rate_limits set window_started_at = now() - interval '61 minutes'"
            " where bucket = 'settings-contact' and identifier = %s",
            (f"user:{user_id}",),
        )
        conn.commit()

    novo = _telefone()
    resp = patch(phone=novo)
    assert resp.status_code == 200, resp.text
    assert _auth_row(user_id)["phone_e164"] == novo


def test_outra_conta_nao_gasta_a_cota_de_b(user_id):
    """A autorização vem antes da contagem: A não esgota o teto de B."""
    _, email_a, _ = _conta(user_id, phone=_telefone())
    uid_b, patch_b, _, _ = _outra_conta()
    client_a, headers_a = _client_for(user_id, email_a)
    for _ in range(TETO + 2):
        resp = client_a.patch(f"/settings/{uid_b}/security/contact", json={"phone": _telefone()},
                              headers=headers_a)
        assert resp.status_code in (401, 403), resp.text
    assert _tentativas(uid_b) is None
    for _ in range(TETO):
        assert patch_b(phone=_telefone()).status_code == 200


# ── #630: a variante com/sem o nono dígito (critério do `telefone_livre`) ─────

def _com_nove() -> str:
    return "551198" + f"{uuid.uuid4().int % 10_000_000:07d}"


def _sem_nove(tel: str) -> str:
    return tel[:4] + tel[5:]


def _par(b_com_nove: bool) -> tuple[str, str]:
    """(número gravado, a variante dele com ou sem o nono dígito)."""
    tel = _com_nove()
    return (tel, _sem_nove(tel)) if b_com_nove else (_sem_nove(tel), tel)


@pytest.mark.parametrize("b_com_nove", [True, False])
def test_variante_do_telefone_de_outra_conta_da_409(user_id, b_com_nove):
    tel_b, variante = _par(b_com_nove)
    _outra_conta(phone=tel_b)
    patch, _, meu_tel = _conta(user_id, phone=_telefone())

    resp = patch(phone=variante)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == CONTATO_EM_USO
    assert _auth_row(user_id)["phone_e164"] == meu_tel
    assert _tentativas(user_id) == 1


@pytest.mark.parametrize("com_nove", [True, False])
def test_variante_do_proprio_numero_da_200(user_id, com_nove):
    meu_tel, variante = _par(com_nove)
    patch, _, _ = _conta(user_id, phone=meu_tel)

    resp = patch(phone=variante)
    assert resp.status_code == 200, resp.text
    assert _auth_row(user_id)["phone_e164"] == variante


def test_depois_do_409_o_numero_de_b_segue_ligando_na_conta_de_b(user_id, enviadas):
    """A conversa: sem o critério das variantes, A gravava a variante do número de
    B e a mensagem do próprio B caía em `multiple_accounts`."""
    tel_b = _com_nove()
    uid_b, _, _, _ = _outra_conta(phone=tel_b)
    patch, _, _ = _conta(user_id, phone=_telefone())
    resp = patch(phone=_sem_nove(tel_b))

    _manda(tel_b, "oi")
    _manda(tel_b, "Gastei R$ 50 no mercado")
    assert _dono_do_numero(tel_b) == uid_b, enviadas
    assert _gastos(uid_b) == 1, enviadas
    assert resp.status_code == 409, resp.text
