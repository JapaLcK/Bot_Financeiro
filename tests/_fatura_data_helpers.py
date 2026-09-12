"""
tests/_fatura_data_helpers.py — a data da fatura nos testes de pendência de
crédito.

Arquivo próprio porque `tests/_pendencia_credito_helpers.py` estava em 349
linhas, a uma do teto de `tests/test_max_lines_python.py`, e porque o assunto é
outro: lá é ARMAR pendência, aqui é derivar a data da fatura que o armador
criou. Importe daqui — não há re-export no irmão, de propósito (§0.7).

Existe por um bug pago em produção de CI: dois casos cravavam `"setembro"` e
ficaram vermelhos no dia 11 de setembro de 2026, travando o merge. Eles
quebrariam do dia 11 ao fim de TODO mês, para sempre, em qualquer branch.
"""
from __future__ import annotations

import db

# Marcador para "responda o MÊS da fatura, por nome" numa lista de
# `parametrize`. A parametrização é avaliada na COLETA, quando ainda não existe
# conta nem fatura, então o nome do mês não pode estar lá — ele é resolvido
# dentro do teste, depois do armador, por `mes_da_fatura`.
MES_DA_FATURA = "<mes-da-fatura>"


def mes_da_fatura(uid: int) -> str:
    """O nome do mês da fatura que `arma_pay_bill_choice` de fato criou.

    **DERIVA, nunca crava.** O caso cravava `"setembro"` e quebrou no dia 11 de
    setembro de 2026 — e quebraria do dia 11 ao fim de TODO mês, para sempre, em
    qualquer branch: `cartao()` fecha no dia 10 e o armador compra em
    `date.today()`, então compra feita depois do dia 10 cai na fatura do mês
    SEGUINTE. No dia 11/09 a fatura em aberto era `Nubank — Outubro/2026` e o
    teste respondia "setembro".

    Cravar `"outubro"` no lugar seria o mesmo bug adiado até o dia 1º. Um teste
    com data dentro não pode assumir o mês do calendário — ele pergunta ao
    estado que acabou de criar.

    Lê `period_end` e o vocabulário de `_MONTH_NAMES_PT`, que são a MESMA fonte
    que o produto usa para rotular a fatura (`credit._format_bill_label`) e para
    interpretar a resposta (`credit._parse_month_year_token`) — §0.7. Uma lista
    de meses escrita aqui divergiria da do produto sem ninguém ver.
    """
    from core.handlers.credit import _MONTH_NAMES_PT

    abertas = db.list_open_bills(uid)
    assert abertas, "não há fatura em aberto: o armador não rodou?"
    return _MONTH_NAMES_PT[abertas[0]["period_end"].month - 1].lower()


def resolve_resposta(uid: int, resposta: str) -> str:
    """Troca o marcador `MES_DA_FATURA` pelo mês real; o resto passa direto."""
    return mes_da_fatura(uid) if resposta == MES_DA_FATURA else resposta
