"""Rotas de reconciliação: sessão, CSRF, isolamento por usuário e mapa de erros."""
from __future__ import annotations

from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard

from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401 (fixtures)
from tests.test_reconciliacao_resolver import _estado, pendencia


def _cliente(uid):
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(uid, "recon@test.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(uid, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "recon-test")
    return client


H = {dashboard.CSRF_HEADER_NAME: "recon-test"}


def test_rotas_confere_sessao_csrf_isolamento_e_erros(uid_pro, ia_fora):
    _, of_tx, manual, _ = pendencia(uid_pro)
    base = f"/open-finance/{uid_pro}/reconciliations"

    assert TestClient(dashboard.app).get(base).status_code in (401, 403)

    client = _cliente(uid_pro)
    r = client.get(base)
    assert r.status_code == 200, r.text
    assert [x["of_tx_id"] for x in r.json()["reconciliations"]] == [of_tx]

    assert client.post(f"{base}/{of_tx}/confirm").status_code == 403, "sem CSRF passou"
    assert client.get(f"/open-finance/{uid_pro + 1}/reconciliations").status_code in (401, 403)
    assert client.post(f"/open-finance/{uid_pro + 1}/reconciliations/{of_tx}/confirm",
                       headers=H).status_code in (401, 403)

    outro = uid_pro + 1
    db.ensure_user(outro)
    alheio = _cliente(outro)
    assert alheio.get(f"/open-finance/{outro}/reconciliations").json()["reconciliations"] == []
    for acao in ("confirm", "reject", "undo"):
        r = alheio.post(f"/open-finance/{outro}/reconciliations/{of_tx}/{acao}", headers=H)
        assert r.status_code == 404, (acao, r.text)
    assert _estado(of_tx)["reconciliation_status"] == "pending"

    r = client.post(f"{base}/{of_tx}/reject", headers=H)
    assert r.status_code == 200 and r.json()["changed"] is True, r.text
    r = client.post(f"{base}/{of_tx}/confirm", headers=H)
    assert r.status_code == 409, r.text
    assert client.post(f"{base}/{of_tx}/apagar", headers=H).status_code in (404, 422)
