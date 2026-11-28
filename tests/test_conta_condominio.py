"""PR 3a da conta de condomínio: `users.tipo_conta`, `user_categories.grupo`, seed do plano de contas,
regras e `scripts/ativar_conta_condominio.py`. Banco real."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

import db
import frontend.finance_bot_websocket_custom as dashboard
from conftest import _cleanup_user
from db.categories import ensure_user_categories_seeded, list_user_categories_full, update_user_category
from db.categorias_condominio import (
    CATEGORIAS_CONDOMINIO, GRUPOS_CONDOMINIO, REGRAS_CONDOMINIO, semear_condominio,
)
from db.users import is_conta_condominio
from scripts.ativar_conta_condominio import ativar, main
from token_utils import make_dashboard_token
from utils_text import normalize_text


def _q(sql, params=()):
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall() if cur.description else None
        conn.commit()
    return rows


def _semeia(uid):
    ensure_user_categories_seeded(uid)
    with db.get_conn() as conn, conn.cursor() as cur:
        r = semear_condominio(cur, uid)
        conn.commit()
    return r


def _cats(uid):
    return {c["name"]: c for c in list_user_categories_full(uid)}


@pytest.fixture()
def outro_user():
    uid = int(uuid.uuid4().int % 10_000_000_000)
    db.ensure_user(uid)
    yield uid
    _cleanup_user(uid)


def test_migracao_idempotente():
    db.init_db()  # o conftest já rodou uma vez: esta é a 2ª
    cols = {r["column_name"]: r for r in _q(
        "select column_name, column_default, is_nullable from information_schema.columns "
        "where (table_name='users' and column_name='tipo_conta') "
        "or (table_name='user_categories' and column_name='grupo')")}
    assert set(cols) == {"tipo_conta", "grupo"}
    assert cols["tipo_conta"]["is_nullable"] == "NO" and "pessoal" in cols["tipo_conta"]["column_default"]


def test_conta_pessoal_e_check(user_id):
    assert _q("select tipo_conta from users where id=%s", (user_id,))[0]["tipo_conta"] == "pessoal"
    assert is_conta_condominio(user_id) is False
    assert is_conta_condominio(-1) is False
    assert all(c["grupo"] is None for c in list_user_categories_full(user_id))
    with pytest.raises(Exception):
        _q("update users set tipo_conta='x' where id=%s", (user_id,))


def test_lista_do_seed_e_coerente():
    nomes = [c[0] for c in CATEGORIAS_CONDOMINIO]
    chaves = [normalize_text(n) for n in nomes]
    assert len(set(chaves)) == len(chaves)
    canonicas = {normalize_text(n) for n, _, _ in __import__("db.categories", fromlist=["x"]).SYSTEM_CATEGORIES_SEED}
    assert not canonicas & set(chaves)
    assert {c[1] for c in CATEGORIAS_CONDOMINIO} <= set(GRUPOS_CONDOMINIO)
    assert set(REGRAS_CONDOMINIO.values()) <= set(nomes)


def test_seed_idempotente_e_nao_sobrescreve_grupo(user_id):
    r1 = _semeia(user_id)
    n1 = _q("select count(*) n from user_categories where user_id=%s", (user_id,))[0]["n"]
    regras1 = _q("select count(*) n from user_category_rules where user_id=%s", (user_id,))[0]["n"]
    assert r1["regras_novas"] == regras1 == len(REGRAS_CONDOMINIO)
    _q("update user_categories set grupo='outras' where user_id=%s and name='mensalidades'", (user_id,))
    r2 = _semeia(user_id)
    assert _q("select count(*) n from user_categories where user_id=%s", (user_id,))[0]["n"] == n1
    assert (r2["arquivadas"], r2["regras_novas"]) == (0, 0)
    assert _cats(user_id)["mensalidades"]["grupo"] == "outras"  # grupo alterado pelo síndico sobrevive
    assert _cats(user_id)["salários"]["grupo"] == "pessoal"


def test_seed_preenche_grupo_de_categoria_que_ja_existia_sem_grupo(user_id):
    ensure_user_categories_seeded(user_id)
    _q("insert into user_categories (user_id, name) values (%s, 'mensalidades')", (user_id,))
    _semeia(user_id)
    assert _cats(user_id)["mensalidades"]["grupo"] == "receitas"


def test_isolamento_entre_usuarios(user_id, outro_user):
    ensure_user_categories_seeded(outro_user)
    _semeia(user_id)
    outras = _cats(outro_user)
    assert "salários" not in outras
    assert all(c["grupo"] is None and not c["is_archived"] for c in outras.values())
    assert _q("select count(*) n from user_category_rules where user_id=%s", (outro_user,))[0]["n"] == 0
    assert _q("select tipo_conta from users where id=%s", (outro_user,))[0]["tipo_conta"] == "pessoal"


def test_arquiva_so_canonica_sem_uso_deste_usuario(user_id, outro_user):
    ensure_user_categories_seeded(outro_user)
    _q("insert into launches (user_id, tipo, valor, categoria, source, external_id) "
       "values (%s,'despesa',10,'Mercado','manual',%s)", (user_id, uuid.uuid4().hex))
    _q("insert into launches (user_id, tipo, valor, categoria, source, external_id) "
       "values (%s,'despesa',10,'lazer','manual',%s)", (outro_user, uuid.uuid4().hex))  # uso de OUTRO user não conta
    _semeia(user_id)
    c = _cats(user_id)
    assert c["mercado"]["is_archived"] is False          # tem lançamento deste usuário
    assert c["lazer"]["is_archived"] is True             # só o outro usuário usa
    assert c["pets"]["is_archived"] is True
    assert c["salários"]["is_archived"] is False         # categoria do condomínio nunca é arquivada
    assert not any(v["is_archived"] for v in _cats(outro_user).values())


def test_regras_nao_sobrescrevem_keyword_existente(user_id):
    ensure_user_categories_seeded(user_id)
    _q("insert into user_category_rules (user_id, keyword, category) values (%s,'inss','outros'),(%s,'SALARIO','outros')",
       (user_id, user_id))
    _semeia(user_id)
    regras = {r["keyword"]: r["category"] for r in _q(
        "select keyword, category from user_category_rules where user_id=%s", (user_id,))}
    assert regras["inss"] == "outros"                       # mesma keyword
    assert "salário" not in regras and regras["SALARIO"] == "outros"  # mesma keyword a menos de acento/caixa
    assert regras["gps"] == "inss"


def test_rename_mantem_grupo_e_cascateia_regras(user_id):
    _semeia(user_id)
    cid = _cats(user_id)["mensalidades"]["id"]
    update_user_category(user_id, cid, new_name="taxa de condomínio")
    c = _cats(user_id)["taxa de condomínio"]
    assert c["grupo"] == "receitas"
    assert _q("select category from user_category_rules where user_id=%s and keyword='mensalidade'",
              (user_id,))[0]["category"] == "taxa de condomínio"


def test_get_categorias_traz_grupo(user_id, pro_user_id):
    client = TestClient(dashboard.app)
    hdr = lambda u: {"Authorization": f"Bearer {make_dashboard_token(u, hours=1)}"}  # noqa: E731
    pessoal = client.get(f"/categories/{pro_user_id}", headers=hdr(pro_user_id)).json()["categories"]
    assert pessoal and all(c["grupo"] is None for c in pessoal)
    _semeia(pro_user_id)
    cond = {c["name"]: c for c in client.get(f"/categories/{pro_user_id}", headers=hdr(pro_user_id)).json()["categories"]}
    assert cond["energia elétrica"]["grupo"] == "funcionais"


def test_script_idempotente(user_id):
    r1 = ativar(user_id)
    assert is_conta_condominio(user_id) is True and r1["regras_novas"] == len(REGRAS_CONDOMINIO)
    antes = _q("select count(*) n from user_categories where user_id=%s", (user_id,))[0]["n"]
    r2 = ativar(user_id)
    assert (r2["arquivadas"], r2["regras_novas"]) == (0, 0)
    assert _q("select count(*) n from user_categories where user_id=%s", (user_id,))[0]["n"] == antes


def test_script_recusa_user_inexistente_e_avisa_plano(user_id, capsys):
    with pytest.raises(SystemExit):
        main(["--user-id", "-5"])
    assert not _q("select 1 from users where id=-5")
    main(["--user-id", str(user_id)])  # conta grátis: não libera categorias personalizadas
    out = capsys.readouterr().out
    assert "AVISO" in out and "condominio" in out
