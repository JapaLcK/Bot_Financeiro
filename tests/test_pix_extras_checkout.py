"""Cadernos extras no Pix anual, PR B (emitir com os cadernos) — banco real.

O que é falso é só o mundo lá fora: o Asaas (`asaas_falso`, que conta e guarda o
valor de cada cobrança) e o `stripe.Price.retrieve` (`loja`). A oferta passa pela
`extras_assinar.ofertas_da_pagina` DE VERDADE — a mesma fonte do cartão.

Controles negativos (rodados — ver o relato do PR):
  · `escolher` sem o `raise` de id fora da oferta          → o 409 fica vermelho;
  · matcher antigo (sem comparar o conjunto de ids)          → troca de conjunto vermelha;
  · Asaas cobrando `amount_cents` em vez de `total_cents`    → venda e upgrade vermelhos;
  · `selecao` sem `qr_vivo`                                   → GET de cobrança vencida vermelho;
  · `_ofertas` sem `pagina_propria_ligada`                    → flag desligada vermelha;
  · `escolher` consultando o Stripe sem ids                   → o positivo do Stripe fora vermelho;
  · `conferir_entrega` sem conferir o preço na foto           → lacuna do PR A vermelha.
  Positivos: a venda sem cadernos sai com o Stripe fora (e sem consultá-lo); o
  mesmo conjunto em outra ordem reaproveita o QR.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.billing_pix as rotas
from _billing_grants_helpers import conta
from _pix_checkout_helpers import (  # noqa: F401 - fixtures
    PRECO,
    _comprar,
    _linhas,
    _marcar_paga,
    asaas_falso,
    vendavel,
)
from core.services.pix_checkout import CheckoutIndisponivel
from core.services.pix_extras import ExtrasIndisponiveis
from db.connection import get_conn
from test_billing_checkout import _extras_env

_CAT = {"price_ca": 1990, "price_cb": 2990, "price_cc": 990}
_CPF = "52998224725"   # estruturalmente válido (o mesmo do test_pix_409_contrato.py)

client = TestClient(dashboard.app)


@pytest.fixture()
def loja(monkeypatch):
    """Três cadernos na env, Stripe configurado, flag da página própria ligada."""
    import stripe

    _extras_env(monkeypatch, {n: (p, f"https://pdf.test/{p}") for n, p in enumerate(_CAT, 1)})
    monkeypatch.setenv("CHECKOUT_PAGINA_PROPRIA", "1")
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_pix")
    estado = {"retrieves": [], "falha": None}

    def _retrieve(preco, expand=None):
        estado["retrieves"].append(preco)
        if estado["falha"]:
            raise estado["falha"]
        return {"id": preco, "active": True, "currency": "brl", "type": "one_time",
                "unit_amount": _CAT[preco], "product": {
                    "active": True, "name": f"Caderno {preco}", "description": "d", "images": []}}

    monkeypatch.setattr(stripe.Price, "retrieve", _retrieve)
    return estado


def _csrf() -> dict[str, str]:
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "test-csrf-token")
    return {dashboard.CSRF_HEADER_NAME: "test-csrf-token"}


def _post(uid, monkeypatch, **corpo):
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req: uid)
    return client.post("/billing/pix/checkout", headers=_csrf(),
                       json={"plan": "pro", "cpf_cnpj": _CPF, **corpo})


def _vencer(charge_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pix_charges set qr_expires_at = now() - interval '1 minute'"
                    " where id = %s", (charge_id,))
        conn.commit()


# ── a venda ─────────────────────────────────────────────────────────────────

def test_asaas_cobra_plano_mais_cadernos_e_grava_a_foto(user_id, vendavel, asaas_falso, loja):
    conta(user_id, "free", None)
    r = _comprar(user_id, extras=["price_cb", "price_ca"])
    assert asaas_falso["valores"] == [PRECO + 1990 + 2990]
    assert r["total_cents"] == PRECO + 1990 + 2990 and r["amount_cents"] == PRECO
    linha, = _linhas(user_id)
    assert linha["amount_cents"] == PRECO, "caderno entrou em amount_cents"
    assert linha["extras"] == [
        {"price": "price_ca", "url": "https://pdf.test/price_ca", "nome": "Caderno price_ca",
         "valor_cents": 1990},
        {"price": "price_cb", "url": "https://pdf.test/price_cb", "nome": "Caderno price_cb",
         "valor_cents": 2990}]
    assert asaas_falso["descricoes"][-1].endswith(" - plano anual + 2 cadernos")


def test_um_caderno_no_singular_e_sem_caderno_a_descricao_de_antes(user_id, vendavel,
                                                                   asaas_falso, loja):
    conta(user_id, "free", None)
    _comprar(user_id, extras=["price_cc"])
    assert asaas_falso["descricoes"][-1].endswith(" - plano anual + 1 caderno")
    _comprar(user_id, extras=[])
    assert asaas_falso["descricoes"][-1].endswith(" - plano anual")


def test_id_fora_da_oferta_e_409_sem_escrever_nem_chamar_o_asaas(user_id, vendavel,
                                                                 asaas_falso, loja, monkeypatch):
    conta(user_id, "free", None)
    r = _post(user_id, monkeypatch, extras=["price_ca", "price_zz"])
    assert r.status_code == 409, r.text
    detalhe = r.json()["detail"]
    assert detalhe["error"] == "extras_indisponiveis"
    assert [e["price"] for e in detalhe["extras"]] == list(_CAT)
    assert not [e for e in detalhe["extras"] if "url" in e], "a URL do PDF saiu na oferta"
    assert _linhas(user_id) == [] and asaas_falso["ordem"] == []


@pytest.mark.parametrize("extras", [["price_ca", "price_ca"],
                                    ["price_ca", "price_cb", "price_cc", "price_zz"]],
                         ids=["repetido", "mais-de-3"])
def test_repetido_ou_mais_de_3_e_400(user_id, vendavel, asaas_falso, loja, monkeypatch, extras):
    conta(user_id, "free", None)
    assert _post(user_id, monkeypatch, extras=extras).status_code == 400
    assert _linhas(user_id) == [] and asaas_falso["ordem"] == [] and loja["retrieves"] == []


def test_o_valor_vem_do_price_mesmo_com_o_corpo_trazendo_valor(user_id, vendavel, asaas_falso,
                                                               loja, monkeypatch):
    conta(user_id, "free", None)
    r = _post(user_id, monkeypatch, extras=["price_ca"], valor_cents=1, amount_cents=1,
              price="price_ca")
    assert r.status_code == 200, r.text
    assert asaas_falso["valores"] == [PRECO + 1990]
    item = _post(user_id, monkeypatch, extras=[{"price": "price_ca", "valor_cents": 1}])
    assert item.status_code == 422, "objeto no lugar do id"


def test_stripe_fora_com_cadernos_e_409_sem_cadernos_vende(user_id, vendavel, asaas_falso, loja):
    import stripe

    conta(user_id, "free", None)
    loja["falha"] = stripe.error.APIConnectionError("fora")
    with pytest.raises(ExtrasIndisponiveis) as exc:
        _comprar(user_id, extras=["price_ca"])
    assert exc.value.oferta == [] and _linhas(user_id) == [] and asaas_falso["ordem"] == []
    loja["retrieves"].clear()
    # POSITIVO: o plano vende com o Stripe fora, e sem cadernos ele nem é consultado.
    r = _comprar(user_id)
    assert r["total_cents"] == PRECO and loja["retrieves"] == []
    assert asaas_falso["valores"] == [PRECO]


# ── reaproveitar × substituir ───────────────────────────────────────────────

def test_mesmo_conjunto_em_outra_ordem_devolve_o_mesmo_qr(user_id, vendavel, asaas_falso, loja):
    conta(user_id, "free", None)
    primeira = _comprar(user_id, extras=["price_ca", "price_cb"])
    asaas_falso["ordem"].clear()
    segunda = _comprar(user_id, extras=["price_cb", "price_ca"])
    assert segunda["public_token"] == primeira["public_token"]
    assert segunda["total_cents"] == PRECO + 1990 + 2990
    assert asaas_falso["ordem"] == [] and len(_linhas(user_id)) == 1


def test_acrescentar_ou_tirar_caderno_cancela_e_cria(user_id, vendavel, asaas_falso, loja):
    """DISCRIMINA. Com o matcher antigo (só plano), o QR velho voltava cobrando o
    valor do conjunto anterior."""
    conta(user_id, "free", None)
    primeira = _comprar(user_id, extras=["price_ca"])
    asaas_falso["ordem"].clear()
    mais = _comprar(user_id, extras=["price_ca", "price_cb"])
    assert mais["public_token"] != primeira["public_token"]
    assert asaas_falso["ordem"] == [f"delete:pay_{asaas_falso['marca']}_1", "customer", "create", "qr"]
    assert asaas_falso["valores"][-1] == PRECO + 1990 + 2990
    menos = _comprar(user_id, extras=[])
    assert menos["public_token"] != mais["public_token"]
    assert asaas_falso["valores"][-1] == PRECO and menos["total_cents"] == PRECO
    assert [l["status"] for l in _linhas(user_id)] == ["canceled", "canceled", "pending"]


def test_delete_que_falha_na_troca_de_conjunto_nao_cria_nada(user_id, vendavel, asaas_falso, loja):
    conta(user_id, "free", None)
    _comprar(user_id, extras=["price_ca"])
    asaas_falso["delete_falha"] = True
    with pytest.raises(CheckoutIndisponivel):
        _comprar(user_id, extras=["price_ca", "price_cb"])
    linha, = _linhas(user_id)
    assert linha["status"] == "canceling" and len(asaas_falso["valores"]) == 1


def test_creating_ambigua_com_troca_de_conjunto_deleta_a_remota(user_id, vendavel,
                                                                asaas_falso, loja):
    conta(user_id, "free", None)
    asaas_falso["qr_falha"] = True
    with pytest.raises(CheckoutIndisponivel):
        _comprar(user_id, extras=["price_ca"])
    asaas_falso["qr_falha"] = False
    orfa = f"pay_{asaas_falso['marca']}_1"
    asaas_falso["remotas"] = [{"id": orfa, "status": "PENDING"}]
    asaas_falso["ordem"].clear()
    _comprar(user_id, extras=["price_cb"])
    assert asaas_falso["ordem"].index(f"delete:{orfa}") < asaas_falso["ordem"].index("create")
    assert asaas_falso["valores"][-1] == PRECO + 2990
    assert [l["status"] for l in _linhas(user_id)] == ["canceled", "pending"]


def test_qr_vencido_com_o_mesmo_conjunto_substitui(user_id, vendavel, asaas_falso, loja):
    conta(user_id, "free", None)
    primeira = _comprar(user_id, extras=["price_ca"])
    _vencer(_linhas(user_id)[0]["id"])
    segunda = _comprar(user_id, extras=["price_ca"])
    assert segunda["public_token"] != primeira["public_token"]
    assert asaas_falso["valores"] == [PRECO + 1990, PRECO + 1990]


def test_upgrade_com_credito_e_cadernos(user_id, vendavel, asaas_falso, loja):
    """Crédito só sobre o plano (`amount = preço − crédito`); o Asaas cobra
    `amount + cadernos`."""
    conta(user_id, "free", None)
    plus = _comprar(user_id, "pro")
    _marcar_paga(user_id, plus["public_token"], dias=-1)
    r = _comprar(user_id, "pro_max", extras=["price_ca", "price_cb"])
    linha = _linhas(user_id)[-1]
    assert linha["credit_cents"] > 0
    assert linha["amount_cents"] == PRECO - linha["credit_cents"]
    assert asaas_falso["valores"][-1] == linha["amount_cents"] + 1990 + 2990 == r["total_cents"]


# ── GET /billing/pix-extras ─────────────────────────────────────────────────

def test_get_exige_login():
    assert TestClient(dashboard.app).get("/billing/pix-extras").status_code == 401


def test_get_devolve_a_oferta_e_a_selecao_pendente_so_do_dono(user_id, vendavel, asaas_falso,
                                                             loja, monkeypatch):
    conta(user_id, "free", None)
    _comprar(user_id, extras=["price_cb", "price_ca"])
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req: user_id)
    corpo = client.get("/billing/pix-extras").json()
    assert [(e["price"], e["valor_centavos"]) for e in corpo["extras"]] == list(_CAT.items())
    assert not [e for e in corpo["extras"] if "url" in e]
    assert sorted(corpo["selecao"]) == ["price_ca", "price_cb"]
    # Isolamento (CLAUDE.md §0): outro usuário não vê a seleção deste.
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req: user_id + 10_000_000)
    assert client.get("/billing/pix-extras").json()["selecao"] == []


def test_get_de_cobranca_vencida_nao_marca_nada(user_id, vendavel, asaas_falso, loja, monkeypatch):
    conta(user_id, "free", None)
    _comprar(user_id, extras=["price_ca"])
    _vencer(_linhas(user_id)[0]["id"])
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req: user_id)
    assert client.get("/billing/pix-extras").json()["selecao"] == []


@pytest.mark.parametrize("env", ["CHECKOUT_PAGINA_PROPRIA", "ASAAS_PIX_ANNUAL_ENABLED"])
def test_flag_desligada_oferta_vazia(user_id, vendavel, asaas_falso, loja, monkeypatch, env):
    conta(user_id, "free", None)
    monkeypatch.setenv(env, "")
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req: user_id)
    assert client.get("/billing/pix-extras").json()["extras"] == []
    assert loja["retrieves"] == []


def test_sem_stripe_configurado_oferta_vazia_sem_consultar(user_id, vendavel, asaas_falso, loja,
                                                          monkeypatch):
    """Positivo antes: a mesma loja COM a chave tem as 3 caixas — o vazio é da chave, não da fixture."""
    conta(user_id, "free", None)
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id", lambda req: user_id)
    assert len(client.get("/billing/pix-extras").json()["extras"]) == 3
    loja["retrieves"].clear()
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "")
    assert client.get("/billing/pix-extras").json()["extras"] == []
    assert loja["retrieves"] == []


def test_pagina_propria_desligada_post_com_cadernos_e_409(user_id, vendavel, asaas_falso,
                                                          loja, monkeypatch):
    conta(user_id, "free", None)
    monkeypatch.setenv("CHECKOUT_PAGINA_PROPRIA", "")
    r = _post(user_id, monkeypatch, extras=["price_ca"])
    assert r.status_code == 409 and r.json()["detail"]["extras"] == []
    assert _linhas(user_id) == [] and asaas_falso["ordem"] == []


# ── ponta a ponta: PR B emite, PR A recebe e grava ──────────────────────────

def test_post_com_cadernos_ate_o_received_grava_as_entregas(user_id, vendavel, asaas_falso,
                                                            loja, monkeypatch):
    from _dreno_pix_helpers import entregar, mundo_externo

    externo = mundo_externo(monkeypatch)
    conta(user_id, "free", None)
    assert _post(user_id, monkeypatch, extras=["price_ca", "price_cb"]).status_code == 200
    linha, = _linhas(user_id)
    entregar("PAYMENT_RECEIVED", linha, valor=asaas_falso["valores"][0] / 100)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("select session_id, ebook_price, ebook_url from ebook_entregas"
                    " where user_id = %s order by ebook_price", (user_id,))
        entregas = [tuple(r.values()) for r in cur.fetchall()]
    ref = linha["external_reference"]
    assert entregas == [(ref, "price_ca", "https://pdf.test/price_ca"),
                        (ref, "price_cb", "https://pdf.test/price_cb")]
    assert externo["alerta"] == [], "o total cobrado pelo QR alertou valor divergente"


# ── lacuna do PR A ──────────────────────────────────────────────────────────

def test_conferir_entrega_preco_fora_da_foto_e_nao_comprou_sem_asaas(user_id, monkeypatch):
    import core.services.asaas as asaas
    from _dreno_pix_helpers import nova_cobranca
    from core.services.pix_extras import conferir_entrega

    chamadas = []
    monkeypatch.setattr(asaas, "buscar_pagamento", lambda pid: chamadas.append(pid) or {
        "status": "RECEIVED", "refunds": None})
    conta(user_id, "free", None)
    cob = nova_cobranca(user_id, extras=[{"price": "price_ca", "url": "https://pdf.test/a",
                                          "nome": "A", "valor_cents": 1990}])
    assert conferir_entrega(user_id, cob["external_reference"], "price_outro") == ("nao_comprou", None)
    assert chamadas == []
    # POSITIVO: o preço que está na foto passa para o Asaas.
    assert conferir_entrega(user_id, cob["external_reference"], "price_ca") == ("pago", "A")
