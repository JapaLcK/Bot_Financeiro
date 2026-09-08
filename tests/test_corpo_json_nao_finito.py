"""NÚMERO NÃO FINITO no corpo do #310: topo objeto, campo do tipo certo, e
mesmo assim 500.

`json.loads` (o que o `request.json()` do Starlette usa por dentro) aceita
`NaN`, `Infinity`, `-Infinity` e `1e400` — nenhum deles é JSON pela RFC 8259;
é o módulo do Python que é leniente. O valor sai como float('nan')/float('inf')
e quebra DOIS mecanismos que as guardas de topo
(`test_corpo_json_topo_nao_objeto.py`) e de campo filho
(`test_corpo_json_campo_filho.py`) não alcançam:

  • `int(inf)` levanta OverflowError, que não é TypeError nem ValueError —
    escapava dos dois `except` e virava 500;
  • `Jsonb(event)` (psycopg) serializa o float não finito com json.dumps, que
    emite o token cru; o Postgres recusa com InvalidTextRepresentation → 500.

`1e400` está aqui separado de `Infinity` de propósito: são tokens diferentes
e hooks diferentes do parser (`parse_float` × `parse_constant`). Um conserto
que só cubra um deixa o outro em 500.
"""
import pytest

import core.admin_dashboard as admin_dashboard
import frontend.routes.open_finance as open_finance_routes
from _corpo_json_helpers import (  # noqa: F401  (fixtures autouse)
    INEXISTENTE,
    _admin_client,
    _admin_tables,
    _pluggy_post_texto,
    _post_texto,
    afiliado_stub,
    configured_admin,
)

NAO_FINITOS = ["NaN", "Infinity", "-Infinity", "1e400"]
NF_IDS = ["nan", "infinity", "menos_infinity", "1e400"]


# --------------------------------------------------------------------------
# int() sobre não finito: OverflowError → 422 (era 500)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("token", NAO_FINITOS, ids=NF_IDS)
def test_affiliate_commission_bps_nao_finito_da_422(afiliado_stub, token):
    r = _post_texto(
        _admin_client(), "/admin/api/affiliates",
        '{"email": "x@y.com", "commission_bps": %s}' % token,
    )
    assert r.status_code == 422, r.text
    assert afiliado_stub == []


@pytest.fixture
def plano_espiao(monkeypatch):
    """`set_account_plan` vira espião que devolve None (→ 404, o mesmo fim da
    conta inexistente) e guarda o (plan, months) que recebeu."""
    vistos = []

    def _set(plan, months, **kwargs):
        vistos.append((plan, months))
        return None

    monkeypatch.setattr(admin_dashboard, "set_account_plan", _set)
    return vistos


@pytest.mark.parametrize("token", NAO_FINITOS, ids=NF_IDS)
def test_plan_months_nao_finito_da_422(plano_espiao, token):
    """O `except (TypeError, ValueError)` daqui foi o modelo copiado para o
    commission_bps — e os dois deixavam OverflowError escapar."""
    r = _post_texto(
        _admin_client(), f"/admin/api/users/{INEXISTENTE}/plan",
        '{"plan": "pro", "months": %s}' % token,
    )
    assert r.status_code == 422, r.text
    # Nada chega ao banco: a recusa é antes do set_account_plan.
    assert plano_espiao == []


@pytest.mark.parametrize("corpo,esperado", [
    ('{"plan": "pro"}', 12),
    ('{"plan": "pro", "months": null}', 12),
    ('{"plan": "pro", "months": 6}', 6),
    ('{"plan": "pro", "months": "3"}', 3),
    ('{"plan": "pro", "months": 1.9}', 1),
], ids=["ausente", "null", "int", "string_numerica", "float_trunca"])
def test_controle_positivo_plan_months_valido_inalterado(plano_espiao, corpo, esperado):
    """Os 5 formatos que `int()` aceitava continuam aceitos e com o MESMO valor.
    O `1.9 → 1` e a string `"3"` estão aqui porque são o que um `isinstance(int)`
    no lugar do try/except teria quebrado em silêncio."""
    r = _post_texto(
        _admin_client(), f"/admin/api/users/{INEXISTENTE}/plan", corpo
    )
    # 404 porque a conta não existe; o que não pode é 422 (número recusado).
    assert r.status_code == 404, r.text
    assert plano_espiao == [("pro", esperado)]


# --------------------------------------------------------------------------
# Webhook Pluggy: não finito em QUALQUER chave → 400 (era 500 no Jsonb)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("token", NAO_FINITOS, ids=NF_IDS)
def test_pluggy_webhook_numero_nao_finito_da_400(token):
    """`extra` é chave que o handler NEM LÊ: o corpo inteiro vai para o
    `Jsonb(event)` de update_pluggy_open_finance_item_status
    (db/open_finance.py), e o json.dumps do psycopg emite o token cru →
    `InvalidTextRepresentation: Token "Infinity" is invalid` → 500.

    E 500 num webhook faz a Pluggy REENVIAR: é o laço que o comentário do
    `item` (seis linhas acima, no mesmo handler) existe para evitar. 400 põe o
    caso na classe que este handler já tratava assim antes deste PR — "corpo
    que não é JSON" —, que é exatamente o que `NaN`/`Infinity` são pela RFC.
    """
    r = _pluggy_post_texto(
        '{"event":"item/error","itemId":"item-310-nf","extra":%s}' % token
    )
    assert r.status_code == 400, r.text


def test_controle_positivo_pluggy_float_normal_chega_intacto(monkeypatch):
    """Sem este controle o grupo acima passaria num parser que recusa TODO
    float. Prova o valor, não só o status: o número atravessa o parse e chega
    ao `raw` que vai para o banco, com o mesmo tipo e o mesmo valor.

    `1e-400` (underflow → 0.0) entra de propósito: é finito, e um conserto
    escrito com "expoente grande demais" em vez de `isfinite` o recusaria.
    """
    vistos = []
    monkeypatch.setattr(
        open_finance_routes, "update_pluggy_open_finance_item_status",
        lambda item_id, status, raw=None: vistos.append((item_id, status, raw)) or 1,
    )
    monkeypatch.setattr(
        open_finance_routes, "get_connections_by_item_id", lambda item_id: [],
    )
    r = _pluggy_post_texto(
        '{"event":"item/error","itemId":"item-310-ok",'
        '"valor":123.45,"negativo":-0.5,"zero":0.0,"underflow":1e-400,"inteiro":7}'
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"received": True}
    assert len(vistos) == 1, vistos
    item_id, status, raw = vistos[0]
    assert (item_id, status) == ("item-310-ok", "ERROR")
    assert raw["valor"] == 123.45 and isinstance(raw["valor"], float)
    assert raw["negativo"] == -0.5
    assert raw["zero"] == 0.0
    assert raw["underflow"] == 0.0
    assert raw["inteiro"] == 7 and isinstance(raw["inteiro"], int)
