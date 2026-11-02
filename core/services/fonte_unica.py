"""Q36 no painel antigo: Open Finance é a fonte única para quem tem a chave do v2.

Para quem está em `dashboard_v2_beta` (a lista beta, não a env geral), as funções de escrita que todo canal
chama (`/app`, WhatsApp, IA) recusam investimento manual (criar e aportar),
importação de extrato (OFX/CSV/PDF), importação de fatura OFX e compra manual
no cartão. Resgatar e apagar investimento, caixinha e carteira seguem livres.
A tabela do que é bloqueado e liberado está em `docs/CLAUDE.md`.

A recusa da receita fixa (`recusa_receita`) é de outra natureza: vale por banco
conectado (a regra da Q40, `forma_pagamento.regra_ativa`), não pela chave do v2.

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

    def __init__(self, caso: str, texto: str | None = None):
        super().__init__(texto or MENSAGENS[caso])
        self.caso = caso


def recusa(user_id: int, caso: str) -> str | None:
    """O texto da recusa se `user_id` está na lista beta do v2 (não a env geral
    DASHBOARD_V2_TODOS, ADR 0006); senão None.

    Fail-open: a checagem falhando não bloqueia ninguém. É uma trava de produto
    num beta de um usuário — recusar a escrita legítima de todo mundo porque o
    banco piscou seria pior que deixar o dono gravar um investimento manual."""
    try:
        from core.services.plan_service import dashboard_v2_beta
        bloqueado = dashboard_v2_beta(int(user_id))
    except Exception as exc:
        logger.warning("fonte_unica: chave do v2 falhou, liberando user=%s causa=%s sqlstate=%s", user_id,
                       type(exc).__name__, getattr(exc, "sqlstate", None), extra={"user_id": user_id})
        return None
    return MENSAGENS[caso] if bloqueado else None


def exigir(user_id: int, caso: str) -> None:
    """Levanta `FonteUnicaOF` se `user_id` tem a chave do v2."""
    if recusa(user_id, caso):
        raise FonteUnicaOF(caso)


# Fora de `MENSAGENS` de propósito: os genéricos `recusa`/`exigir` leem essa tabela pela chave do v2
# e são fail-open; a receita vale por banco conectado e é fail-closed (`recusa_receita`).
MSG_RECEITA = (
    "Receita fixa não se cadastra mais: o Piggy prevê o que entra pelo extrato do seu banco "
    "no Open Finance, quando o mesmo pagador deposita todo mês (o salário, por exemplo)."
)


def recusa_receita(user_id: int) -> str | None:
    """O texto da recusa se `user_id` tem banco conectado (regra da Q40, a mesma do motor da
    Previsão: conexão fora de PAUSED/DELETED); senão None. Quem não tem banco segue cadastrando.
    Falha na consulta conta como banco conectado (`regra_ativa`): recusar é o lado seguro, porque
    com banco a Previsão ignora a receita manual de qualquer jeito."""
    from core.handlers.forma_pagamento import regra_ativa
    return MSG_RECEITA if regra_ativa(user_id) else None
