"""Q40 no Essencial com banco conectado: foto e áudio único gravam dinheiro vivo
na Carteira e recusam pix.

Par de `tests/test_forma_pagamento_conversa.py`, que mede a regra no Pro. Aqui a
conversa inteira passa pelo `handle_incoming` no Essencial, o menor plano com
banco, áudio e foto (`core/services/plan_limits.py`), e a foto entra pelo anexo
em vez de semear `confirm_media_launch` à mão.

São falsas a leitura da mídia (`analyze_image` e `transcribe_audio`, trocados em
`core.handle_incoming`) e a IA (`ai_chat.chat`, trocada pela fixture `ia_fora`):
verde aqui não prova OCR, transcrição nem resposta da IA reais.

Os casos de pix valem com `DECLARACAO_BANCO` desligada, o estado da suíte: ligada,
a recusa vira declaração pelo banco.

`origem='carteira'` sozinho não prova nada, porque é o padrão de
`db/accounts.py`. A prova de que o dinheiro saiu é o delta de `carteira()`.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

import core.handle_incoming as hi
import db
from conftest import usuario_pagante
from core.services.plan_service import feature_enabled, get_plan_tier, is_pro
from core.types import Attachment, IncomingMessage
from tests._fusao_of_helpers import ia_fora, manda  # noqa: F401 (fixture)
from tests.test_forma_pagamento_conversa import carteira, fala, manuais, pendencia
from tests.test_manual_launches_carteira_piggy import _connect_fake_bank
from utils_date import today_tz

CUPOM = {"tem_dado_financeiro": True, "tipo": "despesa", "valor": 37.9, "alvo": "Padaria do Zé",
         "categoria": "alimentação", "data": None}


def essencial() -> int:
    """O gate de `_handle_audio`/`_handle_image` é fail-open (exceção libera),
    então a mídia chegar ao handler não prova que o plano dá o recurso."""
    uid = usuario_pagante("essencial")
    assert get_plan_tier(uid) == "essencial" and not is_pro(uid)
    assert feature_enabled(uid, "audio_enabled") and feature_enabled(uid, "image_ocr_enabled")
    return uid


@pytest.fixture
def com_banco() -> int:
    uid = essencial()
    _connect_fake_bank(uid)
    return uid


def fotografa(monkeypatch, uid: int, leitura: dict = CUPOM) -> str:
    """Uma foto de WhatsApp pelo caminho de produção; afirma que o anexo foi lido."""
    lidas = []

    def _le(data, filename):
        lidas.append((data, filename))
        return leitura

    monkeypatch.setattr(hi, "analyze_image", _le)
    out = hi.handle_incoming(IncomingMessage(
        platform="whatsapp", user_id=uid, text="", message_id="1",
        attachments=[Attachment("cupom.jpg", "image/jpeg", b"foto")], external_id=str(uid), raw={}))
    assert lidas == [(b"foto", "cupom.jpg")]
    return out[0].text


def lancamentos(uid: int) -> list[dict]:
    with db.get_conn() as conn, conn.cursor() as cur:
        cur.execute("select tipo, valor, alvo, categoria, origem, criado_em from launches "
                    "where user_id=%s order by id", (uid,))
        return cur.fetchall()


def resumo(uid: int) -> list[tuple]:
    return [(r["alvo"], float(r["valor"]), r["origem"]) for r in lancamentos(uid)]


# ── Foto ─────────────────────────────────────────────────────────────────────

def test_foto_confirma_pergunta_a_forma_e_dinheiro_grava_na_carteira(com_banco, ia_fora, monkeypatch):
    antes = carteira(com_banco)
    r = fotografa(monkeypatch, com_banco)
    assert "R$ 37,90" in r and "Padaria do Zé" in r and "O lançamento está correto?" in r, r
    assert pendencia(com_banco) == "confirm_media_launch" and manuais(com_banco) == 0

    r = manda(com_banco, "sim")
    assert "R$ 37,90" in r and "dinheiro vivo" in r, r
    assert pendencia(com_banco) == "payment_method_choice" and manuais(com_banco) == 0

    r = manda(com_banco, "dinheiro")
    assert "Lançamento registrado" in r and "Carteira Piggy" in r, r
    assert resumo(com_banco) == [("Padaria do Zé", 37.9, "carteira")]
    assert lancamentos(com_banco)[0]["categoria"] == "alimentação"
    assert carteira(com_banco) == pytest.approx(antes - 37.9)
    assert pendencia(com_banco) != "payment_method_choice"
    assert not ia_fora


def test_foto_respondida_pix_nao_grava(com_banco, ia_fora, monkeypatch):
    antes = carteira(com_banco)
    fotografa(monkeypatch, com_banco)
    manda(com_banco, "sim")
    r = manda(com_banco, "pix")
    assert "Não registrei" in r and "Open Finance" in r, r
    assert manuais(com_banco) == 0 and pendencia(com_banco) is None
    assert carteira(com_banco) == pytest.approx(antes)
    assert not ia_fora


def test_foto_grava_na_data_do_recibo(com_banco, ia_fora, monkeypatch):
    dia = today_tz() - timedelta(days=3)
    fotografa(monkeypatch, com_banco, {**CUPOM, "data": dia.isoformat()})
    manda(com_banco, "sim")
    manda(com_banco, "dinheiro")
    assert [r["criado_em"].date() for r in lancamentos(com_banco)] == [dia]
    assert not ia_fora


# ── Áudio único ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("frase", [
    "Gastei 50 reais no mercado em dinheiro.",
    "Gastei R$ 50,00 no mercado, em dinheiro.",
    "Paguei em dinheiro 50 reais no mercado.",
    "gastei 50 no mercado em espécie",
])
def test_audio_que_declara_dinheiro_grava_sem_perguntar(com_banco, ia_fora, monkeypatch, frase):
    antes = carteira(com_banco)
    r = fala(monkeypatch, com_banco, frase)
    assert "Entendi:" in r and "Despesa registrada" in r and "dinheiro vivo" not in r, r
    assert resumo(com_banco) == [("mercado", 50.0, "carteira")]
    assert carteira(com_banco) == pytest.approx(antes - 50)
    assert pendencia(com_banco) == "undo_audio"
    assert not ia_fora


def test_audio_com_a_forma_em_frase_propria_grava_sem_perguntar(com_banco, ia_fora, monkeypatch):
    # Sem afirmar o alvo de propósito: hoje grava "mercado. Foi", defeito de produção.
    antes = carteira(com_banco)
    r = fala(monkeypatch, com_banco, "Gastei 50 reais no mercado. Foi em dinheiro.")
    assert "Despesa registrada" in r and "dinheiro vivo" not in r, r
    assert [float(l["valor"]) for l in lancamentos(com_banco)] == [50.0]
    assert carteira(com_banco) == pytest.approx(antes - 50)
    assert pendencia(com_banco) != "payment_method_choice"
    assert not ia_fora


@pytest.mark.parametrize("frase", ["Gastei 50 reais no mercado.", "gastei 50 no mercado"])
@pytest.mark.parametrize("canal", ["texto", "audio"])
def test_audio_sem_forma_pergunta_e_dinheiro_grava(com_banco, ia_fora, monkeypatch, frase, canal):
    antes = carteira(com_banco)
    r = fala(monkeypatch, com_banco, frase)
    assert "R$ 50,00" in r and "dinheiro vivo" in r, r
    assert manuais(com_banco) == 0 and pendencia(com_banco) == "payment_method_choice"

    r = manda(com_banco, "dinheiro") if canal == "texto" else fala(monkeypatch, com_banco, "Dinheiro.")
    assert "Despesa registrada" in r, r
    assert resumo(com_banco) == [("mercado", 50.0, "carteira")]
    assert carteira(com_banco) == pytest.approx(antes - 50)
    assert pendencia(com_banco) != "payment_method_choice"
    assert not ia_fora


@pytest.mark.parametrize("frase", ["Gastei 50 reais no mercado no Pix.",
                                   "Gastei 50 reais no mercado. Foi no Pix."])
def test_audio_que_declara_pix_nao_grava(com_banco, ia_fora, monkeypatch, frase):
    antes = carteira(com_banco)
    r = fala(monkeypatch, com_banco, frase)
    assert "Não registrei" in r and "Open Finance" in r, r
    assert manuais(com_banco) == 0 and pendencia(com_banco) is None
    assert carteira(com_banco) == pytest.approx(antes)
    assert not ia_fora


def test_audio_sem_forma_respondido_pix_nao_grava(com_banco, ia_fora, monkeypatch):
    antes = carteira(com_banco)
    fala(monkeypatch, com_banco, "Gastei 50 reais no mercado.")
    r = manda(com_banco, "pix")
    assert "Não registrei" in r, r
    assert manuais(com_banco) == 0 and pendencia(com_banco) is None
    assert carteira(com_banco) == pytest.approx(antes)
    assert not ia_fora


# ── Conversa inteira ─────────────────────────────────────────────────────────

def test_audio_e_foto_em_sequencia_somam_na_carteira(com_banco, ia_fora, monkeypatch):
    antes = carteira(com_banco)
    fala(monkeypatch, com_banco, "Gastei 20 reais no uber em dinheiro.")
    assert pendencia(com_banco) == "undo_audio"

    # O `undo_audio` em aberto não pode engolir a pergunta do áudio seguinte.
    r = fala(monkeypatch, com_banco, "Gastei 50 reais no mercado.")
    assert "dinheiro vivo" in r, r
    assert pendencia(com_banco) == "payment_method_choice" and manuais(com_banco) == 1
    manda(com_banco, "dinheiro")

    fotografa(monkeypatch, com_banco)
    manda(com_banco, "sim")
    assert manuais(com_banco) == 2
    manda(com_banco, "dinheiro")
    assert [alvo for alvo, _, _ in resumo(com_banco)] == ["uber", "mercado", "Padaria do Zé"]
    assert carteira(com_banco) == pytest.approx(antes - 107.9)
    assert not ia_fora


def test_pix_de_um_usuario_nao_mexe_na_pergunta_do_outro(com_banco, ia_fora, monkeypatch):
    outro = essencial()
    _connect_fake_bank(outro)
    antes = carteira(com_banco)
    fotografa(monkeypatch, com_banco)
    manda(com_banco, "sim")

    fala(monkeypatch, outro, "Gastei 50 reais no mercado.")
    assert "Não registrei" in manda(outro, "pix")
    assert pendencia(com_banco) == "payment_method_choice" and manuais(com_banco) == 0

    manda(com_banco, "dinheiro")
    assert resumo(com_banco) == [("Padaria do Zé", 37.9, "carteira")]
    assert carteira(com_banco) == pytest.approx(antes - 37.9)
    assert manuais(outro) == 0 and pendencia(outro) is None
    assert carteira(outro) == pytest.approx(0)
    assert not ia_fora


def test_essencial_sem_banco_grava_foto_e_audio_sem_perguntar(ia_fora, monkeypatch):
    """Controle positivo: sem banco a regra não existe, e tudo grava como antes."""
    uid = essencial()
    antes = carteira(uid)
    fotografa(monkeypatch, uid)
    r = manda(uid, "sim")
    assert "Lançamento registrado" in r and "dinheiro vivo" not in r, r
    r = fala(monkeypatch, uid, "Gastei 50 reais no mercado.")
    assert "Despesa registrada" in r and "dinheiro vivo" not in r, r
    assert manuais(uid) == 2
    assert carteira(uid) == pytest.approx(antes - 87.9)
    assert not ia_fora
