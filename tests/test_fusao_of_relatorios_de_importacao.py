"""O `new_balance` do relatório de importação: UM produtor, QUATRO telas.

`db/accounts.py:import_ofx_launches_bulk` alimenta `ofx_service.py:57`
("💰 Saldo final"), `statement_service.py:74` ("💰 Saldo atual") e
`handle_incoming.py:774` ("🏦 Saldo atual") — por isso o conserto é na FONTE e
nenhum dos três formatadores tem uma linha de diff.

Dois ramos sobrescreviam o conserto depois dele, um em cada arquivo de
importação: `ofx_import.py:266` e `statement_import.py:707`, os dois chamando
`set_balance(user_id, ledger_balance)` e devolvendo o CRU. No extrato agrava: a
linha "✅ Saldo conferido com o saldo final informado no extrato" afirmaria
conferência contra um número que a tela contradiz no mesmo instante.

Controle negativo: zerar `MERGED_WALLET_DELTA_SQL` deixa os três vermelhos.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

import db
from utils_text import fmt_brl as _brl
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, ia_fora, manda, of_tx_pendente, saldo_bruto,
    sincroniza, tx, uid_pro,
)


def _funde_cinquenta(uid: int) -> int:
    """Carteira exibida 100,00 = cru 50,00 + os 50,00 do gasto fundido (com a
    confirmação do usuário — lançamento manual nunca funde em silêncio)."""
    hoje = today_tz()
    conexao = conecta_banco(uid, "1000.00")
    db.add_launch_and_update_balance(uid, "receita", 100, None, "seed")
    manda(uid, "gastei 50 no mercado")
    sincroniza(conexao, uid, "950.00", [tx(uid, "-50.00", hoje, "MERCADO")])
    rep = db.import_open_finance_launches(uid, conexao)
    assert rep["pending"] == 1 and rep["auto_merged"] == 0, rep
    db.confirm_reconciliation(uid, of_tx_pendente(uid))
    assert saldo_bruto(uid) == Decimal("50"), "a correção é de LEITURA"
    return conexao


def test_relatorio_ofx_traz_a_carteira_exibida(uid_pro, ia_fora):
    """`db/accounts.py:import_ofx_launches_bulk` alimenta TRÊS telas com o mesmo
    `new_balance`: `core/services/ofx_service.py:57` ("💰 Saldo final"),
    `core/services/statement_service.py:74` ("💰 Saldo atual") e
    `core/handle_incoming.py:774`. Um produtor, três superfícies — por isso o
    conserto é na fonte e nenhum dos três formatadores mudou.

    Chega no produtor sem passar por `ofxparse`: a montagem das linhas é do
    `ofx_import.py`, e o que este teste mede é o número do relatório.
    """
    from datetime import datetime
    from db.accounts import import_ofx_launches_bulk

    _funde_cinquenta(uid_pro)
    hoje = today_tz()

    rep = import_ofx_launches_bulk(
        uid_pro,
        [{"tipo": "despesa", "valor": Decimal("20"), "delta": Decimal("-20"),
          "criado_em": datetime.combine(hoje, datetime.min.time()),
          "external_id": f"ofx-{uid_pro}-1", "nota": "SAQUE"}],
        file_hash=f"hash-{uid_pro}", bank_id="001", acct_id="123",
        acct_type="CHECKING", dt_start=hoje, dt_end=hoje,
    )

    assert rep["inserted"] == 1, rep
    # cru: 50 - 20 = 30. Exibido: 80 (os 50 fundidos voltam).
    assert float(rep["new_balance"]) == pytest.approx(80.0), \
        "sem o conserto: 30,00, com o dashboard mostrando 80,00"
    assert float(rep["new_balance"]) == pytest.approx(
        float(db.get_consolidated_balance(uid_pro)["manual"]))
    assert saldo_bruto(uid_pro) == Decimal("30"), "nada foi escrito a mais"


def test_relatorio_ofx_do_arquivo_repetido_tambem(uid_pro, ia_fora):
    """O ramo `skipped_same_file` tem a SUA própria leitura da Carteira."""
    from datetime import datetime
    from db.accounts import import_ofx_launches_bulk

    _funde_cinquenta(uid_pro)
    hoje = today_tz()
    linhas = [{"tipo": "despesa", "valor": Decimal("20"), "delta": Decimal("-20"),
               "criado_em": datetime.combine(hoje, datetime.min.time()),
               "external_id": f"ofx-{uid_pro}-1", "nota": "SAQUE"}]
    kw = dict(file_hash=f"hash-{uid_pro}", bank_id="001", acct_id="123",
              acct_type="CHECKING", dt_start=hoje, dt_end=hoje)

    import_ofx_launches_bulk(uid_pro, linhas, **kw)
    rep = import_ofx_launches_bulk(uid_pro, linhas, **kw)   # o MESMO arquivo

    assert rep["skipped_same_file"] is True, rep
    assert float(rep["new_balance"]) == pytest.approx(80.0), "sem o conserto: 30,00"


def test_extrato_reconciliado_traz_a_carteira_exibida(uid_pro, ia_fora, monkeypatch):
    """`statement_import.py:707` — `set_balance` sobrescrevia o `new_balance`
    que `import_ofx_launches_bulk` tinha corrigido, e a linha "✅ Saldo
    conferido" afirmava conferência contra um número que a tela contradiz.

    Parser de PDF mockado; a reconciliação, o `set_balance` e o formatador
    rodam de verdade.
    """
    from datetime import date as _date
    import statement_import as si
    from core.services import statement_service

    _funde_cinquenta(uid_pro)
    monkeypatch.setattr(si, "_extract_pdf_text", lambda _d: "extrato falso")
    monkeypatch.setattr(si, "parse_pdf_statement_text", lambda _t: [
        {"posted_at": _date.today(), "amount": Decimal("-20"), "memo": "SAQUE"}])
    monkeypatch.setattr(si, "extract_pdf_final_balance", lambda _t: Decimal("777.00"))

    rep = si.import_statement_bytes(uid_pro, b"%PDF-falso", "extrato.pdf", "pdf")

    assert rep["reconciled"] is True, rep
    # `set_balance` zerou a Carteira em 777,00 (cru); exibida = 777 + 50 fundidos
    assert saldo_bruto(uid_pro) == Decimal("777.00")
    assert float(rep["new_balance"]) == pytest.approx(827.0), "sem o conserto: 777,00"
    assert float(rep["new_balance"]) == pytest.approx(
        float(db.get_consolidated_balance(uid_pro)["manual"]))
    assert _brl(827.0) in statement_service.format_statement_report(rep)
