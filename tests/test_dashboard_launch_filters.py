from frontend.finance_bot_websocket_custom import _dashboard_launch_filter_sql


def test_dashboard_filter_receita_exclui_movimentacoes_internas():
    clauses, params = _dashboard_launch_filter_sql("receita", "")

    assert clauses == ["tipo IN ('receita', 'entrada') AND is_internal_movement = false"]
    assert params == []


def test_dashboard_filter_investimento_inclui_aportes_e_resgates():
    clauses, params = _dashboard_launch_filter_sql("investimento", "")

    assert clauses == ["tipo IN ('aporte_investimento', 'resgate_investimento')"]
    assert params == []


def test_dashboard_filter_busca_textual_procura_nos_campos_visiveis():
    clauses, params = _dashboard_launch_filter_sql("all", "  Mercado  ")

    assert len(clauses) == 1
    assert "lower(coalesce(nota, '')) LIKE %s" in clauses[0]
    assert "lower(coalesce(alvo, '')) LIKE %s" in clauses[0]
    assert "lower(coalesce(categoria, '')) LIKE %s" in clauses[0]
    assert "lower(coalesce(tipo, '')) LIKE %s" in clauses[0]
    assert params == ["%mercado%", "%mercado%", "%mercado%", "%mercado%"]
