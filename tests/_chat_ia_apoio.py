"""Apoio dos testes do PR-B (b) (ADR 0006): a declaração "pelo banco" pelo chat IA, pelos caminhos de produção.

Ana (`ana_ia`) tem o Nubank conectado, Carteira de R$ 200,00 e `DECLARACAO_BANCO=1`; o WhatsApp entra pelo
`handle_incoming` com `WA_IA_PRIMEIRO` (`manda_wamid`, wamid único por mensagem ou repetido = reentrega), o Discord
pelo mesmo `handle_incoming` com snowflake, e a web pelo `TestClient` (`/ai/chat` e `/ai/chat/stream`). Só o modelo é
falso (`ia`): cada rodada roteirizada é UMA chamada; o teste confere `len(ia.creates)`, então "a IA não foi chamada"
é medido, não suposto (no modo IA-primeiro, uma exceção da IA cai no roteador e passaria calada).
"""
from __future__ import annotations

import uuid

import pytest

import db
from core.handlers import declaracao_banco_textos as tx
from core.services.ai_chat.runner import _CONFIRMA
from tests._declaracao_b import carteira, declaracoes, n_lancamentos, q  # noqa: F401
from tests._fusao_of_helpers import manda_wamid, uid_pro  # noqa: F401
from tests._ia_falsa_helpers import lancamento, liga_flag, openai_falso
from tests.test_bank_movements import _bank

FRASE = "gastei 50 no mercado no pix"
RESUMO = "despesa de R$ 50,00 em mercado, hoje, pelo banco"
CONFIRMA = _CONFIRMA.format(resumo=RESUMO)
T19 = tx.ACAO_ENCERRADA
JA_RECEBIDO = tx.SIM_JA_RECEBIDO  # o "sim" já visto que não gravou nada (D-a, rodada 8)


def t3(seq: int) -> str:
    return tx.REPETIDA.format(seq=seq)


def banco(valor=50, **extra):
    """A rodada do modelo que pede a declaração (o `add_launch` com forma "banco")."""
    return lancamento(valor, forma_pagamento="banco", **extra)


@pytest.fixture
def ana_ia(uid_pro, monkeypatch):  # noqa: F811
    _bank(uid_pro)
    q("update accounts set balance=200 where user_id=%s returning 1", (uid_pro,))
    monkeypatch.setenv("DECLARACAO_BANCO", "1")
    liga_flag(monkeypatch)
    return uid_pro


def ia(monkeypatch, *rodadas):
    return openai_falso(monkeypatch, *rodadas)


def wamid() -> str:
    return "wamid.t-" + uuid.uuid4().hex


def wa(uid: int, texto: str, mid: str | None = None) -> str:
    return "\n".join(manda_wamid(uid, texto, mid))


def dc(uid: int, texto: str, mid: str) -> str:
    import core.handle_incoming as hi
    from core.types import IncomingMessage
    out = hi.handle_incoming(IncomingMessage(platform="discord", user_id=uid, text=texto, message_id=mid,
                                             attachments=[], external_id=str(uid), raw={}))
    return out[0].text


def livro(uid: int) -> list[dict]:
    return q("""select papel, estado, acao is not null as ligado, launch_id, user_seq from ia_declaracao_livro
                 where user_id=%s order by id""", (uid,), fetch=True)


def pendencia_ia(uid: int):
    return db.ai_get_pending_action(uid)


def valores(uid: int) -> list[float]:
    return [float(d["valor"]) for d in declaracoes(uid)]
