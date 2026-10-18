"""`logo_url` da conexão: escalar derivado de `raw.connector.imageUrl`, validado.

Caminho real: o item é gravado por `save_pluggy_open_finance_item` (o `raw` é o
item da Pluggy) e lido por `get_open_finance_snapshot`. O `raw` nunca sai.

CONTROLE NEGATIVO (medido; remeça se mexer): `logo_url_valida` devolvendo o texto
sem conferir → `javascript:`, `data:`, `http:` e os casos de espaço/controle
vermelhos; https, isolamento e `raw` fora verdes.
"""
from __future__ import annotations

import pytest
from psycopg.types.json import Jsonb

import db
from conftest import promote_to_pro
from db.connection import get_conn
from db.open_finance_state import logo_url_valida

HTTPS = "https://cdn.pluggy.ai/assets/connector-icons/612.svg"

INVALIDAS = [
    "http://cdn.pluggy.ai/a.svg", "javascript:alert(1)", "data:image/svg+xml;base64,AAAA",
    "", "https://", "https://" + "a" * 493, "https://cdn.pluggy.ai/a b.svg",
    "https://cdn.pluggy.ai/a\n.svg", "https://cdn.pluggy.ai/a\t.svg",
    "https://cdn.pluggy.ai/a\r.svg", "https://cdn.pluggy.ai/a\x00.svg", " https://x.com/a.svg",
    "https://x.com/a.svg\n",
    # DEL e espaços/controles NÃO-ASCII (\s do Python é Unicode): NBSP, EM SPACE, NEL, LINE SEPARATOR
    "https://x.com/a\x7f", "https://x.com/a b", "https://x.com/a\u00a0b", "https://x.com/a\u2003b",
    "https://x.com/a\x85b", "https://x.com/a\u2028b",
    # C1 e invisíveis de formatação: a classe tem de recusar, não só o C0
    "https://x.com/a\x80b", "https://x.com/a\x9bb", "https://x.com/a\u200bb",
    "https://x.com/a\u202eb", "https://x.com/a\u2066b", "https://x.com/a\ufeffb",
    # a CLASSE (categoria Cf/Co/Z*), não só as faixas: U+061C (Codex, #1106), soft hyphen, word joiner,
    # separador de vogal mongol, tag Unicode, uso privado, separador de parágrafo
    "https://x.com/a\u061cb", "https://x.com/a\u00adb", "https://x.com/a\u2060b", "https://x.com/a\u180eb",
    "https://x.com/a\U000e0001b", "https://x.com/a\ue000b", "https://x.com/a\u2029b",
]

VALIDAS_NAO_ASCII = ["https://x.com/caf\u00e9", "https://x.com/\U0001f437/a.svg"]


def test_validador_aceita_https_ate_500_e_recusa_o_resto():
    assert logo_url_valida(HTTPS) == HTTPS
    limite = "https://" + "a" * 492
    assert len(limite) == 500 and logo_url_valida(limite) == limite
    for v in VALIDAS_NAO_ASCII:  # controle positivo: a classe nova não recusa tudo
        assert logo_url_valida(v) == v, repr(v)
    for v in INVALIDAS + [None, 123, 1.5, True, {"a": HTTPS}, [HTTPS]]:
        assert logo_url_valida(v) is None, repr(v)


def _logo_pelo_snapshot(uid: int, connector, item_id: str = "item-logo"):
    promote_to_pro(uid)
    item = {"id": item_id, "status": "UPDATED"}
    if connector is not ...:
        item["connector"] = connector
    db.save_pluggy_open_finance_item(uid, item)
    (c,) = db.get_open_finance_snapshot(uid)["connections"]
    return c


def test_https_chega_ao_snapshot_e_o_raw_continua_fora(user_id):
    c = _logo_pelo_snapshot(user_id, {"id": 612, "name": "Nubank", "imageUrl": HTTPS})
    assert c["logo_url"] == HTTPS
    assert "raw" not in c and "connector" not in c and "imageUrl" not in c


@pytest.mark.parametrize("ruim", INVALIDAS)
def test_texto_invalido_vira_none_no_snapshot(user_id, ruim):
    if "\x00" in ruim:
        pytest.skip("jsonb do Postgres recusa NUL; o validador já cobre acima")
    c = _logo_pelo_snapshot(user_id, {"id": 612, "imageUrl": ruim})
    assert c["logo_url"] is None


@pytest.mark.parametrize("ruim", [123, 1.5, True, {"https": HTTPS}, [HTTPS]])
def test_nao_texto_vira_none_no_snapshot(user_id, ruim):
    c = _logo_pelo_snapshot(user_id, {"id": 612, "imageUrl": ruim})
    assert c["logo_url"] is None


@pytest.mark.parametrize("connector", [..., {"id": 612}, None])
def test_connector_ausente_vira_none(user_id, connector):
    assert _logo_pelo_snapshot(user_id, connector)["logo_url"] is None


@pytest.mark.parametrize("connector", ["Nubank", [1], 7])
def test_connector_que_nao_e_objeto_vira_none(user_id, connector):
    # o upsert não grava isso; o `raw` é regravado direto para provar que o SELECT não estoura
    _logo_pelo_snapshot(user_id, {"id": 1})
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("update open_finance_connections set raw = jsonb_set(raw, '{connector}', %s) "
                    "where user_id = %s", (Jsonb(connector), user_id))
        conn.commit()
    (c,) = db.get_open_finance_snapshot(user_id)["connections"]
    assert c["logo_url"] is None


def test_conexao_mock_pluggy_vira_none(user_id):
    db.create_mock_open_finance_connection(user_id)
    (c,) = db.get_open_finance_snapshot(user_id)["connections"]
    assert c["provider"] == "mock_pluggy" and c["logo_url"] is None


def test_logo_de_um_usuario_nao_aparece_para_o_outro(user_id):
    outro = user_id + 1
    db.ensure_user(outro)
    logo_b = "https://cdn.pluggy.ai/assets/connector-icons/999.svg"
    try:
        _logo_pelo_snapshot(user_id, {"id": 612, "imageUrl": HTTPS}, "item-a")
        promote_to_pro(outro)
        db.save_pluggy_open_finance_item(
            outro, {"id": "item-b", "status": "UPDATED", "connector": {"id": 999, "imageUrl": logo_b}})
        a = db.get_open_finance_snapshot(user_id)["connections"]
        b = db.get_open_finance_snapshot(outro)["connections"]
        # controle positivo: cada um vê o SEU logo; o do outro não aparece
        assert [c["logo_url"] for c in a] == [HTTPS]
        assert [c["logo_url"] for c in b] == [logo_b]
    finally:
        from conftest import _cleanup_user
        _cleanup_user(outro)
