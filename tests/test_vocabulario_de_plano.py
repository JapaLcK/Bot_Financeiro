"""Um vocabulário por FRONTEIRA: o corpo do checkout Pix fala o mesmo que o do
Stripe, e a tradução para o legado acontece dentro.

O bug que este arquivo prende esteve VIVO em produção com a venda ligada
(`ASAAS_PIX_ANNUAL_ENABLED=1`, 2026-09-10). O JS é UM só e manda o slug público
da `/precos` nas duas rotas; a do Pix validava contra o vocabulário LEGADO da
coluna, e o resultado por card era:

    Essencial → `essencial`  ✔ (o único que alguém tinha testado)
    Plus      → `plus`       ✘ 400 `plan inválido` — o botão não vendia
    Pro       → `pro`        ✘ aceito, mas `pro` NO BANCO é o tier Plus:
                               R$ 199 cobrados num card que anuncia R$ 499

O terceiro é o coração do arquivo, e é por isso que o preço é ASSERÇÃO e não
comentário: um teste que só conferisse `plan_stored` ficaria verde no dia em que
`PRECOS_ANUAIS_CENTS` mudasse de chave.

CONTROLES DO GRUPO (medidos, não prometidos):

  * NEGATIVO — desfaça a tradução na rota (troque `plan_stored=plan_stored` por
    `plan_stored=plan` em `frontend/routes/billing_pix.py`) e
    `test_cada_card_cobra_o_preco_que_anuncia[pro]` fica VERMELHO cobrando
    19900 em vez de 49900. É o caso que DISCRIMINA: com `plan="pro"` o valor
    público e o legado são strings diferentes, então a mutação aparece.
    Injetada num caso verde de propósito — `essencial` traduz para si mesmo e
    seguiria passando, e `plus` já falharia por outro motivo.
  * POSITIVO — `test_cada_card_cobra_o_preco_que_anuncia[essencial]` é o card
    que JÁ funcionava em produção. Sem ele o grupo passaria numa rota que
    recusasse tudo, que é pior que o bug.

O que este arquivo NÃO pega: o Asaas é falso aqui (fixture `asaas_falso`), então
o que o provedor faz com o valor não é medido — só o que sai da nossa rota. O
banco é de verdade: as linhas de `pix_charges` são lidas do disco.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from fastapi.testclient import TestClient

import frontend.finance_bot_websocket_custom as dashboard
import frontend.routes.billing_pix as rotas
from _billing_grants_helpers import conta
from _pix_checkout_helpers import asaas_falso, vendavel  # noqa: F401 — fixtures
from core.services.pix_pricing import PRECOS_ANUAIS_CENTS
from core.services.plan_service import _STORED_PLAN_TO_TIER, TIER_TO_STORED_PLAN
from db.pix_charges import buscar_por_public_token


@pytest.fixture(autouse=True)
def _sem_teto_de_taxa():
    """O checkout é 20/hora por IP e o storage do slowapi é compartilhado entre
    arquivos: sem zerar, o último teste do grupo cai com 429 e a falha não tem
    nada a ver com plano."""
    try:
        dashboard.limiter._storage.reset()
    except Exception:  # noqa: BLE001 — storage é detalhe do slowapi
        pass
    yield


@pytest.fixture()
def logado(monkeypatch, user_id):
    """Cliente HTTP com as DUAS rotas autenticadas, e o usuário sem cobertura —
    toda compra aqui é a PRIMEIRA, então `credit_cents` é 0 e `amount_cents` é o
    preço cheio, que é o que deixa a asserção de dinheiro ser direta.

    `app` é lido AQUI, e não no topo do módulo, de propósito:
    `tests/test_pix_rota_registrada.py` faz `importlib.reload` do monólito, e um
    `TestClient(dashboard.app)` de import-time fica preso ao app ANTIGO enquanto
    o override cai no novo. O sintoma era 401 só quando os dois arquivos rodavam
    juntos — efeito de ORDEM, invisível no arquivo isolado.
    """
    conta(user_id, "free", None)
    monkeypatch.setattr(rotas.shared, "resolve_dashboard_user_id",
                        lambda req: user_id)
    app = dashboard.app
    # As duas rotas autenticam por caminhos DIFERENTES (o Pix pelo cookie de
    # dashboard, o Stripe por `Depends(_get_current_user)`), e a comparação entre
    # elas precisa das duas logadas — senão o teste mede 401, não vocabulário.
    app.dependency_overrides[dashboard._get_current_user] = lambda: user_id
    cliente = TestClient(app)
    cliente.cookies.set(dashboard.CSRF_COOKIE_NAME, _CSRF)
    yield SimpleNamespace(user_id=user_id, http=cliente)
    app.dependency_overrides.pop(dashboard._get_current_user, None)


_CSRF = "test-csrf-vocabulario"


def _cabecalhos() -> dict[str, str]:
    return {dashboard.CSRF_HEADER_NAME: _CSRF}


def _checkout(logado, plan: str):
    # CPF com o mod-11 fechando: o que este arquivo mede é o VOCABULÁRIO de plano,
    # e desde 2026-09-10 o router recusa documento estruturalmente inválido antes
    # de chegar ao preço. `12345678901` levava 400 aqui e o teste media a recusa
    # do documento achando que media a do plano.
    return logado.http.post("/billing/pix/checkout", headers=_cabecalhos(),
                            json={"plan": plan, "cpf_cnpj": "52998224725"})


# ── os três cards da /precos, ponta a ponta pela rota ────────────────────────

@pytest.mark.parametrize("publico,legado", [
    ("essencial", "essencial"),   # POSITIVO do grupo: o que já vendia
    ("plus", "pro"),              # dava 400 `plan inválido`
    ("pro", "pro_max"),           # cobrava 19900 num card de 49900
], ids=["essencial", "plus", "pro"])
def test_cada_card_cobra_o_preco_que_anuncia(logado, vendavel, asaas_falso,
                                             publico, legado):
    """As três metades de uma vez: aceitou, gravou o LEGADO, cobrou o preço dele.

    O preço esperado é LIDO de `PRECOS_ANUAIS_CENTS` pela chave legada (§0.7) —
    cravar 49900 aqui criaria uma segunda fonte do número que a `/precos` mostra.
    """
    r = _checkout(logado, publico)
    assert r.status_code == 200, r.text

    esperado = PRECOS_ANUAIS_CENTS[legado]
    assert r.json()["amount_cents"] == esperado, (
        f"card '{publico}' cobrou {r.json()['amount_cents']}, anuncia {esperado}")

    linha = buscar_por_public_token(logado.user_id, r.json()["public_token"])
    assert linha is not None, "a cobrança não foi gravada"
    # As DUAS colunas continuam no vocabulário legado: grants, projeção e o
    # `_plan_publico` do /billing/subscription dependem disso.
    assert linha["plan"] == legado
    assert linha["plan_stored"] == legado
    assert int(linha["price_cents"]) == esperado


def test_o_vocabulario_legado_nao_entra_mais_pela_rota(logado, vendavel,
                                                       asaas_falso):
    """`pro_max` é valor de COLUNA, e o JS nunca o mandou. Aceitá-lo deixaria a
    rota com dois vocabulários — que é a armadilha inteira, só que armada de
    novo. 400, e nenhuma cobrança nasce."""
    r = _checkout(logado, "pro_max")
    assert r.status_code == 400, r.text
    assert "essencial" in str(r.json()["detail"])
    assert asaas_falso["ordem"] == [], "recusa de plano falou com o Asaas"


# ── o par de dicionários, e o par de rotas (§0.7) ────────────────────────────

def test_os_dois_mapas_de_plano_sao_inversos():
    """Ida e volta: público → legado → tier tem de voltar ao público de origem.

    É o que impede os dois dicionários de derivarem em silêncio — mexer num sem
    o outro nasce vermelho aqui, e não como preço errado numa venda.
    """
    for publico, legado in TIER_TO_STORED_PLAN.items():
        assert _STORED_PLAN_TO_TIER[legado] == publico, (
            f"'{publico}' → '{legado}' → '{_STORED_PLAN_TO_TIER.get(legado)}'")
        assert dashboard._plan_publico(legado) == publico
    # E todo plano vendável tem preço: um valor sem entrada aqui viraria 503
    # `preco_anual_nao_configurado` só na hora da venda.
    assert set(TIER_TO_STORED_PLAN.values()) <= set(PRECOS_ANUAIS_CENTS)


@pytest.mark.parametrize("plan", [
    "essencial", "plus", "pro",   # públicos: nenhuma das duas rotas recusa
    "pro_max", "free",            # nenhuma das duas rotas aceita
    "PLUS",                       # as duas normalizam a caixa antes de validar
    "",                           # vazio: 400 nas duas (o Stripe fazia Plus, #352)
    " plus ",                     # espaços: as duas dão `.strip()` antes de validar
], ids=["essencial", "plus", "pro", "pro_max", "free", "PLUS", "vazio", "com-espacos"])
def test_pix_e_stripe_aceitam_a_MESMA_lista_de_planos(logado, vendavel,
                                                      asaas_falso, plan):
    """O gêmeo do Stripe (`/billing/create-checkout`) e o do Pix têm de concordar
    sobre o que é `plan` válido — a próxima divergência entre eles nasce vermelha
    aqui em vez de virar 400 num botão de venda.

    Mede só o veredito de VOCABULÁRIO (400 `plan inválido` × qualquer outra
    coisa): o Stripe responde 503 sem os price IDs configurados e o Pix responde
    200, e essa diferença é de configuração, não de plano.
    """
    def _recusa_o_plano(resp) -> bool:
        return resp.status_code == 400 and "plan inválido" in str(resp.json().get("detail"))

    pix = _checkout(logado, plan)
    stripe = logado.http.post("/billing/create-checkout", headers=_cabecalhos(),
                         json={"plan": plan, "interval": "annual"})
    # 401 significaria que o teste mediu autenticação, não plano.
    assert stripe.status_code != 401, "o cliente do Stripe não autenticou"
    assert _recusa_o_plano(pix) == _recusa_o_plano(stripe), (
        f"'{plan}': Pix {pix.status_code} × Stripe {stripe.status_code}")


# CEGUEIRA CONHECIDA da tabela acima: ela sempre manda a chave `plan` (nem que
# seja `""`), então nunca vê um corpo SEM a chave — e é exatamente aí que as
# três rotas divergem (medido em 2026-09-10; comando: POST com
# `{"interval":"annual"}` em cada uma):
#
#   /billing/create-checkout → 400 `plan inválido ...`   (detail STRING)
#   /billing/pix/checkout    → 422 `Field required`      (detail LISTA)
#   /billing/change-plan     → 422 `Field required`      (detail LISTA)
#
# Só o Stripe dá 400 porque só ele tem `plan: str = ""` no modelo, e só ele tem
# porque só ele aceita POST sem body nenhum (`payload: ... | None = None`) —
# a razão inteira está na docstring de `billing_create_checkout`. Não entra na
# tabela: a asserção dela é de IGUALDADE entre as colunas, e aqui a diferença é
# intencional. Fica ANOTADO porque `frontend/pix-checkout.js:269` tem a mesma
# forma de `JSON.stringify` que apagou a chave e gerou a #352: se um dia ela
# apagar `plan`, o Pix responde 422 e a /precos mostra o fallback genérico.


# ── /billing/change-plan: a mesma normalização, com semântica própria ────────
#
# Esta rota fica FORA da tabela de duas colunas acima, e a razão NÃO é
# incompatibilidade de status — essa foi medida e é FALSA: `_recusa_o_plano` já
# colapsa 200 e 503 em "não é recusa de plano", e colapsaria 409 igual. Uma
# terceira coluna PASSA: medido em 2026-09-10, as 8 linhas concordaram
# (`essencial/plus/pro/PLUS/" plus "` → pix 200, stripe 503, change 503, os três
# `recusa=False`; `pro_max/free/""` → 400 nos três), sem monkeypatch nenhum.
#
# Ela fica de fora porque a duplicação que vigiaria FOI REMOVIDA em vez de
# vigiada (§0.7 é sobre UMA fonte; o teste comparador é o que se faz quando a
# cópia é inevitável, e aqui não era): as três rotas de plano não têm mais tupla
# literal — todas validam contra `TIER_TO_STORED_PLAN`
# (`core/services/plan_service.py`), e um quarto tier entra num lugar só. Nenhum
# comentário guarda isso; o `not in` guarda.
#
# O que sobra medir é COMPORTAMENTO, e a coluna não saberia dizer:
#   * a ACEITAÇÃO aqui é o 409 `no_subscription` ESPECÍFICO, não "qualquer
#     coisa que não seja 400 `plan inválido`";
#   * a RECUSA é medida pelo TRABALHO — `chamadas == []`, a rota nem leu a
#     conta. O predicado da tabela só enxerga status.
#
# CONTROLES DO GRUPO:
#   * NEGATIVO — tire o `.strip()` de `plan = (payload.plan or "").strip().lower()`
#     em `frontend/finance_bot_websocket_custom.py` (rota `/billing/change-plan`)
#     e `test_change_plan_normaliza_como_o_checkout[com-espacos]` fica VERMELHO
#     (400 `plan inválido` onde se espera 409 `no_subscription`). Injetado num
#     caso que estava VERDE — `plus` sem espaços passa com e sem o conserto.
#   * POSITIVO — o caso `plus` prova que o plano legítimo continua atravessando
#     a validação e chegando a LER a conta; sem ele o grupo passaria numa rota
#     que recusasse tudo.

_ACEITA, _RECUSA = "aceita", "recusa"


@pytest.mark.parametrize("plan,veredito", [
    ("plus", _ACEITA),      # positivo: o plano correto continua funcionando
    (" plus ", _ACEITA),    # o conserto desta rodada
    ("pro_max", _RECUSA),   # o vocabulário da COLUNA continua fora da fronteira
    ("", _RECUSA),          # vazio nunca vira Plus (esta rota nunca teve default)
], ids=["plus", "com-espacos", "pro_max", "vazio"])
def test_change_plan_normaliza_como_o_checkout(logado, monkeypatch, plan, veredito):
    """Aceitar/recusar é medido pelo TRABALHO, não só pelo status: quando a rota
    recusa o plano ela não pode ter lido a conta, e quando aceita ela tem de ter
    lido — senão o teste ficaria verde numa rota que recusa tudo cedo demais."""
    import db as db_mod

    chamadas: list[int] = []
    real = db_mod.get_auth_user
    monkeypatch.setattr(db_mod, "get_auth_user",
                        lambda uid: (chamadas.append(uid), real(uid))[1])
    # Sem price ID configurado, TODO plano válido morre em 503 antes de a rota
    # fazer qualquer coisa — e aí o teste mediria configuração, não plano.
    monkeypatch.setattr(dashboard, "STRIPE_SECRET_KEY", "sk_test_vocabulario")
    monkeypatch.setattr(dashboard, "STRIPE_PRICE_ID_PRO_MENSAL", "price_test_plus_mensal")

    r = logado.http.post("/billing/change-plan", headers=_cabecalhos(),
                         json={"plan": plan})

    if veredito == _RECUSA:
        assert r.status_code == 400, r.text
        assert "plan inválido" in str(r.json()["detail"])
        assert chamadas == [], f"'{plan}' foi recusado mas a rota leu a conta"
    else:
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["error"] == "no_subscription", r.text
        assert chamadas == [logado.user_id], (
            f"'{plan}' foi aceito mas a rota não chegou a ler a conta")


@pytest.mark.parametrize("rota", ["/billing/create-checkout", "/billing/change-plan"],
                         ids=["stripe", "change-plan"])
def test_interval_vazio_e_400_nas_duas_rotas(logado, rota):
    """`interval: ""` é recusado nas duas — é a #352 com `interval` no lugar de
    `plan`. As duas tinham `(payload.interval or "monthly")`, que vendia o ciclo
    MENSAL para um corpo que não escolheu ciclo nenhum, em silêncio e com 200.

    CONTROLE NEGATIVO: reponha o `or "monthly"` em qualquer uma das duas
    (`frontend/finance_bot_websocket_custom.py`) e a linha correspondente fica
    VERMELHA — 503 `Pagamentos ainda não configurados` no `stripe`, 503 `Esse
    plano ainda não está configurado` no `change-plan`, porque sem price ID a
    rota segue adiante em vez de recusar. Injetado nos dois casos VERDES.
    CONTROLE POSITIVO: `interval` legítimo continua atravessando —
    `test_pix_e_stripe_aceitam_a_MESMA_lista_de_planos` manda `"annual"` no
    Stripe e `test_change_plan_normaliza_como_o_checkout[plus]` omite o campo
    (default `monthly`); os dois passam da validação de `interval`.
    """
    r = logado.http.post(rota, headers=_cabecalhos(),
                         json={"plan": "plus", "interval": ""})
    assert r.status_code == 400, r.text
    assert "interval inválido" in str(r.json()["detail"]), r.text
