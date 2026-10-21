"""PR-A2b-2: a borda HTTP do "foi em dinheiro" (`POST /open-finance/{uid}/movements/cash`, `GET .../movements`) e a
conversa de ponta a ponta pelo `handle_incoming` (H15), com estado real.

Controles (rodados numa CÓPIA):
- H16: tirar `shared.authorize_dashboard_access(request, user_id)` da rota nova -> VERMELHO `test_sem_cookie_nao_acessa`
  e `test_sessao_de_outro_usuario_no_caminho`. Positivo: `test_rota_invalida_o_cache_do_dashboard_so_quando_a_acao_valeu`.
- H16 isolamento: tirar o `user_id = %s` de `candidatos_carteira`, do `for update` de C E do CAS do marcador -> VERMELHO
  `test_declaracao_de_b_com_c_de_a_e_409` (só as duas primeiras fora segue verde: o CAS segura).
- H15: a conversa vale pelo que o isolado não vê: `apagar #N` depois da seleção responde a frase de hoje (DP-9) e a
  Carteira volta; sem o `delete from launches` da declaração (a #N continuaria existindo) -> VERMELHO
  `test_h15_conversa_selecionar_e_apagar`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from frontend.routes import shared
from tests._fusao_of_helpers import conecta_banco, ia_fora, manda, saldo_bruto, soma_delta_conta, ultimo_launch, uid_pro  # noqa: F401
from tests._patrimonio_helpers import q
from tests._retrato_declaracao import em, retrato
from tests.test_declaracao_a2b2_dinheiro import ana2, dinheiro, lanca, outro  # noqa: F401 (fixtures)
from tests.test_declaracao_a2b_acoes import H, cliente
from tests.test_declaracao_fluxo_reconcilia import declara

INEXISTENTE = 10**12


@pytest.mark.parametrize("body", [
    {}, {"carteira_launch_id": 1}, {"launch_id": "x"}, {"launch_id": -1}, {"launch_id": 10**30},
    {"launch_id": 1.5}, {"launch_id": None}, {"launch_id": [1]}, {"launch_id": INEXISTENTE},
    {"launch_id": INEXISTENTE, "carteira_launch_id": "a"}, {"launch_id": INEXISTENTE, "carteira_launch_id": 10**30},
    {"launch_id": INEXISTENTE, "carteira_launch_id": -1}, {"launch_id": True, "carteira_launch_id": True},
    {"launch_id": INEXISTENTE, "carteira_launch_id": [1]},
])
def test_entrada_invalida_nunca_500(user_id, body):
    cid, d, c40, c = ana2(user_id)
    antes = retrato(user_id)
    r = c.post(f"/open-finance/{user_id}/movements/cash", json=body, headers=H)
    assert r.status_code in (404, 409, 422), (r.status_code, r.text)
    assert retrato(user_id) == antes


def test_corpo_nao_json(user_id):
    cid, d, c40, c = ana2(user_id)
    r = c.post(f"/open-finance/{user_id}/movements/cash", content=b"not json", headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 422


def test_sem_cookie_nao_acessa(user_id):
    cid, d, c40, c = ana2(user_id)
    antes = retrato(user_id)
    anon = TestClient(dashboard.app, raise_server_exceptions=False)
    assert anon.post(f"/open-finance/{user_id}/movements/cash", json={"launch_id": d}, headers=H).status_code in (401, 403)
    assert retrato(user_id) == antes


def test_sem_csrf_e_403(user_id):
    cid, d, c40, c = ana2(user_id)
    antes = retrato(user_id)
    assert c.post(f"/open-finance/{user_id}/movements/cash", json={"launch_id": d}).status_code == 403
    assert retrato(user_id) == antes


def test_sessao_de_outro_usuario_no_caminho(user_id, outro):
    cid, d, c40, _ = ana2(user_id)
    antes = retrato(user_id)
    r = cliente(outro).post(f"/open-finance/{user_id}/movements/cash", json={"launch_id": d}, headers=H)
    assert r.status_code in (401, 403), r.status_code  # não 404: a sessão não é dona do caminho
    assert retrato(user_id) == antes


def test_declaracao_de_a_com_a_sessao_de_b_e_404(user_id, outro):
    cid, d, c40, _ = ana2(user_id)
    antes_a, antes_b = retrato(user_id), retrato(outro)
    assert dinheiro(cliente(outro), outro, d).status_code == 404
    assert dinheiro(cliente(outro), outro, d, c40).status_code == 404
    assert retrato(user_id) == antes_a and retrato(outro) == antes_b


def test_declaracao_de_b_com_c_de_a_e_409(user_id, outro):
    cid, d, c40, _ = ana2(user_id)
    db_b = declara(outro, criado_em=em(0))
    antes_a, antes_b = retrato(user_id), retrato(outro)
    assert dinheiro(cliente(outro), outro, db_b, c40).status_code == 409
    assert retrato(user_id) == antes_a and retrato(outro) == antes_b


def test_get_devolve_cash_e_saldo_so_do_proprio_usuario(user_id, outro):
    cid, d, c40, c = ana2(user_id)
    lanca(outro)  # a mesma forma de lançamento, de outro dono, não aparece na lista de A
    lista = c.get(f"/open-finance/{user_id}/movements").json()
    assert [x["id"] for x in lista["movements"][0]["cash"]] == [c40] and lista["wallet_balance"] == 950.0
    assert lista["movements"][0]["cash"][0].keys() == {"id", "name", "category", "amount", "date"}  # sem nota nem #N
    assert cliente(outro).get(f"/open-finance/{outro}/movements").json() == {"ok": True, "movements": [], "wallet_balance": -50.0}


def test_rota_invalida_o_cache_do_dashboard_so_quando_a_acao_valeu(user_id, monkeypatch):
    cid, d, c40, c = ana2(user_id)
    chamadas = []
    monkeypatch.setattr(shared, "invalidate_dashboard_current_cache", chamadas.append)
    assert dinheiro(c, user_id, d, INEXISTENTE).status_code == 409
    assert dinheiro(c, user_id, INEXISTENTE).status_code == 404
    assert chamadas == []
    assert dinheiro(c, user_id, d, c40).status_code == 200
    assert dinheiro(c, user_id, d, c40).status_code == 404
    assert chamadas == [user_id]


def test_h15_conversa_selecionar_e_apagar(uid_pro, ia_fora):
    """Conversa de verdade: o usuário lança em dinheiro pelo WhatsApp, o /app associa a declaração a esse
    lançamento, "apagar #N" da declaração responde a frase de hoje e "apagar" do lançamento devolve a Carteira."""
    uid = uid_pro
    conecta_banco(uid, "1000.00")
    d = declara(uid, criado_em=em(0), nota="compras da semana")
    n_decl = q("select user_seq from launches where id=%s", (d,))["user_seq"]
    manda(uid, "gastei 50 no mercado em dinheiro")
    c40 = ultimo_launch(uid)
    n40 = q("select user_seq from launches where id=%s", (c40,))["user_seq"]
    assert saldo_bruto(uid) == soma_delta_conta(uid) == -50
    c = cliente(uid)
    assert [x["id"] for x in c.get(f"/open-finance/{uid}/movements").json()["movements"][0]["cash"]] == [c40]
    assert dinheiro(c, uid, d, c40).status_code == 200
    assert saldo_bruto(uid) == soma_delta_conta(uid) == -50  # sem novo débito
    antes = retrato(uid)
    assert "Não encontrei o lançamento" in manda(uid, f"apagar #{n_decl}")  # DP-9: a frase de hoje
    assert retrato(uid) == antes
    assert "sim" in manda(uid, f"apagar #{n40}").lower()
    assert "apagado" in manda(uid, "sim").lower()
    assert saldo_bruto(uid) == soma_delta_conta(uid) == 0
    assert q("select count(*) n from launches where user_id=%s and efeitos ? 'foi_em_dinheiro'", (uid,))["n"] == 0


def test_h15_conversa_converter_e_apagar(uid_pro, ia_fora):
    uid = uid_pro
    conecta_banco(uid, "1000.00")
    d = declara(uid, criado_em=em(0))
    n = q("select user_seq from launches where id=%s", (d,))["user_seq"]
    assert saldo_bruto(uid) == 0
    assert dinheiro(cliente(uid), uid, d).status_code == 200
    assert saldo_bruto(uid) == soma_delta_conta(uid) == -50
    assert "sim" in manda(uid, f"apagar #{n}").lower()  # a convertida mantém o #N: o apagar de hoje a encontra
    assert "apagado" in manda(uid, "sim").lower()
    assert saldo_bruto(uid) == soma_delta_conta(uid) == 0
    assert q("select count(*) n from launches where user_id=%s", (uid,))["n"] == 0
    assert q("select count(*) n from bank_movement_declarations where user_id=%s", (uid,))["n"] == 0
