"""PR-B (ADR 0006), G-conversa: com a flag ligada "banco" grava a declaração, pelo `handle_incoming` com estado real.

Cada porta de `tests/_declaracao_b.PORTAS` cria o número esperado de declarações, com a Carteira intacta, a chave de
ação gravada como hash (nunca o wamid) e nenhuma pendência de pé. As conversas de dois assuntos são as do plano
(seção 11). O que estes testes NÃO provam: o NLP/IA real (a IA está fora, `ia_fora`), o wamid e a reentrega reais da
Meta, o Whisper (a transcrição é trocada), a Pluggy real.

Controle negativo: apagar o ramo `declara` de `launches.add` deixa vermelhos as portas de texto e `test_resposta_*`
(a recusa volta). Positivo: `test_dinheiro_*` (`test_declaracao_b_flag_desligada.py`) e `test_cartao_*` seguem iguais.
"""
from __future__ import annotations

import pytest

import db
from tests._declaracao_b import (PORTAS, ana, carteira, declaracoes, fala, liga, manda_wamid,  # noqa: F401
                                 n_lancamentos, pendencia, q, sem_pergunta)
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from tests.test_bank_movements import _bank, _sync


@pytest.mark.parametrize("nome", sorted(PORTAS))
def test_porta_ligada_declara(ana, liga, monkeypatch, nome):
    roteiro, quantas = PORTAS[nome]
    respostas = roteiro(ana, monkeypatch)
    decls = declaracoes(ana)
    assert len(decls) == quantas, respostas
    assert n_lancamentos(ana) == quantas and carteira(ana) == 200.0
    assert any("registrada pelo banco" in r for r in respostas), respostas
    assert sem_pergunta(ana)
    for d in decls:  # a chave é hash + ordem no turno; o wamid cru nunca vai para a coluna
        assert d["external_id"].startswith("wa:") and "wamid" not in d["external_id"], d["external_id"]
    assert len({d["external_id"] for d in decls}) == quantas


@pytest.mark.parametrize("resposta", ["pix", "débito", "transferência", "pelo banco", "foi no pix mesmo", "banco"])
def test_resposta_de_banco_declara(ana, liga, resposta):
    assert "dinheiro vivo" in manda_wamid(ana, "gastei 50 no mercado")[0]
    r = manda_wamid(ana, resposta)[0]
    assert "Despesa registrada pelo banco" in r, r
    assert len(declaracoes(ana)) == 1 and carteira(ana) == 200.0 and sem_pergunta(ana)


def test_resposta_dinheiro_vai_para_a_carteira(ana, liga):
    manda_wamid(ana, "gastei 50 no mercado")
    manda_wamid(ana, "dinheiro")
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 1 and carteira(ana) == 150.0


def test_cancelar_e_negacao_nao_gravam(ana, liga):
    manda_wamid(ana, "gastei 50 no mercado")
    assert manda_wamid(ana, "cancela") == ["Ok, não registrei nada."]
    manda_wamid(ana, "gastei 50 no mercado")
    assert "dinheiro vivo" in manda_wamid(ana, "não foi em dinheiro")[0]  # negação: pergunta de novo
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and pendencia(ana) == "payment_method_choice"


def test_pergunta_abandonada_nao_grava_declaracao(ana, liga):
    """A9: uma mensagem nova com número durante a pergunta abandona com aviso; "banco" depois não ressuscita o pedido."""
    manda_wamid(ana, "gastei 50 no mercado")
    r = manda_wamid(ana, "gastei 30 na padaria em dinheiro")
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 1 and carteira(ana) == 170.0, r
    assert "registrada pelo banco" not in manda_wamid(ana, "banco")[0]
    assert declaracoes(ana) == []


def test_misto_nao_grava_e_pede_duas_mensagens(ana, liga, monkeypatch):
    for r in (manda_wamid(ana, "gastei 40 em dinheiro e 60 no pix")[0],
              fala(monkeypatch, ana, "gastei 40 no mercado em dinheiro e 60 na padaria no pix")[0]):
        assert "Manda separado" in r and "Open Finance" not in r, r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and carteira(ana) == 200.0


def test_receita_declara_sem_mexer_na_carteira(ana, liga):
    r = manda_wamid(ana, "recebi 1000 de salário no pix")[0]
    assert "Receita registrada pelo banco" in r and "R$ 1.000,00" in r, r
    (d,) = declaracoes(ana)
    assert d["tipo"] == "receita" and float(d["valor"]) == 1000.0 and carteira(ana) == 200.0


def test_retroativo_declara_com_a_data_de_ontem(ana, liga):
    from utils_date import today_tz
    manda_wamid(ana, "gastei 50 ontem no mercado no pix")
    (d,) = declaracoes(ana)
    assert (today_tz() - d["criado_em"].astimezone(__import__("utils_date")._tz()).date()).days == 1


def test_sem_conta_corrente_declara_mesmo_assim(uid_pro, ia_fora, liga):
    """A12/P4: conexão ativa só com cartão (nenhuma conta BANK/BRL): grava; nunca casa, fica "a conferir"."""
    item = db.save_pluggy_open_finance_item(uid_pro, {"id": f"cc-{uid_pro}", "connector": {"id": 1, "name": "Nu"},
                                                       "status": "UPDATED"})
    db.save_open_finance_sync(item["id"], [{"provider_account_id": f"cc-a-{uid_pro}", "name": "Cartão",
                                            "type": "CREDIT", "currency": "BRL", "balance": 0, "transactions": []}])
    assert db.has_open_finance_connections(uid_pro)
    r = manda_wamid(uid_pro, "gastei 50 no mercado no pix")[0]
    assert "registrada pelo banco" in r
    assert "sem uma conta corrente conectada, eu não consigo conferir sozinho" in r and "Ainda não apareceu no seu extrato." not in r
    (d,) = declaracoes(uid_pro)
    assert d["matched_transaction_id"] is None


def test_dois_bancos_declaram_sem_conta_fixa(ana, liga):
    item = db.save_pluggy_open_finance_item(ana, {"id": f"b2-{ana}", "connector": {"id": 2, "name": "Itau"},
                                                   "status": "UPDATED"})
    _sync(item["id"], 500)
    manda_wamid(ana, "gastei 50 no mercado no pix")
    assert q("select account_id from bank_movement_declarations where user_id=%s", (ana,))["account_id"] is None


def test_consulta_do_banco_falha_nao_declara(uid_pro, ia_fora, liga, monkeypatch):
    """A14: a consulta cai, `decidir` devolve BANCO por fail-safe e o declarante confere de novo: T9, nada grava."""
    def cai(_uid):
        raise RuntimeError("DB caiu")
    monkeypatch.setattr(db, "has_open_finance_connections", cai)
    r = manda_wamid(uid_pro, "gastei 50 no mercado no pix")[0]
    assert "Não consegui confirmar seu banco agora" in r, r
    assert declaracoes(uid_pro) == [] and n_lancamentos(uid_pro) == 0


def test_conversa_declara_saldo_e_quanto_gastei(ana, liga):
    """Plano 11, conversa 1: a Carteira do /saldo não muda, o "quanto gastei" soma a declaração, "desfazer" apaga."""
    manda_wamid(ana, "gastei 50 no mercado")
    assert "registrada pelo banco" in manda_wamid(ana, "banco")[0]
    saldo = manda_wamid(ana, "saldo")[0]
    assert "Carteira: R$ 200,00" in saldo and "não confirmadas" in saldo, saldo
    assert "R$ 50,00" in manda_wamid(ana, "quanto gastei hoje?")[0]
    assert sem_pergunta(ana)
    manda_wamid(ana, "desfazer")
    assert "apagado" in manda_wamid(ana, "sim")[0]
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and carteira(ana) == 200.0
    assert q("select count(*) n from launches where user_id=%s", (ana,))["n"] == 0  # nem sombra extra


def test_conversa_pergunta_abandonada_por_conta_a_pagar(ana, liga):
    """Plano 11, conversa 2: "paguei a luz" abandona a pergunta; a conta segue a regra de hoje (marca paga SEM
    lançamento, DP-3); a declaração só nasce da frase nova com wamid novo."""
    from tests.test_conta_paga_forma import B, conta_fixa
    conta = conta_fixa(ana, valor=90.0)
    manda_wamid(ana, "gastei 50 no mercado")
    assert "Cancelei a pergunta anterior" in manda_wamid(ana, "paguei a luz")[0]
    assert "marcada como paga pelo banco" in manda_wamid(ana, "banco")[0]
    assert B.get_bill(ana, conta["id"])["status"] == "paid" and declaracoes(ana) == [] and n_lancamentos(ana) == 0
    assert "registrada pelo banco" in manda_wamid(ana, "gastei 50 no mercado pelo banco")[0]
    assert len(declaracoes(ana)) == 1 and carteira(ana) == 200.0


def test_recorrente_x_banco_pergunta_o_valor(ana, liga):
    """Q42 não é restaurado: `register_if_recurring` não lança sozinho em "banco" (a declaração tem valor travado).
    Com histórico de 3 aluguéis na Carteira, "paguei o aluguel pelo banco" pergunta o valor e declara a resposta."""
    for _ in range(3):
        db.add_launch_and_update_balance(ana, "despesa", 1500, "aluguel", "aluguel", "moradia")
    manda_wamid(ana, "gastei 40 no mercado e paguei o aluguel no pix")  # multi: o pedaço sem valor vai para a fila, sem lançar
    assert pendencia(ana) == "multi_launch_values"
    assert len(declaracoes(ana)) == 1
    manda_wamid(ana, "1500")
    assert sorted(float(d["valor"]) for d in declaracoes(ana)) == [40.0, 1500.0]


def test_valor_e_data_da_declaracao_sem_par_seguem_travados_categoria_edita(ana, liga):
    """DP-4: a declaração nascida pela conversa recusa a correção de valor e de data ("apague e lance de novo")
    e aceita categoria e descrição; recategorizar para interna é recusado (PR-A)."""
    from datetime import datetime
    from decimal import Decimal
    from adapters.whatsapp import wa_runtime
    manda_wamid(ana, "gastei 50 no mercado")
    manda_wamid(ana, "banco")
    lid = declaracoes(ana)[0]["id"]
    from db.lancamentos import NaoEditavel
    with pytest.raises(NaoEditavel):  # a regra do valor é a do `pode` da v2: declaração não edita valor
        db.update_launch_fields(ana, lid, valor=Decimal("500"), exigir_pode=True)
    with pytest.raises(db.LaunchDateLockedError, match="apague e lance de novo"):
        db.update_launch_fields(ana, lid, criado_em=datetime(2026, 1, 1))
    assert "declarado como pago pelo banco" in wa_runtime._apply_recategorize(ana, lid, "investimento_aporte")
    assert db.update_launch_fields(ana, lid, categoria="lazer", nota="outra nota")
    (d,) = declaracoes(ana)
    assert float(d["valor"]) == 50.0 and d["categoria"] == "lazer"
