"""PR-B (ADR 0006): os textos T1 a T17 (sem T13), aprovados pelo dono em 2026-10-26 (DP-1), 2026-10-27 (T9, T10, T11, variante
do T1) e 2026-10-28 (T12, T14, T15, issue #1310; T16, T17 e o T12 reescrito pela E3), exatos, e nenhum deixa vazar o
código `DECLARACAO_*` do escritor. Pino de texto (como `test_declaracao_a2c_texto.py`): sem controle de mutação.
T1/T2/T3/T4/T6/T7/T8/T9 saem pela conversa; T5 (defesa, raro) pela tradução do erro do escritor.
"""
from __future__ import annotations

import db
from core.handlers import declaracao_banco as decl
from tests._declaracao_b import ana, liga, manda_wamid  # noqa: F401
from tests._fusao_of_helpers import uid_pro  # noqa: F401
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from tests._retrato_declaracao import BASE, t
from tests.test_bank_movements import _bank
from tests.test_declaracao_fluxo_reconcilia import sincroniza
from utils_date import today_tz

T1 = ("💸 *Despesa registrada pelo banco*: R$ 50,00\n🏷️ Categoria: mercado\n"
      "🕓 Ainda não apareceu no seu extrato. Quando aparecer, eu confiro sozinho. Sua Carteira não mudou.\nID: #1")
T4 = ("🐷 Não registrei: o valor aceita no máximo 2 casas decimais (você mandou 50,123). "
      "Manda de novo, por exemplo: *gastei 50,12 no mercado pelo banco*.")
T6 = ("🐷 Não registrei como pelo banco: isso parece uma movimentação interna (investimento, transferência entre as "
      "suas contas ou pagamento de fatura), e o banco já mostra esse tipo de movimento sozinho. "
      "Se foi em dinheiro vivo, diga *em dinheiro*.")
T7 = ("🐷 Mandou dinheiro e banco na mesma mensagem. Manda separado: uma mensagem com o que foi em dinheiro e outra "
      "com o que passou pelo banco (Pix, débito, transferência).")
T8 = "🐷 Não registrei: as compras no cartão de crédito conectado chegam sozinhas pelo Open Finance."
T9 = ("🐷 Não consegui confirmar seu banco agora, então não registrei nada. "
      "Tenta de novo em instantes, mandando o gasto inteiro.")


def test_t1_despesa_sem_par(ana, liga):
    manda_wamid(ana, "gastei 50 no mercado")
    assert manda_wamid(ana, "banco") == [T1 + "\n\n🎉 Esse foi seu primeiro lançamento — viu como é rápido?\n"
                                          "Agora tenta *saldo*. Quando quiser, tem caixinhas, cartões e dashboard: "
                                          "é só mandar *ajuda*."]


def test_t1_receita(ana, liga):
    manda_wamid(ana, "recebi 1000 de salário")
    r = manda_wamid(ana, "pix")[0]
    assert r.startswith("💰 *Receita registrada pelo banco*: R$ 1.000,00\n🏷️ Categoria: outros\n🕓 Ainda não apareceu"), r


def test_t2_casou_na_hora(ana, liga):
    cid, _ = _bank(ana)
    dia = (today_tz() - BASE).days
    sincroniza(cid, ana, t("T1", -50, dia, "SUPERMERCADO XYZ"))
    manda_wamid(ana, "gastei 50 no mercado")
    r = manda_wamid(ana, "banco")[0]
    esperado = (f"💸 *Despesa registrada pelo banco*: R$ 50,00\n🏷️ Categoria: mercado\n"
                f"✅ Já estava no seu extrato e conferi: {today_tz():%d/%m} · SUPERMERCADO XYZ · R$ 50,00. "
                f"Sua Carteira não mudou.\nID: #2")  # #1 foi da sombra que a declaração substituiu
    assert r.startswith(esperado), r


def test_t3_reentrega(ana, liga):
    manda_wamid(ana, "gastei 50 no mercado no pix", "wamid.t3")
    assert manda_wamid(ana, "gastei 50 no mercado no pix", "wamid.t3") == ["🐷 Esse eu já tinha registrado (ID #1). Não dupliquei."]


def test_t4_mais_de_duas_casas(ana, liga):
    manda_wamid(ana, "gastei 50,123 no mercado")
    assert manda_wamid(ana, "banco") == [T4]


def test_t5_valor_nao_positivo_no_escritor():
    assert decl.recusa(ValueError("DECLARACAO_VALOR_INVALIDO: o valor precisa ser um número maior que zero (recebido: 0)"),
                       1, 0) == "🐷 Não registrei: o valor precisa ser um número maior que zero."


def test_t6_categoria_interna(ana, liga):
    assert manda_wamid(ana, "gastei 200 no mercado #investimento no pix") == [T6]


def test_t7_misto_so_para_lancamento(ana, liga):
    assert manda_wamid(ana, "gastei 40 em dinheiro e 60 no pix") == [T7]


def test_t7_a_conta_a_pagar_mantem_o_texto_de_hoje(ana, liga):
    from tests.test_conta_paga_forma import conta_fixa
    conta_fixa(ana, valor=90.0)
    r = manda_wamid(ana, "paguei a luz em dinheiro e no pix")[0]
    assert "o que passou pelo banco chega pelo Open Finance" in r, r


def test_t8_cartao_sincronizado_com_a_flag(ana, liga):
    manda_wamid(ana, "gastei 50 no mercado")
    assert manda_wamid(ana, "cartão")[0].startswith(T8 + "\n\nAinda não apareceu no extrato;")


def test_t8_so_com_a_flag_ligada_o_texto_de_hoje_fica(ana):
    manda_wamid(ana, "gastei 50 no mercado")
    r = manda_wamid(ana, "cartão")[0]
    assert r.startswith("🐷 Não registrei: com banco conectado, Pix, cartão e débito chegam sozinhos pelo Open Finance."), r


def test_t9_sem_como_garantir(ana, liga):
    assert manda_wamid(ana, "gastei 50 no mercado no pix", "sem-wamid") == [T9]


def test_nenhuma_resposta_expoe_o_codigo_do_escritor(ana, liga):
    for frase in ("gastei 50,123 no mercado no pix", "gastei 200 no mercado #investimento no pix", "gastei 0,001 no mercado no pix"):
        assert "DECLARACAO" not in manda_wamid(ana, frase)[0]


def test_t10_valor_abreviado(ana, liga):
    assert manda_wamid(ana, "gastei 2,2k no mercado no pix") == [
        "🐷 Não registrei: não tenho certeza do valor (você mandou 2,2k). "
        "Manda de novo só com números, por exemplo: *gastei 2200 no mercado pelo banco*."]


def test_t1_falha_ao_listar_contas_depois_do_commit_cai_no_t1_de_sempre(uid_pro, ia_fora, liga, monkeypatch):
    """A variante "sem conta corrente" é escolhida pós-commit: se a leitura da lista falha, a resposta é o T1 de sempre
    e a declaração já gravada não se perde. Usuário SEM conta BANK (só cartão), onde a variante apareceria."""
    item = db.save_pluggy_open_finance_item(uid_pro, {"id": f"cc-{uid_pro}", "connector": {"id": 1, "name": "Nu"},
                                                       "status": "UPDATED"})
    db.save_open_finance_sync(item["id"], [{"provider_account_id": f"cc-a-{uid_pro}", "name": "Cartão",
                                            "type": "CREDIT", "currency": "BRL", "balance": 0, "transactions": []}])

    def cai(_uid):
        raise RuntimeError("DB caiu")
    monkeypatch.setattr(db, "list_bank_accounts", cai)
    r = manda_wamid(uid_pro, "gastei 50 no mercado no pix")[0]
    assert "Ainda não apareceu no seu extrato. Quando aparecer" in r and "sem uma conta corrente" not in r, r


def test_t11_valor_ambiguo(ana, liga):
    assert manda_wamid(ana, "gastei 2 reais e 50 no mercado pelo banco") == [
        "🐷 Não registrei: não sei se *2 reais e 50* é um valor só ou dois gastos. "
        "Manda de novo só com números, por exemplo: *gastei 2,50 no mercado pelo banco*."]


def test_t12_nao_sei_qual_numero_e_o_valor(ana, liga):
    assert manda_wamid(ana, "comprei 2 camisetas por 80 pelo banco") == [
        "🐷 Não registrei: não sei qual desses números é o valor. Manda de novo só o valor em número, com *R$* antes; "
        "se o nome tem número, escreve junto (*99pop*). Por exemplo: *gastei R$ 80 na Renner pelo banco*."]


def test_t14_parcelado(ana, liga):
    assert manda_wamid(ana, "paguei R$ 600 em 3x pelo banco") == [
        "🐷 Não registrei: compra parcelada não sai inteira da conta de uma vez. Se você pagou uma parcela agora, "
        "manda só ela, por exemplo: *paguei R$ 200 da parcela da geladeira pelo banco*."]


def test_t15_mensagem_longa(ana, liga):
    assert manda_wamid(ana, "gastei 50 no mercado " + "muito bom " * 100 + "pelo banco") == [
        "🐷 Não registrei: a mensagem ficou longa demais para eu ter certeza do valor. Manda só o gasto, por exemplo: "
        "*gastei 50 no mercado pelo banco*."]


def test_t16_um_gasto_por_mensagem(ana, liga):
    assert manda_wamid(ana, "gastei 20 no mercado e 30 na padaria pelo banco") == [
        "🐷 Não registrei: pelo banco eu registro um gasto por mensagem. Manda cada um separado, por exemplo: "
        "*gastei 20 no mercado pelo banco*."]


def test_t17_valor_aproximado(ana, liga):
    assert manda_wamid(ana, "gastei uns 50 no mercado pelo banco") == [
        "🐷 Não registrei: pelo banco eu preciso do valor exato. Manda de novo com o valor certinho, por exemplo: "
        "*gastei 50 no mercado pelo banco*."]
