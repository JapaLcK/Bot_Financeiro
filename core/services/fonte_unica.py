"""Q36 no painel antigo: Open Finance é a fonte única para quem tem a chave do v2.

Para quem está em `dashboard_v2_enabled`, as funções de escrita que todo canal
chama (`/app`, WhatsApp, IA) recusam investimento manual (criar e aportar),
importação de extrato (OFX/CSV/PDF), importação de fatura OFX e compra manual
no cartão. Resgatar e apagar investimento, caixinha e carteira seguem livres.
A tabela do que é bloqueado e liberado está em `docs/CLAUDE.md`.

Os textos moram SÓ aqui. A exceção carrega o código `FONTE_UNICA_OF` e o texto
do caso em `str()`, como a `PlanLimitExceeded`: é `ValueError` para as rotas do
`/app` que já fazem `except ValueError → 400 str(exc)` responderem o texto certo
sem mudar.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

CODIGO = "FONTE_UNICA_OF"

MENSAGENS = {
    "investimento": (
        "Investimentos agora vêm do seu banco pelo Open Finance, então não dá mais para "
        "criar investimento manual nem aportar nele. Se ainda não conectou, conecte o banco em Configurações. "
        "Os investimentos manuais que você já tem ainda podem ser resgatados ou apagados."
    ),
    "extrato": (
        "Extratos agora vêm do seu banco pelo Open Finance, então não dá mais para importar "
        "arquivo de extrato (OFX, CSV ou PDF). Se ainda não conectou, conecte o banco em Configurações."
    ),
    "cartao": (
        "Compras no cartão agora vêm do seu banco pelo Open Finance, então não dá mais para "
        "lançar compra no cartão à mão nem importar fatura. Se ainda não conectou, conecte o banco em Configurações. "
        "Gasto em dinheiro vivo continua indo para a Carteira Piggy."
    ),
}


class FonteUnicaOF(ValueError):
    codigo = CODIGO

    def __init__(self, caso: str):
        super().__init__(MENSAGENS[caso])
        self.caso = caso


def recusa(user_id: int, caso: str) -> str | None:
    """O texto da recusa se `user_id` tem a chave do v2; senão None.

    Fail-open: a checagem falhando não bloqueia ninguém. É uma trava de produto
    num beta de um usuário — recusar a escrita legítima de todo mundo porque o
    banco piscou seria pior que deixar o dono gravar um investimento manual."""
    try:
        from core.services.plan_service import dashboard_v2_enabled
        bloqueado = dashboard_v2_enabled(int(user_id))
    except Exception:
        logger.warning("fonte_unica: chave do v2 falhou, liberando user=%s", user_id, exc_info=True)
        return None
    return MENSAGENS[caso] if bloqueado else None


def exigir(user_id: int, caso: str) -> None:
    """Levanta `FonteUnicaOF` se `user_id` tem a chave do v2."""
    if recusa(user_id, caso):
        raise FonteUnicaOF(caso)
