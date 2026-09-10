"""A VOLTA da fronteira: o que sai das rotas do Pix também é público (#350).

O gêmeo de `tests/test_vocabulario_de_plano.py`, que prende a IDA (o corpo que
entra). Arquivo separado, e não mais uma seção lá, porque o teto de 350 linhas
(`tests/test_max_lines_python.py`) reprovou a soma — e a divisão por ASSUNTO
caiu bem: entra × sai são as duas metades da mesma fronteira, e cada uma tem os
seus controles.

Quatro saídas carregavam o valor LEGADO da coluna, medidas na `main` em
`b71de69`:

    resposta do checkout   `core/services/pix_checkout_resposta.py`
    resposta do poll       `frontend/routes/billing_pix.py`
    corpo do 409           `frontend/routes/billing_pix.py`
    `descricao` da fatura  `core/services/pix_checkout.py`

As três primeiras não têm consumidor nenhum hoje — é por isso que ninguém viu.
A quarta o cliente lê: saía `PigBank anual (pro_max)` no app do banco de quem
paga.

CONTROLES DESTE GRUPO (medidos, não prometidos):

  * NEGATIVO — uma mutação por saída, RODADAS, com os nomes que caem:

      resposta do checkout   `tier_publico(linha["plan"])` → `linha["plan"]`
        em `pix_checkout_resposta.py`  → 4 vermelhos:
        `..._falam_publico[plus]`, `[pro]`, `..._pro_max_escapar`,
        `..._consumidor_real_continua_casando`
      resposta do poll       idem em `frontend/routes/billing_pix.py`
        → `..._falam_publico[plus]`, `[pro]`, `..._pro_max_escapar`
      corpo do 409           `tier_publico(exc.plano)` → `exc.plano`
        → `test_o_409_de_cobertura_ja_paga_fala_publico`
      `descricao` da fatura  volta a `f"PigBank anual ({linha['plan']})"`
        → os 3 `..._nome_comercial[*]` e `..._pro_max_escapar`

    `[essencial]` continua VERDE nas três primeiras de propósito: ali o legado e
    o público são a MESMA string, e é `[plus]`/`[pro]` que discriminam.
  * POSITIVO — `test_o_consumidor_real_continua_casando`. O único consumidor de
    `plan` que existe hoje é `pixSub.plan === plano`
    (`frontend/pix-checkout.js:101`), e ele lê o `/billing/subscription`, que
    JÁ falava público. Que ele MEDE está provado por uma quinta mutação:
    `tier_publico` devolvendo um vocabulário TERCEIRO
    (`{"pro": "PLUS", "pro_max": "PRO"}`) o deixa vermelho junto com
    `..._falam_publico[plus]`, `[pro]` e o 409 — e o portão do `pro_max` fica
    VERDE, porque legado nenhum vazou. Sem este caso o grupo aprovaria a
    tradução que quebra o botão "Renovar" de quem já é assinante.

O que este arquivo NÃO pega: o provedor é falso (`asaas_falso`), então o que ele
faz com a `descricao` não é medido — só o texto que sai daqui. E o JS não roda:
a ponta do `pixSub.plan === plano` é verificada comparando as respostas das duas
rotas, não clicando no botão.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.billing_pix as rotas
from _billing_grants_helpers import conta
from _pix_checkout_helpers import (  # noqa: F401 — fixtures
    _marcar_paga, asaas_falso, vendavel)
from core.services.email_service import PIX_PLAN_NAMES
from core.services.plan_service import TIER_TO_STORED_PLAN

_CSRF = "test-csrf-saidas"

# O molde da `descricao`, para o assert não repetir o literal três vezes. O NOME
# vem de `PIX_PLAN_NAMES` (§0.7) — cravá-lo aqui criaria a segunda fonte do nome
# que o e-mail de confirmação já usa.
_FATURA = "{} — plano anual"


@pytest.fixture(autouse=True)
def _sem_teto_de_taxa():
    """20/hora por IP, e o storage do slowapi é compartilhado entre arquivos:
    sem zerar, o último teste cai com 429 por causa do vizinho."""
    try:
        dashboard.limiter._storage.reset()
    except Exception:  # noqa: BLE001 — storage é detalhe do slowapi
        pass
    yield


@pytest.fixture()
def logado(monkeypatch, user_id):
    """Cliente HTTP com as rotas do Pix e a `/billing/subscription` autenticadas.

    As duas autenticam por caminhos DIFERENTES (o Pix pelo cookie de dashboard,
    o monólito por `Depends(_get_current_user)`), e o POSITIVO deste arquivo
    compara as duas — com uma só logada ele mediria 401.

    `app` é lido AQUI e não no topo do módulo: `tests/test_pix_rota_registrada.py`
    faz `importlib.reload` do monólito, e um `TestClient` de import-time fica
    preso ao app ANTIGO enquanto o override cai no novo (efeito de ORDEM).
    """
    conta(user_id, "free", None)
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id",
                        lambda req: user_id)
    app = dashboard.app
    app.dependency_overrides[dashboard._get_current_user] = lambda: user_id
    cliente = TestClient(app)
    cliente.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF)
    yield SimpleNamespace(user_id=user_id, http=cliente)
    app.dependency_overrides.pop(dashboard._get_current_user, None)


def _checkout(logado, plan: str):
    return logado.http.post(
        "/billing/pix/checkout", headers={dashboard.CSRF_HEADER_NAME: _CSRF},
        json={"plan": plan, "cpf_cnpj": "12345678901"})


_CARDS = [("essencial", "essencial"), ("plus", "pro"), ("pro", "pro_max")]
_IDS = ["essencial", "plus", "pro"]


@pytest.mark.parametrize("publico,legado", _CARDS, ids=_IDS)
def test_as_saidas_do_pix_falam_publico(logado, vendavel, asaas_falso,
                                        publico, legado):
    """As DUAS respostas HTTP da compra, num fluxo só: quem pediu `plus` lê
    `plus` de volta no checkout e no poll, e a COLUNA continua legada.

    As duas juntas de propósito: a mesma tela lê as duas, e traduzir uma só
    trocaria o vazamento por uma DIVERGÊNCIA — pior, porque some num teste que
    olha uma resposta de cada vez.
    """
    from db.pix_charges import buscar_por_public_token

    r = _checkout(logado, publico)
    assert r.status_code == 200, r.text
    assert r.json()["plan"] == publico, (
        f"checkout devolveu '{r.json()['plan']}' para o card '{publico}'")

    token = r.json()["public_token"]
    poll = logado.http.get(f"/billing/pix/{token}")
    assert poll.status_code == 200, poll.text
    assert poll.json()["plan"] == publico, (
        f"poll devolveu '{poll.json()['plan']}' para o card '{publico}'")

    linha = buscar_por_public_token(logado.user_id, token)
    assert linha["plan"] == legado, "a coluna deixou de guardar o legado"


@pytest.mark.parametrize("publico,legado", _CARDS, ids=_IDS)
def test_a_fatura_do_pagador_traz_o_nome_comercial(logado, vendavel,
                                                   asaas_falso, publico, legado):
    """A ÚNICA das quatro saídas que o cliente já lê hoje: a `descricao` da
    cobrança, que aparece no app do banco de quem paga.

    Nome comercial e não o tier público (`pro`): numa fatura não há legenda para
    traduzir slug.
    """
    assert _checkout(logado, publico).status_code == 200
    assert asaas_falso["descricoes"] == [_FATURA.format(PIX_PLAN_NAMES[legado])]
    assert legado not in asaas_falso["descricoes"][0], (
        f"o valor de coluna '{legado}' foi para a fatura do pagador")


def test_o_409_de_cobertura_ja_paga_fala_publico(logado, vendavel, asaas_falso):
    """A saída que não tinha teste nenhum: o corpo da recusa.

    `exc.plano` é o `plan_stored` (provado em `tests/test_pix_recompra.py`:
    `capturado.value.plano == "pro_max"`), então o 409 devolvia `pro_max` ao
    mesmo JS que tinha mandado `pro`.

    O estado é montado COMPRANDO, não com `upsert_grant` de mentira: a recusa só
    nasce com um vigente de tier menor MAIS um grant Pix futuro já pago
    (`core/services/pix_pricing.py`), e grant sem cobrança por trás derruba a
    guarda do `amount_cents` antes de chegar à recusa — medido.
    """
    primeira = _checkout(logado, "essencial")
    assert primeira.status_code == 200, primeira.text
    _marcar_paga(logado.user_id, primeira.json()["public_token"], dias=-10)

    renovacao = _checkout(logado, "essencial")   # agendada: cobre o ano seguinte
    assert renovacao.status_code == 200, renovacao.text
    _marcar_paga(logado.user_id, renovacao.json()["public_token"], dias=355)

    r = _checkout(logado, "pro")
    assert r.status_code == 409, r.text
    detalhe = r.json()["detail"]
    assert detalhe["error"] == "pix_future_purchase_conflict"
    assert detalhe["plan"] == "pro", (
        f"o 409 devolveu '{detalhe['plan']}' para quem pediu 'pro'")
    assert asaas_falso["ordem"][-1] != "create", "a recusa emitiu cobrança"


def test_nenhuma_saida_do_pix_deixa_o_pro_max_escapar(logado, vendavel,
                                                      asaas_falso):
    """O PORTÃO DA CATEGORIA: a próxima saída nova nasce vermelha aqui.

    Os asserts acima olham o campo `plan` POR NOME, e por isso são cegos a um
    campo que ainda não existe — foi assim que quatro saídas passaram
    despercebidas. Este olha o corpo INTEIRO, serializado: qualquer chave que
    venha a carregar o valor da coluna cai aqui sem ninguém ter de se lembrar de
    escrever o assert.

    **O que ele NÃO pega, de propósito:** o legado do tier Plus é `pro`, que
    também é valor PÚBLICO válido (o tier Pro) — procurá-lo daria falso positivo
    em toda compra de Pro. `pro_max` é o único token puramente legado, e é por
    isso que a compra medida aqui é a de Pro. Contra o vazamento de `pro` valem
    os casos por campo acima, um por card.
    """
    r = _checkout(logado, "pro")
    assert r.status_code == 200, r.text
    poll = logado.http.get(f"/billing/pix/{r.json()['public_token']}")

    for onde, corpo in (("checkout", r.text), ("poll", poll.text),
                        ("fatura", asaas_falso["descricoes"][0])):
        assert "pro_max" not in corpo, (
            f"a resposta do {onde} carrega o valor de coluna 'pro_max': {corpo}")


def test_o_consumidor_real_continua_casando(logado, vendavel, asaas_falso):
    """POSITIVO do grupo. `pixSub.plan === plano` (`pix-checkout.js:101`) compara
    o `/billing/subscription` com o slug do card. Traduzir as saídas do Pix não
    pode desalinhar essa comparação — se desalinhar, o assinante Pix vê "Pagar"
    no lugar de "Renovar" e paga um ano que já tem.

    As três pontas na MESMA asserção de propósito: o slug que o JS itera, o que
    o `/billing/subscription` devolve, e o que o checkout do Pix passou a
    devolver.
    """
    r = _checkout(logado, "pro")
    _marcar_paga(logado.user_id, r.json()["public_token"])

    sub = logado.http.get("/billing/subscription")
    assert sub.status_code == 200, sub.text
    assert sub.json()["gateway"] == "pix", sub.text
    # `TIER_TO_STORED_PLAN` são os slugs dos cards — a mesma lista que o
    # `PIX_PLANOS` do JS percorre.
    assert sub.json()["plan"] in TIER_TO_STORED_PLAN
    assert sub.json()["plan"] == "pro" == r.json()["plan"], (
        "o /billing/subscription e o checkout do Pix divergiram: "
        f"{sub.json()['plan']} x {r.json()['plan']}")
