"""Ordem inversa × recorte: o par do lançamento manual só nasce numa transação
que o aviso e o modal enxergam (`ACTIONABLE_PENDING_SQL`, recorte de
`BANK_ACCOUNTS_SQL`: BRL, conexão não PAUSED/DELETED, identidade da conta).

Apontamento P1 do Codex no PR #563: as candidatas de
`_propose_manual_reconciliation` filtravam só por usuário e `type = 'BANK'`.
Transação de conexão pausada ou de conta em dólar virava um par que nenhuma
tela mostra — e tomava o lugar de uma elegível de mesmo valor e data.

Controle negativo esperado (CLAUDE.md §3; NÃO medido, medir no CI: sem
Postgres aqui): voltar as candidatas ao join direto `a.id = o.account_id` com
só `upper(a.type) = 'BANK'` deveria deixar vermelhos a pausada, a em dólar e a
pausada × ativa. Positivo: a reconexão por item novo (transação presa à linha
antiga da mesma conta) continua formando par, e na pausada × ativa a ativa
forma.
"""
from __future__ import annotations

import db
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    ia_fora, manda, tx, uid_pro, ultimo_launch,
)
from tests.test_fusao_of_reconexao_por_item_novo import _item
from tests.test_reconciliacao_manual_depois_do_import import _of, _pendentes, _sem_par
from tests.test_reconciliacao_resolver import _estado, _q


def _importado(uid, item_id, conta, *transacoes) -> int:
    conexao = _item(uid, item_id, conta, "950.00", transacoes)
    assert db.import_open_finance_launches(uid, conexao)["inserted"] == len(transacoes)
    return conexao


def test_conexao_pausada_nao_forma_par(uid_pro, ia_fora):
    a = _importado(uid_pro, f"item-A-{uid_pro}", f"acc-A-{uid_pro}",
                   tx(uid_pro, "-50.00", today_tz(), "MERCADO"))
    db.pause_open_finance_connection(a)

    manda(uid_pro, "gastei 50 no mercado em dinheiro")

    _sem_par(_of(uid_pro))
    assert _pendentes(uid_pro) == 0


def test_conta_em_dolar_nao_forma_par(uid_pro, ia_fora):
    _importado(uid_pro, f"item-A-{uid_pro}", f"acc-A-{uid_pro}",
               tx(uid_pro, "-50.00", today_tz(), "MERCADO"))
    assert _q("update open_finance_accounts set currency='USD' "
              "where provider_account_id=%s returning id", (f"acc-A-{uid_pro}",))

    manda(uid_pro, "gastei 50 no mercado em dinheiro")

    _sem_par(_of(uid_pro))
    assert _pendentes(uid_pro) == 0


def test_pausada_e_ativa_o_par_vai_para_a_ativa(uid_pro, ia_fora):
    """A pausada é a que o código antigo escolheria: id menor e nome que bate."""
    hoje = today_tz()
    a = _importado(uid_pro, f"item-A-{uid_pro}", f"acc-A-{uid_pro}",
                   tx(uid_pro, "-50.00", hoje, "MERCADO", ident="1"))
    _importado(uid_pro, f"item-B-{uid_pro}", f"acc-B-{uid_pro}",
               tx(uid_pro, "-50.00", hoje, "PADARIA", ident="2"))
    db.pause_open_finance_connection(a)

    manda(uid_pro, "gastei 50 no mercado em dinheiro")

    _sem_par(_of(uid_pro, "1"))
    assert _estado(_of(uid_pro, "2"))["match_launch_id"] == ultimo_launch(uid_pro)
    assert _pendentes(uid_pro) == 1


def test_reconexao_por_item_novo_ainda_forma_par(uid_pro, ia_fora):
    """Mesma conta em duas linhas: a antiga (conexão pausada) presa à transação,
    a nova sem ela. O recorte casa pela identidade, então o par nasce e aparece."""
    conta = f"acc-real-{uid_pro}"
    a = _importado(uid_pro, f"item-A-{uid_pro}", conta,
                   tx(uid_pro, "-50.00", today_tz(), "MERCADO"))
    db.pause_open_finance_connection(a)
    _item(uid_pro, f"item-B-{uid_pro}", conta, "950.00")

    manda(uid_pro, "gastei 50 no mercado em dinheiro")

    assert _estado(_of(uid_pro))["match_launch_id"] == ultimo_launch(uid_pro)
    assert _pendentes(uid_pro) == 1
