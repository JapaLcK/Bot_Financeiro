"""Textos do Piggy para despesa/receita declarada "paga pelo banco" (ADR 0006, PR-B; T1 a T9 originais aprovados
pelo dono em 2026-10-26; T10, o T9 reescrito e o T1 SEM_CONTA em 2026-10-27; T11 (VALOR_AMBIGUO) teve a SUBSTÂNCIA
aprovada em 2026-10-27 e a redação final, com asteriscos, é do Coder; `.time-dev/pr-b/decisoes-dono-b.md`; T12, T14 e
T15 do leitor estrito, issue #1310, aprovados pelo dono em 2026-10-28; T16 e T17 (decisões E1 e E2) e o T12 reescrito
pela E3, aprovados pelo dono no mesmo dia; não há T13).
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
VALOR_INCERTO = ("🐷 Não registrei: não sei qual desses números é o valor. Manda de novo só o valor em número, com *R$* "
                 "antes; se o nome tem número, escreve junto (*99pop*). Por exemplo: *gastei R$ 80 na Renner pelo banco*."
                 )  # T12 (redação da rodada 3 da #1310)
PARCELADO = ("🐷 Não registrei: compra parcelada não sai inteira da conta de uma vez. Se você pagou uma parcela agora, "
             "manda só ela, por exemplo: *paguei R$ 200 da parcela da geladeira pelo banco*.")  # T14
LONGA = ("🐷 Não registrei: a mensagem ficou longa demais para eu ter certeza do valor. Manda só o gasto, por exemplo: "
         "*gastei 50 no mercado pelo banco*.")  # T15
UM_POR_MENSAGEM = ("🐷 Não registrei: pelo banco eu registro um gasto por mensagem. Manda cada um separado, por exemplo: "
                   "*gastei 20 no mercado pelo banco*.")  # T16
APROXIMADO = ("🐷 Não registrei: pelo banco eu preciso do valor exato. Manda de novo com o valor certinho, por exemplo: "
              "*gastei 50 no mercado pelo banco*.")  # T17
# Rodadas 10 e 11 da #1310: a fila respondida com valor + "cartão" não grava, mantém o item e repete a pergunta. Nenhum
# afirma "foi no cartão" (a resposta pode negar). *deixa* é o descarte da fila INTEIRA (`launches._CANCEL_WORDS`). Os
# dois primeiros só para despesa no crédito; débito com cartão, receita e o resto recebem o neutro. Aprovados pelo dono.
FILA_CARTAO_MANUAL = ("🐷 Não registrei e mantive este item na fila. Se foi no cartão, responda *deixa* (isso descarta "
                      "todos os itens que faltam) e envie o lançamento completo, por exemplo: *paguei o aluguel 1500 no "
                      "cartão*. Se não foi, me manda só o valor.")
FILA_CARTAO_OF = ("🐷 Não registrei e mantive este item na fila: compra no cartão de crédito conectado chega sozinha pelo "
                  "Open Finance. Se foi Pix, débito ou transferência, me manda só o valor; se foi no cartão, responda "
                  "*deixa* (isso descarta todos os itens que faltam).")
FILA_CARTAO_NEUTRO = ("🐷 Não registrei e mantive este item na fila: não sei se foi pelo banco ou no cartão. Se foi Pix, "
                      "débito ou transferência, me manda só o valor (ex.: *1500*); para desistir, responda *deixa* (isso "
                      "descarta todos os itens que faltam).")
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
