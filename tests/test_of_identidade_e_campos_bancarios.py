"""PR 3: identidade por conta, legado e campos bancários reversíveis, com Postgres real."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

import db
from db.connection import get_conn
from db.lancamentos import listar
from db.resumo_mes import totais
from tests._of_cash_helpers import conecta, q
from utils_date import _tz

JAN = date(2026, 1, 31)
FEV = date(2026, 2, 1)
ORIGINAL = datetime(2026, 1, 31, 9, 45, tzinfo=_tz())


def transacao(amount=-100, day=JAN, at=None):
    return dict(provider_transaction_id="igual", description="Mercado", amount=Decimal(amount),
                transaction_date=day, transacted_at=at, category="Groceries", raw={})


def conta(ident, kind="BANK", tx=None):
    return dict(provider_account_id=ident, name=ident, type=kind, currency="BRL", balance=900,
                raw={}, transactions=[tx or transacao(100 if kind == "CREDIT" else -100)])


def ciclo(uid, cid, accounts):
    db.save_open_finance_sync(cid, accounts)
    db.import_open_finance_launches(uid, cid)
    db.import_open_finance_credit(uid, cid)
    return db.sync_imported_open_finance_updates(uid, cid)


def lancamento(uid, lid):
    return q("select * from launches where user_id=%s and id=%s", (uid, lid), True)[0]


def espelho(uid):
    return q("""select t.*, a.provider_account_id from open_finance_transactions t
                 join open_finance_accounts a on a.id=t.account_id
                 join open_finance_connections c on c.id=a.connection_id
                where c.user_id=%s order by t.id""", (uid,), True)


def mes(uid, month):
    with get_conn() as conn, conn.cursor() as cur:
        inicio, fim = date(2026, month, 1), date(2026, month + 1, 1)
        return listar(cur, uid, inicio, fim)[0], totais(cur, uid, inicio, fim)


def fundida(uid, source="manual", confirm=True):
    cid = conecta(uid, f"item-{uid}")
    db.add_launch_and_update_balance(uid, "despesa", 100, "Mercado", "nota original",
                                     "alimentação", ORIGINAL)
    lid = q("select id from launches where user_id=%s", (uid,), True)[0]["id"]
    if source == "ofx":
        q("update launches set source='ofx' where user_id=%s and id=%s", (uid, lid))
    original = lancamento(uid, lid)
    ciclo(uid, cid, [conta("conta")])
    tx = espelho(uid)[0]
    if source == "manual":
        assert tx["reconciliation_status"] == "pending"
        if confirm:
            db.confirm_reconciliation(uid, tx["id"])
    else:
        assert tx["reconciliation_status"] == "auto_merged"
    return cid, lid, tx["id"], original


@pytest.mark.parametrize("kind", ["BANK", "CREDIT"])
def test_mesmo_id_em_duas_contas_e_outro_usuario(user_id, kind):
    uid = user_id
    cid = conecta(uid, f"item-{uid}")
    accounts = [conta("a", kind), conta("b", kind, transacao(70 if kind == "CREDIT" else -70))]
    ciclo(uid, cid, accounts)
    link = "imported_launch_id" if kind == "BANK" else "imported_credit_tx_id"
    ids = [r[link] for r in espelho(uid)]
    assert len(set(ids)) == 2 and all(ids)
    ciclo(uid, cid, accounts)
    assert [r[link] for r in espelho(uid)] == ids
    outro = uid + 1
    db.ensure_user(outro)
    other = conecta(outro, f"item-{outro}")
    ciclo(outro, other, accounts)
    assert set(ids).isdisjoint(r[link] for r in espelho(outro))
    table = "launches" if kind == "BANK" else "credit_transactions"
    assert q(f"select sum(valor) as total from {table} where user_id=%s", (uid,), True)[0]["total"] == 170
    if kind == "CREDIT":
        assert q("select sum(total) as total from credit_bills where user_id=%s", (uid,), True)[0]["total"] == 170


@pytest.mark.parametrize("kind", ["BANK", "CREDIT"])
@pytest.mark.parametrize("legacy", [False, True])
def test_reconexao_reutiliza_identidade_e_edicao(user_id, kind, legacy):
    uid = user_id
    cid = conecta(uid, f"item-{uid}")
    ciclo(uid, cid, [conta("estavel", kind)])
    link = "imported_launch_id" if kind == "BANK" else "imported_credit_tx_id"
    table = "launches" if kind == "BANK" else "credit_transactions"
    ident = espelho(uid)[0][link]
    if legacy:
        q(f"update {table} set external_id='igual' where user_id=%s and id=%s", (uid, ident))
    q(f"update {table} set categoria='viagem',categoria_editada=true,nota='minha nota' "
      "where user_id=%s and id=%s", (uid, ident))
    previous_card = q("select id from credit_cards where user_id=%s", (uid,), True)
    cid2 = conecta(uid, f"item-novo-{uid}")
    ciclo(uid, cid2, [conta("estavel", kind)])
    assert [r[link] for r in espelho(uid)] == [None, ident]
    row = q(f"select categoria,nota from {table} where user_id=%s and id=%s", (uid, ident), True)[0]
    assert (row["categoria"], row["nota"]) == ("viagem", "minha nota")
    assert q(f"select count(*) as n from {table} where user_id=%s", (uid,), True)[0]["n"] == 1
    # A conexão antiga já não possui a representação importada, inclusive no cleanup.
    ciclo(uid, cid, [conta("estavel", kind, transacao(-999))])
    db.disconnect_open_finance_connection(uid, cid)
    assert q(f"select valor from {table} where user_id=%s and id=%s", (uid, ident), True)[0]["valor"] == 100
    if kind == "CREDIT":
        assert q("select id from credit_cards where user_id=%s", (uid,), True) == previous_card
        ciclo(uid, cid2, [conta("estavel", kind, transacao(-25, FEV))])
        assert q("select sum(total) as total from credit_bills where user_id=%s", (uid,), True)[0]["total"] == -25


@pytest.mark.parametrize("kind", ["BANK", "CREDIT"])
def test_legado_colidido_recusa_sem_reparar(user_id, kind):
    uid = user_id
    cid = conecta(uid, f"item-{uid}")
    ciclo(uid, cid, [conta("a", kind)])
    link = "imported_launch_id" if kind == "BANK" else "imported_credit_tx_id"
    table = "launches" if kind == "BANK" else "credit_transactions"
    ident = espelho(uid)[0][link]
    q(f"update {table} set external_id='igual' where user_id=%s and id=%s", (uid, ident))
    db.save_open_finance_sync(cid, [conta("b", kind, transacao(-200))])
    other = espelho(uid)[1]
    q(f"update open_finance_transactions set {link}=%s where id=%s", (ident, other["id"]))
    before = q(f"select * from {table} where user_id=%s", (uid,), True)
    with pytest.raises(ValueError, match="OF_IDENTITY_AMBIGUOUS"):
        db.sync_imported_open_finance_updates(uid, cid)
    assert q(f"select * from {table} where user_id=%s", (uid,), True) == before


@pytest.mark.parametrize("source", ["manual", "ofx"])
@pytest.mark.parametrize("at", [None, datetime(2026, 2, 1, 14, 32, tzinfo=timezone.utc)])
def test_fundida_corrige_mes_sinal_hora_sem_mover_carteira_e_desfaz(user_id, source, at):
    uid = user_id
    cid, lid, txid, original = fundida(uid, source)
    # A primeira junção já aplica a data sem hora, mesmo sem transactions/updated.
    assert lancamento(uid, lid)["efeitos"]["time_known"] is False
    assert mes(uid, 1)[0][0]["hora"] is None
    db.update_launch_fields(uid, lid, categoria="lazer", alvo="minha descrição", nota="editada")
    before = q("select balance from accounts where user_id=%s", (uid,), True)
    ciclo(uid, cid, [conta("conta", tx=transacao(135, FEV, at))])
    bank = lancamento(uid, lid)
    assert (bank["valor"], bank["tipo"], bank["posted_at"]) == (135, "receita", FEV)
    assert bank["efeitos"]["delta_conta"] == original["efeitos"]["delta_conta"]
    assert bank["efeitos"]["of_original"]["valor"] == 100
    assert (bank["categoria"], bank["alvo"], bank["nota"]) == ("lazer", "minha descrição", "editada")
    assert mes(uid, 1)[1]["saiu"] == 0
    rows, totals = mes(uid, 2)
    assert (totals["entrou"], totals["saiu"]) == (135, 0)
    assert (rows[0]["dia"], rows[0]["hora"]) == (FEV, "11:32" if at else None)
    assert "data" not in rows[0]["pode"] and "valor" not in rows[0]["pode"]
    assert ciclo(uid, cid, [conta("conta", tx=transacao(135, FEV, at))])["launches_updated"] == 0
    assert q("select balance from accounts where user_id=%s", (uid,), True) == before
    db.undo_reconciliation(uid, txid)
    restored = lancamento(uid, lid)
    for field in ["valor", "tipo", "posted_at", "criado_em", "efeitos"]:
        assert restored[field] == original[field], field
    assert (restored["categoria"], restored["alvo"], restored["nota"]) == ("lazer", "minha descrição", "editada")
    assert q("select balance from accounts where user_id=%s", (uid,), True) == before
    assert mes(uid, 1)[1]["saiu"] == 100
    assert mes(uid, 2)[1]["entrou"] == 135


@pytest.mark.parametrize("at", [None, datetime(2026, 2, 1, 14, 32, tzinfo=timezone.utc)])
def test_sombra_corrigida_muda_lista_resumo_e_hora(user_id, at):
    cid = conecta(user_id, f"item-{user_id}")
    ciclo(user_id, cid, [conta("conta")])
    lid = espelho(user_id)[0]["imported_launch_id"]
    ciclo(user_id, cid, [conta("conta", tx=transacao(-150, FEV, at))])
    assert mes(user_id, 1)[1]["saiu"] == 0
    rows, total = mes(user_id, 2)
    assert (total["saiu"], rows[0]["hora"]) == (150, "11:32" if at else None)
    assert lancamento(user_id, lid)["criado_em"].astimezone(_tz()).date() == FEV
    assert "of_original" not in lancamento(user_id, lid)["efeitos"]


@pytest.mark.parametrize("exit_kind", ["disconnect", "deleted", "apagar", "pausar"])
def test_saida_apos_correcao_preserva_original_e_dinheiro(user_id, exit_kind):
    uid = user_id
    cid, lid, txid, original = fundida(uid)
    ciclo(uid, cid, [conta("conta", tx=transacao(-140, FEV))])
    db.update_launch_fields(uid, lid, categoria="viagem", nota="editada")
    if exit_kind == "disconnect":
        db.disconnect_open_finance_connection(uid, cid)
    elif exit_kind == "deleted":
        db.delete_open_finance_transactions(f"item-{uid}", ["igual"])
    elif exit_kind == "apagar":
        assert db.delete_launch_and_rollback(uid, lid)
        assert not q("select id from launches where user_id=%s and id=%s", (uid, lid), True)
        assert mes(uid, 2)[1]["saiu"] == 140
        assert q("select balance from accounts where user_id=%s", (uid,), True)[0]["balance"] == 0
        return
    else:
        db.pause_open_finance_connection(cid)
        assert lancamento(uid, lid)["valor"] == 140
        assert lancamento(uid, lid)["efeitos"]["of_original"]["valor"] == 100
        db.save_pluggy_open_finance_item(uid, {"id": f"item-{uid}", "status": "UPDATED",
                                             "connector": {"id": 612, "name": "Nubank"}})
        ciclo(uid, cid, [conta("conta", tx=transacao(-145, FEV))])
        db.undo_reconciliation(uid, txid)
    restored = lancamento(uid, lid)
    for field in ["valor", "tipo", "posted_at", "criado_em", "efeitos"]:
        assert restored[field] == original[field], field
    assert (restored["categoria"], restored["nota"]) == ("viagem", "editada")
    assert q("select balance from accounts where user_id=%s", (uid,), True)[0]["balance"] == -100


def test_reconectar_fundida_preserva_snapshot_e_desconectar_antiga(user_id):
    uid = user_id
    cid, lid, txid, original = fundida(uid)
    ciclo(uid, cid, [conta("conta", tx=transacao(-140, FEV))])
    cid2 = conecta(uid, f"novo-{uid}")
    ciclo(uid, cid2, [conta("conta", tx=transacao(-150, FEV))])
    mirrors = espelho(uid)
    assert [t["imported_launch_id"] for t in mirrors] == [None, lid]
    assert mirrors[1]["reconciliation_status"] == "confirmed"
    db.disconnect_open_finance_connection(uid, cid)
    assert lancamento(uid, lid)["valor"] == 150
    db.undo_reconciliation(uid, mirrors[1]["id"])
    assert lancamento(uid, lid)["efeitos"] == original["efeitos"]
    assert lancamento(uid, lid)["valor"] == 100


@pytest.mark.parametrize("action", ["desfazer", "apagar", "confirmar", "categoria"])
def test_correcao_concorrente_com_ciclo_de_vida(user_id, action):
    from tests.test_reconciliacao_concorrencia import _corre

    uid = user_id
    cid, lid, txid, original = fundida(uid, confirm=action != "confirmar")
    db.save_open_finance_sync(cid, [conta("conta", tx=transacao(-140, FEV))])
    operations = {
        "desfazer": lambda: db.undo_reconciliation(uid, txid),
        "apagar": lambda: db.delete_launch_and_rollback(uid, lid),
        "confirmar": lambda: db.confirm_reconciliation(uid, txid),
        "categoria": lambda: db.update_launch_fields(uid, lid, categoria="viagem"),
    }
    result = _corre(sync=lambda: db.sync_imported_open_finance_updates(uid, cid),
                    action=operations[action])
    assert not any(isinstance(v, Exception) for v in result.values()), result
    assert mes(uid, 2)[1]["saiu"] == 140
    if action == "desfazer":
        assert lancamento(uid, lid)["efeitos"] == original["efeitos"]
        assert lancamento(uid, lid)["valor"] == 100
    elif action == "apagar":
        assert not q("select id from launches where user_id=%s and id=%s", (uid, lid), True)
    else:
        assert lancamento(uid, lid)["valor"] == 140
        assert lancamento(uid, lid)["efeitos"]["of_original"]["valor"] == 100
        if action == "categoria":
            assert lancamento(uid, lid)["categoria"] == "viagem"
    assert q("select balance from accounts where user_id=%s", (uid,), True)[0]["balance"] == (
        0 if action == "apagar" else -100)


def test_reconexao_parcial_nao_oculta_transacao_antiga(user_id):
    uid = user_id
    cid = conecta(uid, f"item-{uid}")
    db.save_open_finance_sync(cid, [conta("estavel")])
    cid2 = conecta(uid, f"novo-{uid}")
    partial = conta("estavel")
    partial["transactions"] = []
    ciclo(uid, cid2, [partial])
    assert db.import_open_finance_launches(uid, cid)["inserted"] == 1
    assert espelho(uid)[0]["imported_launch_id"] is not None


@pytest.mark.parametrize("source", ["manual", "ofx"])
def test_primeira_fusao_ja_usa_valor_data_e_hora_bancarios(user_id, source):
    uid = user_id
    cid = conecta(uid, f"item-{uid}")
    db.add_launch_and_update_balance(uid, "despesa", 100, "Mercado", None, "alimentação", ORIGINAL)
    lid = q("select id from launches where user_id=%s", (uid,), True)[0]["id"]
    if source == "ofx":
        q("update launches set source='ofx' where user_id=%s and id=%s", (uid, lid))
    at = datetime(2026, 2, 1, 11, 32, tzinfo=_tz())
    db.save_open_finance_sync(cid, [conta("conta", tx=transacao("-100.01", FEV, at))])
    db.import_open_finance_launches(uid, cid)
    if source == "manual":
        db.confirm_reconciliation(uid, espelho(uid)[0]["id"])
    bank = lancamento(uid, lid)
    assert (bank["valor"], bank["posted_at"], bank["criado_em"]) == (Decimal("100.01"), FEV, at)
    assert bank["efeitos"]["of_original"]["valor"] == 100
    assert mes(uid, 1)[1]["saiu"] == 0
    assert mes(uid, 2)[0][0]["hora"] == "11:32"


@pytest.mark.parametrize("kind", ["BANK", "CREDIT"])
@pytest.mark.parametrize("exit_kind", ["disconnect", "deleted"])
def test_reconexao_na_janela_da_limpeza_antiga(user_id, monkeypatch, kind, exit_kind):
    from tests.test_reconciliacao_sombra_orfa import _na_janela_do_rollback

    uid = user_id
    cid = conecta(uid, f"item-{uid}")
    ciclo(uid, cid, [conta("estavel", kind)])
    link = "imported_launch_id" if kind == "BANK" else "imported_credit_tx_id"
    table = "launches" if kind == "BANK" else "credit_transactions"
    ident = espelho(uid)[0][link]
    cid2 = conecta(uid, f"novo-{uid}")
    _na_janela_do_rollback(monkeypatch,
                           lambda: ciclo(uid, cid2, [conta("estavel", kind)]))
    if exit_kind == "disconnect":
        db.disconnect_open_finance_connection(uid, cid)
    else:
        db.delete_open_finance_transactions(f"item-{uid}", ["igual"])
    assert q(f"select valor from {table} where user_id=%s and id=%s", (uid, ident), True)[0]["valor"] == 100
    assert [r[link] for r in espelho(uid)] == [ident]
    if kind == "CREDIT":
        assert q("select sum(total) as total from credit_bills where user_id=%s", (uid,), True)[0]["total"] == 100
