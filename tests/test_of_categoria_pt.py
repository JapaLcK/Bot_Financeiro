"""#149 — a categoria do Open Finance chega em português, pelo mapa do dono.

Caminho de produção: `save_open_finance_sync` → import → correções (a ordem de
core/services/pluggy_sync.py), Postgres real. O espelho segue com o rótulo cru.

Controles negativos (CLAUDE.md §3), cada um vermelho quando o conserto sai:
- `_insert_of_shadow` voltar a gravar `r["category"]` → T2 cai;
- o import de cartão voltar a passar `r["category"]` → T3 cai;
- o sync do cartão (D) ou o de launches (E) sem o mapa → T5 cai;
- `garantir_no_catalogo` com `exigir_plano=True` → T7 cai;
- o snapshot sem a tradução → T8; o auto-merge com o rótulo cru → T9;
- tirar o guarda de `source` de QUALQUER um dos 3 leitores de aporte → T10 cai;
- o undo sem `garantir_no_catalogo` → T12; `categoria_pigbank` sem o " - " → T1;

A proteção da edição do cliente no sync (#712) está em tests/test_of_categoria_editada.py.
"""
from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

import db
from db.categories import _custom_categories_allowed
from db.household_budget import _spent_by_bucket
from db.open_finance_categories import PLUGGY_PARA_PIGBANK, _GRUPOS, categoria_pigbank
from tests._of_cash_helpers import conecta, dia, q, sync
from utils_text import CATEGORY_LABELS, is_internal_category

NOVAS = {"compras", "transferências", "renda", "serviços", "apostas", "taxas e juros", "empréstimos"}


def _tx(ident, valor, category, desc=None):
    return {"provider_transaction_id": ident, "description": desc or f"Loja {ident}",
            "amount": Decimal(str(valor)), "transaction_date": dia(10), "transacted_at": None,
            "category": category, "raw": {"operationType": "PIX"}}


def _banco(uid, txs, item="b", corrige=True):
    cid = conecta(uid, f"item-{item}-{uid}")
    sync(cid, uid, txs, corrige=corrige)
    return cid


def _cartao(uid, txs, cid=None):
    cid = cid or conecta(uid, f"item-c-{uid}")
    db.save_open_finance_sync(cid, [{
        "provider_account_id": f"cred-{cid}", "name": "Cartão", "type": "CREDIT",
        "currency": "BRL", "balance": Decimal("-100"), "raw": {}, "transactions": txs}])
    db.import_open_finance_credit(uid, cid)
    db.sync_imported_open_finance_updates(uid, cid)
    return cid


def _launch(uid, ident):
    rows = q("select l.id, l.categoria, l.is_internal_movement as interno, o.category as espelho "
             "from launches l join open_finance_transactions o on o.imported_launch_id = l.id "
             "where l.user_id=%s and o.provider_transaction_id=%s", (uid, ident), True)
    return rows[0]


def _compra(uid, ident):
    rows = q("select ct.id, ct.categoria from credit_transactions ct "
             "join open_finance_transactions o on o.imported_credit_tx_id = ct.id "
             "where ct.user_id=%s and o.provider_transaction_id=%s", (uid, ident), True)
    return rows[0] if rows else None


def test_t1_mapa_do_dono():
    # soma == dict pega rótulo repetido em dois grupos. 76 rótulos: a 77ª linha da
    # medição que originou o mapa era a das transações SEM categoria (-> "outros").
    assert sum(len(v) for v in _GRUPOS.values()) == len(PLUGGY_PARA_PIGBANK) == 76
    assert categoria_pigbank(" Shopping ") == "compras"
    assert categoria_pigbank("Online shopping") == "compras online"
    assert categoria_pigbank("Cinema, theater and concerts") == "lazer"
    assert categoria_pigbank("Fixed income") is None and categoria_pigbank(None) is None
    # variação por sufixo, como o prefixo do classificador; o exato ganha
    assert categoria_pigbank("Same person transfer - PIX") == "transferencia_interna"
    assert categoria_pigbank("Transfer - PIX") == "transferências"
    assert categoria_pigbank("Xyz - abc") is None
    valores = set(PLUGGY_PARA_PIGBANK.values())
    assert valores - NOVAS <= set(CATEGORY_LABELS.values())
    assert {v for v in valores if is_internal_category(v)} == {
        "transferencia_interna", "pagamento_fatura", "investimento_aporte"}


def test_t2_launch_em_portugues_e_espelho_cru(user_id):
    # corrige=False: mede o import sozinho (o sync traduziria o inglês por cima)
    _banco(user_id, [_tx("a", -10, "Shopping"), _tx("b", -11, "Online shopping"),
                     _tx("c", -12, "Groceries"), _tx("d", -13, "Coisa nova"), _tx("e", -14, None)],
           corrige=False)
    got = {i: _launch(user_id, i)["categoria"] for i in "abcde"}
    assert got == {"a": "compras", "b": "compras online", "c": "mercado", "d": "outros", "e": "outros"}
    assert _launch(user_id, "a")["espelho"] == "Shopping"


def test_t3_cartao_em_portugues(user_id):
    _cartao(user_id, [_tx("c1", 50, "Shopping"), _tx("c2", 60, "Coisa nova"),
                      _tx("c3", -200, "Credit card payment")])
    assert _compra(user_id, "c1")["categoria"] == "compras"
    assert _compra(user_id, "c2")["categoria"] is None
    assert _compra(user_id, "c3") is None  # pagamento de fatura não vira compra


def test_t5_ingles_antigo_vira_portugues_no_sync(user_id):
    txs = [_tx("a", -10, "Shopping"), _tx("f", -20, "Fixed income")]
    cid = _banco(user_id, txs)
    ctxs = [_tx("c1", 50, "Shopping"), _tx("c2", 60, "Fixed income")]
    ccid = _cartao(user_id, ctxs)
    # forma de antes do #149: rótulo cru gravado na linha
    q("update launches set categoria='Shopping' where id=%s", (_launch(user_id, "a")["id"],))
    q("update launches set categoria='Fixed income' where id=%s", (_launch(user_id, "f")["id"],))
    q("update credit_transactions set categoria='Shopping' where id=%s", (_compra(user_id, "c1")["id"],))
    q("update credit_transactions set categoria='Fixed income' where id=%s", (_compra(user_id, "c2")["id"],))
    sync(cid, user_id, txs)
    _cartao(user_id, ctxs, ccid)
    assert _launch(user_id, "a")["categoria"] == "compras"
    assert _compra(user_id, "c1")["categoria"] == "compras"
    # fora do mapa: "outros" no launch (como o insert), NULL no cartão; o espelho segue cru
    f = _launch(user_id, "f")
    assert (f["categoria"], f["interno"], f["espelho"]) == ("outros", True, "Fixed income")
    assert _compra(user_id, "c2")["categoria"] is None


def test_t6_internos_continuam_internos(user_id):
    _banco(user_id, [_tx("s", -100, "Same person transfer"), _tx("p", -200, "Credit card payment"),
                     _tx("i", -300, "Automatic investment"), _tx("t", -50, "Transfers")])
    got = {i: (_launch(user_id, i)["categoria"], _launch(user_id, i)["interno"]) for i in "spit"}
    assert got == {"s": ("transferencia_interna", True), "p": ("pagamento_fatura", True),
                   "i": ("investimento_aporte", True), "t": ("transferências", False)}
    assert _launch(user_id, "s")["espelho"] == "Same person transfer"


def test_t7_categoria_nova_nasce_no_catalogo(user_id):
    assert not _custom_categories_allowed(user_id)  # sem isso o teste mede o plano, não o conserto
    _banco(user_id, [_tx("a", -10, "Shopping"), _tx("c", -12, "Groceries")])
    nomes = [r["name"] for r in q("select name from user_categories where user_id=%s and not is_system",
                                  (user_id,), True)]
    assert "compras" in nomes
    assert "mercado" not in nomes


def test_t8_snapshot_em_portugues(user_id):
    _banco(user_id, [_tx("a", -10, "Shopping"), _tx("d", -13, "Coisa nova")])
    cats = {t["description"]: t["category"] for t in db.get_open_finance_snapshot(user_id)["transactions"]}
    assert cats == {"Loja a": "compras", "Loja d": None}


def test_t9_auto_merge_recebe_categoria_mapeada(user_id):
    q("insert into launches(user_id, tipo, valor, categoria, alvo, criado_em, posted_at, source, external_id) "
      "values (%s,'despesa',80,null,'Supermercado Dia',%s,%s,'ofx','ofx-1')",
      (user_id, dia(10), dia(10)))
    _banco(user_id, [_tx("m", -80, "Groceries", desc="Supermercado Dia")])
    rows = q("select categoria from launches where user_id=%s and source='ofx'", (user_id,), True)
    assert rows[0]["categoria"] == "mercado"
    assert q("select reconciliation_status from open_finance_transactions where provider_transaction_id='m'",
             (), True)[0]["reconciliation_status"] == "auto_merged"


def _aportes(uid):
    import frontend.finance_bot_websocket_custom as mono
    fin = asyncio.run(mono.get_financial_data(uid, year=2026, month=3))
    alloc = sum(b["total"] for b in fin["monthly_allocations"].values())
    pote = _spent_by_bucket(uid, 2026, 3).get("liberdade_financeira", 0.0)
    export = asyncio.run(mono._fetch_export_items(uid, date(2026, 3, 1), date(2026, 3, 31)))
    return alloc, pote, [i["valor"] for i in export if i["natureza"] == "aporte"]


def test_t10_aplicacao_do_of_fica_fora_dos_aportes(pro_user_id):
    user_id = pro_user_id  # free tem corte de histórico: março sumiria do dashboard
    _banco(user_id, [_tx("i", -300, "Automatic investment")])
    assert _launch(user_id, "i")["categoria"] == "investimento_aporte" and _launch(user_id, "i")["interno"]
    assert _aportes(user_id) == (0.0, 0.0, [])
    # "Investments" NÃO é interno: é gasto, e no pote cai onde caía antes (conforto)
    _banco(user_id, [_tx("x1", -500, "Investments")], item="x")
    assert _launch(user_id, "x1")["categoria"] == "investimento_aporte" and not _launch(user_id, "x1")["interno"]
    assert _spent_by_bucket(user_id, 2026, 3) == {"conforto": 500.0}
    # o mesmo no cartão (a fatura de uma compra de 10/03 fecha em abril)
    _cartao(user_id, [_tx("c1", 50, "Investments")])
    assert _compra(user_id, "c1")["categoria"] == "investimento_aporte"
    assert _spent_by_bucket(user_id, 2026, 4) == {"conforto": 50.0}
    # controle positivo: o aporte manual continua contando nos três
    q("insert into launches(user_id, tipo, valor, categoria, alvo, criado_em, is_internal_movement, source) "
      "values (%s,'despesa',70,'investimento_aporte','CDB',%s,true,'manual')", (user_id, dia(10)))
    assert _aportes(user_id) == (70.0, 70.0, [70.0])


def test_t12_desfazer_fusao_poe_a_categoria_no_catalogo(user_id):
    q("insert into launches(user_id, tipo, valor, categoria, alvo, criado_em, posted_at, source, external_id) "
      "values (%s,'despesa',80,'lazer','Loja Z',%s,%s,'ofx','ofx-z')", (user_id, dia(10), dia(10)))
    _banco(user_id, [_tx("z", -80, "Shopping", desc="Loja Z")])
    o = q("select id, reconciliation_status from open_finance_transactions where provider_transaction_id='z'",
          (), True)[0]
    assert o["reconciliation_status"] == "auto_merged"
    catalogo = "select name from user_categories where user_id=%s and not is_system"
    assert "compras" not in [r["name"] for r in q(catalogo, (user_id,), True)]  # a fusão não gravou
    assert db.undo_reconciliation(user_id, o["id"])["changed"]
    assert _launch(user_id, "z")["categoria"] == "compras"
    assert "compras" in [r["name"] for r in q(catalogo, (user_id,), True)]
