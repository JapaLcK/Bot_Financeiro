"""Plano Família, PR 5: a casa nunca pede uma 2a conexão do pool enquanto segura a 1a (a escrita segura o lock do grupo).

Pool de 1 conexão e timeout de 3 s: pedir a 2a custa o timeout e `autor` sai "" (o PoolTimeout é engolido em _primeiro_nome).
"""
import os
import time

import psycopg
from psycopg_pool import ConnectionPool

import db_support
from db import connection
from db import family_house as house
from test_api_v2_familia_dados import w  # noqa: F401  (w é fixture)


def _pool_de_1(monkeypatch):
    p = ConnectionPool(os.environ["DATABASE_URL"], connection_class=connection._Conn, min_size=1, max_size=1, timeout=3,
                       kwargs={"row_factory": psycopg.rows.dict_row}, open=True)
    p.wait()  # a 1a conexão do pool recém-aberto não conta no limite de tempo
    monkeypatch.setattr(connection, "_pool", p)
    db_support.invalidate_auth_user_cache()
    return p


def test_escrita_e_leitura_nao_pedem_2a_conexao_e_os_autores_saem_certos(w, monkeypatch):
    house.criar_meta(w.ma1, "a", 1000, None), house.criar_meta(w.ta, "b", 1000, None)
    p = _pool_de_1(monkeypatch)
    try:
        t0 = time.monotonic()
        r = house.criar_meta(w.ma1, "c", 1000, None)
        assert time.monotonic() - t0 < 2.5  # com a 2a conexão pedida seriam >= 3 s por nome
        assert all(m["autor"] for m in r["metas"])
        db_support.invalidate_auth_user_cache()
        t0 = time.monotonic()
        g = house.casa(w.ta)
        assert time.monotonic() - t0 < 2.5
        assert [m["autor"] for m in g["metas"]] == ["Maria", "Ana", "Maria"]
    finally:
        monkeypatch.undo()
        p.close()


def test_n_itens_do_mesmo_autor_resolvem_o_nome_uma_vez(w, monkeypatch):
    for n in "abc":
        house.criar_meta(w.ma1, n, 1000, None)
    chamadas = []
    real = house._primeiro_nome
    monkeypatch.setattr(house, "_primeiro_nome", lambda uid: chamadas.append(uid) or real(uid))
    house.casa(w.ta)
    assert chamadas == [w.ma1]
