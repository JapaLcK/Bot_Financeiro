"""Lançamento manual NUNCA funde em silêncio com transação Open Finance (2026-09).

Antes desta mudança `_find_manual_candidates` (db/open_finance.py) incluía
`source='manual'` entre os candidatos a merge: lançamento manual + tx OF de
mesmo valor/data fundiam numa linha só (`auto_merged=1`) e o gasto não contava
duas vezes no "Gastos do mês". Este arquivo inteiro prendia esse dedupe — o
tipo legado ('saida'/'entrada'), o `alvo` nulo e o falso casamento por nome.

A decisão "Lançamentos Manuais Exclusivos para Dinheiro (Carteira Piggy)"
inverte a premissa: lançamento manual é DINHEIRO EM ESPÉCIE e não pode ser
ABSORVIDO em silêncio. O mecanismo exato:
- casamento AUTO com candidato manual é REBAIXADO a 'ask' no importador — vira
  PENDÊNCIA e o usuário decide (`confirm_reconciliation`/`reject_reconciliation`);
- na ordem inversa (lançamento manual criado DEPOIS da importação) os
  escritores do manual só criam a mesma pendência
  (`propose_manual_reconciliation`): permanece separado até o usuário decidir.

O que os cenários antigos provam AGORA: nenhum AUTO-merge acontece — nem tipo
legado, nem moderno, nem `alvo` nulo, nem nome divergente. Os casos ambíguos
(`alvo` nulo, nome divergente) agora vão a 'ask' (`pending=1`) em vez de fundir;
os de tipo incompatível (receita vs. despesa do OF, aporte) continuam sem
candidato nenhum (`pending=0`). E o preço, assumido e documentado: o mesmo
valor lançado à mão E importado pelo banco conta DUAS vezes no "Gastos do mês"
até o usuário resolver a pendência.

O parametrizado legado/moderno continua valendo: as duas formas de `tipo` se
comportam IGUAL (nenhuma funde em silêncio) — escolha, não acidente.
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


# ── receita/aporte: continuam sem casamento (filtro de tipo, sem pendência) ──

def test_receita_legada_nao_casa_com_gasto_do_of(pro_user_id):
    """'entrada' é receita. Uma despesa do OF não a engole — antes por causa do
    tipo, hoje pelo mesmo filtro de tipo (candidato manual existe, mas o tipo
    não casa). OF launch entra separado, sem pendência."""
    hoje = today_tz()
    _grava_tipo_legado(pro_user_id, "entrada", 50.00, "salario",
                       nota="Mercado Pague Menos", alvo="Mercado Pague Menos",
                       criado_em=datetime.combine(hoje, time(9, 0)))

    rep = _importa_of(pro_user_id, hoje)

    assert (rep["auto_merged"], rep["pending"], rep["inserted"]) == (0, 0, 1), rep
    assert _gasto_do_mes(pro_user_id) == 50.0, _gasto_do_mes(pro_user_id)


def test_aporte_legado_nao_casa_com_gasto_do_of(pro_user_id):
    """`aporte_investimento` casava exato por `TIPO_CANON_SQL`; hoje o filtro de
    tipo continua separando — o candidato manual existe, mas não casa com a
    despesa do OF. Sem pendência."""
    hoje = today_tz()
    _grava_tipo_legado(pro_user_id, "aporte_investimento", 50.00, "mercado",
                       nota="Mercado Pague Menos", alvo="Mercado Pague Menos",
                       criado_em=datetime.combine(hoje, time(9, 0)))

    rep = _importa_of(pro_user_id, hoje)

    assert (rep["auto_merged"], rep["pending"], rep["inserted"]) == (0, 0, 1), rep


def test_gasto_legado_de_outro_estabelecimento_vira_pendencia(pro_user_id):
    """Nome diverge → 'ask': SEMPRE foi sugestão pendente (nunca fusão auto),
    e continua sendo — mas agora o 'ask' é o ÚNICO desfecho possível para
    candidato manual (o 'auto' foi rebaixado). OF launch entra como sombra
    pendente e a linha manual do usuário continua existindo."""
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


# ── a fatia mais larga do mundo antigo: `alvo` NULO ─────────────────────────

@pytest.mark.parametrize("tipo", ["saida", "despesa"])
def test_alvo_nulo_vira_pendencia_nao_fusao(pro_user_id, tipo):
    """`alvo` NULO casava com QUALQUER estabelecimento via `_is_generic_merchant`
    e fundia em silêncio. Hoje o 'auto' em candidato manual é rebaixado a
    'ask': vira pendência (`pending=1`), e legado/moderno continuam dando o
    MESMO resultado nas duas formas de `tipo` — escolha, não acidente. O preço,
    assumido: `monthly_expense=100.0` e duas linhas (manual + sombra) até o
    usuário confirmar ou rejeitar."""
    hoje = today_tz()
    _grava_tipo_legado(pro_user_id, tipo, 50.00, "mercado", nota="gasto",
                       criado_em=datetime.combine(hoje, time(9, 0)))

    rep = _importa_of(pro_user_id, hoje, descricao="POSTO IPIRANGA",
                      categoria="transporte")

    assert (rep["auto_merged"], rep["pending"], rep["inserted"]) == (0, 1, 1), rep
    assert _gasto_do_mes(pro_user_id) == 100.0, _gasto_do_mes(pro_user_id)
    assert _qtd_lancamentos(pro_user_id) == 2
