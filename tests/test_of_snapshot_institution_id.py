"""`institution_id` da conexão no snapshot (`GET /open-finance/{uid}`).

O /painel (Bancos conectados, "Disponíveis para conectar") decide "já conectado" comparando por ID o `institution_id`
da conexão com o `id` do conector de `/open-finance/{uid}/connectors`, nunca por nome (o gêmeo Open Finance de um banco
tem nome igual e id diferente). O campo já era do corpo (a chave está em `CHAVES_DA_CONEXAO`, tests/test_of_connection_state.py,
que prende as demais); este arquivo prende o VALOR: texto, o id do conector, e só o do próprio usuário.

CONTROLE NEGATIVO (medido; remeça se mexer): `c.pop("institution_id", None)` no laço de
`_read_open_finance_connections` deixa os dois testes vermelhos (KeyError); o select intacto, verdes.
"""
from __future__ import annotations

import db
from conftest import _cleanup_user, promote_to_pro


def _salva(uid: int, item_id: str, conector_id: int, nome: str) -> None:
    promote_to_pro(uid)
    db.save_pluggy_open_finance_item(
        uid, {"id": item_id, "status": "UPDATED", "connector": {"id": conector_id, "name": nome}})


def test_institution_id_e_o_id_do_conector_em_texto(user_id):
    _salva(user_id, "item-inst", 612, "Nubank")
    (c,) = db.get_open_finance_snapshot(user_id)["connections"]
    assert c["institution_id"] == "612" and isinstance(c["institution_id"], str)


def test_institution_id_de_um_usuario_nao_aparece_para_o_outro(user_id):
    outro = user_id + 1
    db.ensure_user(outro)
    try:
        _salva(user_id, "item-a", 612, "Nubank")
        _salva(outro, "item-b", 201, "Itaú")
        a = db.get_open_finance_snapshot(user_id)["connections"]
        b = db.get_open_finance_snapshot(outro)["connections"]
        # controle positivo: cada um vê o SEU id; o do outro não aparece
        assert [c["institution_id"] for c in a] == ["612"]
        assert [c["institution_id"] for c in b] == ["201"]
    finally:
        _cleanup_user(outro)
