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
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

import db
from db import get_conn
from db import open_finance
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
        # `== 1` e não `<= 1`: zero dono passaria num conserto que simplesmente
        # RECUSASSE os dois binds, que é pior que o bug. Serializado, o segundo
        # desvincula o primeiro e assume — sempre sobra exatamente uma dona.
        assert len(donos) == 1, f"rodada {rodada}: {len(donos)} metas na mesma posição"
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
        # `== 1` pelo mesmo motivo do teste acima: recusar os dois não é conserto.
        assert len(donos) == 1, f"rodada {rodada}: {len(donos)} pockets na mesma posição"
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


# --- auto-import × bind ------------------------------------------------------
# `sync_open_finance_caixinhas` lia "ninguém é dono desta posição" e só DEPOIS
# inseria o espelho, com guarda só de NOME. Um bind manual que fizesse commit
# entre os dois deixava DOIS pockets na posição, e o passo 3 espelhava o saldo
# nos dois. A janela é de microssegundos; o proxy abaixo a estica para até 3s,
# então a intercalação é determinística. CONTROLE NEGATIVO (medido, ver relato):
# tirar o `_lock_user` do auto-import deixa os dois testes abaixo vermelhos com
# dois donos. Com ele, o bind espera o lock, o Event sai por timeout, o
# auto-import faz commit e o bind toma o espelho puro pela escotilha.

class _Pausa:
    """Conexão/cursor reais; pausa UMA vez antes do `insert into pockets`."""

    def __init__(self, alvo, na_janela, libera):
        self._alvo, self._na_janela, self._libera = alvo, na_janela, libera

    def __getattr__(self, nome):
        return getattr(self._alvo, nome)

    def __enter__(self):
        self._alvo.__enter__()
        return self

    def __exit__(self, *exc):
        return self._alvo.__exit__(*exc)

    def cursor(self, *a, **k):
        return _Pausa(self._alvo.cursor(*a, **k), self._na_janela, self._libera)

    def execute(self, sql, *a, **k):
        if "insert into pockets" in sql and not self._na_janela.is_set():
            self._na_janela.set()
            self._libera.wait(3)
        return self._alvo.execute(sql, *a, **k)


def _pausa_no_insert(get_conn_real, na_janela, libera):
    @contextmanager
    def get_conn_pausado(*a, **k):
        with get_conn_real(*a, **k) as conn:
            yield _Pausa(conn, na_janela, libera)
    return get_conn_pausado


def _cenario(user_id: int) -> tuple[int, int, int]:
    conn_id = _seed_connection(user_id)
    _save(conn_id, [CX_AUTO])                        # posição sem dono ainda
    return conn_id, _of_id(conn_id, "cx-auto"), _meta_manual(user_id, "Viagem")


def _uma_dona_e_dinheiro_uma_vez(user_id, conn_id, posicao, meta):
    # A meta fica com a posição: o espelho que o auto-import criou é puro (sem
    # lote), e a escotilha do bind o apaga na mesma transação.
    assert _donos(user_id, posicao) == [meta], "dois pockets na mesma posição"
    db.sync_open_finance_caixinhas(conn_id, user_id)
    assert _donos(user_id, posicao) == [meta]
    assert _total(user_id) == 800.0, "os R$800 do banco apareceram mais de uma vez"


def test_auto_import_nao_duplica_o_vinculo_de_um_bind_concorrente(user_id, monkeypatch):
    conn_id, posicao, meta = _cenario(user_id)
    na_janela, libera = threading.Event(), threading.Event()
    monkeypatch.setattr(open_finance, "get_conn",
                        _pausa_no_insert(open_finance.get_conn, na_janela, libera))

    sync = threading.Thread(target=db.sync_open_finance_caixinhas, args=(conn_id, user_id))
    sync.start()
    assert na_janela.wait(10), "o auto-import nunca chegou ao insert"
    vinculou: list = []

    def vincula():
        vinculou.append(db.bind_pocket_to_caixinha(user_id, meta, posicao))
        libera.set()

    bind = threading.Thread(target=vincula)
    bind.start()
    bind.join(30)
    sync.join(30)
    assert not bind.is_alive() and not sync.is_alive(), "thread travou (deadlock?)"
    assert vinculou == [True], "o bind não terminou vinculando"
    _uma_dona_e_dinheiro_uma_vez(user_id, conn_id, posicao, meta)


_FILHO = """
import sys, threading
sys.path[:0] = ['.', 'tests']
from test_of_vinculo_concorrencia import _pausa_no_insert
from db import open_finance as of
na_janela, libera = threading.Event(), threading.Event()
of.get_conn = _pausa_no_insert(of.get_conn, na_janela, libera)
def ponte():
    na_janela.wait()
    print('NA_JANELA', flush=True)
    sys.stdin.readline()
    libera.set()
threading.Thread(target=ponte, daemon=True).start()
of.sync_open_finance_caixinhas(int(sys.argv[1]), int(sys.argv[2]))
"""


def test_auto_import_x_bind_em_dois_processos(user_id):
    """O mesmo, com o auto-import em OUTRO processo: sem pool, GIL nem conexão
    compartilhados — só o Postgres entre os dois."""
    conn_id, posicao, meta = _cenario(user_id)
    filho = subprocess.Popen(
        [sys.executable, "-c", _FILHO, str(conn_id), str(user_id)],
        cwd=Path(__file__).resolve().parents[1], text=True,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        saida = []
        for linha in filho.stdout:
            if linha.strip() == "NA_JANELA":
                break
            saida.append(linha)
        else:
            pytest.fail("o filho não chegou ao insert:\n" + "".join(saida))
        assert db.bind_pocket_to_caixinha(user_id, meta, posicao) is True
        resto, _ = filho.communicate("\n", timeout=60)
    finally:
        if filho.poll() is None:
            filho.kill()
    assert filho.returncode == 0, resto
    _uma_dona_e_dinheiro_uma_vez(user_id, conn_id, posicao, meta)
