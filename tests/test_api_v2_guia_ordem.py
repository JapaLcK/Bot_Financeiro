"""`ordem {aba, n}` do `POST /api/v2/guia` (`api/v2/guia.py`, `db/guia.py`) pelo monólito real:
o `dispensar`/`reabrir` mais velho da mesma aba, que chega depois do mais novo, não o desfaz
(sem gesto de outra aba/aparelho/cliente antigo no meio: limite em docs/CLAUDE.md, "Ordem dos gestos").
A guarda mora no `WHERE` do UPDATE; o último teste (2 conexões) é o que a separa de um
`SELECT` seguido de decisão em Python. Isolamento A/B de usuário: T8.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import pytest

from api.v2.guia import IDS
from conftest import usuario_pagante
from test_api_v2_guia import cliente, ler, linha, uid  # noqa: F401 (uid é fixture)
from tests._patrimonio_helpers import q

URL = "/api/v2/guia"


def post(quem, acao, ordem=None, **extra):
    c, h = cliente(quem)
    return c.post(URL, json={"acao": acao, "ordem": ordem, **extra}, headers=h)


def gesto(quem, acao, aba, n) -> str:
    r = post(quem, acao, {"aba": aba, "n": n})
    assert r.status_code == 200, r.text
    return r.json()["estado"]


def ordem_gravada(quem):
    l = linha(quem)
    return l["ordem_aba"], l["ordem_n"]


def test_reabrir_mais_novo_chega_antes_do_dispensar_velho(uid):
    """T1: o dispensar n=1 (velho) chega depois do reabrir n=2 e não desfaz a reabertura."""
    assert gesto(uid, "reabrir", "a", 2) == "em_andamento"
    assert gesto(uid, "dispensar", "a", 1) == "em_andamento"
    assert linha(uid)["dispensado_em"] is None
    assert ordem_gravada(uid) == ("a", 2)


def test_dispensar_mais_novo_chega_antes_do_reabrir_velho(uid):
    """T2: o reabrir n=1 velho não reabre o dispensar n=2; o `visto` da oferta ainda carimba."""
    assert gesto(uid, "dispensar", "a", 2) == "dispensado"
    assert gesto(uid, "reabrir", "a", 1) == "dispensado"
    l = linha(uid)
    assert l["dispensado_em"] is not None and l["oferecido_em"] is not None
    assert ordem_gravada(uid) == ("a", 2)


def test_mesma_aba_em_ordem_aplica_tudo(uid):
    """T3 (positivo): sem ele, uma guarda que recusa tudo passaria nos testes acima."""
    assert [gesto(uid, a, "a", n) for n, a in [(1, "dispensar"), (2, "reabrir"), (3, "dispensar")]] == [
        "dispensado", "em_andamento", "dispensado"]


def test_outra_aba_com_n_menor_aplica(uid):
    """T4: o contador é por aba; o n da B não se compara ao da A."""
    assert gesto(uid, "dispensar", "A", 9) == "dispensado"
    assert gesto(uid, "reabrir", "B", 1) == "em_andamento"
    assert ordem_gravada(uid) == ("B", 1)


def test_cliente_antigo_sem_ordem_aplica_e_zera_a_ordem(uid):
    """T5: sem `ordem` aplica como sempre e apaga o último (aba, n)."""
    assert gesto(uid, "dispensar", "A", 5) == "dispensado"
    assert post(uid, "reabrir").json()["estado"] == "em_andamento"
    assert ordem_gravada(uid) == (None, None)
    assert gesto(uid, "dispensar", "A", 1) == "dispensado"  # sem ordem guardada, o n=1 é novo


def test_n_repetido_na_mesma_aba_e_ignorado(uid):
    """T6: o mesmo (aba, n) reenviado não grava de novo."""
    assert gesto(uid, "dispensar", "a", 3) == "dispensado"
    assert gesto(uid, "reabrir", "a", 3) == "dispensado"


def test_feito_e_visto_com_ordem_velha_gravam(uid):
    """T7: a ordem só vale para dispensar/reabrir."""
    assert gesto(uid, "reabrir", "a", 5) == "em_andamento"
    c, h = cliente(uid)
    r = c.post(URL, json={"acao": "feito", "passo": "gastos.categoria", "ordem": {"aba": "a", "n": 1}}, headers=h)
    assert r.status_code == 200, r.text
    assert list(linha(uid)["feitos"]) == ["gastos.categoria"]
    assert gesto(uid, "visto", "a", 1) == "em_andamento"
    assert ordem_gravada(uid) == ("a", 5)


def test_isolamento_a_ordem_de_x_nao_vale_para_y(monkeypatch, uid):
    """T8: X em ("x", 5), Y dispensa ("x", 10) e depois ("x", 1): a linha de X não muda nenhuma vez.
    O 10 cruza o 5 de X (sem `where user_id` o UPDATE de Y pegaria a linha de X); o 1 não cruza."""
    y = usuario_pagante()
    monkeypatch.setenv("DASHBOARD_V2_BETA_USER_IDS", f"{uid},{y}")
    assert gesto(uid, "reabrir", "x", 5) == "em_andamento"
    antes = linha(uid)
    assert gesto(y, "dispensar", "x", 10) == "dispensado"
    assert linha(uid) == antes and antes["dispensado_em"] is None and ordem_gravada(uid) == ("x", 5)
    assert gesto(y, "reabrir", "x", 1) == "dispensado"  # o 1 é velho para Y: não grava
    assert linha(uid) == antes
    assert ordem_gravada(y) == ("x", 10)
    assert ler(uid)["estado"] == "em_andamento"


@pytest.mark.parametrize("ordem", [{"aba": "a", "n": 0}, {"aba": "a", "n": 2**31}, {"aba": "a" * 33, "n": 1},
                                   {"aba": "", "n": 1}, {"aba": "a"}, {"aba": "a\u0000b", "n": 1}],
                         ids=["n0", "n_estoura_int", "aba_33", "aba_vazia", "sem_n", "aba_com_nul"])
def test_ordem_invalida_e_422_no_envelope_e_nao_grava(uid, ordem):
    r = post(uid, "dispensar", ordem)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"
    assert linha(uid) is None


def test_aba_com_surrogate_solitario_e_422_e_nao_grava(uid):
    """O `json.loads` do FastAPI aceita `\\ud800`; o pydantic o recusa (422). Se um dia aceitar,
    o psycopg falha com `UnicodeEncodeError` ao gravar."""
    c, h = cliente(uid)
    corpo = b'{"acao": "dispensar", "ordem": {"aba": "\\ud800", "n": 1}}'
    r = c.post(URL, content=corpo, headers={**h, "content-type": "application/json"})
    assert r.status_code == 422, r.text
    assert linha(uid) is None


def test_ordem_null_e_aceita(uid):
    assert post(uid, "dispensar", None).status_code == 200


def test_aba_com_unicode_de_32_caracteres_aplica(uid):
    """Positivo do pattern da `aba`: a recusa do NUL não recusa o resto."""
    assert gesto(uid, "dispensar", "😀ç" * 16, 1) == "dispensado"
    assert ordem_gravada(uid) == ("😀ç" * 16, 1)


def test_dois_gestos_concorrentes_o_velho_nao_vence(uid, monkeypatch):
    """T10: A segura a trava da linha (`for no key update`: o `insert on conflict` de B não espera
    por ela, o UPDATE espera). B (dispensar n=1) trava no UPDATE; A então aplica reabrir n=2 e
    comita; B reavalia o WHERE sobre a linha nova e não grava. Um SELECT de `ordem_n` antes do
    UPDATE leria a linha velha (null) e dispensaria por cima."""
    from db import guia as dbguia
    from db.connection import get_conn

    dbguia.registrar(uid, "visto", None, IDS)  # a linha existe
    real, principal, erro = dbguia.get_conn, threading.current_thread(), []

    def b():
        try:
            dbguia.registrar(uid, "dispensar", None, IDS, ("x", 1))
        except Exception as e:  # noqa: BLE001 — volta para a thread principal falhar
            erro.append(e)

    with get_conn() as a:
        a.execute("select 1 from guia_painel where user_id = %s for no key update", (uid,))
        monkeypatch.setattr(dbguia, "get_conn",
                            lambda: contextmanager(lambda: (yield a))() if threading.current_thread() is principal
                            else real())
        t = threading.Thread(target=b)
        t.start()
        limite = time.monotonic() + 10
        while not q("select 1 from pg_stat_activity where datname = current_database()"
                    " and wait_event_type = 'Lock' and query ilike %s", ("update guia_painel%",)):
            assert time.monotonic() < limite and t.is_alive(), f"B não travou no UPDATE: {erro}"
            time.sleep(0.05)
        dbguia.registrar(uid, "reabrir", None, IDS, ("x", 2))  # na transação de A, que ainda tem a trava
        a.commit()
    t.join(10)
    assert not t.is_alive() and not erro, erro
    assert linha(uid)["dispensado_em"] is None
    assert ordem_gravada(uid) == ("x", 2)
