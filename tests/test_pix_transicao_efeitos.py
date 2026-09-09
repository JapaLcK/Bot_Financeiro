"""A máquina de estados: quem decide o EFEITO, e todo estado tem saída (§8.2, §11).

Arquivo próprio, separado do CRUD e das invariantes de `pix_charges`, porque a
invariante aqui é de PROCESSO e nasceu de dois achados do Codex no #304:

  * **P1-A** — a transição commita ANTES dos efeitos. Sob a regra antiga
    (`aplicou == False → efeitos = []`), morrer entre as duas coisas fazia a
    retentativa rodar **zero efeitos**: cliente pago sem grant, ou cliente
    estornado com acesso para sempre. É o defeito do #298 um nível abaixo.
    **O que autoriza pular um efeito é o registro DAQUELE efeito**, nunca o
    resultado da transição — e o §3.4 do plano já dizia isso enquanto o §8.2 C
    dizia o contrário.
  * **P1-B** — `refunded_partial` foi criado por nós (ao separar
    `PAYMENT_PARTIALLY_REFUNDED`) e ficou **sem saída**: estorno total ou
    chargeback depois de um parcial não casava linha nenhuma da matriz, e o
    `revoke` não rodava. Estado novo sem enumerar o que sai dele é a forma do
    §4 do `CLAUDE.md`.

O dreno é 1b-B; a REGRA e o CONTRATO são deste PR, e é contra eles que o 1b-B
vai ser escrito.
"""

import re
import pathlib
import secrets
import uuid

from db.pix_charges import attach_pagamento, criar_cobranca, transicionar
# `_JANELA` vem do arquivo irmão em vez de ser recopiada (§0.7): é a mesma
# fixture da janela que o CHECK `pix_charges_pago_tem_janela` exige.
from test_pix_charges import _JANELA
from db.webhook_outbox import efeito_registrado, registrar_efeito

RAIZ = pathlib.Path(__file__).resolve().parent.parent


def _nova(user_id: int, **kw) -> dict | None:
    campos = dict(public_token=secrets.token_urlsafe(16), plan="pro_max",
                  plan_stored="pro_max", price_cents=29900, credit_cents=0,
                  amount_cents=29900, duration_days=365)
    campos.update(kw)
    return criar_cobranca(user_id, **campos)


def _estados_do_check() -> set[str]:
    ddl = (RAIZ / "db" / "schema.py").read_text(encoding="utf-8")
    bloco = re.search(r"check \(status in \((.*?)\)\)", ddl, re.S)
    assert bloco, "o `check pix_charges_status_valido` sumiu do DDL"
    return set(re.findall(r"'([a-z_]+)'", bloco.group(1)))


def _estados_da_matriz() -> set[str]:
    plano = (RAIZ / "docs" / "plano_pix_anual_asaas.md").read_text(encoding="utf-8")
    ini = plano.index("| estado \\ evento")
    matriz = plano[ini:plano.index("**`refunded_partial` tem linha", ini)]
    achados: set[str] = set()
    for linha in re.findall(r"^\| `([a-z_`/ ]+)` \|", matriz, re.M):
        achados.update(x.strip(" `") for x in linha.split("`/`"))
    return achados


def test_todo_estado_valido_tem_linha_na_matriz_do_plano():
    """FONTE ÚNICA (§0.7) entre o `check` do banco e a matriz do §11 — e o
    portão que teria pego o P1-B sozinho.

    Nós criamos `refunded_partial` e esquecemos as saídas dele; a matriz e o
    `check` divergiram em silêncio, e o custo era o `revoke` não rodar depois de
    um estorno total. Um estado que o banco aceita e a matriz não descreve é um
    estado sem transição definida — ou seja, dinheiro parado num canto que
    ninguém enumerou.

    Compara as duas fontes em vez de fixar uma lista, no padrão do
    `tests/test_phosphor_subset.py`.

    *Negativo: tire a linha de `refunded_partial` da matriz do plano → vermelho
    nomeando o estado.*
    """
    do_check, da_matriz = _estados_do_check(), _estados_da_matriz()
    # Piso anti-tautologia: extração vazia faria o teste passar afirmando nada.
    # Não fixa a CONTAGEM de propósito — estado novo é legítimo, desde que entre
    # nos dois lugares, e é isso que as asserções abaixo cobram. Pinar 11 aqui
    # faria uma adição correta falhar com a mensagem errada ("o check mudou de
    # forma"), que manda a próxima pessoa olhar o lugar errado.
    for esperado in ("paid", "refunded_partial", "draft"):
        assert esperado in do_check, f"extração do `check` quebrada: {esperado!r} sumiu"
        assert esperado in da_matriz, f"extração da matriz quebrada: {esperado!r} sumiu"

    assert not do_check - da_matriz, (
        "estado que o banco ACEITA e a matriz do §11 não descreve (sem saída "
        f"definida): {sorted(do_check - da_matriz)}"
    )
    assert not da_matriz - do_check, (
        "estado na matriz do §11 que o banco RECUSA — a matriz descreve algo "
        f"inserível em lugar nenhum: {sorted(da_matriz - do_check)}"
    )


def test_efeito_roda_na_RETENTATIVA_mesmo_com_a_transicao_ja_commitada(user_id):
    """O cenário exato do P1-A (Codex #304), com as duas peças que existem neste
    PR — a transição e o registro de efeitos. O dreno é 1b-B; a REGRA é daqui.

    Linha do tempo:

      1. o dreno transiciona `pending → paid` e **commita**;
      2. o processo morre antes de rodar o efeito `grant` (nada registrado);
      3. o evento continua pendente na outbox e o Asaas reentrega;
      4. a retentativa chama `transicionar` de novo e recebe **`None`**.

    Sob a regra antiga (`aplicou == False → efeitos = []`), o passo 4 rodava
    **zero efeitos** e o cliente ficava pago e sem grant — para sempre, porque
    toda retentativa seguinte cairia igual. O que decide agora é o registro do
    efeito, e ele não foi feito no passo 2.

    *Negativo: volte a decidir os efeitos pelo retorno da transição — ou seja,
    faça o teste pular o efeito quando `aplicou is None` — e a asserção de que
    o `grant` roda fica vermelha.*
    """
    linha = _nova(user_id)
    pagamento = f"pay_{uuid.uuid4().hex[:10]}"
    assert attach_pagamento(linha["id"], pagamento)

    # 1+2: transição commita, e o processo morre antes do efeito.
    assert transicionar(linha["id"], de="pending", para="paid", **_JANELA) is not None
    assert efeito_registrado(pagamento, "grant") is False

    # 3+4: reentrega. A transição não aplica — e isso NÃO pode decidir nada.
    aplicou = transicionar(linha["id"], de="pending", para="paid")
    assert aplicou is None

    # O que decide é o registro do efeito, e ele está vazio: o grant TEM de rodar.
    assert efeito_registrado(pagamento, "grant") is False, (
        "a autoridade é o registro do efeito, não o resultado da transição"
    )
    assert registrar_efeito(pagamento, "grant", "evt_retentativa") is True

    # E a passada seguinte, aí sim, pula — pelo registro, não pela transição.
    assert efeito_registrado(pagamento, "grant") is True
    assert registrar_efeito(pagamento, "grant", "evt_terceira") is False


def test_reentrega_completa_nao_reexecuta_nada(user_id):
    """POSITIVO do par, e é a proteção que NÃO pode ter se perdido ao tirar a
    transição do caminho: quando os efeitos JÁ rodaram, a reentrega não
    reexecuta — nem o `purchase` do GA4, nem o Purchase da CAPI, nem o e-mail.

    Sem esta linha, o conserto do P1-A poderia ter trocado "perde efeito" por
    "duplica efeito", que é pior: receita dobrada em cima do mesmo dinheiro.
    """
    linha = _nova(user_id)
    pagamento = f"pay_{uuid.uuid4().hex[:10]}"
    attach_pagamento(linha["id"], pagamento)
    transicionar(linha["id"], de="pending", para="paid", **_JANELA)

    for efeito in ("stripe_cancel", "grant", "ga4", "capi", "email"):
        assert registrar_efeito(pagamento, efeito, "evt_1") is True
    # Reentrega: transição não aplica E todos os efeitos já estão registrados.
    assert transicionar(linha["id"], de="pending", para="paid") is None
    for efeito in ("stripe_cancel", "grant", "ga4", "capi", "email"):
        assert efeito_registrado(pagamento, efeito) is True
        assert registrar_efeito(pagamento, efeito, "evt_2") is False
