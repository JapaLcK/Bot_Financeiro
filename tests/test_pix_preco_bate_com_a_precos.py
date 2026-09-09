"""O preço que o código cobra é o preço que a `/precos` mostra.

Arquivo próprio, no molde do `tests/test_phosphor_subset.py`: o assunto é uma
**duplicação inevitável** e o §0.7 manda pôr um teste comparando as duas fontes.
HTML estático não importa Python, então o preço existe em dois lugares — e a
regra é que nenhum dos dois pode andar sozinho.

## As TRÊS fontes, porque não são duas

1. `core.services.pix_pricing.PRECOS_ANUAIS_CENTS` — o que a cobrança Pix emite;
2. `PLAN_PRICES` no script da `frontend/precos.html` — o que o `planValue()` manda
   ao GA4 e o que o resumo do checkout monta. O arquivo se declara fonte única;
3. os `data-price-annual` do MARKUP — **o que o usuário lê**.

A 3 é a que quase ficou de fora, e ela é a que importa para o cliente: o preço
visível **não** sai do `PLAN_PRICES`. Ele está cravado no HTML (`precos.html`
`:249`, `:267`, `:288`, e a tabela comparativa em `:352`, `:357`, `:361`), e o JS
só faz `display:none` para alternar mensal ⇄ anual. Um comparador que olhasse só
a 1 × 2 ficaria verde com a página anunciando outro número.

## A ponte de nomes é obrigatória

`PRECOS_ANUAIS_CENTS` é chaveado pelo valor **LEGADO** de `auth_accounts.plan`
(`pro`, `pro_max`); o `PLAN_PRICES` pelo **tier público** (`plus`, `pro`).
Comparar `pro` com `pro` casa **R$ 199 com R$ 499 e sai verde**. Quem traduz é
`_STORED_PLAN_TO_TIER` (`core/services/plan_service.py`), dentro de
`_precos_anuais_de_producao()` — reusada e não reescrita, senão o comparador vira
a duplicação que ele existe para caçar.

## O que este arquivo NÃO prova

Ele ata **código ↔ HTML**. Não ata **Stripe ↔ HTML**: o valor que o cartão de
fato cobra mora no painel do Stripe, fora deste repositório, e nenhum teste daqui
o alcança. Verde aqui quer dizer "as três fontes DESTE repo concordam", nunca "o
cliente é cobrado isso".
"""

import pathlib
import re

from test_pix_pricing_contrato import _precos_anuais_de_producao

from core.services.pix_pricing import PRECOS_ANUAIS_CENTS

# O que a rota do Pix aceita vender (`frontend/routes/billing_pix.py:58`).
_VENDAVEIS = {"essencial", "pro", "pro_max"}


def _anuais_visiveis() -> set[int]:
    """Os preços anuais que o usuário LÊ na `/precos`, em centavos.

    **Só o que tem `/ano`.** Os `data-price-annual` com "Equivale a … por mês"
    são o rateio mensal do anual, não o preço — entrariam como R$ 8,25 e
    quebrariam a comparação por um motivo falso.

    Devolve um CONJUNTO e não um mapa plano→preço: no markup estático não há
    atributo que nomeie o plano, e derivar por POSIÇÃO daria um teste que quebra
    ao reordenar os cards. Quem prende a atribuição tier ↔ preço é o
    `_precos_anuais_de_producao()`, que lê o `PLAN_PRICES` chaveado por tier.

    **Cegueira declarada:** dois planos trocando de preço ENTRE SI no markup
    passa aqui. Fica vermelho na outra metade — a menos que o `PLAN_PRICES`
    tenha trocado junto, e aí sobra revisão de diff.
    """
    html = (pathlib.Path(__file__).resolve().parent.parent
            / "frontend" / "precos.html").read_text(encoding="utf-8")
    # `[^>]*` e não `.*`: o casamento não pode atravessar a tag seguinte.
    elementos = re.findall(r"data-price-annual[^>]*>(.*?)</(?:span|div)>", html)
    # `.split("<")[0]` corta o `<small class="per">/ano</small>` que vem dentro do
    # valor nos cards — sem isso o `per` entraria no número.
    numeros = {re.sub(r"[^\d,]", "", texto.split("<")[0]).replace(",", ".")
               for texto in elementos if "/ano" in texto}
    assert numeros, (
        "nenhum `data-price-annual` com `/ano` em frontend/precos.html — o "
        "markup mudou de forma e este comparador virou verde vazio"
    )
    return {round(float(n) * 100) for n in numeros}


def test_a_varredura_do_markup_acha_os_precos_visiveis():
    """PISO do comparador, e o mesmo motivo do
    `test_os_precos_vem_da_fonte_e_nao_de_um_numero_copiado`: regex que casa de
    menos faria a comparação abaixo rodar sobre um conjunto pequeno demais e
    concordar por acaso.

    **Não repete os números aqui de propósito** — seria a QUARTA cópia do preço,
    dentro do arquivo que existe para caçar cópia (§0.7). O que se afirma é a
    FORMA: um preço por plano vendável, cada um em dois lugares do markup (o card
    e a tabela comparativa), todos positivos.
    """
    visiveis = _anuais_visiveis()
    assert len(visiveis) == len(_VENDAVEIS), (
        f"a varredura achou {len(visiveis)} preço(s) anual(is) no markup e a rota "
        f"vende {len(_VENDAVEIS)} planos: {sorted(visiveis)}"
    )
    assert all(isinstance(c, int) and c > 0 for c in visiveis)


def test_o_preco_do_codigo_e_o_que_o_script_da_precos_declara():
    """1 × 2, com a ponte de nomes. É a metade que prende a ATRIBUIÇÃO.

    *Negativo: troque `"pro_max": 49900` por `29900` em `PRECOS_ANUAIS_CENTS`, ou
    o `annual` de um tier no `PLAN_PRICES` → vermelho nomeando o plano.*
    """
    da_tela = _precos_anuais_de_producao()
    divergentes = {legado: (cents, da_tela.get(legado))
                   for legado, cents in PRECOS_ANUAIS_CENTS.items()
                   if da_tela.get(legado) != cents}
    assert not divergentes, (
        "preço divergiu entre o código e o PLAN_PRICES da precos.html "
        f"{{plano: (código, tela)}}: {divergentes}"
    )


def test_a_tabela_do_pix_cobre_exatamente_o_que_a_rota_vende():
    """Sem isto, APAGAR uma entrada de `PRECOS_ANUAIS_CENTS` deixaria o teste
    acima verde por vácuo — e a venda daquele plano viraria 503 calado."""
    assert set(PRECOS_ANUAIS_CENTS) == _VENDAVEIS


def test_o_preco_do_codigo_e_o_que_o_usuario_LE():
    """1 × 3 — a metade que o `PLAN_PRICES` sozinho não cobre.

    *Negativo: troque um `R$&nbsp;499/ano` do markup por `R$&nbsp;599/ano` (só no
    markup, deixando `PLAN_PRICES` e o código como estão) → vermelho aqui e VERDE
    nos outros dois testes. É exatamente o buraco: a página anuncia um preço e a
    cobrança emite outro.*
    """
    assert set(PRECOS_ANUAIS_CENTS.values()) == _anuais_visiveis(), (
        f"o preço do CÓDIGO não é o que a página MOSTRA: "
        f"código={sorted(PRECOS_ANUAIS_CENTS.values())}, "
        f"markup={sorted(_anuais_visiveis())}"
    )
