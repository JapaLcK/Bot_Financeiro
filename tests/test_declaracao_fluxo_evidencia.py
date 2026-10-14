"""P1 do Codex (#968): o casamento AUTOMÁTICO da despesa/receita declarada exige evidência de comércio.

Além de sinal, R$ 0,05, janela de 3 dias, candidato único nos dois sentidos e não interna, o automático só
liga se a descrição do banco for parecida com o alvo/nota da declaração OU a declaração for genérica (sem
estabelecimento): o mesmo critério do dinheiro (`open_finance.merchant_evidence`). Sem evidência a transação
segue candidata da escolha MANUAL (modal), não casa sozinha, a sombra NÃO é apagada e as duas linhas contam
até a pessoa confirmar. A unicidade nos dois sentidos conta só candidatos que passam também na evidência.
Revalidar um vínculo feito olha valor/data como antes (descrição do banco mudar não desfaz), e o manual
nunca exige evidência.
Controle negativo: sem `tem_evidencia` no grafo de candidatos de `reconcile_bank_movements`, ficam vermelhos
os de "não casa"; os de "casa" (nome parecido, genérica) são o positivo do conserto restritivo.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

import db
import db.bank_movements as movements
from db.bank_declaracoes import tem_evidencia
from db.resumo_mes import totais_do_mes
from tests._patrimonio_helpers import q
from tests.test_bank_movements import _bank, _deposit, _sync, _tx
from tests.test_declaracao_fluxo_reconcilia import decl, declara, n_linhas, sincroniza, tx
from utils_date import _tz, today_tz

D = Decimal


def saiu(uid):
    return totais_do_mes(uid, today_tz())["saiu"]


def _tx_id(txid):
    return q("select id from open_finance_transactions where provider_transaction_id=%s", (txid,))["id"]


def test_exemplo_do_codex_farmacia_nao_casa_com_mercado_e_a_escolha_manual_casa(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id, alvo="mercado")
    sincroniza(cid, user_id, tx(desc="FARMÁCIA SÃO JOÃO"))
    assert decl(lid)["matched_transaction_id"] is None
    assert n_linhas(user_id, 50) == 2 and saiu(user_id) == 100  # declaração + sombra da farmácia
    [mov] = movements.list_bank_movements(user_id)
    assert [c["description"] for c in mov["candidates"]] == ["FARMÁCIA SÃO JOÃO"]  # segue candidata manual
    movements.confirm_bank_movement(user_id, lid, mov["candidates"][0]["id"])
    d = decl(lid)
    assert d["matched_transaction_id"] and d["confirmation_method"] == "manual"
    assert n_linhas(user_id, 50) == 1 and saiu(user_id) == 50


@pytest.mark.parametrize("alvo,desc", [
    ("mercado", "SUPERMERCADO XYZ"),
    ("Padaria Pão Quente", "padaria pao quente ltda"),   # acento e caixa
    ("açaí", "ACAI DO ZE"),
    ("Pão de Açúcar", "PAO DE ACUCAR 123"),
    ("uber", "Uber *TRIP"),
])
def test_nome_parecido_casa_sozinha(user_id, alvo, desc):
    cid, _ = _bank(user_id)
    lid = declara(user_id, alvo=alvo, nota="msg")
    sincroniza(cid, user_id, tx(desc=desc))
    assert decl(lid)["confirmation_method"] == "automatic", (alvo, desc)
    assert n_linhas(user_id, 50) == 1


@pytest.mark.parametrize("alvo,nota", [(None, "gastei 50"), ("", None), ("almoco", None)])
@pytest.mark.parametrize("desc", ["FARMÁCIA SÃO JOÃO", "", "PIX ENVIADO"])
def test_declaracao_generica_casa_com_qualquer_unico_de_50(user_id, alvo, nota, desc):
    cid, _ = _bank(user_id)
    lid = declara(user_id, alvo=alvo, nota=nota)
    sincroniza(cid, user_id, tx(desc=desc))
    assert decl(lid)["confirmation_method"] == "automatic"


def test_descricao_do_banco_vazia_nao_e_evidencia_para_declaracao_com_nome(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id, alvo="mercado")
    sincroniza(cid, user_id, tx(desc=""))
    assert decl(lid)["matched_transaction_id"] is None
    assert len(movements.list_bank_movements(user_id)[0]["candidates"]) == 1


def test_dois_candidatos_so_um_com_nome_parecido_casa_com_o_parecido(user_id):
    """A unicidade conta os candidatos ELEGÍVEIS PARA O AUTOMÁTICO (os que passam na evidência): a
    farmácia nem é candidata automática da declaração "mercado"; fica como sombra até a pessoa decidir."""
    cid, _ = _bank(user_id)
    lid = declara(user_id, alvo="mercado")
    sincroniza(cid, user_id, tx(txid="s", desc="SUPERMERCADO XYZ"), tx(txid="f", desc="FARMACIA SAO JOAO"))
    assert decl(lid)["matched_transaction_id"] == _tx_id("s")
    assert n_linhas(user_id, 50) == 2  # a declaração (ligada ao supermercado) + a sombra da farmácia


def test_dois_candidatos_ambos_parecidos_nao_casa(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id, alvo="mercado")
    sincroniza(cid, user_id, tx(txid="a", desc="MERCADO A"), tx(txid="b", desc="MERCADO B"))
    assert decl(lid)["matched_transaction_id"] is None


def test_simetria_transacao_com_evidencia_para_duas_declaracoes_nao_casa(user_id):
    cid, _ = _bank(user_id)
    a, b = declara(user_id, alvo="mercado"), declara(user_id, alvo=None, nota="gastei 50")  # genérica: evidência de tudo
    sincroniza(cid, user_id, tx(desc="SUPERMERCADO XYZ"))
    assert decl(a)["matched_transaction_id"] is None and decl(b)["matched_transaction_id"] is None


def test_cada_declaracao_casa_com_a_sua_transacao(user_id):
    cid, _ = _bank(user_id)
    a, b = declara(user_id, alvo="mercado"), declara(user_id, alvo="farmacia")
    sincroniza(cid, user_id, tx(txid="s", desc="SUPERMERCADO XYZ"), tx(txid="f", desc="FARMACIA SAO JOAO"))
    assert decl(a)["matched_transaction_id"] == _tx_id("s") and decl(b)["matched_transaction_id"] == _tx_id("f")


def test_ordem_inversa_sombra_so_some_com_evidencia(user_id):
    cid, _ = _bank(user_id)
    sincroniza(cid, user_id, tx(txid="f", desc="FARMACIA SAO JOAO"))
    sem = declara(user_id, alvo="mercado")
    assert decl(sem)["matched_transaction_id"] is None and n_linhas(user_id, 50) == 2
    com = declara(user_id, 50, alvo="farmacia")
    # "sem" e "com" disputam a mesma transação: "com" tem evidência e é a única que a reivindica
    assert decl(com)["matched_transaction_id"] == _tx_id("f") and decl(sem)["matched_transaction_id"] is None


def test_receita_exige_evidencia_do_pagador(user_id):
    cid, _ = _bank(user_id)
    a = declara(user_id, 3000, "receita", alvo="salario")
    sincroniza(cid, user_id, tx(3000, desc="TED RECEBIDO ACME LTDA", category="Salary"))
    assert decl(a)["matched_transaction_id"] is None
    b = declara(user_id, 3000, "receita", alvo="acme")  # o pagador bate: só b é candidata automática
    assert decl(b)["matched_transaction_id"] and decl(a)["matched_transaction_id"] is None


def test_revalidacao_do_vinculo_automatico_nao_exige_evidencia(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id, alvo="mercado")
    sincroniza(cid, user_id, tx(desc="SUPERMERCADO XYZ"))
    assert decl(lid)["confirmation_method"] == "automatic"
    sincroniza(cid, user_id, tx(desc="COMPRA NO DEBITO 1234"))  # o banco muda só o texto
    assert decl(lid)["matched_transaction_id"]


def test_vinculo_manual_nunca_exige_evidencia_nem_cai_por_descricao(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id, alvo="mercado")
    sincroniza(cid, user_id, tx(desc="FARMACIA"))
    movements.confirm_bank_movement(user_id, lid, movements.list_bank_movements(user_id)[0]["candidates"][0]["id"])
    for _ in range(2):
        with db.get_conn() as conn, conn.cursor() as cur:
            movements.reconcile_bank_movements(cur, user_id)
            conn.commit()
    sincroniza(cid, user_id, tx(desc="OUTRO TEXTO"))
    d = decl(lid)
    assert d["matched_transaction_id"] and d["confirmation_method"] == "manual"


def test_correcao_numerica_do_banco_continua_desvinculando(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id, alvo="mercado")
    sincroniza(cid, user_id, tx(desc="SUPERMERCADO XYZ"))
    sincroniza(cid, user_id, tx(-52.30, desc="SUPERMERCADO XYZ"))
    assert decl(lid)["matched_transaction_id"] is None and decl(lid)["requires_review"]


def test_congelada_segue_a_mesma_regra(user_id):
    cid, _ = _bank(user_id)
    antiga = datetime.now(_tz()) - timedelta(days=50)
    sem = declara(user_id, alvo="mercado", criado_em=antiga)
    sincroniza(cid, user_id, tx(days=-50, desc="FARMACIA"))
    assert decl(sem)["matched_transaction_id"] is None
    q("delete from launches where id=%s returning id", (sem,))
    com = declara(user_id, alvo="farmacia", criado_em=antiga)
    sincroniza(cid, user_id, tx(days=-50, desc="FARMACIA"))
    assert decl(com)["matched_transaction_id"]
    sincroniza(cid, user_id, tx(days=-50, desc="TEXTO NOVO"))  # congelada e ligada: descrição não desfaz
    assert decl(com)["matched_transaction_id"]


def test_aporte_nao_exige_evidencia(user_id):
    cid, source = _bank(user_id)
    lid = _deposit(user_id, source)  # caixinha "viagem"; o extrato diz "Aplicação ou resgate"
    _sync(cid, 500, _tx())
    assert decl(lid)["matched_transaction_id"]


def test_tem_evidencia_unitario():
    base = {"tipo": "despesa", "account_id": None, "alvo": "mercado", "nota": "gastei no mercado"}
    assert tem_evidencia(base, {"description": "SUPERMERCADO XYZ"})
    assert not tem_evidencia(base, {"description": "FARMACIA"}) and not tem_evidencia(base, {"description": None})
    assert tem_evidencia({**base, "alvo": None}, {"description": "FARMACIA"})  # genérica
    assert tem_evidencia({**base, "tipo": "aporte_investimento"}, {"description": "FARMACIA"})  # aporte: não se aplica
