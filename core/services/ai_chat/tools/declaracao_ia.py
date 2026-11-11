"""A declaração "pelo banco" pelo chat IA (ADR 0006, PR-B (b)), atrás da flag `DECLARACAO_BANCO`.

A tool `add_launch` com forma "banco" não grava direto: pede "sim" (`confirmar`), congela no servidor o que a
confirmação mostra (`congela`, o payload `_declaracao`; `resumo` lê só ele) e o "sim" grava só esse payload
(`executar`), sem chamar o modelo. O id da ação é a mensagem que a pediu (`decl.chave_da_mensagem() + ":0"`); a
reentrega se vincula a ela pelo id, nunca pelo conteúdo (`na_chegada`, antes da IA). O livro (`db/declaracao_livro.py`)
guarda a ação e cada "sim" visto, e sobrevive ao apagar a declaração. Cartão não declara (T8); sem id, T18.
Com a flag desligada nada aqui toca o banco: `aplica` e `na_chegada` saem antes.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import db
from core.handlers import declaracao_banco as decl
from core.handlers import declaracao_banco_textos as tx
from core.handlers import forma_pagamento as fp
from db import declaracao_livro as livro

from .._context import CURRENT_PLATFORM, CURRENT_USER_MESSAGE


def aplica(user_id: int, args: dict[str, Any]) -> bool:
    """O `add_launch` destes args é uma declaração pelo banco (flag ligada para o usuário e `fp.decidir` BANCO)?"""
    from .launches import _TIPOS_VALIDOS, forma_declarada
    if not decl.ligada(user_id) or str(args.get("tipo") or "").strip().lower() not in _TIPOS_VALIDOS:
        return False
    try:
        if float(args.get("valor") or 0) <= 0:
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    return decl.declara(user_id, fp.decidir(user_id, forma_declarada(args)))


def acao(user_id: int) -> str:
    """"cartao" (T8, antes de qualquer confirmação), "sem_id" (T18) ou "declara"."""
    from parsers import split_financial_transactions
    texto = CURRENT_USER_MESSAGE.get() or ""
    cartao = (fp._no_cartao(texto, debito=True) if fp.e_resposta(texto)
              else decl.tem_cartao(split_financial_transactions(texto) or [texto]))
    if cartao:
        return "cartao"
    return "declara" if decl.chave_da_mensagem() else "sem_id"


def _cabe(valor) -> bool:
    """Até 2 casas: a mesma conta do escritor (`db/accounts.py`), antes de mostrar (T4 sem confirmação)."""
    from db.bank_movements import _amount, _money
    d = _amount(valor)
    return d is not None and _money(d) == d


def _viva(user_id: int) -> dict | None:
    """A pendência viva de declaração (com o payload): a da IA ou a confirmação do roteador (#1315), esta no formato
    da IA (`tool_args`, `summary`), para a reentrega repetir a mesma pergunta."""
    pend = db.ai_get_pending_action(user_id)
    args = (pend or {}).get("tool_args")
    if isinstance(args, dict) and isinstance(args.get("_declaracao"), dict):
        return pend
    r = db.get_pending_action(user_id)
    if r and r.get("action_type") == "confirm_declaracao_banco":
        return {"tool_args": r["payload"], "summary": r["payload"]["summary"]}
    return None


def _do_widget(platform: str | None = None) -> bool:
    """O `/ai/chat` (o widget do /dashboard, que só desenha `**x**`): a web sem emissor. O stream (/painel e app) e o
    WhatsApp desenham `*x*` (D 1A)."""
    from .. import streaming
    return (platform or CURRENT_PLATFORM.get()) == "dashboard" and not streaming.ativo()


def _molde(texto: str, platform: str | None = None) -> str:
    """O negrito do canal num texto FIXO desta conversa (o molde, antes de entrar qualquer campo do usuário)."""
    return texto.replace("*", "**") if _do_widget(platform) else texto


def confirmacao(resumo: str, platform: str | None = None) -> str:
    """A confirmação da declaração: o negrito do canal no molde, e o resumo (com os campos do usuário) entra depois."""
    from ..runner import _CONFIRMA
    return _molde(_CONFIRMA, platform).format(resumo=resumo)


def _confirmacao(pend: dict, platform: str) -> str:
    return confirmacao(pend["summary"], platform)


def _bloqueio(user_id: int, chave: str) -> str | None:
    """Por que a ação `chave` não arma: T3 (já gravou), T19 (já passou pelo livro) ou T9 (consulta do banco caiu);
    None = pode armar. A ação com a pendência viva dela não chega aqui: a `reentrega`, sob a mesma trava do turno e
    antes do modelo, já respondeu a mesma confirmação."""
    linha = livro.le(user_id, chave)
    if linha:
        return tx.REPETIDA.format(seq=linha["user_seq"]) if linha["estado"] == "confirmada" else tx.ACAO_ENCERRADA
    r = decl.preparar(user_id, chave)  # o roteador já gravou esta mensagem (`external_id`): T3
    return r if isinstance(r, decl.NaoGravou) else None


def confirmar(user_id: int, args: dict[str, Any]) -> bool:
    """`confirmar_se` da declaração. Falso = a tool executa direto e responde T8, T18, T4, T3 ou T19 sem armar."""
    if acao(user_id) != "declara" or not _cabe(args.get("valor")):
        return False
    return _bloqueio(user_id, decl.chave_da_mensagem() + ":0") is None


def hash_do_payload(p: dict[str, Any]) -> str:
    """O HMAC (`hash_pii`, com pepper: alvo e nota são do usuário) do payload, numa forma canônica que sobrevive à ida e
    volta pelo jsonb da pendência: chaves ordenadas, o valor como `Decimal` de 2 casas em texto, o resto como está."""
    from core.crypto import hash_pii
    canonico = {k: p.get(k) for k in ("acao", "tipo", "alvo", "nota", "data", "categoria", "category_reason")}
    canonico["valor"] = str(Decimal(str(p["valor"])).quantize(Decimal("0.01")))
    return hash_pii(json.dumps(canonico, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def congela(user_id: int, args: dict[str, Any]) -> dict[str, Any]:
    """`ao_confirmar` da declaração, depois do `_congela_pendencia` (dia efetivo e categoria): o payload que a
    confirmação mostra e o "sim" grava, e a ação anotada no livro como armada."""
    from core.services.category_service import infer_category
    from utils_text import is_internal_category
    nota = (args.get("nota") or "").strip() or None
    alvo = fp.limpar(args.get("alvo") or "", banco=True) or None
    categoria = (args.get("categoria") or "").strip() or None
    if categoria:
        motivo = args.get("_category_reason") or ("explicit" if args.get("_categoria_explicita") else "ai")
    else:
        res = infer_category(user_id, nota or alvo or "", None)
        categoria, motivo = res.category or "outros", res.reason
    if motivo != "explicit" and is_internal_category(categoria):  # M4: a inferida não recusa, vira "outros"
        categoria, motivo = "outros", "default"
    chave = decl.chave_da_mensagem() + ":0"
    payload = {"acao": chave, "tipo": str(args["tipo"]).strip().lower(), "valor": float(args["valor"]),
               "alvo": alvo, "nota": nota, "data": args["data"], "categoria": categoria, "category_reason": motivo}
    livro.registra_acao(user_id, chave, hash_do_payload(payload))
    return {**args, "_declaracao": payload}


def resumo(p: dict[str, Any]) -> str:
    """O texto da confirmação, só do payload (o mesmo formato do `add_launch`, sem categoria)."""
    from .launches import _add_launch_summary
    return _add_launch_summary({"tipo": p["tipo"], "valor": p["valor"], "alvo": p["alvo"], "nota": p["nota"],
                                "data": p["data"], "forma_pagamento": fp.BANCO})


def executar(user_id: int, args: dict[str, Any], platform: str | None = None) -> str:
    """O "sim": grava exatamente o payload congelado, com a ação dele e a chave deste "sim", e só no turno que a
    consumiu (a marca de `consumida`); o livro, no mesmo commit, confere a ação, o "sim" e o hash do payload. Sem flag ou
    sem banco, não grava nada. `platform`: o roteador (#1315), que não põe o `CURRENT_PLATFORM` da IA."""
    platform = platform or CURRENT_PLATFORM.get()
    from core.handlers.launches import add_from_entities
    from .launches import _parse_iso_datetime_for_launch
    p = args["_declaracao"]
    try:
        if not decl.declara(user_id, fp.decidir(user_id, fp.BANCO)):
            return fp.msg_banco(user_id, p["tipo"], p["valor"])
        if not decl.autorizada(user_id, p.get("acao")):  # M8: só o turno que consumiu a ação (nunca outro, nem o modelo)
            return tx.ACAO_ENCERRADA
        sim = decl.chave_da_mensagem()
        if sim is None:
            return _molde(tx.SEM_ID, platform)
        r = add_from_entities(
            user_id, tipo=p["tipo"], valor=p["valor"], alvo=p["alvo"], nota=p["nota"], categoria=p["categoria"],
            category_reason=p["category_reason"], criado_em=_parse_iso_datetime_for_launch(p["data"]),
            platform=platform, forma_pagamento=fp.BANCO, chave_acao=p["acao"], confirmacao=sim,
            payload_hash=hash_do_payload(p))
        return _molde(tx.INTERNA, platform) if r is tx.INTERNA else r  # o T6 do escritor (`decl.recusa` devolve a constante)
    finally:
        # Uma tentativa só: a ação consumida que não virou `confirmada` (recusa, T9, exceção) fica encerrada (a reentrega
        # da mensagem que armou dá T19; o gasto mandado de novo é ação nova). A marca some com o turno.
        livro.descarta(user_id, p["acao"], de="consumida")


def sem_payload(user_id: int, tipo: str, valor: float) -> str:
    """Declaração executada sem confirmação (o `confirmar` recusou) ou de uma pendência sem payload (armada antes da
    flag, L-b3: T9). Nunca grava."""
    a = acao(user_id)
    if a == "cartao":
        return fp.msg_banco(user_id, tipo, valor, cartao=True)
    if a == "sem_id":
        return _molde(tx.SEM_ID)
    if not _cabe(valor):
        return tx.valor_casas(valor, _molde(tx.VALOR_CASAS))
    return _bloqueio(user_id, decl.chave_da_mensagem() + ":0") or tx.SEM_GARANTIA


def consumida(user_id: int, pend: dict) -> None:
    """O "sim" que ganhou o CAS da pendência de declaração: o livro anota a ação como consumida por ele. É o que
    autoriza a gravação (`confirma_no_cursor`); sem isto, nada grava (T19). Não confere se o "sim" é novo: um "sim" já
    visto (ou sem id) com declaração viva nunca chega ao CAS, porque a `reentrega` responde antes, sob a mesma trava do
    turno em que a pendência é armada e consumida."""
    p = (pend.get("tool_args") or {}).get("_declaracao")
    sim = decl.chave_da_mensagem()
    if isinstance(p, dict) and sim and livro.consome(user_id, p["acao"], sim):
        decl.autoriza(user_id, p["acao"])  # só este turno grava esta ação (M8)


def descartada(user_id: int, pend: dict) -> None:
    """O "não" que ganhou o CAS de uma pendência de declaração."""
    p = (pend.get("tool_args") or {}).get("_declaracao")
    if isinstance(p, dict):
        livro.descarta(user_id, p["acao"])


def na_chegada(user_id: int, texto: str, platform: str) -> str | None:
    """Antes da IA (e da guarda anti-sequestro): anota o "sim" e responde a reentrega. None = segue o fluxo."""
    if not decl.ligada(user_id):
        return None
    decl.registrar_mensagem(user_id, texto)
    return reentrega(user_id, texto, platform)


def reentrega(user_id: int, texto: str, platform: str) -> str | None:
    """A resposta a uma mensagem que já passou por aqui, pelo id dela (nunca pelo conteúdo):
    1. a mensagem que armou a ação: T3 (gravou), a mesma confirmação (viva) ou T19 (encerrada);
    2. a resposta já registrada no livro (pelo id, qualquer que seja o texto de agora): o "sim" que consumiu uma ação
       que gravou dá T3; o resto do "sim", o texto neutro `SIM_JA_RECEBIDO`, sempre (sem modelo, sem roteador, sem
       consumir nem a pendência nova); o "não", a confirmação viva de novo sem consumi-la (4A), ou `NAO_JA_RECEBIDO` sem ela.
    Um "sim" ou "não" sem id não consome uma declaração (T18): ela fica viva para um canal com id."""
    k = decl.chave_da_mensagem()
    resposta = decl.e_sim(texto, platform == "whatsapp") or decl.e_nao(texto, platform == "whatsapp")
    if k is None:
        return _molde(tx.SEM_ID, platform) if resposta and _viva(user_id) else None
    linha = livro.le(user_id, k + ":0")
    if linha:
        if linha["estado"] == "confirmada":
            return tx.REPETIDA.format(seq=linha["user_seq"])
        pend = _viva(user_id)
        return (_confirmacao(pend, platform) if pend and pend["tool_args"]["_declaracao"]["acao"] == k + ":0"
                else tx.ACAO_ENCERRADA)
    if decl.mensagem_nova():
        return None
    # Rodada 9 (C11): decide pela mensagem JÁ REGISTRADA no livro (pelo id), nunca pelo texto de agora: o áudio
    # retranscrito ("sim, gastei 50 no mercado no pix") ou o cliente que reenvia o mesmo id com outro texto não
    # chega ao modelo nem ao roteador. A mensagem nunca registrada segue o fluxo.
    vista = livro.le(user_id, k)
    if not vista:
        return None
    if vista["papel"] == "nao":  # o "não" já visto (4A): a confirmação viva de novo, sem consumir; sem ela, só o aviso
        pend = _viva(user_id)
        return _confirmacao(pend, platform) if pend else tx.NAO_JA_RECEBIDO
    ligada = vista["acao"] and livro.le(user_id, vista["acao"])
    if ligada and ligada["estado"] == "confirmada":
        return tx.REPETIDA.format(seq=ligada["user_seq"])
    # Rodadas 7 e 8: o "sim" já visto que não gravou nada (ligado a ação que falhou, ou nunca ligado: queda ou falha antes
    # do vínculo, pendência expirada, "sim" do roteador) nunca consome pendência, nem a nova, e nunca vai ao modelo; o
    # desfecho é incerto (o perdedor da corrida do S1 consigo mesmo cai aqui), então o texto é neutro (D-a).
    return tx.SIM_JA_RECEBIDO
