"""Declaração financeira só é confirmada por prova compatível no extrato."""
from datetime import timedelta
from decimal import Decimal

import pytest
import db
from db.bank_movements import (
    bank_movement_summary, confirm_bank_movement, list_bank_movements,
    migrate_legacy_bank_movements,
)
from core.services import funding
from utils_date import _tz
from datetime import datetime


def _bank(uid, balance=1000):
    connection = db.save_pluggy_open_finance_item(uid, {
        "id": f"decl-{uid}", "connector": {"id": 612, "name": "Nubank"}, "status": "UPDATED"})
    _sync(connection["id"], balance)
    account = db.list_bank_accounts(uid)[0]
    return connection["id"], {"kind": "bank", "of_account_id": account["id"], "label": account["label"]}


def _sync(cid, balance, *transactions):
    return db.save_open_finance_sync(cid, [{"provider_account_id": f"account-{cid}",
        "name": "Conta", "type": "BANK", "currency": "BRL", "balance": balance,
        "transactions": list(transactions)}])


def _tx(amount=-500, txid="tx1", category="Fixed income", days=0):
    return {"provider_transaction_id": txid, "description": "Aplicação ou resgate",
            "amount": amount, "transaction_date": datetime.now(_tz()).date()+timedelta(days=days),
            "category": category}


def _deposit(uid, source, amount=500):
    db.create_pocket(uid, "viagem")
    return db.pocket_deposit_from_account(uid, "viagem", amount, funding_source=source)[0]


def test_declaracao_antes_do_extrato_persiste_sync_vazio_e_confirma(user_id):
    cid, source = _bank(user_id)
    lid = _deposit(user_id, source)
    assert bank_movement_summary(user_id)["pending_count"] == 1
    assert db.pending_bank_outflows(user_id) == {source["of_account_id"]: Decimal(500)}
    _sync(cid, 1000)
    assert bank_movement_summary(user_id)["patrimony_status"] == "to_review"
    assert db.pending_bank_outflows(user_id)[source["of_account_id"]] == 500
    _sync(cid, 500, _tx())
    db.import_open_finance_launches(user_id)
    assert bank_movement_summary(user_id)["pending_count"] == 0
    assert db.pending_bank_outflows(user_id) == {}
    assert db.get_balance(user_id) == 0
    rows = db.list_launches(user_id, limit=50)
    assert len([x for x in rows if float(x["valor"]) == 500]) == 1
    assert next(x for x in rows if x["id"] == lid)["tipo"] == "deposito_caixinha"
    _sync(cid, 500, _tx())
    db.import_open_finance_launches(user_id)
    assert len(db.list_launches(user_id, limit=50)) == len(rows)


def test_extrato_antes_declaracao_saldo_pos_aporte_nao_recusa_fato(user_id):
    cid, source = _bank(user_id, 100)
    _sync(cid, 100, _tx())
    db.import_open_finance_launches(user_id)
    assert funding.resolve(user_id, 500)["source"]["of_account_id"] == source["of_account_id"]
    _deposit(user_id, source)
    assert bank_movement_summary(user_id)["pending_count"] == 0
    assert len([x for x in db.list_launches(user_id, limit=50) if float(x["valor"]) == 500]) == 1
    with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
        db.pocket_deposit_from_account(user_id, "viagem", 500, funding_source=source)


@pytest.mark.parametrize("category", ["Credit card payment", "Same person transfer"])
def test_tipo_interno_nao_bypassa_saldo(category, user_id):
    cid, source = _bank(user_id, 100)
    _sync(cid, 100, _tx(category=category))
    assert "insufficient" in funding.resolve(user_id, 500)
    with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
        _deposit(user_id, source)


def test_ambiguidade_e_confirmacao_manual_idempotente(user_id):
    cid, source = _bank(user_id)
    lid = _deposit(user_id, source)
    _sync(cid, 0, _tx(txid="a"), _tx(txid="b"))
    db.import_open_finance_launches(user_id)
    pending = list_bank_movements(user_id)
    assert len(pending) == 1
    assert len(pending[0]["candidates"]) == 2
    candidate = pending[0]["candidates"][0]["id"]
    confirm_bank_movement(user_id, lid, candidate)
    confirm_bank_movement(user_id, lid, candidate)
    assert bank_movement_summary(user_id)["pending_count"] == 0


def test_correcao_extrato_invalida_prova_sem_reescrever_lote(user_id):
    cid, source = _bank(user_id)
    _deposit(user_id, source)
    _sync(cid, 500, _tx())
    assert bank_movement_summary(user_id)["pending_count"] == 0
    _sync(cid, 400, _tx(-600))
    db.import_open_finance_launches(user_id)
    assert bank_movement_summary(user_id)["pending_count"] == 1
    assert float(db.list_pockets(user_id)[0]["balance"]) == 500
    assert len([x for x in db.list_launches(user_id, limit=50) if x["source"] == "open_finance"]) == 1


def test_excluir_transacao_bancaria_preserva_declaracao(user_id):
    cid, source = _bank(user_id)
    _deposit(user_id, source)
    _sync(cid, 500, _tx())
    db.delete_open_finance_transactions(f"decl-{user_id}", ["tx1"])
    assert bank_movement_summary(user_id)["pending_count"] == 1
    assert float(db.list_pockets(user_id)[0]["balance"]) == 500


def test_fatura_nao_e_candidata_manual(user_id):
    cid, source = _bank(user_id)
    _deposit(user_id, source)
    _sync(cid, 500, _tx(category="Credit card payment"))
    assert list_bank_movements(user_id)[0]["candidates"] == []


def test_transferencia_generica_exige_confirmacao_manual(user_id):
    cid, source = _bank(user_id)
    lid = _deposit(user_id, source)
    _sync(cid, 500, _tx(category="Same person transfer"))
    pending = list_bank_movements(user_id)
    assert len(pending) == 1
    confirm_bank_movement(user_id, lid, pending[0]["candidates"][0]["id"])
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select confirmation_method from bank_movement_declarations where user_id=%s", (user_id,))
        assert cur.fetchone()["confirmation_method"] == "manual"


def test_resgate_usa_liquido_nao_bruto(user_id):
    cid, source = _bank(user_id)
    _deposit(user_id, source)
    _sync(cid, 500, _tx())
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("update pocket_lots set balance=600 where user_id=%s", (user_id,))
        conn.commit()
    result = db.pocket_withdraw_to_account(user_id, "viagem", None, withdraw_all=True)
    lid, _, _, _, taxes, _ = result
    assert taxes["net"] < taxes["gross"]
    _sync(cid, 1100, _tx(taxes["gross"], "gross"))
    pending = list_bank_movements(user_id)
    assert pending[0]["launch_id"] == lid
    assert pending[0]["candidates"] == []
    _sync(cid, 500+taxes["net"], _tx(taxes["net"], "net"))
    assert bank_movement_summary(user_id)["pending_count"] == 0


def test_falha_no_vinculo_reverte_declaracao_e_lote(user_id, monkeypatch):
    import db.bank_movements as movement
    cid, source = _bank(user_id)
    _sync(cid, 500, _tx())
    db.import_open_finance_launches(user_id)
    db.create_pocket(user_id, "viagem")
    original = movement._bind
    def falha(*args):
        original(*args)
        raise RuntimeError("falha injetada depois do vínculo")
    monkeypatch.setattr(movement, "_bind", falha)
    with pytest.raises(RuntimeError, match="injetada"):
        db.pocket_deposit_from_account(user_id, "viagem", 500, funding_source=source)
    assert float(db.list_pockets(user_id)[0]["balance"]) == 0
    assert len([l for l in db.list_launches(user_id, limit=50) if l["source"] == "open_finance"]) == 1
    assert bank_movement_summary(user_id)["pending_count"] == 0


def test_migracao_idempotente_json_legado_invalido(user_id):
    from psycopg.types.json import Jsonb
    cid, source = _bank(user_id)
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""insert into launches(user_id,tipo,valor,efeitos,is_internal_movement)
                       values(%s,'resgate_investimento',100,%s,true) returning id""",
                    (user_id, Jsonb({"funding_source": {"kind":"bank","of_account_id":"abc"}, "tax_summary": ["inválido"]})))
        lid = cur.fetchone()["id"]
        migrate_legacy_bank_movements(cur)
        migrate_legacy_bank_movements(cur)
        cur.execute("select * from bank_movement_declarations where user_id=%s", (user_id,))
        rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["launch_id"] == lid
        assert rows[0]["amount"] is None
        assert rows[0]["requires_review"]
        conn.commit()
    assert list_bank_movements(user_id)[0]["candidates"] == []


def test_duas_declaracoes_nao_consumem_mesma_prova(user_id):
    cid, source = _bank(user_id, 1000)
    _deposit(user_id, source)
    db.pocket_deposit_from_account(user_id, "viagem", 500, funding_source=source)
    _sync(cid, 0, _tx())
    assert bank_movement_summary(user_id)["pending_count"] == 2


def test_prova_de_outro_usuario_nao_e_selecionavel(user_id):
    from tests.conftest import promote_to_pro
    other = user_id + 983471
    db.ensure_user(other)
    promote_to_pro(other)
    cid, source = _bank(user_id)
    other_cid, _ = _bank(other)
    lid = _deposit(user_id, source)
    _sync(other_cid, 500, _tx())
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""select t.id from open_finance_transactions t
                    join open_finance_accounts a on a.id=t.account_id
                    join open_finance_connections c on c.id=a.connection_id where c.user_id=%s""", (other,))
        txid = cur.fetchone()["id"]
    with pytest.raises(LookupError):
        confirm_bank_movement(user_id, lid, txid)
    assert bank_movement_summary(user_id)["pending_count"] == 1


def test_concorrencia_nao_reutiliza_prova_para_bypass(user_id):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    cid, source = _bank(user_id, 100)
    _sync(cid, 100, _tx())
    db.import_open_finance_launches(user_id)
    db.create_pocket(user_id, "viagem")
    barrier = Barrier(2)
    def declara():
        barrier.wait(timeout=5)
        try:
            db.pocket_deposit_from_account(user_id, "viagem", 500, funding_source=source)
            return "ok"
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(declara), pool.submit(declara)
        result = sorted([first.result(timeout=10), second.result(timeout=10)])
    assert result == ["INSUFFICIENT_ACCOUNT", "ok"]
    assert float(db.list_pockets(user_id)[0]["balance"]) == 500


def test_vinculo_manual_antigo_nao_e_roubado(user_id):
    cid, source = _bank(user_id, 100)
    manual_id = db.add_launch_and_update_balance(user_id, "receita", 500, "salário", "manual")[0]
    _sync(cid, 100, _tx())
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""update open_finance_transactions t set imported_launch_id=%s,match_launch_id=%s
                    from open_finance_accounts a,open_finance_connections c
                    where a.id=t.account_id and c.id=a.connection_id and c.user_id=%s""",
                    (manual_id,manual_id,user_id))
        conn.commit()
    # Exerce explicitamente a conta bancária; Carteira tem dinheiro, mas não é esta origem.
    with pytest.raises(ValueError, match="INSUFFICIENT_ACCOUNT"):
        _deposit(user_id, source)


def test_api_confere_com_csrf_e_isolamento(user_id):
    import frontend.finance_bot_websocket_custom as dashboard
    from fastapi.testclient import TestClient
    cid, source = _bank(user_id)
    lid = _deposit(user_id, source)
    _sync(cid, 500, _tx(category="Same person transfer"))
    client = TestClient(dashboard.app)
    assert client.get(f"/open-finance/{user_id}/movements").status_code in (401,403)
    client.cookies.set(dashboard.AUTH_COOKIE_NAME, dashboard._make_jwt(user_id, "movements@test.com"))
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, dashboard.make_dashboard_token(user_id, hours=1))
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, "movement-test")
    response = client.get(f"/open-finance/{user_id}/movements")
    assert response.status_code == 200, response.text
    txid = response.json()["movements"][0]["candidates"][0]["id"]
    body = {"launch_id":lid,"transaction_id":txid}
    assert client.post(f"/open-finance/{user_id}/movements/confirm", json=body).status_code == 403
    headers = {dashboard.CSRF_HEADER_NAME: "movement-test"}
    assert client.post(f"/open-finance/{user_id+1}/movements/confirm", json=body, headers=headers).status_code in (401,403)
    result = client.post(f"/open-finance/{user_id}/movements/confirm", json=body, headers=headers)
    assert result.status_code == 200, result.text
    assert bank_movement_summary(user_id)["pending_count"] == 0
