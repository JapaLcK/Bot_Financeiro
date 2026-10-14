"""`GET /api/v2/gastos/categoria`: bordas do corte, seed, ordem/origem dos motivos e valor médio gigante.

Complementa `test_api_v2_gastos_categoria.py` (mesmos helpers). Cada teste carrega o controle NEGATIVO medido
à mão (a linha de `db/gastos.py` indicada, mutada, deixa o teste vermelho) e o positivo no mesmo teste.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from psycopg.types.json import Jsonb

from conftest import usuario_pagante
from tests._patrimonio_helpers import conexao, conta, tx_banco
from tests.test_api_v2_gastos import _l
from tests.test_api_v2_gastos_bordas import corte  # noqa: F401 (fixture)
from tests.test_api_v2_gastos_categoria import _antes, _cat, _dia
from tests.test_resumo_mes_regra import INICIO

D = Decimal


# ── corte exatamente no dia 1 de um mês da série ─────────────────────────────

@pytest.mark.parametrize("k, serie, aviso", [
    (2, [D(33), D(55), D(77)], False),   # corte no dia 1 de M-2: nenhum mês é tocado
    (1, [None, D(55), D(77)], True),     # no dia 1 de M-1: só o M-2 sai
    (0, [None, None, D(77)], True)])     # no dia 1 de M: M-2 e M-1 saem, M fica inteiro e sem `historico_desde`
def test_corte_no_dia_1_nao_toca_o_proprio_mes(corte, k, serie, aviso):
    a = usuario_pagante()
    for meses, valor in ((2, 33), (1, 55), (0, 77)):
        _l(a, valor, _antes(INICIO, meses).replace(day=5), categoria="mercado")
    corte(_antes(INICIO, k))
    r = _cat(a)
    assert [s["valor"] for s in r["serie"]] == serie and ("inicio_do_historico" in r["motivos"]) is aviso
    assert r["total"] == D(77) and r["historico_desde"] is None
    corte(INICIO.replace(day=2))  # positivo: o corte dentro do mês pedido avisa e informa a data
    assert _cat(a)["historico_desde"] == INICIO.replace(day=2).isoformat()


# ── seed antes de ler ────────────────────────────────────────────────────────

def test_usuario_novo_sai_com_o_emoji_do_catalogo_e_nao_o_neutro():
    a = usuario_pagante()
    _l(a, 10, _dia(3), categoria="alimentação")
    c = _cat(a, "alimentação")["categoria"]
    assert (c["emoji"], c["cor"]) == ("🍔", "#f59e0b")


# ── motivos: ordem e origem ──────────────────────────────────────────────────

def test_motivos_saem_na_ordem_do_contrato_e_nao_na_alfabetica(corte):
    a = usuario_pagante()
    corte(INICIO.replace(day=5))
    lid = _l(a, 30, _dia(8), categoria="mercado", source="open_finance")
    tx_banco(conta(conexao(a, f"item-{a}"), "acc-a", "1000"), "tx-pend", "-30", imported_launch_id=lid,
             raw=Jsonb({"status": "PENDING"}))
    _l(a, 100, _dia(12), categoria="mercado", moeda="USD")
    assert _cat(a)["motivos"] == ["inicio_do_historico", "transacao_pendente", "outra_moeda"]


def test_motivo_do_bloco_de_contas_sobe_na_categoria(corte):
    velho, limpo = usuario_pagante(), usuario_pagante()
    conta(conexao(velho, f"item-{velho}", sync=datetime.now(timezone.utc) - timedelta(hours=72)), "acc-v", "10")
    for u in (velho, limpo):
        _l(u, 10, _dia(3), categoria="mercado")
    assert _cat(velho)["motivos"] == ["banco_desatualizado"]
    assert _cat(limpo)["motivos"] == []


# ── valor médio com total gigante ────────────────────────────────────────────

def test_valor_medio_com_total_acima_de_1e27_nao_estoura_o_contexto_decimal():
    # Direto no `ler_categoria`: a serialização (`api/nativo/dinheiro.py::em_centavos`) tem o mesmo teto e é
    # outro conserto; aqui só o valor médio. A coluna `numeric` aceita o valor, só outro escritor chegaria lá.
    a = usuario_pagante()
    for n in (3, 4):
        _l(a, D(10) ** 27, _dia(n), categoria="mercado")
    m = _cat(a)["valor_medio"]
    assert m == D(10) ** 27 and m.as_tuple().exponent == -2
