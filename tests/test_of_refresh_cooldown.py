"""Os dois apertos do botão "Atualizar" do Open Finance dizem a mesma coisa.

O 2º aperto cai no cooldown do manual (`OF_MANUAL_REFRESH_COOLDOWN_SEC`), que não
pede coleta nova — e devolvia `still_updating: 0` fixo. Com o item `updated`
virando `rate_limited`, o veredito do front caía em "já está tudo em dia" logo
depois de o 1º aperto ter dito "o banco ainda está atualizando".

Mora num arquivo próprio porque `test_of_refresh_response.py` passaria do teto
de 350 linhas (`tests/test_max_lines_python.py`); reaproveita o mundo remoto de lá.
"""

from __future__ import annotations

import db
import core.services.pluggy_sync as ps
from test_of_refresh_response import ITEM_OK, _mundo_remoto


def _dois_apertos(user_id, monkeypatch, item_id: str, status_remoto: str) -> tuple[dict, dict]:
    """Os dois apertos do botão, encadeados e SEM mock no cooldown: o 1º leva
    PATCH e espera de verdade (o laço consulta o GET até o prazo), o 2º cai no
    `claim_manual_refresh` real, dentro dos 120s. O item remoto traz produto
    medido como atualizado, então o `connection_ui_state` diz `updated` — e o
    `OF_VERDICT` do front não acha estado nenhum: quem decide é o contador."""
    db.save_pluggy_open_finance_item(user_id, {"id": item_id, "status": "UPDATED",
                                               "connector": {"id": 612, "name": "Nubank"}})
    _mundo_remoto(monkeypatch)
    remoto = {**ITEM_OK, "id": item_id, "status": status_remoto}
    monkeypatch.setattr(ps, "get_pluggy_item", lambda i, k=None: remoto)
    monkeypatch.setattr(ps, "update_pluggy_item", lambda i, k=None: remoto)
    primeiro = ps.refresh_and_sync_pluggy_user(user_id, wait_seconds=1, poll_interval=0.05)
    segundo = ps.refresh_and_sync_pluggy_user(user_id, wait_seconds=1, poll_interval=0.05)
    return primeiro, segundo


def test_os_dois_apertos_dizem_a_mesma_coisa_com_o_banco_ainda_coletando(user_id, monkeypatch):
    """10:01 aperta Atualizar: PATCH, espera, item ainda `UPDATING` → "o banco
    ainda está atualizando". 10:01:40 aperta de novo, dentro do cooldown: sem
    PATCH, e o ramo do cooldown devolvia `still_updating: 0` FIXO — com o item
    `updated` virando `rate_limited`, o front dizia "Você acabou de atualizar —
    já está tudo em dia", com a coleta que nós mesmos pedimos ainda rodando.

    CONTROLE NEGATIVO (medido): repor o `0` fixo no retorno do ramo do cooldown
    de `refresh_and_sync_pluggy_user` deixa este teste vermelho no 2º aperto.
    CONTROLE POSITIVO: `test_cooldown_com_item_realmente_atualizado_segue_verde`."""
    primeiro, segundo = _dois_apertos(user_id, monkeypatch, "item-coletando", "UPDATING")

    assert primeiro["refreshed"] == 1 and segundo["refreshed"] == 0, "o 2º tem de ser o cooldown"
    for aperto in (primeiro, segundo):
        # Nenhum estado da tabela do front: sem o contador, o veredito seria verde.
        assert {i["state"] for i in aperto["items"]} <= {"updated", "rate_limited"}, aperto["items"]
        assert aperto["still_updating"] == 1, aperto
        assert [i["still_updating"] for i in aperto["items"]] == [True], aperto["items"]


def test_cooldown_com_item_realmente_atualizado_segue_verde(user_id, monkeypatch):
    """O caso legítimo que o cooldown existe para cobrir: acabamos de coletar e a
    Pluggy terminou. Sem esta asserção o conserto passaria num código que conta
    TODO item do cooldown como "coletando" — que devolveria o laço do botão."""
    primeiro, segundo = _dois_apertos(user_id, monkeypatch, "item-pronto", "UPDATED")

    assert segundo["refreshed"] == 0, "o 2º tem de ser o cooldown"
    assert [i["state"] for i in segundo["items"]] == ["rate_limited"], segundo["items"]
    assert segundo["still_updating"] == 0 and segundo["ok"] is True, segundo
    assert primeiro["still_updating"] == 0, primeiro
