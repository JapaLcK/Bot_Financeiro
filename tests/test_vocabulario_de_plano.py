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
    return logado.http.post("/billing/pix/checkout", headers=_cabecalhos(),
                            json={"plan": plan, "cpf_cnpj": "12345678901"})


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
