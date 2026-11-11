"""A confirmação da declaração "pelo banco" pelo ROTEADOR do WhatsApp (#1315, ADR 0006), atrás da flag.

`add_from_entities` não grava a declaração do roteador: `arma` congela o payload (o mesmo formato e o mesmo livro da
IA, `declaracao_ia`), anota a ação e deixa a pendência `confirm_declaracao_banco` com a pergunta. `responde` roda na
chegada, depois da reentrega da IA e antes de qualquer outro fluxo (texto, o "sim" falado depois da transcrição, botão):
- o botão `decl_sim:<id>`/`decl_nao:<id>` decide só sobre a ação daquele id (o id do livro), nunca sobre outra;
- o "sim"/"não" digitado ou falado só decide a confirmação viva se o `timestamp` da Meta mostrar com certeza que foi
  mandado DEPOIS dela (na precisão dele, segundos: empate, ausente ou ilegível não grava e repete a pergunta);
- sem confirmação viva, a resposta que pertence à última ação do livro (24 h) recebe o texto dela e não consome outra
  pendência ("sim" antigo não confirma o que veio depois).
Toda leitura e escrita filtra `user_id`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import db
from core.handlers import declaracao_banco as decl
from core.handlers import declaracao_banco_textos as tx
from core.handlers import declaracao_correcao as correcao
from db import declaracao_livro as livro
from db.ai_chat import PENDING_TTL_MINUTES

TIPO = "confirm_declaracao_banco"
SUSPENSA_TIPO = "confirm_declaracao_suspensa"  # `declaracao_suspensao`
BOTAO_SIM, BOTAO_NAO = "decl_sim:", "decl_nao:"
# DP-4: a expiração da pendência do roteador é a da IA (uma fonte só): o livro não separa as ações das duas, e a mesma
# conta diz "expirou" pelo livro (`_vencida`).
PRAZO = timedelta(minutes=PENDING_TTL_MINUTES)
JANELA = timedelta(hours=24)  # R2-1: só limita o texto de "expirou"; o livro (a reentrega) não tem prazo


def arma(user_id: int, payload: dict, platform: str) -> str:
    """No lugar da gravação: a ação no livro, a pendência com o payload congelado e a pergunta. A linha ocupada por
    outra pergunta: nada armado (`msg_ocupado`). A confirmação mais recente vence (DP-13): a anterior do roteador e a
    pendência viva da IA saem."""
    from core.handlers import forma_pagamento as fp
    from core.services.ai_chat.tools import declaracao_ia
    livro_id = livro.registra_acao(user_id, payload["acao"], declaracao_ia.hash_do_payload(payload))
    resumo = declaracao_ia.resumo({**payload, "nota": None})  # DP-1: a pergunta sem a nota (a frase inteira do usuário)
    pergunta = declaracao_ia.confirmacao(resumo, platform)
    anterior = db.get_pending_action(user_id)
    if not db.claim_pending_action(user_id, "confirm_declaracao_banco",  # literal: `tests/test_pending_registry.py`
                                   {"_declaracao": payload, "livro_id": livro_id, "summary": resumo, "question": pergunta},
                                   minutes=PRAZO.total_seconds() / 60):
        atual = db.get_pending_action(user_id)
        if _acao(atual) == payload["acao"]:  # outra entrega da MESMA mensagem armou ao mesmo tempo: a mesma pergunta
            return atual["payload"]["question"]
        livro.descarta(user_id, payload["acao"])
        return decl.NaoGravou(fp.msg_ocupado(user_id))
    if _acao(anterior) not in (None, payload["acao"]):  # a anterior era de outra mensagem: não vale mais
        livro.descarta(user_id, _acao(anterior))
    ia = db.ai_get_pending_action(user_id)
    if ia and db.ai_consume_pending_action(user_id, ia):
        declaracao_ia.descartada(user_id, ia)
    return pergunta


def _acao(pend: dict | None) -> str | None:
    """A chave da ação de uma confirmação do roteador viva; None para qualquer outra pendência."""
    return pend["payload"]["_declaracao"]["acao"] if pend and pend.get("action_type") == TIPO else None


def abandona(user_id: int, pend: dict | None = None) -> bool:
    """Tira a confirmação do roteador (CAS) e encerra a ação no livro: a flag desligada (`na_rota`) ou a IA armou outra
    (R2-6, "a mais recente vence"). Vale também para a confirmação SUSPENSA (a marca `confirm_declaracao_suspensa`, de
    qualquer porta): o `desfecho` que viria restaurá-la perde o CAS da marca. Uma 2ª leitura cobre o `desfecho` que
    restaurou a marca entre a leitura e o consumo (ele restaura no máximo uma vez)."""
    for _ in range(2):
        pend = pend or db.get_pending_action(user_id)
        tipo = (pend or {}).get("action_type")
        if tipo not in (TIPO, SUSPENSA_TIPO):
            return False
        p = pend["payload"]
        acao = (p.get("_declaracao") or p["_ia"]["tool_args"]["_declaracao"])["acao"]
        if db.consume_pending_action(user_id, pend):
            livro.descarta(user_id, acao)
            return True
        pend = None
    return False


def na_rota(user_id: int, pend: dict) -> str:
    """A resposta que o `route()` não reconhece como comando (`out_of_scope`, confiança baixa, ou `confirm.*` que não é
    o "sim"/"não" do gancho: "sim, mas foi 60"): repete a pergunta sem consumir (T-6 A). Com a flag desligada a
    confirmação nunca grava: sai, com o texto do banco."""
    if decl.ligada(user_id):
        return pend["payload"]["question"]
    from core.handlers import forma_pagamento as fp
    p = pend["payload"]["_declaracao"]
    abandona(user_id, pend)
    return fp.msg_banco(user_id, p["tipo"], p["valor"])


def _segundos(msg) -> int | None:
    """O `timestamp` da Meta (segundos Unix, em texto); None se ausente ou ilegível."""
    s = str(getattr(msg, "enviado_em", None) or "").strip()
    return int(s) if s.isdigit() and int(s) > 0 else None


def _depois(ts: int | None, quando: datetime) -> bool:
    """Mandada com certeza depois de `quando`, na precisão do `timestamp` (segundos): o empate não é depois."""
    return ts is not None and ts > int(quando.timestamp())


def _vencida(acao: dict) -> bool:
    return acao["criado_em"] + PRAZO <= datetime.now(timezone.utc)


def _decide(user_id: int, pend: dict, sim: bool, platform: str) -> str:
    from core.services.ai_chat.tools import declaracao_ia
    if not db.consume_pending_action(user_id, pend):
        return tx.ACAO_ENCERRADA
    if not sim:
        livro.descarta(user_id, pend["payload"]["_declaracao"]["acao"])
        return tx.NAO_FIZ_NADA
    declaracao_ia.consumida(user_id, {"tool_args": pend["payload"]})
    return declaracao_ia.executar(user_id, pend["payload"], platform)


def _viva(user_id: int, platform: str, pend: dict | None) -> tuple[str, dict] | None:
    """A confirmação de declaração viva e a porta dela: "roteador" (`pend`, a pendência do roteador já lida) ou "ia" (só
    no WhatsApp: T-4 A, R3-3 A; no Discord e na web a da IA segue o runner, como antes)."""
    if _acao(pend):
        return "roteador", pend
    if platform == "whatsapp":
        from core.services.ai_chat.tools import declaracao_ia
        ia = declaracao_ia._viva(user_id)  # sem a do roteador viva: só a da IA
        if ia:
            return "ia", ia
    return None


def _pergunta(viva: tuple[str, dict], platform: str) -> str:
    from core.services.ai_chat.tools import declaracao_ia
    porta, pend = viva
    return pend["payload"]["question"] if porta == "roteador" else declaracao_ia._confirmacao(pend, platform)


def _payload(viva: tuple[str, dict]) -> dict:
    porta, pend = viva
    return (pend["payload"] if porta == "roteador" else pend["tool_args"])["_declaracao"]


def _consome(user_id: int, viva: tuple[str, dict]) -> bool:
    porta, pend = viva
    return (db.consume_pending_action if porta == "roteador" else db.ai_consume_pending_action)(user_id, pend)


def _encerra(user_id: int, viva: tuple[str, dict]) -> None:
    """A ação da confirmação (já sem pendência) fica `descartada` no livro."""
    livro.descarta(user_id, _payload(viva)["acao"])


def _decide_ia(user_id: int, pend: dict, sim: bool, texto: str, platform: str) -> str:
    """A forma completa (R3-3 A) na confirmação da IA no WhatsApp: o que o runner faz com o "sim"/"não" exato."""
    from core.services.ai_chat import runner
    from core.services.ai_chat.tools import declaracao_ia
    if not db.ai_consume_pending_action(user_id, pend):
        return runner._CAS_PERDIDO
    if sim:
        declaracao_ia.consumida(user_id, pend)
        r = declaracao_ia.executar(user_id, pend["tool_args"], platform)
    else:
        declaracao_ia.descartada(user_id, pend)
        r = tx.NAO_FIZ_NADA
    db.ai_append_message(user_id, "user", texto)
    runner._grava_assistant(user_id, r)
    return r


_NAO_CONFIANTE = 0.85  # R3-2 A: o `confirm.no` do classificador que vale como "não"


def _nao_do_classificador(user_id: int, texto: str) -> bool:
    """R3-2 A: o `confirm.no` com confiança alta do classificador (com o LLM, como no fluxo normal)."""
    from core.intent_classifier import classify
    r = classify(texto, user_id=user_id)
    return r.intent == "confirm.no" and r.confidence >= _NAO_CONFIANTE


def _corrige(user_id: int, viva: tuple[str, dict], trecho: str, platform: str) -> str:
    # o trecho inteiro vai a `so_valor`: nada da mensagem é descartado
    """A correção: a confirmação viva é invalidada PRIMEIRO. Só um valor, lido com certeza: nova confirmação com ele (o
    mesmo tipo, alvo, nota, dia e categoria), identificada pela mensagem da correção (`<canal>:<hmac do id dela>:0`).
    Qualquer outra coisa (R4-4): nada fica para confirmar e o usuário manda o gasto completo de novo."""
    if not _consome(user_id, viva):
        return tx.ACAO_ENCERRADA
    _encerra(user_id, viva)
    valor = correcao.so_valor(trecho)
    if valor is None:
        return tx.CORRECAO_INCOMPLETA
    chave = decl.chave_da_mensagem()
    if chave is None:
        return tx.SEM_ID
    return arma(user_id, {**_payload(viva), "acao": chave + ":0", "valor": valor}, platform)


def _repete(viva: tuple[str, dict], ts: int | None, platform: str) -> str:
    pergunta = _pergunta(viva, platform)
    return pergunta if _depois(ts, viva[1]["created_at"]) else f"{tx.ENVIADA_ANTES}\n\n{pergunta}"


def responde(user_id: int, texto: str, msg, platform: str) -> str | None:
    """A resposta a uma confirmação de declaração, ou None (segue o fluxo: com a confirmação viva, é outro comando).
    Com ela viva, só o que é enviado com certeza depois dela decide; o resto repete a pergunta (`ENVIADA_ANTES`):
    1. "sim"/"não" (`e_sim`/`e_nao`; as formas completas só no WhatsApp, M-8 A): grava ou descarta;
    2. correção (`declaracao_correcao.correcao`: "sim, mas foi 60", "foi 60", "60 ontem"): `_corrige`;
    3. cortesia sozinha ("obrigado", "sim, obrigado"): repete a pergunta (M-1 A);
    4. "sim"/"não"/👍 seguido de outra coisa: o `confirm.no` do classificador com confiança alta ("não quero isso")
       descarta (R3-2 A); o resto ("ok, qual meu saldo?") é outro comando (M-1 A).
    Com ela suspensa por outro comando em andamento, o "sim"/"não" (e o botão) recebe `AINDA_TERMINANDO` (R4-2).
    Sem nenhuma dessas formas é outro comando (`declaracao_suspensao.suspende`); o "não" que só o classificador do
    fluxo lê ("nem pensar") volta em `desfecho`."""
    botao = str(getattr(msg, "botao", None) or "")
    de_declaracao = botao.startswith((BOTAO_SIM, BOTAO_NAO))
    if not decl.ligada(user_id):
        return _toque(user_id, texto, botao, platform) if de_declaracao else None
    wa = platform == "whatsapp"
    pend = db.get_pending_action(user_id)
    papel = ("sim" if botao.startswith(BOTAO_SIM) else "nao") if de_declaracao else (
        "sim" if decl.e_sim(texto, wa) else "nao" if decl.e_nao(texto, wa) else None)
    if pend and pend.get("action_type") == SUSPENSA_TIPO and papel and decl.registrar_mensagem(user_id, texto, papel):
        return tx.AINDA_TERMINANDO.format(resposta="sim" if papel == "sim" else "não")
    if de_declaracao:
        return _toque(user_id, texto, botao, platform)
    viva = _viva(user_id, platform, pend)
    ts = _segundos(msg)
    if not botao and papel is None and (trecho := correcao.correcao(texto)):
        if viva:
            if not _depois(ts, viva[1]["created_at"]):
                return f"{tx.ENVIADA_ANTES}\n\n{_pergunta(viva, platform)}"
            return _corrige(user_id, viva, trecho, platform)
        from core.services.ai_chat.tools import declaracao_ia  # R4-5: a correção da confirmação que expirou
        r = _da_ultima_acao(user_id, True, texto, ts, pend, declaracao_ia._viva(user_id))
        return r if r == tx.EXPIROU else None
    if viva and not botao and papel is None:
        resto = correcao.resto(texto)
        if correcao.cortesia(resto or texto):
            return _repete(viva, ts, platform)
        if not resto:
            return None  # outro comando
        if not _nao_do_classificador(user_id, texto):
            return None  # cortesia + comando ("ok, qual meu saldo?"): outro comando (M-1 A)
        papel = "nao"
    if papel is None or not decl.registrar_mensagem(user_id, texto, papel):
        return None
    sim = papel == "sim"
    if viva:
        porta, p = viva
        if not botao and _depois(ts, p["created_at"]):
            if porta == "roteador":
                return _decide(user_id, p, sim, platform)
            from core.services.ai_chat.confirmations import is_cancel, is_confirm
            exata = is_confirm(texto) if sim else is_cancel(texto)
            return None if exata else _decide_ia(user_id, p, sim, texto, platform)  # a exata, o runner decide
        # botão de outra pergunta, ou sem prova de que veio depois desta: não decide nada e repete
        return f"{tx.BOTAO_VELHO if botao else tx.ENVIADA_ANTES}\n\n{_pergunta(viva, platform)}"
    if botao:
        return None
    from core.services.ai_chat.tools import declaracao_ia
    return _da_ultima_acao(user_id, sim, texto, ts, pend, declaracao_ia._viva(user_id))


def _toque(user_id: int, texto: str, botao: str, platform: str) -> str:
    """O botão decide só sobre a ação do id dele (do livro e deste usuário); qualquer outro caso não decide nada. Com a
    flag desligada ele não grava: encerra só a confirmação do id dele (com o texto que o "sim" digitado recebe), sem
    repetir pergunta nem botão; o botão de outra confirmação não toca em nada."""
    pend = db.get_pending_action(user_id)
    viva = pend if pend and pend.get("action_type") == TIPO else None
    ident = botao.split(":", 1)[1]
    if not decl.ligada(user_id):
        dele = viva if viva and ident.isdigit() and viva["payload"].get("livro_id") == int(ident) else None
        return na_rota(user_id, dele) if dele else tx.BOTAO_VELHO
    if ident.isdigit() and decl.registrar_mensagem(user_id, texto):
        sim = botao.startswith(BOTAO_SIM)
        if viva and viva["payload"].get("livro_id") == int(ident):
            return _decide(user_id, viva, sim, platform)
        acao = livro.acao_por_id(user_id, int(ident))
        if acao and acao["estado"] == "confirmada":
            r = tx.REPETIDA.format(seq=acao["user_seq"])
        elif acao and acao["estado"] == "armada" and _vencida(acao):
            r = tx.EXPIROU if sim else tx.NAO_FIZ_NADA
        else:
            r = tx.BOTAO_VELHO
    else:
        r = tx.BOTAO_VELHO
    return f"{r}\n\n{viva['payload']['question']}" if viva else r


def _pergunta_da_ia(user_id: int) -> datetime | None:
    """Quando a IA deixou uma pergunta aberta sem pendência (a oferta "Quer ver por categoria?", `pergunta_aberta_da_ia`):
    a hora da última mensagem do histórico, pelo relógio da aplicação."""
    from core.services.ai_chat_commands import pergunta_aberta_da_ia
    if pergunta_aberta_da_ia(user_id) is None:
        return None
    ultima = db.ai_get_last_messages(user_id, 1)
    return datetime.now(timezone.utc) - ultima[0]["age"] if ultima else None


def _da_ultima_acao(user_id: int, sim: bool, texto: str, ts: int | None, pend: dict | None,
                    ia: dict | None) -> str | None:
    """Sem confirmação viva: a resposta pertence à última ação do livro se nenhuma pergunta mais nova que ela (pendência do
    roteador, da IA, ou a pergunta aberta da IA sem pendência) foi feita com certeza antes do envio. Responde sem
    consumir nada. None = o fluxo de hoje (inclui sem `timestamp`, fora do WhatsApp, e o "cancelar" do plano, que só
    vale como "não" com a confirmação aberta: DP-12). `ia`: a confirmação de declaração viva da IA, que decide a dela."""
    from core.services.billing_commands import is_billing_command
    if ts is None or (not sim and is_billing_command(texto)):
        return None
    acao = livro.ultima_acao(user_id, ts, JANELA)
    if acao is None:
        return None
    q = db.ai_get_pending_action(user_id)
    pergunta = pend if pend and not db.eh_oferta_de_conveniencia(pend.get("action_type")) else None  # botão, não pergunta
    for quando in (pergunta and pergunta["created_at"], q and q["created_at"], _pergunta_da_ia(user_id)):
        if quando and quando > acao["criado_em"] and _depois(ts, quando):
            return None
    if acao["estado"] == "consumida" or (ia and ia["tool_args"]["_declaracao"]["acao"] == acao["chave"]):
        return None  # a da IA, viva: ela decide
    if not sim:
        return tx.NAO_FIZ_NADA
    if acao["estado"] == "confirmada":
        return tx.REPETIDA.format(seq=acao["user_seq"])
    return tx.EXPIROU if acao["estado"] == "armada" and _vencida(acao) else tx.ACAO_ENCERRADA
