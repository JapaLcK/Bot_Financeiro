"""Ordem inversa da reconciliação: o banco importou antes, o usuário lança depois.

Cenário base: banco em 950 depois de "MERCADO -50" importado; o usuário manda
"gastei 50 no mercado". O resultado tem de ser o MESMO estado da ordem direta
(`tests/test_reconciliacao_resolver.py::pendencia`): `imported` = a sombra,
`match` = o manual, status `pending`. Nada funde sozinho, a sombra fica viva e
os totais contam duas vezes até o usuário decidir.

Controle negativo esperado (CLAUDE.md §3; NÃO medido, medir no CI: os testes
com banco não rodam sem Postgres): trocar a chamada de `add_from_entities` por
no-op deveria deixar vermelhos a conversa principal, as duas mensagens, a
entrada real e o um-para-um. Controle positivo: fora da tolerância, fora da
janela, rejeitada e isolamento entre usuários continuam sem par. Medido aqui só
com `pytest --noconftest` (sem o conftest do repositório, que exige Postgres):
`test_conta_paga_leitura_do_aviso_falha_nao_afirma_em_dia`, com
`db.get_consolidated_balance` em stub. Com o conftest, nenhum teste deste
arquivo rodou localmente.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest

import db
from utils_date import today_tz

from tests._fusao_of_helpers import (  # noqa: F401 (uid_pro/ia_fora são fixtures)
    conecta_banco, consolidado, ia_fora, manda, sincroniza, tx, uid_pro, ultimo_launch,
)
from tests.test_fusao_of_superficies_da_carteira import _Req, sem_autorizacao  # noqa: F401
from tests.test_reconciliacao_resolver import _estado, _gasto_do_mes, _q


def _banco(uid, *transacoes, saldo="950.00"):
    """Banco conectado com as transações JÁ importadas (a ordem inversa)."""
    conexao = conecta_banco(uid, saldo, list(transacoes))
    assert db.import_open_finance_launches(uid, conexao)["inserted"] == len(transacoes)
    return conexao


def _of(uid, ident="1"):
    return _q("select id from open_finance_transactions where provider_transaction_id=%s",
              (f"of-tx-{uid}-{ident}",))[0]["id"]


def _pendentes(uid):
    return db.reconciliation_summary(uid)["pending_count"]


def _sem_par(of_tx):
    e = _estado(of_tx)
    assert (e["match_launch_id"], e["reconciliation_status"]) == (None, "imported"), e


# ── 1. a conversa principal ────────────────────────────────────────────────

def test_manual_depois_do_import_vira_a_mesma_pendencia_da_ordem_direta(uid_pro, ia_fora):
    _banco(uid_pro, tx(uid_pro, "-50.00", today_tz(), "MERCADO"))
    of_tx = _of(uid_pro)
    sombra = _estado(of_tx)["imported_launch_id"]

    resp = manda(uid_pro, "gastei 50 no mercado em dinheiro")
    manual = ultimo_launch(uid_pro)

    assert "1 lançamento(s) a conferir" in resp, resp
    assert _estado(of_tx) == {"imported_launch_id": sombra, "match_launch_id": manual,
                              "reconciliation_status": "pending"}
    assert _q("select 1 from launches where id=%s", (sombra,)), "a sombra sumiu"
    assert consolidado(uid_pro) == (900.0, -50.0)
    assert _gasto_do_mes(uid_pro) == 100.0
    assert [r["of_tx_id"] for r in db.list_reconciliations(uid_pro)] == [of_tx]

    assert db.confirm_reconciliation(uid_pro, of_tx)["ok"] is True
    assert consolidado(uid_pro) == (950.0, 0.0)
    assert _gasto_do_mes(uid_pro) == 50.0


# ── 2. duas mensagens de assuntos diferentes ──────────────────────────────

def test_so_o_gasto_igual_forma_par(uid_pro, ia_fora):
    _banco(uid_pro, tx(uid_pro, "-50.00", today_tz(), "MERCADO"))
    resp_uber = manda(uid_pro, "gastei 30 no uber em dinheiro")
    uber = ultimo_launch(uid_pro)
    assert "a conferir" not in resp_uber, resp_uber

    manda(uid_pro, "gastei 50 no mercado em dinheiro")

    assert _estado(_of(uid_pro))["match_launch_id"] == ultimo_launch(uid_pro)
    assert not _q("select 1 from open_finance_transactions where match_launch_id=%s", (uber,))
    assert _pendentes(uid_pro) == 1


# ── 3. entrada real: R$, vírgula, maiúscula, "ontem"; nome que não parece ──

def test_entrada_real_com_data_de_ontem(uid_pro, ia_fora):
    """Banco a 4 dias: "ontem" fica a 3 (borda de dentro da janela) e forma par.
    Se o "ontem" se perdesse, o manual cairia hoje, a 4 dias, e ficaria sem par
    (o controle positivo `test_data_fora_da_janela_nao_forma_par`)."""
    _banco(uid_pro, tx(uid_pro, "-50.00", today_tz() - timedelta(days=4), "MERCADO"))
    resp = manda(uid_pro, "Gastei R$ 50,00 ontem no MERCADO em dinheiro")
    assert "registrada" in resp, resp
    assert _estado(_of(uid_pro))["match_launch_id"] == ultimo_launch(uid_pro)


def test_nome_diferente_tambem_vira_pendencia(uid_pro, ia_fora):
    _banco(uid_pro, tx(uid_pro, "-1.00", today_tz(), "COMPRA CARTAO 4412 XPTO"), saldo="113.88")
    manda(uid_pro, "Gastei 1 real com a barbara em dinheiro")
    assert _estado(_of(uid_pro))["reconciliation_status"] == "pending"


# ── 4. POSITIVOS: fora da tolerância e fora da janela não formam par ───────

@pytest.mark.parametrize("valor,forma_par", [("50,05", True), ("50,06", False)])
def test_tolerancia_de_valor_na_fronteira(uid_pro, ia_fora, valor, forma_par):
    """RECON_AMOUNT_TOL = 0,05: a borda casa, um centavo além não."""
    _banco(uid_pro, tx(uid_pro, "-50.00", today_tz(), "MERCADO"))
    resp = manda(uid_pro, f"gastei {valor} no mercado em dinheiro")
    assert "registrada" in resp and ("a conferir" in resp) is forma_par, resp
    assert _pendentes(uid_pro) == int(forma_par)
    if not forma_par:
        _sem_par(_of(uid_pro))


def test_data_fora_da_janela_nao_forma_par(uid_pro, ia_fora):
    """4 dias = um além de RECON_DATE_WINDOW (3)."""
    _banco(uid_pro, tx(uid_pro, "-50.00", today_tz() - timedelta(days=4), "MERCADO"))
    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    _sem_par(_of(uid_pro))
    assert _pendentes(uid_pro) == 0


# ── 5. rejeitada não volta ─────────────────────────────────────────────────

def test_rejeitada_nao_volta_com_outra_mensagem_nem_novo_import(uid_pro, ia_fora):
    conexao = _banco(uid_pro, tx(uid_pro, "-50.00", today_tz(), "MERCADO"))
    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    of_tx = _of(uid_pro)
    assert db.reject_reconciliation(uid_pro, of_tx)["changed"] is True

    manda(uid_pro, "gastei 30 no uber em dinheiro")
    sincroniza(conexao, uid_pro, "950.00", [tx(uid_pro, "-50.00", today_tz(), "MERCADO")])
    db.import_open_finance_launches(uid_pro, conexao)

    _sem_par(of_tx)
    assert _pendentes(uid_pro) == 0


# ── 6. isolamento entre usuários ───────────────────────────────────────────

def test_transacao_de_outro_usuario_nunca_forma_par(uid_pro, ia_fora):
    outro = uid_pro + 1
    db.ensure_user(outro)
    _banco(outro, tx(outro, "-50.00", today_tz(), "MERCADO"))  # B primeiro: id menor
    conecta_banco(uid_pro, "1000.00")

    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    manual_a = ultimo_launch(uid_pro)

    _sem_par(_of(outro))
    assert _pendentes(uid_pro) == 0
    # o manual de A, oferecido em nome de B, também não toca a transação de B
    assert db.propose_manual_reconciliation(outro, manual_a) == {"ok": True, "of_tx_id": None}
    _sem_par(_of(outro))


# ── 7 e 8. um-para-um e idempotência ───────────────────────────────────────

def test_um_manual_por_transacao(uid_pro, ia_fora):
    hoje = today_tz()
    _banco(uid_pro, tx(uid_pro, "-50.00", hoje - timedelta(days=1), "MERCADO", "1"),
           tx(uid_pro, "-50.00", hoje, "MERCADO", "2"), saldo="900.00")
    ontem, de_hoje = _of(uid_pro, "1"), _of(uid_pro, "2")

    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    primeiro = ultimo_launch(uid_pro)
    assert _estado(de_hoje)["match_launch_id"] == primeiro, "a data mais próxima vence"
    _sem_par(ontem)

    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    assert _estado(ontem)["match_launch_id"] == ultimo_launch(uid_pro)
    assert _estado(de_hoje)["match_launch_id"] == primeiro, "o segundo roubou o par"

    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    assert not _q("select 1 from open_finance_transactions where match_launch_id=%s",
                  (ultimo_launch(uid_pro),))


def test_chamar_duas_vezes_nao_cria_segunda_pendencia(uid_pro, ia_fora):
    hoje = today_tz()
    _banco(uid_pro, tx(uid_pro, "-50.00", hoje - timedelta(days=1), "MERCADO", "1"),
           tx(uid_pro, "-50.00", hoje, "MERCADO", "2"), saldo="900.00")
    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    manual = ultimo_launch(uid_pro)

    assert db.propose_manual_reconciliation(uid_pro, manual) == {"ok": True, "of_tx_id": None}
    assert len(_q("select id from open_finance_transactions where match_launch_id=%s",
                  (manual,))) == 1
    _sem_par(_of(uid_pro, "1"))


# ── 9. as outras portas: POST /launches e "paguei a luz" ───────────────────

def test_rota_post_launches_cria_a_pendencia(uid_pro, ia_fora, sem_autorizacao):
    import frontend.finance_bot_websocket_custom as mono
    _banco(uid_pro, tx(uid_pro, "-50.00", today_tz(), "MERCADO"))

    r = asyncio.run(mono.create_launch_route(_Req(), uid_pro, mono.LaunchCreatePayload(
        tipo="despesa", valor=50.0, nota="mercado", funding_source="carteira")))

    assert _estado(_of(uid_pro))["match_launch_id"] == r["launch_id"]
    assert _pendentes(uid_pro) == 1


def test_paguei_a_luz_depois_do_debito_importado(uid_pro, ia_fora):
    """Opção B: a conversa inteira (`handle_incoming` → intent_router →
    `try_pay_from_text` → `mark_bill_paid`) avisa na resposta, sem "em dia"."""
    from db.bills import create_boleto
    _banco(uid_pro, tx(uid_pro, "-120.00", today_tz(), "PAGAMENTO ENEL LUZ"), saldo="880.00")
    create_boleto(uid_pro, "Luz", 120.0, today_tz(), category="moradia")

    resp = manda(uid_pro, "paguei a luz em dinheiro")

    assert "Conta paga" in resp, resp
    assert "⚠ 1 lançamento(s) a conferir no PigBank." in resp, resp
    assert "pode ser" not in resp and "tudo em dia" not in resp, resp
    assert _estado(_of(uid_pro))["match_launch_id"] == ultimo_launch(uid_pro)
    assert _pendentes(uid_pro) == 1


def test_paguei_a_luz_sem_debito_igual_segue_em_dia(uid_pro, ia_fora):
    """Controle positivo: sem transação que case, a resposta de sempre."""
    from db.bills import create_boleto
    _banco(uid_pro, tx(uid_pro, "-50.00", today_tz(), "MERCADO"))
    create_boleto(uid_pro, "Luz", 120.0, today_tz(), category="moradia")

    resp = manda(uid_pro, "paguei a luz em dinheiro")

    assert "Conta paga" in resp and "Tá tudo em dia! 🐷" in resp, resp
    assert "a conferir" not in resp, resp
    _sem_par(_of(uid_pro))


def test_conta_paga_leitura_do_aviso_falha_nao_afirma_em_dia(monkeypatch, caplog):
    """Sem banco: a conta já foi paga; se ler a pendência estoura, a resposta
    fecha no "lançado e categorizado." — nem "em dia" (não dá para afirmar
    sem ler) nem "a conferir" — e a exceção vai para o log."""
    from core.handlers.bills import conta_paga

    def _estoura(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(db, "get_consolidated_balance", _estoura)
    caplog.set_level(logging.ERROR, logger="core.handlers.bills")

    resp = conta_paga(1, {"name": "Luz"}, 120.0)

    assert resp.endswith("lançado e categorizado."), resp
    assert "em dia" not in resp and "a conferir" not in resp, resp
    assert any("aviso a conferir falhou" in r.getMessage() and r.exc_info
               for r in caplog.records)


# ── 10. falha dentro da função não derruba o lançamento ───────────────────

def test_falha_vira_log_e_o_lancamento_fica(uid_pro, ia_fora, monkeypatch, caplog):
    import db.open_finance as of_mod
    _banco(uid_pro, tx(uid_pro, "-50.00", today_tz(), "MERCADO"))

    def _estoura(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(of_mod, "pick_reconciliation_match", _estoura)
    caplog.set_level(logging.ERROR, logger="db.open_finance")

    resp = manda(uid_pro, "gastei 50 no mercado em dinheiro")

    assert "registrada" in resp, resp
    assert consolidado(uid_pro) == (900.0, -50.0), "o lançamento não ficou"
    assert any("propose_manual_reconciliation falhou" in r.getMessage() for r in caplog.records)
    _sem_par(_of(uid_pro))


# ── 11. receita também vira pendência ─────────────────────────────────────

def test_receita_depois_do_import_vira_pendencia(uid_pro, ia_fora):
    _banco(uid_pro, tx(uid_pro, "100.00", today_tz(), "CREDITO XPTO 9981"), saldo="1100.00")
    manda(uid_pro, "recebi 100 do fulano em dinheiro")

    assert _estado(_of(uid_pro))["match_launch_id"] == ultimo_launch(uid_pro)
    assert db.reconciliation_summary(uid_pro)["receita_back"] == -100


# ── 12. pendência órfã (manual apagado) volta a formar par ─────────────────

def test_pendencia_orfa_forma_par_com_o_manual_novo(uid_pro, ia_fora):
    _banco(uid_pro, tx(uid_pro, "-50.00", today_tz(), "MERCADO"))
    of_tx = _of(uid_pro)
    sombra = _estado(of_tx)["imported_launch_id"]
    manda(uid_pro, "gastei 50 no mercado em dinheiro")
    db.delete_launch_and_rollback(uid_pro, ultimo_launch(uid_pro))
    assert _estado(of_tx)["match_launch_id"] is None
    assert _pendentes(uid_pro) == 0

    manda(uid_pro, "gastei 50 no mercado em dinheiro")

    assert _estado(of_tx) == {"imported_launch_id": sombra,
                              "match_launch_id": ultimo_launch(uid_pro),
                              "reconciliation_status": "pending"}
    assert _pendentes(uid_pro) == 1
