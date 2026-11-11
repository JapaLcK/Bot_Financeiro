"""PR-B (ADR 0006), G-corrida: a MESMA resposta em vários workers (reentrega da Meta durante o processamento) grava UMA
declaração, responde "registrada" uma vez e nunca grava de novo, sem deadlock nem `UniqueViolation` vazando.

Desde a #1315 o roteador só grava no "sim" da confirmação, então a corrida que chega ao escritor é a do "sim": o
árbitro é o livro (`registra_sim`, o `insert ... on conflict` atômico, e o CAS da pendência), antes do mutex do
escritor. O mutex e o índice único continuam testados direto no escritor
(`test_declaracao_b_idempotencia.py::test_escritor_*`). Antes da #1315 estes testes corriam a mensagem original, que
gravava na hora, e soltavam o 2º worker no lock do escritor (`_par_ordenado`); esse caminho deixou de ser alcançável
pelo `handle_incoming`.

Controle negativo: o "sim" sem o livro (`decl.registrar_mensagem` devolvendo sempre True) deixa vermelho
`test_mesmo_sim_em_oito_threads_sem_barreira` (a pendência é uma só e o CAS ainda segura a gravação, mas as respostas
deixam de ser "já tinha recebido"). Positivo: `test_sims_diferentes_em_paralelo_gravam_um` (o CAS).
"""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

import db.declaracao_livro as L

from core.handlers import declaracao_banco_textos as tx
from tests._declaracao_b import ana, carteira, declaracoes, liga, manda_ts, manda_wamid  # noqa: F401
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401

FRASE = "gastei 50 no mercado no pix"


def _captura(fn):
    def run():
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — quem julga é o teste
            return exc
    return run


def _em_paralelo(fns):
    with ThreadPoolExecutor(max_workers=len(fns)) as pool:
        resultados = [f.result(timeout=60) for f in [pool.submit(_captura(fn)) for fn in fns]]
    assert not [r for r in resultados if isinstance(r, Exception)], resultados  # nem deadlock nem UniqueViolation
    return [r[0] for r in resultados]


def test_mesmo_sim_em_dois_workers(ana, liga):
    manda_wamid(ana, FRASE)
    w = "wamid.R4-" + uuid.uuid4().hex
    textos = _em_paralelo([lambda: manda_ts(ana, "sim", w)] * 2)
    assert sum("registrada pelo banco" in t for t in textos) == 1, textos
    assert len(declaracoes(ana)) == 1 and carteira(ana) == 200.0


def test_mesmo_sim_em_oito_threads_sem_barreira(ana, liga):
    manda_wamid(ana, FRASE)
    w = "wamid.R4b-" + uuid.uuid4().hex
    textos = _em_paralelo([lambda: manda_ts(ana, "sim", w)] * 8)
    assert sum("registrada pelo banco" in t for t in textos) == 1, textos
    assert all("registrada pelo banco" in t or t in (tx.SIM_JA_RECEBIDO, tx.REPETIDA.format(seq=1)) for t in textos), textos
    assert len(declaracoes(ana)) == 1 and carteira(ana) == 200.0


def test_sims_diferentes_em_paralelo_gravam_um(ana, liga):
    """Oito "sim" diferentes (wamids diferentes) para uma confirmação: o CAS da pendência deixa um só gravar."""
    manda_wamid(ana, FRASE)
    textos = _em_paralelo([lambda: manda_ts(ana, "sim")] * 8)
    assert sum("registrada pelo banco" in t for t in textos) == 1, textos
    assert len(declaracoes(ana)) == 1 and carteira(ana) == 200.0


@pytest.mark.parametrize("n", [2, 8])
def test_mesmo_original_em_n_workers_arma_uma_e_o_sim_grava(ana, liga, monkeypatch, n):
    """O sucessor do antigo `test_mesmo_wamid_em_dois_workers`: a mesma mensagem entregue em N workers ao mesmo tempo.
    A barreira em `registra_acao` segura todos depois do gancho de reentrega e antes de qualquer um anotar a ação, então
    todos chegam ao `arma` juntos (corrida de verdade). Todos respondem a mesma pergunta e o "sim" grava 1. Negativo
    (rodado): sem a guarda de `arma` para a mesma ação (o claim perdido, ou a "anterior" sendo ela mesma, encerrava a
    ação viva no livro), o "sim" recebe T19 e nada grava."""
    barreira, real = Barrier(n, timeout=20), L.registra_acao

    def registra(*a, **k):
        barreira.wait()
        return real(*a, **k)
    monkeypatch.setattr(L, "registra_acao", registra)
    w = "wamid.par-" + uuid.uuid4().hex
    textos = _em_paralelo([lambda: manda_ts(ana, FRASE, w)] * n)
    monkeypatch.setattr(L, "registra_acao", real)
    assert all(t.startswith("🐷 Só confirmando") for t in textos), textos
    assert "registrada pelo banco" in manda_ts(ana, "sim")[0]
    assert len(declaracoes(ana)) == 1 and carteira(ana) == 200.0
