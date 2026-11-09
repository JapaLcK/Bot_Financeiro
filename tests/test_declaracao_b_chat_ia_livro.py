"""PR-B (b) (ADR 0006), D7=B e D9=B: o livro da IA (`db/declaracao_livro.py`). Sobrevive ao apagar a declaração, não é
podado, e sai só no reset e na exclusão da conta, sem tocar no do vizinho. O escritor só grava, no mesmo commit, a ação
que o "sim" deste turno consumiu (M8); armada, abandonada, descartada, inexistente ou de outro "sim": nada fica gravado.
Controles negativos: tirar a tabela de `_RESET_TABLES` deixa linhas depois do reset (T12); tirar de `user_owned_tables`
deixa vermelho `test_lock_ordem_exclusao.py` (a tabela está em `_TABELAS_VARRIDAS`); apagar o livro no
`delete_launch_and_rollback` faz o "sim" velho consumir a pendência nova (T10, no arquivo do WhatsApp); o escritor
aceitando ação não consumida deixa vermelho `test_escritor_recusa_acao_que_este_sim_nao_consumiu`.
"""
from __future__ import annotations

import uuid

import pytest

import db
from conftest import promote_to_pro
from db import declaracao_livro as livro_db
from db.bank_declaracoes import DeclaracaoRepetida
from db.privacy import delete_user_data, reset_user_data
from core.handlers import declaracao_banco_textos as tx
from tests._chat_ia_apoio import ana_ia, banco, declaracoes, ia, livro, pendencia_ia, q, uid_pro, wa  # noqa: F401
from tests._declaracao_b import n_lancamentos

SENHA = "Senha-do-reset-1"


def _vizinho() -> int:
    uid = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(uid)
    promote_to_pro(uid)
    livro_db.registra_acao(uid, "wa:viz:0", H)
    livro_db.registra_sim(uid, "wa:viz-sim")
    return uid


def _gravar(uid, monkeypatch):
    ia(monkeypatch, banco())
    wa(uid, "gastei 50 no mercado no pix")
    wa(uid, "sim")
    assert len(livro(uid)) == 2


def test_t17_apagar_a_declaracao_nao_muda_o_livro(ana_ia, monkeypatch):
    _gravar(ana_ia, monkeypatch)
    antes = livro(ana_ia)
    (d,) = declaracoes(ana_ia)
    assert db.delete_launch_and_rollback(ana_ia, d["id"]) is None
    assert declaracoes(ana_ia) == [] and livro(ana_ia) == antes


def test_t12_reset_apaga_o_livro_do_usuario_e_nao_o_do_vizinho(ana_ia, monkeypatch):
    from db.users import _hash_password
    _gravar(ana_ia, monkeypatch)
    viz = _vizinho()
    q("update auth_accounts set password_hash=%s where user_id=%s returning 1", (_hash_password(SENHA), ana_ia))
    reset_user_data(ana_ia, SENHA)
    assert livro(ana_ia) == [] and len(livro(viz)) == 2


def test_t12_exclusao_apaga_o_livro_do_usuario_e_nao_o_do_vizinho(ana_ia, monkeypatch):
    _gravar(ana_ia, monkeypatch)
    viz = _vizinho()
    delete_user_data(ana_ia)
    assert livro(ana_ia) == [] and len(livro(viz)) == 2


H = "hash-do-payload"  # o escritor só confere a igualdade; o cálculo está em `test_declaracao_b_chat_ia_payload.py`


def _escreve(uid, acao, sim, payload_hash=H):
    return db.add_launch_and_update_balance(uid, "despesa", 50, "mercado", "mercado", "mercado", declaracao_banco=True,
                                            chave_acao=acao, confirmacao=sim, payload_hash=payload_hash)


def _consumida(uid, acao="web:a:0", sim="web:s"):
    livro_db.registra_acao(uid, acao, H)
    livro_db.registra_sim(uid, sim)
    assert livro_db.consome(uid, acao, sim) is True


def test_escritor_grava_so_a_acao_consumida_por_este_sim(ana_ia):
    """Positivo: a ação que o "sim" consumiu passa a confirmada no mesmo commit do lançamento."""
    _consumida(ana_ia)
    _, seq, _ = _escreve(ana_ia, "web:a:0", "web:s")
    assert livro_db.le(ana_ia, "web:a:0")["estado"] == "confirmada" and livro_db.le(ana_ia, "web:a:0")["user_seq"] == seq
    with pytest.raises(DeclaracaoRepetida):  # a mesma ação de novo
        _escreve(ana_ia, "web:a:0", "web:s")
    assert n_lancamentos(ana_ia) == 1


@pytest.mark.parametrize("caso", ["armada", "abandonada", "descartada", "inexistente", "outro_sim"])
def test_escritor_recusa_acao_que_este_sim_nao_consumiu(ana_ia, caso):
    """M8 (decisão do dono): payload do modelo ou do histórico nunca autoriza; a ação ativa e confirmada, sim."""
    if caso in ("armada", "abandonada", "descartada"):
        livro_db.registra_acao(ana_ia, "web:a:0", H)  # "abandonada" é a mesma linha: `armada` sem pendência viva
        if caso == "descartada":
            livro_db.descarta(ana_ia, "web:a:0")
    if caso == "outro_sim":
        _consumida(ana_ia, sim="web:outro")
    livro_db.registra_sim(ana_ia, "web:s")
    with pytest.raises(ValueError, match="DECLARACAO_NAO_CONFIRMADA"):
        _escreve(ana_ia, "web:a:0", "web:s")
    assert n_lancamentos(ana_ia) == 0


@pytest.mark.parametrize("gravado,enviado", [(H, "outro-hash"), (None, H), (None, None)])
def test_escritor_recusa_payload_que_nao_e_o_congelado(ana_ia, gravado, enviado):
    """M8: a ação consumida por este "sim", mas com outro conteúdo (ou sem hash), não grava."""
    livro_db.registra_acao(ana_ia, "web:a:0", gravado)
    livro_db.registra_sim(ana_ia, "web:s")
    assert livro_db.consome(ana_ia, "web:a:0", "web:s") is True
    with pytest.raises(ValueError, match="DECLARACAO_NAO_CONFIRMADA"):
        _escreve(ana_ia, "web:a:0", "web:s", enviado)
    assert n_lancamentos(ana_ia) == 0


def test_consumo_e_tudo_ou_nada(ana_ia):
    """Um "sim" já ligado a outra ação não consome: a ação fica armada (a reentrega dele já teria respondido T3)."""
    _consumida(ana_ia, acao="web:b:0")
    livro_db.registra_acao(ana_ia, "web:a:0", H)
    assert livro_db.consome(ana_ia, "web:a:0", "web:s") is False
    assert livro_db.le(ana_ia, "web:a:0")["estado"] == "armada" and livro_db.le(ana_ia, "web:s")["acao"] == "web:b:0"


def test_livro_isolado_por_usuario(ana_ia):
    viz = _vizinho()
    assert livro_db.le(ana_ia, "wa:viz:0") is None and livro_db.registra_sim(ana_ia, "wa:viz-sim") is True
    assert livro_db.le(viz, "wa:viz-sim")["acao"] is None


def test_juncao_leva_o_livro_e_o_sim_da_origem_nao_consome_a_declaracao_do_destino(ana_ia, monkeypatch):
    """Isolamento na junção (Manager r7): o livro da origem vai para o destino (`_DESTINO_VENCE`). Sem isso ele sumiria
    pela cascata, e o "sim" S1 da origem, reentregue depois da junção, pareceria novo e consumiria a declaração viva do
    destino. Com ele: o texto neutro, a pendência intacta, e o "sim" novo grava uma vez."""
    from core.handlers import declaracao_banco as decl
    from tests._chat_ia_apoio import CONFIRMA, FRASE, valores, wa, wamid
    from tests._ia_falsa_helpers import liga_flag
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    liga_flag(monkeypatch)
    origem = int(uuid.uuid4().int % 1_000_000_000)
    db.ensure_user(origem)
    s1 = wamid()
    with decl.turno("wa", s1):
        assert decl.registrar_mensagem(origem, "sim") is True  # a origem já recebeu S1
    db.merge_users(origem, ana_ia)
    assert q("select count(*) n from users where id=%s", (origem,))["n"] == 0
    ia(monkeypatch, banco())
    assert wa(ana_ia, FRASE) == CONFIRMA
    viva = pendencia_ia(ana_ia)["created_at"]
    assert wa(ana_ia, "sim", s1) == tx.SIM_JA_RECEBIDO
    assert pendencia_ia(ana_ia)["created_at"] == viva and valores(ana_ia) == []
    assert "registrada pelo banco" in wa(ana_ia, "sim") and valores(ana_ia) == [50.0]
