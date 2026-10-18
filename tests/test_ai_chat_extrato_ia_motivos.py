"""PR C1, passada 1 do Tester: o motivo por estado, o que o Extrato não lista, o item editado depois da
pergunta, a corrida entre a releitura e o lock, a categoria ausente e o item que some no meio.

CONTROLES NEGATIVOS (injetados e restaurados, ver o relato): `exigir_pode=True` fora de cada uma das 3
escritas -> `test_corrida_*`; a comparação do `_item` fora de `_reler` -> `test_item_editado_*`;
`_fora_do_extrato` fora de `_validar` -> `test_fora_do_extrato_*`; o `isinstance/resolve` fora de
`validar_categoria` -> `test_categoria_ausente_*`. POSITIVOS: o item igual apaga/recategoriza.
"""
from __future__ import annotations

from datetime import date

import pytest

import db
from conftest import usuario_pagante
from core.services.ai_chat import runner
from core.services.ai_chat.tools import extrato_ia as ia
from db import lancamentos_leitura
from db.cards import MSG_COMPRA_DO_BANCO
from tests._fusao_of_helpers import uid_pro  # noqa: F401 (fixture)
from tests._patrimonio_helpers import conexao, conta, q, tx_banco
from tests.test_ai_chat_extrato_ia import arma, categoria, ct_existe, existe, lancar, pend, sim
from tests.test_api_v2_lancamentos_pode import _carteira, _sombra


def _cat(uid, ident, nova="lazer"):
    return arma(uid, "recategorize_launch", {"launch_id": ident, "new_category": nova})[1]


def _apagar(uid, ident):
    return arma(uid, "delete_launch", {"launch_id": ident})[1]


def _limpa(uid):
    runner._PENDENCIA_DO_TURNO.set(None)
    if p := pend(uid):
        db.ai_consume_pending_action(uid, p)


# ── A2: o motivo por estado ──────────────────────────────────────────────────────────────────

def test_cada_estado_recusa_com_o_seu_motivo_e_o_que_a_ia_faz():
    a = usuario_pagante()
    acc = conta(conexao(a, f"item-mot-{a}"), "acc-mot", "100")
    db.add_launch_and_update_balance(a, "receita", 1000, "seed", None)
    antiga = _carteira(a, marcada=False)
    delta_zero = db.add_launch_and_update_balance(a, "despesa", 36, "x", None, apply_delta=False)[0]
    sombra = _sombra(a, 38)
    paga = _carteira(a, valor=35, bill_id=1, paid_amount_added=35)
    paga_fund = _carteira(a, valor=41, bill_id=2, paid_amount_added=41)
    tx_banco(acc, "tx-pf", "-41", imported_launch_id=paga_fund, reconciliation_status="auto_merged")
    especie = _carteira(a, valor=33)
    q("""insert into of_cash_links (user_id, tx_key, key_durable, account_key, kind, status,
             launch_id, amount, tx_date) values (%s, 'k0', true, 'k', 'saque', 'ativo', %s, 1, %s)
         returning id""", (a, especie, date.today()))
    cartao = db.create_card(a, "Nubank", closing_day=31, due_day=10)
    manual_ct = db.add_credit_purchase(a, cartao, 39, "mercado", "manual", date.today())[0]
    of_ct = db.add_imported_credit_purchase(a, cartao, 40, "mercado", date.today(), "ct-mot")[0]
    db.create_pocket(a, "viagem")
    db.pocket_deposit_from_account(a, "viagem", 5, "guardando")
    guardado = int(q("select id from launches where user_id=%s and tipo='deposito_caixinha'", (a,))["id"])

    ANTIGO, BANCO, PAGAMENTO = "antigo ou veio de um extrato", "veio do seu banco", "pagamento de uma conta"
    CAIXINHA, CARTAO, ESPECIE = "caixinha ou de investimento", "compra no cartão foi lançada", "saque ou depósito em dinheiro"
    esperado = [  # (id, motivo de apagar, motivo de categoria) — None = pode
        (f"l{antiga}", ANTIGO, ANTIGO), (f"l{delta_zero}", ANTIGO, ANTIGO), (f"l{sombra}", BANCO, None),
        (f"l{paga}", PAGAMENTO, None), (f"l{paga_fund}", PAGAMENTO, None), (f"l{especie}", None, ESPECIE),
        (f"c{manual_ct}", CARTAO, CARTAO), (f"c{of_ct}", MSG_COMPRA_DO_BANCO, None),
        (f"l{guardado}", CAIXINHA, CAIXINHA),
    ]
    for ident, m_apagar, m_cat in esperado:
        for fn, motivo, outro in ((_apagar, m_apagar, m_cat), (_cat, m_cat, m_apagar)):
            final = fn(a, ident)
            if motivo is None:
                assert final is None, (ident, final)
                _limpa(a)
                continue
            assert final.startswith("🐷") and motivo in final and pend(a) is None, (ident, final)
            # o que a IA ainda faz: oferece o outro campo se o `pode` o permite, senão diz que é de leitura
            assert (("Se quiser" in final) if outro is None else ("só de leitura" in final)), (ident, final)
    assert ct_existe(a, manual_ct) and existe(a, antiga)


# ── A1: o que o Extrato não lista ────────────────────────────────────────────────────────────

def test_fora_do_extrato_diz_o_motivo_e_nao_nao_achei(uid_pro):
    db.create_pocket(uid_pro, "viagem")
    db.create_investment(uid_pro, "cdb", 1.0, "monthly")
    db.create_investment(uid_pro, "lci", 1.0, "monthly")
    db.delete_investment(uid_pro, "lci")
    q("insert into launches (user_id, tipo, valor, alvo) values (%s, 'tipo_novo', 0, 'x') returning id", (uid_pro,))
    linhas = q("""select id, tipo, user_seq from launches where user_id = %s
                   and tipo in ('criar_caixinha','create_investment','delete_investment','tipo_novo')""",
               (uid_pro,), fetch=True)
    assert {r["tipo"] for r in linhas} == {"criar_caixinha", "create_investment", "delete_investment", "tipo_novo"}
    for r in linhas:
        for bruto in (f"l{r['id']}", f"#{r['user_seq']}"):
            for final in (_apagar(uid_pro, bruto), _cat(uid_pro, bruto)):
                assert "Não achei" not in final and "não aparece no Extrato" in final, (r["tipo"], bruto, final)
                assert ("caixinha ou um investimento" in final) == (r["tipo"] != "tipo_novo"), final
                assert pend(uid_pro) is None
    assert q("select count(*) n from launches where user_id = %s", (uid_pro,))["n"] >= 4  # nada apagado


def test_fora_do_extrato_do_outro_usuario_continua_nao_achei(uid_pro):
    outro = usuario_pagante()
    db.create_pocket(outro, "viagem")
    row = q("select id, user_seq from launches where user_id = %s and tipo = 'criar_caixinha'", (outro,))
    for bruto in (f"l{row['id']}", f"#{row['user_seq']}"):
        assert "Não achei" in _apagar(uid_pro, bruto)


# ── A3: editado depois da pergunta ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool", ["delete_launch", "recategorize_launch"])
def test_item_editado_entre_a_pergunta_e_o_sim_nao_executa(uid_pro, tool):
    lid, _ = lancar(uid_pro, 50, "Mercado Extra")
    extra = {"new_category": "lazer"} if tool == "recategorize_launch" else {}
    h, final = arma(uid_pro, tool, {"launch_id": f"l{lid}", **extra})
    assert final is None and "Mercado Extra" in h["summary"]
    q("update launches set valor = 5000, alvo = 'Aluguel' where user_id = %s and id = %s returning id", (uid_pro, lid))
    antes = categoria(uid_pro, lid)
    r = sim(uid_pro)
    assert "mudou desde a pergunta" in r and "Me pede de novo" in r, r
    assert existe(uid_pro, lid) and categoria(uid_pro, lid) == antes


@pytest.mark.parametrize("campo", ["valor", "alvo", "data"])
def test_cada_campo_do_resumo_conta(uid_pro, campo):
    lid, _ = lancar(uid_pro)
    arma(uid_pro, "delete_launch", {"launch_id": f"l{lid}"})
    novo = {"valor": "valor = 51", "alvo": "alvo = 'Outro'", "data": "criado_em = criado_em - interval '3 days'"}[campo]
    q(f"update launches set {novo} where user_id = %s and id = %s returning id", (uid_pro, lid))
    assert "mudou desde a pergunta" in sim(uid_pro) and existe(uid_pro, lid)


def test_item_igual_apaga(uid_pro):  # controle positivo
    lid, _ = lancar(uid_pro)
    arma(uid_pro, "delete_launch", {"launch_id": f"l{lid}"})
    assert "Apagado" in sim(uid_pro) and not existe(uid_pro, lid)


# ── A4: a corrida entre a releitura e o lock ─────────────────────────────────────────────────

def _velho(monkeypatch, sql, params):
    """`um_item` devolve o item COMO ESTAVA e só depois o banco muda: a janela entre a releitura e o lock."""
    real = lancamentos_leitura.um_item

    def lento(u, t, n):
        item = real(u, t, n)
        q(sql, params(u, n))
        return item

    monkeypatch.setattr(lancamentos_leitura, "um_item", lento)


def test_corrida_apagar_l(uid_pro, monkeypatch):
    db.add_launch_and_update_balance(uid_pro, "receita", 1000, "seed", None)
    lid, _ = lancar(uid_pro)
    arma(uid_pro, "delete_launch", {"launch_id": f"l{lid}"})
    _velho(monkeypatch, "update launches set origem = null where user_id = %s and id = %s returning id",
           lambda u, n: (u, n))
    saldo = db.get_balance(uid_pro)
    assert "mudou desde a pergunta" in sim(uid_pro)
    assert existe(uid_pro, lid) and db.get_balance(uid_pro) == saldo


def test_corrida_recategorizar_l(uid_pro, monkeypatch):
    lid, _ = lancar(uid_pro)
    antes = categoria(uid_pro, lid)
    arma(uid_pro, "recategorize_launch", {"launch_id": f"l{lid}", "new_category": "lazer"})
    _velho(monkeypatch, "update launches set origem = null where user_id = %s and id = %s returning id",
           lambda u, n: (u, n))
    assert "mudou desde a pergunta" in sim(uid_pro)
    assert categoria(uid_pro, lid) == antes


def test_corrida_recategorizar_c(uid_pro, monkeypatch):
    cartao = db.create_card(uid_pro, "Nubank", closing_day=31, due_day=10)
    c = db.add_imported_credit_purchase(uid_pro, cartao, 40, "mercado", date.today(), "ct-corrida")[0]
    antes = q("select categoria from credit_transactions where user_id = %s and id = %s", (uid_pro, c))["categoria"]
    arma(uid_pro, "recategorize_launch", {"launch_id": f"c{c}", "new_category": "lazer"})
    _velho(monkeypatch, "update credit_transactions set source = 'manual' where user_id = %s and id = %s returning id",
           lambda u, n: (u, n))
    assert "mudou desde a pergunta" in sim(uid_pro)
    assert q("select categoria from credit_transactions where user_id = %s and id = %s",
             (uid_pro, c))["categoria"] == antes


# ── A5: categoria ausente ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("extra", [{}, {"new_category": ""}, {"new_category": "  "}, {"new_category": None},
                                   {"new_category": 5}, {"new_category": ["lazer"]}, {"new_category": "!!!"}])
def test_categoria_ausente_nao_arma(uid_pro, extra):
    lid, _ = lancar(uid_pro)
    h, final = arma(uid_pro, "recategorize_launch", {"launch_id": f"l{lid}", **extra})
    assert final and "Faltou a categoria" in final and pend(uid_pro) is None, final


def test_categoria_nova_ainda_arma(uid_pro):  # controle positivo: a recusa não pega categoria que não existe ainda
    lid, _ = lancar(uid_pro)
    h, final = arma(uid_pro, "recategorize_launch", {"launch_id": f"l{lid}", "new_category": "Pet shop"})
    assert final is None and "pet shop" in h["summary"].lower()


# ── A7: o item some entre o validate e o ao_confirmar ────────────────────────────────────────

def test_item_que_some_entre_validate_e_ao_confirmar_diz_nao_achei_mais(uid_pro, monkeypatch):
    lid, _ = lancar(uid_pro)
    real, n = lancamentos_leitura.um_item, {"i": 0}

    def some(u, t, i):
        n["i"] += 1
        return None if n["i"] >= 2 else real(u, t, i)  # 1ª = validate; do ao_confirmar em diante, sumiu

    monkeypatch.setattr(lancamentos_leitura, "um_item", some)
    h, final = arma(uid_pro, "delete_launch", {"launch_id": f"#{_seq(uid_pro, lid)}"})
    assert pend(uid_pro)["tool_args"]["launch_id"] == f"l{lid}"  # canônico, não o `#N` cru
    assert "Não achei mais" in sim(uid_pro)


def _seq(uid, lid):
    return q("select user_seq from launches where user_id = %s and id = %s", (uid, lid))["user_seq"]
