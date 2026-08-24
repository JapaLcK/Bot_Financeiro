"""Testes do script de limpeza de regras envenenadas.

Separado de `test_custom_category_infer.py` porque o script vive em PR próprio:
ele é operação destrutiva rodada à mão em produção, e o resto daquele arquivo
cobre o caminho de consulta, que é verificável aqui.

`find_poisoned` é o predicado que SELECIONA linhas para DELETE — e é testável
localmente, ao contrário do `_delete_rule`, que só se prova em produção.
"""
import pytest

from db.categories import create_user_category


def test_cleanup_script_apply_exige_user(monkeypatch):
    # Codex P1: --apply global apagaria regras criadas de propósito (sem coluna
    # de proveniência). --apply exige --user pra forçar revisão cliente a cliente.
    import sys
    from scripts.cleanup_poisoned_category_rules import main
    monkeypatch.setattr(sys, "argv", ["cleanup", "--apply"])
    with pytest.raises(SystemExit):
        main()


def test_find_poisoned_seleciona_so_a_regra_que_rouba_a_custom(pro_user_id):
    # Observação 4 do Manager: `find_poisoned` é o predicado que SELECIONA linhas
    # pro DELETE de scripts/cleanup_poisoned_category_rules.py e não tinha teste
    # nenhum. Só o `_delete_rule` depende de produção; a seleção roda aqui.
    from db.categories import upsert_category_rule
    from scripts.cleanup_poisoned_category_rules import find_poisoned

    create_user_category(pro_user_id, "gastos com minha namorada")
    upsert_category_rule(pro_user_id, "namorada", "lazer")          # VENENO
    upsert_category_rule(pro_user_id, "cinema", "lazer")            # neutra
    upsert_category_rule(
        pro_user_id, "namorada jantar", "gastos com minha namorada"  # aponta pra própria
    )

    achados = find_poisoned(pro_user_id)
    assert [(kw, cat) for kw, cat, _ in achados] == [("namorada", "lazer")], achados
    assert achados[0][2] == "gastos com minha namorada"


def test_find_poisoned_vazio_sem_categoria_custom(pro_user_id):
    # Sem categoria custom não há o que roubar: nenhuma regra é selecionada.
    # Guarda contra a pior falha possível num script de DELETE — selecionar demais.
    from db.categories import upsert_category_rule
    from scripts.cleanup_poisoned_category_rules import find_poisoned

    upsert_category_rule(pro_user_id, "namorada", "lazer")
    upsert_category_rule(pro_user_id, "cinema", "lazer")
    assert find_poisoned(pro_user_id) == []
