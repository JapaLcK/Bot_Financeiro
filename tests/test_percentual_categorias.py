"""Paridade do % de categoria (CLAUDE.md §0.7): `periodo()` e o `percentualDe` do app leem a MESMA tabela.
O app tem a sua cópia da regra (`app/src/features/painel/decimal.ts`); o jest `painel_card_gastos` lê o mesmo JSON."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from api.nativo import mes_detalhes

TABELA = json.loads((Path(__file__).parent / "fixtures" / "percentual_categorias.json").read_text())


@pytest.mark.parametrize("caso", TABELA, ids=lambda c: "+".join(c["valores"]))
def test_percentual_do_periodo_bate_com_a_tabela(caso, monkeypatch):
    cats = [{"categoria": f"c{i}", "valor": Decimal(v), "quantidade": 1, "maiores": []} for i, v in enumerate(caso["valores"])]
    leitura = {"inicio": "2026-08", "fim": "2026-08", "ate": "2026-08-31", "entrou": Decimal(0), "saiu": Decimal(0),
               "meses": [{"mes": "2026-08", "entrou": Decimal(0), "saiu": Decimal(0)}], "categorias": cats,
               "motivos": [], "historico_desde": None, "fora_do_total": 0}
    monkeypatch.setattr(mes_detalhes, "ler_periodo", lambda *a: leitura)
    p = mes_detalhes.periodo((2026, 8), (2026, 8), 1)
    assert [c.percentual for c in p.categorias] == caso["percentuais"]
