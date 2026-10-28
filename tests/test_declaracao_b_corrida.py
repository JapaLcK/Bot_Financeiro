"""PR-B (ADR 0006), G-corrida: a MESMA mensagem em dois workers (reentrega da Meta durante o processamento) grava UMA
declaração, responde "registrada" uma vez e "já registrei" nas outras, sem deadlock nem `UniqueViolation` vazando.

O mutex do escritor é o `update accounts` (a 1ª instrução); a checagem da chave vem logo depois, sob ele. O gancho
solta o 2º worker só quando o 1º já o segura e (`_esperar_backend_travado`) o 2º está parado no lock: corrida de
verdade, não duas chamadas em sequência. O `ContextVar` do turno nasce DENTRO do `handle_incoming`, na thread de quem o
chama (no runtime, `asyncio.to_thread(process_payload)` roda tudo na mesma thread), então a thread nova não precisa herdá-lo.

Controle negativo: tirar a checagem sob o mutex E o índice único (o escritor ignorar `chave_acao`) deixa 2 linhas e
vermelho `test_mesmo_wamid_em_dois_workers`. Só o `select` sob o mutex fora: o índice ainda segura (a 2ª defesa,
`test_declaracao_b_idempotencia.py::test_escritor_indice_unico_e_a_segunda_defesa`). Positivo: wamids diferentes em
paralelo gravam todos.
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread

import pytest

import db.accounts as acc
from tests._declaracao_b import ana, carteira, declaracoes, liga, manda_wamid  # noqa: F401
from tests._espera_lock import _esperar_backend_travado
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401

FRASE = "gastei 50 no mercado no pix"


def _captura(fn):
    def run():
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — quem julga é o teste
            return exc
    return run


def _par_ordenado(monkeypatch, first, second):
    """O 1º já segura o mutex do usuário (o `update accounts` do escritor) quando o 2º começa, e só segue quando o 2º
    está parado no lock."""
    segurando, outro_comecou, primeira, travou = Event(), Event(), [], []
    real = acc.chave_existente

    def gancho(cur, uid, chave):
        achou = real(cur, uid, chave)
        if primeira and current_thread().ident == primeira[0] and not segurando.is_set():
            segurando.set()
            assert outro_comecou.wait(5)
            travou.append(_esperar_backend_travado(5))
        return achou
    monkeypatch.setattr(acc, "chave_existente", gancho)

    def run_first():
        primeira.append(current_thread().ident)
        return first()

    def run_second():
        assert segurando.wait(10)
        outro_comecou.set()
        return second()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = pool.submit(run_first), pool.submit(run_second)
        resultado = a.result(timeout=30), b.result(timeout=30)
    assert travou == [True], "o 2º worker não chegou a esperar o lock do 1º: não foi corrida"
    return resultado


def test_mesmo_wamid_em_dois_workers(ana, liga, monkeypatch):
    w = "wamid.R4-" + uuid.uuid4().hex
    envia = _captura(lambda: manda_wamid(ana, FRASE, w))
    a, b = _par_ordenado(monkeypatch, envia, envia)
    assert not isinstance(a, Exception) and not isinstance(b, Exception), (a, b)
    assert "registrada pelo banco" in a[0] and "já tinha registrado" in b[0], (a, b)
    assert len(declaracoes(ana)) == 1 and carteira(ana) == 200.0


def test_mesmo_wamid_em_oito_threads_sem_barreira(ana, liga):
    w = "wamid.R4b-" + uuid.uuid4().hex
    envia = _captura(lambda: manda_wamid(ana, FRASE, w))
    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = [f.result(timeout=60) for f in [pool.submit(envia) for _ in range(8)]]
    erros = [r for r in resultados if isinstance(r, Exception)]
    assert not erros, erros  # nem deadlock nem UniqueViolation vazando
    textos = [r[0] for r in resultados]
    assert sum("registrada pelo banco" in t for t in textos) == 1, textos
    assert sum("já tinha registrado" in t for t in textos) == 7, textos
    assert len(declaracoes(ana)) == 1 and carteira(ana) == 200.0


def test_wamids_diferentes_em_paralelo_gravam_todos(ana, liga):
    """Positivo do grupo: a chave é por ação, então oito mensagens iguais com wamids diferentes são oito gastos."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = [f.result(timeout=60) for f in [
            pool.submit(_captura(lambda: manda_wamid(ana, FRASE))) for _ in range(8)]]
    assert not [r for r in resultados if isinstance(r, Exception)], resultados
    assert len(declaracoes(ana)) == 8 and carteira(ana) == 200.0
