"""PR-A2c (ADR 0006): o lembrete NÃO muda dinheiro (I9, G8) e a conversa de verdade (G9): pelo `handle_incoming` real,
com estado real, duas mensagens de assuntos diferentes. Retrato completo (`tests/_retrato_declaracao.py`) antes e depois.

Controles NEGATIVOS (numa CÓPIA; `db/bank_lembrete.py`):
- G8. gravar fora de `lembrete_em` no caminho do lembrete (injeção: `update accounts set balance = balance + 1` no
  claim, ou `update launches set valor = valor + 1`) -> VERMELHO `test_o_lembrete_so_escreve_lembrete_em[enviado]` e
  `[recusado]` (o retrato muda: `C`, `L`). Positivo: os mesmos testes sem a injeção, e a linha da declaração (todas as
  colunas menos `lembrete_em`) igual.
- G9. tirar o passo do loop (`test_loop_real_manda_o_lembrete`, em `test_declaracao_a2c_envio.py`) liga a conversa
  ao laço; aqui, tirar `lembrete_em is null` do `_VIVA` -> VERMELHO `test_conversa_lembrete_resolve_no_app_e_nao_reenvia`
  (o 2º tick reenvia a que sobrou).

O QUE ESTA CONVERSA NÃO PROVA (CLAUDE.md §3): os testes de WhatsApp mockam o LLM/NLP (`ia_fora`) e o envio (a borda
HTTP). Não provam o mapeamento real telefone -> usuário (`get_or_create_canonical_user`/`attempt_whatsapp_phone_link`
de produção), a classificação REAL de "foi em dinheiro" pelo NLP/IA (aqui cai na IA falsa), o aceite da Meta, a janela
real de 24 h da Meta (aqui é `last_activity_at`, que o Discord também escreve) nem a renderização do negrito no
aparelho. Só no número real, depois do deploy.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

import core.services.declaracao_lembrete as dl
import db
from db.bank_dinheiro import foi_em_dinheiro
from tests._fusao_of_helpers import conecta_banco, ia_fora, manda, saldo_bruto, soma_delta_conta, ultimo_launch, uid_pro  # noqa: F401
from tests._lembrete_declaracao import (  # noqa: F401 (_event_logs é fixture autouse)
    FONE_A, _event_logs, agora, armar, corpos, declara_ha, dono, lembretes, para, reservadas)
from tests._patrimonio_helpers import q
from tests._retrato_declaracao import retrato
from tests.test_declaracao_a2b2_dinheiro import ana2


def _linhas(uid):
    """Todas as colunas da declaração menos `lembrete_em`: o que o lembrete NÃO pode tocar."""
    return q("""select to_jsonb(d) - 'lembrete_em' j from bank_movement_declarations d
                  where d.user_id=%s order by d.launch_id""", (uid,), fetch=True)


@pytest.mark.parametrize("status", [200, 401], ids=["enviado", "recusado"])
def test_o_lembrete_so_escreve_lembrete_em(user_id, monkeypatch, status):
    """I9: lançamento, vínculo, saldo da Carteira, sombra e totais do mês iguais antes e depois; reservar OU soltar."""
    ag = agora()
    posts = armar(monkeypatch, status=status)
    cid, d, c40, _ = ana2(user_id)
    q("update bank_movement_declarations set declared_at=%s where launch_id=%s", (ag - timedelta(days=8), d))
    q("update launches set criado_em=%s where id=%s", (ag - timedelta(days=8), d))
    dono(user_id, FONE_A, ag)
    antes, linhas, saldo = retrato(user_id), _linhas(user_id), saldo_bruto(user_id)

    dl.notify_declaracoes_whatsapp_once(now=ag)

    assert len(para(posts, FONE_A)) == 1
    assert retrato(user_id) == antes and _linhas(user_id) == linhas and saldo_bruto(user_id) == saldo
    assert saldo == soma_delta_conta(user_id) + 1000  # a Carteira de Ana: 1000 de partida, menos o #40 em dinheiro
    assert (lembretes(user_id)[d] is not None) == (status == 200)


def test_conversa_lembrete_resolve_no_app_e_nao_reenvia(uid_pro, ia_fora, monkeypatch):
    """O usuário lança em dinheiro pelo WhatsApp (abre a janela com o escritor real), recebe o lembrete só da
    declaração velha (sem `#N`, D9) e resolve uma pelo núcleo do "foi em dinheiro" do /app (A2b). O lembrete não
    mexeu no dinheiro, o 2º tick não reenvia e a que sobrou segue reservada: uma vez por declaração."""
    uid = uid_pro
    ag = agora()
    posts = armar(monkeypatch)
    conecta_banco(uid, "1000.00")
    db.bind_identity("whatsapp", FONE_A, uid)
    velha = declara_ha(uid, ag, 8, alvo="mercado", valor=50)
    outra = declara_ha(uid, ag, 9, alvo="padaria", valor=12)
    nova = declara_ha(uid, ag, 2, alvo="recente", valor=7)  # 2 dias: ainda não
    manda(uid, "gastei 30 no mercado em dinheiro")
    comum = ultimo_launch(uid)
    assert comum not in (velha, outra, nova)
    saldo = saldo_bruto(uid)

    assert dl.notify_declaracoes_whatsapp_once(now=ag) == 1
    [corpo] = corpos(posts, FONE_A)
    assert "gasto mercado," in corpo and "gasto padaria," in corpo and "#" not in corpo
    assert "recente" not in corpo and reservadas(uid) == 2 and saldo_bruto(uid) == saldo

    foi_em_dinheiro(uid, outra)  # outra conversa, outro assunto: o usuário resolve no /app
    assert set(lembretes(uid)) == {velha, nova}
    assert dl.notify_declaracoes_whatsapp_once(now=ag) == 0 and len(para(posts, FONE_A)) == 1
    assert lembretes(uid)[velha] is not None and lembretes(uid)[nova] is None


def test_foi_em_dinheiro_pelo_whatsapp_nao_mexe_em_dinheiro(uid_pro, ia_fora, monkeypatch):
    """DP-9: o WhatsApp não tem a ação. Depois do lembrete, "foi em dinheiro" não muda lançamento, vínculo nem saldo.
    O que o Piggy responde depende do classificador real (aqui, mockado): não afirmado."""
    uid = uid_pro
    ag = agora()
    armar(monkeypatch)
    conecta_banco(uid, "1000.00")
    db.bind_identity("whatsapp", FONE_A, uid)
    declara_ha(uid, ag, 8)
    manda(uid, "oi piggy")
    assert dl.notify_declaracoes_whatsapp_once(now=ag) == 1
    antes, linhas = retrato(uid), _linhas(uid)

    manda(uid, "foi em dinheiro")

    assert retrato(uid) == antes and _linhas(uid) == linhas
