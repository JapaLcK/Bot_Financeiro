# ADR 0003 — Movimentações bancárias são declarações conferidas com o extrato

Status: aceito para implementação em 14/09/2026, decisão explícita do usuário na issue #188.

“Guardei/saiu do banco” significa que a pessoa declara ter feito a operação. O PigBank registra o lado da caixinha/investimento, mas não executa uma ordem bancária nem confirma uma operação pelo tempo transcorrido. A declaração sem prova permanece visível; não expira, não é estornada automaticamente e não desaparece por um sync sem a transação.

O saldo bancário é o último espelho observado. Não se aplica uma compensação presumida ao patrimônio: o snapshot pode já refletir uma operação cujo extrato ainda não chegou. Enquanto houver declaração não confirmada, o patrimônio aparece “A conferir”, com as declarações separadas dos saldos observados.

O vínculo pertence a `bank_movement_declarations`: uma declaração por lançamento e uma prova OF por vínculo. Débito de aporte usa bruto; crédito de resgate usa líquido de impostos. A mesma rotina transacional atende declaração→extrato e extrato→declaração. Confirmação automática exige conta BANK ativa em BRL, valor assinado, data próxima, categoria econômica de aplicação/resgate e candidato único nos dois sentidos. Pagamento de fatura nunca é prova de aporte. Transferência genérica só pode ser vinculada pela escolha explícita do usuário; o método manual fica registrado. Vínculos de conciliações antigas não podem ser tomados.

Um aporte já comprovado pelo extrato pode ser declarado mesmo quando o saldo pós-aporte é menor que seu valor. A mesma elegibilidade econômica é revalidada sob lock antes da escrita. Sem saldo e sem prova, as guardas atuais permanecem; resgate declarado não credita disponibilidade antes da prova.

Origem de lotes mista/indisponível não identifica automaticamente uma conta. Mantém-se a regra financeira #286, registrando que essa origem precisa de conferência. Legados incompletos exigem revisão; migração não supõe que um sync antigo confirmou os fatos.

Correção da prova que deixe de corresponder à declaração remove a confirmação e preserva os efeitos/lotes manuais. Exclusão do extrato ou desconexão remove a prova, nunca a declaração. Exclusão/desfazer manual continua sujeito às guardas de integridade existentes e deixa a transação reimportável. Vínculo e remoção da linha analytics-only do OF acontecem na mesma transação, sem usar undo em conexão separada.

A escolha da transação pelo usuário reaplica proprietário, conta, moeda, direção, valor, janela e exclusividade. Não existe “confirmar sem prova”. Resultados locais e simulações não validam a Pluggy de produção.

## Precisão e concorrência da conferência

O líquido bancário é registrado/comparado em centavos (Decimal, ROUND_HALF_UP), mantendo precisão original do lote e do resumo tributário. Delta contábil de sombra não é arredondado: apenas zero finito explícito permite consumir lançamento analytics do Open Finance.

A conta do usuário é travada antes de ativos/transações nos escritores de declaração e conferência. A exclusão segue essa ordem somente para declarações bancárias e sombras OF com delta exatamente zero, usando o mesmo predicado e revalidando o lançamento owned após a trava. Não depende do is_internal histórico: o provedor pode recategorizar uma despesa para aplicação antes de atualizar a sombra. Pagamentos manuais preservam sua ordem existente de fatura e conta.

Pausa, status terminal, exclusão e substituição da conta revalidam a prova na transação. Se a origem sair do recorte canônico, ela se torna ambígua; mantém-se a referência histórica, e outra conta só pode ser escolhida explicitamente. Método/data de confirmação são limpos quando a prova deixa de valer. Reconectar não autoriza presumir que duas contas sejam a mesma origem.

## Fronteira transacional entre cartão e banco

Correções de CREDIT (compra/estorno e totais de fatura) commitam antes da fase BANK (declaração, vínculo e remoção de sombra atômicos). Pagamento de fatura mantém a fatura travada enquanto um helper em outra conexão debita accounts; manter accounts durante correção de fatura criaria espera circular. Falha em BANK propaga e permite retry, cujo comparador não reaplica diferenças de crédito já persistidas.

Desconexão conclui rollback e cleanup de cartões vazios antes de adquirir accounts para excluir conexão e invalidar provas. O cartão sobrevivente usa FK SET NULL na origem OF, sem cascade em faturas nessa fase. `swept_out` só recebe IDs depois do commit final. Falha entre fases pode deixar cleanup concluído e conexão existente para retry, sem declarar sucesso global.
