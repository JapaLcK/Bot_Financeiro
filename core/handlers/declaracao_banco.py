"""core/handlers/declaracao_banco.py — despesa/receita "paga pelo banco" pelo WhatsApp (ADR 0006, PR-B).

Com banco conectado a resposta "banco" (Pix, débito, transferência) deixa de ser recusada e grava uma DECLARAÇÃO
(`add_launch_and_update_balance(declaracao_banco=True)`: Carteira intocada, conferida contra o extrato). Só para quem a
flag libera, e com a idempotência POR AÇÃO: a chave é a mensagem recebida (wamid), nunca o conteúdo. Duas mensagens
iguais com wamids diferentes são dois gastos.

- Flag: `DECLARACAO_BANCO` (liga) e `DECLARACAO_BANCO_USER_IDS` (restringe), lidas a cada chamada, fail-closed. Nasce
  DESLIGADA; ligar é ato do dono, fora do PR. `fp.decidir` não muda (a conta a pagar paga "pelo banco" depende dele).
- Chave: `wa:<hash_pii(wamid)>:<n>` em `launches.external_id` (índice único que já existe), n = ordem da declaração
  no turno. O wamid cru nunca vai para a coluna nem para o log. Sem wamid utilizável não se declara (T9).
"""
from __future__ import annotations

import functools
import logging
import os
from contextvars import ContextVar, Token

import db
from core.handlers import declaracao_banco_textos as tx
from core.handlers import declaracao_valor
from core.handlers import forma_pagamento as fp

logger = logging.getLogger(__name__)
_LIGADA = {"1", "true", "yes", "on"}

# [wamid, n] do turno em curso; None fora de um `handle_incoming` do WhatsApp com wamid. Aberto e fechado em
# `handle_incoming` (mesmo molde de `pergunta_no_turno`). O `handle_incoming` roda inteiro na thread do worker.
ACAO_DO_TURNO: ContextVar[list | None] = ContextVar("declaracao_acao_do_turno", default=None)


@functools.cache
def _avisa_lista_sem_id(raw: str) -> None:  # um aviso por valor e por processo, não um por mensagem
    logger.warning("DECLARACAO_BANCO_USER_IDS sem id válido: declaração pelo banco desligada")


def ligada(user_id: int) -> bool:
    """Nunca levanta: qualquer erro = desligada. Lista presente sem id válido (inclusive vazia) desliga para todos."""
    try:
        if (os.getenv("DECLARACAO_BANCO") or "").strip().lower() not in _LIGADA:
            return False
        raw = os.getenv("DECLARACAO_BANCO_USER_IDS")
        if raw is None:  # ausente: sem restrição. Presente e vazia ("", "  ", ",") = NINGUÉM (fail-closed)
            return True
        ids = {s.strip() for s in raw.split(",") if s.strip().isdigit()}
        if not ids:
            _avisa_lista_sem_id(raw)
            return False
        return str(int(user_id)) in ids
    except Exception:
        return False


def declara(user_id: int, decisao: str) -> bool:
    """O lançamento com decisão BANCO grava como declaração (em vez de recusar)?"""
    return decisao == fp.BANCO and ligada(user_id)


def msg_misto(user_id: int) -> str:
    """Mistura de formas num lançamento: T7 com a flag; a conta a pagar mantém `fp.msg_misto`."""
    return tx.MISTO if ligada(user_id) else fp.msg_misto()


def abre_turno(msg) -> Token:
    wamid = msg.message_id if msg.platform == "whatsapp" and str(msg.message_id or "").startswith("wamid.") else None
    return ACAO_DO_TURNO.set([wamid, 0] if wamid else None)


def fecha_turno(token: Token) -> None:
    ACAO_DO_TURNO.reset(token)


def proxima_chave() -> str | None:
    """A chave da próxima declaração do turno (None = sem wamid utilizável; nunca timestamp nem hash do corpo)."""
    turno = ACAO_DO_TURNO.get()
    if not turno:
        return None
    from core.crypto import hash_pii
    chave = f"wa:{hash_pii(turno[0], kind='external_id')}:{turno[1]}"
    turno[1] += 1
    return chave


class NaoGravou(str):
    """Texto de quem não gravou NADA e pode ser tentado de novo (reentrega, sem como garantir a ação). A fila de
    valores devolve o item: sem isto uma reentrega da resposta gastaria o item seguinte."""


def preparar(user_id: int) -> str:
    """A chave de ação da próxima declaração, ou o texto de "sem como garantir a ação" (`NaoGravou`: nada foi
    gravado e dá para tentar de novo). Sem wamid utilizável não se declara; a consulta do banco é a de verdade,
    porque `regra_ativa` devolve True quando cai, e sem banco a declaração nunca casaria."""
    chave = proxima_chave()
    if chave is None:
        return NaoGravou(tx.SEM_GARANTIA)
    try:
        if db.has_open_finance_connections(user_id):
            # Reentrega: a chave já gravou. Dizer T3 aqui (antes do teto do plano); o escritor segue sendo a autoridade.
            from db.bank_declaracoes import DeclaracaoRepetida, chave_existente
            with db.get_conn() as conn, conn.cursor() as cur:
                ja = chave_existente(cur, user_id, chave)
            return recusa(DeclaracaoRepetida(ja["id"], ja["user_seq"]), user_id, 0) if ja else chave
    except Exception as exc:
        logger.warning("declaração: consulta do banco falhou (%s, sqlstate=%s)", type(exc).__name__,
                       getattr(exc, "sqlstate", None), extra={"user_id": user_id})
    return NaoGravou(tx.SEM_GARANTIA)


def recusa(exc: Exception, user_id: int, valor: float) -> str | None:
    """O texto para o que o escritor da declaração recusou (nada foi gravado); None = não é recusa, quem chamou
    re-levanta. A mensagem do escritor (`DECLARACAO_*: ...`) nunca chega ao usuário."""
    from db.bank_declaracoes import DeclaracaoRepetida
    if isinstance(exc, DeclaracaoRepetida):
        logger.info("declaracao_repetida", extra={"user_id": user_id})
        return NaoGravou(tx.REPETIDA.format(seq=exc.user_seq))
    codigo = str(exc).split(":")[0] if isinstance(exc, ValueError) else ""
    return {"DECLARACAO_VALOR_CASAS": lambda: tx.valor_casas(valor),
            "DECLARACAO_VALOR_INVALIDO": lambda: tx.VALOR_INVALIDO,
            "DECLARACAO_INTERNA": lambda: tx.INTERNA}.get(codigo, lambda: None)()


def recusa_valor(texto: str, valor: float | None = None) -> str | None:
    """A recusa do leitor estrito (`declaracao_valor`) como `NaoGravou`, ou None. `valor`: o que o chamador grava quando
    não é o parser por pedaço. Só a declaração recusa: a Carteira, o cartão e a flag desligada seguem como sempre."""
    recusa = declaracao_valor.recusa(texto, valor)
    return NaoGravou(recusa) if recusa else None


tem_cartao = declaracao_valor.tem_cartao  # cartão nunca declara: o mesmo predicado para o texto e para o áudio


def linha_do_par(user_id: int, launch_id: int) -> str:
    """T2 se a declaração casou na hora com uma transação do extrato, senão T1. Pós-commit: nunca levanta."""
    sem_conta = False
    try:
        from db.bank_declaracoes import par_da_declaracao
        par = par_da_declaracao(user_id, launch_id)
        sem_conta = par is None and not db.list_bank_accounts(user_id)  # a mesma lista que a conciliação usa
    except Exception as exc:
        logger.warning("declaração: leitura do par falhou (lançamento %s, %s, sqlstate=%s)", launch_id,
                       type(exc).__name__, getattr(exc, "sqlstate", None), extra={"user_id": user_id})
        par = None
    return tx.com_par(par) if par else tx.SEM_CONTA if sem_conta else tx.SEM_PAR
