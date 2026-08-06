"""
"Desfazer" deve remover o último lançamento CRIADO (maior id), não o mais
recente por data. Regressão: um lançamento retroativo ("gastei 50 ontem") entra
com criado_em no passado; se o undo mirasse por data, apagaria o lançamento de
hoje (o errado) e corromperia o saldo.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

import db
from core.handlers import launches
from utils_date import today_tz, _tz


def test_undo_mira_ultimo_inserido_mesmo_retroativo(user_id):
    # lançamento de HOJE (id menor, data mais recente)
    id_hoje, _, _ = db.add_launch_and_update_balance(
        user_id, "despesa", 100, "ifood", "ifood", "alimentação",
    )
    # lançamento RETROATIVO (ontem) inserido DEPOIS → id maior, data anterior
    ontem = datetime.combine(today_tz() - timedelta(days=1), time(12, 0), tzinfo=_tz())
    id_ontem, _, _ = db.add_launch_and_update_balance(
        user_id, "despesa", 50, "mercado", "mercado", "mercado", criado_em=ontem,
    )
    assert id_ontem > id_hoje

    msg = launches.undo(user_id)

    p = db.get_pending_action(user_id)
    assert p["action_type"] == "delete_launch"
    # mira o último INSERIDO (retroativo), não o de hoje
    assert p["payload"]["launch_id"] == id_ontem
    assert "50" in msg          # mostra o valor do lançamento certo
