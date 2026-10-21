"""PR-A2b-2: clique duplo, duas abas e sync concorrente no "foi em dinheiro". Retrato completo depois de cada corrida.

A proteção tem camadas: o mutex `_lock_user` (relê o estado depois dele), o `for update` das linhas e o CAS por
linha (`delete ... returning`, `not (efeitos ? 'foi_em_dinheiro')`). Cada teste de "duas ações iguais" roda duas
vezes: COM o mutex (`_ordered_pair`: a 2ª só começa quando a 1ª já o segura e a 2ª está parada no lock) e SEM ele
(`sem_mutex`: as duas leem o estado velho antes de qualquer escrita, sobra a defesa em profundidade).

Controles (rodados numa CÓPIA; um conserto sozinho não derruba, porque as camadas se cobrem de propósito):
- H1: `_lock_user` no-op E sem o `for update` das linhas da declaração E sem o CAS do `delete ... returning`
  (o saldo vem de `d["amount"]`) -> VERMELHO `test_h1_converter_duas_vezes_mexe_no_saldo_uma_vez[sem_mutex]`
  (Carteira 900 no lugar de 950). Só o mutex fora, ou só o CAS fora, continua verde: é a defesa em profundidade.
- H2: tirar `not (efeitos ? 'foi_em_dinheiro')` do CAS E do filtro de `candidatos_carteira` -> VERMELHO
  `test_h2_duas_declaracoes_disputam_a_mesma_c[com_mutex]` e `[sem_mutex]` (as duas somem, C com um contexto só): o mutex
  sozinho NÃO protege C, o marcador protege. Só um dos dois lugares fora segue verde (o outro segura).
- H13: tirar o `for update` de C (`select id from launches ... for update`) -> VERMELHO
  `test_h13_c_editada_enquanto_o_clique_esperava_e_recusada` (a C de R$ 51,00 é marcada).
- H9: `_lock_user` no-op no `foi_em_dinheiro` -> VERMELHO `test_h9_converter_e_sync_concorrentes[...]` na ordem em que o sync liga
  antes (a conversão comita junto com o par ligado). Positivo: as outras ordens do mesmo teste.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import db
import db.bank_conferencia as conferencia
import db.bank_movements as movements
from db.accounts import get_balance
from db.bank_dinheiro import foi_em_dinheiro
from db.connection import get_conn
from tests._espera_lock import _esperar_backend_travado
from tests._patrimonio_helpers import q
from tests._retrato_declaracao import carteira, decl, em, marcadores, retrato, sombra, solta, t, tid, casada
from tests.test_bank_movements_concurrency import _captura, _ordered_pair
from tests.test_declaracao_a2b2_dinheiro import SOMBRA9, T9, ana2, lanca
from tests.test_declaracao_a2b_acoes import R, T1, T2, ana
from tests.test_declaracao_a2b_corrida import TROCADA
from tests.test_declaracao_fluxo_reconcilia import declara, sincroniza

SEM_PAR = [("decl mercado", -50.0, None, None, False, False)]


def sem_mutex(monkeypatch):
    """Mutex desligado e as duas threads leem a declaração ANTES de qualquer escrita: só a defesa em profundidade."""
    barreira, original = Barrier(2, timeout=5), movements._declarations
    monkeypatch.setattr(movements, "_lock_user", lambda cur, uid: None)

    def lendo(cur, uid):
        lido = original(cur, uid)
        barreira.wait()
        return lido
    monkeypatch.setattr(movements, "_declarations", lendo)


def corre(monkeypatch, a, b, modo):
    if modo == "com_mutex":
        return _ordered_pair(monkeypatch, a, b, esperar_travado=True)
    sem_mutex(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa, fb = pool.submit(a), pool.submit(b)
        return fa.result(timeout=15), fb.result(timeout=15)


def uma_vence(a, b, erro):
    """Exatamente uma terminou bem; a outra levantou `erro` (na ordem em que as threads chegaram)."""
    falhas = [x for x in (a, b) if isinstance(x, Exception)]
    assert len(falhas) == 1 and isinstance(falhas[0], erro), (a, b)


MODOS = ["com_mutex", "sem_mutex"]


@pytest.mark.parametrize("modo", MODOS)
def test_h1_converter_duas_vezes_mexe_no_saldo_uma_vez(user_id, monkeypatch, modo):
    cid, d, c40, _ = ana2(user_id)
    conv = _captura(lambda: foi_em_dinheiro(user_id, d))
    a, b = corre(monkeypatch, conv, conv, modo)
    uma_vence(a, b, LookupError if modo == "com_mutex" else ValueError)
    assert retrato(user_id) == R([carteira(0), carteira(1), *SOMBRA9], [solta("T9", -39.9, 0)], [], 139.9, 900.0)
    assert marcadores(user_id) == {"manual mercado": {"convertida": True}}


@pytest.mark.parametrize("modo", MODOS)
def test_h2_duas_declaracoes_disputam_a_mesma_c(user_id, monkeypatch, modo):
    cid, d1, c40, _ = ana2(user_id)
    d2 = declara(user_id, criado_em=em(0), alvo="padaria", categoria="padaria")
    a, b = corre(monkeypatch, _captura(lambda: foi_em_dinheiro(user_id, d1, c40)),
                 _captura(lambda: foi_em_dinheiro(user_id, d2, c40)), modo)
    uma_vence(a, b, ValueError)
    r = retrato(user_id)
    assert len(r["D"]) == 1 and len(marcadores(user_id)) == 1 and r["C"] == 950.0, r
    assert q("select count(*) n from launches where id in (%s, %s)", (d1, d2))["n"] == 1  # a perdedora segue a conferir
    assert q("select count(*) n from launches where id=%s", (c40,))["n"] == 1


def test_h2_c_diferentes_as_duas_resolvem(user_id, monkeypatch):
    cid, d1, c40, _ = ana2(user_id)
    d2 = declara(user_id, criado_em=em(0), alvo="padaria", categoria="padaria")
    c2 = lanca(user_id, alvo="padaria", nota="balcao")
    a, b = _ordered_pair(monkeypatch, _captura(lambda: foi_em_dinheiro(user_id, d1, c40)),
                         _captura(lambda: foi_em_dinheiro(user_id, d2, c2)), esperar_travado=True)
    assert not isinstance(a, Exception) and not isinstance(b, Exception), (a, b)
    assert retrato(user_id) == R([carteira(1), carteira(1, alvo="padaria"), *SOMBRA9], [solta("T9", -39.9, 0)], [], 139.9, 900.0)
    assert sorted(marcadores(user_id)) == ["manual mercado", "manual padaria"]


@pytest.mark.parametrize("converte_primeiro", [True, False])
def test_converter_e_apagar_a_declaracao_concorrentes(user_id, monkeypatch, converte_primeiro):
    cid, d, c40, _ = ana2(user_id, com_40=False)
    conv, apaga = (_captura(lambda: foi_em_dinheiro(user_id, d)), _captura(lambda: db.delete_launch_and_rollback(user_id, d)))
    a, b = _ordered_pair(monkeypatch, *((conv, apaga) if converte_primeiro else (apaga, conv)), esperar_travado=True)
    if not converte_primeiro:  # apagada antes: a conversão não encontra mais a declaração
        assert isinstance(b, LookupError), (a, b)
    assert retrato(user_id) == R(SOMBRA9, [solta("T9", -39.9, 0)], [], 39.9, 1000.0)


def test_h13_c_editada_enquanto_o_clique_esperava_e_recusada(user_id):
    """A edição de C comita DEPOIS de o clique tomar o mutex e ANTES de ele travar C: o `for update` + o snapshot
    novo da lista recalculada recusam o C velho. Sem o `for update` a C de R$ 51,00 seria marcada."""
    cid, d, c40, _ = ana2(user_id)
    with get_conn() as edicao, edicao.cursor() as cur:
        cur.execute("update launches set valor=51 where id=%s", (c40,))  # sem commit: segura a linha de C
        with ThreadPoolExecutor(max_workers=1) as pool:
            clique = pool.submit(_captura(lambda: foi_em_dinheiro(user_id, d, c40)))
            _esperar_backend_travado(3)  # com o `for update`, o clique para aqui; sem ele passa e o teste vê
            edicao.commit()
            resultado = clique.result(timeout=15)
    assert isinstance(resultado, ValueError) and str(resultado) == "CASH_NOT_ELIGIBLE", resultado
    assert marcadores(user_id) == {} and q("select valor from launches where id=%s", (c40,))["valor"] == 51
    assert q("select count(*) n from bank_movement_declarations")["n"] == 1


@pytest.mark.parametrize("seleciona", [False, True])
def test_h13_declaracao_apagada_enquanto_o_clique_esperava_e_404(user_id, seleciona):
    """Quem apaga sem o mutex segura a linha da declaração: o clique já a leu em `_declarations` e para no
    `for update`; quando o delete comita a linha não existe mais. Sem o `if not row` era TypeError (500)."""
    cid, d, c40, _ = ana2(user_id)
    with get_conn() as outra, outra.cursor() as cur:
        cur.execute("delete from launches where id=%s", (d,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            clique = pool.submit(_captura(lambda: foi_em_dinheiro(user_id, d, c40 if seleciona else None)))
            _esperar_backend_travado(3)
            outra.commit()
            resultado = clique.result(timeout=15)
    assert isinstance(resultado, LookupError), resultado
    assert marcadores(user_id) == {} and q("select count(*) n from launches where id=%s", (c40,))["n"] == 1
    assert get_balance(user_id) == 950


def test_h13_c_apagada_enquanto_o_clique_esperava_e_recusada(user_id):
    cid, d, c40, _ = ana2(user_id)
    with get_conn() as outra, outra.cursor() as cur:
        cur.execute("delete from launches where id=%s", (c40,))
        with ThreadPoolExecutor(max_workers=1) as pool:
            clique = pool.submit(_captura(lambda: foi_em_dinheiro(user_id, d, c40)))
            _esperar_backend_travado(3)
            outra.commit()
            resultado = clique.result(timeout=15)
    assert isinstance(resultado, ValueError) and str(resultado) == "CASH_NOT_ELIGIBLE", resultado
    assert q("select count(*) n from bank_movement_declarations")["n"] == 1 and marcadores(user_id) == {}


@pytest.mark.parametrize("converte_primeiro", [True, False])
def test_h9_converter_e_sync_concorrentes(user_id, monkeypatch, converte_primeiro):
    cid, d, _, _ = ana2(user_id, com_40=False)
    conv = _captura(lambda: foi_em_dinheiro(user_id, d))
    sync = _captura(lambda: sincroniza(cid, user_id, T9, T1))
    a, b = _ordered_pair(monkeypatch, *((conv, sync) if converte_primeiro else (sync, conv)), esperar_travado=True)
    if converte_primeiro:  # sem declaração, a T1 chega como sombra e PERGUNTA se é a convertida (E4b), nunca funde
        assert not isinstance(a, Exception) and not isinstance(b, Exception), (a, b)
        assert retrato(user_id) == R([carteira(0), sombra("T1", 50.0, 0), *SOMBRA9],
                                     [("T1", -50.0, 0, "sombra T1", "manual mercado", "pending"), solta("T9", -39.9, 0)],
                                     [], 139.9, 950.0)
    else:  # o sync ligou o par antes: a conversão recebe 409 e a prova segue ligada
        assert isinstance(b, ValueError) and str(b) == "MOVEMENT_ALREADY_MATCHED", (a, b)
        assert retrato(user_id) == R([decl(0), *SOMBRA9], [casada("T1", -50.0, 0), solta("T9", -39.9, 0)],
                                     [("decl mercado", -50.0, "T1", "automatic", False, False)], 89.9, 1000.0)


@pytest.mark.parametrize("seleciona_primeiro", [True, False])
def test_h9_selecionar_e_sync_concorrentes(user_id, monkeypatch, seleciona_primeiro):
    cid, d, c40, _ = ana2(user_id)
    sel = _captura(lambda: foi_em_dinheiro(user_id, d, c40))
    sync = _captura(lambda: sincroniza(cid, user_id, T9, T1))
    a, b = _ordered_pair(monkeypatch, *((sel, sync) if seleciona_primeiro else (sync, sel)), esperar_travado=True)
    if seleciona_primeiro:
        assert not isinstance(a, Exception) and not isinstance(b, Exception), (a, b)
        assert d not in [x["launch_id"] for x in q("select launch_id from bank_movement_declarations", fetch=True)]
        assert marcadores(user_id).keys() == {"manual mercado"}
    else:
        assert isinstance(b, ValueError) and str(b) == "MOVEMENT_ALREADY_MATCHED", (a, b)
        assert retrato(user_id) == R([decl(0), carteira(1), *SOMBRA9], [casada("T1", -50.0, 0), solta("T9", -39.9, 0)],
                                     [("decl mercado", -50.0, "T1", "automatic", False, False)], 139.9, 950.0)


@pytest.mark.parametrize("desfaz_primeiro", [True, False])
def test_h9_converter_e_desfazer_concorrentes(user_id, monkeypatch, desfaz_primeiro):
    cid, d, c = ana(user_id)
    t1 = tid(user_id, "T1")
    undo = _captura(lambda: conferencia.undo_bank_movement(user_id, d, t1))
    conv = _captura(lambda: foi_em_dinheiro(user_id, d))
    a, b = _ordered_pair(monkeypatch, *((undo, conv) if desfaz_primeiro else (conv, undo)), esperar_travado=True)
    if desfaz_primeiro:  # desfeita antes: a conversão vale e a T1 volta como sombra
        assert not isinstance(a, Exception) and not isinstance(b, Exception), (a, b)
        assert retrato(user_id) == R([carteira(0), sombra("T1", 50.0, 0), sombra("T2", 50.0, 1)],
                                     [solta("T1", -50.0, 0), solta("T2", -50.0, 1)], [], 150.0, 950.0)
    else:  # converter primeiro encontrou a declaração casada: 409, e o desfazer vale depois
        assert isinstance(a, ValueError) and str(a) == "MOVEMENT_ALREADY_MATCHED" and not isinstance(b, Exception), (a, b)
        assert retrato(user_id) == R([decl(0), sombra("T1", 50.0, 0), sombra("T2", 50.0, 1)],
                                     [solta("T1", -50.0, 0), solta("T2", -50.0, 1)],
                                     [("decl mercado", -50.0, None, None, True, False)], 150.0)


@pytest.mark.parametrize("troca_primeiro", [True, False])
def test_h9_converter_e_trocar_concorrentes_dao_409_nas_duas_ordens(user_id, monkeypatch, troca_primeiro):
    cid, d, c = ana(user_id)
    t1, t2 = tid(user_id, "T1"), tid(user_id, "T2")
    troca = _captura(lambda: conferencia.confirm_bank_movement(user_id, d, t2, t1))
    conv = _captura(lambda: foi_em_dinheiro(user_id, d))
    a, b = _ordered_pair(monkeypatch, *((troca, conv) if troca_primeiro else (conv, troca)), esperar_travado=True)
    erro = b if troca_primeiro else a
    assert isinstance(erro, ValueError) and str(erro) == "MOVEMENT_ALREADY_MATCHED", (a, b)
    assert retrato(user_id) == TROCADA
