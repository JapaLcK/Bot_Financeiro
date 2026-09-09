"""O MESMO mecanismo do #310 um nível abaixo: topo objeto, campo FILHO ruim.

A guarda de topo (`test_corpo_json_topo_nao_objeto.py`) não alcança nada daqui:
ela prova que `payload` é dict, e o 500 volta na primeira linha que faz
`.get()`/`.strip()`/`int()` sobre o VALOR de um campo. Um deles (`item`, no
webhook) fica 6 linhas abaixo da própria guarda.

São quatro campos e o mesmo defeito: `{"item": null}`, `{"note": 42}`,
`{"code": [..]}`, `{"commission_bps": "abc"}`. O caso do número não finito
(topo objeto, campo do tipo certo, e ainda assim 500) mora em
`test_corpo_json_nao_finito.py`.
"""
import pytest

import frontend.routes.open_finance as open_finance_routes
from _corpo_json_helpers import (  # noqa: F401  (fixtures autouse)
    IDS,
    INEXISTENTE,
    NAO_OBJETOS,
    _admin_client,
    _admin_tables,
    _pluggy_post,
    _post_bruto,
    afiliado_stub,
    configured_admin,
)
from db.affiliates import DEFAULT_COMMISSION_BPS

# Filhos não-objeto que chegam a um `.get()`/`.strip()`/`int()`. `None` sai
# dos que exigem valor truthy (`x or default` já o neutraliza) e entra nos
# controles positivos, onde é o caso legítimo mais comum.
FILHOS_RUINS = [42, ["a"], {"x": 1}, True]
FILHOS_IDS = ["numero", "lista", "objeto", "true"]


# --------------------------------------------------------------------------
# Webhook Pluggy: `item` não-objeto vale como AUSENTE (era 500)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("filho", NAO_OBJETOS, ids=IDS)
def test_pluggy_item_nao_objeto_nao_da_500(filho):
    """`{"item": null}` é corpo que a PRÓPRIA Pluggy manda em evento sem item —
    não precisa de atacante com o secret. E webhook que responde erro faz ela
    REENVIAR, então 500 aqui vira laço. `event.get("item", {})` só usava o
    default `{}` quando a CHAVE faltava; com a chave presente e valor `null`,
    o `.get("id")` seguinte levantava AttributeError.

    `event: "ping"` de propósito: fora de PLUGGY_SYNC_EVENTS e de
    status_by_event, então o caminho não escreve nada no banco.
    """
    r = _pluggy_post({"event": "ping", "item": filho})
    assert r.status_code == 200, r.text
    assert r.json() == {"received": True}


@pytest.mark.parametrize("corpo,esperado", [
    ({"itemId": "AAA"}, "AAA"),
    ({"item_id": "BBB"}, "BBB"),
    ({"item": {"id": "CCC"}}, "CCC"),
    ({"itemId": "AAA", "item": {"id": "CCC"}}, "AAA"),
], ids=["itemId", "item_id", "item.id", "precedencia"])
def test_controle_positivo_pluggy_item_id_resolve_igual(monkeypatch, corpo, esperado):
    """Os 3 formatos legítimos e a precedência entre eles não podem mudar —
    é o item_id que decide de quem é a conexão e o que sincroniza."""
    vistos = []
    monkeypatch.setattr(
        open_finance_routes, "get_connections_by_item_id",
        lambda item_id: vistos.append(item_id) or [],
    )
    r = _pluggy_post({"event": "ping", **corpo})
    assert r.status_code == 200, r.text
    assert vistos == [esperado]


# --------------------------------------------------------------------------
# Saque: `note` não-string (era 500 no .strip())
# --------------------------------------------------------------------------

ACOES_PAYOUT = [("paid", "mark_payout_paid"), ("reject", "reject_payout")]


def _espiao_payout(monkeypatch, funcao):
    """Troca a função de banco por um espião que devolve False (→ 404, o mesmo
    fim do saque inexistente) e guarda o `note` que recebeu."""
    import db
    import db.affiliates

    vistos = []
    monkeypatch.setattr(
        db.affiliates, funcao,
        lambda payout_id, note: vistos.append(note) or False,
    )
    return vistos


@pytest.mark.parametrize("acao,funcao", ACOES_PAYOUT, ids=["paid", "reject"])
@pytest.mark.parametrize("filho", FILHOS_RUINS, ids=FILHOS_IDS)
def test_payout_note_nao_string_nao_da_500(monkeypatch, filho, acao, funcao):
    vistos = _espiao_payout(monkeypatch, funcao)
    r = _post_bruto(
        _admin_client(),
        f"/admin/api/affiliates/payouts/{INEXISTENTE}/{acao}",
        {"note": filho},
    )
    assert r.status_code == 404, r.text
    # O que importa não é o texto que sai, é que a coluna text nunca recebe
    # um não-string — e que a ação seguiu, como segue hoje sem corpo.
    assert vistos and isinstance(vistos[0], str), vistos


@pytest.mark.parametrize("acao,funcao", ACOES_PAYOUT, ids=["paid", "reject"])
@pytest.mark.parametrize("corpo,esperado", [
    ({}, None),
    ({"note": None}, None),
    ({"note": ""}, None),
    ({"note": "   "}, None),
    ({"note": "pago à mão ✓"}, "pago à mão ✓"),
    ({"note": "  x  "}, "x"),
], ids=["vazio", "null", "string_vazia", "so_espaco", "acento", "com_espaco"])
def test_controle_positivo_payout_note_string_inalterada(
    monkeypatch, corpo, esperado, acao, funcao
):
    """`note` é opcional por design. Os 6 formatos abaixo × 2 ações = os 24
    casos que o Tester mediu com espião: têm de continuar idênticos."""
    vistos = _espiao_payout(monkeypatch, funcao)
    r = _post_bruto(
        _admin_client(),
        f"/admin/api/affiliates/payouts/{INEXISTENTE}/{acao}",
        corpo,
    )
    assert r.status_code == 404, r.text
    assert vistos == [esperado]


# --------------------------------------------------------------------------
# Afiliado: `code` não-string e `commission_bps` não-inteiro (eram 500)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bps", ["abc", "1e5", [10], {"a": 1}],
                         ids=["texto", "cientifico", "lista", "objeto"])
def test_affiliate_commission_bps_invalido_da_422(afiliado_stub, bps):
    """`int(payload.get(...))` estava FORA do try — e o try abaixo só pega
    ValueError, então nem 'abc' era coberto."""
    r = _post_bruto(_admin_client(), "/admin/api/affiliates",
                    {"email": "x@y.com", "commission_bps": bps})
    assert r.status_code == 422, r.text
    assert afiliado_stub == []


@pytest.mark.parametrize("code", FILHOS_RUINS, ids=FILHOS_IDS)
def test_affiliate_code_nao_string_da_422(afiliado_stub, code):
    """`code` não-string chegava em _normalize_code (db/affiliates.py:37), que
    faz .strip() → AttributeError, que o `except ValueError` não pega."""
    r = _post_bruto(_admin_client(), "/admin/api/affiliates",
                    {"email": "x@y.com", "code": code})
    assert r.status_code == 422, r.text
    assert afiliado_stub == []


@pytest.mark.parametrize("corpo,esperado", [
    ({}, (4242, None, DEFAULT_COMMISSION_BPS)),
    ({"code": "MEUCODIGO"}, (4242, "MEUCODIGO", DEFAULT_COMMISSION_BPS)),
    ({"code": ""}, (4242, None, DEFAULT_COMMISSION_BPS)),
    ({"commission_bps": 2000}, (4242, None, 2000)),
    ({"commission_bps": "1500"}, (4242, None, 1500)),
    ({"code": "ABCD", "commission_bps": 500}, (4242, "ABCD", 500)),
], ids=["so_email", "code", "code_vazio", "bps_int", "bps_string", "ambos"])
def test_controle_positivo_affiliate_caminho_valido(afiliado_stub, corpo, esperado):
    """O caminho válido não pode mudar de status nem de argumento. A string
    numérica ("1500") está aqui de propósito: o `int()` a aceitava antes e um
    isinstance(int) no lugar do try/except a teria quebrado em silêncio."""
    r = _post_bruto(_admin_client(), "/admin/api/affiliates",
                    {"email": "x@y.com", **corpo})
    assert r.status_code == 200, r.text
    assert afiliado_stub == [esperado]


def test_affiliate_code_nao_string_nao_chega_no_normalize_code(monkeypatch):
    """Sem o stub de create_affiliate: prova o mecanismo REAL, não só o contrato
    da rota. Os testes acima trocam create_affiliate por espião, então nunca
    chegam a _normalize_code — e a guarda derrubada daria 200, não o 500 que
    existe em produção. Aqui só o email é resolvido; o resto é código de verdade,
    e sem a guarda isto é AttributeError: 'int' object has no attribute 'strip'.

    Não escreve no banco em nenhum dos dois mundos: com a guarda, para na rota;
    sem ela, o _normalize_code estoura antes do get_conn().
    """
    import db

    monkeypatch.setattr(db, "find_user_id_by_email", lambda email: 4242)
    r = _post_bruto(_admin_client(), "/admin/api/affiliates",
                    {"email": "x@y.com", "code": 42})
    assert r.status_code == 422, r.text
