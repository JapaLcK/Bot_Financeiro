"""Corpo JSON de topo não-objeto não pode virar 500 (issue #310).

`await request.json()` aceita qualquer JSON válido — `[1,2,3]`, `"abc"`, `42`,
`null`, `true`. O `try/except` em volta só pega falha de parse, então o topo
não-objeto passava e o `payload.get(...)` seguinte levantava AttributeError,
virando 500 com stack trace.

São 6 endpoints e um defeito só, por isso um arquivo só. Dois tratamentos:
- recusa com 400 — login admin, afiliados, webhook Pluggy;
- normaliza para `{}` — saques, onde o corpo é opcional por design e o
  `except` já dizia "corpo ausente/ruim não impede a ação".

Os corpos vão como `content=json.dumps(...)`, nunca `json=`: httpx traduz
`json=None` para "sem corpo", e aí o caso `null` cairia no `except` do parse
em vez da guarda de topo — o teste ficaria verde sem medir a guarda.

A 2ª metade do arquivo é o MESMO mecanismo um nível abaixo: topo objeto,
campo filho não-objeto (`{"item": null}`, `{"note": 42}`, `{"code": [..]}`,
`{"commission_bps": "abc"}`). A guarda de topo não alcança nenhum deles.
"""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import core.admin_dashboard as admin_dashboard
import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.open_finance as open_finance_routes
from db.affiliates import DEFAULT_COMMISSION_BPS

# Topos válidos em JSON que não são objeto. `null` e `true` incluídos de
# propósito: são os que passam despercebidos numa validação por truthiness.
NAO_OBJETOS = [[1, 2, 3], "abc", 42, None, True]
IDS = ["lista", "string", "numero", "null", "true"]

PLUGGY_SECRET = "test-webhook-secret"
CSRF = "test-admin-csrf"
# id que não existe: isola o teste de estado de outros testes e faz o caminho
# feliz terminar em 404 de forma determinística, sem fixture de dados.
INEXISTENTE = 999_999_999


@pytest.fixture(scope="module", autouse=True)
def _admin_tables():
    """system_event_logs não vem do init_db — só do ensure_admin_tables()
    (startup do app). Mesmo precedente de tests/test_admin_users_panel.py."""
    import asyncio

    asyncio.run(admin_dashboard.ensure_admin_tables())


@pytest.fixture(autouse=True)
def configured_admin(monkeypatch):
    async def _noop_log(*args, **kwargs):
        return None

    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD", "secret-admin")
    monkeypatch.setattr(admin_dashboard, "ADMIN_DASHBOARD_PASSWORD_HASH", "")
    monkeypatch.setattr(admin_dashboard, "log_system_event", _noop_log)
    monkeypatch.setattr(open_finance_routes, "log_system_event", _noop_log)
    monkeypatch.setenv("PLUGGY_WEBHOOK_SECRET", PLUGGY_SECRET)
    # /admin/auth/login tem @limiter.limit("10/minute") com storage em memória
    # compartilhado entre testes. Sem este reset, os 5 corpos ruins + o corpo
    # bom + os logins de _admin_client() estouram o teto e o 429 passaria por
    # "não é 500" — por isso os asserts abaixo são por status exato.
    try:
        dashboard.limiter._storage.reset()
    except Exception:
        pass


def _client() -> TestClient:
    # raise_server_exceptions=False: sem isso o 500 vira exceção propagada e o
    # teste nunca chega a ler o status que quer medir.
    client = TestClient(
        dashboard.app, base_url="https://testserver", raise_server_exceptions=False
    )
    client.cookies.set(dashboard.CSRF_COOKIE_NAME, CSRF)
    return client


def _admin_client() -> TestClient:
    client = _client()
    login = client.post(
        "/admin/auth/login",
        headers={dashboard.CSRF_HEADER_NAME: CSRF},
        json={"username": "admin", "password": "secret-admin"},
    )
    assert login.status_code == 200
    return client


def _post_texto(client: TestClient, url: str, texto: str) -> "object":
    """POST com o corpo JSON escrito à mão, byte a byte.

    Existe separado do `_post_bruto` porque `json.dumps` não consegue produzir
    o token `1e400`: em Python ele JÁ é `inf`, e o dumps o escreve como
    `Infinity` — outro token, outro hook do parser. Para medir o `1e400` o
    corpo tem de ser texto.

    O header CSRF é obrigatório em todo POST: sem ele a resposta é 403 e um
    assert frouxo ficaria verde de graça.
    """
    return client.post(
        url,
        content=texto,
        headers={
            "Content-Type": "application/json",
            dashboard.CSRF_HEADER_NAME: CSRF,
        },
    )


def _post_bruto(client: TestClient, url: str, corpo) -> "object":
    """POST com o corpo exatamente como escrito, sem o açúcar do httpx."""
    return _post_texto(client, url, json.dumps(corpo))


def _pluggy_post_texto(texto: str) -> "object":
    """Webhook com o corpo em texto cru — mesma razão do `_post_texto`.

    A assinatura HMAC é calculada sobre os bytes que vão de fato, então o corpo
    passa pelo `_authorize_pluggy_webhook` sem depender de reserialização.
    """
    raw_body = texto.encode("utf-8")
    assinatura = hmac.new(
        PLUGGY_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return TestClient(dashboard.app, raise_server_exceptions=False).post(
        "/open-finance/pluggy/webhook",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Pluggy-Signature": f"sha256={assinatura}",
        },
    )


def _pluggy_post(corpo) -> "object":
    return _pluggy_post_texto(json.dumps(corpo, separators=(",", ":")))


# --------------------------------------------------------------------------
# Grupo admin: recusa com 400 (era 500)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("corpo", NAO_OBJETOS, ids=IDS)
def test_admin_login_topo_nao_objeto_da_400(corpo):
    """O pior dos seis: rota anônima (o CSRF é double-submit, um GET qualquer
    entrega o cookie), então o 500 com stack trace era alcançável sem conta."""
    r = _post_bruto(_client(), "/admin/auth/login", corpo)
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("corpo", NAO_OBJETOS, ids=IDS)
def test_admin_affiliate_create_topo_nao_objeto_da_400(corpo):
    r = _post_bruto(_admin_client(), "/admin/api/affiliates", corpo)
    assert r.status_code == 400, r.text


@pytest.mark.parametrize("corpo", NAO_OBJETOS, ids=IDS)
def test_admin_affiliate_status_topo_nao_objeto_da_400(corpo):
    r = _post_bruto(
        _admin_client(), f"/admin/api/affiliates/{INEXISTENTE}/status", corpo
    )
    assert r.status_code == 400, r.text


# --------------------------------------------------------------------------
# Grupo Pluggy: recusa com 400 (era 500)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("corpo", NAO_OBJETOS, ids=IDS)
def test_pluggy_webhook_topo_nao_objeto_da_400(corpo):
    r = _pluggy_post(corpo)
    assert r.status_code == 400, r.text


# --------------------------------------------------------------------------
# Grupo saque: normaliza para {} e segue (era 500)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("acao", ["paid", "reject"])
@pytest.mark.parametrize("corpo", NAO_OBJETOS, ids=IDS)
def test_payout_topo_nao_objeto_normaliza_e_segue(corpo, acao):
    """404 (saque inexistente), não 400: aqui o corpo é opcional por design.
    Virar 400 mudaria um caminho que hoje funciona."""
    r = _post_bruto(
        _admin_client(),
        f"/admin/api/affiliates/payouts/{INEXISTENTE}/{acao}",
        corpo,
    )
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------
# Controles positivos: o caminho legítimo continua funcionando.
# Sem eles o arquivo inteiro passaria num código que recusa tudo.
# --------------------------------------------------------------------------

def test_controle_positivo_login_valido_ainda_autentica():
    """Rota de autenticação: regressão aqui tranca o painel."""
    client = _client()
    r = client.post(
        "/admin/auth/login",
        headers={dashboard.CSRF_HEADER_NAME: CSRF},
        json={"username": "admin", "password": "secret-admin"},
    )
    assert r.status_code == 200, r.text
    assert client.cookies.get(admin_dashboard.ADMIN_AUTH_COOKIE_NAME)


def test_controle_positivo_affiliate_status_objeto_valido():
    r = _post_bruto(
        _admin_client(),
        f"/admin/api/affiliates/{INEXISTENTE}/status",
        {"status": "disabled"},
    )
    # 404 porque o afiliado não existe; o que não pode é 400 (corpo recusado).
    assert r.status_code == 404, r.text


def test_controle_positivo_pluggy_webhook_objeto_valido():
    r = _pluggy_post({"event": "item/updated", "itemId": ""})
    assert r.status_code == 200, r.text
    assert r.json() == {"received": True}


@pytest.mark.parametrize("acao", ["paid", "reject"])
def test_controle_positivo_payout_sem_corpo_nenhum(acao):
    """Prova que o fallback `payload = {}` do `except` continua de pé: sem
    corpo, a ação segue e termina em 404 por saque inexistente, igual a hoje."""
    client = _admin_client()
    r = client.post(
        f"/admin/api/affiliates/payouts/{INEXISTENTE}/{acao}",
        headers={dashboard.CSRF_HEADER_NAME: CSRF},
    )
    assert r.status_code == 404, r.text


# ==========================================================================
# 2ª rodada — o MESMO mecanismo nos campos FILHOS, com o topo já sendo objeto.
#
# A guarda de topo acima não alcança nada daqui: ela prova que `payload` é
# dict, e o 500 volta na primeira linha que faz `.get()`/`.strip()`/`int()`
# sobre o VALOR de um campo. Um deles (`item`, no webhook) fica 6 linhas
# abaixo da própria guarda.
# ==========================================================================

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

@pytest.fixture
def afiliado_stub(monkeypatch):
    """Sem tocar no banco: o email resolve para um user_id fixo e o
    create_affiliate vira espião. Devolve a lista de (user_id, code, bps)."""
    import db
    import db.affiliates

    chamadas = []

    def _cria(user_id, code, commission_bps):
        chamadas.append((user_id, code, commission_bps))
        return {"id": 1, "code": code or "GERADO", "status": "active",
                "commission_bps": commission_bps}

    monkeypatch.setattr(db, "find_user_id_by_email", lambda email: 4242)
    monkeypatch.setattr(db.affiliates, "create_affiliate", _cria)
    return chamadas


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


# ==========================================================================
# 3ª rodada — NÚMERO NÃO FINITO. Topo objeto, campo do tipo certo (número), e
# mesmo assim 500.
#
# `json.loads` (o que o `request.json()` do Starlette usa por dentro) aceita
# `NaN`, `Infinity`, `-Infinity` e `1e400` — nenhum deles é JSON pela RFC 8259;
# é o módulo do Python que é leniente. O valor sai como float('nan')/float('inf')
# e quebra DOIS mecanismos que as guardas acima não alcançam:
#
#   • `int(inf)` levanta OverflowError, que não é TypeError nem ValueError —
#     escapava dos dois `except` e virava 500;
#   • `Jsonb(event)` (psycopg) serializa o float não finito com json.dumps, que
#     emite o token cru; o Postgres recusa com InvalidTextRepresentation → 500.
#
# `1e400` está aqui separado de `Infinity` de propósito: são tokens diferentes
# e hooks diferentes do parser (`parse_float` × `parse_constant`). Um conserto
# que só cubra um deixa o outro em 500.
# ==========================================================================

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
