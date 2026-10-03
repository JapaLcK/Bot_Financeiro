"""`GET /api/v2/lancamentos` (`db/lancamentos.py`) pelo monólito real: sessão e banco reais.

- regra: a soma dos itens não internos do mês = Entrou/Saiu de `resumo-do-mes` (a matriz de
  `test_resumo_mes_regra.py`: cartão pela fatura, legado, interno, pendente, fatura NULL),
  também com a janela do plano cortando o mês. Controle NEGATIVO executável: a perna do
  cartão pela data da COMPRA (`test_cartao_pela_compra_quebra_a_soma`). Positivo: a matriz casa.
- isolamento: B não vê nada de A, nem com o mesmo `provider_*_id`, nem pedindo a conta, o
  cartão ou o cursor de A; e a resposta não carrega `provider_*_id`, `raw` nem `external_id`.
- keyset: lançamento novo no meio da paginação não repete nem pula linha.
- 422 no envelope: cursor adulterado, mês futuro, limite 0, id fora da faixa.
A tabela de `pode` × estado: `tests/test_api_v2_lancamentos_pode.py`.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

import db
import frontend.finance_bot_websocket_custom as dashboard
from _apoio_auth_app import sessao_de
from api.v2.lancamentos import Lancamento, Lancamentos
from conftest import usuario_pagante
from core.services import plan_service
from db import lancamentos
from tests._patrimonio_helpers import conexao, conta, q, tx_banco
from tests.test_api_v2_resumo_mes import libera  # noqa: F401 (fixture)
from tests.test_resumo_mes_regra import ENTROU, INICIO, SAIU, _lanc, semeia_a, semeia_b
from utils_date import _tz

D = Decimal
M = f"{INICIO:%Y-%m}"


def pede(quem, url="/api/v2/lancamentos", **params):
    client = TestClient(dashboard.app)
    client.cookies.set(dashboard.DASHBOARD_COOKIE_NAME, sessao_de(quem)["dashboard"])
    return client.get(url, params=params)


def ok(quem, **params) -> dict:
    r = pede(quem, **params)
    assert r.status_code == 200, r.text
    Lancamentos.model_validate(r.json())
    return r.json()


def todos(quem, cursor=None, **params) -> list[dict]:
    """Todas as páginas, seguindo `proximo` (a partir de `cursor`, se vier)."""
    itens = []
    for _ in range(50):  # teto: um keyset quebrado repetiria a página para sempre
        r = ok(quem, **params, **({"cursor": cursor} if cursor else {}))
        itens += r["itens"]
        if not (cursor := r["proximo"]):
            return itens
    raise AssertionError("paginação não termina")


def soma(itens) -> tuple[Decimal, Decimal]:
    fora = [i for i in itens if not i["interno"]]
    return (sum((D(i["valor"]) for i in fora if i["tipo"] == "entrada"), D(0)),
            sum((D(i["valor"]) for i in fora if i["tipo"] == "saida"), D(0)))


def resumo(quem, **params) -> tuple[Decimal, Decimal]:
    r = pede(quem, "/api/v2/resumo-do-mes", **params).json()
    return D(r["entrou"]), D(r["saiu"])


# ── a regra do mês ──────────────────────────────────────────────────────────

def test_soma_dos_itens_e_o_resumo_do_mes(libera):
    a = usuario_pagante()
    semeia_a(a)
    libera(a)
    itens = todos(a, mes=M, limite=3)  # paginando: a soma atravessa as páginas
    assert soma(itens) == resumo(a, mes=M) == (ENTROU, SAIU)
    assert any(i["interno"] for i in itens)  # o interno entra, marcado, fora da soma
    cartao = sorted(D(i["valor"]) for i in itens if i["id"].startswith("c"))
    assert cartao == [D(50), D(80), D(100)]  # pela fatura: nem a de 999 nem o estorno
    parcela = next(i for i in itens if i["parcela"])
    assert parcela["parcela"] == {"n": 1, "total": 3} and parcela["fatura"] == M
    assert parcela["data"] == f"{(INICIO - timedelta(days=1)).replace(day=20)}"  # dia da COMPRA (P6)


def test_cartao_pela_compra_quebra_a_soma(libera, monkeypatch):
    """Controle NEGATIVO: a perna do cartão pela data da compra, e não pela fatura, sai do
    Entrou/Saiu do Resumo — o teste de cima ficaria vermelho."""
    a = usuario_pagante()
    semeia_a(a)
    libera(a)
    monkeypatch.setattr(lancamentos, "_PERNA_CARTAO",
                        lancamentos._PERNA_CARTAO.replace("b.period_end", "ct.purchased_at"))
    assert soma(todos(a, mes=M))[1] != SAIU


def test_janela_do_plano_corta_igual_ao_resumo(libera, monkeypatch):
    a = usuario_pagante()
    semeia_a(a)
    libera(a)
    corte = INICIO.replace(day=5)
    monkeypatch.setattr(plan_service, "history_earliest_date", lambda uid, now=None: corte)
    r = ok(a, mes=M)
    assert soma(todos(a, mes=M)) == resumo(a, mes=M) == (0, D("360"))
    assert "inicio_do_historico" in r["motivos"]
    assert all(i["data"] >= f"{corte}" for i in r["itens"] if i["id"].startswith("l"))
    # com busca, a janela inteira do plano em vez do mês: o 999 da fatura seguinte aparece
    achou = todos(a, mes=M, q="fatura seguinte")
    assert [D(i["valor"]) for i in achou] == [D(999)] and achou[0]["fatura"] > M
    assert "inicio_do_historico" in ok(a, mes=M, q="fatura seguinte")["motivos"]


def test_sem_corte_nao_marca(libera):
    a = usuario_pagante()
    semeia_a(a)
    libera(a)
    assert "inicio_do_historico" not in ok(a, mes=M)["motivos"]
    assert ok(a, mes=M)["motivos"] == ["conciliacao_pendente"]  # a matriz tem o par pendente


def test_data_e_hora_seguem_launch_day():
    """§0.7: o dia sai do SQL (precisa dele na ordem do keyset); `launch_day` é a regra."""
    from utils_date import launch_day

    a = usuario_pagante()
    noite = datetime.combine(INICIO.replace(day=10), time(23, 50), tzinfo=_tz())
    manual = db.add_launch_and_update_balance(a, "despesa", 10, "x", None, criado_em=noite)[0]
    of = _lanc(a, "despesa", 20, 11, source="open_finance")  # sem hora: manda o `posted_at`
    q("update launches set posted_at = %s where id = %s returning id", (INICIO.replace(day=12), of))
    from db.connection import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        itens = {r["id"]: r for r in lancamentos.listar(cur, a, INICIO, INICIO + timedelta(days=40))[0]}
        cur.execute("select id, criado_em, posted_at, source from launches where user_id = %s", (a,))
        crus = {r["id"]: r for r in cur.fetchall()}
    assert (itens[manual]["dia"], itens[manual]["hora"]) == (launch_day(noite), "23:50")
    sem_hora = crus[of]
    assert itens[of]["dia"] == launch_day(sem_hora["criado_em"], sem_hora["posted_at"], False) == INICIO.replace(day=12)
    assert itens[of]["hora"] is None


# ── isolamento ──────────────────────────────────────────────────────────────

def _banco_com_os_mesmos_ids(uid) -> tuple[int, int]:
    """Conta e transação com ids do PROVEDOR iguais para todo usuário; devolve (conta, lançamento)."""
    acc = conta(conexao(uid, f"item-igual-{uid}"), "ACC-IGUAL", "10")
    lid = _lanc(uid, "despesa", 41, 9, source="open_finance")
    q("update launches set external_id = 'TX-IGUAL', efeitos = %s where id = %s returning id",
      (Jsonb({"delta_conta": 0, "open_finance": {"provider_transaction_id": "TX-IGUAL"}}), lid))
    tx_banco(acc, "TX-IGUAL", "-41", imported_launch_id=lid, raw=Jsonb({"segredo": "RAW-SEGREDO"}))
    return acc, lid


def test_b_nao_ve_nada_de_a(libera):
    a, b = usuario_pagante(), usuario_pagante()
    semeia_a(a)
    semeia_b(b)
    conta_a, lanc_a = _banco_com_os_mesmos_ids(a)
    conta_b, lanc_b = _banco_com_os_mesmos_ids(b)
    cartao_a = q("select id from credit_cards where user_id = %s and name = 'Nubank'", (a,))["id"]
    libera(a, b)
    de_b = todos(b, mes=M, user_id=a)
    assert sorted(D(i["valor"]) for i in de_b) == sorted([D(7777), D(5555), D(888), D(41)])
    assert soma(de_b) == resumo(b, mes=M)
    # a conta e o cartão de A pedidos por B = igual a id inexistente
    assert ok(b, mes=M, conta=conta_a)["itens"] == ok(b, mes=M, conta=999_999_999)["itens"] == []
    assert ok(b, mes=M, cartao=cartao_a)["itens"] == []
    # positivo: cada um acha o seu pela conta, com o id que /contas devolve
    assert [i["id"] for i in ok(a, mes=M, conta=conta_a)["itens"]] == [f"l{lanc_a}"]
    assert [i["id"] for i in ok(b, mes=M, conta=conta_b)["itens"]] == [f"l{lanc_b}"]
    assert ok(a, mes=M, conta=conta_a)["itens"][0]["conta_id"] == conta_a
    assert {D(i["valor"]) for i in ok(a, mes=M, cartao=cartao_a)["itens"]} == {D(80), D(100)}
    # o cursor de A nas mãos de B pagina só a lista de B
    cursor_a = ok(a, mes=M, limite=1)["proximo"]
    assert {i["id"] for i in ok(b, mes=M, cursor=cursor_a)["itens"]} <= {i["id"] for i in de_b}


def test_compra_de_a_no_cartao_de_b_sai_sem_o_cartao(libera):
    """Barreira do cartão (`test_barreira_cartao._cena`: 4321 e o 3x300 de A com o cartão de B):
    o valor conta, `cartao_id` e `instituicao` saem NULL e o filtro pelo cartão de B não acha.
    Positivo: a compra de 80 no cartão de A sai com ele e o filtro dele a acha."""
    from tests.test_barreira_cartao import _cena

    a, _ = _cena()
    cartao_a = q("select id from credit_cards where user_id = %s", (a,))["id"]
    cartao_b = q("select card_id from credit_transactions where user_id = %s and valor = 4321", (a,))["card_id"]
    libera(a)
    itens = [i for i in todos(a, mes=M) if i["id"].startswith("c")]
    assert {D(i["valor"]): (i["cartao_id"], i["instituicao"]) for i in itens} == {
        D(80): (cartao_a, None), D(4321): (None, None), D(100): (None, None)}
    assert ok(a, mes=M, cartao=cartao_b)["itens"] == []
    assert [D(i["valor"]) for i in ok(a, mes=M, cartao=cartao_a)["itens"]] == [D(80)]


def test_resposta_nao_carrega_id_do_provedor_nem_raw(libera):
    a = usuario_pagante()
    _, lid = _banco_com_os_mesmos_ids(a)
    q("update launches set criado_em = now() where id = %s returning id", (lid,))
    tx = q("select id from open_finance_transactions where imported_launch_id = %s", (lid,))["id"]
    libera(a)
    r = pede(a)
    assert r.status_code == 200 and [i["id"] for i in r.json()["itens"]] == [f"l{lid}"]
    for proibido in ("TX-IGUAL", "ACC-IGUAL", "RAW-SEGREDO", "provider", "raw", "external"):
        assert proibido not in r.text, proibido
    # nem o id da transação do banco: só os campos do contrato saem
    assert set(r.json()["itens"][0]) == set(Lancamento.model_fields) and tx


# ── keyset ──────────────────────────────────────────────────────────────────

def test_lancamento_novo_no_meio_da_paginacao_nao_repete_nem_pula(libera):
    a = usuario_pagante()
    ids = [_lanc(a, "despesa", 10 + d, d) for d in range(2, 8)]
    libera(a)
    p1 = ok(a, mes=M, limite=2)
    _lanc(a, "despesa", 99, 27)  # mais novo que a página 1: num OFFSET empurraria a fronteira
    vistos = [i["id"] for i in p1["itens"]] + [i["id"] for i in todos(a, mes=M, limite=2, cursor=p1["proximo"])]
    assert vistos == [f"l{i}" for i in reversed(ids)]  # cada um uma vez, na ordem


# ── 422 e filtros ───────────────────────────────────────────────────────────

def _cursor(*chave) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(chave)).encode()).decode()


@pytest.mark.parametrize("params, campo", [
    ({"cursor": "nao-e-base64!!"}, "cursor"),
    ({"cursor": _cursor("2026-01-01", "2026-01-01T00:00:00+00:00", "x", 1)}, "cursor"),
    ({"cursor": _cursor("2026-01-01", "2026-01-01T00:00:00", "l", 1)}, "cursor"),  # sem fuso
    ({"cursor": _cursor("2026-01-01", "2026-01-01T00:00:00+00:00", "l", 2**63)}, "cursor"),
    ({"cursor": _cursor("2026-01-01", "2026-01-01T00:00:00+00:00", "l", "1")}, "cursor"),
    ({"cursor": _cursor("2026-01-01", "2026-01-01T00:00:00+00:00", "l")}, "cursor"),
    ({"mes": "2999-01"}, "mes"), ({"mes": "2026-13"}, "mes"),
    ({"limite": 0}, "limite"), ({"conta": 2**63}, "conta"), ({"cartao": 0}, "cartao"),
    ({"origem": "outra"}, "origem"), ({"tipo": "despesa"}, "tipo"), ({"q": "x" * 201}, "q"),
])
def test_entrada_invalida_e_422_no_envelope(libera, params, campo):
    (a,) = libera(usuario_pagante())
    r = pede(a, **params)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["details"][0]["loc"] == ["query", campo]


def test_filtros_e_limite(libera):
    a = usuario_pagante()
    semeia_a(a)
    libera(a)
    itens = todos(a, mes=M)
    assert {i["tipo"] for i in ok(a, mes=M, tipo="entrada")["itens"]} == {"entrada"}
    assert sorted(D(i["valor"]) for i in todos(a, mes=M, origem="banco")) == [D(30), D(70), D(200)]
    assert {i["origem"] for i in itens} == {"banco", "registro_antigo"}  # nada marcado ainda
    assert len(ok(a, mes=M, limite=500)["itens"]) == min(len(itens), 100)
    casa = todos(a, mes=M, categoria="Casa")  # chave e nome casam pela chave
    assert [D(i["valor"]) for i in casa] == [D(100)] and casa[0]["categoria"] == "casa"


def test_categorias_da_chave_e_nome_do_usuario(libera):
    a, b = usuario_pagante(), usuario_pagante()
    q("insert into user_categories (user_id, name) values (%s, 'Viagem Só Minha') returning id", (a,))
    libera(a, b)
    de_a = pede(a, "/api/v2/categorias").json()["categorias"]
    assert {"chave": "alimentacao", "nome": "alimentação"} in de_a
    assert {"chave": "viagem so minha", "nome": "Viagem Só Minha"} in de_a
    assert not any(c["nome"] == "Viagem Só Minha" for c in pede(b, "/api/v2/categorias").json()["categorias"])
