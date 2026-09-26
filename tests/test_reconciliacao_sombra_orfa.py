"""Sombra órfã: `undo_reconciliation` cria uma sombra nova (`_insert_of_shadow`)
bem na janela em que `delete_open_finance_transactions`/`disconnect_open_finance_connection`
já leram os dados velhos sem trava. Sem reler dentro da transação final que já
segura `_lock_user`, a sombra nova fica sem nenhuma `open_finance_transactions`
apontando para ela — conta como gasto pra sempre (CLAUDE.md §0, isolamento por
usuário: toda consulta abaixo filtra por user_id).
"""
from __future__ import annotations

import uuid

import db
import db.bank_movements as bm_mod
import db.open_finance as of_mod
from db.reconciliation import ReconciliationConflict

from tests._fusao_of_helpers import (  # noqa: F401 (ia_fora/uid_pro são fixtures)
    conecta_banco, ia_fora, manda, saldo_bruto, sincroniza, soma_delta_conta, tx, uid_pro,
)
from tests.conftest import promote_to_pro
from tests.test_reconciliacao_concorrencia import _corre
from tests.test_reconciliacao_desfazer import _of_tx, _sombras, funde_a
from tests.test_reconciliacao_resolver import _estado, _gasto_do_mes, _q
from utils_date import today_tz


def _novo_uid() -> int:
    """Mesma receita da fixture `uid_pro`, chamável em loop (uma por rodada)."""
    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    return promote_to_pro(uid)


def _existe(launch_id, uid) -> bool:
    return bool(_q("select 1 from launches where id=%s and user_id=%s", (launch_id, uid)))


def _sombras_orfas(uid) -> int:
    """Sombra OF (source='open_finance', delta_conta=0) sem NENHUMA
    open_finance_transactions apontando pra ela via imported_launch_id."""
    return _q(
        """select count(*) as n from launches l
             where l.user_id=%s and l.source='open_finance'
               and (l.efeitos->>'delta_conta')::numeric = 0
               and not exists (
                   select 1 from open_finance_transactions t where t.imported_launch_id = l.id)""",
        (uid,),
    )[0]["n"]


def _na_janela_do_rollback(monkeypatch, escrita):
    """Injeta `escrita` (a corrida) exatamente no ponto onde o plano documentou a
    janela: entre a leitura sem trava de `delete_open_finance_transactions`/
    `disconnect_open_finance_connection` e a transação final com `_lock_user`."""
    original = of_mod._rollback_imported_of

    def _wrapper(rows):
        escrita()
        return original(rows)

    monkeypatch.setattr(of_mod, "_rollback_imported_of", _wrapper)


def test_desfazer_na_janela_do_delete_do_provedor(uid_pro, ia_fora, monkeypatch):
    conexao = funde_a(uid_pro)
    of_tx = _of_tx(uid_pro)
    manual = _estado(of_tx)["imported_launch_id"]
    # NO CONTRATO NOVO a fusão do `funde_a` é a CONFIRMADA (o 'auto' em
    # candidato manual virou 'ask' e o teste confirma) — mesma classe de
    # estado fundido que o 'auto_merged' de antes.
    assert _estado(of_tx)["reconciliation_status"] == "confirmed"

    _na_janela_do_rollback(
        monkeypatch, lambda: db.undo_reconciliation(uid_pro, of_tx))

    deleted = db.delete_open_finance_transactions(f"item-of-{uid_pro}", [f"of-tx-{uid_pro}-1"])

    assert deleted == 1
    assert _sombras(uid_pro) == 0, "sombra criada pelo undo concorrente ficou órfã"
    assert _gasto_do_mes(uid_pro) == 50.0, "sem o conserto, a sombra órfã dobra o gasto"
    assert _existe(manual, uid_pro), "X manual não pode sumir"


def test_desfazer_na_janela_do_disconnect(uid_pro, ia_fora, monkeypatch):
    conexao = funde_a(uid_pro)
    of_tx = _of_tx(uid_pro)
    manual = _estado(of_tx)["imported_launch_id"]

    _na_janela_do_rollback(
        monkeypatch, lambda: db.undo_reconciliation(uid_pro, of_tx))

    deleted = db.disconnect_open_finance_connection(uid_pro, conexao)

    assert deleted == 1
    assert _sombras(uid_pro) == 0, "sombra criada pelo undo concorrente ficou órfã"
    assert _gasto_do_mes(uid_pro) == 50.0
    assert _existe(manual, uid_pro)


def test_import_na_janela_do_delete_do_provedor(uid_pro, ia_fora, monkeypatch):
    """Transação ainda não importada: o import (não o undo) cria a sombra nova
    na mesma janela. Vermelho em origin/main (achado do plano) — a classe já
    existe lá, o PR 1 só abriu mais uma porta pra ela (o undo)."""
    conexao = conecta_banco(uid_pro, "1000.00")
    sincroniza(conexao, uid_pro, "950.00", [tx(uid_pro, "-50.00", today_tz(), "MERCADO")])

    def _importa_concorrente():
        rep = db.import_open_finance_launches(uid_pro, conexao)
        assert rep["inserted"] == 1, rep

    _na_janela_do_rollback(monkeypatch, _importa_concorrente)

    deleted = db.delete_open_finance_transactions(f"item-of-{uid_pro}", [f"of-tx-{uid_pro}-1"])

    assert deleted == 1
    assert _sombras(uid_pro) == 0, "sombra criada pelo import concorrente ficou órfã"


def test_delete_preserva_manual_e_sombra_alheia(uid_pro, ia_fora):
    """Positivo: apagar só T1 (fundida com X manual) não mexe em T2 (sombra de
    outra transação, sem relação nenhuma com T1). NO CONTRATO NOVO o casamento
    de T1 com o manual vira pendência e o usuário confirma — o estado fundido
    é o mesmo do antigo auto-merge."""
    conexao = conecta_banco(uid_pro, "1000.00")
    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    sincroniza(conexao, uid_pro, "850.00", [
        tx(uid_pro, "-50.00", today_tz(), "MERCADO", ident="1"),
        tx(uid_pro, "-100.00", today_tz(), "OUTRO", ident="2"),
    ])
    rep = db.import_open_finance_launches(uid_pro, conexao)
    assert (rep["auto_merged"], rep["pending"], rep["inserted"]) == (0, 1, 2), rep

    t1_pre = _q("select o.id from open_finance_transactions o "
                "where o.provider_transaction_id=%s", (f"of-tx-{uid_pro}-1",))[0]
    db.confirm_reconciliation(uid_pro, t1_pre["id"])

    t1 = _q("select o.id, o.imported_launch_id from open_finance_transactions o "
            "where o.provider_transaction_id=%s", (f"of-tx-{uid_pro}-1",))[0]
    t2 = _q("select o.id, o.imported_launch_id from open_finance_transactions o "
            "where o.provider_transaction_id=%s", (f"of-tx-{uid_pro}-2",))[0]
    manual, sombra2 = t1["imported_launch_id"], t2["imported_launch_id"]

    deleted = db.delete_open_finance_transactions(f"item-of-{uid_pro}", [f"of-tx-{uid_pro}-1"])

    assert deleted == 1
    assert _existe(manual, uid_pro), "X de T1 foi apagado por engano"
    assert _existe(sombra2, uid_pro), "sombra de T2 foi apagada por engano"
    assert not _q("select 1 from open_finance_transactions where id=%s", (t1["id"],))
    assert _q("select 1 from open_finance_transactions where id=%s", (t2["id"],))


def test_positivo_guarda_do_is_of_shadow(uid_pro, ia_fora, monkeypatch):
    """Controle positivo do teste anterior: sem o filtro `is_of_shadow`, o
    helper `delete_if_shadow` apagaria o X manual por engano. Prova que a
    guarda em `db/bank_movements.py` é o que preserva o manual."""
    conexao = funde_a(uid_pro)
    of_tx = _of_tx(uid_pro)
    manual = _estado(of_tx)["imported_launch_id"]

    monkeypatch.setattr(bm_mod, "is_of_shadow", lambda *a, **k: True)

    db.delete_open_finance_transactions(f"item-of-{uid_pro}", [f"of-tx-{uid_pro}-1"])

    assert not _existe(manual, uid_pro), "mutação não pegou: X devia ter sido apagado"


def _rodadas(uid_maker, montar, correr, rodadas=12) -> int:
    """Roda `rodadas` corridas independentes (uid novo a cada rodada, via
    Barrier de 2 fios) e devolve quantas ficaram com sombra órfã — a coluna
    do relato (CLAUDE.md §3: DUAS colunas, com e sem o conserto)."""
    orfas = 0
    for _ in range(rodadas):
        uid = uid_maker()
        contexto = montar(uid)
        saida = correr(uid, contexto)
        for nome, r in saida.items():
            if isinstance(r, Exception):
                assert isinstance(r, (LookupError, ReconciliationConflict)), f"{nome}: {r!r}"
        assert saldo_bruto(uid) == soma_delta_conta(uid), saida
        if _sombras_orfas(uid):
            orfas += 1
    return orfas


def test_barrier_desfazer_x_delete(ia_fora):
    def correr(uid, conexao):
        of_tx = _of_tx(uid)
        return _corre(
            undo=lambda: db.undo_reconciliation(uid, of_tx),
            delete=lambda: db.delete_open_finance_transactions(
                f"item-of-{uid}", [f"of-tx-{uid}-1"]),
        )

    orfas = _rodadas(_novo_uid, funde_a, correr)
    assert orfas == 0, f"{orfas}/12 rodadas com sombra órfã (undo × delete do provedor)"


def test_barrier_desfazer_x_disconnect(ia_fora):
    def correr(uid, conexao):
        of_tx = _of_tx(uid)
        return _corre(
            undo=lambda: db.undo_reconciliation(uid, of_tx),
            disconnect=lambda: db.disconnect_open_finance_connection(uid, conexao),
        )

    orfas = _rodadas(_novo_uid, funde_a, correr)
    assert orfas == 0, f"{orfas}/12 rodadas com sombra órfã (undo × disconnect)"
