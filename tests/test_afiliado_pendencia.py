"""Pendência de reembolso do afiliado: bloqueio, revisão, recuperação e aviso ao admin.

Rota real, Postgres real, e `_send` real pela fixture `canal` (`requests.post` falso). Exemplo do
dono: comissão A de R$ 100 reembolsada em parte e comissão B de R$ 70 intacta.

Os controles negativos e positivos (§3) estão no relato do PR; os positivos ficam aqui, nos casos
que dizem "positivo".
"""
import json
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import core.services.afiliado_reembolso as modulo_reembolso
import db
import db.affiliate_reviews as reviews
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import csrf
from _lifespan_probe import sondar
from core.services import admin_notify
from core.services.afiliado_reembolso import reenviar_pendentes, tratar_evento
from db.affiliate_reviews import PendenciaMudou, reivindicar, registrar_reembolso, resolver_parcial
from db.affiliates import (
    SaqueComPendencia,
    get_affiliate_stats,
    list_affiliate_commissions,
    mark_payout_paid,
    record_commission_for_invoice,
    registrar_pagamento_realizado,
    reject_payout,
    request_payout,
)
from db.connection import get_conn
from test_afiliado_area import ME, _afiliado, _comissao, _esperando_lock, _me, _painel_aberto, _sacar  # noqa: F401
from test_afiliado_pagantes import AGORA, _admin, _indicado
from test_afiliado_reembolso import (  # noqa: F401
    _cliente,
    _cobranca,
    _fatura,
    _enviar,
    _evento,
    _logs,
    _paga,
    _segura,
    _status,
    mundo,
    script,
)

REAL = admin_notify._send
URL = "https://discord.com/api/webhooks/1/x"
FUSO = ZoneInfo("America/Sao_Paulo")
OVERVIEW = "/admin/api/affiliates"


def _hoje(dias=0):
    return (datetime.now(FUSO).date() + timedelta(days=dias)).isoformat()


@pytest.fixture
def canal(mundo, monkeypatch):
    """O `_send` de verdade. `status` e `erro` dizem o que o webhook do admin responde."""
    c = SimpleNamespace(status=204, erro=None, enviados=[])

    def post(url, json, timeout):
        if c.erro:
            raise c.erro
        c.enviados.append(json["content"])
        return SimpleNamespace(status_code=c.status, text="")

    monkeypatch.setattr(admin_notify, "_send", REAL)
    monkeypatch.setattr(requests, "post", post)
    return c


@pytest.fixture
def adm(mundo, monkeypatch):
    return _admin(monkeypatch)


def _rev(fatura):
    with get_conn() as conn:
        return conn.execute("select * from affiliate_refund_reviews where stripe_invoice_id = %s", (fatura,)).fetchone()


def _eventos(fatura):
    with get_conn() as conn:
        return conn.execute("select e.* from affiliate_refund_review_events e join affiliate_refund_reviews r"
                            " on r.id = e.review_id where r.stripe_invoice_id = %s order by e.id", (fatura,)).fetchall()


def _acoes(fatura):
    return [e["action"] for e in _eventos(fatura)]


def _saque(saque):
    with get_conn() as conn:
        return conn.execute("select status, amount_cents, note, paid_at from affiliate_payouts where id = %s",
                            (saque,)).fetchone()


def _comissao_db(com):
    with get_conn() as conn:
        return conn.execute("select * from affiliate_commissions where id = %s", (com.id,)).fetchone()


def _parcial(com, devolvido=30_000):
    assert _enviar("charge.refunded", _cobranca(com.fatura, devolvido=devolvido)).status_code == 200


def _total(com):
    assert _enviar("charge.refunded", _cobranca(com.fatura)).status_code == 200


def _quadros(c):
    s = get_affiliate_stats(c.aff["id"])
    return {k: s[f"{k}_cents"] // 100 for k in ("held", "available", "review", "requested", "paid", "generated")}


def _post(adm, caminho, corpo=None):
    """Corpo já serializado: o `json=` do cliente não codifica surrogate solitário, e o servidor recebe o escape."""
    return adm.post(f"/admin/api/affiliates/{caminho}", headers={**csrf(adm), "content-type": "application/json"},
                    content=json.dumps(corpo or {}).encode())


def _pagar(adm, saque):
    return _post(adm, f"payouts/{saque}/paid")


def _rejeitar(adm, saque):
    return _post(adm, f"payouts/{saque}/reject", {"note": "reembolso"})


def _aprovar(adm, com, cents, nota="Ajuste pelo valor que ficou com o cliente"):
    r = _rev(com.fatura)
    return _post(adm, f"refund-reviews/{r['id']}/approve",
                 {"approved_cents": cents, "refunded_cents": r["refunded_cents"], "justification": nota})


def _realizado(adm, saque, **campos):
    corpo = {"justification": "Pix feito antes do reembolso", "paid_on": _hoje(), "receipt_ref": "E1234567", **campos}
    return _post(adm, f"payouts/{saque}/payment-recorded", corpo)


def _encerrar(adm, com, resultado="recovered", **campos):
    corpo = {"result": resultado, "justification": "Afiliado devolveu por Pix"}
    if resultado == "recovered":
        corpo |= {"recovered_on": _hoje(), "receipt_ref": "E7654321"}
    return _post(adm, f"refund-reviews/{_rev(com.fatura)['id']}/close-recovery", {**corpo, **campos})


def _dois_com_saque(c):
    a, b = _paga(c), _paga(c, 70)
    return a, b, request_payout(c.aff["id"])["id"]


def _paga_e_saca(c, reais=100):
    com = _paga(c, reais)
    saque = request_payout(c.aff["id"])["id"]
    assert mark_payout_paid(saque)
    return com, saque


# ── Reprodução (D7) ──────────────────────────────────────────────────────────

def test_exclusao_nao_libera_saque_com_reembolso(canal, adm):
    """Comissão em saque, indicado exclui a conta, reembolso total, admin tenta concluir o saque."""
    c = _afiliado()
    com = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    db.delete_user_data(com.indicado.uid)
    objeto = _cobranca(com.fatura)
    for _ in range(3):
        assert _enviar("charge.refunded", objeto).status_code == 200
    r = _pagar(adm, saque)
    assert r.status_code == 409, r.text
    assert _saque(saque)["status"] == "requested"
    assert _rev(com.fatura)["status"] == "open" and len(canal.enviados) == 1


# ── "Em revisão" (D1) ────────────────────────────────────────────────────────

def test_parcial_fica_fora_do_saque(mundo):
    c = _afiliado()
    a, b = _paga(c), _paga(c, 70)
    _parcial(a)
    assert _quadros(c) == {"held": 0, "available": 70, "review": 100, "requested": 0, "paid": 0, "generated": 170}
    assert request_payout(c.aff["id"])["amount_cents"] == 70_00
    assert _quadros(c) == {"held": 0, "available": 0, "review": 100, "requested": 70, "paid": 0, "generated": 170}
    assert _status(a) == {"status": "pending", "payout_id": None}


def _corrida(primeiro, segundo, com):
    """`primeiro` e depois `segundo` param na trava da comissão, nesta ordem, e saem juntos."""
    with ThreadPoolExecutor(2) as pool, get_conn() as segura:
        _segura(segura, com)
        try:
            f1 = pool.submit(primeiro)
            assert _esperando_lock(1)
            f2 = pool.submit(segundo)
            assert _esperando_lock(2)
        finally:
            segura.commit()
        return f1.result(timeout=15), f2.result(timeout=15)


def _evento_parcial(com, devolvido=30_000):
    evento = _evento(com.fatura)
    evento["data"]["object"]["amount_refunded"] = devolvido
    return evento


def test_pedido_x_webhook_parcial(mundo):
    """O webhook parcial não altera a linha da comissão: só a segunda leitura o enxerga."""
    c = _afiliado()
    a, _ = _paga(c), _paga(c, 70)
    evento = _evento_parcial(a)
    _, pedido = _corrida(lambda: tratar_evento(evento), lambda: request_payout(c.aff["id"]), a)
    assert pedido["amount_cents"] == 70_00
    assert _status(a) == {"status": "pending", "payout_id": None}
    assert mark_payout_paid(pedido["id"])


def _em_carencia(c):
    _parcial(_paga(c, disponivel=False))


def _so_revisao(c):
    _parcial(_paga(c))


def _em_saque(c):
    _dois_com_saque(c)


def _saque_bloqueado(c):
    _parcial(_dois_com_saque(c)[0])


def _paga_com_parcial(c):
    _parcial(_paga_e_saca(c)[0])


def _reembolsada_em_saque(c):
    _total(_dois_com_saque(c)[0])


@pytest.mark.parametrize("cenario", [_em_carencia, _so_revisao, _em_saque, _saque_bloqueado, _paga_com_parcial,
                                     _reembolsada_em_saque], ids=lambda f: f.__name__)
def test_generated_e_soma_dos_quadros(mundo, cenario):
    c = _afiliado()
    _paga(c, 40, disponivel=False)
    cenario(c)
    q = _quadros(c)
    assert q["generated"] == q["held"] + q["available"] + q["review"] + q["requested"] + q["paid"]
    assert q["generated"] > 40


@pytest.mark.parametrize("disponivel,quadro", [(False, "held"), (True, "available")], ids=["carencia", "disponivel"])
def test_revisao_volta_ao_quadro_certo(adm, disponivel, quadro):
    c = _afiliado()
    a = _paga(c, disponivel=disponivel)
    _parcial(a)
    assert _quadros(c)["review"] == 100 and _quadros(c)[quadro] == 0
    assert _aprovar(adm, a, 10_000).status_code == 200
    assert _quadros(c)["review"] == 0 and _quadros(c)[quadro] == 100


# ── Revisão com valor aprovado ───────────────────────────────────────────────

def test_valor_aprovado_fora_de_saque(adm):
    c = _afiliado()
    a = _paga(c)
    _parcial(a)
    r = _aprovar(adm, a, 6_000)
    assert r.status_code == 200, r.text
    linha = _comissao_db(a)
    assert (linha["amount_cents"], linha["invoice_amount_cents"]) == (6_000, 100_000)
    rev = _rev(a.fatura)
    assert (rev["status"], rev["amount_cents"], rev["approved_cents"]) == ("resolved", 10_000, 6_000)
    e = _eventos(a.fatura)[-1]
    assert (e["action"], e["before_cents"], e["after_cents"]) == ("partial_approved", 10_000, 6_000)
    assert e["actor"] and e["justification"].startswith("Ajuste") and e["created_at"]
    item = _me(c)["commissions"][0]
    assert (item["amount"], item["original_amount"], item["in_review"]) == (60, 100, False)


def test_valor_aprovado_em_saque_ajusta_o_saque(adm):
    c = _afiliado()
    a, b, saque = _dois_com_saque(c)
    _parcial(a)
    assert _pagar(adm, saque).status_code == 409
    assert _aprovar(adm, a, 6_000).status_code == 200
    assert _saque(saque)["amount_cents"] == 13_000
    assert _comissao_db(a)["amount_cents"] + _comissao_db(b)["amount_cents"] == 13_000
    assert _quadros(c)["generated"] == 130
    assert _pagar(adm, saque).status_code == 200
    assert _quadros(c) == {"held": 0, "available": 0, "review": 0, "requested": 0, "paid": 130, "generated": 130}


def test_parcial_aprovado_cheio_paga_o_valor_original(adm):
    """Positivo: revisão que confirma o valor não mexe em nada e libera."""
    c = _afiliado()
    a, _, saque = _dois_com_saque(c)
    _parcial(a)
    assert _aprovar(adm, a, 10_000).status_code == 200
    assert _pagar(adm, saque).status_code == 200
    assert _saque(saque)["amount_cents"] == 17_000
    assert next(x for x in _me(c)["commissions"] if x["id"] == a.id)["original_amount"] is None


@pytest.mark.parametrize("cenario", ["solta", "saque", "unica"])
def test_aprovado_zero(adm, cenario):
    c = _afiliado()
    a = _paga(c)
    b = _paga(c, 70) if cenario == "saque" else None
    saque = request_payout(c.aff["id"])["id"] if cenario != "solta" else None
    _parcial(a)
    assert _aprovar(adm, a, 0).status_code == 200
    assert _status(a) == {"status": "reversed", "payout_id": None}
    assert _comissao_db(a)["amount_cents"] == 10_000  # fica como estava
    if cenario == "saque":
        assert _saque(saque)["amount_cents"] == 7_000 and _status(b)["payout_id"] == saque
        assert _pagar(adm, saque).status_code == 200
    elif cenario == "unica":
        linha = _saque(saque)
        assert (linha["status"], linha["amount_cents"], linha["note"]) == ("rejected", 10_000, "Comissão ajustada na revisão.")
        assert _quadros(c)["generated"] == 0


@pytest.mark.parametrize("valor", [10_001, -1, True, 1.5, "6000", None, [60]],
                         ids=["acima", "negativo", "true", "decimal", "texto", "nulo", "lista"])
def test_valor_invalido(adm, valor):
    c = _afiliado()
    a = _paga(c)
    _parcial(a)
    r = _aprovar(adm, a, valor)
    assert r.status_code == 422, r.text
    assert _rev(a.fatura)["status"] == "open" and _comissao_db(a)["amount_cents"] == 10_000


def test_valor_velho_nao_fecha_revisao(adm):
    """O admin viu R$ 300 devolvidos, e chegou mais: decidir sobre o velho dá 409."""
    c = _afiliado()
    a = _paga(c)
    _parcial(a)
    rev = _rev(a.fatura)
    _parcial(a, 50_000)
    r = _post(adm, f"refund-reviews/{rev['id']}/approve", {"approved_cents": 6_000, "refunded_cents": 30_000,
                                                          "justification": "Ajuste do valor antigo"})
    assert r.status_code == 409 and _rev(a.fatura)["status"] == "open"


def test_total_nao_fecha_com_nota(adm):
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    _total(a)
    assert _aprovar(adm, a, 10_000).status_code == 409
    assert _pagar(adm, saque).status_code == 409
    assert _rev(a.fatura)["status"] == "open" and _acoes(a.fatura) == []


def test_revisao_exige_justificativa(adm):
    c = _afiliado()
    a = _paga(c)
    _parcial(a)
    for nota in ("", "curta", 123, None):
        assert _aprovar(adm, a, 6_000, nota=nota).status_code == 422
    assert _rev(a.fatura)["status"] == "open" and _comissao_db(a)["amount_cents"] == 10_000


# ── Rejeitar saque ───────────────────────────────────────────────────────────

def test_rejeitar_total_sem_rotulo(adm):
    """Comissão sem linha no registro: quem estorna é a pendência `total`. A outra volta (positivo)."""
    c = _afiliado()
    a = _comissao(c, 100)
    b = _paga(c, 70)
    saque = request_payout(c.aff["id"])["id"]
    _total(a)
    assert _rejeitar(adm, saque).status_code == 200
    assert _status(a) == {"status": "reversed", "payout_id": None}
    assert _status(b) == {"status": "pending", "payout_id": None} and _quadros(c)["available"] == 70
    e = _eventos(a.fatura)[-1]
    assert (e["action"], e["payout_id"]) == ("payout_rejected", saque) and e["actor"]
    assert _rev(a.fatura)["status"] == "resolved"


def test_rejeitar_parcial_volta_para_revisao(adm):
    c = _afiliado()
    a, _, saque = _dois_com_saque(c)
    _parcial(a)
    assert _rejeitar(adm, saque).status_code == 200
    assert _rev(a.fatura)["status"] == "open"
    assert _quadros(c)["review"] == 100 and _quadros(c)["available"] == 70
    assert request_payout(c.aff["id"])["amount_cents"] == 7_000
    assert _quadros(c)["review"] == 100


# ── "Pagamento já realizado" ─────────────────────────────────────────────────

def test_pagamento_realizado(adm):
    c = _afiliado()
    a, b, saque = _dois_com_saque(c)
    _total(a)
    r = _realizado(adm, saque)
    assert r.status_code == 200, r.text
    linha = _saque(saque)
    assert linha["status"] == "paid" and linha["note"] is None
    assert linha["paid_at"].astimezone(FUSO).hour == 12
    assert _status(a)["status"] == _status(b)["status"] == "paid"
    rev = _rev(a.fatura)
    assert (rev["status"], rev["recovery_cents"]) == ("recovery", 10_000)
    e = _eventos(a.fatura)[-1]
    assert (e["action"], e["receipt_ref"], e["payout_id"]) == ("payment_recorded", "E1234567", saque)
    assert _quadros(c) == {"held": 0, "available": 0, "review": 0, "requested": 0, "paid": 170, "generated": 170}
    with pytest.raises(ValueError):
        request_payout(c.aff["id"])


def test_pagamento_realizado_sem_pendencia(adm):
    c = _afiliado()
    _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    assert _realizado(adm, saque).status_code == 409
    assert _saque(saque)["status"] == "requested"
    assert _pagar(adm, saque).status_code == 200  # positivo: saque sem pendência paga


@pytest.mark.parametrize("campos", [
    {"paid_on": _hoje(1)}, {"paid_on": _hoje(-1)}, {"paid_on": "2026-02-30"}, {"paid_on": "05/11/2026"},
    {"paid_on": _hoje().replace("-", "")}, {"paid_on": ""}, {"paid_on": None}, {"paid_on": 20261105}, {"paid_on": "2026-11-05\x00"},
    {"justification": ""}, {"justification": "curta"}, {"justification": "x" * 501}, {"justification": None},
    {"justification": ["texto bem longo aqui"]}, {"justification": 1234567890},
    {"receipt_ref": ""}, {"receipt_ref": "abc"}, {"receipt_ref": "x" * 121}, {"receipt_ref": "E 123 456"},
    {"receipt_ref": "E1234\x00567"}, {"receipt_ref": None}, {"receipt_ref": 12345678},
], ids=lambda campos: repr(campos)[:34])
def test_validacao_pagamento_realizado(adm, campos):
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    _total(a)
    r = _realizado(adm, saque, **campos)
    assert r.status_code == 422, r.text
    assert _saque(saque)["status"] == "requested" and _rev(a.fatura)["status"] == "open"
    valor = next(iter(campos.values()))
    assert not (isinstance(valor, str) and len(valor) > 5 and valor in r.text)  # o erro não ecoa


def test_realizado_aceita_data_anterior_a_hoje_desde_o_pedido(adm):
    """Positivo: Pix de ontem, com o pedido de anteontem."""
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    with get_conn() as conn:
        conn.execute("update affiliate_payouts set requested_at = now() - interval '2 days' where id = %s", (saque,))
        conn.commit()
    _total(a)
    assert _realizado(adm, saque, paid_on=_hoje(-1), receipt_ref="a.b_c:d/e-1").status_code == 200


def test_referencia_nao_vaza(canal, adm, capfd):
    c = _afiliado()
    a, _, saque = _dois_com_saque(c)
    _total(a)
    assert _realizado(adm, saque, receipt_ref="CANARIO-REF", justification="CANARIO-JUST do pagamento").status_code == 200
    assert _encerrar(adm, a, receipt_ref="CANARIO-REC", justification="CANARIO-FIM da recuperacao").status_code == 200
    reenviar_pendentes()
    with get_conn() as conn:
        logs = conn.execute("select * from system_event_logs").fetchall()
    saida = capfd.readouterr()
    lugares = {"logs": str(logs), "stdout": saida.out + saida.err, "nota": str(_saque(saque)),
               "me": TestClient(dashboard.app).get(ME, headers=c.h).text, "aviso": str(canal.enviados)}
    assert {k: v for k, v in lugares.items() if "CANARIO" in v} == {}
    assert _eventos(a.fatura)[-1]["receipt_ref"] == "CANARIO-REC"  # positivo: o canário foi mesmo gravado


# ── Recuperação (D4 e D6) ────────────────────────────────────────────────────

def test_paga_antes_do_total_cai_em_recuperacao(canal, adm):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    _total(a)
    rev = _rev(a.fatura)
    assert (rev["status"], rev["recovery_cents"]) == ("recovery", 10_000)
    assert _acoes(a.fatura) == ["paid_before_refund"]
    assert len(canal.enviados) == 1 and "recuperação pendente" in canal.enviados[0]
    outra = _paga(c, 70)  # o saque seguinte não fica preso
    saque = request_payout(c.aff["id"])["id"]
    assert _pagar(adm, saque).status_code == 200 and _status(outra)["status"] == "paid"
    item = next(x for x in _me(c)["commissions"] if x["id"] == a.id)
    assert (item["status"], item["payment_refund"], item["original_amount"]) == ("paid", "total", None)


def test_recuperacao_diferenca_paga(adm):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    objeto = _cobranca(a.fatura, devolvido=30_000)
    assert _enviar("charge.refunded", objeto).status_code == 200
    assert _aprovar(adm, a, 6_000).status_code == 200
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 4_000)
    assert _comissao_db(a)["amount_cents"] == 10_000 and _quadros(c)["paid"] == 100
    assert _enviar("charge.refunded", objeto).status_code == 200  # reentrega: atribuído, não somado
    assert _rev(a.fatura)["recovery_cents"] == 4_000
    assert _encerrar(adm, a).status_code == 200
    _total(a)
    rev = _rev(a.fatura)
    assert (rev["status"], rev["recovery_cents"], rev["recovery_closed_cents"]) == ("recovery", 6_000, 4_000)


def test_realizado_em_parcial(adm):
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    _parcial(a)
    assert _realizado(adm, saque).status_code == 200
    assert _status(a)["status"] == "paid" and _rev(a.fatura)["status"] == "open"
    assert _aprovar(adm, a, 6_000).status_code == 200
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 4_000)
    assert _acoes(a.fatura) == ["payment_recorded", "partial_approved"]


@pytest.mark.parametrize("resultado", ["recovered", "waived"])
def test_encerrar_recuperacao(adm, resultado):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    _total(a)
    antes = _quadros(c)
    visto = adm.get(OVERVIEW).json()
    minha = [r for r in visto["refund_recoveries"] if r["commission_id"] == a.id]
    assert len(minha) == 1 and minha[0]["origem"] == "paid_before_refund"
    assert _encerrar(adm, a, resultado).status_code == 200
    assert _quadros(c) == antes
    visto = adm.get(OVERVIEW).json()
    assert a.id not in [r["commission_id"] for r in visto["refund_recoveries"]]
    fim = next(e for e in visto["refund_history"] if e["commission_id"] == a.id)
    assert fim["action"] == f"recovery_{resultado}" and fim["actor"] and fim["before_cents"] == 10_000
    rev = _rev(a.fatura)
    assert (rev["status"], rev["recovery_closed_cents"], rev["recovery_cents"]) == ("resolved", 10_000, None)


COMPROVANTE = {"recovered_on": _hoje(), "receipt_ref": "E7654321"}  # válido: quem recusa é o resultado


@pytest.mark.parametrize("resultado,campos", [
    ("recovered", {"recovered_on": None}), ("recovered", {"receipt_ref": None}), ("recovered", {"recovered_on": _hoje(1)}),
    ("recovered", {"recovered_on": _hoje(-1)}), ("recovered", {"justification": "curta"}),
    ("waived", {"recovered_on": _hoje()}), ("waived", {"receipt_ref": "E1234567"}), ("waived", {"justification": None}),
    ("devolvido", COMPROVANTE), (None, COMPROVANTE), (["recovered"], COMPROVANTE),
], ids=lambda v: repr(v)[:24])
def test_encerrar_validacao(adm, resultado, campos):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    _total(a)
    r = _encerrar(adm, a, resultado, **campos)
    assert r.status_code == 422, r.text
    assert _rev(a.fatura)["status"] == "recovery" and _acoes(a.fatura) == ["paid_before_refund"]


def test_encerrar_repetido(adm):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    _total(a)
    assert _encerrar(adm, a, "waived").status_code == 200
    assert _encerrar(adm, a, "recovered").status_code == 409
    assert _acoes(a.fatura) == ["paid_before_refund", "recovery_waived"]


# ── Estados ──────────────────────────────────────────────────────────────────

def test_estorno_deixa_historico(canal):
    c = _afiliado()
    a = _paga(c)
    objeto = _cobranca(a.fatura)
    for _ in range(3):
        assert _enviar("charge.refunded", objeto).status_code == 200
    assert _status(a)["status"] == "reversed" and _rev(a.fatura)["status"] == "resolved"
    assert _acoes(a.fatura) == ["auto_reversed"] and canal.enviados == []


def test_estados_com_valor_maior(adm):
    c = _afiliado()  # parcial resolvido, em saque, depois total
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    _parcial(a)
    assert _aprovar(adm, a, 10_000).status_code == 200
    _total(a)
    rev = _rev(a.fatura)
    assert (rev["status"], rev["tipo"]) == ("open", "total") and _pagar(adm, saque).status_code == 409

    c = _afiliado()  # parcial resolvido, pago, depois total
    a = _paga(c)
    _parcial(a)
    assert _aprovar(adm, a, 10_000).status_code == 200
    assert mark_payout_paid(request_payout(c.aff["id"])["id"])
    _total(a)
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)

    c = _afiliado()  # recuperação aberta, depois total: atribui R$ 100, não soma aos R$ 40
    a, _ = _paga_e_saca(c)
    _parcial(a)
    assert _aprovar(adm, a, 6_000).status_code == 200
    _total(a)
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)

    c = _afiliado()  # tudo já recuperado, depois total: nada a cobrar, não abre recuperação zerada
    a, _ = _paga_e_saca(c)
    _parcial(a)
    assert _aprovar(adm, a, 0).status_code == 200
    assert _encerrar(adm, a).status_code == 200
    _total(a)
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("resolved", None)

    c = _afiliado()  # parcial aberto, solta, depois total
    a = _paga(c)
    _parcial(a)
    _total(a)
    assert _status(a)["status"] == "reversed" and _rev(a.fatura)["status"] == "resolved"

    c = _afiliado()  # linha `total` nunca reabre nem rebaixa, mesmo com valor maior
    a, _ = _paga_e_saca(c)
    _total(a)
    assert _encerrar(adm, a, "waived").status_code == 200
    assert _enviar("charge.refunded", _cobranca(a.fatura, amount=300_000, devolvido=200_000)).status_code == 200
    rev = _rev(a.fatura)
    assert (rev["status"], rev["tipo"], rev["refunded_cents"]) == ("resolved", "total", 100_000)

    c = _afiliado()  # valor igual não é fato novo: a revisão fechada fica fechada
    a = _paga(c)
    _parcial(a)
    assert _aprovar(adm, a, 6_000).status_code == 200
    _parcial(a)
    assert _rev(a.fatura)["status"] == "resolved" and _comissao_db(a)["amount_cents"] == 6_000


def test_estorno_manual_fecha_a_pendencia(adm):
    c = _afiliado()
    a = _paga(c)
    _parcial(a)
    assert _post(adm, f"commissions/{a.id}/reverse").status_code == 200
    assert _rev(a.fatura)["status"] == "resolved" and _acoes(a.fatura) == ["commission_reversed"]
    assert _quadros(c)["review"] == 0


def test_saque_de_outro_afiliado_paga(adm):
    """Positivo: a pendência de A não bloqueia B."""
    a, b = _afiliado(), _afiliado()
    de_a, _ = _paga(a), _paga(b)
    saque_a, saque_b = request_payout(a.aff["id"])["id"], request_payout(b.aff["id"])["id"]
    _total(de_a)
    assert _pagar(adm, saque_a).status_code == 409
    assert _pagar(adm, saque_b).status_code == 200


# ── Exclusão do indicado (D7) ────────────────────────────────────────────────

@pytest.mark.parametrize("estado", ["disponivel", "paga"])
def test_exclusao_com_reembolso(canal, estado):
    c = _afiliado()
    a = _paga_e_saca(c)[0] if estado == "paga" else _paga(c)
    db.delete_user_data(a.indicado.uid)
    objeto = _cobranca(a.fatura)
    for _ in range(3):
        assert _enviar("charge.refunded", objeto).status_code == 200
    if estado == "paga":
        assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)
        assert _acoes(a.fatura) == ["paid_before_refund"] and len(canal.enviados) == 1
    else:
        assert _status(a)["status"] == "reversed"
        assert _acoes(a.fatura) == ["auto_reversed"] and canal.enviados == []


def test_comissao_fica_nos_quadros(adm):
    c = _afiliado()
    a, b = _paga(c), _paga(c, 70, disponivel=False)
    antes = _quadros(c)
    db.delete_user_data(a.indicado.uid)
    db.delete_user_data(b.indicado.uid)
    assert _quadros(c) == antes == {"held": 70, "available": 100, "review": 0, "requested": 0, "paid": 0, "generated": 170}
    saque = request_payout(c.aff["id"])["id"]
    assert _saque(saque)["amount_cents"] == 10_000 and _pagar(adm, saque).status_code == 200


def test_indicado_excluido_nao_vaza(mundo, adm):
    c = _afiliado()
    a = _paga(c)
    request_payout(c.aff["id"])
    email, uid = a.indicado.email, str(a.indicado.uid)
    db.delete_user_data(a.indicado.uid)
    _total(a)
    assert _comissao_db(a)["referred_user_id"] is None
    textos = (TestClient(dashboard.app).get(ME, headers=c.h).text, adm.get(OVERVIEW).text)
    assert [p for t in textos for p in (email, uid, a.fatura, "referred_user_id", "stripe_invoice_id") if p in t] == []
    assert a.id in [r["commission_id"] for r in adm.get(OVERVIEW).json()["refund_reviews"]]  # positivo


def test_payment_refund_depois_da_exclusao(mundo):
    c = _afiliado()
    a = _paga(c)
    _parcial(a)
    db.delete_user_data(a.indicado.uid)
    assert list_affiliate_commissions(c.aff["id"])[0]["payment_refund"] == "partial"


def _fk_da_comissao():
    with get_conn() as conn:
        return conn.execute(
            """
            select k.confdeltype, c.is_nullable
              from pg_constraint k
              join information_schema.columns c on c.table_name = 'affiliate_commissions'
                                               and c.column_name = 'referred_user_id'
                                               and c.table_schema = current_schema()
             where k.contype = 'f' and k.conrelid = 'affiliate_commissions'::regclass
               and k.confrelid = 'users'::regclass
            """).fetchone()


def test_init_db_migra_comissao_antiga(mundo):
    with get_conn() as conn:
        conn.execute("delete from affiliate_commissions where referred_user_id is null")
        nome = conn.execute("select conname from pg_constraint where contype = 'f'"
                            " and conrelid = 'affiliate_commissions'::regclass"
                            " and confrelid = 'users'::regclass").fetchone()["conname"]
        conn.execute(f'alter table affiliate_commissions drop constraint "{nome}", add constraint "{nome}"'
                     " foreign key (referred_user_id) references users(id) on delete cascade")
        conn.execute("alter table affiliate_commissions alter column referred_user_id set not null")
        conn.commit()
    assert _fk_da_comissao() == {"confdeltype": "c", "is_nullable": "NO"}
    db.init_db()
    assert _fk_da_comissao() == {"confdeltype": "n", "is_nullable": "YES"}
    a = _paga(_afiliado())
    db.delete_user_data(a.indicado.uid)  # sem o `drop not null`: `NotNullViolation`
    assert _comissao_db(a)["referred_user_id"] is None


# ── Corridas ─────────────────────────────────────────────────────────────────

def _tenta_pagar(saque):
    try:
        return mark_payout_paid(saque)
    except SaqueComPendencia:
        return "recusado"


def _realiza(saque):
    return lambda: registrar_pagamento_realizado(saque, datetime.now(FUSO).date(), "Pix feito antes", "E1234567", "adm")


def test_corridas_webhook_antes_de_marcar_pago(mundo):
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    evento = _evento(a.fatura)
    _, pago = _corrida(lambda: tratar_evento(evento), lambda: _tenta_pagar(saque), a)
    assert pago == "recusado" and _saque(saque)["status"] == "requested"


def test_corridas_marcar_pago_antes_do_webhook(mundo):
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    evento = _evento(a.fatura)
    pago, _ = _corrida(lambda: _tenta_pagar(saque), lambda: tratar_evento(evento), a)
    assert pago is True and _status(a)["status"] == "paid"
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)


def test_corridas_webhook_total_antes_da_rejeicao(mundo):
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    evento = _evento(a.fatura)
    _corrida(lambda: tratar_evento(evento), lambda: reject_payout(saque, "x", "adm"), a)
    assert _status(a) == {"status": "reversed", "payout_id": None}
    assert _rev(a.fatura)["status"] == "resolved" and _acoes(a.fatura) == ["payout_rejected"]


def test_corridas_rejeicao_antes_do_webhook_total(mundo):
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    evento = _evento(a.fatura)
    _corrida(lambda: reject_payout(saque, "x", "adm"), lambda: tratar_evento(evento), a)
    assert _status(a) == {"status": "reversed", "payout_id": None}
    assert _rev(a.fatura)["status"] == "resolved" and _acoes(a.fatura) == ["auto_reversed"]


@pytest.mark.parametrize("realizado_primeiro", [True, False], ids=["realizado_primeiro", "rejeicao_primeiro"])
def test_corridas_realizado_contra_rejeicao(mundo, realizado_primeiro):
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    _total(a)
    realiza, rejeita = _realiza(saque), lambda: reject_payout(saque, "x", "adm")
    if realizado_primeiro:
        assert _corrida(realiza, rejeita, a) == (True, False)
        assert (_saque(saque)["status"], _status(a)["status"], _rev(a.fatura)["status"]) == ("paid", "paid", "recovery")
    else:
        assert _corrida(rejeita, realiza, a) == (True, False)
        assert (_saque(saque)["status"], _status(a)["status"], _rev(a.fatura)["status"]) == ("rejected", "reversed", "resolved")


def test_corridas_realizado_antes_do_webhook_total(mundo):
    c = _afiliado()
    a, _, saque = _dois_com_saque(c)
    _parcial(a)
    evento = _evento(a.fatura)
    _corrida(_realiza(saque), lambda: tratar_evento(evento), a)
    assert _status(a)["status"] == "paid"
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)


def test_webhook_e_exclusao_nao_travam_e_o_saque_segue_recusado(mundo):
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    evento = _evento(a.fatura)
    with ThreadPoolExecutor(2) as pool, get_conn() as segura:
        _segura(segura, a)
        try:
            webhook = pool.submit(tratar_evento, evento)
            assert _esperando_lock(1)
            exclusao = pool.submit(db.delete_user_data, a.indicado.uid)
            time.sleep(0.8)
            assert not exclusao.done()
        finally:
            segura.commit()
        assert webhook.exception(timeout=20) is None and exclusao.exception(timeout=20) is None
    assert _rev(a.fatura)["status"] == "open" and _comissao_db(a)["referred_user_id"] is None
    assert _tenta_pagar(saque) == "recusado"


# ── Reentrega e aviso ────────────────────────────────────────────────────────

def test_reentrega_nao_duplica(canal):
    c = _afiliado()
    a = _paga(c)
    request_payout(c.aff["id"])
    antes = _quadros(c)
    objeto = _cobranca(a.fatura)
    for _ in range(3):
        assert _enviar("charge.refunded", objeto).status_code == 200
    with get_conn() as conn:
        assert conn.execute("select count(*) as n from affiliate_refund_reviews where stripe_invoice_id = %s",
                            (a.fatura,)).fetchone()["n"] == 1
    assert len(canal.enviados) == 1 and _quadros(c) == antes


def test_banco_falha_responde_erro(canal, monkeypatch):
    c = _afiliado()
    a = _paga(c)
    request_payout(c.aff["id"])
    objeto = _cobranca(a.fatura)
    with monkeypatch.context() as m:
        m.setattr(reviews, "_gravar", lambda *x, **k: 1 / 0)
        assert _enviar("charge.refunded", objeto).status_code == 500
    assert _rev(a.fatura) is None and canal.enviados == []
    assert _me(c)["commissions"][0]["payment_refund"] == "none"  # o rótulo voltou junto
    assert _enviar("charge.refunded", objeto).status_code == 200
    assert _rev(a.fatura)["status"] == "open" and len(canal.enviados) == 1


@pytest.mark.parametrize("falha", [{"status": 500}, {"status": 429}, {"erro": requests.Timeout()}], ids=["500", "429", "timeout"])
def test_aviso_falha_responde_200(canal, adm, falha):
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    vars(canal).update(falha)
    assert _enviar("charge.refunded", _cobranca(a.fatura)).status_code == 200
    rev = _rev(a.fatura)
    assert rev["status"] == "open" and rev["notified_at"] is None
    assert _pagar(adm, saque).status_code == 409


def test_aviso_entregue_nao_libera(canal, adm):
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    _total(a)
    assert _rev(a.fatura)["notified_at"] is not None and len(canal.enviados) == 1
    assert _pagar(adm, saque).status_code == 409


def test_sem_url_nao_marca(canal, adm, monkeypatch):
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    monkeypatch.delenv("ADMIN_NOTIFY_WEBHOOK_URL")
    _total(a)
    assert reenviar_pendentes() == 0
    rev = _rev(a.fatura)
    assert rev["notified_at"] is None and rev["notify_claimed_until"] is None and canal.enviados == []
    assert _pagar(adm, saque).status_code == 409
    assert a.id in [r["commission_id"] for r in adm.get(OVERVIEW).json()["refund_reviews"]]


def _limpa_fila():
    with get_conn() as conn:
        conn.execute("update affiliate_refund_reviews set notified_at = now() where notified_at is null")
        conn.commit()


def _vence_claim(fatura):
    with get_conn() as conn:
        conn.execute("update affiliate_refund_reviews set notify_claimed_until = now() - interval '1 second'"
                     " where stripe_invoice_id = %s", (fatura,))
        conn.commit()


def test_marca_so_com_true(canal, monkeypatch):
    _limpa_fila()
    c = _afiliado()
    a = _paga(c)
    canal.status = 500
    _parcial(a)
    assert _rev(a.fatura)["notified_at"] is None
    with monkeypatch.context() as m:
        m.setattr(admin_notify, "_send", lambda msg: False)
        _vence_claim(a.fatura)
        assert reenviar_pendentes() == 0
    rev = _rev(a.fatura)
    assert rev["notified_at"] is None and rev["notify_claimed_until"] is not None
    canal.status = 204
    assert reenviar_pendentes() == 0  # claim vivo: a falha espera o prazo
    _vence_claim(a.fatura)
    assert reenviar_pendentes() == 1 and _rev(a.fatura)["notified_at"] is not None


def test_claim_vencido_retoma(canal, monkeypatch):
    """O processo reivindicou e morreu antes do envio."""
    _limpa_fila()
    c = _afiliado()
    a = _paga(c)
    monkeypatch.delenv("ADMIN_NOTIFY_WEBHOOK_URL")
    _parcial(a)
    monkeypatch.setenv("ADMIN_NOTIFY_WEBHOOK_URL", URL)
    assert reivindicar(_rev(a.fatura)["id"])
    assert reenviar_pendentes() == 0 and canal.enviados == []
    _vence_claim(a.fatura)
    assert reenviar_pendentes() == 1 and len(canal.enviados) == 1
    assert reenviar_pendentes() == 0 and len(canal.enviados) == 1


def test_fato_novo_durante_o_envio_segue_pendente(canal, monkeypatch):
    _limpa_fila()
    c = _afiliado()
    a = _paga(c)
    monkeypatch.delenv("ADMIN_NOTIFY_WEBHOOK_URL")
    _parcial(a)
    monkeypatch.setenv("ADMIN_NOTIFY_WEBHOOK_URL", URL)

    def envia_e_chega_mais(msg):
        registrar_reembolso(a.fatura, "partial", 50_000, AGORA)
        canal.enviados.append(msg)
        return True

    monkeypatch.setattr(admin_notify, "_send", envia_e_chega_mais)
    assert reenviar_pendentes(limite=1) == 1
    assert _rev(a.fatura)["notified_at"] is None and _rev(a.fatura)["refunded_cents"] == 50_000


def test_workers_concorrentes(canal, monkeypatch):
    _limpa_fila()
    c = _afiliado()
    monkeypatch.delenv("ADMIN_NOTIFY_WEBHOOK_URL")
    comissoes = [_paga(c, 60) for _ in range(12)]
    for com in comissoes:
        _parcial(com, 1_000)
    monkeypatch.setenv("ADMIN_NOTIFY_WEBHOOK_URL", URL)
    enviados, trava = Counter(), threading.Lock()

    def envia(msg):
        with trava:
            enviados[msg] += 1
        time.sleep(0.03)
        return True

    monkeypatch.setattr(admin_notify, "_send", envia)
    with ThreadPoolExecutor(6) as pool:
        total = sum(f.result(timeout=30) for f in [pool.submit(reenviar_pendentes) for _ in range(6)])
    assert total == 12 and len(enviados) == 12 and set(enviados.values()) == {1}


def test_worker_ligado():
    resultado, diagnostico = sondar({"aviso": "core.services.afiliado_reembolso:reenviar_pendentes"})
    assert resultado == {"aviso": True, "do_disco": []}, diagnostico


# ── Reembolso antes do `invoice.paid` ────────────────────────────────────────

@pytest.mark.parametrize("devolvido,esperado", [(100_000, ("reversed", "resolved")), (30_000, ("pending", "open"))],
                         ids=["total", "parcial"])
def test_reembolso_antes_da_comissao(canal, devolvido, esperado):
    c = _afiliado()
    ind = _indicado(c)
    fatura = f"in_{uuid.uuid4().hex[:12]}"
    assert _enviar("charge.refunded", _cobranca(fatura, devolvido=devolvido)).status_code == 200
    assert _rev(fatura)["status"] == "no_commission" and canal.enviados == []
    linha = record_commission_for_invoice(ind.uid, fatura, 100_000)
    com = SimpleNamespace(id=linha["id"], fatura=fatura)
    assert (_comissao_db(com)["status"], _rev(fatura)["status"]) == esperado
    assert _rev(fatura)["commission_id"] == com.id and _rev(fatura)["amount_cents"] == 10_000


def test_no_commission_nao_rebaixa(mundo):
    """Evento velho depois do total: quem nasce depois nasce pelo total."""
    fatura = f"in_{uuid.uuid4().hex[:12]}"
    registrar_reembolso(fatura, "total", 100_000, AGORA)
    registrar_reembolso(fatura, "partial", 200_000, AGORA)
    assert (_rev(fatura)["tipo"], _rev(fatura)["refunded_cents"]) == ("total", 100_000)


def test_corrida_fatura_sem_comissao(mundo):
    soltas = 0
    c = _afiliado()
    for _ in range(40):
        ind = _indicado(c)
        fatura = f"in_{uuid.uuid4().hex[:12]}"
        largada = threading.Barrier(2, timeout=10)

        def comissao():
            largada.wait()
            return record_commission_for_invoice(ind.uid, fatura, 100_000)

        def reembolso():
            largada.wait()
            return registrar_reembolso(fatura, "total", 100_000, AGORA)

        with ThreadPoolExecutor(2) as pool:
            passos = [pool.submit(comissao), pool.submit(reembolso)]
            linha = passos[0].result(timeout=15)
            passos[1].result(timeout=15)
        soltas += _status(SimpleNamespace(id=linha["id"]))["status"] != "reversed"
    assert soltas == 0


# ── Rotas ────────────────────────────────────────────────────────────────────

def test_rota_pix_recusa(adm):
    c = _afiliado()
    a = _paga(c)
    pix = TestClient(dashboard.app).post("/api/affiliate/payout", headers=c.h, json={"pix_key": "chave-pix@example.com"})
    assert pix.status_code == 200, pix.text
    saque = pix.json()["payout"]["id"]
    _parcial(a)
    caminho = f"/admin/api/affiliates/payouts/{saque}/pix"
    assert adm.get(caminho).status_code == 409
    assert next(p for p in adm.get(OVERVIEW).json()["payouts"] if p["id"] == saque)["blocked"] is True
    assert _aprovar(adm, a, 6_000).status_code == 200
    r = adm.get(caminho)
    assert r.status_code == 200 and r.json()["amount_cents"] == 6_000  # QR novo, com o valor novo
    assert next(p for p in adm.get(OVERVIEW).json()["payouts"] if p["id"] == saque)["blocked"] is False


@pytest.mark.parametrize("caminho", ["payouts/1/payment-recorded", "refund-reviews/1/approve", "refund-reviews/1/close-recovery"])
def test_rotas_exigem_admin(adm, caminho):
    c = _afiliado()
    anonimo = TestClient(dashboard.app, base_url="https://testserver")
    assert anonimo.post(f"/admin/api/affiliates/{caminho}", headers=csrf(anonimo), json={}).status_code in (401, 403)
    assert anonimo.post(f"/admin/api/affiliates/{caminho}", headers={**csrf(anonimo), **c.h}, json={}).status_code in (401, 403)
    assert anonimo.get(OVERVIEW, headers=c.h).status_code in (401, 403)


def test_overview_nao_leva_o_indicado(mundo, adm):
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    b = _paga(c, 70)
    _total(a)
    _parcial(b)
    texto = adm.get(OVERVIEW).text
    assert [p for p in (a.indicado.email, str(a.indicado.uid), a.fatura, b.fatura, "stripe_invoice_id") if p in texto] == []


# ── Script (D5) ──────────────────────────────────────────────────────────────

def _roda(script, capsys, aplicar):
    capsys.readouterr()
    n = script.rodar(aplicar=aplicar)
    return n, capsys.readouterr().out


def _linha_de(saida, com):
    return next((x for x in saida.splitlines() if f"comissão {com.id} " in x), "")


def test_script_categorias(script, capsys):
    c = _afiliado()
    solta, parcial = _paga(c), _paga(c, 50)
    d = _afiliado()
    em_saque = _paga(d)
    request_payout(d.aff["id"])
    e = _afiliado()
    paga = _paga_e_saca(e)[0]
    f = _afiliado()
    disputada, intacta, registrada = _paga(f), _paga(f), _paga(f)
    script.stripe.disputas[disputada.fatura] = [{"status": "needs_response", "amount": 100_000}]
    registrar_reembolso(registrada.fatura, "partial", 1_000, AGORA)
    cheia = {"amount": 100_000, "amount_refunded": 100_000, "disputed": False}
    script.stripe.cobrancas |= {solta.fatura: cheia, em_saque.fatura: cheia, paga.fatura: cheia,
                                parcial.fatura: {"amount": 50_000, "amount_refunded": 1_000, "disputed": False},
                                disputada.fatura: {**cheia, "disputed": True},  # disputa aberta abaixo
                                registrada.fatura: {"amount": 100_000, "amount_refunded": 1_000, "disputed": False}}
    n, saida = _roda(script, capsys, False)
    assert _linha_de(saida, solta).startswith("a_estornar:") and "R$ 100.00" in _linha_de(saida, solta)
    for com in (parcial, em_saque, paga):
        assert _linha_de(saida, com).startswith("segue_revisao:")
    assert _linha_de(saida, disputada).endswith("· disputa_aberta")
    assert _linha_de(saida, intacta) == "" and _linha_de(saida, registrada) == ""
    assert n["a_estornar_cents"] >= 10_000 and n["ja_registrada"] >= 1
    assert _status(solta)["status"] == "pending" and _rev(solta.fatura) is None  # simulação não grava
    assert [p for com in (solta, paga, disputada) for p in (com.fatura, com.indicado.email, str(com.indicado.uid))
            if p in saida] == []
    n, saida = _roda(script, capsys, True)
    assert _status(solta)["status"] == "reversed" and "pendência resolved" in _linha_de(saida, solta)
    assert _rev(parcial.fatura)["status"] == "open" and _rev(em_saque.fatura)["status"] == "open"
    assert (_rev(paga.fatura)["status"], _rev(paga.fatura)["recovery_cents"]) == ("recovery", 10_000)
    assert _rev(disputada.fatura) is None and _rev(intacta.fatura) is None


@pytest.mark.parametrize("caso,motivo", [("uma_de_duas", None), ("disputa", "disputa_aberta"), ("payment_record", "forma_inesperada"),
                                         ("erro_api", "ConnectionError"), ("vazio", "sem_cobranca")])
def test_script_confirmacao(script, capsys, caso, motivo):
    c = _afiliado()
    com = _paga(c)
    cheia = {"amount": 100_000, "amount_refunded": 100_000, "disputed": False}
    pagamento = {"status": "paid", "payment": {"type": "payment_intent", "payment_intent": com.fatura}}
    if caso == "uma_de_duas":
        script.stripe.pagamentos[com.fatura] = [pagamento, {"status": "paid", "payment": {"type": "payment_intent", "payment_intent": "pi_b"}}]
        script.stripe.cobrancas |= {com.fatura: cheia, "pi_b": {"amount": 100_000, "amount_refunded": 0, "disputed": False}}
    elif caso == "disputa":
        script.stripe.cobrancas[com.fatura] = {**cheia, "disputed": True}
        script.stripe.disputas[com.fatura] = [{"status": "needs_response", "amount": 100_000}]
    elif caso == "payment_record":
        script.stripe.pagamentos[com.fatura] = [{"status": "paid", "payment": {"type": "payment_record", "payment_intent": None}}]
    elif caso == "erro_api":
        script.stripe.pagamentos[com.fatura] = ConnectionError("fatura " + com.fatura)
    else:
        script.stripe.pagamentos[com.fatura] = []
    _, saida = _roda(script, capsys, True)
    if motivo is None:
        assert (_rev(com.fatura)["tipo"], _rev(com.fatura)["status"]) == ("partial", "open")
        assert _status(com)["status"] == "pending"
    else:
        assert _linha_de(saida, com) == f"sem_confirmacao: comissão {com.id} · {motivo}"
        assert _rev(com.fatura) is None and _status(com)["status"] == "pending"
    assert com.fatura not in saida


def test_script_revalida(script, capsys, monkeypatch):
    """A comissão entra em saque depois da leitura do script: o `--aplicar` não estorna."""
    import core.services.ebook_entrega as modulo  # a passada de comissões lê a Stripe pela regra da conferência
    c = _afiliado()
    com = _paga(c)
    script.stripe.cobrancas[com.fatura] = {"amount": 100_000, "amount_refunded": 100_000, "disputed": False}
    real = modulo.cobrancas_da_fatura

    def saca_antes(fatura, chave):
        if fatura == com.fatura:
            request_payout(c.aff["id"])
        return real(fatura, chave)

    monkeypatch.setattr(modulo, "cobrancas_da_fatura", saca_antes)
    _, saida = _roda(script, capsys, True)
    assert _status(com)["status"] == "pending" and _status(com)["payout_id"]
    assert _rev(com.fatura)["status"] == "open" and "pendência open" in _linha_de(saida, com)
    with pytest.raises(SaqueComPendencia):
        mark_payout_paid(_status(com)["payout_id"])


def test_script_duas_vezes(script, capsys):
    """Estornada some da varredura: quem mede a repetição é a paga, que segue nela."""
    c = _afiliado()
    paga, _ = _paga_e_saca(c)
    com = _paga(c)
    cheia = {"amount": 100_000, "amount_refunded": 100_000, "disputed": False}
    script.stripe.cobrancas |= {com.fatura: cheia, paga.fatura: cheia}
    _roda(script, capsys, True)
    antes = _quadros(c)
    _, saida = _roda(script, capsys, True)
    assert _quadros(c) == antes and _linha_de(saida, com) == "" and _linha_de(saida, paga) == ""
    assert [(x["action"], x["actor"]) for x in _eventos(com.fatura)] == [("auto_reversed", "script")]
    assert [(x["action"], x["actor"]) for x in _eventos(paga.fatura)] == [("paid_before_refund", "script")]


def test_script_alcanca_conta_excluida(script, capsys):
    c = _afiliado()
    com = _paga(c)
    request_payout(c.aff["id"])
    script.stripe.cobrancas[com.fatura] = {"amount": 100_000, "amount_refunded": 100_000, "disputed": False}
    db.delete_user_data(com.indicado.uid)
    _roda(script, capsys, True)
    assert _rev(com.fatura)["status"] == "open"


# ── Rodada 2 do Tester (A1 a A12) e mutações que sobreviveram ────────────────

def _saque_com_pix(c):
    r = _sacar(c)
    assert r.status_code == 200, r.text
    return r.json()["payout"]["id"]


@pytest.mark.parametrize("como", ["rejeitado", "pago", "realizado"])
def test_qr_so_de_saque_aberto(adm, como):
    """A1: QR de saque fechado deixaria pagar de novo o que já saiu ou foi recusado."""
    c = _afiliado()
    a = _paga(c)
    saque = _saque_com_pix(c)
    if como == "rejeitado":
        assert _rejeitar(adm, saque).status_code == 200
    elif como == "pago":
        assert _pagar(adm, saque).status_code == 200
    else:
        _total(a)
        assert _realizado(adm, saque).status_code == 200
    assert adm.get(f"/admin/api/affiliates/payouts/{saque}/pix").status_code == 409


def _cheia():
    return {"amount": 100_000, "amount_refunded": 100_000, "disputed": False}


@pytest.mark.parametrize("estado", ["solta", "em_saque", "paga"])
def test_script_ve_reembolso_maior_de_pendencia_ja_registrada(script, capsys, estado):
    """A2: parcial já resolvida, total perdido no webhook. O script tem de chegar ao total."""
    c = _afiliado()
    a = _paga(c)
    saque = None
    if estado == "em_saque":
        saque = request_payout(c.aff["id"])["id"]
    elif estado == "paga":
        saque = request_payout(c.aff["id"])["id"]
        assert mark_payout_paid(saque)
    registrar_reembolso(a.fatura, "partial", 30_000, AGORA)
    assert resolver_parcial(_rev(a.fatura)["id"], "adm", 10_000, "nota de teste 1", 30_000)
    script.stripe.cobrancas[a.fatura] = _cheia()
    n = script.rodar(aplicar=True)
    if estado == "solta":
        assert _status(a)["status"] == "reversed" and n["a_estornar"] >= 1
    elif estado == "em_saque":
        assert _rev(a.fatura)["status"] == "open"
        with pytest.raises(SaqueComPendencia):
            mark_payout_paid(saque)
    else:
        assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 10_000)


def test_script_pula_so_a_linha_ja_total(script, capsys):
    """Positivo do A2: pendência `total` não é relida; sem fato novo, nada muda."""
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    registrar_reembolso(a.fatura, "total", 100_000, AGORA)
    eventos = _acoes(a.fatura)
    script.stripe.cobrancas[a.fatura] = _cheia()
    n, saida = _roda(script, capsys, True)
    assert _acoes(a.fatura) == eventos and _linha_de(saida, a) == "" and n["ja_registrada"] >= 1


def test_aviso_velho_nao_marca_a_recuperacao(canal, adm, monkeypatch):
    """A4: o worker manda o aviso `open`, e no meio do envio o admin registra o pagamento."""
    _limpa_fila()
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    monkeypatch.delenv("ADMIN_NOTIFY_WEBHOOK_URL")
    _total(a)
    monkeypatch.setenv("ADMIN_NOTIFY_WEBHOOK_URL", URL)

    def envia_e_registra(msg):
        canal.enviados.append(msg)
        assert _realizado(adm, saque).status_code == 200
        return True

    monkeypatch.setattr(admin_notify, "_send", envia_e_registra)
    assert reenviar_pendentes(limite=1) == 1
    rev = _rev(a.fatura)
    assert (rev["status"], rev["notified_at"]) == ("recovery", None)
    monkeypatch.setattr(admin_notify, "_send", lambda msg: canal.enviados.append(msg) or True)
    assert reenviar_pendentes() == 1 and "recuperação pendente" in canal.enviados[-1]


def test_script_nao_rotula_cobranca_contestada(script, capsys):
    """A6: disputa aberta não confirma nada, e a passada de pagamentos não rotula o que a de comissões
    não confirmou."""
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    script.stripe.cobrancas[a.fatura] = {**_cheia(), "disputed": True}
    script.stripe.disputas[a.fatura] = [{"status": "needs_response", "amount": 100_000}]
    script.stripe.faturas[_cliente(a.indicado)] = [_fatura(a.fatura)]
    n, saida = _roda(script, capsys, True)
    with get_conn() as conn:
        rotulo = conn.execute("select refund_status from affiliate_referral_payments where payment_ref = %s",
                              (a.fatura,)).fetchone()["refund_status"]
    assert rotulo == "none" and _rev(a.fatura) is None
    assert _linha_de(saida, a).endswith("· disputa_aberta") and n["contestadas_para_conferir"] == 1
    assert _status(a)["payout_id"] == saque and _status(a)["status"] == "pending"


def test_rejeicao_fecha_pendencia_da_comissao_estornada_pelo_rotulo(mundo, adm):
    """A8: duas cobranças na fatura. A 2ª devolvida inteira rotula `total`; a pendência segue parcial."""
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    assert _enviar("charge.refunded", _cobranca(a.fatura, amount=60_000, devolvido=50_000)).status_code == 200
    assert _enviar("charge.refunded", _cobranca(a.fatura, amount=40_000, devolvido=40_000)).status_code == 200
    assert _rejeitar(adm, saque).status_code == 200
    assert _status(a)["status"] == "reversed"
    assert _rev(a.fatura)["status"] == "resolved" and _acoes(a.fatura)[-1] == "payout_rejected"
    assert _quadros(c)["review"] == 0


@pytest.mark.parametrize("veneno", ["\x00" * 12, "\ud800" * 12, " \x00\ud800 " * 6], ids=["nul", "surrogate", "misto"])
@pytest.mark.parametrize("acao", ["aprovar", "realizado", "encerrar"])
def test_justificativa_so_de_veneno_e_recusada(adm, veneno, acao):
    """A9: limpa vira U+FFFD, e U+FFFD não é texto."""
    c = _afiliado()
    if acao == "aprovar":
        a = _paga(c)
        _parcial(a)
        r = _aprovar(adm, a, 6_000, nota=veneno)
    elif acao == "realizado":
        a = _paga(c)
        saque = request_payout(c.aff["id"])["id"]
        _total(a)
        r = _realizado(adm, saque, justification=veneno)
    else:
        a, _ = _paga_e_saca(c)
        _total(a)
        r = _encerrar(adm, a, "waived", justification=veneno)
    assert r.status_code == 422, r.text


def test_justificativa_com_veneno_e_texto_passa(adm):
    """Positivo do A9: o veneno sai, o texto fica."""
    c = _afiliado()
    a = _paga(c)
    _parcial(a)
    assert _aprovar(adm, a, 6_000, nota="Ajuste\x00 do valor devolvido").status_code == 200


def test_script_conta_pelo_desfecho_real(script, capsys, monkeypatch):
    """A10: comissão entra em saque entre a leitura e a gravação. Não estornou: não conta como estorno."""
    import backfill_afiliado_pagamentos as modulo
    c = _afiliado()
    a = _paga(c)
    script.stripe.cobrancas[a.fatura] = _cheia()
    real = modulo.registrar_reembolso

    def saca_antes(*x, **k):
        request_payout(c.aff["id"])
        return real(*x, **k)

    monkeypatch.setattr(modulo, "registrar_reembolso", saca_antes)
    n, saida = _roda(script, capsys, True)
    assert (n["a_estornar"], n["a_estornar_cents"]) == (0, 0)
    assert n["segue_revisao"] >= 1 and _linha_de(saida, a).startswith("segue_revisao:")


def test_script_sem_confirmacao_sai_com_erro(script, capsys, monkeypatch):
    """A10: a API fora em toda comissão não pode sair com 0."""
    c = _afiliado()
    a = _paga(c)
    script.stripe.pagamentos[a.fatura] = ConnectionError("fora")
    monkeypatch.setattr(sys, "argv", ["backfill", "--aplicar"])
    assert script.main() == 1


def test_script_sem_pendencias_sai_com_zero(script, capsys, monkeypatch):
    """Positivo do A10: com tudo confirmado, o código de saída segue 0."""
    _paga(_afiliado())  # o Stripe falso diz "sem reembolso" para toda comissão do banco
    monkeypatch.setattr(sys, "argv", ["backfill"])
    assert script.main() == 0


def test_fato_novo_reabre_o_aviso(canal):
    """Mutação 1: reembolso maior zera `notified_at` e o admin é avisado de novo."""
    c = _afiliado()
    a = _paga(c)
    request_payout(c.aff["id"])
    _parcial(a)
    assert len(canal.enviados) == 1 and _rev(a.fatura)["notified_at"] is not None
    _parcial(a, 50_000)
    assert len(canal.enviados) == 2 and _rev(a.fatura)["notified_at"] is not None


def test_realizado_reabre_o_aviso_como_recuperacao(canal, adm):
    """Mutação 43: o aviso `open` já saiu; a recuperação nascida no "Pagamento já realizado" sai também."""
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    _total(a)
    assert len(canal.enviados) == 1
    assert _realizado(adm, saque).status_code == 200
    assert _rev(a.fatura)["notified_at"] is None
    assert reenviar_pendentes() >= 1 and any("recuperação pendente" in m for m in canal.enviados[1:])


def test_recuperacao_desconta_e_acumula_o_ja_encerrado(adm):
    """Mutações 19 e 23: duas revisões parciais numa comissão paga, cada uma encerrada, e depois o total."""
    c = _afiliado()
    a, _ = _paga_e_saca(c)
    _parcial(a)
    assert _aprovar(adm, a, 6_000).status_code == 200
    assert _rev(a.fatura)["recovery_cents"] == 4_000
    assert _encerrar(adm, a).status_code == 200
    _parcial(a, 50_000)
    assert _aprovar(adm, a, 3_000).status_code == 200
    rev = _rev(a.fatura)
    assert (rev["status"], rev["recovery_cents"]) == ("recovery", 3_000)  # 100 - 30 - 40 já encerrados
    assert _encerrar(adm, a).status_code == 200
    assert _rev(a.fatura)["recovery_closed_cents"] == 7_000
    _total(a)
    assert (_rev(a.fatura)["status"], _rev(a.fatura)["recovery_cents"]) == ("recovery", 3_000)


def test_saque_pago_nao_aparece_bloqueado(adm):
    """Mutação 47: parcial aberta sobre comissão já paga não bloqueia saque fechado."""
    c = _afiliado()
    _, saque = _paga_e_saca(c)
    _parcial(SimpleNamespace(fatura=_fatura_da(saque)))
    assert next(p for p in adm.get(OVERVIEW).json()["payouts"] if p["id"] == saque)["blocked"] is False


def _fatura_da(saque):
    with get_conn() as conn:
        return conn.execute("select stripe_invoice_id from affiliate_commissions where payout_id = %s",
                            (saque,)).fetchone()["stripe_invoice_id"]


def test_comissao_em_saque_com_parcial_nao_esta_em_revisao(mundo):
    """Mutação 38: `in_review` é só a comissão fora do saque; a do saque segue "em saque"."""
    c = _afiliado()
    a, _, _ = _dois_com_saque(c)
    _parcial(a)
    item = next(x for x in _me(c)["commissions"] if x["id"] == a.id)
    assert item["in_review"] is False and item["payout_id"]


def test_ja_pago_do_admin_vem_do_saque(adm):
    """Mutação 46: comissão apagada pela cascata antiga não encolhe o "Já pago" do admin."""
    c = _afiliado()
    a, b, saque = _dois_com_saque(c)
    assert mark_payout_paid(saque)
    with get_conn() as conn:
        conn.execute("delete from affiliate_commissions where id = %s", (b.id,))
        conn.commit()
    linha = next(x for x in adm.get(OVERVIEW).json()["affiliates"] if x["id"] == c.aff["id"])
    assert linha["paid_cents"] == 17_000


def test_revisao_recusa_comissao_que_saiu_do_saque_no_meio(mundo):
    """Mutação 16: a comissão sai do saque entre a leitura e a trava; a revisão não mexe no saque velho."""
    c = _afiliado()
    a, _, saque = _dois_com_saque(c)
    _parcial(a)
    rev = _rev(a.fatura)
    with ThreadPoolExecutor(1) as pool, get_conn() as segura:
        _segura(segura, a)
        try:
            revisao = pool.submit(resolver_parcial, rev["id"], "adm", 6_000, "ajuste no meio", rev["refunded_cents"])
            assert _esperando_lock(1)
            segura.execute("update affiliate_commissions set payout_id = null where id = %s", (a.id,))
        finally:
            segura.commit()
        assert isinstance(revisao.exception(timeout=15), PendenciaMudou)
    assert _saque(saque)["amount_cents"] == 17_000 and _comissao_db(a)["amount_cents"] == 10_000


@pytest.mark.parametrize("rota", ["reject", "reverse"])
def test_autor_do_evento_e_o_admin_logado(mundo, monkeypatch, rota):
    """Mutações 73 e 74: com usuário diferente do padrão, o evento leva quem agiu."""
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_USERNAME", "lucas")
    adm = _admin(monkeypatch)
    c = _afiliado()
    a = _paga(c)
    if rota == "reject":
        saque = request_payout(c.aff["id"])["id"]
        _total(a)
        assert _rejeitar(adm, saque).status_code == 200
    else:
        _parcial(a)
        assert _post(adm, f"commissions/{a.id}/reverse").status_code == 200
    assert _eventos(a.fatura)[-1]["actor"] == "lucas"


@pytest.mark.parametrize("tamanho,status", [(9, 422), (10, 200), (500, 200), (501, 422)])
def test_justificativa_nas_bordas(adm, tamanho, status):
    """Mutação 59: 9 e 501 recusados; 10 e 500 aceitos."""
    c = _afiliado()
    a = _paga(c)
    _parcial(a)
    assert _aprovar(adm, a, 6_000, nota="j" * tamanho).status_code == status


class _Relogio(datetime):
    """`now` fixo: 2026-11-06 23:30 em Brasília, já 07/11 em UTC."""
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 7, 2, 30, tzinfo=ZoneInfo("UTC")).astimezone(tz)


@pytest.mark.parametrize("dia,status", [("2026-11-07", 422), ("2026-11-06", 200)])
def test_futuro_e_em_brasilia(adm, monkeypatch, dia, status):
    """Mutação 55: às 23:30 de Brasília o dia seguinte ainda é futuro, mesmo já sendo amanhã em UTC."""
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    with get_conn() as conn:
        conn.execute("update affiliate_payouts set requested_at = '2026-11-01 12:00+00' where id = %s", (saque,))
        conn.commit()
    _total(a)
    monkeypatch.setattr(modulo_reembolso, "datetime", _Relogio)
    assert _realizado(adm, saque, paid_on=dia).status_code == status


@pytest.mark.parametrize("pedido,dia,status", [
    ("2026-11-02 01:30+00", "2026-11-01", 200),  # 22:30 de 01/11 em Brasília; já 02/11 em UTC
    ("2026-11-02 03:30+00", "2026-11-01", 422),  # 00:30 de 02/11 em Brasília; ainda 01/11 a oeste
], ids=["brasilia_atras_de_utc", "brasilia_a_frente_do_oeste"])
def test_minimo_e_o_dia_do_pedido_em_brasilia(adm, pedido, dia, status):
    """Mutação 65: o dia do pedido é o de Brasília, qualquer que seja o fuso da sessão do banco."""
    c = _afiliado()
    a = _paga(c)
    saque = request_payout(c.aff["id"])["id"]
    with get_conn() as conn:
        conn.execute("update affiliate_payouts set requested_at = %s where id = %s", (pedido, saque))
        conn.commit()
    _total(a)
    assert _realizado(adm, saque, paid_on=dia).status_code == status


def test_dia_em_brasilia_nao_depende_do_fuso_de_quem_entrega():
    """Mutação 65: o pool entrega em Brasília (a rota acima não a separa); aqui o instante vem em UTC."""
    from datetime import date
    assert modulo_reembolso.dia_em_brasilia(datetime(2026, 11, 2, 1, 30, tzinfo=ZoneInfo("UTC"))) == date(2026, 11, 1)
