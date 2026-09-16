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


def test_rota_invalida_o_cache_do_dashboard(uid_pro, ia_fora, monkeypatch):
    from frontend.routes import shared
    _, of_tx, _, _ = pendencia(uid_pro)
    chamadas = []
    monkeypatch.setattr(shared, "invalidate_dashboard_current_cache", chamadas.append)
    r = _cliente(uid_pro).post(f"/open-finance/{uid_pro}/reconciliations/{of_tx}/confirm", headers=H)
    assert r.status_code == 200, r.text
    assert chamadas == [uid_pro]


def test_teto_nao_se_contorna_variando_a_url(uid_pro):
    """Com `limit()` cada `of_tx_id` abria balde próprio: 31 ids → zero 429.

    Não crava em QUAL requisição o 429 chega: `tests/test_of_connect_token_gate.py`
    faz `importlib.reload` deste router, cada reload soma o limite de novo e na
    suíte inteira uma requisição gasta vários slots (medido: 429 na 7ª). O que
    discrimina é existir 429 com URLs todas diferentes — com `limit()` não há."""
    client = _cliente(uid_pro)
    base = f"/open-finance/{uid_pro}/reconciliations"
    codes = [client.post(f"{base}/{900000000 + i}/{('confirm', 'reject', 'undo')[i % 3]}",
                         headers=H).status_code for i in range(31)]
    assert codes[0] == 404 and 429 in codes, codes
    codes = [client.get(f"/open-finance/{uid_pro + i}/reconciliations").status_code
             for i in range(61)]
    assert codes[0] == 200 and 429 in codes, codes
