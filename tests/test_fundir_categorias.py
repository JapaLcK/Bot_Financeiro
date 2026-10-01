"""Issue #149, PR C: prevenção de gêmeas no catálogo, seed sem linhas do OF e o
script `scripts/fundir_categorias.py`. Banco real; o estado legado entra por SQL."""
from __future__ import annotations

import uuid
from datetime import date

import pytest

import db
from conftest import _cleanup_user
from db.categories import (
    create_user_category,
    ensure_user_categories_seeded,
    update_user_category,
)
from scripts.fundir_categorias import fundir_usuario, main


def _q(sql, params=()):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.description else None
        conn.commit()
    return rows


def _cat(uid, nome):
    """Linha legada do catálogo, como o 1º seed a deixou."""
    return _q("insert into user_categories (user_id, name, emoji, color, is_system, created_at) "
              "values (%s, %s, '🏷️', '#7c3aed', false, to_timestamp(0)) returning id",
              (uid, nome))[0]["id"]


def _launch(uid, cat, source="manual"):
    return _q("insert into launches (user_id, tipo, valor, categoria, source, external_id) "
              "values (%s, 'despesa', 10, %s, %s, %s) returning id",
              (uid, cat, source, uuid.uuid4().hex))[0]["id"]


def _credito_of(uid, cat):
    card = db.create_card(uid, f"Cartao {uuid.uuid4().hex[:6]}", 10, 17)
    tx_id, _ = db.add_imported_credit_purchase(uid, card, -10, cat, date.today(), uuid.uuid4().hex)
    return tx_id


def _regra(uid, kw, cat):
    _q("insert into user_category_rules (user_id, keyword, category) values (%s,%s,%s)", (uid, kw, cat))


def _recorrente(uid, cat):
    _q("insert into recurring_expenses (user_id, name, amount, category, due_day, payment_type) "
       "values (%s, 'x', 10, %s, 5, 'account')", (uid, cat))


def _receita(uid, cat):
    _q("insert into recurring_incomes (user_id, name, amount, category, pay_day) "
       "values (%s, 'x', 10, %s, 5)", (uid, cat))


def _orcamento(uid, cat):
    _q("insert into category_budgets (user_id, categoria, budget) values (%s,%s,100)", (uid, cat))


def _alerta(uid, cat, ym="2026-09", threshold=80):
    _q("insert into budget_alert_sent (user_id, categoria, ym, threshold) values (%s,%s,%s,%s)",
       (uid, cat, ym, threshold))


def _arquiva(uid, nome):
    _q("update user_categories set is_archived=true where user_id=%s and name=%s", (uid, nome))


def _nomes(uid):
    return sorted(r["name"] for r in _q("select name from user_categories where user_id=%s", (uid,)))


def _snapshot(uid):
    tabelas = {
        "user_categories": "id, name, is_system, is_archived",
        "launches": "id, categoria, is_internal_movement",
        "credit_transactions": "id, categoria",
        "user_category_rules": "keyword, category",
        "recurring_expenses": "id, category",
        "category_budgets": "id, categoria",
        "budget_alert_sent": "categoria, ym, threshold",
        "recurring_incomes": "id, category",
    }
    return {t: _q(f"select {c} from {t} where user_id=%s order by 1", (uid,)) for t, c in tabelas.items()}


@pytest.fixture()
def uid(pro_user_id):
    ensure_user_categories_seeded(pro_user_id)
    return pro_user_id


@pytest.fixture()
def outro_uid():
    u = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(u)
    ensure_user_categories_seeded(u)
    yield u
    _cleanup_user(u)


# ─── 1. prevenção ────────────────────────────────────────────────────────────


def test_criar_gemea_de_grafia_e_recusado(uid):
    create_user_category(uid, "Café")
    with pytest.raises(ValueError, match="CATEGORIA_DUPLICADA"):
        create_user_category(uid, "cafe")
    with pytest.raises(ValueError, match="CATEGORIA_DUPLICADA"):
        create_user_category(uid, "Alimentacao")   # gêmea da "alimentação" do sistema


def test_nomes_distintos_continuam_passando(uid):
    """Controle positivo: emoji puro não colapsa em "", e prefixo não é gêmea."""
    create_user_category(uid, "☕")
    create_user_category(uid, "🍕")
    create_user_category(uid, "café")
    create_user_category(uid, "Café da Manhã")
    assert {"☕", "🍕", "café", "café da manhã"} <= set(_nomes(uid))


def test_rename_para_gemea_e_recusado_e_o_proprio_acento_passa(uid):
    create_user_category(uid, "café")
    cat = create_user_category(uid, "padaria")
    with pytest.raises(ValueError, match="CATEGORIA_DUPLICADA"):
        update_user_category(uid, cat["id"], new_name="Cafe")
    padaria = update_user_category(uid, cat["id"], new_name="padariá")   # a própria linha
    assert padaria["name"] == "padariá"


# ─── 2. inglês antes da sync ────────────────────────────────────────────────


def _estado_groceries(uid):
    _cat(uid, "groceries")
    lid = _launch(uid, "Groceries", "open_finance")
    tx = _credito_of(uid, "groceries")
    return lid, tx


def test_dry_run_nao_escreve_e_relata(uid):
    _estado_groceries(uid)
    _alerta(uid, "groceries")
    _alerta(uid, "mercado")   # colide: o DELETE do alerta órfão também tem de ser desfeito
    antes = _snapshot(uid)
    rel = fundir_usuario(uid, aplicar=False)
    assert _snapshot(uid) == antes
    assert [(de, para) for de, para, _ in rel["fundidas"]] == [("groceries", "mercado")]
    assert rel["fundidas"][0][2]["launches"] == 1


def test_apply_funde_ingles_no_sistema_e_e_idempotente(uid):
    lid, tx = _estado_groceries(uid)
    mercado = _q("select id, emoji, is_system from user_categories where user_id=%s and name='mercado'", (uid,))
    fundir_usuario(uid, aplicar=True)

    assert _q("select categoria from launches where id=%s", (lid,))[0]["categoria"] == "mercado"
    assert _q("select categoria from credit_transactions where id=%s", (tx,))[0]["categoria"] == "mercado"
    assert "groceries" not in _nomes(uid)
    assert _q("select id, emoji, is_system from user_categories where user_id=%s and name='mercado'", (uid,)) == mercado

    antes = _snapshot(uid)
    rel = fundir_usuario(uid, aplicar=True)
    assert rel["fundidas"] == [] and _snapshot(uid) == antes


# ─── 3/4. destino ausente e destino interno ─────────────────────────────────


def test_destino_ausente_nasce_no_catalogo(uid):
    _cat(uid, "shopping")
    lid = _launch(uid, "shopping", "open_finance")
    rel = fundir_usuario(uid, aplicar=True)
    assert rel["criar"] == ["compras"]
    assert _q("select categoria from launches where id=%s", (lid,))[0]["categoria"] == "compras"
    assert "compras" in _nomes(uid) and "shopping" not in _nomes(uid)


def test_dry_run_diz_criaria_e_nao_cria(uid, capsys):
    _cat(uid, "shopping")
    _launch(uid, "shopping", "open_finance")
    antes = _snapshot(uid)
    assert fundir_usuario(uid, aplicar=False)["criar"] == ["compras"]
    assert _snapshot(uid) == antes and "compras" not in _nomes(uid)
    main(["--user", str(uid)])
    assert "criaria 'compras'" in capsys.readouterr().out
    assert "compras" not in _nomes(uid)


def test_destino_interno_nao_nasce_no_catalogo(uid):
    _cat(uid, "credit card payment")
    lid = _launch(uid, "credit card payment", "open_finance")
    antes = _q("select is_internal_movement from launches where id=%s", (lid,))
    rel = fundir_usuario(uid, aplicar=True)
    assert rel["criar"] == []
    assert _q("select categoria from launches where id=%s", (lid,))[0]["categoria"] == "pagamento_fatura"
    assert _q("select is_internal_movement from launches where id=%s", (lid,)) == antes
    assert "pagamento_fatura" not in _nomes(uid) and "credit card payment" not in _nomes(uid)


# ─── 5/6. guarda ─────────────────────────────────────────────────────────────


_USO_FORA_DO_OF = {   # uma entrada por linha de `_FORA_DO_OF` no script
    "launch_manual": lambda u: _launch(u, "internet"),
    "credito_manual": lambda u: db.add_credit_purchase(
        u, db.create_card(u, f"Cartao {uuid.uuid4().hex[:6]}", 10, 17), 10, "internet", None, date.today()),
    "regra": lambda u: _regra(u, "vivo fibra", "internet"),
    "recorrente_despesa": lambda u: _recorrente(u, "internet"),
    "recorrente_receita": lambda u: _receita(u, "internet"),
    "orcamento": lambda u: _orcamento(u, "internet"),
}


@pytest.mark.parametrize("uso", list(_USO_FORA_DO_OF))
def test_ingles_com_uso_fora_do_of_e_pulado(uid, uso):
    _cat(uid, "internet")
    _launch(uid, "internet", "open_finance")
    _USO_FORA_DO_OF[uso](uid)
    antes = _snapshot(uid)
    rel = fundir_usuario(uid, aplicar=True)
    assert rel["puladas"] == [("internet", "uso fora do OF (1)")]
    assert _snapshot(uid) == antes
    assert "internet" in _nomes(uid)


def test_ingles_so_com_uso_do_of_vai_para_moradia(uid):
    """Irmão positivo da guarda: o mesmo "internet" sem uso fora do OF é fundido."""
    _cat(uid, "internet")
    lid = _launch(uid, "internet", "open_finance")
    fundir_usuario(uid, aplicar=True)
    assert _q("select categoria from launches where id=%s", (lid,))[0]["categoria"] == "moradia"


@pytest.mark.parametrize("com_launch", [True, False])
def test_gas_com_acento_nao_casa_com_gas_da_pluggy(uid, com_launch):
    """Sem o launch é que o caso discrimina: com ele, a guarda também seguraria."""
    _cat(uid, "gás")
    if com_launch:
        _launch(uid, "gás")
    antes = _snapshot(uid)
    fundir_usuario(uid, aplicar=True)
    assert _snapshot(uid) == antes
    assert "gás" in _nomes(uid)


# ─── 7. gêmeas de grafia ────────────────────────────────────────────────────


def test_gemea_leva_launch_regra_e_recorrente_para_o_sistema(uid):
    _cat(uid, "mercádo")
    lid = _launch(uid, "mercádo")
    _regra(uid, "atacadao", "mercádo")
    _recorrente(uid, "mercádo")
    _receita(uid, "mercádo")
    fundir_usuario(uid, aplicar=True)
    assert _q("select categoria from launches where id=%s", (lid,))[0]["categoria"] == "mercado"
    assert _q("select category from user_category_rules where user_id=%s", (uid,))[0]["category"] == "mercado"
    assert _q("select category from recurring_expenses where user_id=%s", (uid,))[0]["category"] == "mercado"
    assert _q("select category from recurring_incomes where user_id=%s", (uid,))[0]["category"] == "mercado"
    assert "mercádo" not in _nomes(uid)


def test_vence_a_mais_usada(uid):
    _cat(uid, "café")
    _cat(uid, "cafe")
    for _ in range(3):
        _launch(uid, "cafe")
    _launch(uid, "café")
    fundir_usuario(uid, aplicar=True)
    assert "cafe" in _nomes(uid) and "café" not in _nomes(uid)
    assert {r["categoria"] for r in _q("select categoria from launches where user_id=%s", (uid,))} == {"cafe"}


def test_empate_vence_o_menor_id(uid):
    _cat(uid, "pão")
    _cat(uid, "pao")
    fundir_usuario(uid, aplicar=True)
    assert "pão" in _nomes(uid) and "pao" not in _nomes(uid)


def test_orcamento_nas_duas_pula_o_grupo(uid):
    _cat(uid, "cafe")
    _cat(uid, "café")
    _orcamento(uid, "cafe")
    _orcamento(uid, "café")
    antes = _snapshot(uid)
    rel = fundir_usuario(uid, aplicar=True)
    assert rel["puladas"] == [("cafe, café", "orçamento em mais de uma")]
    assert _snapshot(uid) == antes


# ─── 7b. correções do Tester: prefixo, arquivada e alerta órfão ─────────────


def test_custom_com_prefixo_ingles_fica_intacta(uid):
    """ "Travel - Japão" não é chave exata da Pluggy: o fallback " - " a fundiria em "lazer"."""
    _cat(uid, "Travel - Japão")
    antes = _snapshot(uid)
    rel = fundir_usuario(uid, aplicar=True)
    assert rel["fundidas"] == [] and _snapshot(uid) == antes
    main(["--user", str(uid), "--apply"])
    assert "Travel - Japão" in _nomes(uid)


def test_chave_exata_travel_sem_uso_e_fundida(uid):
    """Irmão positivo: "travel" é chave exata e vai para "lazer"."""
    _cat(uid, "travel")
    rel = fundir_usuario(uid, aplicar=True)
    assert [(de, para) for de, para, _ in rel["fundidas"]] == [("travel", "lazer")]
    assert "travel" not in _nomes(uid)


def test_ativa_vence_arquivada_mais_usada(uid):
    _cat(uid, "café")
    velhas = [_launch(uid, "café"), _launch(uid, "café")]
    _arquiva(uid, "café")
    _cat(uid, "cafe")
    fundir_usuario(uid, aplicar=True)
    assert "cafe" in _nomes(uid) and "café" not in _nomes(uid)
    assert {r["categoria"] for r in _q("select categoria from launches where id = any(%s)", (velhas,))} == {"cafe"}
    assert _q("select is_archived from user_categories where user_id=%s and name='cafe'", (uid,))[0]["is_archived"] is False


def test_vencedora_de_sistema_arquivada_com_perdedora_ativa_pula(uid):
    _arquiva(uid, "mercado")
    _cat(uid, "mercádo")
    _launch(uid, "mercádo")
    antes = _snapshot(uid)
    rel = fundir_usuario(uid, aplicar=True)
    assert rel["puladas"] == [("mercado, mercádo", "vencedora arquivada com perdedora ativa")]
    assert _snapshot(uid) == antes


def test_destino_de_sistema_arquivado_funde_o_alias_em_ingles(uid):
    """O alias ativo "groceries" não é concorrente da "mercado" arquivada: é o alvo."""
    _arquiva(uid, "mercado")
    _cat(uid, "groceries")
    lid = _launch(uid, "groceries", "open_finance")
    rel = fundir_usuario(uid, aplicar=True)
    assert [(de, para) for de, para, _ in rel["fundidas"]] == [("groceries", "mercado")]
    assert _q("select categoria from launches where id=%s", (lid,))[0]["categoria"] == "mercado"
    assert "groceries" not in _nomes(uid)
    assert _q("select is_archived from user_categories where user_id=%s and name='mercado'", (uid,))[0]["is_archived"] is True


def test_ingles_com_caixa_e_espaco_funde(uid):
    _cat(uid, "  Groceries  ")
    lid = _launch(uid, "  Groceries  ", "open_finance")
    fundir_usuario(uid, aplicar=True)
    assert _q("select categoria from launches where id=%s", (lid,))[0]["categoria"] == "mercado"
    assert "  Groceries  " not in _nomes(uid)


def test_todas_arquivadas_funde(uid):
    _cat(uid, "pão")
    _cat(uid, "pao")
    lid = _launch(uid, "pão")
    _launch(uid, "pao")
    _launch(uid, "pao")
    _arquiva(uid, "pão")
    _arquiva(uid, "pao")
    fundir_usuario(uid, aplicar=True)
    assert "pao" in _nomes(uid) and "pão" not in _nomes(uid)
    assert _q("select categoria from launches where id=%s", (lid,))[0]["categoria"] == "pao"


def test_fusao_nao_deixa_alerta_orfao(uid):
    _cat(uid, "cafe")
    _cat(uid, "café")
    _alerta(uid, "cafe")
    _alerta(uid, "café")
    fundir_usuario(uid, aplicar=True)
    assert _nomes(uid).count("cafe") == 1 and "café" not in _nomes(uid)
    alertas = _q("select categoria from budget_alert_sent where user_id=%s", (uid,))
    assert [r["categoria"] for r in alertas] == ["cafe"]


def test_gemea_so_de_caixa_nao_apaga_o_alerta_da_vencedora(uid):
    _cat(uid, "cafe")   # menor id: vence
    _cat(uid, "Cafe")
    _alerta(uid, "cafe")
    _alerta(uid, "Cafe")
    rel = fundir_usuario(uid, aplicar=True)
    assert [(de, para) for de, para, _ in rel["fundidas"]] == [("Cafe", "cafe")]
    alertas = _q("select categoria from budget_alert_sent where user_id=%s", (uid,))
    assert "cafe" in [r["categoria"] for r in alertas]


# ─── 8. isolamento ──────────────────────────────────────────────────────────


def test_apply_de_um_usuario_nao_toca_o_outro(uid, outro_uid):
    for u in (uid, outro_uid):
        _estado_groceries(u)
        _alerta(u, "groceries")
        _alerta(u, "mercado")   # colide: o alerta de "groceries" vai para o DELETE
    antes_b = _snapshot(outro_uid)
    main(["--user", str(uid), "--apply"])
    assert "groceries" not in _nomes(uid)
    assert _snapshot(outro_uid) == antes_b


# ─── 9. seed ────────────────────────────────────────────────────────────────


def test_seed_nao_importa_linhas_do_of(pro_user_id):
    _launch(pro_user_id, "Groceries", "open_finance")
    _credito_of(pro_user_id, "Shopping")
    _launch(pro_user_id, "freela")
    ensure_user_categories_seeded(pro_user_id)
    nomes = _nomes(pro_user_id)
    assert "freela" in nomes and "groceries" not in nomes and "shopping" not in nomes


# ─── 10. global ─────────────────────────────────────────────────────────────


def test_dry_run_global_nao_escreve_e_apply_global_processa_todos(uid, outro_uid):
    _estado_groceries(uid)
    _estado_groceries(outro_uid)
    antes = (_snapshot(uid), _snapshot(outro_uid))
    main([])
    assert (_snapshot(uid), _snapshot(outro_uid)) == antes
    main(["--apply"])
    assert "groceries" not in _nomes(uid) and "groceries" not in _nomes(outro_uid)


def test_user_inexistente_aborta():
    with pytest.raises(SystemExit):
        main(["--user", str(9_999_999_999_999)])
