"""PR-B (ADR 0006), G-B2: com a flag LIGADA o cartão nunca declara (B1/B2 intactos).

"banco" vira declaração só para Pix, débito e transferência. Compra no cartão sincronizado segue sem gravar (T8: só o
cartão "chega sozinho"), cartão manual segue indo para a fatura, o pagamento manual de fatura de cartão do banco segue
recusado (`CartaoDoBanco`) e o parcelado nunca declara.

Controle negativo: `declara` verdadeiro dentro de `_compra_no_credito` (ou o `no_cartao` fora da condição do `resolver`)
deixa vermelhos os de compra no cartão. Positivo: `test_cartao_manual_*` (continua gravando na fatura) e o Pix da
conversa (`test_declaracao_b_conversa.py`).
"""
from __future__ import annotations

import pytest

import db
from tests._declaracao_b import ana, carteira, declaracoes, liga, manda_wamid, n_lancamentos  # noqa: F401
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from tests._pendencia_credito_helpers import diga
from tests.test_fatura_cartao_do_banco_sem_pagar import banco, pagamentos, pago, recusou
from tests.test_of_fatura_quitada_pelo_banco import rodar  # noqa: F401 (fixture)

T8 = "as compras no cartão de crédito conectado chegam sozinhas pelo Open Finance"


def _compras(uid) -> int:
    from tests._of_cash_helpers import q
    return q("select count(*) as n from credit_transactions where user_id=%s", (uid,), True)[0]["n"]


def test_pagar_fatura_do_cartao_do_banco_segue_recusado(uid_pro, rodar, ia_fora, liga):
    banco(uid_pro, rodar)
    antes = (pago(uid_pro), db.get_balance(uid_pro))
    for frase in ("pagar fatura nubank mastercard", "paguei a fatura do nubank mastercard pelo banco"):
        r = diga(uid_pro, frase)
        assert "registrada pelo banco" not in r, r
    assert recusou(diga(uid_pro, "pagar fatura nubank mastercard"))
    assert (pagamentos(uid_pro), pago(uid_pro), db.get_balance(uid_pro)) == (0, antes[0], antes[1])
    assert declaracoes(uid_pro) == [] and n_lancamentos(uid_pro) == 0


def test_compra_no_cartao_sincronizado_nao_declara_e_diz_t8(uid_pro, rodar, ia_fora, liga):
    banco(uid_pro, rodar)
    compras, lancamentos = _compras(uid_pro), n_lancamentos(uid_pro)
    r = manda_wamid(uid_pro, "gastei 50 no mercado no cartão")[0]
    assert T8 in r and "Pix, cartão e débito" not in r, r
    assert (_compras(uid_pro), n_lancamentos(uid_pro)) == (compras, lancamentos) and declaracoes(uid_pro) == []


def test_resposta_cartao_a_pergunta_nao_declara(ana, ia_fora, liga):
    manda_wamid(ana, "gastei 50 no mercado")
    r = manda_wamid(ana, "cartão")[0]
    assert T8 in r and declaracoes(ana) == [] and n_lancamentos(ana) == 0


def test_parcelado_nunca_declara(ana, liga):
    r = manda_wamid(ana, "comprei uma geladeira de 3000 em 10x no cartão")[0]
    assert "registrada pelo banco" not in r, r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and carteira(ana) == 200.0


def test_cartao_manual_segue_indo_para_a_fatura(ana, liga):
    """Positivo do grupo: a flag não tira o cartão manual da fatura."""
    db.set_default_card(ana, db.create_card(ana, "Manual", closing_day=10, due_day=17))
    manda_wamid(ana, "gastei 50 no mercado")
    r = manda_wamid(ana, "cartão")[0]
    assert "Compra no Crédito Registrada" in r, r
    assert _compras(ana) == 1 and declaracoes(ana) == [] and n_lancamentos(ana) == 0
    manda_wamid(ana, "gastei 30 no uber no cartão")
    assert _compras(ana) == 2 and declaracoes(ana) == []


def test_recibo_por_imagem_respondido_com_cartao_nao_declara(ana, liga):
    """O fluxo `entities` (recibo por imagem, IA no WhatsApp) também não declara cartão: segue `msg_banco` com T8."""
    db.set_pending_action(ana, "confirm_media_launch", {
        "tipo": "despesa", "valor": 50.0, "alvo": "mercado", "categoria": "mercado", "data": None,
        "platform": "whatsapp", "text": "gastei 50,00 mercado"})
    manda_wamid(ana, "sim")
    r = manda_wamid(ana, "cartão")[0]
    assert T8 in r and declaracoes(ana) == [] and n_lancamentos(ana) == 0, r
