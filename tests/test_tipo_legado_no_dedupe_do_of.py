"""Lançamento manual NÃO casa mais com transação Open Finance (decisão 2026-09).

Antes desta mudança `_find_manual_candidates` (db/open_finance.py) incluía
`source='manual'` entre os candidatos a merge: lançamento manual + tx OF de
mesmo valor/data fundiam numa linha só (`auto_merged=1`) e o gasto não contava
duas vezes no "Gastos do mês". Este arquivo inteiro prendia esse dedupe — o
tipo legado ('saida'/'entrada'), o `alvo` nulo e o falso casamento por nome.

A decisão "Lançamentos Manuais Exclusivos para Dinheiro (Carteira Piggy)"
inverte a premissa: lançamento manual é DINHEIRO EM ESPÉCIE, não transação
bancária. Se o banco importar depois um movimento de mesmo valor/data, são
dois fatos distintos e o OF launch entra SEPARADO (`inserted=1`). A
reconciliação reversa (`reconcile_manual_launch`) também saiu dos três
escritores (rota do dashboard, handler do bot, entrada rápida).

Custo aceito e explícito: o mesmo valor lançado à mão E importado pelo banco
conta DUAS vezes no "Gastos do mês" — o manual tem `delta_conta` real (move a
Carteira) e o OF launch (`delta_conta=0`) alimenta o mês/sobrou/timeline.
É o preço de não deixar o sync bancário "absorver" dinheiro em espécie, e é
por isso que o manual agora é só dinheiro: quem lança à mão o que o banco já
importa está usando a ferramenta errada, e o produto não esconde mais isso
fundindo as linhas.

Os cenários antigos ficam como registro do comportamento novo, um a um. O que
eles provam agora: NENHUM casamento acontece — nem tipo legado, nem tipo
moderno, nem `alvo` nulo, nem nome divergente. O parametrizado legado/moderno
continua valendo: as duas formas de `tipo` se comportam IGUAL (nenhuma casa).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time
from decimal import Decimal

import pytest

import db
import frontend.finance_bot_websocket_custom as dashboard
from utils_date import _tz, today_tz

from tests.test_tipo_legado_no_dashboard import _grava_tipo_legado


def _importa_of(user_id: int, dia, *, valor="50.00", descricao="MERCADO PAGUE MENOS",
                categoria="mercado") -> dict:
    """A transação OF pelo caminho de PRODUÇÃO: espelho Pluggy → importador."""
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


def _qtd_lancamentos(user_id: int) -> int:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from launches where user_id=%s", (user_id,))
        return cur.fetchone()["n"]


# ── o novo contrato: manual nunca é candidato a merge ────────────────────────

def test_manual_moderno_nao_casa_com_of(pro_user_id):
    """Era o "casamento moderno continua igual" do mundo do dedupe. Hoje a
    assertiva é a inversa: NÃO funde. O OF launch entra separado e o gasto do
    mês soma as duas linhas (custo documentado no docstring do módulo)."""
    hoje = today_tz()
    db.add_launch_and_update_balance(
        pro_user_id, "despesa", 50.00, "Mercado Pague Menos", "Mercado Pague Menos",
        "mercado", criado_em=datetime.combine(hoje, time(9, 0), tzinfo=_tz()),
    )

    rep = _importa_of(pro_user_id, hoje)

    assert (rep["auto_merged"], rep["inserted"]) == (0, 1), rep
    assert _qtd_lancamentos(pro_user_id) == 2
    assert _gasto_do_mes(pro_user_id) == 100.0, _gasto_do_mes(pro_user_id)


def test_manual_legado_nao_casa_com_of(pro_user_id):
    """O caso que DOBRAVA dinheiro antes (gêmeo legado não era candidato e o
    gasto contava duas vezes). Com a decisão nova o legado se comporta como o
    moderno: nenhum dos dois casa — e a dupla contagem deixa de ser defeito
    para ser o contrato explícito do mundo manual=cash."""
    hoje = today_tz()
    _grava_tipo_legado(pro_user_id, "saida", 50.00, "mercado",
                       nota="Mercado Pague Menos", alvo="Mercado Pague Menos",
                       criado_em=datetime.combine(hoje, time(9, 0)))

    rep = _importa_of(pro_user_id, hoje)

    assert (rep["auto_merged"], rep["inserted"]) == (0, 1), rep
    assert _qtd_lancamentos(pro_user_id) == 2
    assert _gasto_do_mes(pro_user_id) == 100.0, _gasto_do_mes(pro_user_id)
    # a linha manual do usuário continua intacta
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from launches where user_id=%s and tipo='saida'",
                    (pro_user_id,))
        assert cur.fetchone()["n"] == 1


# ── receita/aporte: continuam sem casamento (agora por exclusão de manual) ───

def test_receita_legada_nao_casa_com_gasto_do_of(pro_user_id):
    """'entrada' é receita. Uma despesa do OF não a engole — antes por causa do
    tipo, hoje porque manual nunca é candidato. O OF launch entra separado."""
    hoje = today_tz()
    _grava_tipo_legado(pro_user_id, "entrada", 50.00, "salario",
                       nota="Mercado Pague Menos", alvo="Mercado Pague Menos",
                       criado_em=datetime.combine(hoje, time(9, 0)))

    rep = _importa_of(pro_user_id, hoje)

    assert (rep["auto_merged"], rep["pending"], rep["inserted"]) == (0, 0, 1), rep
    assert _gasto_do_mes(pro_user_id) == 50.0, _gasto_do_mes(pro_user_id)


def test_aporte_legado_nao_casa_com_gasto_do_of(pro_user_id):
    """`aporte_investimento` casava exato por `TIPO_CANON_SQL`; hoje não casa
    por nenhum motivo — manual está fora dos candidatos."""
    hoje = today_tz()
    _grava_tipo_legado(pro_user_id, "aporte_investimento", 50.00, "mercado",
                       nota="Mercado Pague Menos", alvo="Mercado Pague Menos",
                       criado_em=datetime.combine(hoje, time(9, 0)))

    rep = _importa_of(pro_user_id, hoje)

    assert (rep["auto_merged"], rep["pending"], rep["inserted"]) == (0, 0, 1), rep


def test_gasto_legado_de_outro_estabelecimento_nenhum_casamento(pro_user_id):
    """Antes: nome diverge → 'ask' (sugestão pendente, `pending=1`). Hoje nem
    chega a avaliar nome — sem candidatos não há ambiguidade, o OF launch é
    criado direto (`pending=0`) e a linha manual do usuário continua existindo."""
    hoje = today_tz()
    _grava_tipo_legado(pro_user_id, "saida", 50.00, "farmacia",
                       nota="Drogaria Sao Paulo", alvo="Drogaria Sao Paulo",
                       criado_em=datetime.combine(hoje, time(9, 0)))

    rep = _importa_of(pro_user_id, hoje)

    assert (rep["auto_merged"], rep["pending"], rep["inserted"]) == (0, 0, 1), rep
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select count(*) as n from launches where user_id=%s and tipo='saida'",
                    (pro_user_id,))
        assert cur.fetchone()["n"] == 1, "a linha manual do usuário sumiu"


# ── a fatia mais larga do mundo antigo: `alvo` NULO ─────────────────────────

@pytest.mark.parametrize("tipo", ["saida", "despesa"])
def test_alvo_nulo_tambem_nao_casa(pro_user_id, tipo):
    """`alvo` NULO casava com QUALQUE estabelecimento via `_is_generic_merchant`
    — era o maior furo do dedupe. Hoje nada casa: legado e moderno, o MESMO
    insert com só o `tipo` mudando, dão o MESMO resultado (nenhum merge), que é
    o que mantém o comportamento escolha e não acidente. O preço, assumido:
    `monthly_expense=100.0` e duas linhas nas duas formas."""
    hoje = today_tz()
    _grava_tipo_legado(pro_user_id, tipo, 50.00, "mercado", nota="gasto",
                       criado_em=datetime.combine(hoje, time(9, 0)))

    rep = _importa_of(pro_user_id, hoje, descricao="POSTO IPIRANGA",
                      categoria="transporte")

    assert (rep["auto_merged"], rep["pending"], rep["inserted"]) == (0, 0, 1), rep
    assert _gasto_do_mes(pro_user_id) == 100.0, _gasto_do_mes(pro_user_id)
    assert _qtd_lancamentos(pro_user_id) == 2
