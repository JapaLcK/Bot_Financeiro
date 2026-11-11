"""core/handlers/declaracao_banco.py — despesa/receita "paga pelo banco" pelo WhatsApp (ADR 0006, PR-B).

Com banco conectado a resposta "banco" (Pix, débito, transferência) deixa de ser recusada e grava uma DECLARAÇÃO
(`add_launch_and_update_balance(declaracao_banco=True)`: Carteira intocada, conferida contra o extrato). Só para quem a
flag libera, e com a idempotência POR AÇÃO: a chave é a mensagem recebida (wamid), nunca o conteúdo. Duas mensagens
iguais com wamids diferentes são dois gastos.

- Flag: `DECLARACAO_BANCO` (liga) e `DECLARACAO_BANCO_USER_IDS` (restringe), lidas a cada chamada, fail-closed. Nasce
  DESLIGADA; ligar é ato do dono, fora do PR. `fp.decidir` não muda (a conta a pagar paga "pelo banco" depende dele).
- Chave: `wa:<hash_pii(wamid)>:<n>` em `launches.external_id` (índice único que já existe), n = ordem da declaração
  no turno. O wamid cru nunca vai para a coluna nem para o log. Sem wamid utilizável não se declara (T18).
- Chat IA (PR-B (b)): o turno também se abre no Discord (`dc`, o snowflake) e na web (`web`, o `client_message_id`);
  a chave da mensagem (`chave_da_mensagem`) identifica a ação e o "sim" no livro (`db/declaracao_livro.py`). O roteador
  só declara pelo WhatsApp (`proxima_chave`).
"""
from __future__ import annotations

import functools
import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar, Token

import db
from core.handlers import declaracao_banco_textos as tx
from core.handlers import declaracao_valor
from core.handlers import forma_pagamento as fp

logger = logging.getLogger(__name__)
_LIGADA = {"1", "true", "yes", "on"}

# [prefixo, id da mensagem, n, novo, autorizada] do turno em curso; None sem id utilizável. `novo`: o "sim" desta
# mensagem chegou agora pela 1ª vez (memória do turno; None = ainda não registrado). `autorizada`: (user_id, ação) que o
# "sim" DESTE turno consumiu no CAS (`autoriza`); só ela pode ser gravada, e só neste turno. Aberto e fechado em `handle_incoming` (mesmo molde de
# `pergunta_no_turno`, roda inteiro na thread do worker) e, na web, por `turno` em volta do chat.
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
    mid = str(msg.message_id or "")
    prefixo = ("wa" if msg.platform == "whatsapp" and mid.startswith("wamid.")
               else "dc" if msg.platform == "discord" and mid.isdigit() else None)
    return ACAO_DO_TURNO.set([prefixo, mid, 0, None, None] if prefixo else None)


def fecha_turno(token: Token) -> None:
    ACAO_DO_TURNO.reset(token)


@contextmanager
def turno(prefixo: str, ident: str | None):
    """O turno de um canal sem `handle_incoming` (a web): `ident` é o id que o cliente deu à mensagem."""
    token = ACAO_DO_TURNO.set([prefixo, ident, 0, None, None] if ident else None)
    try:
        yield
    finally:
        ACAO_DO_TURNO.reset(token)


def chave_da_mensagem() -> str | None:
    """`<canal>:<hmac do id da mensagem>` (None = sem id utilizável; nunca timestamp nem hash do corpo)."""
    t = ACAO_DO_TURNO.get()
    if not t:
        return None
    from core.crypto import hash_pii
    return f"{t[0]}:{hash_pii(t[1], kind='external_id')}"


def proxima_chave() -> str | None:
    """A chave da próxima declaração do ROTEADOR no turno: só no WhatsApp (o Discord segue sem, T18)."""
    t = ACAO_DO_TURNO.get()
    if not t or t[0] != "wa":
        return None
    chave = f"{chave_da_mensagem()}:{t[2]}"
    t[2] += 1
    return chave


def registrar_mensagem(user_id: int, texto: str, papel: str | None = None) -> bool | None:
    """Anota no livro a mensagem com forma de "sim" (`e_sim`) ou de "não" (`e_nao`), ou com o `papel` que quem chama
    já decidiu (o "não" que o classificador lê, #1315), e diz se ela chegou agora pela 1ª vez. Uma vez por turno: a 2ª
    chamada devolve a memória (a 2ª ida ao banco diria "já vista"). None = flag desligada, sem id ou não é resposta."""
    t = ACAO_DO_TURNO.get()
    if not t or not ligada(user_id):
        return None
    if t[3] is None:
        wa = t[0] == "wa"  # M-8 A: as formas completas só no WhatsApp
        papel = papel or ("sim" if e_sim(texto, wa) else "nao" if e_nao(texto, wa) else None)
        if papel is None:
            return None
        from db.declaracao_livro import registra_sim
        t[3] = registra_sim(user_id, chave_da_mensagem(), papel)
    return t[3]


_SIM_COMPLETO = frozenset({"sim pode registrar", "pode registrar"})
_NAO_COMPLETO = frozenset({"nao obrigado", "nao registra", "cancela isso", "nao nao", "nope"})  # R3-2 A
SIM_ALONGADO = r"si+m+"  # "simm", "siiim"


def e_nao(texto: str | None, whatsapp: bool = True) -> bool:
    """O "não" à confirmação da declaração: o `is_cancel` da IA e, só no WhatsApp (M-8 A), as recusas completas
    aprovadas (R3-2 A, #1315). Discord e web ficam com o `is_cancel`, como antes."""
    from core.services.ai_chat.confirmations import _normalize, is_cancel
    return is_cancel(texto or "") or (whatsapp and _normalize(texto or "") in _NAO_COMPLETO)


def e_sim(texto: str | None, whatsapp: bool = True) -> bool:
    """O "sim" à confirmação da declaração: o `is_confirm` da IA e, só no WhatsApp (M-8 A), as formas completas
    aprovadas pelo dono (T-6 A, #1315): "Sim, pode registrar", "pode registrar", "sim sim" (e "sim" repetido), "simm" e
    o 👍 sozinho. "sim, mas foi 60" não é (`declaracao_correcao`: correção)."""
    import re
    from core.services.ai_chat.confirmations import _normalize, is_confirm
    if is_confirm(texto or ""):
        return True
    n = _normalize(texto or "")
    return whatsapp and (n in _SIM_COMPLETO or re.fullmatch(rf"(?:{SIM_ALONGADO} ?)+", n) is not None
                         or (not n and "👍" in (texto or "")))


def mensagem_nova() -> bool:
    t = ACAO_DO_TURNO.get()
    return bool(t and t[3])


def autoriza(user_id: int, acao: str) -> None:
    """A marca do turno (M8): o "sim" deste turno consumiu a ação. Some com o turno (o token do `ContextVar` volta no
    `fecha_turno`/`turno`, inclusive com exceção), então nunca passa para outro turno."""
    t = ACAO_DO_TURNO.get()
    if t:
        t[4] = (user_id, acao)


def autorizada(user_id: int, acao: str) -> bool:
    t = ACAO_DO_TURNO.get()
    return bool(t and t[4] == (user_id, acao))


class NaoGravou(str):
    """Texto de quem não gravou NADA e pode ser tentado de novo (reentrega, sem como garantir a ação). A fila de
    valores devolve o item: sem isto uma reentrega da resposta gastaria o item seguinte."""


def preparar(user_id: int, chave: str | None = None) -> str:
    """A chave de ação da declaração (`chave`, a da IA, ou a próxima do roteador), ou o texto de "sem como garantir a
    ação" (`NaoGravou`: nada foi gravado e dá para tentar de novo). Sem id utilizável não se declara (T18); a consulta
    do banco é a de verdade, porque `regra_ativa` devolve True quando cai, e sem banco a declaração nunca casaria (T9)."""
    chave = chave or proxima_chave()
    if chave is None:
        return NaoGravou(tx.SEM_ID)
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
            "DECLARACAO_INTERNA": lambda: tx.INTERNA,
            "DECLARACAO_NAO_CONFIRMADA": lambda: NaoGravou(tx.ACAO_ENCERRADA),  # o livro da IA (`confirma_no_cursor`)
            }.get(codigo, lambda: None)()


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
