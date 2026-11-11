"""Teto de bancos do Open Finance do membro da família sem plano pago próprio (1 banco).

Postgres real, HTTP (`/pluggy-item`, `/limite`, `/auth/me`) e a varredura `enforce_of_bank_limits`.
Fonte única: `OF_BANKS_MAX_FAMILIA` + `teto_bancos_plus`, aplicados em `get_user_limits`.

Controles (mutação em `get_user_limits`, tirar o `min(...)`): `test_membro_sem_plano_proprio_2o_banco_402`,
`test_varredura_pausa_a_de_id_maior` e a tabela do predicado ficam vermelhos; os positivos
(titular Pro 5, Plus próprio 2, Pro próprio 5, vitalício 2, Essencial próprio 1, reconexão, sair da família)
provam que o caminho legítimo segue.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as of_routes
from _billing_grants_helpers import conta
from core.services import open_finance_trial_expiry as ofte
from core.services.billing_access import teto_bancos_plus
from core.services.plan_limits import OF_BANKS_MAX_FAMILIA
from core.services.plan_service import get_user_limits
from db import family as fam
from db.connection import get_conn
from db.plan_grants import upsert_grant
from test_api_v2_familia import _agora, _titular, _usuario
from test_of_item_ownership import _auth
from test_of_webhook_adopt_guards import webhook_pluggy  # noqa: F401 (fixture)

MSG_FAMILIA = "Pelo Plano Família você conecta 1 banco. Assine um plano próprio para conectar mais."


@pytest.fixture(autouse=True)
def _pluggy(monkeypatch, webhook_pluggy):  # noqa: F811
    # item "tf-<uid>-<n>" pertence ao uid do nome
    monkeypatch.setattr(of_routes, "get_pluggy_item", lambda item_id, api_key=None: {
        "id": item_id, "status": "UPDATED", "clientUserId": item_id.split("-")[1],
        "connector": {"id": 612, "name": "Nubank"}})


def _membro(owner, plano="free"):
    uid = _usuario(plano)
    fam.accept_invite(fam.create_invite(owner), uid)
    return uid


def _conecta(uid, n):
    c = TestClient(dashboard.app)
    h = _auth(c, uid)
    return c.post(f"/open-finance/{uid}/pluggy-item", json={"item": {"id": f"tf-{uid}-{n}"}}, headers=h)


def _limite(uid):
    c = TestClient(dashboard.app)
    _auth(c, uid)
    return c.get(f"/open-finance/{uid}/limite").json()


def _me_teto(uid):
    c = TestClient(dashboard.app)
    _auth(c, uid)
    return c.get("/auth/me").json()["of_banks_max"]


def _conecta_ate(uid, ok):
    for n in range(ok):
        assert _conecta(uid, n).status_code == 200, n
    return _conecta(uid, ok)


@pytest.fixture()
def titular():
    return _titular("Ana")


def test_membro_sem_plano_proprio_1o_banco_ok_e_limite_1(titular):
    m = _membro(titular)
    assert _conecta(m, 0).status_code == 200
    assert _limite(m)["of_banks_max"] == 1 == OF_BANKS_MAX_FAMILIA
    assert _me_teto(m) == 1


def test_membro_sem_plano_proprio_2o_banco_402(titular):
    m = _membro(titular)
    r = _conecta_ate(m, 1)
    assert r.status_code == 402, r.text
    d = r.json()["detail"]
    assert (d["code"], d["limit"], d["message"]) == ("OF_BANK_LIMIT", 1, MSG_FAMILIA)
    assert _limite(m)["message"] == MSG_FAMILIA


def test_reconexao_do_mesmo_item_passa(titular):
    m = _membro(titular)
    assert _conecta(m, 0).status_code == 200
    assert _conecta(m, 0).status_code == 200


def test_plus_proprio_2_e_mensagem_antiga(titular):
    m = _membro(titular, "pro")  # 'pro' armazenado = Plus
    r = _conecta_ate(m, 2)
    assert r.status_code == 402 and r.json()["detail"]["limit"] == 2
    assert r.json()["detail"]["message"].startswith("Seu plano conecta até 2 bancos")


def test_essencial_proprio_sem_familia_402_com_mensagem_antiga():
    uid = _usuario("essencial")
    r = _conecta_ate(uid, 1)
    d = r.json()["detail"]
    assert r.status_code == 402 and d["limit"] == 1
    assert d["message"] == "Seu plano conecta até 1 banco. Faça upgrade pra conectar mais: /precos"
    assert _limite(uid)["message"] == d["message"]


def test_pro_proprio_5(titular):
    m = _membro(titular, "pro_max")
    assert _conecta_ate(m, 5).json()["detail"]["limit"] == 5


@pytest.mark.parametrize("status", ["grandfathered", "active"])
def test_plus_vitalicio_2(titular, status):
    m = _usuario("free")
    conta(m, "pro", None, status)
    fam.accept_invite(fam.create_invite(titular), m)
    assert get_user_limits(m)["of_banks_max"] == 2


def test_essencial_proprio_com_familia_1_com_mensagem_do_essencial(titular):
    m = _membro(titular, "essencial")
    assert get_user_limits(m)["of_banks_max"] == 1
    assert _limite(m)["of_banks_max"] == 1
    r = _conecta_ate(m, 1)
    d = r.json()["detail"]
    assert r.status_code == 402 and d["limit"] == 1
    assert d["message"] == "Seu plano conecta até 1 banco. Faça upgrade pra conectar mais: /precos"
    assert _limite(m)["message"] == d["message"]


def test_titular_pro_5(titular):
    assert get_user_limits(titular)["of_banks_max"] == 5


def test_sair_da_familia_com_plus_proprio_segue_em_2(titular):
    m = _membro(titular, "pro")
    assert _limite(m)["of_banks_max"] == 2          # Plus próprio: a família não rebaixa
    fam.leave_family(m)
    assert _limite(m)["of_banks_max"] == 2          # segue com o do próprio plano


def test_isolamento_entre_membros(titular):
    outro = _titular("Bia")
    a, b = _membro(titular), _membro(outro, "pro")
    assert get_user_limits(a)["of_banks_max"] == 1 and get_user_limits(b)["of_banks_max"] == 2


def test_varredura_pausa_a_de_id_maior(titular, monkeypatch):
    m = _membro(titular)
    with get_conn() as conn, conn.cursor() as cur:
        ids = []
        for i in range(2):
            cur.execute("insert into open_finance_connections (user_id, provider, provider_item_id, status,"
                        " institution_id, institution_name) values (%s,'pluggy',%s,'UPDATED','612','Nubank')"
                        " returning id", (m, f"tf-{m}-v{i}"))
            ids.append(cur.fetchone()["id"])
        conn.commit()
    apagados = []
    monkeypatch.setattr(ofte, "create_pluggy_api_key", lambda: "k")
    monkeypatch.setattr(ofte, "delete_pluggy_item", lambda item, key=None: apagados.append(item) or True)
    real = ofte.list_pluggy_connections_for_trial_sweep
    monkeypatch.setattr(ofte, "list_pluggy_connections_for_trial_sweep",
                        lambda: [c for c in real() if int(c["user_id"]) == m])
    ofte.enforce_of_bank_limits()
    assert apagados == [f"tf-{m}-v1"]


# ── predicado: tabela ────────────────────────────────────────────────────────

def _grant(uid, source, plano, ref, fim_dias=30):
    upsert_grant(uid, source, ref, plano, _agora() - timedelta(days=1), _agora() + timedelta(days=fim_dias), 1)


@pytest.mark.parametrize("proprios, conta_, esperado", [
    ([], ("free", None, ""), (1, "familia")),                                   # só family
    ([("stripe", "pro")], ("pro", "d", "active"), (2, None)),                   # Plus próprio
    ([("stripe", "essencial")], ("essencial", "d", "active"), (1, None)),       # Essencial próprio
    ([("pix", "essencial")], ("essencial", "d", "active"), (1, None)),          # Essencial por Pix
    ([("pix", "pro_max")], ("pro_max", "d", "active"), (5, None)),              # Pro próprio
    ([], ("pro", None, "grandfathered"), (2, None)),                            # grandfathered
    ([], ("pro", None, "active"), (2, None)),                                   # vitalício
])
def test_tabela_do_teto(proprios, conta_, esperado):
    uid = _usuario("free")
    plano, exp, st = conta_
    conta(uid, plano, _agora() + timedelta(days=30) if exp == "d" else exp, st)
    _grant(uid, "family", "pro", f"family_member:{uid}")
    for i, (src, p) in enumerate(proprios):
        _grant(uid, src, p, f"{src}_{uid}_{i}")
    assert teto_bancos_plus(uid) == esperado


def test_sem_grant_family_motivo_none():
    assert teto_bancos_plus(_usuario("pro"))[1] is None


def test_grandfathered_com_validade_preenchida_e_so_family_2():
    uid = _usuario("free")
    conta(uid, "pro", _agora() + timedelta(days=30), "grandfathered")
    _grant(uid, "family", "pro", f"family_member:{uid}")
    assert teto_bancos_plus(uid) == (2, None)


def test_plan_atrasado_pro_max_vigente_so_grant_family_402_limit_5_sem_mensagem_da_familia():
    # auth_accounts.plan ainda 'pro_max' (vigente) mas só resta o grant family: o teto cobrado é 5
    uid = _usuario("free")
    conta(uid, "pro_max", _agora() + timedelta(days=30), "active")
    _grant(uid, "family", "pro", f"family_member:{uid}")
    assert get_user_limits(uid)["of_banks_max"] == 5
    r = _conecta_ate(uid, 5)
    assert r.status_code == 402, r.text
    d = r.json()["detail"]
    assert d["limit"] == 5 and d["message"] != MSG_FAMILIA and "Família" not in d["message"]
