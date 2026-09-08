"""Contrato de entrada e DETERMINISMO de `plano_da_cobranca` (§7).

Arquivo próprio, separado da tabela das quatro relações
(`tests/test_pix_pricing.py`), porque o assunto é outro: lá se mede QUANTO se
cobra; aqui se mede que a resposta **não depende de nada que o contrato não
promete** — nem da ordem da lista, nem de um campo extra que o chamador possa
ou não trazer, nem de qual ramo foi tomado.

Três propriedades, e as três nasceram de defeito medido:

  * **ordem da lista** — `max()` devolve o primeiro máximo, e sem desempate o
    crédito oscilava R$ 193,55 no mesmo conjunto de entrada;
  * **entrada MÍNIMA do contrato** — o primeiro desempate usava `external_ref`,
    que não está no contrato publicado; na forma mínima a oscilação voltava, em
    R$ 123,29;
  * **`min_cents` inválido** — `int(min_cents)` só era avaliado num dos cinco
    caminhos, então três passavam silenciosos e um estourava.

E o aviso de folga de preço, que mora aqui porque ele lê a FONTE do preço em
vez de copiar o número — o mesmo assunto: não depender do que não é promessa.
"""

import functools
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from core.services.pix_pricing import DURACAO_DIAS, plano_da_cobranca

# Valores ARBITRÁRIOS de teste — não são os preços de produção. Estes fixam a
# aritmética; os de produção são LIDOS da fonte em `_precos_anuais_de_producao`
# mais abaixo, e é lá que mora a asserção sobre o mundo real.
AGORA = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
PRECO = 29900
MIN = 500           # ASAAS_MIN_CHARGE_CENTS medido no Sandbox: R$ 5,00 (§7)


def _grant(source, plan_stored, inicio_dias, fim_dias, amount_cents=None):
    return {
        "source": source,
        "plan_stored": plan_stored,
        "starts_at": AGORA + timedelta(days=inicio_dias),
        "ends_at": AGORA + timedelta(days=fim_dias),
        "amount_cents": amount_cents,
    }


# ── determinismo: o crédito não pode depender da ORDEM da lista ──────────────

def test_credito_nao_depende_da_ordem_da_lista():
    """R$ 193,55 de diferença no MESMO conjunto de entrada.

    `max()` devolve o primeiro máximo. Com dois grants vigentes de mesmo tier e
    `source` diferente, o resultado dependia da ordem em que o chamador passasse
    a lista — e ele a monta com um `join`, que sem `order by` não promete ordem.
    Medido pelo Tester: `credit=0` ou `credit=19355`. Negativo: volte
    `_acesso_atual` ao `max()` só por tier → vermelho.
    """
    pix = _grant("pix", "pro", -10, 355, amount_cents=19900)
    stripe = _grant("stripe", "pro", -10, 355, amount_cents=None)

    uma = plano_da_cobranca([pix, stripe], "pro_max", PRECO, MIN, agora=AGORA)
    outra = plano_da_cobranca([stripe, pix], "pro_max", PRECO, MIN, agora=AGORA)
    assert uma == outra, (
        f"o preço depende da ordem da lista: {uma['credit_cents']} × "
        f"{outra['credit_cents']} centavos"
    )
    # E o desempate escolhe o lado CERTO: crédito monetário existe só Pix → Pix
    # (§7), e o dinheiro do grant `pix` é o único que está na nossa conta.
    # Escolher o `stripe` negaria ao cliente um crédito a que ele tem direito.
    assert uma["credit_cents"] > 0


def test_desempate_e_ordem_total_entre_grants_do_mesmo_source():
    """Dois grants `pix` de mesmo tier e mesmo `ends_at`: o desempate por
    `source` sozinho não os separa, e é o `amount_cents` que decide.

    A docstring anterior dizia "sem o último critério (`external_ref`)…" e a
    fixture passava `external_ref`. Resíduo morto: `_acesso_atual` **não usa
    `external_ref`** — a escada é `(tier, source=="pix", ends_at, amount_cents,
    starts_at)`, e o `external_ref` foi REMOVIDO justamente por não estar no
    contrato, que é o que `test_desempate_vale_com_a_entrada_MINIMA_do_contrato`
    (logo abaixo) existe para provar. Um teste descrevendo o bug como se fosse a
    solução, ao lado do teste que o mede.
    """
    a = _grant("pix", "pro", -10, 100, amount_cents=10000)
    b = _grant("pix", "pro", -10, 100, amount_cents=90000)
    assert (plano_da_cobranca([a, b], "pro_max", PRECO, MIN, agora=AGORA)
            == plano_da_cobranca([b, a], "pro_max", PRECO, MIN, agora=AGORA))


def test_tier_maior_continua_ganhando_do_desempate():
    """POSITIVO: o desempate é o CRITÉRIO 2, não o primeiro. Um grant `pix` de
    tier menor não pode roubar a decisão de um `stripe` de tier maior — senão a
    correção do determinismo teria trocado um bug por outro."""
    pix_menor = _grant("pix", "pro", -10, 300, amount_cents=19900)
    stripe_maior = _grant("stripe", "pro_max", -10, 300)
    r = plano_da_cobranca([pix_menor, stripe_maior], "pro_max", PRECO, MIN, agora=AGORA)
    # `pro_max` = `pro_max` → renovação, não upgrade: preço cheio, sem crédito.
    assert r["credit_cents"] == 0


# ── só grant FUTURO: a compra é agendada ─────────────────────────────────────

def test_so_grant_futuro_produz_compra_agendada():
    """Quem tem um downgrade já agendado e nada vigente cai no ramo "sem
    vigentes" — e a compra emenda no fim do futuro, mais de um ano à frente.

    `agendada=False` ali era a tela dizendo "acesso imediato" numa compra que só
    começa em 395 dias. Nenhum teste cobria "só grant futuro".
    """
    futuro = _grant("pix", "pro", 30, 395, amount_cents=19900)
    r = plano_da_cobranca([futuro], "pro_max", PRECO, MIN, agora=AGORA)
    assert r["access_starts_at"] == futuro["ends_at"]
    assert r["agendada"] is True, "compra que começa em 395 dias não é imediata"


def test_primeira_compra_de_verdade_continua_imediata():
    """POSITIVO do par: sem ele, `agendada=True` sempre passaria — e toda venda
    nova diria ao cliente que o acesso só começa depois."""
    assert plano_da_cobranca([], "pro_max", PRECO, MIN, agora=AGORA)["agendada"] is False
    vencido = _grant("pix", "pro", -400, -35, amount_cents=PRECO)
    assert plano_da_cobranca([vencido], "pro_max", PRECO, MIN,
                             agora=AGORA)["agendada"] is False


# ── o aviso para o dia em que os preços se aproximarem ───────────────────────

def _precos_anuais_de_producao() -> dict[str, int]:
    """Preço anual, em centavos, por valor LEGADO de `auth_accounts.plan`.

    **Lido da fonte, não copiado.** Mesmo padrão do
    `tests/test_phosphor_subset.py`, que compara duas fontes em vez de fixar
    valor. Duas fontes, e as duas já existem:

      * `frontend/precos.html` → `PLAN_PRICES[tier].annual`, que o próprio
        arquivo declara fonte única (*"preço só é editado num lugar"*, e o
        `planValue()` do GA4 lê dali em vez de manter segunda tabela);
      * `core/services/plan_service._STORED_PLAN_TO_TIER` → a ponte entre o
        valor legado (`pro`, `pro_max`) e o tier da tela (`plus`, `pro`).

    A versão anterior fixava `{"pro": 19900, "pro_max": 29900}` e chamava de "os
    preços de hoje". O `29900` **não existe no repositório** — nasceu de um
    número de briefing e foi copiado adiante. Um teste que existe para avisar
    quando a folga acabar, ancorado num preço inventado, fica verde e
    autoritativo para sempre. O preço real do tier `pro` é R$ 499/ano.
    """
    import re

    from core.services.plan_service import _STORED_PLAN_TO_TIER

    html = (pathlib.Path(__file__).resolve().parent.parent
            / "frontend" / "precos.html").read_text(encoding="utf-8")
    bloco = re.search(r"const PLAN_PRICES = \{(.*?)\};", html, re.S)
    assert bloco, "PLAN_PRICES sumiu de frontend/precos.html — a fonte mudou de forma"

    por_tier: dict[str, int] = {}
    padrao = r'(\w+):\s*\{[^}]*?annual:\s*"([^"]+)"'
    for tier, rotulo in re.findall(padrao, bloco.group(1)):
        numero = re.sub(r"[^\d,]", "", rotulo).replace(",", ".")
        por_tier[tier] = round(float(numero) * 100)

    return {legado: por_tier[tier] for legado, tier in _STORED_PLAN_TO_TIER.items()
            if tier in por_tier}


# Sem `PRECOS_DE_PRODUCAO = _precos_anuais_de_producao()` no nível do módulo, e
# a razão é raio de dano: calculado no import, uma mudança de forma na
# `precos.html` derrubava a COLETA dos 19 testes deste arquivo — barulho alto e
# claro, mas 17 deles não têm nada a ver com preço de produção. `lru_cache`
# mantém o parse uma vez só por sessão e limita o vermelho aos dois testes que
# de fato leem a fonte. O piso não se perde: quem chama primeiro é
# `test_os_precos_vem_da_fonte_e_nao_de_um_numero_copiado`.
_precos_anuais_de_producao = functools.lru_cache(maxsize=1)(_precos_anuais_de_producao)


def test_os_precos_vem_da_fonte_e_nao_de_um_numero_copiado():
    """Piso da extração: regex quebrada ou arquivo movido faria o teste da folga
    passar sobre um dicionário vazio, afirmando nada.

    Também fixa a PONTE, que é onde eu errei: `pro_max` é o tier `pro` da tela
    (R$ 499/ano), não os R$ 299 que vieram do briefing.
    """
    assert _precos_anuais_de_producao().get("pro") == 19900, "stored 'pro' = tier plus = R$ 199/ano"
    # DIVERGÊNCIA PRÉ-EXISTENTE, nomeada aqui porque é aqui que um teste passa a
    # afirmar um dos dois números: `core/services/plan_service.py:45` comenta que
    # `pro_max` é o "tier novo de R$ 39,90"; a `precos.html` (fonte única
    # declarada) diz R$ 49,90/mês → R$ 499/ano. Esta asserção segue a
    # `precos.html`, que é a que o usuário vê e a que o `planValue()` do GA4 lê.
    # O comentário do `plan_service` não é meu para consertar — mas quem for
    # mexer em preço tem de reconciliar os dois antes.
    assert _precos_anuais_de_producao().get("pro_max") == 49900, "stored 'pro_max' = tier pro = R$ 499/ano"
    assert _precos_anuais_de_producao().get("essencial") == 9900


def test_com_os_precos_de_hoje_o_ramo_do_minimo_nunca_dispara():
    """O outro lado do achado: hoje esta célula é INALCANÇÁVEL.

    O crédito tem teto no `amount_cents`, então o líquido nunca desce abaixo da
    DIFERENÇA entre os dois preços. Com os preços reais — R$ 199/ano (stored
    `pro`) e R$ 499/ano (stored `pro_max`) — o pior caso é **R$ 300**, sessenta
    vezes o mínimo do provedor.

    (A versão anterior desta docstring dizia "com 199 → 299 o líquido mínimo é
    R$ 100". O 299 nunca existiu no repositório: veio de um número de briefing.
    O código já lia da fonte e a asserção já estava certa — o número errado
    ficou só na explicação, que é o pior lugar para ele estar, porque é o que
    alguém lê em vez de medir.)

    O ramo é defesa legítima: fica alcançável se o mínimo do Asaas subir, ou se
    surgirem tiers a menos de ~R$ 5 de distância. Este teste é o aviso — ele
    **falha quando a folga acabar**, acendendo a luz aqui em vez de numa
    cobrança recusada pelo Asaas na venda.
    """
    pior_caso = None
    for dias in range(1, DURACAO_DIAS + 1):
        atual = _grant("pix", "pro", dias - DURACAO_DIAS, dias,
                       amount_cents=_precos_anuais_de_producao()["pro"])
        r = plano_da_cobranca([atual], "pro_max", _precos_anuais_de_producao()["pro_max"],
                              MIN, agora=AGORA)
        assert r["agendada"] is False, (
            f"com {dias} dias restantes o ramo do mínimo DISPAROU — os preços de "
            "produção chegaram perto do mínimo do provedor. Reveja o §7 e o "
            "texto da tela de agendamento."
        )
        liquido = r["amount_cents"]
        pior_caso = liquido if pior_caso is None else min(pior_caso, liquido)

    assert pior_caso == _precos_anuais_de_producao()["pro_max"] - _precos_anuais_de_producao()["pro"], (
        "o pior caso deixou de ser a diferença entre os dois preços — a "
        "aritmética do crédito mudou, remeça a folga"
    )
    assert pior_caso > MIN * 5, f"folga de apenas {pior_caso} centavos sobre o mínimo"


# ── o desempate tem de valer na entrada MÍNIMA do contrato ──────────────────

CONTRATO_MINIMO = ("source", "plan_stored", "starts_at", "ends_at", "amount_cents")


def _grant_minimo(source, plan_stored, inicio_dias, fim_dias, amount_cents):
    """SÓ os campos que a docstring pública promete. Nada de `external_ref`."""
    return {"source": source, "plan_stored": plan_stored,
            "starts_at": AGORA + timedelta(days=inicio_dias),
            "ends_at": AGORA + timedelta(days=fim_dias),
            "amount_cents": amount_cents}


def test_desempate_vale_com_a_entrada_MINIMA_do_contrato():
    """R$ 123,29 decididos pela ordem da lista, um nível abaixo do conserto
    anterior.

    O desempate usava `external_ref`, que **não está no contrato** publicado na
    docstring de `plano_da_cobranca`. Com dois grants `pix`, mesmo tier e **mesmo
    `ends_at`**, passados na forma mínima, não havia critério nenhum — e o teste
    da rodada passada não pegava porque passava `external_ref` nos dois.

    Aqui os grants têm exatamente os cinco campos do contrato, e nada mais.
    """
    a = _grant_minimo("pix", "pro", -10, 100, 10000)
    b = _grant_minimo("pix", "pro", -10, 100, 55000)
    assert set(a) == set(CONTRATO_MINIMO), "a fixture saiu do contrato mínimo"

    uma = plano_da_cobranca([a, b], "pro_max", PRECO, MIN, agora=AGORA)
    outra = plano_da_cobranca([b, a], "pro_max", PRECO, MIN, agora=AGORA)
    assert uma == outra, (
        f"a ordem da lista decide {abs(uma['credit_cents'] - outra['credit_cents'])} "
        "centavos com a entrada mínima do contrato"
    )


def test_grants_indistinguiveis_pelo_contrato_dao_o_MESMO_resultado():
    """O fim da escada: esgotados os cinco campos, os grants são idênticos PELO
    CONTRATO — e aí qual deles o `max()` escolhe não importa, porque tudo o que
    a função calcula sai desses campos. É isso que torna a SAÍDA determinística,
    e não só a escolha."""
    a = _grant_minimo("pix", "pro", -10, 100, 19900)
    b = _grant_minimo("pix", "pro", -10, 100, 19900)
    assert (plano_da_cobranca([a, b], "pro_max", PRECO, MIN, agora=AGORA)
            == plano_da_cobranca([b, a], "pro_max", PRECO, MIN, agora=AGORA))


# ── min_cents inválido falha SEMPRE, não só num ramo ────────────────────────

CENARIOS_DAS_QUATRO_RELACOES = [
    ("primeira compra", []),
    ("renovação", [_grant("pix", "pro_max", -100, 265, amount_cents=PRECO)]),
    ("upgrade a partir do pix", [_grant("pix", "pro", -182, 183, amount_cents=19900)]),
    ("upgrade a partir do stripe", [_grant("stripe", "pro", -100, 65)]),
    ("downgrade", [_grant("pix", "pro_max", -10, 300, amount_cents=PRECO)]),
]


@pytest.mark.parametrize("rotulo,grants", CENARIOS_DAS_QUATRO_RELACOES,
                         ids=[r for r, _ in CENARIOS_DAS_QUATRO_RELACOES])
def test_min_cents_invalido_falha_em_TODAS_as_relacoes(rotulo, grants):
    """O conserto que tinha ficado sem controle nenhum.

    `int(min_cents)` só era avaliado no ramo de upgrade Pix→Pix. Com
    `min_cents=None`, três das cinco entradas passavam SILENCIOSAS e a quarta
    estourava `TypeError` — falha que depende do ramo some no teste e aparece na
    venda, num caminho que decide quanto cobrar.

    Negativo: tire o `min_cents = int(min_cents)` do topo de `plano_da_cobranca`
    → as linhas que não são upgrade-Pix ficam vermelhas (elas devolvem um
    resultado em vez de levantar).
    """
    with pytest.raises(TypeError):
        plano_da_cobranca(grants, "pro_max", PRECO, None, agora=AGORA)


@pytest.mark.parametrize("rotulo,grants", CENARIOS_DAS_QUATRO_RELACOES,
                         ids=[r for r, _ in CENARIOS_DAS_QUATRO_RELACOES])
def test_min_cents_valido_funciona_em_TODAS_as_relacoes(rotulo, grants):
    """POSITIVO do par: sem ele, um `plano_da_cobranca` que levantasse sempre
    passaria no teste acima."""
    r = plano_da_cobranca(grants, "pro_max", PRECO, MIN, agora=AGORA)
    assert r["amount_cents"] == r["price_cents"] - r["credit_cents"]
