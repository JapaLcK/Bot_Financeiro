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


def _conta(tabela: str) -> int:
    import db
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"select count(*) as n from {tabela}")
        r = cur.fetchone()
    return r["n"] if isinstance(r, dict) else r[0]


def _existe(user_id: int) -> bool:
    # SELECT local de propósito: importar o `_user_existe` do script faria o
    # teste morrer de ImportError antes de chegar na asserção que interessa —
    # e ImportError não prova nada sobre escrita no banco.
    import db
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select 1 from users where id=%s", (user_id,))
        return cur.fetchone() is not None


def _id_inexistente() -> int:
    import db
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select coalesce(max(id), 0) as m from users")
        r = cur.fetchone()
    m = r["m"] if isinstance(r, dict) else r[0]
    return int(m) + 1000          # sempre livre, mesmo depois de um run sujo


def test_dry_run_com_user_inexistente_nao_escreve(monkeypatch):
    # Codex P2: `list_user_category_rules` chama `ensure_user`, que INSERE em
    # `users` e em `accounts` e commita. O dry-run — a única defesa deste script
    # destrutivo — criava em produção o cliente que o operador digitou errado.
    import sys
    from scripts.cleanup_poisoned_category_rules import main

    fantasma = _id_inexistente()
    assert not _existe(fantasma)
    antes = (_conta("users"), _conta("accounts"))

    monkeypatch.setattr(sys, "argv", ["cleanup", "--user", str(fantasma)])
    try:
        main()
        abortou = False
    except SystemExit:
        abortou = True

    assert not _existe(fantasma), "o dry-run CRIOU o usuário inexistente"
    assert (_conta("users"), _conta("accounts")) == antes, "o dry-run ESCREVEU no banco"
    assert abortou, "--user inexistente tem que abortar, não reportar '0 regras'"


def test_dry_run_global_nao_escreve(monkeypatch, pro_user_id):
    # A varredura global é o outro caminho do mesmo `ensure_user` — não corrigir
    # só a instância que o revisor citou. Este já passava ANTES (`_all_user_ids`
    # só devolve id que existe, e o insert é `on conflict do nothing`): é guarda
    # de que a varredura continue read-only, não discriminante do P2.
    import sys
    from db.categories import upsert_category_rule
    from scripts.cleanup_poisoned_category_rules import main

    create_user_category(pro_user_id, "gastos com minha namorada")
    upsert_category_rule(pro_user_id, "namorada", "lazer")
    antes = (_conta("users"), _conta("accounts"), _conta("user_category_rules"))

    monkeypatch.setattr(sys, "argv", ["cleanup"])
    main()

    assert (_conta("users"), _conta("accounts"), _conta("user_category_rules")) == antes
