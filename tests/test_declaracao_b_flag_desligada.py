"""PR-B (ADR 0006), G-F0: com a flag DESLIGADA o comportamento é o de hoje, byte a byte, em TODAS as portas.

Para cada porta da tabela (`tests/_declaracao_b.PORTAS`), com banco conectado e sem a flag: nada em
`bank_movement_declarations`, nada nos lançamentos, Carteira intacta, a resposta é a recusa de hoje ("Não registrei"),
e nenhuma pendência fica armada. A flag é ligada só para outro usuário (lista de ids) em `test_flag_de_outro_usuario`.

Controle negativo: `declara` devolvendo `decisao == fp.BANCO` (sem a flag) deixa vermelha a tabela inteira.
Positivo: sem banco a flag não é lida e a Carteira grava como sempre; os dez arquivos antigos de Q40 seguem verdes
sem edição (rodados no relato).
"""
from __future__ import annotations

import pytest

import db
from tests._declaracao_b import (PORTAS, ana, carteira, declaracoes, fala, liga, manda_wamid,  # noqa: F401
                                 n_lancamentos, pendencia)
from tests._fusao_of_helpers import ia_fora, uid_pro  # noqa: F401
from tests._retrato_declaracao import retrato


@pytest.mark.parametrize("nome", sorted(PORTAS))
def test_porta_desligada_e_a_recusa_de_hoje(ana, monkeypatch, nome):
    roteiro, _ = PORTAS[nome]
    respostas = roteiro(ana, monkeypatch)
    assert any("Não registrei" in r for r in respostas), respostas
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and carteira(ana) == 200.0
    assert pendencia(ana) is None


def test_flag_de_outro_usuario_nao_libera_este(ana, monkeypatch):
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    monkeypatch.setenv("DECLARACAO_BANCO_USER_IDS", str(ana + 1))
    r = manda_wamid(ana, "gastei 50 no mercado no pix")
    assert "Não registrei" in r[0] and declaracoes(ana) == [] and n_lancamentos(ana) == 0


def test_flag_ligada_para_o_usuario_certo_e_desligada_para_o_outro(ana, monkeypatch):
    """O segundo usuário (com banco) fica fora da lista: a mesma frase, uma declaração para um e a recusa para o outro."""
    from tests._fusao_of_helpers import uid_pro as _  # noqa: F401
    import uuid
    from conftest import promote_to_pro
    outro = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(outro)
    promote_to_pro(outro)
    from tests.test_bank_movements import _bank
    _bank(outro)
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    monkeypatch.setenv("DECLARACAO_BANCO_USER_IDS", f"{ana}")
    assert "registrada pelo banco" in manda_wamid(ana, "gastei 50 no mercado no pix")[0]
    assert "Não registrei" in manda_wamid(outro, "gastei 50 no mercado no pix")[0]
    assert len(declaracoes(ana)) == 1 and declaracoes(outro) == []


def test_sem_banco_ignora_a_flag(uid_pro, ia_fora, monkeypatch):
    """Sem banco `decidir` devolve CARTEIRA: "no pix" grava na Carteira, flag ligada ou não."""
    antes = carteira(uid_pro)
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    r = manda_wamid(uid_pro, "gastei 50 no mercado no pix")
    assert "Despesa registrada" in r[0] and "pelo banco" not in r[0], r
    assert declaracoes(uid_pro) == [] and n_lancamentos(uid_pro) == 1 and carteira(uid_pro) == antes - 50


def test_dinheiro_nunca_declara(ana, liga):
    manda_wamid(ana, "gastei 50 no mercado em dinheiro")
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 1 and carteira(ana) == 150.0
    manda_wamid(ana, "gastei 30 na padaria")
    r = manda_wamid(ana, "dinheiro")
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 2 and carteira(ana) == 120.0, r


def test_conta_a_pagar_paga_pelo_banco_continua_como_hoje(ana, liga):
    """DP-3: marca paga SEM lançamento (o débito vem pelo Open Finance), com a flag ligada ou não."""
    from tests.test_conta_paga_forma import B, conta_fixa
    conta = conta_fixa(ana, valor=90.0)
    r = manda_wamid(ana, "paguei a luz no pix")
    assert B.get_bill(ana, conta["id"])["status"] == "paid", r
    assert declaracoes(ana) == [] and n_lancamentos(ana) == 0 and carteira(ana) == 200.0


def test_quick_entry_legado_segue_recusando_com_a_flag_ligada(ana, liga):
    from core.services.quick_entry import handle_quick_entry
    r = handle_quick_entry(ana, "gastei 50 no pix")
    assert r.text.startswith("🐷 Não registrei") and declaracoes(ana) == [] and n_lancamentos(ana) == 0


def test_desligar_depois_nao_apaga_nada(ana, monkeypatch):
    """X8: declara com a flag, desliga: a declaração fica intacta (retrato idêntico) e "pix" volta a ser recusado."""
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    assert "registrada pelo banco" in manda_wamid(ana, "gastei 50 no mercado no pix")[0]
    antes = retrato(ana)
    monkeypatch.delenv("DECLARACAO_BANCO")
    assert retrato(ana) == antes
    assert "Não registrei" in manda_wamid(ana, "gastei 30 na padaria no pix")[0]
    assert retrato(ana) == antes
