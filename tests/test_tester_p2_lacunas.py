"""Tester P2: lacunas de cobertura de `tests/test_previsao_variavel.py` (cada teste abaixo passa hoje e fica
vermelho com a mutação indicada no código de produção; as mutações sobreviviam à suíte do Coder)."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal as D

from tests.test_previsao_variavel import (HOJE, Previsao, cartao, gasto, historico, ler_em, mensal, previsao_v2, pro, q,  # noqa: F401
                                          tres_janelas)


def test_historico_de_outro_usuario_nao_define_o_inicio_do_meu(user_id):
    """Mutação: tirar `user_id` das subqueries de `_inicio_do_historico`. Sem isto o gasto de 5 dias de um
    usuário novo ganha 90 dias de histórico (e a mediana de 3 janelas o zera) por causa da conta de outro."""
    from conftest import usuario_pagante
    outro = usuario_pagante("pro_max")
    historico(outro)  # 100 dias atrás, na conta alheia
    cartao(outro, [(1, 100, 10, {})])  # e na compra de cartão alheia (outra subquery)
    gasto(user_id, 1, 10, categoria="salario", tipo="receita")
    gasto(user_id, 70, 5)
    e = ler_em(user_id).estimativa_variavel
    assert e["dias_de_historico"] == 10 and e["janelas_completas"] == 0
    assert e["despesa_mensal"] == D("210.00")  # 70 em 10 dias × 30/10


def test_nome_da_categoria_vem_de_user_categories_e_a_ordem_e_do_maior_para_o_menor(user_id):
    """Mutações: `nomes.get(c, c)` -> `c`; `sorted(...)` -> ordem do dict."""
    historico(user_id)
    q("insert into user_categories (user_id, name, is_system) values (%s, 'Café da manhã', false)", (user_id,))
    for i in range(3):  # o café é o mais recente: sem a ordenação ele viria primeiro
        gasto(user_id, 10, 5 + 30 * i, categoria="cafe da manha")
        gasto(user_id, 500, 10 + 30 * i, categoria="aluguelzao")
    cats = ler_em(user_id).estimativa_variavel["categorias"]
    assert [c["chave"] for c in cats] == ["aluguelzao", "cafe da manha"]
    assert cats[1]["nome"] == "Café da manhã"


def test_despesa_no_horizonte_e_pior_dia_em_dias_diferentes_de_90(pro):
    """Mutações: `despesa_no_horizonte` fixo em ×3 (só dá certo com dias=90); `range(topo + 1, ...)` -> `range(0, ...)`."""
    from utils_date import now_tz
    hoje = now_tz().date()
    historico(pro, hoje=hoje)
    tres_janelas(pro, (300, 300, 300), hoje=hoje)
    q("update accounts set balance=10000 where user_id=%s", (pro,))
    for dias, horizonte in ((30, "300.00"), (60, "600.00")):
        c = Previsao.model_validate(previsao_v2.ler_previsao(pro, dias, (30, 60), True)).model_dump(mode="json")
        assert c["estimativa_variavel"]["despesa_no_horizonte"] == horizonte
        assert c["pior_dia"]["despesa_variavel"] == horizonte  # saldo só desce: pico = hoje, pior = último dia


def test_pior_dia_soma_a_estimativa_so_depois_do_ultimo_pico():
    """Mutação: `range(topo + 1, worst_i + 1)` -> `range(0, ...)`. Um salário no dia 10 faz o pico ali; só os 80
    dias seguintes (não os 90) explicam a queda até o pior dia."""
    from core.services.cashflow_contract import Ocorrencia
    from tests.test_previsao_variavel import snap, _trajectory
    salario = Ocorrencia("receita_recorrente", 1, "c", HOJE + timedelta(days=10), "receita", "Salario", D(2000), "entrada")
    s = snap(1000, 3000, [salario])
    t = _trajectory(HOJE, s.base, s.ocorrencias, 90, 0)
    assert t["worst_day"]["date"] == (HOJE + timedelta(days=90)).isoformat()
    assert t["worst_day"]["despesa_variavel"] == D("8000.00")  # dias 11..90
