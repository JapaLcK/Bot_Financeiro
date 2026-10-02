"""`/api/v2/assinaturas` pelo monólito real: sessão, CSRF do pai, gate Plus/Pro,
marcação e isolamento entre usuários. Banco real."""
import io
import json
import uuid
import zipfile
from datetime import date

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from _apoio_assinaturas import conta, mensais, netflix_no_cartao, rp, semeia, tx
from _apoio_auth_app import csrf, sessao_de as sessao
from conftest import promote_to_pro
from db import ensure_user
from db.of_recurring import marcas
from db.privacy import build_user_export_zip

GET = "/api/v2/assinaturas"
POST = "/api/v2/assinaturas/marca"


@pytest.fixture
def a_e_b(monkeypatch, user_id):
    a = user_id
    b = int(uuid.uuid4().int % 10_000_000_000)  # o autouse do conftest apaga depois
    ensure_user(b)
    promote_to_pro(a)  # 'pro' gravado = Plus no v2
    promote_to_pro(b)
    monkeypatch.setenv("DASHBOARD_V2_BETA_EMAILS", "")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{a},{b}")
    return a, b


def _cliente(uid, com_csrf=True):
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao(uid)["dashboard"])
    return client, (csrf(client) if com_csrf else {})


def _get(uid):
    client, _ = _cliente(uid)
    return client.get(GET)


def _post(uid, chave, status):
    client, h = _cliente(uid)
    return client.post(POST, json={"chave": chave, "status": status}, headers=h)


# ── 4. Isolamento no join ────────────────────────────────────────────────────

def test_mesmo_id_de_transacao_em_b_nao_contamina_a(a_e_b):
    a, b = a_e_b
    netflix_no_cartao(a)
    semeia(b, [conta("acc-b", mensais("nf", [-99] * 3, desc="OUTRA"), nome="Conta B")], [])
    r = _get(a)
    assert r.status_code == 200, r.text
    (it,) = r.json()["servicos"]
    assert (it["valor"], it["meses"]) == (39.9, 3)
    assert it["meio"] == {"tipo": "cartao", "nome": "Nubank Mastercard", "final": "1234"}
    rb = _get(b)
    assert rb.status_code == 200, rb.text
    assert rb.json() == {"servicos": [], "outras": [], "total_mensal": 0.0, "total_anual": 0.0}


# ── 10. Marcação ─────────────────────────────────────────────────────────────

def _com_netflix_e_claro(uid):
    netflix_no_cartao(uid)
    txs = mensais("cl", [-49.9] * 3, category="Telecommunications", ultima=date(2026, 9, 20))
    semeia(uid, [conta("acc-cl", txs)], [rp("claro flex", -49.9, txs)])


def test_ignorar_esconde_e_nenhuma_traz_de_volta(a_e_b):
    a, _ = a_e_b
    _com_netflix_e_claro(a)
    r = _post(a, "netflix", "ignorar")
    assert r.status_code == 200, r.text
    assert (r.json()["servicos"], r.json()["total_mensal"]) == ([], 0.0)
    assert _get(a).json()["servicos"] == []
    r = _post(a, "netflix", "nenhuma")
    assert [x["chave"] for x in r.json()["servicos"]] == ["netflix"]
    assert r.json()["total_mensal"] == 39.9


def test_assinatura_move_de_outras_para_servicos(a_e_b):
    a, _ = a_e_b
    _com_netflix_e_claro(a)
    assert [x["chave"] for x in _get(a).json()["outras"]] == ["claro flex"]
    j = _post(a, "claro flex", "assinatura").json()
    assert j["outras"] == []
    assert {x["chave"]: x["marcada"] for x in j["servicos"]} == {"claro flex": True, "netflix": False}
    assert j["total_mensal"] == 89.8


def test_chave_inexistente_404(a_e_b):
    a, _ = a_e_b
    _com_netflix_e_claro(a)
    r = _post(a, "nao existe", "ignorar")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")


def test_b_nao_marca_a_chave_de_a(a_e_b):
    a, b = a_e_b
    _com_netflix_e_claro(a)
    assert _post(a, "netflix", "assinatura").status_code == 200
    for status in ("ignorar", "nenhuma"):
        r = _post(b, "netflix", status)
        assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")
    assert marcas(a) == {"netflix": "assinatura"}
    assert marcas(b) == {}


def test_nenhuma_de_b_nao_apaga_a_marca_de_a_com_a_mesma_chave(a_e_b):
    a, b = a_e_b
    netflix_no_cartao(a)
    netflix_no_cartao(b)
    assert _post(a, "netflix", "assinatura").status_code == 200
    assert _post(b, "netflix", "assinatura").status_code == 200
    assert _post(b, "netflix", "nenhuma").status_code == 200
    assert (marcas(a), marcas(b)) == ({"netflix": "assinatura"}, {})


def test_export_lgpd_de_a_nao_leva_dado_de_b(a_e_b):
    a, b = a_e_b
    netflix_no_cartao(a)
    netflix_no_cartao(b)
    _post(b, "netflix", "ignorar")
    dados = json.loads(zipfile.ZipFile(io.BytesIO(build_user_export_zip(a))).read("dados.json"))["dados"]
    assert [r["description"] for r in dados["recorrencias_open_finance"]] == ["NETFLIX.COM"]
    assert dados["marcacoes_assinaturas"] == []


def test_assinatura_depois_ignorar_troca_o_status(a_e_b):
    a, _ = a_e_b
    _com_netflix_e_claro(a)
    _post(a, "netflix", "assinatura")
    j = _post(a, "netflix", "ignorar").json()
    assert marcas(a) == {"netflix": "ignorar"}
    assert [x["chave"] for x in j["servicos"]] == []


def test_chave_de_parcela_nao_e_marcavel(a_e_b):
    a, _ = a_e_b
    parc = [tx(f"pc-{i}", -100, date(2026, m, 10), cc={"installmentNumber": i, "totalInstallments": 10})
            for i, m in ((1, 7), (2, 8), (3, 9))]
    semeia(a, [conta("acc-1", parc)], [rp("LOJA X", -100, parc)])
    r = _post(a, "loja x", "assinatura")
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")


# ── 11. Gate e CSRF ──────────────────────────────────────────────────────────

def test_essencial_leva_403_pro_required(monkeypatch, user_id):
    promote_to_pro(user_id, plan="essencial")
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", str(user_id))
    netflix_no_cartao(user_id)
    for r in (_get(user_id), _post(user_id, "netflix", "ignorar")):
        assert (r.status_code, r.json()["error"]["code"]) == (403, "pro_required"), r.text
    assert marcas(user_id) == {}


def test_positivo_plus_entra(a_e_b):
    a, _ = a_e_b
    netflix_no_cartao(a)
    assert _get(a).status_code == 200
    assert _post(a, "netflix", "ignorar").status_code == 200


def test_post_sem_token_csrf_403_e_com_token_200(a_e_b):
    a, _ = a_e_b
    netflix_no_cartao(a)
    client, _ = _cliente(a, com_csrf=False)
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "tok")
    r = client.post(POST, json={"chave": "netflix", "status": "ignorar"})
    assert r.status_code == 403, r.text
    assert marcas(a) == {}
    r = client.post(POST, json={"chave": "netflix", "status": "ignorar"},
                    headers={dashboard.CSRF_HEADER_NAME: "tok"})
    assert r.status_code == 200, r.text
    assert marcas(a) == {"netflix": "ignorar"}
