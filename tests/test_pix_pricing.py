"""`core/services/pix_pricing.plano_da_cobranca` — a tabela do §7.

Função PURA: nenhuma conexão, nenhuma env, nenhum relógio implícito (`agora` é
parâmetro, senão a asserção da vigência vira flake de milissegundo).

As quatro relações do §7, e o que cada uma protege:

| relação                          | vigência                 | preço    | crédito |
|----------------------------------|--------------------------|----------|---------|
| mesmo plano (renovação)          | max(now, fim dos grants) | cheio    | —       |
| upgrade, acesso atual **PIX**    | now                      | −crédito | proporcional |
| upgrade, acesso atual **STRIPE** | fim do período do cartão | cheio    | **nenhum** |
| downgrade                        | fim do período atual     | cheio    | —       |

**Crédito monetário existe só no caminho Pix → Pix.** Vindo do Stripe, aquele
dinheiro está no outro gateway — creditá-lo aqui seria dar desconto sobre
receita que não é nossa e ainda deixar o cartão renovando.

CONTROLES NEGATIVOS MEDIDOS:

  * faça o ramo de upgrade ignorar `source` (creditar sempre) →
    `test_upgrade_vindo_do_stripe_nao_gera_credito` vermelho;
  * remova a guarda do mínimo (`preco - credito < min_cents`) →
    `test_liquido_abaixo_do_minimo_vira_compra_agendada` vermelho;
  * compare o mínimo contra o BRUTO (`preco_novo_cents < min_cents`) →
    `test_o_minimo_incide_sobre_o_LIQUIDO` vermelho. Este é o que a suíte não
    teria acusado sozinha: a recusa do Sandbox é literal sobre o líquido
    ("O valor da cobrança menos o valor do desconto não pode ser menor que
    R$ 5,00", §7), e comparar o bruto é comparar grandeza errada;
  * tire o teto `min(valor, credito)` de `_credito_proporcional` →
    `test_credito_nunca_excede_o_que_entrou` vermelho.

POSITIVOS: `test_renovacao_do_mesmo_plano_cobra_cheio_e_emenda`,
`test_upgrade_pix_para_pix_credita_proporcional` e
`test_primeira_compra_comeca_agora` — sem eles o grupo passaria num código que
nunca credita e sempre agenda, que é pior que o bug.
"""

from datetime import datetime, timedelta, timezone

import pytest

from core.services.pix_pricing import DURACAO_DIAS, plano_da_cobranca

AGORA = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
# Valor ARBITRÁRIO de teste: fixa a aritmética, não o preço de produção.
# Os preços reais são lidos da fonte em tests/test_pix_pricing_contrato.py.
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


# ── primeira compra ──────────────────────────────────────────────────────────

def test_primeira_compra_comeca_agora():
    """POSITIVO. Sem grant nenhum não há o que comparar: preço cheio, sem
    crédito, acesso agora."""
    r = plano_da_cobranca([], "pro_max", PRECO, MIN, agora=AGORA)
    assert r["access_starts_at"] == AGORA
    assert r["access_expires_at"] == AGORA + timedelta(days=DURACAO_DIAS)
    assert (r["price_cents"], r["credit_cents"], r["amount_cents"]) == (PRECO, 0, PRECO)
    assert r["agendada"] is False


def test_acesso_ja_vencido_recomeca_agora():
    """Grant que já acabou não é "acesso atual": ele não pode empurrar a
    vigência para o passado nem gerar crédito de tempo que não existe mais."""
    vencido = _grant("pix", "pro_max", -400, -35, amount_cents=PRECO)
    r = plano_da_cobranca([vencido], "pro_max", PRECO, MIN, agora=AGORA)
    assert r["access_starts_at"] == AGORA
    assert r["credit_cents"] == 0


# ── renovação (mesmo plano) ──────────────────────────────────────────────────

def test_renovacao_do_mesmo_plano_cobra_cheio_e_emenda():
    """POSITIVO da renovação: preço cheio, sem crédito, e a vigência EMENDA no
    fim do que já existe. Começar hoje faria o cliente perder os dias que já
    pagou — crédito nunca vira tempo, e tempo nunca vira crédito."""
    atual = _grant("pix", "pro_max", -100, 265, amount_cents=PRECO)
    r = plano_da_cobranca([atual], "pro_max", PRECO, MIN, agora=AGORA)
    assert r["access_starts_at"] == atual["ends_at"]
    assert (r["credit_cents"], r["amount_cents"]) == (0, PRECO)
    assert r["agendada"] is True


def test_renovacao_emenda_depois_do_grant_FUTURO_tambem():
    """`fim da cobertura` usa TODOS os grants ativos, não só os vigentes: quem
    já tem um downgrade agendado e renova por cima não pode ganhar duas janelas
    sobrepostas — o cliente perderia os dias da segunda."""
    vigente = _grant("pix", "pro_max", -100, 30, amount_cents=PRECO)
    agendado = _grant("pix", "pro", 30, 395, amount_cents=19900)
    r = plano_da_cobranca([vigente, agendado], "pro_max", PRECO, MIN, agora=AGORA)
    assert r["access_starts_at"] == agendado["ends_at"]


# ── upgrade ──────────────────────────────────────────────────────────────────

def test_upgrade_pix_para_pix_credita_proporcional():
    """POSITIVO do crédito, e o caso 51 do §16.

    Metade do ano restante de uma compra de R$ 199,00 → crédito de metade dela,
    e o acesso novo começa AGORA (o antigo é substituído, não empilhado).
    """
    atual = _grant("pix", "pro", -182.5, 182.5, amount_cents=19900)
    r = plano_da_cobranca([atual], "pro_max", PRECO, MIN, agora=AGORA)
    assert r["access_starts_at"] == AGORA
    assert r["credit_cents"] == pytest.approx(9950, abs=2)
    assert r["amount_cents"] == r["price_cents"] - r["credit_cents"]
    assert r["agendada"] is False


def test_upgrade_vindo_do_stripe_nao_gera_credito():
    """Caso 52 do §16. O dinheiro do período atual está no OUTRO gateway:
    creditá-lo aqui seria desconto sobre receita que não é nossa.

    A vigência começa no fim do período do cartão — o cliente não perde nada, e
    o `stripe_cancel` do §8.2 é quem impede a renovação por cima.
    """
    atual = _grant("stripe", "pro", -100, 65, amount_cents=None)
    r = plano_da_cobranca([atual], "pro_max", PRECO, MIN, agora=AGORA)
    assert r["credit_cents"] == 0
    assert r["amount_cents"] == PRECO
    assert r["access_starts_at"] == atual["ends_at"]
    assert r["agendada"] is True


def test_upgrade_pix_sem_amount_cents_nao_credita():
    """A cegueira do contrato de entrada, virada em asserção: `amount_cents` NÃO
    existe em `plan_grants` — quem monta a lista faz o `join` com `pix_charges`.
    Esquecendo o `join`, o campo vem `None`.

    O resultado tem de ser crédito ZERO (conservador: cobra cheio), nunca um
    `TypeError` no meio da venda nem um crédito calculado sobre `None`.
    """
    atual = _grant("pix", "pro", -100, 200, amount_cents=None)
    r = plano_da_cobranca([atual], "pro_max", PRECO, MIN, agora=AGORA)
    assert r["credit_cents"] == 0
    assert r["amount_cents"] == PRECO


def test_credito_nunca_excede_o_que_entrou():
    """Teto do §7: um grant com janela MAIOR que 365 dias — reparo manual,
    concessão do admin, `set_account_plan` — não pode gerar crédito maior que o
    dinheiro que de fato entrou. Sem o teto, a cobrança sairia negativa."""
    atual = _grant("pix", "pro", -10, 3650, amount_cents=19900)
    r = plano_da_cobranca([atual], "pro_max", PRECO, MIN, agora=AGORA)
    assert r["credit_cents"] == 19900
    assert r["amount_cents"] == PRECO - 19900


# ── o mínimo do Asaas ────────────────────────────────────────────────────────

# O cenário do mínimo, com números que FECHAM entre si. Ele só é alcançável
# quando a DIFERENÇA de preço entre os dois planos é pequena: o crédito é
# proporcional ao tempo restante e tem teto no que entrou, então o líquido nunca
# desce abaixo dessa diferença. Com os preços REAIS — R$ 199/ano (stored `pro`)
# e R$ 499/ano (stored `pro_max`, lidos da `precos.html` em
# `tests/test_pix_pricing_contrato.py`) — o piso é R$ 300, sessenta vezes o
# mínimo do provedor. Por isso a fixture usa um plano novo de R$ 200,00: a
# célula existe, mas hoje mora num par de preços que o produto não vende.
# Está escrito porque um revisor que veja 199 → 200 vai perguntar.
#
# (Este comentário dizia "com o par 199/299 de hoje o líquido nunca desce abaixo
# de R$ 100". Os dois números eram falsos — o `pro_max` é 49900 e o piso é
# R$ 300 — e sobreviveram uma rodada inteira DEPOIS de o arquivo irmão declarar
# a mesma frase consertada. A lição não é o número: é que corrigir a instância
# e não a CATEGORIA deixa a cópia viva no arquivo ao lado, CLAUDE.md §2.)
PRECO_VIZINHO = 20000       # R$ 200,00
CREDITO_QUASE_CHEIO = _grant("pix", "pro", -5, 360, amount_cents=19900)


def test_liquido_abaixo_do_minimo_vira_compra_agendada():
    """Caso 53 do §16: quando `preço − crédito < mínimo`, **não se cobra o
    mínimo**. Cobrar R$ 5,00 por um ano inteiro seria vender abaixo do custo.

    A compra vira AGENDADA: preço cheio, crédito 0, começando quando a cobertura
    atual acaba. O crédito não some — ele continua sendo o acesso que a pessoa
    já tem até lá.
    """
    r = plano_da_cobranca([CREDITO_QUASE_CHEIO], "pro_max", PRECO_VIZINHO, MIN,
                          agora=AGORA)
    assert r["agendada"] is True
    assert r["credit_cents"] == 0
    assert r["amount_cents"] == PRECO_VIZINHO
    assert r["access_starts_at"] == CREDITO_QUASE_CHEIO["ends_at"]


def test_o_minimo_incide_sobre_o_LIQUIDO():
    """O que separa a regra certa da errada, e o que a suíte não acusaria
    sozinha (§7): o Asaas compara DEPOIS de subtrair o desconto — a recusa
    medida no Sandbox é literal ("O valor da cobrança (R$ 4,99) menos o valor do
    desconto (R$ 0,00) não pode ser menor que R$ 5,00").

    A fixture prova as DUAS pontas antes de olhar o resultado: o BRUTO acima do
    mínimo e o LÍQUIDO abaixo. Sem a primeira, um código que comparasse o bruto
    passaria neste teste — e a cobrança seria recusada pelo provedor, na venda.
    """
    credito = round(19900 * 360 / DURACAO_DIAS)
    assert PRECO_VIZINHO > MIN, "o BRUTO tem de estar ACIMA do mínimo, senão o teste é cego"
    assert PRECO_VIZINHO - credito < MIN, "a fixture não exercita o caso — refaça os números"

    r = plano_da_cobranca([CREDITO_QUASE_CHEIO], "pro_max", PRECO_VIZINHO, MIN,
                          agora=AGORA)
    assert r["agendada"] is True
    assert r["amount_cents"] == PRECO_VIZINHO


def test_liquido_acima_do_minimo_cobra_normal():
    """POSITIVO do par: sem ele o grupo passaria num código que agenda TODA
    compra com crédito, e ninguém mais faria upgrade imediato."""
    atual = _grant("pix", "pro", -300, 65, amount_cents=19900)
    r = plano_da_cobranca([atual], "pro_max", PRECO, MIN, agora=AGORA)
    assert r["agendada"] is False
    assert r["credit_cents"] > 0
    assert r["amount_cents"] >= MIN


# ── downgrade ────────────────────────────────────────────────────────────────

def test_downgrade_comeca_no_fim_do_periodo_atual_sem_credito():
    """Quem pagou o plano caro usa até o fim. Preço cheio do plano novo, crédito
    nenhum, vigência começando no fim da cobertura — o inverso do upgrade."""
    atual = _grant("pix", "pro_max", -100, 265, amount_cents=PRECO)
    r = plano_da_cobranca([atual], "pro", 19900, MIN, agora=AGORA)
    assert r["access_starts_at"] == atual["ends_at"]
    assert (r["credit_cents"], r["amount_cents"]) == (0, 19900)
    assert r["plan_stored"] == "pro"
    assert r["agendada"] is True


def test_vigencia_e_sempre_de_365_dias():
    """`access_expires_at = access_starts_at + 365d` (§7), em toda relação.
    Crédito NUNCA vira tempo: quem faz upgrade recebe desconto em dinheiro, não
    meses a mais."""
    casos = [
        ([], "pro_max"),
        ([_grant("pix", "pro", -182, 183, amount_cents=19900)], "pro_max"),
        ([_grant("stripe", "pro", -10, 20)], "pro_max"),
        ([_grant("pix", "pro_max", -10, 300, amount_cents=PRECO)], "pro"),
    ]
    for grants, plano in casos:
        r = plano_da_cobranca(grants, plano, PRECO, MIN, agora=AGORA)
        assert r["access_expires_at"] - r["access_starts_at"] == timedelta(days=DURACAO_DIAS)
