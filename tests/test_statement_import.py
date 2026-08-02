# tests/test_statement_import.py
"""
Testes da importação de extrato bancário em CSV e PDF (statement_import.py).

Os testes de parse são puros (sem DB). O teste de importação usa o fixture
user_id do conftest e valida idempotência/saldo no Postgres.
"""
import io
from datetime import date
from decimal import Decimal

import pytest

from statement_import import (
    StatementParseError,
    build_statement_external_id,
    parse_br_amount,
    parse_br_date,
    parse_csv_statement,
    parse_pdf_statement,
)


# ─────────────────────────────────────────────────────────────────────────────
# Primitivas
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_br_amount_formats():
    assert parse_br_amount("1.234,56") == Decimal("1234.56")
    assert parse_br_amount("-1234,56") == Decimal("-1234.56")
    assert parse_br_amount("R$ 1.234,56") == Decimal("1234.56")
    assert parse_br_amount("(1.234,56)") == Decimal("-1234.56")
    assert parse_br_amount("1.234,56-") == Decimal("-1234.56")
    assert parse_br_amount("1.234,56D") == Decimal("-1234.56")
    assert parse_br_amount("1.234,56 C") == Decimal("1234.56")
    # decimal com ponto (CSV do Nubank)
    assert parse_br_amount("-40.00") == Decimal("-40.00")
    assert parse_br_amount("1234.56") == Decimal("1234.56")
    # "1.234" sem decimais é milhar
    assert parse_br_amount("1.234") == Decimal("1234")
    assert parse_br_amount("") is None
    assert parse_br_amount("abc") is None


def test_parse_br_date_formats():
    assert parse_br_date("02/08/2026") == date(2026, 8, 2)
    assert parse_br_date("02/08/26") == date(2026, 8, 2)
    assert parse_br_date("2026-08-02") == date(2026, 8, 2)
    assert parse_br_date("02/08/2026 04:58") == date(2026, 8, 2)
    assert parse_br_date("SALDO ANTERIOR") is None


def test_external_id_stable_and_occurrence_aware():
    a = build_statement_external_id(date(2026, 8, 2), Decimal("-50"), "uber", 0)
    b = build_statement_external_id(date(2026, 8, 2), Decimal("-50"), "uber", 0)
    c = build_statement_external_id(date(2026, 8, 2), Decimal("-50"), "uber", 1)
    assert a == b
    assert a != c
    assert a.startswith("stmt:")


# ─────────────────────────────────────────────────────────────────────────────
# CSV
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_csv_nubank_conta():
    csv_bytes = (
        "Data,Valor,Identificador,Descrição\n"
        "01/08/2026,-40.00,abc-123,Compra no débito - UBER\n"
        "02/08/2026,1500.00,def-456,Transferência recebida - JOÃO\n"
    ).encode("utf-8")
    txs = parse_csv_statement(csv_bytes)
    assert len(txs) == 2
    assert txs[0]["posted_at"] == date(2026, 8, 1)
    assert txs[0]["amount"] == Decimal("-40.00")
    assert "UBER" in txs[0]["memo"]
    assert txs[0]["external_id"] == "abc-123"
    assert txs[1]["amount"] == Decimal("1500.00")


def test_parse_csv_inter_semicolon_com_preambulo_e_saldo():
    csv_bytes = (
        "Extrato Conta Corrente\n"
        "Conta;12345-6\n"
        "\n"
        "Data Lançamento;Descrição;Valor;Saldo\n"
        "01/08/2026;PIX RECEBIDO;R$ 1.000,00;R$ 1.000,00\n"
        "02/08/2026;COMPRA MERCADO;-R$ 234,56;R$ 765,44\n"
    ).encode("utf-8")
    txs = parse_csv_statement(csv_bytes)
    assert len(txs) == 2
    assert txs[0]["amount"] == Decimal("1000.00")
    assert txs[1]["amount"] == Decimal("-234.56")
    assert txs[1]["memo"] == "COMPRA MERCADO"


def test_parse_csv_coluna_tipo_define_sinal():
    csv_bytes = (
        "Data;Histórico;Tipo;Valor\n"
        "01/08/2026;PAGAMENTO BOLETO;D;150,00\n"
        "02/08/2026;DEPÓSITO;C;300,00\n"
    ).encode("latin-1")
    txs = parse_csv_statement(csv_bytes)
    assert txs[0]["amount"] == Decimal("-150.00")
    assert txs[1]["amount"] == Decimal("300.00")


def test_parse_csv_colunas_credito_debito():
    csv_bytes = (
        "Data;Descrição;Crédito;Débito\n"
        "01/08/2026;SALÁRIO;5.000,00;\n"
        "02/08/2026;ALUGUEL;;1.800,00\n"
    ).encode("utf-8")
    txs = parse_csv_statement(csv_bytes)
    assert txs[0]["amount"] == Decimal("5000.00")
    assert txs[1]["amount"] == Decimal("-1800.00")


def test_parse_csv_ignora_linhas_sem_data_ou_valor():
    csv_bytes = (
        "Data,Descrição,Valor\n"
        "SALDO ANTERIOR,,\n"
        "01/08/2026,UBER,-25.90\n"
        "01/08/2026,SALDO DO DIA,\n"
        ",,\n"
    ).encode("utf-8")
    txs = parse_csv_statement(csv_bytes)
    assert len(txs) == 1
    assert txs[0]["memo"] == "UBER"


def test_parse_csv_fatura_nubank_rejeitada():
    csv_bytes = (
        "date,title,amount\n"
        "2026-08-01,Ifood,45.90\n"
    ).encode("utf-8")
    with pytest.raises(StatementParseError, match="fatura de cartão"):
        parse_csv_statement(csv_bytes)


def test_parse_csv_sem_colunas_reconheciveis():
    csv_bytes = "foo,bar\n1,2\n".encode("utf-8")
    with pytest.raises(StatementParseError, match="colunas"):
        parse_csv_statement(csv_bytes)


# ─────────────────────────────────────────────────────────────────────────────
# PDF
# ─────────────────────────────────────────────────────────────────────────────

def _make_pdf(lines: list[str]) -> bytes:
    """Gera um PDF de uma página com uma linha de texto por linha lógica."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in lines:
        c.drawString(40, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


def test_parse_pdf_sicoob_like():
    pdf = _make_pdf([
        "SICOOB - SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL",
        "EXTRATO CONTA CORRENTE",
        "PERÍODO: 01/08/2026 - 02/08/2026",
        "DATA HISTÓRICO VALOR",
        "01/08/2026 PIX RECEBIDO - JOÃO DA SILVA 1.500,00C",
        "01/08/2026 SALDO DO DIA 1.500,00C",
        "02/08/2026 DÉBITO PIX SUPERMERCADO BOM PREÇO 234,56D",
    ])
    txs = parse_pdf_statement(pdf)
    assert len(txs) == 2
    assert txs[0]["posted_at"] == date(2026, 8, 1)
    assert txs[0]["amount"] == Decimal("1500.00")
    assert "PIX RECEBIDO" in txs[0]["memo"]
    assert txs[1]["amount"] == Decimal("-234.56")


def test_parse_pdf_valor_e_saldo_na_mesma_linha():
    # layout tipo Itaú/BB: data, histórico, valor da movimentação, saldo
    pdf = _make_pdf([
        "Extrato de conta corrente",
        "01/08/2026 TED RECEBIDA EMPRESA X 2.000,00 2.000,00",
        "02/08/2026 PAGAMENTO BOLETO -150,00 1.850,00",
    ])
    txs = parse_pdf_statement(pdf)
    assert len(txs) == 2
    assert txs[0]["amount"] == Decimal("2000.00")
    assert txs[1]["amount"] == Decimal("-150.00")


def test_parse_pdf_sufixo_c_nao_engole_palavra():
    pdf = _make_pdf([
        "01/08/2026 50,00 COMPRA CARTAO PADARIA",
    ])
    txs = parse_pdf_statement(pdf)
    assert len(txs) == 1
    # "C" de COMPRA não pode virar sufixo de crédito nem sumir da descrição
    assert txs[0]["amount"] == Decimal("50.00")
    assert "COMPRA" in txs[0]["memo"]


def test_parse_pdf_sem_transacoes():
    pdf = _make_pdf(["Documento sem movimentações financeiras"])
    assert parse_pdf_statement(pdf) == []


# ─────────────────────────────────────────────────────────────────────────────
# Detecção de anexo no handle_incoming
# ─────────────────────────────────────────────────────────────────────────────

def test_attachment_detection():
    from core.handle_incoming import _is_csv_attachment, _is_pdf_attachment
    from core.types import Attachment

    assert _is_csv_attachment(Attachment(filename="extrato.CSV"))
    assert _is_csv_attachment(Attachment(filename="x", content_type="text/csv; charset=utf-8"))
    assert not _is_csv_attachment(Attachment(filename="extrato.ofx"))

    assert _is_pdf_attachment(Attachment(filename="Extrato.pdf"))
    assert _is_pdf_attachment(Attachment(filename="doc", content_type="application/pdf"))
    assert not _is_pdf_attachment(Attachment(filename="foto.jpeg", content_type="image/jpeg"))


# ─────────────────────────────────────────────────────────────────────────────
# Import completo (Postgres) — idempotência e saldo
# ─────────────────────────────────────────────────────────────────────────────

def test_import_statement_bytes_csv_idempotente(user_id):
    from statement_import import import_statement_bytes
    from db import get_balance

    csv_bytes = (
        "Data,Descrição,Valor\n"
        "01/08/2026,SALÁRIO EMPRESA,\"5.000,00\"\n"
        "02/08/2026,MERCADO PAGUE MENOS,\"-350,00\"\n"
        "02/08/2026,MERCADO PAGUE MENOS,\"-350,00\"\n"
    ).encode("utf-8")

    saldo_antes = get_balance(user_id)

    rep = import_statement_bytes(user_id, csv_bytes, "extrato.csv", "csv")
    assert rep["total"] == 3
    assert rep["inserted"] == 3  # compras idênticas no mesmo dia são preservadas
    assert rep["duplicates"] == 0
    assert rep["source"] == "csv"
    assert rep["reconciled"] is False
    assert rep["dt_start"] == date(2026, 8, 1)
    assert rep["dt_end"] == date(2026, 8, 2)
    assert Decimal(rep["new_balance"]) == Decimal(saldo_antes) + Decimal("4300.00")

    # mesmo arquivo de novo → curto-circuito pelo file_hash
    rep2 = import_statement_bytes(user_id, csv_bytes, "extrato.csv", "csv")
    assert rep2["skipped_same_file"] is True

    # mesmo período re-exportado (bytes diferentes, mesmas transações +1 nova)
    csv_bytes3 = (
        "Data,Descrição,Valor\n"
        "01/08/2026,SALÁRIO EMPRESA,\"5.000,00\"\n"
        "02/08/2026,MERCADO PAGUE MENOS,\"-350,00\"\n"
        "02/08/2026,MERCADO PAGUE MENOS,\"-350,00\"\n"
        "03/08/2026,UBER,\"-25,00\"\n"
    ).encode("utf-8")
    rep3 = import_statement_bytes(user_id, csv_bytes3, "extrato2.csv", "csv")
    assert rep3["inserted"] == 1
    assert rep3["duplicates"] == 3
    assert Decimal(rep3["new_balance"]) == Decimal(saldo_antes) + Decimal("4275.00")


def test_import_statement_bytes_vazio_ou_grande(user_id):
    from statement_import import import_statement_bytes, MAX_STATEMENT_BYTES

    with pytest.raises(StatementParseError):
        import_statement_bytes(user_id, b"", "x.csv", "csv")

    grande = b"0" * (MAX_STATEMENT_BYTES + 1)
    with pytest.raises(StatementParseError, match="grande"):
        import_statement_bytes(user_id, grande, "x.csv", "csv")
