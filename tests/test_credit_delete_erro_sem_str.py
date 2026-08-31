"""`core/handlers/credit.py`: apagar compra/parcelamento no cartão não pode
devolver `str(e)` ao usuário.

Mesma ação do usuário que as tools `undo_credit_transaction` /
`undo_installment_group` do `/ai/chat` (`core/services/ai_chat/tools/launches.py`)
— porta diferente, e até aqui comportamento oposto: a tool logava a causa e
mandava uma frase genérica, o handler do WhatsApp interpolava o texto do
psycopg (`DETAIL: Key (…)=(…)`) na resposta.

As duas funções de `db/cards.py` NÃO levantam exceção prevista: "não achei"
volta como `None` e já é tratado antes do `try`. Então tudo que cai no `except`
é inesperado — e aí "tenta de novo" é o conselho CERTO (o oposto do
`resolve_delete`, onde `LookupError`/`ValueError` são permanentes).

Controle negativo (medido): repondo `return f"❌ Erro ao apagar a compra
CC{ct_id}: {e}"` os dois testes de vazamento falham.
"""
import logging

from core.handlers import credit as h_credit


def _explode(*a, **k):
    raise RuntimeError("DETAIL: Key (descricao)=(farmacia segredo) valor=77,50")


def test_apagar_compra_cc_nao_vaza_str_da_excecao(user_id, monkeypatch, caplog):
    monkeypatch.setattr(h_credit, "undo_credit_transaction", _explode)

    with caplog.at_level(logging.WARNING, logger="core.handlers.pending"):
        resp = h_credit.handle(user_id, "apagar cc12")

    assert resp is not None
    assert "segredo" not in resp and "DETAIL" not in resp and "77,50" not in resp, resp
    assert "CC12" in resp, "o usuário precisa saber QUAL compra falhou"
    assert "de novo" in resp.lower(), "causa inesperada sem ação deixa o usuário parado"
    linhas = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("undo_credit_transaction:")]
    assert linhas == [
        f"undo_credit_transaction: falha user_id={user_id} credit_tx_id=12 "
        f"causa=RuntimeError sqlstate=None"
    ], linhas


def test_desfazer_parcelamento_nao_vaza_str_da_excecao(user_id, monkeypatch, caplog):
    # `_extract_installment_group_id` resolve o código PCxxxx; força um grupo.
    monkeypatch.setattr(h_credit, "_extract_installment_group_id", lambda uid, t: "grp-1")
    monkeypatch.setattr(h_credit, "undo_installment_group", _explode)

    with caplog.at_level(logging.WARNING, logger="core.handlers.pending"):
        resp = h_credit.handle(user_id, "apagar pcabcd1234")

    assert resp is not None
    assert "segredo" not in resp and "DETAIL" not in resp and "77,50" not in resp, resp
    assert "de novo" in resp.lower(), resp
    assert any(r.getMessage().startswith("undo_installment_group: falha")
               for r in caplog.records)
