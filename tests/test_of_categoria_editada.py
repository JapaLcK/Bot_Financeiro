"""#712 — o sync do Open Finance não desfaz a categoria/interno que o cliente editou.

Sync de produção (`save_open_finance_sync` → import → correções), Postgres real.
As portas de edição (PATCH, WhatsApp, IA, lote) estão em
`test_of_categoria_editada_portas.py`.

Controles negativos (CLAUDE.md §3), medidos:
- os dois CASE de db/open_finance.py voltam a gravar o que o banco diz → A1, A2, A4, B1, B2, E;
- `_SET_CATEGORIA` sem `categoria_editada = true` → A1, A2, A4, D, E (lançamento);
- db/cards.py sem a marca → B1, B2, D, E (cartão);
- o alvo da editada sem `forcado` (o par da Carteira) → A4, A5;
- o CASE antigo (`categoria_editada and not forcado`): o par acaba e o interno fica preso → A5;
- o CASE sem `categoria is not distinct from` (o lido) → A7;
- o alvo da editada só `forcado`, sem `is_internal_category` → A6;
- o CASE trocado por guarda só em Python (o que o SELECT leu) → E;
- o auto-merge do import sem `not categoria_editada` → F[*-editada];
- `update_launch_fields` marcando também quando só a nota muda → A1 só a nota.
Positivos: A3, B3 e F[*-nao_editada] (linha não editada segue o banco), A4 (o
par da Carteira segue interno mesmo editado), A6 (categoria interna segue interna
depois do par), D (a marca de um usuário não vale para outro).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import db
import db.open_finance as of
from conftest import usuario_pagante
from tests._of_cash_helpers import caixa, conecta, dia, q, sync, tx  # noqa: F401  (fixture)
from tests.test_of_categoria_pt import _banco, _cartao, _launch, _tx


def _l(lid):
    return q("select categoria, valor, is_internal_movement as interno, categoria_editada as ed "
             "from launches where id=%s", (lid,), True)[0]


def _ct(uid, ident):
    return q("select ct.id, ct.categoria, ct.valor, ct.group_id, ct.categoria_editada as ed, b.total "
             "from credit_transactions ct join credit_bills b on b.id = ct.bill_id "
             "join open_finance_transactions o on o.imported_credit_tx_id = ct.id "
             "where ct.user_id=%s and o.provider_transaction_id=%s", (uid, ident), True)[0]


def _parcela(ident, n, mes, category="Shopping", valor=-50):
    t = _tx(ident, valor, category, desc="Loja Parcelada")
    t["transaction_date"] = dia(10, mes)
    t["raw"] = {"creditCardMetadata": {"installmentNumber": n, "totalInstallments": 3,
                                       "totalAmount": 150}}
    return t


# ─── A: conta ────────────────────────────────────────────────────────────────

def test_a1_categoria_editada_sobrevive_e_o_valor_segue_o_banco(user_id):
    cid = _banco(user_id, [_tx("a", -10, "Shopping")])
    lid = _launch(user_id, "a")["id"]
    assert db.update_launch_fields(user_id, lid, categoria="lazer")

    sync(cid, user_id, [_tx("a", -25, "Groceries")])

    r = _l(lid)
    assert (r["categoria"], r["valor"], r["ed"]) == ("lazer", Decimal("25"), True)


def test_a2_transferencia_interna_editada_nao_volta_a_ser_interna(user_id):
    cid = _banco(user_id, [_tx("s", -100, "Same person transfer - PIX")])
    lid = _launch(user_id, "s")["id"]
    assert (_l(lid)["categoria"], _l(lid)["interno"]) == ("transferencia_interna", True)
    assert db.update_launch_fields(user_id, lid, categoria="alimentação")

    # o valor muda junto: sem isso o UPDATE nem roda, e o teste não mede o CASE
    sync(cid, user_id, [_tx("s", -120, "Same person transfer - PIX")])

    r = _l(lid)
    assert (r["categoria"], r["interno"]) == ("alimentação", False)


def test_a3_nao_editada_segue_o_banco(user_id):
    cid = _banco(user_id, [_tx("s", -100, "Same person transfer - PIX")])
    lid = _launch(user_id, "s")["id"]

    sync(cid, user_id, [_tx("s", -100, "Groceries")])

    r = _l(lid)
    assert (r["categoria"], r["interno"], r["ed"]) == ("mercado", False, False)


def test_a4_par_da_carteira_segue_interno_mesmo_editado(caixa):
    uid = usuario_pagante()
    cid = conecta(uid, f"item-{uid}")
    sync(cid, uid, [tx("t1", -200, dia(10))])
    lid = _launch(uid, "t1")["id"]  # o lado do BANCO (o par da Carteira é outro launch)
    assert db.update_launch_fields(uid, lid, categoria="outros")
    assert _l(lid)["interno"] is False  # senão o teste não mede o sync

    sync(cid, uid, [tx("t1", -200, dia(10))])

    r = _l(lid)
    assert (r["categoria"], r["interno"]) == ("outros", True)


@pytest.mark.parametrize("cat,interno", [("outros", False), ("investimentos", True)],
                         ids=["a5_comum_volta", "a6_interna_fica"])
def test_a5_a6_par_da_carteira_acaba_e_o_interno_segue_a_categoria(caixa, cat, interno):
    """O banco reclassifica o saque para compra: o par acaba e o interno da linha
    editada volta a ser o que a categoria dela diz."""
    uid = usuario_pagante()
    cid = conecta(uid, f"item-{uid}")
    sync(cid, uid, [tx("t1", -200, dia(10))])
    lid = _launch(uid, "t1")["id"]
    assert db.update_launch_fields(uid, lid, categoria=cat)
    sync(cid, uid, [tx("t1", -200, dia(10))])
    assert _l(lid)["interno"] is True  # A4: enquanto o par vale

    sync(cid, uid, [tx("t1", -200, dia(10), op="CARTAO", desc="Compra", category="Shopping")])

    assert q("select status from of_cash_links where user_id=%s", (uid,), True) == [
        {"status": "estornado"}]
    r = _l(lid)
    assert (r["categoria"], r["interno"]) == (cat, interno)


def test_a1_so_a_nota_nao_marca(user_id):
    cid = _banco(user_id, [_tx("a", -10, "Shopping")])
    assert db.update_launch_fields(user_id, _launch(user_id, "a")["id"], nota="x")

    sync(cid, user_id, [_tx("a", -10, "Groceries")])

    assert (_launch(user_id, "a")["categoria"], _l(_launch(user_id, "a")["id"])["ed"]) == (
        "mercado", False)


# ─── B: cartão ───────────────────────────────────────────────────────────────

def test_b1_compra_editada_sobrevive_e_valor_e_fatura_seguem_o_banco(user_id):
    cid = _cartao(user_id, [_tx("c1", -50, "Shopping")])
    ct = _ct(user_id, "c1")
    assert ct["total"] == Decimal("50")
    assert db.update_credit_transaction_fields(user_id, ct["id"], categoria="lazer")

    _cartao(user_id, [_tx("c1", -80, "Groceries")], cid)

    ct = _ct(user_id, "c1")
    assert (ct["categoria"], ct["valor"], ct["ed"], ct["total"]) == (
        "lazer", Decimal("80"), True, Decimal("80"))


def test_b1_so_a_nota_nao_marca(user_id):
    cid = _cartao(user_id, [_tx("c1", -50, "Shopping")])
    assert db.update_credit_transaction_fields(user_id, _ct(user_id, "c1")["id"], nota="x")

    _cartao(user_id, [_tx("c1", -50, "Groceries")], cid)

    assert (_ct(user_id, "c1")["categoria"], _ct(user_id, "c1")["ed"]) == ("mercado", False)


def test_b2_parcelada_editada_sobrevive(user_id):
    cid = _cartao(user_id, [_parcela("p1", 1, 3), _parcela("p2", 2, 4)])
    gid = _ct(user_id, "p1")["group_id"]
    assert gid and gid == _ct(user_id, "p2")["group_id"]
    assert db.update_installment_group_meta(user_id, str(gid), categoria="viagem")

    _cartao(user_id, [_parcela("p1", 1, 3, "Groceries", -60), _parcela("p2", 2, 4, "Groceries", -60)],
            cid)  # valor junto: senão o UPDATE nem roda e o teste não mede o CASE

    assert [(_ct(user_id, i)["categoria"], _ct(user_id, i)["valor"]) for i in ("p1", "p2")] == [
        ("viagem", 60), ("viagem", 60)]


def test_b3_compra_nao_editada_segue_o_banco(user_id):
    cid = _cartao(user_id, [_tx("c1", -50, "Shopping")])

    _cartao(user_id, [_tx("c1", -50, "Groceries")], cid)

    assert _ct(user_id, "c1")["categoria"] == "mercado"


# ─── D: isolamento ───────────────────────────────────────────────────────────

def test_d_a_marca_de_um_usuario_nao_vale_para_outro():
    ua, ub = usuario_pagante(), usuario_pagante()
    banco = {u: _banco(u, [_tx("x", -10, "Shopping")]) for u in (ua, ub)}
    cartao = {u: _cartao(u, [_tx("cx", -50, "Shopping")]) for u in (ua, ub)}
    la, cta = _launch(ua, "x")["id"], _ct(ua, "cx")["id"]

    # B tentando editar a linha de A: recusa, e a linha de A fica intacta
    assert db.update_launch_fields(ub, la, categoria="viagem") is False
    assert db.update_credit_transaction_fields(ub, cta, categoria="viagem") is False
    assert (_l(la)["categoria"], _l(la)["ed"]) == ("compras", False)
    assert (_ct(ua, "cx")["categoria"], _ct(ua, "cx")["ed"]) == ("compras", False)

    assert db.update_launch_fields(ua, la, categoria="lazer")
    assert db.update_credit_transaction_fields(ua, cta, categoria="lazer")
    for u in (ua, ub):
        sync(banco[u], u, [_tx("x", -10, "Groceries")])
        _cartao(u, [_tx("cx", -50, "Groceries")], cartao[u])

    assert (_launch(ua, "x")["categoria"], _ct(ua, "cx")["categoria"]) == ("lazer", "lazer")
    assert (_launch(ub, "x")["categoria"], _ct(ub, "cx")["categoria"]) == ("mercado", "mercado")


# ─── E: edição commitada entre o SELECT e o UPDATE do sync ───────────────────

def _edita_no_meio(monkeypatch, edita):
    """Na 1ª tradução do sync (depois do SELECT, antes do UPDATE), o cliente edita
    em OUTRA conexão, com commit."""
    original, feito = of.categoria_pigbank, []

    def no_meio(category):
        if not feito:
            feito.append(1)
            edita()
        return original(category)

    monkeypatch.setattr(of, "categoria_pigbank", no_meio)
    return feito


def test_e_edicao_no_meio_do_sync_lancamento(user_id, monkeypatch):
    cid = _banco(user_id, [_tx("a", -10, "Shopping")])
    lid = _launch(user_id, "a")["id"]
    sync(cid, user_id, [_tx("a", -10, "Groceries")], corrige=False)  # o banco mudou no espelho
    feito = _edita_no_meio(monkeypatch, lambda: db.update_launch_fields(user_id, lid, categoria="lazer"))

    db.sync_imported_open_finance_updates(user_id, cid)

    assert feito and _l(lid)["categoria"] == "lazer"


def test_a7_edicao_no_meio_do_sync_nao_perde_o_interno(user_id, monkeypatch):
    """O sync leu 'outros' (não interno); a edição concorrente grava 'investimentos'
    (interno). O UPDATE não reescreve o interno com o alvo da categoria velha."""
    cid = _banco(user_id, [_tx("a", -10, "Shopping")])
    lid = _launch(user_id, "a")["id"]
    assert db.update_launch_fields(user_id, lid, categoria="outros")
    sync(cid, user_id, [_tx("a", -12, "Shopping")], corrige=False)  # valor muda: o UPDATE roda
    feito = _edita_no_meio(
        monkeypatch, lambda: db.update_launch_fields(user_id, lid, categoria="investimentos"))

    db.sync_imported_open_finance_updates(user_id, cid)

    r = _l(lid)
    assert feito and (r["categoria"], r["interno"], r["valor"]) == ("investimentos", True, 12)


def test_e_edicao_no_meio_do_sync_cartao(user_id, monkeypatch):
    cid = _cartao(user_id, [_tx("c1", -50, "Shopping")])
    ctid = _ct(user_id, "c1")["id"]
    db.save_open_finance_sync(cid, [{
        "provider_account_id": f"cred-{cid}", "name": "Cartão", "type": "CREDIT", "currency": "BRL",
        "balance": Decimal("-100"), "raw": {}, "transactions": [_tx("c1", -50, "Groceries")]}])
    feito = _edita_no_meio(
        monkeypatch, lambda: db.update_credit_transaction_fields(user_id, ctid, categoria="lazer"))

    db.sync_imported_open_finance_updates(user_id, cid)

    assert feito and _ct(user_id, "c1")["categoria"] == "lazer"


# ─── F: auto-merge do import num lançamento não-OF (`ofx`, recorrente em conta) ─

@pytest.mark.parametrize("editada", [True, False], ids=["editada", "nao_editada"])
@pytest.mark.parametrize("origem", ["ofx", "of_recurring"])
def test_f_auto_merge_respeita_outros_escolhido_pelo_cliente(user_id, origem, editada):
    efeitos = '{"of_recurring": true}' if origem == "of_recurring" else "{}"
    lid = q("insert into launches(user_id, tipo, valor, categoria, alvo, criado_em, posted_at, "
            "source, efeitos) values (%s,'despesa',80,'outros','Supermercado Dia',%s,%s,%s,%s) "
            "returning id", (user_id, dia(10), dia(10), "ofx" if origem == "ofx" else "manual",
                             efeitos), True)[0]["id"]
    if editada:
        assert db.update_launch_fields(user_id, lid, categoria="outros")

    _banco(user_id, [_tx("m", -80, "Groceries", desc="Supermercado Dia")])

    assert q("select reconciliation_status as s from open_finance_transactions "
             "where imported_launch_id=%s", (lid,), True) == [{"s": "auto_merged"}]
    assert _l(lid)["categoria"] == ("outros" if editada else "mercado")
