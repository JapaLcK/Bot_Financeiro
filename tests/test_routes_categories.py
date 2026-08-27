"""Rota GET /categories/{user_id}/launches (frontend/routes/categories.py).

Smoke no molde de tests/test_routes_analytics.py: a rota está registrada e
protegida pela cadeia de auth do dashboard. Não toca dados — 401 sem token,
403 com token de outro user, e a validação de entrada que roda ANTES do banco.
"""

from urllib.parse import quote

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
from token_utils import make_dashboard_token

client = TestClient(dashboard.app)

PATH = "/categories/{uid}/launches?categoria=mercado"


def test_launches_sem_token_401():
    resp = client.get(PATH.format(uid=832398038))
    assert resp.status_code == 401, resp.status_code


def test_launches_token_de_outro_user_403():
    token = make_dashboard_token(832398038, hours=1)
    resp = client.get(
        PATH.format(uid=999999999),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, resp.status_code


def test_crud_de_categories_continua_registrado():
    # A rota nova mora num router incluído ANTES do CRUD do monólito: o
    # /categories/{user_id} não pode ter sido sombreado.
    resp = client.get("/categories/832398038")
    assert resp.status_code == 401, resp.status_code


def _auth(uid):
    return {"Authorization": f"Bearer {make_dashboard_token(uid, hours=1)}"}


def test_categoria_vazia_400(pro_user_id):
    resp = client.get(
        f"/categories/{pro_user_id}/launches?categoria=%20%20",
        headers=_auth(pro_user_id),
    )
    assert resp.status_code == 400, (resp.status_code, resp.text)


def test_categoria_ausente_422(pro_user_id):
    resp = client.get(f"/categories/{pro_user_id}/launches", headers=_auth(pro_user_id))
    assert resp.status_code == 422, (resp.status_code, resp.text)


def test_data_invalida_400(pro_user_id):
    resp = client.get(
        f"/categories/{pro_user_id}/launches?categoria=mercado&from=13/02/2026",
        headers=_auth(pro_user_id),
    )
    assert resp.status_code == 400, (resp.status_code, resp.text)


def test_caminho_legitimo_200(pro_user_id):
    # Controle positivo: a validação acima não pode estar recusando tudo.
    import db
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "pao", None, categoria="mercado",
    )
    resp = client.get(
        f"/categories/{pro_user_id}/launches?categoria=mercado&limit=999",
        headers=_auth(pro_user_id),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert len(body["launches"]) == 1, body
    row = body["launches"][0]
    assert row["id"] is not None
    assert row["is_internal_movement"] is False
    # `data` sai em ISO (o JSON não serializa `date` sozinho).
    from utils_date import today_tz
    assert row["data"] == today_tz().isoformat(), row
    assert body["resumo"]["n_total"] == 1


# ── Fronteira de entrada: o critério é o dano, não a boa digitação ────────
def test_limit_zero_e_negativo_422(pro_user_id):
    # Pedir "nenhuma linha" e receber uma é inventar resposta. `ge=1` do FastAPI
    # (declarativo) → 422, a MESMA classe de `limit=abc`, não 400.
    for lim in (0, -1):
        resp = client.get(
            f"/categories/{pro_user_id}/launches?categoria=lazer&limit={lim}",
            headers=_auth(pro_user_id),
        )
        assert resp.status_code == 422, (lim, resp.status_code, resp.text)


def test_limit_nao_numerico_422(pro_user_id):
    resp = client.get(
        f"/categories/{pro_user_id}/launches?categoria=lazer&limit=abc",
        headers=_auth(pro_user_id),
    )
    assert resp.status_code == 422, resp.status_code


def test_limit_acima_do_teto_e_cortado_nao_recusado(pro_user_id):
    # Controle positivo da fronteira: teto não é contradição — pedir 999 e
    # receber no máximo 100 é o servidor cortando, não recusando.
    import db
    for i in range(3):
        db.add_launch_and_update_balance(
            pro_user_id, "despesa", 10 + i, f"x{i}", None, categoria="lazer",
        )
    resp = client.get(
        f"/categories/{pro_user_id}/launches?categoria=lazer&limit=999",
        headers=_auth(pro_user_id),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["launches"]) == 3


def test_janela_invertida_400(pro_user_id):
    # from > to devolvia 200 com lista vazia: "não achei nada" é uma resposta
    # ERRADA pra um pedido impossível — o usuário conclui que não tem gasto.
    resp = client.get(
        f"/categories/{pro_user_id}/launches?categoria=lazer"
        f"&from=2030-01-01&to=2020-01-01",
        headers=_auth(pro_user_id),
    )
    assert resp.status_code == 400, (resp.status_code, resp.text)


# ── Filtros que fazem a lista bater com a barra clicada ───────────────────
def test_include_internal_false_e_tipo_despesa(pro_user_id):
    """O que a Distribuição do mês manda. Sem os dois, a barra dizia R$ 50 e o
    rodapé da lista dizia R$ 750 pra mesma categoria e o mesmo mês."""
    import db
    from utils_date import today_tz
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50, "compra", None, categoria="mercado",
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 700, "transferencia", None, categoria="mercado",
        is_internal_movement=True,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "receita", 40, "estorno", None, categoria="mercado",
    )
    ini = today_tz().replace(day=1).isoformat()
    fim = today_tz().isoformat()
    base = f"/categories/{pro_user_id}/launches?categoria=mercado&from={ini}&to={fim}"

    filtrado = client.get(
        base + "&tipo=despesa&include_internal=false", headers=_auth(pro_user_id),
    ).json()
    assert filtrado["resumo"] == {"n_total": 1, "despesa": 50.0, "receita": 0.0}, filtrado
    assert [r["descricao"] for r in filtrado["launches"]] == ["compra"]

    # Controle positivo: sem os filtros a rota continua mostrando TUDO (é o que
    # a pill "Ver lançamentos" abre, e é o comportamento dos 3 chamadores do bot).
    completo = client.get(base, headers=_auth(pro_user_id)).json()
    assert completo["resumo"]["n_total"] == 3, completo["resumo"]
    assert completo["resumo"]["despesa"] == 750.0


def test_janela_do_plano_corta_o_historico_inteiro(pro_user_id, monkeypatch):
    """Sem `from`, a rota não pode virar uma porta pra ignorar o teto de
    histórico do plano — mesmo clamp de /history/{id}/list."""
    import datetime as _dt

    import db
    from core.services import plan_service
    from utils_date import today_tz

    antigo = _dt.datetime.combine(
        today_tz() - _dt.timedelta(days=400), _dt.time(9, 0),
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 11, "ano passado", None,
        categoria="mercado", criado_em=antigo,
    )
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 22, "agora", None, categoria="mercado",
    )
    corte = today_tz() - _dt.timedelta(days=30)
    monkeypatch.setattr(plan_service, "history_earliest_date", lambda _uid: corte)

    body = client.get(
        f"/categories/{pro_user_id}/launches?categoria=mercado",
        headers=_auth(pro_user_id),
    ).json()
    assert [r["descricao"] for r in body["launches"]] == ["agora"], body["launches"]
    # E a janela REALMENTE aplicada volta no corpo — é ela que o dashboard usa
    # pra escrever "desde dd/mm/aaaa" em vez de mentir "todo o histórico".
    assert body["window"]["from"] == corte.isoformat(), body["window"]
    # `capped_by_plan` é o que faz o subtítulo da tela parar de dizer "Tudo nesta
    # categoria" mostrando um mês. `window.from` sozinho não distingue "o plano
    # cortou" de "o usuário pediu esta janela" — e pela Distribuição do mês, que
    # SEMPRE manda from/to, o corte ficaria invisível.
    assert body["window"]["capped_by_plan"] is True, body["window"]


def test_sem_teto_de_plano_a_janela_volta_aberta(pro_user_id, monkeypatch):
    # Controle positivo do clamp: quem não tem teto continua vendo tudo.
    import datetime as _dt

    import db
    from core.services import plan_service
    from utils_date import today_tz

    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 11, "ano passado", None, categoria="mercado",
        criado_em=_dt.datetime.combine(today_tz() - _dt.timedelta(days=400), _dt.time(9)),
    )
    monkeypatch.setattr(plan_service, "history_earliest_date", lambda _uid: None)
    body = client.get(
        f"/categories/{pro_user_id}/launches?categoria=mercado",
        headers=_auth(pro_user_id),
    ).json()
    assert [r["descricao"] for r in body["launches"]] == ["ano passado"]
    assert body["window"] == {"from": None, "to": None, "capped_by_plan": False}


def test_nota_alvo_e_criado_em_saem_na_resposta(pro_user_id):
    """O editor do dashboard abre a partir DESTAS chaves. Sem elas ele
    pré-preenchia "Descrição" com o rótulo (que é o alvo) e o Salvar gravava o
    alvo por cima da nota real."""
    import db
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 49.9, "recorrente:Netflix", "cobrança de 10/02",
        categoria="assinaturas",
    )
    row = client.get(
        f"/categories/{pro_user_id}/launches?categoria=assinaturas",
        headers=_auth(pro_user_id),
    ).json()["launches"][0]
    assert row["descricao"] == "recorrente:Netflix"
    assert row["nota"] == "cobrança de 10/02"
    assert row["alvo"] == "recorrente:Netflix"
    # `data` é o dia de PAREDE em São Paulo; `criado_em` sai no fuso da sessão do
    # Postgres. Comparar as duas STRINGS cruas dava falso vermelho toda noite
    # depois das 21h (ou toda madrugada, dependendo do fuso da sessão).
    from datetime import datetime
    from zoneinfo import ZoneInfo
    inst = datetime.fromisoformat(row["criado_em"])
    assert inst.astimezone(ZoneInfo("America/Sao_Paulo")).date().isoformat() == row["data"], row
    # o par que o `fmtLaunchWhen` do dashboard lê pra decidir se imprime a HORA
    assert row["has_time"] is True, row
    assert row["posted_at"] is None, row


# ══ M5. As duas listas de chaves escritas à mão ═════════════════════════════

def test_chaves_da_rota_batem_com_a_fixture_do_frontend(pro_user_id):
    r"""§0.7: a resposta desta rota e a fixture `LINHAS` de
    tests/frontend/dashboard_category_escape.test.mjs são a MESMA lista de
    chaves, escrita duas vezes. Sem este teste, renomear `has_time` na rota
    deixava os 51 testes de frontend verdes (eles mockam o fetch) e a tela
    quebrada — o mesmo remédio que tests/test_phosphor_subset.py já dá aos
    ícones.

    Controle NEGATIVO: apague `has_time` do dict de `list_launches_by_category`
    (db/accounts.py) e este teste fica vermelho apontando a chave que sumiu.
    """
    import pathlib
    import re

    import db

    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 10, "pao", None, categoria="mercado",
    )
    resp = client.get(
        f"/categories/{pro_user_id}/launches?categoria=mercado",
        headers=_auth(pro_user_id),
    )
    assert resp.status_code == 200, resp.text
    da_rota = set(resp.json()["launches"][0])

    mjs = (pathlib.Path(__file__).resolve().parent
           / "frontend/dashboard_category_escape.test.mjs").read_text(encoding="utf-8")
    bloco = re.search(r"const LINHAS = \[(.*?)\n\];", mjs, re.S)
    assert bloco, "a fixture LINHAS sumiu ou mudou de forma"
    # Os valores são escalares (string, número, null, bool) — nenhum objeto
    # aninhado —, então "identificador seguido de dois-pontos" já dá as chaves.
    # `-03:00` e `T09:00` não casam: o que vem antes do `:` não é [a-z_].
    da_fixture = set(re.findall(r"(?:^|[{,]\s*)([a-z_]+):", bloco.group(1), re.M))

    assert da_fixture == da_rota, {
        "só na rota (a fixture do front não conhece)": sorted(da_rota - da_fixture),
        "só na fixture (a rota não manda mais)": sorted(da_fixture - da_rota),
    }


# ══ D1. cursor (keyset) ════════════════════════════════════════════════════
# Era `offset`. Trocou porque o bot escreve no banco com o dashboard aberto: uma
# linha nova entra ACIMA do corte e desloca a fronteira do OFFSET — a página 2
# repete a última da 1 e come outra (`db/accounts.py`).

def _n_linhas(uid, n):
    import db
    for i in range(n):
        db.add_launch_and_update_balance(
            uid, "despesa", 10 + i, f"item {i:02d}", None, categoria="mercado",
        )


def test_cursor_pagina_a_lista_sem_repetir(pro_user_id):
    """Controle POSITIVO do "Carregar mais": p1 + p2 == a lista inteira, e o
    `resumo` continua sendo o total REAL nas duas páginas."""
    _n_linhas(pro_user_id, 5)
    h = _auth(pro_user_id)
    base = f"/categories/{pro_user_id}/launches?categoria=mercado&limit=3"
    p1 = client.get(base, headers=h).json()
    assert p1["next_cursor"], p1
    p2 = client.get(f"{base}&cursor={quote(p1['next_cursor'])}", headers=h).json()

    assert p1["resumo"]["n_total"] == p2["resumo"]["n_total"] == 5
    # o `ord_id` (id CRU das duas tabelas) fica dentro do cursor e não vaza no corpo
    assert "next_after" not in p1["resumo"], p1["resumo"]
    assert all("ord_id" not in r for r in p1["launches"]), p1["launches"][0]
    d1 = [r["descricao"] for r in p1["launches"]]
    d2 = [r["descricao"] for r in p2["launches"]]
    assert len(d1) == 3 and len(d2) == 2, (d1, d2)
    assert len(set(d1) & set(d2)) == 0, (d1, d2)
    inteiro = client.get(
        f"/categories/{pro_user_id}/launches?categoria=mercado&limit=50", headers=h,
    ).json()
    assert d1 + d2 == [r["descricao"] for r in inteiro["launches"]]


def test_cursor_com_lancamento_novo_no_meio_nao_repete_nem_come(pro_user_id):
    """O cenário de verdade: chega um gasto pelo WhatsApp entre a página 1 e o
    "Carregar mais". Com OFFSET, `item 02` voltaria repetido e `item 00` sumiria."""
    import db
    _n_linhas(pro_user_id, 6)
    h = _auth(pro_user_id)
    base = f"/categories/{pro_user_id}/launches?categoria=mercado&limit=3"
    p1 = client.get(base, headers=h).json()

    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 99, "intruso", None, categoria="mercado",
    )

    p2 = client.get(f"{base}&cursor={quote(p1['next_cursor'])}", headers=h).json()
    vistos = [r["descricao"] for r in p1["launches"] + p2["launches"]]
    assert vistos == ["item 05", "item 04", "item 03", "item 02", "item 01", "item 00"], vistos


def test_cursor_corrompido_e_400(pro_user_id):
    """Fronteira de confiança: texto de query string vira parâmetro de SQL. Não
    pode virar 500 nem uma página silenciosamente errada."""
    h = _auth(pro_user_id)
    for ruim in ("lixo", "2026-01-01T00:00:00+00:00|launches",
                 "2026-01-01T00:00:00+00:00|launches|abc",
                 "2026-01-01T00:00:00+00:00|outra|1",
                 "nao-e-data|launches|1"):
        resp = client.get(
            f"/categories/{pro_user_id}/launches?categoria=mercado&cursor={quote(ruim)}",
            headers=h,
        )
        assert resp.status_code == 400, (ruim, resp.status_code, resp.text)


def test_sem_cursor_continua_na_primeira_pagina(pro_user_id):
    """Aditivo: quem não manda `cursor` vê exatamente o que via antes."""
    _n_linhas(pro_user_id, 4)
    h = _auth(pro_user_id)
    sem = client.get(
        f"/categories/{pro_user_id}/launches?categoria=mercado&limit=2", headers=h,
    ).json()
    vazio = client.get(
        f"/categories/{pro_user_id}/launches?categoria=mercado&limit=2&cursor=",
        headers=h,
    ).json()
    assert sem == vazio
