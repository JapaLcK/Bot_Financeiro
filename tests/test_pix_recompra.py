"""A guarda que impede COBRAR DUAS VEZES pelo mesmo tempo (§7).

Arquivo próprio, separado da tabela das quatro relações
(`tests/test_pix_pricing.py`), porque a invariante é outra e nasceu de dois
achados independentes do Codex no #304:

  * **P1-1 (rodada anterior)** — os dois ramos de UPGRADE decidiam
    `access_starts_at` olhando só o grant VIGENTE e ignoravam `fim_cobertura`,
    que é `max(ends_at)` sobre TODOS os grants ativos, justamente para incluir
    os futuros. Medido: **365 dias sobrepostos, 0 novos, R$ 499,00**. A
    varredura da categoria achou o irmão no ramo Pix→Pix (R$ 305,45).
  * **P1-4 (esta rodada)** — a primeira guarda só bloqueava cobertura futura de
    tier IGUAL OU MAIOR, e eu declarei o resto como teto aceitável. Não bastava:
    com um grant Pix de tier MENOR já pago e futuro, o cliente comprava um ano
    inteiro do tier maior sobrepondo os 365 dias daquele, com crédito ZERO.

A invariante que os dois compartilham: **uma compra tem de acrescentar acesso.**

DECIDIDO PELO DONO (2026-09-07): **a recusa é a resposta, não um interino.** O
checkout do 1b-B devolve **409** com `error = "pix_future_purchase_conflict"`,
`plan` e `covered_until`, e **não cobra nem altera grant algum**.

Substituir o grant futuro e creditá-lo foi recusado como escopo: exige modelar a
LINHAGEM do crédito e definir estorno/chargeback das DUAS cobranças. Registrado
no §7 do plano como evolução separada — não é "um pouco mais de código".

**Fronteira:** o 409 é do 1b-B (o checkout mora lá; este PR não tem rota). Daqui
sai a exceção com `ERRO`, `plano` e `cobertura_ate` já preenchidos, para a
tradução ser uma linha e ninguém reconsultar o banco.
"""

from datetime import datetime, timedelta, timezone

import pytest

from core.services.pix_pricing import (
    DURACAO_DIAS,
    CoberturaJaPaga,
    plano_da_cobranca,
)

_OMITIDO = object()
AGORA = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
# Valor ARBITRÁRIO de teste: fixa a aritmética, não o preço de produção.
# Os preços reais são lidos da fonte em tests/test_pix_pricing_contrato.py.
PRECO = 29900
MIN = 500           # ASAAS_MIN_CHARGE_CENTS medido no Sandbox: R$ 5,00 (§7)


def _grant(source, plan_stored, inicio_dias, fim_dias, amount_cents=_OMITIDO):
    """`amount_cents` é OBRIGATÓRIO para `source='pix'`, e o default deixou de
    ser `None` de propósito: era ele que ensinava o modo de falha do P1-1 como
    normal. Para as outras fontes o default continua `None` — elas não geram
    crédito (§7)."""
    if amount_cents is _OMITIDO:
        assert source != "pix", (
            "fixture de grant pix sem `amount_cents`: era esse default que "
            "codificava o bug do P1-1 como comportamento esperado"
        )
        amount_cents = None
    return {
        "source": source,
        "plan_stored": plan_stored,
        "starts_at": AGORA + timedelta(days=inicio_dias),
        "ends_at": AGORA + timedelta(days=fim_dias),
        "amount_cents": amount_cents,
    }


# Grant Pix Pro **futuro e JÁ PAGO**: começa quando o Stripe acaba, vale um ano.
# É o que torna a recompra vazia — e é o que os dois ramos de upgrade ignoravam,
# porque olham `atual` (o grant VIGENTE) em vez de `fim_cobertura`.
FUTURO_PAGO = _grant("pix", "pro_max", 60, 60 + DURACAO_DIAS, amount_cents=49900)


@pytest.mark.parametrize("rotulo,vigente", [
    # O cenário EXATO do Codex: assinante Stripe Plus, upgrade para Pro.
    ("upgrade a partir do STRIPE", _grant("stripe", "pro", -300, 60)),
    # O IRMÃO, que a varredura do §2 achou e o apontamento não citava: mesmo
    # defeito no ramo de cima, upgrade Pix→Pix.
    ("upgrade PIX para PIX", _grant("pix", "pro", -10, 355, amount_cents=19900)),
])
def test_recompra_do_tier_ja_coberto_e_RECUSADA(rotulo, vigente):
    """Bug de dinheiro, medido antes do conserto:

        upgrade a partir do STRIPE   365d sobrepostos, 0 novos, R$ 499,00
        upgrade PIX -> PIX           365d sobrepostos, 0 novos, R$ 305,45

    A pessoa paga preço cheio e ganha **zero** dias. `fim_cobertura` é
    `max(ends_at)` sobre TODOS os grants ativos justamente para incluir os
    futuros, e os ramos de renovação/downgrade já o usam — só os dois de
    upgrade não usavam.

    Recusa em vez de agendar: agendar é o certo para RENOVAÇÃO (e já é o que
    aqueles ramos fazem), mas aqui o cliente pediu o tier maior AGORA, e ele já
    comprou esse tier. Vender caladamente um ano que só começa em 60 dias com a
    tela dizendo "upgrade" é a mesma desonestidade que o campo `agendada`
    existe para evitar. Antecipar acesso já comprado é o §4.4, não uma venda.

    *Negativo: tire a guarda `if tier_novo > tier_atual: cobre_o_tier…` → as
    duas linhas voltam a VENDER 365 dias sobrepostos.*
    """
    with pytest.raises(CoberturaJaPaga) as capturado:
        plano_da_cobranca([vigente, FUTURO_PAGO], "pro_max", 49900, MIN, agora=AGORA)
    assert capturado.value.cobertura_ate == FUTURO_PAGO["ends_at"]
    assert capturado.value.plano == "pro_max"


def test_upgrade_com_grant_pix_futuro_de_tier_MENOR_tambem_e_RECUSADO():
    """P1-4 do Codex, e ele derruba um teto que eu tinha DECLARADO como aceitável.

    O caso: grant Pix Plus **futuro e já pago**, cliente compra Pro. A guarda
    anterior só bloqueava tier igual/maior, então esta venda passava — um ano
    inteiro de Pro sobrepondo os 365 dias de Plus já pagos, com **crédito zero**.

    Eu argumentei que "ali o upgrade entrega tier real". Entrega — mas ganho de
    tier não paga um ano duplicado, e declarar um teto não conserta cobrança
    dupla. Cobrar de novo por tempo que o cliente já comprou é o mesmo defeito
    do P1-1 da rodada passada, num tier diferente.

    Recusar é a resposta decidida pelo dono, não um interino: creditar exigiria
    modelar a linhagem do crédito e definir estorno das duas cobranças, o que é
    evolução separada (§7 do plano).

    *Negativo: tire o `pix_futuro_pago` da guarda → volta a vender.*
    """
    futuro_menor = _grant("pix", "pro", 60, 60 + DURACAO_DIAS, amount_cents=19900)
    with pytest.raises(CoberturaJaPaga) as capturado:
        plano_da_cobranca([_grant("stripe", "pro", -300, 60), futuro_menor],
                          "pro_max", 49900, MIN, agora=AGORA)
    assert capturado.value.cobertura_ate == futuro_menor["ends_at"]


def test_grant_futuro_de_OUTRA_fonte_nao_bloqueia():
    """POSITIVO, e ele mantém a guarda ligada ao DINHEIRO NOSSO: um grant
    `stripe` futuro não é crédito que devemos (§9), então não pode impedir a
    venda. Sem esta linha, a guarda viraria "qualquer cobertura futura recusa" e
    bloquearia a migração Stripe→Pix, que é o caminho que o §9 existe para
    permitir."""
    futuro_stripe = _grant("stripe", "pro", 60, 60 + DURACAO_DIAS)
    r = plano_da_cobranca([_grant("stripe", "pro", -300, 60), futuro_stripe],
                          "pro_max", 49900, MIN, agora=AGORA)
    assert r["amount_cents"] == 49900


def test_upgrade_SEM_cobertura_futura_nao_e_afetado():
    """POSITIVO do par: a guarda não pode tocar o upgrade comum. Sem ele, o
    grupo passaria num código que recusa toda venda — pior que o bug."""
    stripe = plano_da_cobranca([_grant("stripe", "pro", -300, 60)], "pro_max",
                               49900, MIN, agora=AGORA)
    assert stripe["access_starts_at"] == AGORA + timedelta(days=60)
    assert stripe["credit_cents"] == 0

    pix = plano_da_cobranca([_grant("pix", "pro", -182, 183, amount_cents=19900)],
                            "pro_max", 49900, MIN, agora=AGORA)
    assert pix["access_starts_at"] == AGORA
    assert pix["credit_cents"] > 0


@pytest.mark.parametrize("plano,preco", [("pro_max", 49900), ("pro", 19900)])
def test_renovacao_e_downgrade_com_futuro_pago_continuam_agendando(plano, preco):
    """POSITIVO: os ramos que JÁ estavam certos não podem ser recusados. Eles
    emendam em `fim_cobertura` e entregam 365 dias novos — a guarda é só para
    upgrade, que é onde `atual` decidia sozinho."""
    r = plano_da_cobranca([_grant("pix", "pro_max", -10, 60, amount_cents=49900),
                           FUTURO_PAGO], plano, preco, MIN, agora=AGORA)
    assert r["access_starts_at"] == FUTURO_PAGO["ends_at"]
    assert r["agendada"] is True


# ── a recusa NÃO cobra e NÃO altera grant algum (decisão do dono) ────────────

def test_a_excecao_carrega_o_que_o_1bB_precisa_para_o_409():
    """Os três atributos existem para a tradução no 1b-B ser UMA linha.

    O 409 é de lá — o checkout mora no 1b-B e este PR não tem rota. Daqui sai a
    exceção **com os dados na mão**: se alguém no 1b-B for ao banco buscar "até
    quando ele já tem", está refazendo uma consulta cujo resultado já veio
    junto, e arriscando uma resposta que diverge da que motivou a recusa.

    `ERRO` é string estável, no formato do `already_subscribed` que o
    `_billing_checkout_for_user` já devolve: é o que deixa o front distinguir
    esta recusa sem regex na mensagem — que é humana, em português, e vai mudar.
    """
    futuro = _grant("pix", "pro", 60, 60 + DURACAO_DIAS, amount_cents=19900)
    with pytest.raises(CoberturaJaPaga) as capturado:
        plano_da_cobranca([_grant("stripe", "pro", -300, 60), futuro],
                          "pro_max", 49900, MIN, agora=AGORA)

    exc = capturado.value
    assert exc.ERRO == "pix_future_purchase_conflict"
    assert exc.plano == "pro_max"
    assert exc.cobertura_ate == futuro["ends_at"]
    # Estável e sem espaço: é chave de máquina, não texto de tela.
    assert exc.ERRO.replace("_", "").isalnum()


def test_recusa_nao_toca_no_banco_nem_muda_os_grants(monkeypatch):
    """**Não cobra e não altera grant algum** — o que o dono frisou, medido em
    vez de afirmado.

    Duas metades, e a segunda é a que vale: afirmar só que a exceção subiu
    deixaria passar uma versão que escrevesse antes de levantar.

      1. **nenhuma conexão é pedida.** Troca `db.connection._get_pool`, que é o
         ponto por onde TODA cópia de `get_conn` passa (é o mesmo ponto de
         captura do `tests/test_pix_ddl_sem_escrita.py`, e por isso alcança um
         `from .connection import get_conn` feito em qualquer módulo).
      2. **a lista de entrada sai intacta.** A função é pura, e um "conserto"
         que mutasse o grant futuro para não conflitar seria escrita de estado
         sem banco nenhum.
    """
    import db.connection as conexao

    def _explode():  # pragma: no cover - o teste falha se for chamada
        raise AssertionError("a recusa pediu conexão ao banco")

    monkeypatch.setattr(conexao, "_get_pool", _explode)

    futuro = _grant("pix", "pro", 60, 60 + DURACAO_DIAS, amount_cents=19900)
    vigente = _grant("stripe", "pro", -300, 60)
    antes = [dict(futuro), dict(vigente)]

    with pytest.raises(CoberturaJaPaga):
        plano_da_cobranca([vigente, futuro], "pro_max", 49900, MIN, agora=AGORA)

    assert [dict(futuro), dict(vigente)] == antes, "a recusa mutou os grants"
