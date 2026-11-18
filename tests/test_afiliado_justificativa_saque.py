"""#1610: "Marcar pago" e "Rejeitar" exigem justificativa (10 a 500), a mesma regra das outras ações do admin."""
import pytest

import core.services.afiliado_reembolso as modulo_reembolso
from db.affiliates import request_payout
from test_afiliado_pendencia import _afiliado, _paga, _post, _quadros, _saque, adm, mundo  # noqa: F401

BOA = "  Pix feito por fora hoje  "
RUINS = [{}, {"note": "texto longo e valido de observacao"}, {"justification": "curta"},
         {"justification": " " * 20}, {"justification": "a" * 501}, {"justification": 42}]


def _um_saque():
    c = _afiliado()
    _paga(c)
    return c, request_payout(c.aff["id"])["id"]


@pytest.mark.parametrize("acao", ["paid", "reject"])
@pytest.mark.parametrize("corpo", RUINS)
def test_justificativa_invalida_422_nada_muda(adm, monkeypatch, acao, corpo):
    c, saque = _um_saque()
    antes = _quadros(c)

    def stripe(*a, **k):
        raise AssertionError("conferiu a Stripe antes de validar")

    monkeypatch.setattr(modulo_reembolso, "conferir_saque", stripe)
    r = _post(adm, f"payouts/{saque}/{acao}", corpo)
    assert r.status_code == 422, r.text
    assert _saque(saque)["status"] == "requested" and _saque(saque)["note"] is None
    assert _quadros(c) == antes


@pytest.mark.parametrize("acao,status", [("paid", "paid"), ("reject", "rejected")])
def test_justificativa_valida_grava_limpa_na_nota(adm, acao, status):
    _, saque = _um_saque()
    r = _post(adm, f"payouts/{saque}/{acao}", {"justification": BOA})
    assert r.status_code == 200, r.text
    assert (_saque(saque)["status"], _saque(saque)["note"]) == (status, BOA.strip())


def test_log_do_reject_nao_leva_texto_livre(adm, monkeypatch):
    import core.admin_dashboard as painel

    eventos = []

    async def captura(*a, **k):
        eventos.append(k.get("details"))

    monkeypatch.setattr(painel, "log_system_event", captura)
    _, saque = _um_saque()
    assert _post(adm, f"payouts/{saque}/reject", {"justification": BOA}).status_code == 200
    assert eventos == [{"payout_id": saque}]
