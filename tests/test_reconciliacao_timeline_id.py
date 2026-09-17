"""`reconciliation_of_tx_id` no histórico (db/analytics.list_history) — o id da
transação OF do outro lado da fusão, só enquanto ela ESTÁ fundida. É o que o
front (Etapa 3) usa para oferecer "Desfazer" na linha do lançamento.

Mesma condição do undo (db/reconciliation.py:119-120): status em
`FUSED_STATUSES` ('auto_merged'/'confirmed') e `match_launch_id` nulo ou
igual ao próprio launch. `bank_movement_confirmed` (db/bank_movements.py) é
um QUARTO status, de uma feature vizinha — tem de dar None aqui mesmo com
imported_launch_id/match_launch_id apontando pro launch, porque não é o que
o undo desfaz.

Negativo (§3): afrouxar o filtro de status (monkeypatch em
`_FUSED_STATUSES_SQL`) tem de fazer o caso `bank_movement_confirmed` virar
não-None — prova que o filtro de status está fazendo trabalho real.
"""
from __future__ import annotations

import db

from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401 (fixtures)
from tests.test_reconciliacao_resolver import _q, pendencia


def _timeline_item(uid, launch_id):
    r = db.list_history(uid)
    item = next((i for i in r["items"] if i["id"] == launch_id), None)
    assert item is not None, f"launch {launch_id} não apareceu no histórico"
    return item


def test_fundido_undo_e_pendencia(uid_pro, ia_fora):
    _, of_tx, manual, _ = pendencia(uid_pro)

    # pending: ainda não fundiu.
    assert _timeline_item(uid_pro, manual)["reconciliation_of_tx_id"] is None

    db.confirm_reconciliation(uid_pro, of_tx)
    assert _timeline_item(uid_pro, manual)["reconciliation_of_tx_id"] == of_tx

    db.undo_reconciliation(uid_pro, of_tx)
    assert _timeline_item(uid_pro, manual)["reconciliation_of_tx_id"] is None


def test_bank_movement_confirmed_nao_conta_como_fundido(uid_pro, ia_fora, monkeypatch):
    _, of_tx, manual, _ = pendencia(uid_pro)
    outra = _q(
        """insert into open_finance_transactions
             (account_id, provider_transaction_id, description, amount, transaction_date,
              imported_launch_id, match_launch_id, reconciliation_status)
           select account_id, 'bmc', 'BMC', -1, transaction_date, %s, %s, 'bank_movement_confirmed'
             from open_finance_transactions where id=%s returning id""",
        (manual, manual, of_tx),
    )[0]["id"]

    assert _timeline_item(uid_pro, manual)["reconciliation_of_tx_id"] is None

    # Negativo: com o filtro de status afrouxado, a MESMA linha vira "fundida".
    import db.analytics as analytics
    monkeypatch.setattr(analytics, "_FUSED_STATUSES_SQL",
                         "('auto_merged','confirmed','bank_movement_confirmed')")
    assert _timeline_item(uid_pro, manual)["reconciliation_of_tx_id"] == outra
