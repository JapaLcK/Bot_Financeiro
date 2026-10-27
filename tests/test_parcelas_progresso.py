"""`db.parcelas_of.progresso` (o "n de N pagas") contra a tabela que as cópias TS e a do dashboard.js também leem."""
import json
from pathlib import Path

import pytest

from db.parcelas_of import cobre_todas, progresso

TABELA = json.loads((Path(__file__).parent / "fixtures" / "parcelas_progresso.json").read_text())


def test_a_tabela_tem_os_casos_de_sempre():
    assert len(TABELA) >= 13


@pytest.mark.parametrize("caso", TABELA, ids=[c["nota"] for c in TABELA])
def test_progresso_da_tabela(caso):
    assert progresso(caso["grupo"]) == caso["esperado"]


def _pc(*nos):
    return [{"installment_no": n, "is_paid": True} for n in nos]


@pytest.mark.parametrize("parcelas,n,esperado", [
    (_pc(1, 2, 3), 3, True), (_pc(3), 12, False), (_pc(5, 6, 7, 8, 9, 10, 11, 12), 12, False),
    (_pc(1, 2, 2), 3, False),  # repetida não cobre a 3 (contar linhas daria True)
    (_pc(None, None, None), 3, True),  # manual sem número: conta as linhas
])
def test_cobre_todas(parcelas, n, esperado):
    assert cobre_todas(parcelas, n) is esperado
