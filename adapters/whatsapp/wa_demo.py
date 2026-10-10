"""adapters/whatsapp/wa_demo.py — "Testar o Piggy": demo no WhatsApp, SEM conta.

Desvio no começo de `process_message` (wa_runtime): quem manda o gatilho do
botão do site — ou já tem sessão aberta — conversa com o Piggy sobre uma
persona FICTÍCIA (core/services/demo). NADA do usuário é salvo: este módulo não
chama `get_or_create_canonical_user`, `handle_incoming` nem nada que grave em
users / user_identities / launches / ai_messages. O único estado é
`demo_sessions` (db/demo_funnel.py), anônima.

`decidir` só LÊ e nunca levanta; `executar` fala com o WhatsApp. Os envios vão
por `wa_client.xxx` (pelo módulo) para o teste trocá-los.

`db.demo_funnel`, `conversa` e o ERROR_MSG do chat entram por import TARDIO
(`_funil()` e dentro de `_responder`): este módulo é importado no topo de
wa_runtime, e o harness (harness_support/safe_runtime.py) carrega o runtime com
`db` e `core.observability` trocados por stubs, onde esses imports não existem.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from adapters.whatsapp import wa_client
from adapters.whatsapp.wa_parse import InboundMessage, get_interactive_id
from core.dashboard_links import base_url_publica
from core.intent_classifier import _normalize
from core.services.demo.dados import CODIGO_PADRAO, LIMITE_MSGS
from utils_phone import mask_phone

logger = logging.getLogger(__name__)

PREFIXO = "demo_q:"
BOTAO = "Ver perguntas"        # <= 20
SECAO = "Pergunte ao Piggy"    # <= 24
# id -> (título <= 24, descrição <= 72, pergunta completa que vai ao modelo).
# A resposta da lista chega com o TÍTULO como texto; o que vale é o id.
PERGUNTAS = {
    f"{PREFIXO}1": ("Fecho o mês no azul?", "Projeção do mês com os dados da Ana",
                    "No ritmo de agora, fecho o mês no azul?"),
    f"{PREFIXO}2": ("Notebook de 4 mil em 10x", "Quanto pesa a parcela nos próximos meses",
                    "Se eu comprar um notebook de R$ 4.000 em 10x, como ficam meus próximos meses?"),
    f"{PREFIXO}3": ("Onde mais gasto?", "Os gastos da Ana por categoria",
                    "Pra onde foi meu dinheiro este mês?"),
    f"{PREFIXO}4": ("Qual assinatura cortar?", "As assinaturas da Ana e quanto custam",
                    "Que assinatura eu poderia cortar?"),
    f"{PREFIXO}5": ("Gastei 80 no iFood", "Veja como o Piggy anotaria um gasto",
                    "gastei 80 no ifood"),
}

# Textos do Piggy (masculino). Nunca "não guardamos nada" nem "nada fica salvo":
# o texto vai à IA que responde. O que vale é "não é salvo no PigBank, só vai
# para a IA que responde".
_JA_TEM_CONTA = "Já tem conta? Manda *link 123456* com o código do site."
BOAS_VINDAS = (
    "🐷 Oi! Eu sou o Piggy. Aqui você testa como é ter um assistente de dinheiro "
    "no WhatsApp.\n\n"
    "Uso os dados de exemplo da Ana, uma pessoa fictícia. O que você escrever "
    "aqui não é salvo no PigBank, só vai para a IA que responde. "
    f"Você tem {LIMITE_MSGS} perguntas. "
    "Toca em *Ver perguntas* ou escreve a sua 👇"
)
# ULTIMA e FIM vão coladas à resposta do modelo (que já abre com 🐷): sem emoji
# aqui; quem manda o FIM sozinho põe o 🐷 na frente.
ULTIMA = "A próxima é a *última pergunta* do teste 😉"
FIM = (
    "Acabaram as perguntas do teste! Na conta de verdade os números são os "
    "seus. Pra criar a sua e continuar: {link}\n\n" + _JA_TEM_CONTA
)
LOTOU = (
    "🐷 O teste do Piggy lotou por hoje, tem muita gente testando. Tenta de novo "
    "daqui a pouco, ou conheça o PigBank agora: {link}\n\n" + _JA_TEM_CONTA
)
SO_TEXTO = (
    "🐷 No teste eu só leio texto. Escreve a sua pergunta, que esta não conta "
    "nas suas perguntas."
)

_GATILHO_RE = re.compile(r"\bquero testar o pigbank\b")
_CODIGO_RE = re.compile(rf"\bteste ({CODIGO_PADRAO})\b", re.I)  # `norm` vem em minúsculas


@dataclass
class Plano:
    acao: str  # abrir | reenviar | link | responder | so_texto | ignorar
    h: str
    code: str | None  # abrir: código do clique; demais: o da sessão


def _funil():
    from db import demo_funnel
    return demo_funnel


def _link(code: str | None) -> str:
    base = base_url_publica()
    return f"{base}/t/{code}" if code else f"{base}/precos?origem=teste"


def decidir(message: InboundMessage) -> Plano | None:
    """None = a mensagem NÃO é do demo (segue o fluxo normal). Só lê; nunca levanta."""
    try:
        funil = _funil()
        if funil.teto_diario() <= 0:
            return None
        h = funil.wa_hash(message.wa_id)
        if h is None:
            return None
        norm = _normalize(message.text or "")
        gatilho = bool(_GATILHO_RE.search(norm))
        sessao = funil.sessao_recente(h)
        if not gatilho and sessao is None:
            return None
        if funil.numero_tem_conta(message.wa_id):
            return None
        vazio = not (message.text or "").strip() and not message.attachments
        if sessao is None:
            m = _CODIGO_RE.search(norm)
            code = m.group(1).upper() if m else None
            return Plano("abrir", h, code if code and funil.CODIGO_RE.fullmatch(code) else None)
        code = sessao["code"]
        if vazio:
            return Plano("ignorar", h, code)
        if sessao["msgs_used"] >= funil.LIMITE_MSGS:
            return Plano("link", h, code)
        if gatilho:
            return Plano("reenviar", h, code)
        if message.attachments:
            return Plano("so_texto", h, code)
        return Plano("responder", h, code)
    except Exception as exc:
        # ImportError = deploy quebrado: o demo morreria calado, então alarma.
        nivel = logging.ERROR if isinstance(exc, ImportError) else logging.WARNING
        logger.log(nivel, "WA demo decidir falhou (segue o fluxo normal): %s", exc)
        return None


def _texto(to: str, body: str) -> None:
    wa_client.send_text(to=to, body=body)


def _enviar_lista(to: str) -> None:
    rows = [{"id": i, "title": t, "description": d} for i, (t, d, _) in PERGUNTAS.items()]
    try:
        ok = wa_client.send_interactive_list(
            to=to, body=BOAS_VINDAS, button_label=BOTAO,
            sections=[{"title": SECAO, "rows": rows}],
        )
        if ok:
            return
    except Exception as exc:
        logger.warning("WA demo lista falhou, usando texto: %s", exc)
    perguntas = "\n".join(f"• {p}" for _, _, p in PERGUNTAS.values())
    _texto(to, f"{BOAS_VINDAS}\n\n{perguntas}")


def _responder(plano: Plano, message: InboundMessage, msg_id: str) -> None:
    from core.services.ai_chat.runner import ERROR_MSG
    from core.services.demo import conversa

    funil = _funil()
    to = message.wa_id
    inter = get_interactive_id(message.raw or {})
    pergunta = PERGUNTAS[inter][2] if inter in PERGUNTAS else message.text
    n = funil.reservar_mensagem(plano.code)
    if n is None:
        # None = limite batido por outra thread OU erro de banco. Só é fim com
        # msgs_used >= limite lido de volta; erro (ou releitura que falha) é
        # retentável e não empurra a pessoa pro checkout.
        s = funil.sessao_recente(plano.h)
        if s is not None and s["msgs_used"] >= LIMITE_MSGS:
            _texto(to, "🐷 " + FIM.format(link=_link(plano.code)))
        else:
            _texto(to, ERROR_MSG)
        return
    try:
        try:
            wa_client.send_typing_indicator(msg_id)
        except Exception as exc:
            logger.warning("WA demo typing indicator failed message_id=%s error=%s", msg_id, exc)
        resposta = conversa.responder(plano.h, pergunta)
    except Exception as exc:
        # Sem resposta, sem desconto: a mensagem volta e a pessoa tenta de novo.
        logger.warning("WA demo modelo falhou to=%s error=%s", mask_phone(to), type(exc).__name__)
        funil.devolver_mensagem(plano.code)
        _texto(to, ERROR_MSG)
        return
    if n >= LIMITE_MSGS:
        resposta += "\n\n" + FIM.format(link=_link(plano.code))
    elif n == LIMITE_MSGS - 1:
        resposta += "\n\n" + ULTIMA
    try:
        _texto(to, resposta)
    except Exception:
        funil.devolver_mensagem(plano.code)  # não chegou: não gasta pergunta
        raise
    funil.marcar_resposta(plano.code, n)


def executar(plano: Plano, message: InboundMessage, msg_id: str) -> None:
    funil = _funil()
    to = message.wa_id
    logger.info("WA demo acao=%s to=%s", plano.acao, mask_phone(to))
    if plano.acao == "abrir":
        code = funil.abrir_sessao(plano.code, plano.h, funil.teto_diario())
        if code is None:
            # /t/{code} só se o código era de um clique livre: o de outro número
            # (ou inválido) levaria o clique de B para a linha de A.
            livre = plano.code if plano.code and funil.codigo_livre(plano.code) else None
            _texto(to, LOTOU.format(link=_link(livre)))
        else:
            _enviar_lista(to)
    elif plano.acao == "reenviar":
        _enviar_lista(to)
    elif plano.acao == "link":
        _texto(to, "🐷 " + FIM.format(link=_link(plano.code)))
    elif plano.acao == "so_texto":
        _texto(to, SO_TEXTO)
    elif plano.acao == "responder":
        _responder(plano, message, msg_id)
    # "ignorar": reação/tipo sem texto — calado de propósito.
