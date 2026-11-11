"""Textos do Piggy para despesa/receita declarada "paga pelo banco" (ADR 0006, PR-B; T1 a T9 originais aprovados
pelo dono em 2026-10-26; T10, o T9 reescrito e o T1 SEM_CONTA em 2026-10-27; T11 (VALOR_AMBIGUO) teve a SUBSTÂNCIA
aprovada em 2026-10-27 e a redação final, com asteriscos, é do Coder; `.time-dev/pr-b/decisoes-dono-b.md`; T12, T14 e
T15 do leitor estrito, issue #1310, aprovados pelo dono em 2026-10-28; T16 e T17 (decisões E1 e E2) e o T12 reescrito
pela E3, aprovados pelo dono no mesmo dia; não há T13; T18 e T19, do chat IA (PR-B (b)), aprovados em 2026-11-02,
`.time-dev/prb-chat/decisoes-dono.md`).
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
SEM_ID = ("🐷 Não registrei: por aqui eu não consigo garantir que o mesmo pagamento pelo banco não entre duas vezes. "
          "Manda pelo WhatsApp. Se foi em dinheiro vivo, diga *em dinheiro*.")  # T18: canal sem id de mensagem
ACAO_ENCERRADA = ("🐷 Não registrei: esse pedido já tinha sido cancelado ou ficou sem resposta. "
                  "Se quiser registrar, me manda de novo.")  # T19: reentrega de um pedido descartado ou abandonado
# O "sim" já recebido que volta sem ter gravado nada, quando o sistema não sabe o desfecho (D-a, rodada 8, 2026-11-04)
SIM_JA_RECEBIDO = "🐷 Essa resposta eu já tinha recebido. Confere no extrato antes de mandar de novo."
NAO_JA_RECEBIDO = "🐷 Essa resposta eu já tinha recebido."  # o "não" visto sem declaração viva: não há o que conferir
# A confirmação do roteador (#1315; textos aprovados pelo dono em 2026-11-09, `.time-dev/1315/decisoes-dono.md`). A
# pergunta em si é a da IA (`runner._CONFIRMA`, DP-1).
NAO_FIZ_NADA = "👍 Beleza, não fiz nada."  # DP-3 e R2-5: o "não" (o mesmo texto da IA)
EXPIROU = ("🐷 Essa confirmação expirou. Não registrei por ela. Se quiser registrar, envie o gasto "
           "novamente.")  # DP-5: o "sim" (ou o botão) de uma confirmação vencida
ENVIADA_ANTES = ("🐷 Essa resposta foi enviada antes da minha última pergunta, então não registrei nada "
                 "por ela.")  # R2-3: o "sim" sem prova de ter sido enviado depois da confirmação viva
BOTAO_VELHO = ("🐷 Esse botão é de uma confirmação que não vale mais. Não registrei nada "
               "por ele.")  # R2-4
# R4-4 (dono, 2026-11-10): a correção que não é só um valor, ou cujo valor não se lê com certeza. Sai DEPOIS de a
# confirmação anterior ser invalidada; nada fica para confirmar.
CORRECAO_INCOMPLETA = ("🐷 Não registrei: não consegui interpretar toda a correção. Cancelei a confirmação anterior. "
                       "Me manda o gasto completo de novo, com valor, descrição, data e forma de pagamento.")
# R4-2 (dono, 2026-11-10; as variantes com *sim* e com *não* aprovadas nessa data): o "sim" (ou "não") que chega
# com a confirmação suspensa por outro comando em andamento.
AINDA_TERMINANDO = ("⏳ Ainda estou terminando sua mensagem anterior. Não registrei nada por este *{resposta}*; se eu "
                    "perguntar de novo, é só responder.")
SEM_CONTA = ("🕓 Anotei como pago pelo banco. Ainda não apareceu no seu extrato e, sem uma conta corrente "
             "conectada, eu não consigo conferir sozinho: confira em Patrimônio total → Movimentações "
             "não confirmadas.")  # T1, variante de quem não tem conta corrente ativa


def com_par(par: dict) -> str:  # T2
    return (f"✅ Já estava no seu extrato e conferi: {par['dia']:%d/%m} · {par['alvo'] or 'sem descrição'} · "
            f"{fmt_brl(float(par['valor']))}. Sua Carteira não mudou.")


def valor_casas(valor: float, molde: str = VALOR_CASAS) -> str:
    """O valor como o usuário o escreve (vírgula, sem notação científica), nunca o `repr` do float. `molde`: o T4 com o
    negrito do canal (`declaracao_ia._molde`)."""
    return molde.format(valor=format(Decimal(str(valor)), "f").replace(".", ","))


def valor_abreviado(trecho: str) -> str:  # T10
    return VALOR_ABREVIADO.format(trecho=trecho)


def valor_ambiguo(trecho: str) -> str:  # T11
    return VALOR_AMBIGUO.format(trecho=trecho)
