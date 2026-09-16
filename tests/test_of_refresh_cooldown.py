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


NUBANK = {"connector": {"id": 612, "name": "Nubank"}}


def _aperta(user_id) -> dict:
    return ps.refresh_and_sync_pluggy_user(user_id, wait_seconds=1, poll_interval=0.05)


def _remoto(monkeypatch, status_por_item: dict) -> None:
    """Pluggy mockada com o status de cada item lido NA HORA do GET — o teste
    pode mudá-lo entre um aperto e outro."""
    _mundo_remoto(monkeypatch)
    item = lambda i, k=None: {**ITEM_OK, "id": i, "status": status_por_item[i]}  # noqa: E731
    monkeypatch.setattr(ps, "get_pluggy_item", item)
    monkeypatch.setattr(ps, "update_pluggy_item", item)


def _dois_apertos(user_id, monkeypatch, item_id: str, status_remoto: str,
                  status_no_segundo: str | None = None) -> tuple[dict, dict]:
    """Os dois apertos do botão, encadeados e SEM mock no cooldown: o 1º leva
    PATCH e espera de verdade (o laço consulta o GET até o prazo), o 2º cai no
    `claim_manual_refresh` real, dentro de `OF_MANUAL_REFRESH_COOLDOWN_SEC`. O
    item remoto traz produto medido como atualizado, então o
    `connection_ui_state` diz `updated` — e o `OF_VERDICT` do front não acha
    estado nenhum: quem decide é o contador."""
    db.save_pluggy_open_finance_item(user_id, {"id": item_id, "status": "UPDATED", **NUBANK})
    status = {item_id: status_remoto}
    _remoto(monkeypatch, status)
    primeiro = _aperta(user_id)
    status[item_id] = status_no_segundo or status_remoto
    return primeiro, _aperta(user_id)


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


def test_o_cooldown_le_a_foto_nova_e_nao_a_do_aperto_anterior(user_id, monkeypatch):
    """A Pluggy termina ENTRE os dois apertos. O ramo do cooldown só sabe disso
    porque o `sync_pluggy_user` dele faz GET e regrava o `health` antes de o
    relatório ser montado; os testes acima não separam leitura fresca de foto
    velha, porque o status remoto é o mesmo nos dois apertos.

    CONTROLE NEGATIVO: no ramo do cooldown de `refresh_and_sync_pluggy_user`,
    montar o relatório ANTES do `sync_pluggy_user` (ele lê a foto do 1º aperto)
    deixa este teste vermelho no 2º aperto."""
    primeiro, segundo = _dois_apertos(user_id, monkeypatch, "item-terminou", "UPDATING", "UPDATED")

    assert primeiro["still_updating"] == 1, primeiro
    assert segundo["refreshed"] == 0, "o 2º tem de ser o cooldown"
    assert [(i["state"], i["still_updating"]) for i in segundo["items"]] == [("rate_limited", False)]
    assert segundo["still_updating"] == 0, segundo


def test_aperto_misto_conta_o_item_do_cooldown_que_ainda_coleta(user_id, monkeypatch):
    """O cooldown é POR ITEM: um banco liberado (leva PATCH e termina) e outro
    ainda em cooldown e coletando. O liberado sai do `pending`; o do cooldown só
    é medido pelo health. Contar `len(pending)` dava 0 com o campo por item
    dizendo `True` — e o front caía em verde.

    CONTROLE NEGATIVO: trocar a soma do campo por item por `len(pending)` no
    retorno do ramo normal de `refresh_and_sync_pluggy_user` deixa este teste
    vermelho."""
    status = {"item-em-cooldown": "UPDATING", "item-liberado": "UPDATED"}
    _remoto(monkeypatch, status)
    db.save_pluggy_open_finance_item(user_id, {"id": "item-em-cooldown", "status": "UPDATED", **NUBANK})
    _aperta(user_id)                                   # põe o 1º no cooldown
    db.save_pluggy_open_finance_item(user_id, {"id": "item-liberado", "status": "UPDATED", **NUBANK})

    saida = _aperta(user_id)

    assert saida["refreshed"] == 1, "só o liberado leva PATCH"
    por_item = {i["item_id"]: i["still_updating"] for i in saida["items"]}
    assert por_item == {"item-em-cooldown": True, "item-liberado": False}, saida["items"]
    assert saida["still_updating"] == 1, saida


def test_conexao_terminal_com_foto_velha_em_coleta_nao_conta_como_coletando(user_id, monkeypatch):
    """`rate_limited` não é só cooldown: o `claim_manual_refresh` também deixa de
    fora conexão PAUSED/DELETED, e o sync não regrava o health dela. Uma foto
    velha em `UPDATING` fazia o campo dizer que a Pluggy ainda coleta um item que
    nem existe mais lá.

    CONTROLE NEGATIVO: tirar a condição de conexão terminal do `still_updating`
    por item em `_refresh_items_report` deixa este teste vermelho. O lado
    positivo (item do cooldown NÃO terminal conta) é
    `test_os_dois_apertos_dizem_a_mesma_coisa_com_o_banco_ainda_coletando`."""
    conexao = db.save_pluggy_open_finance_item(
        user_id, {"id": "item-pausado", "status": "UPDATED", **NUBANK})
    db.mark_sync_result(conexao["id"], ok=True, status="ACTIVE", status_reason="", health={
        "observed_at": "2026-08-20T12:00:00-03:00", "item_status": "UPDATING",
        "execution_status": None, "products": {}, "stale_products": []})
    db.pause_open_finance_connection(conexao["id"])
    _remoto(monkeypatch, {"item-pausado": "UPDATING"})

    saida = _aperta(user_id)

    assert [(i["state"], i["still_updating"]) for i in saida["items"]] == [("paused", False)]
    assert saida["still_updating"] == 0, saida
