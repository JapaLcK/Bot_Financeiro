"""`delete_user_data` (LGPD) toma o mutex do usuário e apaga o pai antes do lote.

Antes: lotes → investimentos/caixinhas, sem `_lock_user`. A ordem inversa de `accrue_all_*`
(pai → lote) e do aporte/depósito (conta → pai → lote) fechava ciclo com eles e com o reset.
A pausa é por statement (tests/_pausa_sql.py). Caso residual: conta SEM linha em `accounts`
(o `select for update` do mutex casa zero linhas): os ciclos com `accrue_*` seguem fechados
só pela ordem pai → lote, e o mutex falta.

O QUE ESTE ARQUIVO NÃO COBRE (medido por varredura de interleaving, já existente antes do PR):
`delete_user_data` × reset, × merge, × sync/conciliação do Open Finance ainda dão DeadlockDetected
em várias posições. O mutex fica antes do laço de tabelas por usuário, DEPOIS dos deletes de Open
Finance e crédito (variante B: no topo da transação, T17/T21/T23 de
tests/test_account_deletion_adocao_corrida.py modelam uma sessão que comita no meio da exclusão e
deixariam de medir), e sync/reset/merge fazem accounts → Open Finance. Por isso o reset NÃO está
na lista de pares abaixo: um teste só nos pontos benignos passaria e daria falsa segurança.
"""
import pytest

import db
import db.investments as investments
import db.pockets as pockets
import db.privacy as privacy
from tests._pausa_sql import PausaSql, sem_deadlock


def _semeia(uid):
    db.add_launch_and_update_balance(uid, "receita", 1000, "salario", "x")
    db.create_pocket(uid, "viagem")
    db.pocket_deposit_from_account(uid, "viagem", 100)
    db.create_investment(uid, "cdb", 0.12, "yearly")
    db.investment_deposit_from_account(uid, "cdb", 100)
    with db.get_conn() as conn, conn.cursor() as cur:  # há o que render: o accrue mexe nos lotes
        cur.execute("update investments set last_date=current_date-30, interest_frozen_at=null where user_id=%s", (uid,))
        cur.execute("update investment_lots set last_date=current_date-30 where user_id=%s", (uid,))
        cur.execute("update pockets set interest_frozen_at=null, interest_enabled=true, "
                    "last_interest_date=current_date-30 where user_id=%s", (uid,))
        cur.execute("update pocket_lots set last_date=current_date-30 where user_id=%s", (uid,))
        conn.commit()


def _delete_ja_nos_lotes_ou_pais(q):  # a 1ª tabela de pai/lote que o laço apaga, qualquer que seja a ordem
    return any(f"delete from {t} " in q for t in ("pocket_lots", "pockets", "investment_lots", "investments"))


def _pai_travado(tabela):
    return lambda q: f"from {tabela}" in q and "for update" in q


def _mutex(q):
    return "from accounts" in q and "for update" in q


# nome -> (operação do outro lado, statement em que ela já tem o 1º lock)
OUTROS = {
    "accrue_pockets": (lambda u: pockets.accrue_all_pockets(u), _pai_travado("pockets")),
    "accrue_investments": (lambda u: investments.accrue_all_investments(u), _pai_travado("investments")),
    "pocket_deposit": (lambda u: db.pocket_deposit_from_account(u, "viagem", 10), _mutex),
    "inv_deposit": (lambda u: db.investment_deposit_from_account(u, "cdb", 10), _mutex),
}


@pytest.mark.parametrize("delete_primeiro", [True, False], ids=["delete_primeiro", "outro_primeiro"])
@pytest.mark.parametrize("outro", list(OUTROS))
def test_exclusao_de_conta_e_outras_operacoes_nao_dao_deadlock(user_id, monkeypatch, outro, delete_primeiro):
    _semeia(user_id)
    op, quando_outro = OUTROS[outro]
    exclui = lambda: privacy.delete_user_data(user_id)
    faz = lambda: op(user_id)
    if delete_primeiro:
        pausa = PausaSql(monkeypatch, "a", _delete_ja_nos_lotes_ou_pais)
        r_del, r_outro = pausa.roda(exclui, faz)
    else:
        pausa = PausaSql(monkeypatch, "a", quando_outro)
        r_outro, r_del = pausa.roda(faz, exclui)
    sem_deadlock(r_del, r_outro)
    assert pausa.casou
    assert isinstance(r_del, dict) and r_del.get("user_id") == user_id, r_del  # a exclusão sempre conclui
    leftovers = {}
    with db.get_conn() as conn, conn.cursor() as cur:
        for t in ("launches", "pockets", "pocket_lots", "investments", "investment_lots", "accounts"):
            cur.execute(f"select count(*) as n from {t} where user_id=%s", (user_id,))
            if cur.fetchone()["n"]:
                leftovers[t] = True
        conn.commit()
    assert leftovers == {}, leftovers


# O conjunto de tabelas que a exclusão varre NÃO muda (LGPD): só a ordem. Cópia da lista de
# `delete_user_data` antes deste PR — mudança legítima da lista atualiza esta linha de propósito.
_TABELAS_VARRIDAS = frozenset("""accounts affiliates ai_messages ai_pending_actions auth_accounts auth_refresh_tokens
    auth_sessions bill_instances category_budgets credit_cards daily_report_prefs dashboard_sessions data_export_tokens
    investment_lots investments launches link_codes of_cash_coverage of_cash_links ofx_imports password_reset_tokens
    patrimonio_fotos pending_actions platform_onboarding_tokens pocket_lots pockets recurring_charges recurring_expenses
    recurring_incomes user_categories user_category_feedback user_category_rules user_category_triggers
    user_identities user_mfa user_mfa_backup_codes user_trigger_candidates""".split())


def test_exclusao_varre_o_mesmo_conjunto_de_tabelas_e_o_pai_antes_do_lote(user_id, monkeypatch):
    """POSITIVO da LGPD: mesmas tabelas, sobra nenhuma, e pai antes de lote na ordem do laço."""
    import re

    import psycopg
    _semeia(user_id)
    vistos: list[str] = []
    original = psycopg.Cursor.execute

    def grava(cur, query, *a, **k):
        m = re.match(r"delete from ([a-z_]+) where user_id = %s", str(query))
        if m and m.group(1) in _TABELAS_VARRIDAS:
            vistos.append(m.group(1))
        return original(cur, query, *a, **k)
    monkeypatch.setattr(psycopg.Cursor, "execute", grava)

    with db.get_conn() as conn, conn.cursor() as cur:  # as que existem com `user_id` (o laço pula as outras)
        cur.execute("select table_name from information_schema.columns where column_name='user_id' "
                    "and table_schema = current_schema() and table_name = any(%s)", (sorted(_TABELAS_VARRIDAS),))
        esperadas = {r["table_name"] for r in cur.fetchall()}
        conn.commit()
    assert {"pockets", "pocket_lots", "investments", "investment_lots", "accounts", "launches"} <= esperadas

    privacy.delete_user_data(user_id)

    assert set(vistos) == esperadas, (esperadas - set(vistos), set(vistos) - esperadas)
    assert vistos.index("pockets") < vistos.index("pocket_lots")
    assert vistos.index("investments") < vistos.index("investment_lots")
    assert vistos.index("accounts") < vistos.index("launches")  # a ordem que o T20 prende
