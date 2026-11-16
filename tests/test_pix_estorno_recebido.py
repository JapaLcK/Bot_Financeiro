"""#1437: a D3 pergunta "o estorno CHEGOU?" e não "o `revoke` RODOU?".

O `PAYMENT_REFUNDED` grava o marcador em `pix_estornos_recebidos` na MESMA transação em que
entra na outbox (`registrar_evento`), antes do 200 ao Asaas. Todo `RECEIVED` (1ª entrega,
retentativa, evento antigo) o lê na D3, o laço o relê antes de cada efeito de compra, e
`reconciliar_grant` também. Caminho real: `entregar` + `drenar_evento`, e o webhook no caso 9;
os casos com afiliado e indicado reais afirmam que nenhuma comissão nasce nem fica viva.

A recepção (filtro do id, tudo-ou-nada, reentrega) está em `tests/test_pix_estorno_recepcao.py`.
Os controles negativos, com os nomes que cada mutação deixa vermelhos, estão no relato do PR.
POSITIVOS: recompra, parcial (caso 12), `test_received_sem_estorno_anterior_concede` e
`test_j1[PAYMENT_REFUNDED]` (2º pagamento, id novo).
"""
from __future__ import annotations

import uuid

import pytest

import core.services.pix_drain as dreno
from _billing_grants_helpers import conta
from _dreno_pix_helpers import efeitos, entregar, evento, ler, nova_cobranca
from core.services.pix_drain_effects import EXECUTORES
from db.connection import get_conn
from test_afiliado_area import _afiliado
from test_afiliado_pagantes import _indicado, _linhas, _pix
from test_pix_dreno_estorno import _grants_ativos, externo  # noqa: F401


def _quebra(*a, **k):
    raise RuntimeError("falha de teste")


def marcador(pid):
    """As classes do marcador do id em `pix_estornos_recebidos` (uma por origem)."""
    with get_conn() as conn:
        return [r["classificacao"] for r in conn.execute(
            "select classificacao from pix_estornos_recebidos where asaas_payment_id = %s order by origem",
            (pid,)).fetchall()]


def _sem_drenar(monkeypatch, tipo, cobranca, **kw):
    """Recebido (200 ao Asaas) e não drenado: o processo morreu antes do dreno."""
    with monkeypatch.context() as m:
        m.setattr(dreno, "drenar_evento", lambda eid: None)
        return entregar(tipo, cobranca, **kw)


def _compra(externo):
    return (externo["ga4"], externo["capi"], externo["email"])


def _comissoes_vivas(cobranca):
    with get_conn() as conn:
        return conn.execute("select status from affiliate_commissions where stripe_invoice_id = %s"
                            " and status <> 'reversed'", (cobranca["external_reference"],)).fetchall()


@pytest.mark.parametrize("quebra", ["ebook", "ga4", "email", "afiliado"])
def test_caso6_retentativa_da_compra_depois_do_estorno_com_revoke_falho(externo, monkeypatch, quebra):
    """R (grant ok, X falha) → F (revoke falha) → R' → F'. Na main, R' rodava a compra sobre `refunded`."""
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    pid = cobranca["asaas_payment_id"]
    with monkeypatch.context() as m:
        externo["email_ok"] = quebra != "email"
        if quebra != "email":
            m.setitem(EXECUTORES, quebra, _quebra)
        compra = entregar("PAYMENT_RECEIVED", cobranca)
    externo["email_ok"] = True
    with monkeypatch.context() as m:
        m.setitem(EXECUTORES, "revoke", _quebra)
        estorno = entregar("PAYMENT_REFUNDED", cobranca)
    antes, compra_antes = efeitos(pid), _compra(externo)
    assert "revoke" not in antes and ler(cobranca["id"])["status"] == "refunded"
    dreno.drenar_evento(compra)  # a varredura pega o mais velho primeiro
    assert evento(compra)["processed_at"] is not None
    assert efeitos(pid) == antes and _compra(externo) == compra_antes, "compra rodou sobre dinheiro devolvido"
    assert _comissoes_vivas(cobranca) == []
    dreno.drenar_evento(estorno)
    assert _grants_ativos(ind.uid) == [] and "revoke" in efeitos(pid)
    assert [linha["refund_status"] for linha in _linhas(c)] == ["total"]
    assert _comissoes_vivas(cobranca) == []


def test_caso8_estorno_antes_com_revoke_falho_nao_concede_nem_comissiona(externo, monkeypatch):
    """F (`pending`, revoke falha) → R → F'. Na main: acesso para sempre (o `revoke` de F' perdia
    a guarda de versão), Purchase, e-mail e comissão `pending`."""
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    with monkeypatch.context() as m:
        m.setitem(EXECUTORES, "revoke", _quebra)
        estorno = entregar("PAYMENT_REFUNDED", cobranca)
    externo["alerta"].clear()
    compra = entregar("PAYMENT_RECEIVED", cobranca)
    assert evento(compra)["processed_at"] is not None
    assert _grants_ativos(ind.uid) == [] and _compra(externo) == (0, 0, 0)
    assert _comissoes_vivas(cobranca) == [] and len(externo["alerta"]) == 1
    dreno.drenar_evento(estorno)
    assert _grants_ativos(ind.uid) == [] and _comissoes_vivas(cobranca) == [] and _linhas(c) == []


def test_caso9_estorno_aceito_pelo_webhook_e_nao_drenado_ja_bloqueia_a_compra(externo, monkeypatch):
    """Pelo webhook real: o 200 só sai com o marcador gravado. O processo morre antes de drenar o
    estorno; o RECEIVED drenado depois não concede nem comissiona."""
    from test_pix_rotas_billing import TOKEN, client

    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    pid = cobranca["asaas_payment_id"]
    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN", TOKEN)
    eid = f"evt_{uuid.uuid4().hex[:16]}"
    corpo = {"id": eid, "event": "PAYMENT_REFUNDED", "payment": {
        "id": pid, "externalReference": cobranca["external_reference"], "value": 499.0, "status": "REFUNDED"}}
    with monkeypatch.context() as m:
        m.setattr(dreno, "drenar_evento", lambda e: None)
        assert client.post("/billing/asaas/webhook", json=corpo,
                           headers={"asaas-access-token": TOKEN}).status_code == 200
    assert marcador(pid) == ["nosso"] and efeitos(pid) == set(), "o 200 saiu sem o marcador"
    entregar("PAYMENT_RECEIVED", cobranca)
    assert _grants_ativos(ind.uid) == [] and _compra(externo) == (0, 0, 0)
    assert _comissoes_vivas(cobranca) == []
    dreno.drenar_evento(eid)
    assert evento(eid)["processed_at"] is not None
    assert _grants_ativos(ind.uid) == [] and _comissoes_vivas(cobranca) == []


def test_caso10_estorno_no_meio_do_laco_para_antes_do_grant(user_id, externo, monkeypatch):
    """A compra passou pela D3 sem estorno; o estorno chega durante o `stripe_cancel`. O laço relê
    o marcador sob a trava e não roda `grant`, `ga4`, `capi`, `email` nem `afiliado`."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    real, estorno = EXECUTORES["stripe_cancel"], []

    def chega_o_estorno(cob, evt):
        estorno.append(_sem_drenar(monkeypatch, "PAYMENT_REFUNDED", cobranca))
        return real(cob, evt)

    monkeypatch.setitem(EXECUTORES, "stripe_cancel", chega_o_estorno)
    compra = entregar("PAYMENT_RECEIVED", cobranca)
    assert evento(compra)["processed_at"] is not None
    assert efeitos(cobranca["asaas_payment_id"]) == {"stripe_cancel"} and marcador(cobranca["asaas_payment_id"])
    assert _grants_ativos(user_id) == [] and _compra(externo) == (0, 0, 0)
    dreno.drenar_evento(estorno[0])
    assert ler(cobranca["id"])["status"] == "refunded" and _grants_ativos(user_id) == []


def test_caso14_reconciliar_grant_nao_reconcede_com_estorno_recebido(user_id, externo, monkeypatch):
    """Baixa desfeita que morreu antes de reabrir + estorno recebido e não drenado. Mesmo com o
    Asaas dizendo "pago" (o pior caso), o marcador é o que impede a reconcessão."""
    from test_pix_baixa_desfeita import _morre_antes_de_reabrir

    cobranca, _ = _morre_antes_de_reabrir(externo, monkeypatch, user_id)
    estorno = _sem_drenar(monkeypatch, "PAYMENT_REFUNDED", cobranca)
    externo["get_status"] = "RECEIVED"
    entregar("PAYMENT_RECEIVED", cobranca)
    assert _grants_ativos(user_id) == []
    dreno.drenar_evento(estorno)
    assert _grants_ativos(user_id) == [] and ler(cobranca["id"])["status"] == "refunded"


def test_recompra_depois_de_estorno_concede(user_id, externo):
    """POSITIVO: o marcador é do PAGAMENTO; cobrança nova do mesmo usuário concede."""
    conta(user_id, "free", None)
    velha = nova_cobranca(user_id)
    entregar("PAYMENT_RECEIVED", velha)
    entregar("PAYMENT_REFUNDED", velha)
    assert _grants_ativos(user_id) == []
    entregar("PAYMENT_RECEIVED", nova_cobranca(user_id))
    # o 2º e-mail cai no `recent_event_exists` de 1 h por usuário do efeito `email`, não no marcador
    assert len(_grants_ativos(user_id)) == 1 and (externo["ga4"], externo["capi"]) == (2, 2)


def test_caso12_parcial_nao_bloqueia_a_retentativa_da_compra(user_id, externo):
    """POSITIVO: o estorno parcial não grava o marcador e a retentativa completa a compra."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    externo["email_ok"] = False
    compra = entregar("PAYMENT_RECEIVED", cobranca)
    externo["email_ok"] = True
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    dreno.drenar_evento(compra)
    feito = efeitos(cobranca["asaas_payment_id"])
    assert evento(compra)["processed_at"] is not None and {"email", "afiliado"} <= feito
    assert marcador(cobranca["asaas_payment_id"]) == [] and len(_grants_ativos(user_id)) == 1



def _apagar_marcador(pid):
    """O estorno foi recebido pelo código de ANTES do deploy, que não gravava o marcador."""
    with get_conn() as conn:
        conn.execute("delete from pix_estornos_recebidos where asaas_payment_id = %s", (pid,))
        conn.commit()


def test_estorno_sem_referencia_antes_da_compra_nao_concede(externo, monkeypatch):
    """Caso 9 com o `PAYMENT_REFUNDED` sem `externalReference`: a recepção marca pelo id."""
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    estorno = _sem_drenar(monkeypatch, "PAYMENT_REFUNDED", cobranca, referencia="")
    entregar("PAYMENT_RECEIVED", cobranca)
    assert _grants_ativos(ind.uid) == [] and _compra(externo) == (0, 0, 0)
    dreno.drenar_evento(estorno)
    assert _grants_ativos(ind.uid) == [] and _comissoes_vivas(cobranca) == []


def test_estorno_sem_referencia_bloqueia_a_retentativa_da_compra(externo, monkeypatch):
    """Caso 6 com o estorno sem `externalReference`, recebido e não drenado."""
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    externo["email_ok"] = False
    compra = entregar("PAYMENT_RECEIVED", cobranca)
    externo["email_ok"] = True
    antes = (efeitos(cobranca["asaas_payment_id"]), _compra(externo))
    estorno = _sem_drenar(monkeypatch, "PAYMENT_REFUNDED", cobranca, referencia="")
    dreno.drenar_evento(compra)  # a varredura pega o mais velho primeiro
    assert (efeitos(cobranca["asaas_payment_id"]), _compra(externo)) == antes
    dreno.drenar_evento(estorno)
    assert _grants_ativos(ind.uid) == [] and _comissoes_vivas(cobranca) == []


def test_estorno_de_antes_do_deploy_drenado_a_mao_bloqueia_a_compra(externo, monkeypatch):
    """O passo de deploy: o `PAYMENT_REFUNDED` aberto de antes do deploy não tem marcador. Drenado
    à mão antes do `RECEIVED`, o dreno grava o marcador antes de tudo, mesmo com o `revoke` falhando."""
    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    pid = cobranca["asaas_payment_id"]
    externo["email_ok"] = False
    compra = entregar("PAYMENT_RECEIVED", cobranca)
    externo["email_ok"] = True
    estorno = _sem_drenar(monkeypatch, "PAYMENT_REFUNDED", cobranca)
    _apagar_marcador(pid)
    antes = _compra(externo)
    with monkeypatch.context() as m:
        m.setitem(EXECUTORES, "revoke", _quebra)
        dreno.drenar_evento(estorno)
    assert marcador(pid) == ["nosso"] and "revoke" not in efeitos(pid)
    dreno.drenar_evento(compra)
    assert _compra(externo) == antes and "afiliado" not in efeitos(pid)
    dreno.drenar_evento(estorno)
    assert _grants_ativos(ind.uid) == [] and _comissoes_vivas(cobranca) == []


def test_estorno_depois_do_grant_para_antes_do_proximo_efeito(user_id, externo, monkeypatch):
    """A releitura do laço vale a cada efeito de compra, não só antes do `grant`."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    real, estorno = EXECUTORES["grant"], []

    def grant_e_estorno(cob, evt):
        real(cob, evt)
        estorno.append(_sem_drenar(monkeypatch, "PAYMENT_REFUNDED", cobranca))

    monkeypatch.setitem(EXECUTORES, "grant", grant_e_estorno)
    entregar("PAYMENT_RECEIVED", cobranca)
    assert efeitos(cobranca["asaas_payment_id"]) == {"stripe_cancel", "grant"}
    assert _compra(externo) == (0, 0, 0)
    dreno.drenar_evento(estorno[0])
    assert _grants_ativos(user_id) == []


def test_caso13_d3_pelo_revoke_do_undone(user_id, externo):
    """A baixa desfeita de um `refunded_partial` grava `revoke` e nenhum marcador: a D3
    continua valendo pelo `revoke`."""
    conta(user_id, "free", None)
    cobranca = nova_cobranca(user_id)
    pid = cobranca["asaas_payment_id"]
    externo["email_ok"] = False
    compra = entregar("PAYMENT_RECEIVED", cobranca)
    externo["email_ok"] = True
    entregar("PAYMENT_PARTIALLY_REFUNDED", cobranca)
    entregar("PAYMENT_RECEIVED_IN_CASH_UNDONE", cobranca)  # o Asaas falso diz PENDING
    assert ler(cobranca["id"])["status"] == "refunded"
    assert "revoke" in efeitos(pid) and marcador(pid) == []
    antes = (efeitos(pid), _compra(externo))
    dreno.drenar_evento(compra)
    assert evento(compra)["processed_at"] is not None and (efeitos(pid), _compra(externo)) == antes


def test_estorno_drenado_inteiro_entre_a_d3_e_a_transicao(externo, monkeypatch):
    """O webhook do estorno chega e o dreno dele roda INTEIRO entre a D3 do `RECEIVED` e a transição.
    A transição para `paid` exige, na mesma instrução, que não haja estorno: a cobrança não vira
    `paid` sem acesso, e o boot não grava pagante."""
    from db.schema import BACKFILL_AFILIADO_PAGAMENTOS_SQL
    from test_pix_rotas_billing import TOKEN, client

    c = _afiliado()
    ind = _indicado(c)
    cobranca = _pix(ind)
    monkeypatch.setenv("ASAAS_WEBHOOK_TOKEN", TOKEN)
    real, estorno = dreno.conferir_valor, []

    def no_meio(*a, **k):
        if not estorno:
            estorno.append(f"evt_{uuid.uuid4().hex[:16]}")
            corpo = {"id": estorno[0], "event": "PAYMENT_REFUNDED", "payment": {
                "id": cobranca["asaas_payment_id"], "externalReference": cobranca["external_reference"]}}
            assert client.post("/billing/asaas/webhook", json=corpo,
                               headers={"asaas-access-token": TOKEN}).status_code == 200
        return real(*a, **k)

    monkeypatch.setattr(dreno, "conferir_valor", no_meio)
    compra = entregar("PAYMENT_RECEIVED", cobranca)
    assert evento(estorno[0])["processed_at"] is not None and evento(compra)["processed_at"] is not None
    # `canceled` (R3.3) é ORIGEM de pagamento: só a condição `sem_estorno` impede o `paid` aqui
    assert ler(cobranca["id"])["status"] == "canceled", "cobrança paga sobre estorno já drenado"
    assert _grants_ativos(ind.uid) == [] and _compra(externo) == (0, 0, 0) and _comissoes_vivas(cobranca) == []
    with get_conn() as conn:
        conn.execute(BACKFILL_AFILIADO_PAGAMENTOS_SQL)
        conn.commit()
    assert _linhas(c) == []
