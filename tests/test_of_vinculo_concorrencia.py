"""Duas mãos ao mesmo tempo no vínculo de uma caixinha OF.

Passada 2 do Tester: a bateria de vínculo era toda SEQUENCIAL e estruturalmente
cega à corrida que criava dinheiro em dobro. `bind_pocket_to_caixinha` não tinha
lock nenhum — duas abas, ou um duplo clique — e as duas chamadas liam
`anterior=None`, vinculando DUAS metas à MESMA posição: banco com 800, tela com
1600.

Antes deste PR o estrago se curava sozinho no ciclo some→volta (a posição saía e
uma caixinha nova nascia). Com a religação por lápide ele passou a se RENOVAR: as
duas lápides casam a mesma chave natural e o `not exists` do UPDATE lê o snapshot
de ANTES do statement, então não vê a irmã da mesma rodada. Por isso são dois
consertos, e este arquivo mede os dois:
  1. `_lock_user` no bind — serializa bind × bind, escotilha × escotilha e
     bind × reconciliação;
  2. UMA religação por chave natural (a de menor `pockets.id`), com a lápide das
     excedentes limpa na mesma transação — o que CURA um par que já exista em
     produção, em vez de eternizá-lo.

CONTROLE NEGATIVO (medido, ver relato): tirar o `_lock_user` de
`bind_pocket_to_caixinha` deixa os dois primeiros vermelhos de forma estável (as
10 rodadas do laço); tirar o `p.id = (select min(...))` do UPDATE de religação
deixa o terceiro vermelho.

CONTROLE POSITIVO: `test_binds_em_posicoes_diferentes_nao_se_atrapalham` — o lock
serializa, não recusa: dois binds concorrentes em posições DIFERENTES continuam
valendo os dois. Sem ele, um conserto que recusasse o segundo bind passaria nos
outros três.

Postgres real e threads de verdade (`threading.Barrier`), como o resto da suíte
de concorrência do repositório (`tests/test_of_concurrency.py`).
"""
import threading

import pytest

import db
from db import get_conn
from test_of_caixinha_autoimport import _pockets, _save, _seed_connection
from test_of_caixinha_vinculo import CDB, _meta_manual, _of_id, _total
from test_of_investimento_reconciliacao import CX_AUTO, _reconcilia
from test_of_lapide_religacao import _lapide

RODADAS = 10


def _em_paralelo(alvos: list) -> list:
    """Dispara os callables no MESMO instante e devolve (resultado, exceção)."""
    barreira = threading.Barrier(len(alvos))
    saida: list = [None] * len(alvos)

    def corre(i, fn):
        barreira.wait()
        try:
            saida[i] = ("ok", fn())
        except Exception as exc:                     # a recusa também é resultado
            saida[i] = ("erro", exc)

    threads = [threading.Thread(target=corre, args=(i, fn)) for i, fn in enumerate(alvos)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "thread travou (deadlock?)"
    return saida


def _donos(user_id: int, of_investment_id: int) -> list[int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "select id from pockets where user_id=%s and of_investment_id=%s order by id",
                (user_id, of_investment_id),
            )
            return [r["id"] for r in (cur.fetchall() or [])]


def _zera(user_id: int) -> None:
    """Deixa só a conexão e as posições: cada rodada do laço começa limpa."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("delete from pocket_lots where user_id=%s", (user_id,))
            cur.execute("delete from pockets where user_id=%s", (user_id,))
        conn.commit()


def test_dois_binds_na_mesma_posicao_nao_criam_dinheiro_em_dobro(user_id):
    """O bloqueante B-1, pelo caminho real: duas metas do MESMO usuário pedindo a
    MESMA posição no mesmo instante. No máximo uma pode ficar com ela."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB])
    posicao = _of_id(conn_id, "cdb-vinc")

    for rodada in range(RODADAS):
        _zera(user_id)
        a = _meta_manual(user_id, "Meta A")
        b = _meta_manual(user_id, "Meta B")

        _em_paralelo([lambda: db.bind_pocket_to_caixinha(user_id, a, posicao),
                      lambda: db.bind_pocket_to_caixinha(user_id, b, posicao)])

        donos = _donos(user_id, posicao)
        assert len(donos) <= 1, f"rodada {rodada}: {len(donos)} metas na mesma posição"
        db.sync_open_finance_caixinhas(conn_id, user_id)
        assert _total(user_id) == 1000.0, (
            f"rodada {rodada}: os R$1000 do banco apareceram mais de uma vez")


def test_duas_escotilhas_contra_o_mesmo_espelho_puro(user_id):
    """A escotilha abriu uma segunda porta para a mesma corrida: duas metas
    manuais tomando o espelho PURO que o auto-import criou."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CX_AUTO])
    posicao = _of_id(conn_id, "cx-auto")

    for rodada in range(RODADAS):
        _zera(user_id)
        db.sync_open_finance_caixinhas(conn_id, user_id)     # recria o espelho puro
        assert _donos(user_id, posicao), "pré-condição: o espelho existe"
        a = _meta_manual(user_id, "Meta A")
        b = _meta_manual(user_id, "Meta B")

        _em_paralelo([lambda: db.bind_pocket_to_caixinha(user_id, a, posicao),
                      lambda: db.bind_pocket_to_caixinha(user_id, b, posicao)])

        donos = _donos(user_id, posicao)
        assert len(donos) <= 1, f"rodada {rodada}: {len(donos)} pockets na mesma posição"
        db.sync_open_finance_caixinhas(conn_id, user_id)
        assert _total(user_id) == 800.0, (
            f"rodada {rodada}: os R$800 do banco apareceram mais de uma vez")


def test_binds_em_posicoes_diferentes_nao_se_atrapalham(user_id):
    """CONTROLE POSITIVO: o `_lock_user` SERIALIZA, não recusa. Duas metas, duas
    posições, ao mesmo tempo — os dois vínculos valem."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CDB, CX_AUTO])
    p1, p2 = _of_id(conn_id, "cdb-vinc"), _of_id(conn_id, "cx-auto")
    a = _meta_manual(user_id, "Meta A")
    b = _meta_manual(user_id, "Meta B")

    saida = _em_paralelo([lambda: db.bind_pocket_to_caixinha(user_id, a, p1),
                          lambda: db.bind_pocket_to_caixinha(user_id, b, p2)])

    assert [s[0] for s in saida] == ["ok", "ok"], saida
    assert [s[1] for s in saida] == [True, True], saida
    assert _donos(user_id, p1) == [a]
    assert _donos(user_id, p2) == [b]
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert _total(user_id) == 1800.0


def test_par_duplicado_existente_e_curado_e_nao_renovado(user_id):
    """A segunda metade: um par que JÁ exista (produção pode ter, de antes do
    lock) não pode se renovar a cada ciclo some→volta.

    O par é FORJADO por SQL — com o `_lock_user` no lugar, a cadeia real não
    produz mais dois pockets na mesma posição, e forjar é o único jeito de
    exercitar a cura sem desligar o conserto que o teste acima mede."""
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CX_AUTO])
    posicao = _of_id(conn_id, "cx-auto")
    a = _meta_manual(user_id, "Meta A")
    b = _meta_manual(user_id, "Meta B")
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("update pockets set of_investment_id=%s where id = any(%s) and user_id=%s",
                        (posicao, [a, b], user_id))
        conn.commit()
    assert _donos(user_id, posicao) == sorted([a, b]), "pré-condição: o par existe"

    _reconcilia(conn_id, [])            # some: as DUAS ganham lápide igual
    _reconcilia(conn_id, [CX_AUTO])     # volta

    donos = _donos(user_id, _of_id(conn_id, "cx-auto"))
    assert donos == [min(a, b)], "religa UMA, a de menor id"
    # a excedente perde a lápide na MESMA transação: a posição já tem dona, e
    # lápide que nunca vai religar é só um ponteiro velho para o id no provedor
    assert _lapide(user_id, max(a, b)) == (None, None)
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert _total(user_id) == 800.0, "o dinheiro do banco conta uma vez"

    # 2º ciclo: o par não volta a existir
    _reconcilia(conn_id, [])
    _reconcilia(conn_id, [CX_AUTO])
    assert _donos(user_id, _of_id(conn_id, "cx-auto")) == [min(a, b)]
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert _total(user_id) == 800.0
