"""Textos do Piggy para despesa/receita declarada "paga pelo banco" (ADR 0006, PR-B; T1 a T9 originais aprovados
pelo dono em 2026-10-26; T10, o T9 reescrito e o T1 SEM_CONTA em 2026-10-27; T11 (VALOR_AMBIGUO) teve a SUBSTÂNCIA
aprovada em 2026-10-27 e a redação final, com asteriscos, é do Coder; `.time-dev/pr-b/decisoes-dono-b.md`).
Um teste pina cada um (tests/test_declaracao_b_textos.py).

T1 e T2 são só a LINHA do meio de uma resposta de lançamento (o cabeçalho "registrada pelo banco" e o ID saem de
`add_from_entities`, o resto da resposta é o de sempre). Nenhum texto expõe o código `DECLARACAO_*` do escritor.
"""
from __future__ import annotations

from decimal import Decimal

from utils_text import fmt_brl

SEM_PAR = "🕓 Ainda não apareceu no seu extrato. Quando aparecer, eu confiro sozinho. Sua Carteira não mudou."  # T1
REPETIDA = "🐷 Esse eu já tinha registrado (ID #{seq}). Não dupliquei."  # T3
VALOR_CASAS = ("🐷 Não registrei: o valor aceita no máximo 2 casas decimais (você mandou {valor}). "
               "Manda de novo, por exemplo: *gastei 50,12 no mercado pelo banco*.")  # T4
VALOR_INVALIDO = "🐷 Não registrei: o valor precisa ser um número maior que zero."  # T5
INTERNA = ("🐷 Não registrei como pelo banco: isso parece uma movimentação interna (investimento, transferência "
           "entre as suas contas ou pagamento de fatura), e o banco já mostra esse tipo de movimento sozinho. "
           "Se foi em dinheiro vivo, diga *em dinheiro*.")  # T6
MISTO = ("🐷 Mandou dinheiro e banco na mesma mensagem. Manda separado: uma mensagem com o que foi em dinheiro "
         "e outra com o que passou pelo banco (Pix, débito, transferência).")  # T7
CARTAO = "🐷 Não registrei: as compras no cartão de crédito conectado chegam sozinhas pelo Open Finance."  # T8
SEM_GARANTIA = ("🐷 Não consegui confirmar seu banco agora, então não registrei nada. "
                "Tenta de novo em instantes, mandando o gasto inteiro.")  # T9
VALOR_ABREVIADO = ("🐷 Não registrei: não tenho certeza do valor (você mandou {trecho}). "
                   "Manda de novo só com números, por exemplo: *gastei 2200 no mercado pelo banco*.")  # T10
VALOR_AMBIGUO = ("🐷 Não registrei: não sei se *{trecho}* é um valor só ou dois gastos. "
                 "Manda de novo só com números, por exemplo: *gastei 2,50 no mercado pelo banco*.")  # T11
SEM_CONTA = ("🕓 Anotei como pago pelo banco. Ainda não apareceu no seu extrato e, sem uma conta corrente "
             "conectada, eu não consigo conferir sozinho: confira em Patrimônio total → Movimentações "
             "não confirmadas.")  # T1, variante de quem não tem conta corrente ativa


def com_par(par: dict) -> str:  # T2
    return (f"✅ Já estava no seu extrato e conferi: {par['dia']:%d/%m} · {par['alvo'] or 'sem descrição'} · "
            f"{fmt_brl(float(par['valor']))}. Sua Carteira não mudou.")


def valor_casas(valor: float) -> str:
    """O valor como o usuário o escreve (vírgula, sem notação científica), nunca o `repr` do float."""
    return VALOR_CASAS.format(valor=format(Decimal(str(valor)), "f").replace(".", ","))


def valor_abreviado(trecho: str) -> str:  # T10
    return VALOR_ABREVIADO.format(trecho=trecho)


def valor_ambiguo(trecho: str) -> str:  # T11
    return VALOR_AMBIGUO.format(trecho=trecho)
