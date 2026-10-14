"""PR-A: despesa/receita declarada "paga pelo banco" casa com a transação do extrato.

Escritor (`add_launch_and_update_balance(declaracao_banco=True)`), `save_open_finance_sync` e
`import_open_finance_launches` reais, Postgres real. Automático só com candidato ÚNICO nos dois
sentidos, valor quase exato (R$ 0,05) e janela de 3 dias; sem par, a declaração fica "a conferir".

Controles (CLAUDE.md §3): `elegivel_fluxo` devolvendo False deixa vermelhos os de vínculo; tirar
`and not e_fluxo(d)` da marcação de ambiguidade em `reconcile_bank_movements` também (conta NULL nunca
casaria); a checagem de sinal tem o seu (`test_sinal_oposto_...`, valor pequeno); os de "não casa" (dois
candidatos, fora da tolerância) são o positivo do conserto.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

import db
import db.bank_movements as movements
from db.bank_declaracoes import congelada, desde_da_leitura, elegivel_fluxo
from db.resumo_mes import totais_do_mes
from tests._patrimonio_helpers import q
from tests.test_bank_movements import _bank, _deposit, _sync
from utils_date import _tz, today_tz


def tx(amount=-50, txid="t1", days=0, category="Groceries", desc="Mercado Bom Preco"):
    return {"provider_transaction_id": txid, "description": desc, "amount": amount,
            "transaction_date": today_tz() + timedelta(days=days), "category": category}


def declara(uid, valor=50, tipo="despesa", criado_em=None, alvo="mercado", nota="msg"):
    return db.add_launch_and_update_balance(
        uid, tipo, valor, alvo, nota, "mercado", criado_em, declaracao_banco=True)[0]


def sincroniza(cid, uid, *txs, saldo=1000):
    _sync(cid, saldo, *txs)
    db.import_open_finance_launches(uid)


def decl(lid):
    return q("select * from bank_movement_declarations where launch_id=%s", (lid,))


def n_linhas(uid, valor):
    return q("select count(*) n from launches where user_id=%s and valor=%s", (uid, valor))["n"]


def saiu(uid):
    return totais_do_mes(uid, today_tz())["saiu"]


def test_ordem_direta_declara_antes_do_sync(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    assert decl(lid)["matched_transaction_id"] is None and n_linhas(user_id, 50) == 1
    assert saiu(user_id) == 50  # sem par: conta uma vez
    sincroniza(cid, user_id, tx())
    d = decl(lid)
    assert d["matched_transaction_id"] and d["confirmation_method"] == "automatic"
    assert d["account_id"] is None  # "qualquer conta" segue NULL: é o que a mantém no caminho do fluxo
    assert n_linhas(user_id, 50) == 1 and saiu(user_id) == 50
    assert db.get_balance(user_id) == 0  # Carteira intocada


def test_ordem_inversa_sombra_some_ao_declarar(user_id):
    cid, _ = _bank(user_id)
    sincroniza(cid, user_id, tx())
    assert n_linhas(user_id, 50) == 1 and saiu(user_id) == 50  # só a sombra
    lid = declara(user_id)
    assert decl(lid)["matched_transaction_id"]
    assert n_linhas(user_id, 50) == 1 and saiu(user_id) == 50
    assert q("select source from launches where id=%s", (lid,))["source"] in (None, "manual")
    assert q("select count(*) n from launches where user_id=%s and source='open_finance'", (user_id,))["n"] == 0


def test_receita_casa_com_credito(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id, 3000, "receita", alvo="salario")  # o nome do pagador é a evidência de comércio
    sincroniza(cid, user_id, tx(3000, category="Salary", desc="Salario"))
    assert decl(lid)["matched_transaction_id"]
    assert n_linhas(user_id, 3000) == 1 and totais_do_mes(user_id, today_tz())["entrou"] == 3000


def test_despesa_nao_casa_com_credito_de_mesmo_valor(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx(50, category="Transfer", desc="Pix recebido"))
    assert decl(lid)["matched_transaction_id"] is None


def test_dois_candidatos_nao_casa_e_o_modal_oferece_os_dois(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx(txid="a"), tx(txid="b"))
    assert decl(lid)["matched_transaction_id"] is None
    assert n_linhas(user_id, 50) == 3 and saiu(user_id) == 150  # D + S1 + S2 até a escolha
    [mov] = movements.list_bank_movements(user_id)
    assert len(mov["candidates"]) == 2


def test_duas_declaracoes_uma_transacao_nao_casa(user_id):
    cid, _ = _bank(user_id)
    a, b = declara(user_id), declara(user_id)
    sincroniza(cid, user_id, tx())
    assert decl(a)["matched_transaction_id"] is None and decl(b)["matched_transaction_id"] is None


@pytest.mark.parametrize("valor,casa", [(-50.05, True), (-49.95, True), (-50.06, False), (-49.9, False)])
def test_tolerancia_de_cinco_centavos(user_id, valor, casa):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx(valor))
    assert bool(decl(lid)["matched_transaction_id"]) is casa


@pytest.mark.parametrize("dias,casa", [(3, True), (-3, True), (4, False), (-4, False)])
def test_janela_de_tres_dias(user_id, dias, casa):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx(days=dias))
    assert bool(decl(lid)["matched_transaction_id"]) is casa


@pytest.mark.parametrize("category,desc", [("Same person transfer", "Pix"), ("Fixed income", "Aplicacao"),
                                           ("Groceries", "RESGATE CDB")])
def test_interna_e_investimento_nao_casam(user_id, category, desc):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx(category=category, desc=desc))
    assert decl(lid)["matched_transaction_id"] is None


def test_duas_rodadas_de_reconcile_mantem_o_vinculo(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx())
    tx_id = decl(lid)["matched_transaction_id"]
    assert tx_id
    for _ in range(2):
        with db.get_conn() as conn, conn.cursor() as cur:
            movements.reconcile_bank_movements(cur, user_id)
            conn.commit()
        d = decl(lid)
        assert d["matched_transaction_id"] == tx_id and not d["requires_review"]


def test_correcao_do_banco_desfaz_o_vinculo(user_id):
    cid, _ = _bank(user_id)
    lid = declara(user_id)
    sincroniza(cid, user_id, tx())
    assert decl(lid)["matched_transaction_id"]
    sincroniza(cid, user_id, tx(-52.30))
    d = decl(lid)
    assert d["matched_transaction_id"] is None and d["requires_review"]


def test_desde_nao_esconde_candidato_nem_aporte_antigo(user_id):
    cid, source = _bank(user_id)
    lid_aporte = _deposit(user_id, source)  # aporte declarado 10 dias atrás, extrato na mesma data
    q("update bank_movement_declarations set declared_at = declared_at - interval '10 days' "
      "where launch_id=%s returning 1", (lid_aporte,))
    declara(user_id)
    sincroniza(cid, user_id, tx(-500, "ap", days=-10, category="Fixed income", desc="Aplicacao"))
    assert decl(lid_aporte)["matched_transaction_id"]
    ds = [dict(d) for d in _decls(user_id)]
    assert desde_da_leitura(ds) == (min(d["declared_at"] for d in ds).astimezone(_tz()).date() - timedelta(days=3))
    assert desde_da_leitura([]) is None


def _decls(uid):
    with db.get_conn() as conn, conn.cursor() as cur:
        return movements._declarations(cur, uid)


def test_elegibilidade_automatica_x_manual():
    base = {"amount": Decimal(-50), "declared_at": datetime.now(_tz()), "account_id": None,
            "account_ambiguous": False, "tipo": "despesa"}
    t = {"amount": Decimal("-52.30"), "transaction_date": today_tz(), "account_id": 7,
         "economic_kind": False, "category": "Groceries", "description": "Mercado"}
    assert not elegivel_fluxo(base, t) and elegivel_fluxo(base, t, manual=True)  # 2,30 <= max(5; 10%)
    assert elegivel_fluxo({**base, "amount": Decimal(-100)}, {**t, "amount": Decimal(-109)}, manual=True)
    assert not elegivel_fluxo({**base, "amount": Decimal(-100)}, {**t, "amount": Decimal(-111)}, manual=True)
    assert not elegivel_fluxo({**base, "amount": Decimal(-20)}, {**t, "amount": Decimal(-26)}, manual=True)
    assert not elegivel_fluxo(base, {**t, "amount": Decimal("52.30")}, manual=True)  # sinal


def test_sinal_oposto_nao_e_elegivel_nem_no_manual():
    """Valor pequeno: -2,00 contra +2,00 difere 4 (<= 5,00); só a checagem de sinal separa os dois."""
    d = {"amount": Decimal(-2), "declared_at": datetime.now(_tz()), "account_id": None,
         "account_ambiguous": False, "tipo": "despesa"}
    t = {"amount": Decimal(2), "transaction_date": today_tz(), "account_id": 7,
         "economic_kind": False, "category": "Groceries", "description": "Mercado"}
    assert not elegivel_fluxo(d, t, manual=True) and not elegivel_fluxo(d, t)
    assert elegivel_fluxo(d, {**t, "amount": Decimal(-2)}, manual=True)  # positivo: mesmo sinal


def test_congelada_so_depois_de_45_dias_e_casada():
    agora = datetime.now(_tz())
    d = {"tipo": "despesa", "declared_at": agora - timedelta(days=46), "matched_transaction_id": 1}
    assert congelada(d, agora.date())
    assert not congelada({**d, "matched_transaction_id": None}, agora.date())
    assert not congelada({**d, "declared_at": agora - timedelta(days=44)}, agora.date())
    assert not congelada({**d, "tipo": "aporte_investimento"}, agora.date())


def test_erros_do_escritor_antes_de_abrir_conexao(user_id):
    for kw, codigo in [({"tipo": "aporte_investimento"}, "DECLARACAO_TIPO_INVALIDO"),
                       *[({"valor": v}, "DECLARACAO_VALOR_INVALIDO")
                         for v in (0, -5, "nan", 1e30, None, "abc", True, Decimal("NaN"))],
                       *[({"valor": v}, "DECLARACAO_VALOR_CASAS") for v in (0.001, 12.345, "7.555")],
                       ({"is_internal_movement": True}, "DECLARACAO_INTERNA"),
                       ({"extra_efeitos": {"bill_id": 1}}, "DECLARACAO_ARGS_INCOMPATIVEIS")]:
        args = {"tipo": "despesa", "valor": 10, **kw}
        with pytest.raises(ValueError) as erro:
            db.add_launch_and_update_balance(user_id, args.pop("tipo"), args.pop("valor"), "x", "y",
                                             declaracao_banco=True, **args)
        assert str(erro.value).split(":")[0] == codigo, (kw, str(erro.value))
        if codigo == "DECLARACAO_VALOR_INVALIDO":
            assert "maior que zero" in str(erro.value) and "recebido" in str(erro.value)
        if codigo == "DECLARACAO_VALOR_CASAS":
            assert "2 casas decimais" in str(erro.value) and str(kw["valor"]) in str(erro.value)
    assert q("select count(*) n from launches where user_id=%s", (user_id,))["n"] == 0
    assert declara(user_id, 12.34) and declara(user_id, "7.5", "receita")  # positivo: centavos válidos


def test_cursor_externo_e_recusado_para_declaracao(user_id):
    with db.get_conn() as conn, conn.cursor() as cur:
        with pytest.raises(ValueError, match="DECLARACAO_CURSOR_EXTERNO"):
            db.add_launch_and_update_balance(user_id, "despesa", 10, "x", "y", declaracao_banco=True, cur=cur)
        conn.rollback()
    assert q("select count(*) n from launches where user_id=%s", (user_id,))["n"] == 0
