"""#1497: a fila da conferência na Stripe não trava as comissões mais novas atrás das que nunca confirmam.

Rota real e Postgres real; Stripe falsa pela `fonte` (`test_afiliado_conferencia`). O relógio é a própria
coluna: o teste grava `payment_check_tried_at = now() - interval ...`, porque a espera é medida pelo `now()`
do banco. Os controles negativos e positivos estão no relato do PR; os positivos ficam aqui, nos casos que
dizem "positivo".
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest

import core.services.afiliado_reembolso as servico
import db
import db.affiliate_reviews_manutencao as manutencao
from db import schema
from db.affiliates import request_payout
from db.connection import get_conn
from test_afiliado_area import _afiliado, _comissao, _vence  # noqa: F401
from test_afiliado_conferencia import _invoice_paid, _nova, fonte  # noqa: F401
from test_afiliado_exclusao import _saque_com_pix
from test_afiliado_pagantes import _admin
from test_afiliado_pendencia import _quadros, _rev
from test_afiliado_reembolso import _logs, mundo  # noqa: F401


@pytest.fixture(autouse=True)
def _fila_so_deste_teste(fonte):
    """As marcas de outros testes entrariam na fila e mudariam a ordem."""
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set payment_check_pending = false where payment_check_pending")
        conn.commit()


def _marcada(c, reais=100):
    com = _comissao(c, reais)
    manutencao.marcar_conferencia([com.id])
    return com


def _presa(fonte, monkeypatch, com, forma):
    """Uma fatura que nunca confirma: disputa aberta, pagamento por `payment_record` ou sem cobrança."""
    if forma == "disputa_aberta":
        fonte.cobra(com.fatura, disputed=True)
        fonte.disputas[f"pi_{com.fatura}"] = [{"status": "needs_response", "amount": 100_000}]
    elif forma == "forma_inesperada":
        fonte.payment_record.add(com.fatura)
    # sem_cobranca: a fatura não tem cobrança paga na `fonte`


@pytest.fixture
def formas(fonte, monkeypatch):
    """`payment_record` por fatura, por cima do `InvoicePayment.list` da `fonte`."""
    import stripe

    fonte.payment_record = set()
    real = stripe.InvoicePayment.list

    def listar(**k):
        if k.get("invoice") in fonte.payment_record:
            fonte.consultas += 1
            return {"data": [{"status": "paid", "payment": {"type": "payment_record", "payment_intent": None}}]}
        return real(**k)

    monkeypatch.setattr(stripe.InvoicePayment, "list", staticmethod(listar))
    return fonte


def _linha(com):
    with get_conn() as conn:
        return conn.execute("select payment_check_pending, payment_check_tried_at, payment_check_reason"
                            " from affiliate_commissions where id = %s", (com.id,)).fetchone()


def _tentada_ha(coms, intervalo):
    with get_conn() as conn:
        conn.execute("update affiliate_commissions set payment_check_tried_at = now() - %s::interval"
                     " where id = any(%s)", (intervalo, [c.id for c in coms]))
        conn.commit()


@pytest.fixture
def espiao(monkeypatch):
    """As comissões que o worker consultou, na ordem."""
    vistas, real = [], servico.conferir_comissao

    def conferir(commission_id, fatura, **k):
        vistas.append(commission_id)
        return real(commission_id, fatura, **k)

    monkeypatch.setattr(servico, "conferir_comissao", conferir)
    return vistas


# ── O cenário da triagem ─────────────────────────────────────────────────────

@pytest.mark.parametrize("forma", ["disputa_aberta", "forma_inesperada", "sem_cobranca"])
def test_presas_nao_seguram_a_seguinte(formas, monkeypatch, forma):
    """20 que nunca confirmam na frente e a 21ª válida: ela sai na 2ª volta. Hoje (`order by id`), nunca."""
    c = _afiliado()
    presas = [_marcada(c) for _ in range(20)]
    for com in presas:
        _presa(formas, monkeypatch, com, forma)
    valida = _marcada(c)
    formas.cobra(valida.fatura)
    servico.conferir_pendentes()
    servico.conferir_pendentes()
    with get_conn() as conn:  # só a marca, que existe antes da migração: o vermelho de hoje é o do comportamento
        assert conn.execute("select payment_check_pending from affiliate_commissions where id = %s",
                            (valida.id,)).fetchone()["payment_check_pending"] is False
    linhas = [_linha(com) for com in presas]
    assert {(x["payment_check_pending"], x["payment_check_reason"]) for x in linhas} == {(True, forma)}
    assert all(x["payment_check_tried_at"] for x in linhas)
    assert _quadros(c)["available"] == 100  # as presas seguem fora do disponível
    assert request_payout(c.aff["id"])["amount_cents"] == 10_000


def test_espera_de_uma_hora(fonte, espiao):
    c = _afiliado()
    presa = _marcada(c)  # sem cobrança: nunca confirma
    _tentada_ha([presa], "59 minutes")
    servico.conferir_pendentes()
    assert espiao == []
    _tentada_ha([presa], "61 minutes")
    servico.conferir_pendentes()
    assert espiao == [presa.id]
    assert _linha(presa)["payment_check_reason"] == "sem_cobranca"


def test_marcada_nova_nao_espera(fonte):
    """40 presas já vencidas na espera (2 h) e uma marcada agora: a nova vai na frente, nunca tentada."""
    c = _afiliado()
    presas = [_marcada(c) for _ in range(40)]
    _tentada_ha(presas, "2 hours")
    nova = _marcada(c)
    fonte.cobra(nova.fatura)
    servico.conferir_pendentes()
    assert _linha(nova)["payment_check_pending"] is False


def test_queda_da_stripe_nao_grava_nem_perde_a_ordem(fonte, espiao):
    import stripe

    c = _afiliado()
    validas = [_marcada(c) for _ in range(3)]
    for com in validas:
        fonte.cobra(com.fatura)
    fonte.falha = stripe.APIConnectionError("fora")
    for _ in range(3):
        assert servico.conferir_pendentes() == 0
    assert espiao == [validas[0].id] * 3  # a queda para a passada na 1ª
    assert [(_linha(x)["payment_check_pending"], _linha(x)["payment_check_tried_at"]) for x in validas] == [(True, None)] * 3
    fonte.falha = None
    assert servico.conferir_pendentes() == 3
    assert espiao[3] == validas[0].id  # a que falhou abre a volta seguinte


def test_conferencia_inline_ignora_a_espera(fonte, monkeypatch):
    """QR e webhook conferem na hora, mesmo com a comissão adiada há 1 min; o webhook não grava a tentativa."""
    adm = _admin(monkeypatch)
    c = _afiliado()
    com = _comissao(c, 100)
    saque = _saque_com_pix(c)
    manutencao.marcar_conferencia([com.id])
    _tentada_ha([com], "1 minute")
    fonte.cobra(com.fatura)
    antes = fonte.consultas
    assert adm.get(f"/admin/api/affiliates/payouts/{saque}/pix").status_code == 200
    assert fonte.consultas > antes

    outra = _afiliado()
    ind, fatura = _nova(outra)
    fonte.cobra(fatura, disputed=True)
    fonte.disputas[f"pi_{fatura}"] = [{"status": "needs_response", "amount": 100_000}]
    _invoice_paid(ind, fatura)  # adiada pelo webhook: a marca fica, mas a tentativa não é gravada
    with get_conn() as conn:
        linha = conn.execute("select payment_check_pending, payment_check_tried_at, payment_check_reason"
                             " from affiliate_commissions where stripe_invoice_id = %s", (fatura,)).fetchone()
    assert (linha["payment_check_pending"], linha["payment_check_tried_at"], linha["payment_check_reason"]) == (
        True, None, None)


def test_liberar_zera_a_tentativa(fonte):
    c = _afiliado()
    com = _marcada(c)
    manutencao.adiar_conferencia(com.id, "disputa_aberta")
    assert _linha(com)["payment_check_reason"] == "disputa_aberta"
    manutencao.liberar_conferencia(com.id)
    assert (_linha(com)["payment_check_tried_at"], _linha(com)["payment_check_reason"]) == (None, None)
    manutencao.marcar_conferencia([com.id])  # remarcada (D10): entra como nunca tentada
    fonte.cobra(com.fatura)
    servico.conferir_pendentes()
    assert _linha(com)["payment_check_pending"] is False


def test_fila_sem_presas_igual_a_hoje(fonte):
    """Positivo: 5 válidas saem numa volta, uma consulta cada, e a coluna fica nula."""
    c = _afiliado()
    validas = [_marcada(c) for _ in range(5)]
    for com in validas:
        fonte.cobra(com.fatura)
    antes = fonte.consultas
    assert servico.conferir_pendentes() == 5
    assert fonte.consultas - antes == 5
    assert {(x["payment_check_pending"], x["payment_check_tried_at"], x["payment_check_reason"])
            for x in map(_linha, validas)} == {(False, None, None)}


def test_dois_workers(fonte):
    """Sem claim, de propósito: duas instâncias na mesma volta repetem a consulta, e nada duplica."""
    c = _afiliado()
    valida, reembolsada = _marcada(c), _marcada(c)
    fonte.cobra(valida.fatura)
    fonte.cobra(reembolsada.fatura, devolvido=100_000)
    presas = [_marcada(c) for _ in range(18)]
    barreira = threading.Barrier(2)

    def worker():
        barreira.wait()
        return servico.conferir_pendentes()

    with ThreadPoolExecutor(2) as pool:
        resultados = [f.result(timeout=60) for f in [pool.submit(worker), pool.submit(worker)]]
    assert all(isinstance(r, int) for r in resultados)
    assert _linha(valida)["payment_check_pending"] is False
    assert _rev(reembolsada.fatura)["status"] in ("open", "resolved")
    with get_conn() as conn:
        n = conn.execute("select count(*) as n from affiliate_refund_reviews r join affiliate_refund_review_events e"
                         " on e.review_id = r.id where r.stripe_invoice_id = %s", (reembolsada.fatura,)).fetchone()["n"]
    assert n == 1
    assert {(x["payment_check_pending"], x["payment_check_reason"]) for x in map(_linha, presas)} == {
        (True, "sem_cobranca")}


# ── Migração ─────────────────────────────────────────────────────────────────

def _sem_as_colunas():
    with get_conn() as conn:
        conn.execute("alter table affiliate_commissions drop column payment_check_tried_at,"
                     " drop column payment_check_reason")
        conn.commit()


def test_init_db_migra_banco_sem_a_coluna(fonte):
    c = _afiliado()
    com = _comissao(c, 100)
    _sem_as_colunas()
    try:
        with get_conn() as conn:
            conn.execute("update affiliate_commissions set payment_check_pending = true where id = %s", (com.id,))
            conn.commit()
        db.init_db()
        db.init_db()
        with get_conn() as conn:
            colunas = conn.execute("select column_name, is_nullable, column_default from information_schema.columns"
                                   " where table_name = 'affiliate_commissions' and column_name like 'payment_check_%%'"
                                   " order by column_name").fetchall()
        assert [tuple(x.values()) for x in colunas] == [("payment_check_pending", "NO", "false"),
                                                        ("payment_check_reason", "YES", None),
                                                        ("payment_check_tried_at", "YES", None)]
        assert (_linha(com)["payment_check_pending"], _linha(com)["payment_check_tried_at"]) == (True, None)
        fonte.cobra(com.fatura)
        servico.conferir_pendentes()
        assert _linha(com)["payment_check_pending"] is False
    finally:
        db.init_db()


def test_migracao_com_tabela_ocupada():
    """O mesmo bloco guardado de `commission_bps`, com 3 tentativas: tabela ocupada derruba o boot com erro
    claro, sem entrar na fila de locks, e nada fica gravado."""
    bloco = next(b for b in re.findall(r'"""(\s*do \$\$ begin.*?end \$\$\s*)"""', Path(schema.__file__).read_text(), re.S)
                 if "lock table affiliates" in b)
    teto = re.search(r"for tentativa in 1\.\.(\d+) loop", bloco).group(1)
    _sem_as_colunas()
    try:
        with get_conn() as leitor, get_conn() as boot:
            leitor.execute("select 1 from affiliate_commissions limit 1")
            # Rede de proteção: sem o `nowait`, cada tentativa esperaria na fila até este prazo, e não para sempre.
            boot.execute("set lock_timeout = '1s'")
            t0 = time.monotonic()
            with pytest.raises(psycopg.errors.LockNotAvailable, match="o boot para aqui"):
                boot.execute(bloco.replace(f"1..{teto} loop", "1..3 loop").replace(f"tentativa = {teto}", "tentativa = 3"))
            assert time.monotonic() - t0 < 1, "a migração entrou na fila de locks"
            boot.rollback()
            leitor.rollback()
        with get_conn() as conn:
            colunas = conn.execute("select attname from pg_attribute where attrelid = 'affiliate_commissions'::regclass"
                                   " and attname like 'payment_check_%%' and not attisdropped").fetchall()
        assert [x["attname"] for x in colunas] == ["payment_check_pending"]
    finally:
        db.init_db()
