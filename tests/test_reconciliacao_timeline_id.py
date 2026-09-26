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

import uuid

import db

from tests._fusao_of_helpers import ia_fora, manda, uid_pro  # noqa: F401 (fixtures)
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


# ── ISOLAMENTO: a otimização (LEFT JOIN em vez de subquery correlacionada,
# CLAUDE.md §0/§2) hoisteou `c.user_id = launches.user_id` pra fora da
# correlação. `imported_launch_id` já é FK pra um launch.id globalmente único,
# então um of_tx legítimo nunca casa com o launch de outro usuário — o caso
# que discrimina é dado CORROMPIDO: uma transação da CONEXÃO de A com
# `imported_launch_id` apontando pro launch de B (só alcançável se outro bug
# já tiver escrito isso). O filtro de user_id é a última linha de defesa
# contra esse vazamento — sem ele, o join casaria mesmo assim.

def test_of_tx_de_a_com_launch_id_de_b_nao_vaza_para_b(uid_pro, ia_fora):
    conexao, of_tx, manual, _ = pendencia(uid_pro)
    db.confirm_reconciliation(uid_pro, of_tx)
    assert _timeline_item(uid_pro, manual)["reconciliation_of_tx_id"] == of_tx

    from tests.conftest import promote_to_pro
    outro = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(outro)
    promote_to_pro(outro)
    manda(outro, "Gastei 1 real com a barbara em dinheiro")
    manual_b = _q("select id from launches where user_id=%s order by id desc limit 1",
                  (outro,))[0]["id"]

    # Dado corrompido: transação na CONEXÃO de A (uid_pro), mas
    # `imported_launch_id`/`match_launch_id` apontando pro launch de B.
    forjada = _q(
        """insert into open_finance_transactions
             (account_id, provider_transaction_id, description, amount, transaction_date,
              imported_launch_id, match_launch_id, reconciliation_status)
           select account_id, 'forjada-cross-user', 'FORJADA', -1, transaction_date,
                  %s, %s, 'confirmed'
             from open_finance_transactions where id=%s returning id""",
        (manual_b, manual_b, of_tx),
    )[0]["id"]

    item_b = _timeline_item(outro, manual_b)
    assert item_b["reconciliation_of_tx_id"] is None
    assert item_b["reconciliation_of_tx_id"] != forjada
