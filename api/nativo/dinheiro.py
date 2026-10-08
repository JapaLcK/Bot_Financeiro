"""Dinheiro público dos complementos, em centavos; taxa contratada não usa isto."""
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated

from pydantic import PlainSerializer


def em_centavos(valor: Decimal) -> str:
    return format(valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def soma_arredondada_diverge(valores: list[Decimal], total: Decimal | None = None) -> bool:
    """Grupos verdadeiros podem exibir centavos cuja soma difere do total real."""
    exibida = sum((Decimal(em_centavos(v)) for v in valores), Decimal(0))
    real = sum(valores, Decimal(0)) if total is None else total
    return exibida != Decimal(em_centavos(real))


Dinheiro = Annotated[Decimal, PlainSerializer(em_centavos, return_type=str, when_used="json")]
