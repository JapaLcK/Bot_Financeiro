"""A VOLTA da fronteira: o que sai das rotas do Pix também é público (#350).

O gêmeo de `tests/test_vocabulario_de_plano.py`, que prende a IDA (o corpo que
entra). Arquivo separado, e não mais uma seção lá, porque o teto de 350 linhas
(`tests/test_max_lines_python.py`) reprovou a soma — e a divisão por ASSUNTO
caiu bem: entra × sai são as duas metades da mesma fronteira, e cada uma tem os
seus controles.

CINCO saídas DO PIX carregavam o valor LEGADO da coluna, medidas na `main` em
`b71de69`. **Cinco do Pix, e não cinco no produto**: fora daqui a categoria
segue ABERTA — `/auth/login`, `/auth/dashboard-profile`, `/auth/mfa/verify-login`
e `/settings/{id}/security` devolvem `plan` cru, e os `PLAN_ALIASES` de
`frontend/home.html` e `frontend/nav-auth.js` são o tradutor copiado em JS, sem
teste ligando-os a `_STORED_PLAN_TO_TIER` (§0.7). Fora do escopo deste PR (§2).

    resposta do checkout   `core/services/pix_checkout_resposta.py`
    resposta do poll       `frontend/routes/billing_pix.py`
    corpo do 409           `frontend/routes/billing_pix.py`
    `descricao` da fatura  `core/services/pix_checkout.py`
    `purchase` do GA4      `core/services/pix_drain_effects.py`

As três primeiras não têm consumidor nenhum hoje — é por isso que ninguém viu.
A quarta o cliente lê: saía `PigBank anual (pro_max)` no app do banco de quem
paga. A QUINTA saiu da primeira varredura deste arquivo (achado do Tester) e é a
que vira dinheiro em relatório: o `plan` do `purchase` é `item_id` e `item_name`
no GA4, e o Stripe já mandava o público no mesmo campo — o legado daqui colidia
o Pro consigo mesmo e o Plus do Pix (R$ 199) com o Pro do Stripe.

CONTROLES DESTE GRUPO (medidos, não prometidos):

  * NEGATIVO — uma mutação por saída, RODADAS (as cinco de novo depois de o
    portão ser parametrizado, porque o nome dele mudou), com os nomes que caem.
    `PORTAO` abaixo é `..._deixa_o_valor_de_coluna_escapar`:

      resposta do checkout   `tier_publico(linha["plan"])` → `linha["plan"]`
        em `pix_checkout_resposta.py`  → 5 vermelhos:
        `..._falam_publico[plus]`, `[pro]`, `PORTAO[plus]`, `PORTAO[pro]`,
        `..._consumidor_real_continua_casando`
      resposta do poll       idem em `frontend/routes/billing_pix.py`
        → 4: `..._falam_publico[plus]`, `[pro]`, `PORTAO[plus]`, `PORTAO[pro]`
      corpo do 409           `tier_publico(exc.plano)` → `exc.plano`
        → 1: `test_o_409_de_cobertura_ja_paga_fala_publico`
      `descricao` da fatura  volta a `f"PigBank anual ({linha['plan']})"`
        → 5: os 3 `..._nome_comercial[*]`, `PORTAO[plus]` e `PORTAO[pro]`
      `purchase` do GA4      `tier_publico(cobranca["plan"])` → `cobranca["plan"]`
        em `pix_drain_effects.py`
        → 2: `..._purchase_do_ga4_leva_o_plano_publico[plus]` e `[pro]`

    `[essencial]` continua VERDE em todas de propósito: ali o legado e o público
    são a MESMA string, e é `[plus]`/`[pro]` que discriminam.

    O PORTÃO tem DUAS mutações próprias, uma por versão dele, e cada uma pega o
    que a versão anterior deixava passar (as duas em `pix_checkout_resposta.py`):

      * legado SÓ do Plus (`"tier_atual": linha["plan"] if linha["plan"] ==
        "pro" else None`): a 1ª versão comprava só Pro e ficava VERDE com o
        grupo inteiro. Parametrizada → VERMELHO em `PORTAO[plus]`, e só nele.
      * legado EMBUTIDO NUMA FRASE (`"resumo": f"plano {linha['plan']} anual"`):
        a 2ª versão comparava VALOR e ficava VERDE nos dois cards (13 passed).
        Por substring → VERMELHO em `PORTAO[plus]` E `PORTAO[pro]`.
  * POSITIVO — `test_o_consumidor_real_continua_casando`. O único consumidor de
    `plan` que existe hoje é `pixSub.plan === plano`
    (`frontend/pix-checkout.js:101`), e ele lê o `/billing/subscription`, que
    JÁ falava público. Que ele MEDE está provado por mais uma mutação:
    `tier_publico` devolvendo um vocabulário TERCEIRO
    (`{"pro": "PLUS", "pro_max": "PRO"}`) o deixa vermelho junto com
    `..._falam_publico[plus]`, `[pro]`, o 409 e os dois do GA4 — e o `PORTAO`
    fica VERDE nos dois cards, porque legado nenhum vazou. Sem este caso o
    grupo aprovaria a tradução que quebra o botão "Renovar" de quem já é
    assinante.

O que este arquivo NÃO pega: o provedor é falso (`asaas_falso`), então o que ele
faz com a `descricao` não é medido — só o texto que sai daqui; e o GA4 também é
falso (`mundo_externo`), então o que se afirma é o valor que chega ao
`send_purchase`, não o que o relatório do Google mostra. E o JS não roda: a ponta
do `pixSub.plan === plano` é verificada comparando as respostas das duas rotas,
não clicando no botão.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.billing_pix as rotas
from _billing_grants_helpers import conta, garantir_system_event_logs
from _dreno_pix_helpers import entregar, mundo_externo, nova_cobranca
from _pix_checkout_helpers import (  # noqa: F401 — fixtures
    _marcar_paga, asaas_falso, vendavel)
from core.services.email_service import PIX_PLAN_NAMES
from core.services.pix_pricing import PRECOS_ANUAIS_CENTS
from core.services.plan_service import TIER_TO_STORED_PLAN

_CSRF = "test-csrf-saidas"

# O molde da `descricao`, para o assert não repetir o literal três vezes. O NOME
# vem de `PIX_PLAN_NAMES` (§0.7) — cravá-lo aqui criaria a segunda fonte do nome
# que o e-mail de confirmação já usa.
_FATURA = "{} - plano anual"


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
    # CPF que fecha o mod-11 (#355): documento inválido leva 400 antes do plano.
    return logado.http.post(
        "/billing/pix/checkout", headers={dashboard.CSRF_HEADER_NAME: _CSRF},
        json={"plan": plan, "cpf_cnpj": "52998224725"})


# Os cards da /precos, na ordem da escada e LIDOS da fonte (§0.7): plano vendável
# novo entra sozinho em todo `parametrize` daqui, o portão de categoria incluído.
_CARDS = list(TIER_TO_STORED_PLAN.items())
_IDS = [publico for publico, _ in _CARDS]

# O portão de categoria só DISCRIMINA onde as duas strings diferem: em
# `essencial` o legado É o público, e procurá-lo acusaria o próprio campo `plan`.
_DISCRIMINAM = [(p, l) for p, l in _CARDS if p != l]
_IDS_DISCRIMINAM = [publico for publico, _ in _DISCRIMINAM]


# Campos OPACOS: string que a MÁQUINA gera e ninguém lê como plano. Ficam fora
# da busca por substring porque o acaso os faz conter `pro` — `public_token` é
# `secrets.token_urlsafe(16)` (alfabeto de 64, ~20 posições: ~7,6e-5 por token) e
# `qr_image` é base64 de milhares de caracteres. Nenhum dos dois abre buraco: o
# `qr_image` é só o DESENHO do `qr_payload`, que CONTINUA sendo varrido, e num
# token aleatório plano nenhum tem como cair.
_OPACOS = {"qr_image", "public_token"}


def _valores(corpo, chave=None):
    """Todo escalar de um corpo JSON, em profundidade, COM a chave que o carrega
    — inclusive o do campo que ainda não existe, que é o ponto do portão.

    A chave sai junto por causa do `_OPACOS`: sem ela a exclusão teria de ser
    por conteúdo, que é adivinhação. Item de lista herda a chave da lista.
    """
    if isinstance(corpo, dict):
        for k, valor in corpo.items():
            yield from _valores(valor, k)
    elif isinstance(corpo, list):
        for valor in corpo:
            yield from _valores(valor, chave)
    else:
        yield chave, corpo


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


@pytest.mark.parametrize("publico,legado", _DISCRIMINAM, ids=_IDS_DISCRIMINAM)
def test_nenhuma_saida_do_pix_deixa_o_valor_de_coluna_escapar(
        logado, vendavel, asaas_falso, publico, legado):
    """O PORTÃO DA CATEGORIA: a próxima saída nova nasce vermelha aqui.

    Os asserts acima olham o campo `plan` POR NOME, e por isso são cegos a um
    campo que ainda não existe — foi assim que quatro saídas passaram
    despercebidas. Este olha o corpo INTEIRO: qualquer chave, em qualquer
    profundidade, que venha a carregar `TIER_TO_STORED_PLAN[publico]` cai aqui
    sem ninguém ter de se lembrar de escrever o assert.

    **Roda nos DOIS cards que discriminam, e a primeira versão não rodava.** Ela
    comprava só Pro e procurava o literal `pro_max`, então um campo novo que
    vazasse só o legado do Plus (`"tier_atual": "pro"`) passava com o grupo
    inteiro verde — medido pelo Tester. Um consumidor futuro desse campo marcaria
    "Renovar" no card Pro (R$ 499) para quem comprou Plus (R$ 199).

    **SUBSTRING, não igualdade de valor.** A versão anterior comparava valor e
    se justificava com um ruído de base64 no `qr_image` que NÃO existe: 500 data
    URLs de `qr_svg_data_url` sobre BR Codes aleatórios (média de 17.321 chars)
    deram `"pro" in url` ZERO vezes. E igualdade é cega ao modo que ESTE PR
    nomeou — `PigBank anual (pro_max)`, o legado EMBUTIDO numa frase —, que só a
    `descricao` cobria, campo a campo: o jeito de errar que o portão existe para
    não deixar errar. Substring cobre igualdade também: `legado` é sempre string,
    e escalar não-string nunca a igualaria.
    """
    r = _checkout(logado, publico)
    assert r.status_code == 200, r.text
    poll = logado.http.get(f"/billing/pix/{r.json()['public_token']}")
    assert poll.status_code == 200, poll.text

    assert legado == TIER_TO_STORED_PLAN[publico]   # o que se procura, da fonte
    for onde, corpo in (("checkout", r.json()), ("poll", poll.json())):
        vazou = [(c, v) for c, v in _valores(corpo)
                 if c not in _OPACOS and isinstance(v, str) and legado in v]
        assert not vazou, (
            f"o {onde} do card '{publico}' carrega o valor de coluna "
            f"'{legado}' em {vazou}")
    assert legado not in asaas_falso["descricoes"][0], (
        f"a fatura do card '{publico}' carrega o valor de coluna '{legado}': "
        f"{asaas_falso['descricoes'][0]}")


@pytest.mark.parametrize("publico,legado", _CARDS, ids=_IDS)
def test_o_purchase_do_ga4_leva_o_plano_publico(user_id, monkeypatch,
                                                publico, legado):
    """A QUINTA saída, e a única que vira DINHEIRO em relatório: o `plan` do
    `purchase` do GA4 (`core/services/pix_drain_effects.py`), que
    `core/services/ga4_mp.py` usa como `item_id` E `item_name`.

    O Stripe já traduz no mesmo destino (`_ga_plano_publico`), então o legado
    daqui colidia DUAS linhas de receita: o Pro virava dois produtos (`pro` pelo
    Stripe, `pro_max` pelo Pix) e o Plus do Pix (`pro`, R$ 199) caía na mesma
    linha do Pro do Stripe (R$ 39,90/mês). É o achado do Codex no #244.

    Pelo DRENO, não chamando `_ga4` na mão: o que se afirma é que o valor
    traduzido chega ao seam, e o `cobranca` que o efeito lê é montado pelo
    caminho real.
    """
    garantir_system_event_logs()
    conta(user_id, "free", None)
    externo = mundo_externo(monkeypatch)
    cobranca = nova_cobranca(user_id, plan=legado, plan_stored=legado,
                             price_cents=PRECOS_ANUAIS_CENTS[legado],
                             amount_cents=PRECOS_ANUAIS_CENTS[legado])

    entregar("PAYMENT_RECEIVED", cobranca)

    assert externo["ga4"] == 1, "o efeito ga4 não rodou — o assert abaixo mediria zero"
    enviado = externo["ga4_kw"][0]
    assert enviado["plan"] == publico, (
        f"o purchase do card '{publico}' foi para o GA4 como '{enviado['plan']}'")
    assert enviado["value"] == PRECOS_ANUAIS_CENTS[legado] / 100


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
