"""
tests/test_stripe_cancel_reason.py — a string que decide o ramo TERMINAL existe
mesmo no enum da Stripe?

Existe porque a resposta foi **não** por todo o tempo em que o ramo existiu. O
webhook comparava `cancellation_details.reason == "payment_failure"`; o valor da
Stripe é `payment_failed`. O ramo terminal — "grave `unpaid` e limpe o relógio
incondicionalmente", decisão do dono — caía SEMPRE no `else`, gravava `canceled`,
e o motivo do bloqueio se perdia exatamente onde ele deveria ser preservado.

**Por que nenhum teste pegou**: `tests/test_dunning_encerramento_terminal.py`
monta o evento com a mesma string do código. Caso e código derivavam da MESMA
constante errada e se moviam juntos — a patologia "injeção pela constante de que
o próprio caso do teste é DERIVADO" do `docs/controles_declarados.md`, que
"deixa tudo verde". O remédio prescrito lá é o caso ABSOLUTO: uma âncora fora da
constante. Aqui a âncora é o **pacote `stripe` instalado**, que é a única coisa
neste repositório que sabe o alfabeto de verdade.

Mesmo padrão, e mesma razão, do subset de ícones (`tests/test_phosphor_subset.py`):
quando a duplicação de uma regra é inevitável — a Stripe declara o enum, nós
escrevemos uma string —, **um teste compara as duas** (§0.7).

O enum é lido do FONTE do pacote e não de um `Literal` importável porque
`CancellationDetails` é uma classe aninhada de tipagem: em runtime o campo é só
`str`. Ler o texto é o que sobra, e é honesto — o alvo é pegar a Stripe
RENOMEANDO o valor numa atualização de dependência, e isso muda o fonte.
"""
from __future__ import annotations

import inspect
import pathlib
import re

import pytest

from core.services.billing_dunning import STRIPE_CANCEL_REASON_INADIMPLENCIA


def _enum_de_cancelamento_do_pacote() -> set[str]:
    """Os valores de `Subscription.CancellationDetails.reason` do pacote instalado.

    Levanta se não achar, em vez de devolver conjunto vazio: um `set()` faria a
    asserção abaixo falhar por motivo errado e o leitor concluiria que a nossa
    string está errada quando o que mudou foi a forma do pacote.
    """
    stripe = pytest.importorskip("stripe")
    fonte = pathlib.Path(inspect.getfile(stripe.Subscription)).read_text(encoding="utf-8")
    bloco = re.search(r"class CancellationDetails.*?(?=\n    class |\n\nclass )",
                      fonte, re.S)
    assert bloco, "não achei `class CancellationDetails` no pacote stripe"
    literais = re.search(r"reason: Optional\[\s*Union\[\s*Literal\[(.*?)\]",
                         bloco.group(0), re.S)
    assert literais, "não achei o Literal de `reason` em CancellationDetails"
    valores = set(re.findall(r'"([a-z_]+)"', literais.group(1)))
    assert valores, "o Literal de `reason` saiu vazio"
    return valores


def test_o_motivo_terminal_existe_no_enum_da_stripe():
    """A âncora ABSOLUTA: nossa string tem de estar no alfabeto do pacote.

    Este é o teste que `payment_failure` reprovaria — e o único do repositório
    que reprovaria, porque todos os outros montam o evento com a nossa string.
    """
    enum = _enum_de_cancelamento_do_pacote()
    assert STRIPE_CANCEL_REASON_INADIMPLENCIA in enum, (
        f"`{STRIPE_CANCEL_REASON_INADIMPLENCIA}` não existe em "
        f"{sorted(enum)} — o ramo terminal do `customer.subscription.deleted` "
        "nunca dispara e o motivo do bloqueio é gravado como `canceled`")


def test_o_enum_continua_tendo_os_motivos_que_a_decisao_assume():
    """POSITIVO do par: o teste acima passaria num enum que virou `{"qualquer"}`
    se a nossa string fosse `"qualquer"`. Este fixa o alfabeto que a decisão de
    produto olhou — se a Stripe acrescentar um motivo, ele fica vermelho e
    alguém decide se o novo é terminal, em vez de o silêncio decidir.

    `payment_disputed` está aqui de propósito e **não** é terminal por
    inadimplência: chargeback é disputa de um pagamento que ENTROU, não retry
    esgotado. Decisão em aberto, registrada — ver a docstring do ramo.
    """
    assert _enum_de_cancelamento_do_pacote() == {
        "canceled_by_retention_policy",
        "cancellation_requested",
        "payment_disputed",
        "payment_failed",
    }
