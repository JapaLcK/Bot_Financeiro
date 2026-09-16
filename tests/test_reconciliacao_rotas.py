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


def _um_registro_do_teto(monkeypatch, rota):
    """`tests/test_of_connect_token_gate.py` faz `importlib.reload` deste router 4
    vezes; o slowapi ESTENDE `_route_limits[rota]` a cada decoração, então na suíte
    inteira a lista tem 5 cópias do mesmo teto e cada requisição gasta 5 slots
    (medido: 5 × '30 per 1 minute', 429 na 7ª). Em produção o módulo é importado
    uma vez. Aqui se exige que as cópias sejam todas iguais e se mede com uma só."""
    from frontend.routes import shared
    nome = f"frontend.routes.open_finance.{rota}"
    limites = shared.limiter._route_limits[nome]
    assert len({(str(l.limit), l.scope) for l in limites}) == 1, limites
    monkeypatch.setitem(shared.limiter._route_limits, nome, limites[:1])
    shared.limiter.reset()


def test_teto_nao_se_contorna_variando_a_url(uid_pro, monkeypatch):
    """Com `limit()` cada `of_tx_id` abria balde próprio: 31 ids → zero 429.
    Com o balde por rota, as 30 primeiras passam e a 31ª dá 429."""
    client = _cliente(uid_pro)
    _um_registro_do_teto(monkeypatch, "reconciliation_action_route")
    base = f"/open-finance/{uid_pro}/reconciliations"
    codes = [client.post(f"{base}/{900000000 + i}/{('confirm', 'reject', 'undo')[i % 3]}",
                         headers=H).status_code for i in range(31)]
    assert codes == [404] * 30 + [429], codes

    _um_registro_do_teto(monkeypatch, "reconciliations_route")
    codes = [client.get(f"/open-finance/{uid_pro + i}/reconciliations").status_code
             for i in range(61)]
    assert 429 not in codes[:60] and codes[60] == 429, codes
