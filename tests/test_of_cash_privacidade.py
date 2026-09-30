"""Q41 grupo 9: as tabelas novas entram na exportação, no "Recomeçar do zero"
e na exclusão de conta; toda FK tem índice."""
import io
import zipfile

import db
from conftest import usuario_pagante
from db.privacy import build_user_export_zip, delete_user_data, reset_user_data
from db.users import _hash_password
from tests._of_cash_helpers import caixa, conecta, dia, q, sync, tx  # noqa: F401

SENHA = "senha-forte-123"


def _com_vinculo_e_cobertura():
    uid = usuario_pagante()
    c = conecta(uid, f"item-{uid}")
    sync(c, uid, [tx("t1", -200, dia(10))])
    db.disconnect_open_finance_connection(uid, c)
    return uid


def _conta(uid):
    return [q(f"select count(*) as n from {t} where user_id=%s", (uid,), True)[0]["n"]
            for t in ("of_cash_links", "of_cash_coverage")]


def test_exportacao_leva_as_duas_tabelas(caixa):
    uid = _com_vinculo_e_cobertura()
    nomes = zipfile.ZipFile(io.BytesIO(build_user_export_zip(uid))).namelist()
    assert "csv/saques_depositos_dinheiro.csv" in nomes
    assert "csv/cobertura_open_finance.csv" in nomes


def test_reset_apaga_e_exclusao_apaga(caixa):
    a, b = _com_vinculo_e_cobertura(), _com_vinculo_e_cobertura()
    assert _conta(a) == _conta(b) == [1, 1]
    q("update auth_accounts set password_hash=%s where user_id=%s", (_hash_password(SENHA), a))

    reset_user_data(a, SENHA)
    delete_user_data(b)

    assert _conta(a) == [0, 0]
    assert _conta(b) == [0, 0]


def test_toda_fk_das_tabelas_novas_tem_indice():
    rows = q("""select c.conrelid::regclass::text as tab, a.attname as col
                  from pg_constraint c join pg_attribute a
                    on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
                 where c.contype = 'f' and c.conrelid in ('of_cash_links'::regclass,
                                                          'of_cash_coverage'::regclass)""", fetch=True)
    idx = q("""select tablename, indexdef from pg_indexes
                where tablename in ('of_cash_links', 'of_cash_coverage')""", fetch=True)
    assert len(rows) == 5
    for r in rows:
        assert any(i["tablename"] == r["tab"] and f"({r['col']}" in i["indexdef"] for i in idx), r
