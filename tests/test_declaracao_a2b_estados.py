"""PR-A2b-1: a máquina de estados da declaração do fluxo (sem par / casada pelo sync / casada à mão) nos
caminhos de borda: escolha repetida, troca para o mesmo par ou para valor diferente, desfazer duas vezes,
par sumido do banco, receita, e o catálogo de categorias que o desfazer devolve.
Todo teste compara o `retrato` do usuário (tests/_retrato_declaracao.py), nunca só o status HTTP."""
from __future__ import annotations

from datetime import timedelta

import pytest

import db.bank_conferencia as conferencia
from conftest import promote_to_pro
from tests._patrimonio_helpers import q
from tests._retrato_declaracao import em, retrato, t, tid
from tests.test_bank_movements import _bank
from tests.test_declaracao_a2b_acoes import H, T1, T2, ana, cliente, confirma, desfaz
from tests.test_declaracao_fluxo_reconcilia import declara, sincroniza


def _decl_row(lid):
    return q("select * from bank_movement_declarations where launch_id=%s", (lid,))


def test_escolher_o_mesmo_par_automatico_liga_a_flag(user_id):
    """DP-3=b: aba velha, o sync já casou T1 e o usuário escolhe T1: a escolha também é manual."""
    cid, d, c = ana(user_id)
    assert (_decl_row(d)["manual_only"], _decl_row(d)["confirmation_method"]) == (False, "automatic")
    assert confirma(c, user_id, d, "T1").status_code == 200
    assert (_decl_row(d)["manual_only"], _decl_row(d)["confirmation_method"]) == (True, "manual")


def test_trocar_para_o_mesmo_par_automatico(user_id):
    cid, d, c = ana(user_id)
    assert confirma(c, user_id, d, "T1", troca="T1").status_code == 200
    assert _decl_row(d)["manual_only"] is True


def test_escolha_do_mesmo_par_automatico_trava_o_sync(user_id):
    """O efeito da flag: o banco corrige T1 e a declaração não religa sozinha em outra transação."""
    cid, d, c = ana(user_id)
    assert confirma(c, user_id, d, "T1").status_code == 200
    sincroniza(cid, user_id, t("T1", -60, 0, "SUPERMERCADO XYZ"), T2, t("T3", -50, 0, "SUPERMERCADO ABC"))
    assert [x[2] for x in retrato(user_id)["D"]] == [None]


def test_desfazer_duas_vezes_e_com_par_errado(user_id):
    cid, d, c = ana(user_id)
    assert desfaz(c, user_id, d, "T1").json() == {"ok": True, "changed": True}
    apos = retrato(user_id)
    assert desfaz(c, user_id, d, "T1").json() == {"ok": True, "changed": False}
    assert desfaz(c, user_id, d, "T2").json() == {"ok": True, "changed": False}
    assert retrato(user_id) == apos


def test_desfazer_em_declaracao_sem_par_trava_o_sync(user_id):
    """Sem par e sem evidência: desfazer responde changed=false, liga manual_only, e o sync seguinte não liga."""
    promote_to_pro(user_id)
    cid, _ = _bank(user_id)
    c = cliente(user_id)
    d = declara(user_id, criado_em=em(0))
    sincroniza(cid, user_id, t("F", -50, 0, "FARMACIA"))
    assert c.post(f"/open-finance/{user_id}/movements/undo", json={"launch_id": d, "transaction_id": tid(user_id, "F")},
                  headers=H).json() == {"ok": True, "changed": False}
    assert _decl_row(d)["manual_only"] is True
    sincroniza(cid, user_id, t("F", -50, 0, "FARMACIA"), t("M", -50, 0, "SUPERMERCADO XYZ"))
    assert [x[2] for x in retrato(user_id)["D"]] == [None]


def test_banco_apagou_a_transacao_do_par(user_id):
    """O par some do extrato (FK set null): desfazer com o id antigo é no-op; trocar com ele é 409."""
    cid, d, c = ana(user_id)
    t1 = tid(user_id, "T1")
    q("update open_finance_transactions set imported_launch_id=null, match_launch_id=null where id=%s returning 1", (t1,))
    q("delete from open_finance_transactions where id=%s returning 1", (t1,))
    assert _decl_row(d)["matched_transaction_id"] is None
    r = c.post(f"/open-finance/{user_id}/movements/undo", json={"launch_id": d, "transaction_id": t1}, headers=H)
    assert r.status_code == 200, r.text
    r = c.post(f"/open-finance/{user_id}/movements/confirm",
               json={"launch_id": d, "transaction_id": tid(user_id, "T2"), "replace_transaction_id": t1}, headers=H)
    assert r.status_code == 409, r.text


def test_troca_para_valor_diferente_e_volta(user_id):
    """R$ 54 no banco vs R$ 50 declarado: a troca projeta o valor do banco, a volta e o desfazer restauram R$ 50."""
    cid, d, c = ana(user_id)
    sincroniza(cid, user_id, T1, T2, t("T9", -54, 1, "OUTRO"))
    assert confirma(c, user_id, d, "T9", troca="T1").status_code == 200
    assert ("decl mercado", "despesa", 54.0, 1, "manual", "0.0", None) in retrato(user_id)["L"]
    assert confirma(c, user_id, d, "T1", troca="T9").status_code == 200
    assert ("decl mercado", "despesa", 50.0, 0, "manual", "0.0", None) in retrato(user_id)["L"]
    assert desfaz(c, user_id, d, "T1").status_code == 200
    assert ("decl mercado", "despesa", 50.0, 0, "manual", "0.0", None) in retrato(user_id)["L"]


@pytest.mark.parametrize("amount,desc,cat", [(50, "ESTORNO", "Groceries"), (-50, "TRANSFERENCIA", "Same person transfer")])
def test_trocar_para_tx_incompativel(user_id, amount, desc, cat):
    cid, d, c = ana(user_id)
    sincroniza(cid, user_id, T1, T2, t("TX", amount, 1, desc, cat))
    antes = retrato(user_id)
    assert confirma(c, user_id, d, "TX", troca="T1").status_code == 409
    assert retrato(user_id) == antes


def test_trocar_para_tx_ja_casada_por_outra_declaracao(user_id):
    cid, d1, c = ana(user_id)
    declara(user_id, criado_em=em(1), alvo="pix maria", categoria="outros")
    sincroniza(cid, user_id, T1, T2)
    antes = retrato(user_id)
    assert sorted(x[2] for x in antes["D"]) == ["T1", "T2"], antes
    assert confirma(c, user_id, d1, "T2", troca="T1").status_code == 409
    assert retrato(user_id) == antes


def test_escolher_outro_com_par_atual_409_e_replace_em_sem_par_409(user_id):
    cid, d, c = ana(user_id)
    antes = retrato(user_id)
    assert confirma(c, user_id, d, "T2").status_code == 409
    assert retrato(user_id) == antes
    assert desfaz(c, user_id, d, "T1").status_code == 200
    apos = retrato(user_id)
    assert confirma(c, user_id, d, "T2", troca="T1").status_code == 409  # o par visto não é mais o atual
    assert retrato(user_id) == apos


def test_receita_troca_e_desfaz(user_id):
    promote_to_pro(user_id)
    q("update accounts set balance=1000 where user_id=%s returning 1", (user_id,))
    cid, _ = _bank(user_id)
    c = cliente(user_id)
    d = declara(user_id, tipo="receita", criado_em=em(0), alvo="salario", categoria="salario")
    r1, r2 = t("R1", 50, 0, "SALARIO EMPRESA", "Salary"), t("R2", 50, 1, "PIX JOAO", "Transfer")
    sincroniza(cid, user_id, r1, r2)
    assert [x[2] for x in retrato(user_id)["D"]] == ["R1"]
    assert confirma(c, user_id, d, "R2", troca="R1").status_code == 200
    assert desfaz(c, user_id, d, "R2").status_code == 200
    ret = retrato(user_id)
    assert ret["C"] == 1000.0
    assert sorted(x[1] for x in ret["L"]) == ["receita", "receita", "receita"], ret
    sincroniza(cid, user_id, r1, r2)
    assert [x[2] for x in retrato(user_id)["D"]] == [None]  # só manual: o sync não religa


def test_apagar_declaracao_desfeita_nao_duplica_sombra(user_id):
    cid, d, c = ana(user_id)
    assert desfaz(c, user_id, d, "T1").status_code == 200
    assert c.delete(f"/launches/{user_id}/{d}", headers=H).status_code == 200
    sincroniza(cid, user_id, T1, T2)
    ret = retrato(user_id)
    assert sorted(x[0] for x in ret["L"]) == ["sombra T1", "sombra T2"], ret
    assert ret["D"] == [] and ret["saiu"] == 100.0


def test_desfazer_garante_a_categoria_da_sombra_no_catalogo(user_id):
    """A sombra devolvida pelo desfazer usa a categoria do banco; se ela é nova no catálogo, nasce lá (como no apagar)."""
    promote_to_pro(user_id)
    cid, _ = _bank(user_id)
    c = cliente(user_id)
    d = declara(user_id, criado_em=em(0), alvo="loja", categoria="compras")
    sincroniza(cid, user_id, t("S1", -50, 0, "LOJA CENTRO", "Shopping"))
    assert [x[2] for x in retrato(user_id)["D"]] == ["S1"]
    q("delete from user_categories where user_id=%s and name='compras' returning 1", (user_id,))
    assert desfaz(c, user_id, d, "S1").status_code == 200
    assert q("select count(*) n from user_categories where user_id=%s and name='compras'", (user_id,))["n"] == 1


def test_previsao_e_motivos_apos_acoes(user_id):
    """Desfazer abre o motivo "a conferir" na previsão; escolher e trocar de volta o fecham."""
    from core.services.cashflow_snapshot import carregar
    from utils_date import today_tz
    cid, d, c = ana(user_id)
    hoje = today_tz()

    def snap():
        s = carregar(user_id, hoje, hoje + timedelta(days=30))
        return s.base.get("saldo"), sorted(str(m.codigo if hasattr(m, "codigo") else m) for m in s.motivos)
    s0 = snap()
    assert desfaz(c, user_id, d, "T1").status_code == 200
    s1 = snap()
    assert confirma(c, user_id, d, "T2").status_code == 200
    s2 = snap()
    assert confirma(c, user_id, d, "T1", troca="T2").status_code == 200
    assert s0 == s2 == snap()
    assert "declaracao_bancaria_a_conferir" in str(s1) and "declaracao_bancaria_a_conferir" not in str(s0)
