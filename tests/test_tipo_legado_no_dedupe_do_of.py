"""A forma legada de `tipo` no dedupe do Open Finance — a única da cauda que
dobra DINHEIRO.

Os irmãos (`test_tipo_legado_sem_numero.py`, `..._na_cauda.py`) tratam sugestão
que some e lista que encolhe. Aqui o sintoma é outro: `_find_manual_candidates`
(db/open_finance.py:1360) filtrava `tipo = %s` com o tipo vindo de
`classify_open_finance_launch` (:1277), que só produz 'despesa'/'receita'. O
gêmeo manual LEGADO ('saida') nunca era candidato, `pick_reconciliation_match`
não tinha o que casar, e o importador criava um segundo lançamento para a MESMA
transação real.

Medido no caminho de produção (espelho Pluggy → `import_open_finance_launches`
→ `get_financial_data`), manual de 50 + transação OF de -50, mesmo dia, mesmo
estabelecimento:

    manual 'saida'   (legado)  → inserted=1, auto_merged=0, monthly_expense=100.0
    manual 'despesa' (moderno) → inserted=0, auto_merged=1, monthly_expense= 50.0

50 reais gastos aparecendo como 100. Não há outra rede: o OF launch entra com
`delta_conta=0` (não mexe em `accounts.balance`), mas conta inteiro no "Gastos
do mês" e no "sobrou".

Conserto: `TIPO_CANON_SQL` no lado da COLUNA, igual `list_launches_by_tipo`
(db/accounts.py:172) — o tipo aqui também é parâmetro, não literal.

O risco espelhado é o FALSO casamento — ampliar o que casa esconde lançamento
legítimo — então o grupo tem as DUAS mutações medidas, uma para cada lado:

  `{TIPO_CANON_SQL} = %s` → `tipo = %s`            (NEGATIVO, desliga o conserto)
      FAILED test_gasto_legado_gemeo_nao_conta_duas_vezes      (100.0 != 50.0)
      FAILED test_gasto_legado_de_outro_estabelecimento_nao_e_engolido
      3 passed

  `{TIPO_CANON_SQL} = %s` → `coalesce(%s, tipo) is not null`   (alargou de MAIS)
      FAILED test_receita_legada_nao_casa_com_gasto_do_of
      FAILED test_aporte_legado_nao_casa_com_gasto_do_of
      3 passed

`test_o_casamento_moderno_continua_igual` é o positivo do meio: verde nas duas
mutações de propósito — ele prova que o caminho que já funcionava não mudou.

`alvo` é obrigatório nos casos: `alvo` NULO cai em `_is_generic_merchant`
(db/open_finance.py:1322) e casa com QUALQUER estabelecimento, o que apagaria a
diferença entre casar por nome e casar por genérico.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time
from decimal import Decimal

import db
import frontend.finance_bot_websocket_custom as dashboard
from utils_date import _tz, today_tz

from tests.test_tipo_legado_no_dashboard import _grava_tipo_legado


def _importa_of(user_id: int, dia, *, valor="50.00", descricao="MERCADO PAGUE MENOS",
                categoria="mercado") -> dict:
    """A transação OF pelo caminho de PRODUÇÃO: espelho Pluggy → importador.

    Local, e não o `_of_do_dia` de `test_category_launches_query.py`, porque
    aquele crava `assert rep["inserted"] == 1` — que é exatamente o número que
    este conserto muda."""
    conexao = db.save_pluggy_open_finance_item(
        user_id,
        {"id": f"item-of-{user_id}", "connector": {"id": 612, "name": "Nubank"},
         "status": "UPDATED"},
    )
    db.save_open_finance_sync(conexao["id"], [{
        "provider_account_id": f"acc-of-{user_id}",
        "name": "Nubank Conta", "type": "BANK", "subtype": "CHECKING_ACCOUNT",
        "currency": "BRL", "balance": Decimal("1000.00"), "raw": {},
        "transactions": [{
            "provider_transaction_id": f"of-tx-{user_id}",
            "description": descricao,
            "amount": Decimal("-" + valor),
            "transaction_date": dia,
            "transacted_at": None,
            "category": categoria,
            "raw": {},
        }],
    }])
    return db.import_open_finance_launches(user_id, conexao["id"])


def _gasto_do_mes(user_id: int) -> float:
    h = today_tz()
    d = asyncio.run(dashboard.get_financial_data(user_id, year=h.year, month=h.month))
    return d["monthly_expense"]


# ── o defeito: gasto contado duas vezes ─────────────────────────────────────

def test_gasto_legado_gemeo_nao_conta_duas_vezes(pro_user_id):
    """Um gasto de 50 lançado à mão na forma legada + a mesma compra chegando
    pelo Open Finance. Uma transação real = uma linha = 50 no mês."""
    hoje = today_tz()
    _grava_tipo_legado(pro_user_id, "saida", 50.00, "mercado",
                       nota="Mercado Pague Menos", alvo="Mercado Pague Menos",
                       criado_em=datetime.combine(hoje, time(9, 0)))

    rep = _importa_of(pro_user_id, hoje)

    assert rep["auto_merged"] == 1, rep
    assert rep["inserted"] == 0, rep
    assert _gasto_do_mes(pro_user_id) == 50.0, _gasto_do_mes(pro_user_id)


# ── positivo 1: não alargou de menos ────────────────────────────────────────

def test_o_casamento_moderno_continua_igual(pro_user_id):
    hoje = today_tz()
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50.00, "Mercado Pague Menos", "Mercado Pague Menos",
        "mercado", criado_em=datetime.combine(hoje, time(9, 0), tzinfo=_tz()),
    )

    rep = _importa_of(pro_user_id, hoje)

    assert (rep["auto_merged"], rep["inserted"]) == (1, 0), rep
    assert _gasto_do_mes(pro_user_id) == 50.0, _gasto_do_mes(pro_user_id)


# ── positivos 2 e 3: não alargou de MAIS (falso casamento) ──────────────────

def test_receita_legada_nao_casa_com_gasto_do_of(pro_user_id):
    """'entrada' é receita. Uma despesa do OF não pode engoli-la só porque o
    valor, o dia e o estabelecimento coincidem — seria esconder uma entrada
    legítima do usuário."""
    hoje = today_tz()
    _grava_tipo_legado(pro_user_id, "entrada", 50.00, "salario",
                       nota="Mercado Pague Menos", alvo="Mercado Pague Menos",
                       criado_em=datetime.combine(hoje, time(9, 0)))

    rep = _importa_of(pro_user_id, hoje)

    assert (rep["auto_merged"], rep["pending"], rep["inserted"]) == (0, 0, 1), rep
    assert _gasto_do_mes(pro_user_id) == 50.0, _gasto_do_mes(pro_user_id)


def test_aporte_legado_nao_casa_com_gasto_do_of(pro_user_id):
    """`aporte_investimento` não é forma legada de nada: `TIPO_CANON_SQL` o
    deixa casando EXATO, como antes. Uma despesa do OF não o alcança."""
    hoje = today_tz()
    _grava_tipo_legado(pro_user_id, "aporte_investimento", 50.00, "mercado",
                       nota="Mercado Pague Menos", alvo="Mercado Pague Menos",
                       criado_em=datetime.combine(hoje, time(9, 0)))

    rep = _importa_of(pro_user_id, hoje)

    assert (rep["auto_merged"], rep["pending"], rep["inserted"]) == (0, 0, 1), rep


def test_gasto_legado_de_outro_estabelecimento_nao_e_engolido(pro_user_id):
    """O que passa a ser candidato continua passando pelos MESMOS portões que a
    forma moderna: nome diverge → 'ask' (sugestão pendente), nunca fusão
    automática. A linha manual do usuário continua existindo."""
    hoje = today_tz()
    _grava_tipo_legado(pro_user_id, "saida", 50.00, "farmacia",
                       nota="Drogaria Sao Paulo", alvo="Drogaria Sao Paulo",
                       criado_em=datetime.combine(hoje, time(9, 0)))

    rep = _importa_of(pro_user_id, hoje)

    assert (rep["auto_merged"], rep["pending"], rep["inserted"]) == (0, 1, 1), rep
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from launches where user_id=%s and tipo='saida'",
                    (pro_user_id,))
        assert cur.fetchone()["n"] == 1, "a linha manual do usuário sumiu"
