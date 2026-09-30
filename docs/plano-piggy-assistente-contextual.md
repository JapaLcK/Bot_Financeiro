# Piggy como assistente financeiro contextual — plano revisado (v3)

> Planejamento, sem autorização para implementar. Código conferido em `origin/main` `a4a5e251`
> em 30/09/2026. Antes de implementar, partir da `main` atual e repetir a conferência
> (`git fetch origin main` com sucesso **antes** de `git rev-list --count a4a5e251..origin/main`:
> sem o fetch, uma referência remota velha dá 0 e valida premissas obsoletas; qualquer número
> acima de 0 é aviso).

## Objetivo e decisões do dono

A Piggy atende dúvidas sobre **finanças pessoais**, inclusive compras, parcelamento e custo de
esperar. Mostra fatos, hipóteses, contas e orientação compreensível. O usuário corrige a oferta
e decide. Uma imagem jamais autoriza lançamento ou contratação.

Decisões registradas (29/09/2026): a orientação de compra segue **regras determinísticas e
visíveis**, sem julgamento livre do modelo; o primeiro canal para imagem de oferta é **print
pelo WhatsApp**. Um cliente para macOS e Windows depende de demanda medida. "Qualquer dúvida
financeira" não promete cálculo quando faltam dados, política ou ferramenta: nesses casos a
Piggy explica a limitação ou pede um dado específico. Conselho sobre ativos específicos continua
fora do escopo.

Decisões registradas (30/09/2026):
- alertas negativos e respostas inconclusivas saem **antes**; o "cabe" fica desligado até a
  estimativa de gastos variáveis ser validada;
- "não recomendo" também exige dados confiáveis: saldo ou datas incompletos podem inverter a
  conclusão nos dois sentidos;
- o fluxo novo de print **falha fechado** no gate de plano, ao contrário do OCR atual;
- a meta de falsos "cabe" é definida antes de ligar respostas positivas, não antes do piloto;
- controle de snapshot fica fora da primeira entrega.

## Base real a reutilizar

| Já existe na `main` | Limite para este plano |
| --- | --- |
| `decision_simulator.simulate()` e tool `simulate_purchase` | Até três cenários; 90 dias de caixa; sem recomendação; gate Pro |
| `cashflow._cashflow_events()` e `cashflow_forecast._trajectory()` | Fonte dos compromissos e do pior dia. Gastos fixos automáticos entram em todas as frequências (mensal, anual, semanal, diária e única); receitas fixas só mensais e anuais; gasto avulso futuro não entra |
| `media_service.transcribe_audio()` e `analyze_image()` | Voz já transcrita no WhatsApp; imagem extrai comprovantes e lançamentos, não condições de oferta |
| `ai_chat.chat()` compartilhado entre web e WhatsApp | Reconsulta dados; a guarda de afirmações numéricas (`_log_unsupported_claims`) só registra logs |
| `handle_incoming._handle_image()` | Interpreta imagem como lançamento a confirmar, processa antes do texto da legenda e **falha aberto** quando o gate de plano dá erro |

O **seam** de cálculo é o simulador existente. Chat, print e eventual desktop devem chamá-lo.
Não criar uma segunda visão de capacidade de compra nem uma segunda fonte de eventos de caixa.

A estimativa de gasto variável é uma **hipótese da simulação de compra**, identificada como tal.
Não alterar silenciosamente o resultado de `cashflow.project()` e das previsões já usadas por
outros recursos. Só promover a estimativa à fonte compartilhada com decisão de produto e
migração de contrato próprias.

## Falhas que tornam uma orientação perigosa

1. **Gasto variável fora da conta.** Mercado, transporte e consumo cotidiano podem acabar com o
   saldo antes do salário. Uma média histórica precisa de período representativo, cobertura,
   reembolsos, sazonalidade e valores atípicos. Parte dos gastos pode já estar em faturas
   futuras: somar uma média bruta duplicaria despesas. Mostrar média, período e cobertura como
   hipótese. Sem estimativa validada, não há "cabe".
2. **Datas de parcelas supostas.** O simulador põe a primeira parcela um mês após a compra.
   Cartão depende de fechamento e vencimento; outras ofertas podem cobrar hoje. Capturar o
   calendário contratual. No cartão, as datas vêm do calendário da fatura (fechamento e
   vencimento), que é contratual. Fora do cartão, sem data contratual não sai veredito: a Piggy
   pergunta quando vence a primeira parcela e qual é o intervalo entre elas.
3. **Contrato maior que a janela.** O custo total inclui parcelas após 90 dias, mas o caixa não.
   Uma compra em 12 ou 36 vezes pode parecer segura por esconder a maior parte dos pagamentos.
   Orientação positiva exige avaliar todo o compromisso até um horizonte confiável explícito.
   Acima dele, mostrar somente a simulação limitada.
4. **Saldo parcial ou desatualizado.** Carteira manual, Open Finance, lançamentos a conciliar e
   renda prevista têm confiabilidades diferentes. `balance_source="unavailable"`, bancos
   excluídos e sincronização antiga impedem **qualquer** veredito, positivo ou negativo: um saldo
   parcial pode tanto esconder um risco quanto fabricar um. Renda apenas inferida só pode esconder
   risco e bloqueia o "cabe"; a matriz do contrato da resposta classifica cada caso. Pendências de
   conferência também contam: conciliação do Open Finance a confirmar
   (`reconciliation.pending_count` e `delta_se_confirmar`, expostos por `get_balance`) e
   declarações não confirmadas (`bank_movements.pending_count`). Hoje
   `cashflow._starting_balance()` descarta esse estado, então o simulador precisa lê-lo à parte.
   Limite do cartão não é capacidade de pagar a fatura.
5. **Reserva padrão de zero.** O simulador aceita `reserva_minima=0`, insuficiente para concluir
   que a compra é prudente. Pedir uma reserva ou definir uma regra conservadora, visível e
   editável. Se o cenário atual já estiver abaixo da reserva, distinguir problema prévio do
   agravamento causado pela compra.
6. **Oferta incompleta.** Print pode omitir frete, tarifa, seguro, IOF, desconto condicionado,
   CET ou vencimento. Campo ilegível fica ausente, nunca zero. Separar recibo já pago de proposta
   de compra. Texto na imagem é dado não confiável e não pode instruir o assistente.

## Contrato da resposta

O cálculo retorna um estado estruturado: `dados_insuficientes`, `dados_desatualizados`,
`risco_identificado` ou `cabe_nas_premissas`. O veredito e os números essenciais são montados
pelo código a partir desse resultado; o modelo pode explicar, mas sua redação não pode inverter
o estado. A guarda atual, que só registra logs, não basta para isso.

- `risco_identificado` exige que nenhuma entrada cuja coluna "Bloqueia" da matriz abaixo inclua o
  risco esteja não confiável: saldo confiável (fonte disponível, sem bancos excluídos, sincronização dentro do limite,
  sem conciliação nem declaração pendente capaz de mudar o resultado), nenhuma receita ativa fora da
  projeção e datas de pagamento conhecidas ou confirmadas. Faltando um deles, o estado é `dados_insuficientes` ou
  `dados_desatualizados`, com o dado que falta. Com pendência de conciliação, o veredito só sai se
  sobreviver ao pior caso **por direção**, e não ao `delta_se_confirmar` agregado: o agregado soma
  ajustes de sinais opostos que se cancelam, mas cada pendência é confirmada sozinha
  (`confirm_reconciliation` age por `of_tx_id`). O limite que enfraquece um "não recomendo" é a soma
  só dos ajustes individuais a favor do saldo; o que enfraquece um "cabe" é a soma só dos contra. É
  a mesma separação que `PENDING_RECONCILIATION_SQL` já faz para `receita_back`. Se o veredito não
  sobreviver, o estado é `dados_desatualizados` e a Piggy pede a conferência. Declaração pendente
  sem efeito quantificado no saldo impede o veredito. A falta **só** da
  estimativa variável não impede `risco_identificado`, porque ela apenas pioraria o caixa.
  Resposta: "não recomendo nesta condição", com data e valor.
- `cabe_nas_premissas` fica **desligado** até a estimativa variável ser validada (Etapa 3), e depois
  exige que nenhuma entrada cuja coluna "Bloqueia" inclua o cabe esteja não confiável. Quando
  ligado: "pelos dados informados, cabe mantendo a reserva de R$ X; confira Y".
- Se a situação já está ruim sem a compra, explicar separadamente o estado atual e o impacto
  incremental.

### Matriz das entradas do veredito

Fonte única da regra de abstenção. Cada entrada do cálculo é classificada pela direção do erro
quando ela falta ou está errada:

- se o erro **só piora** a projeção, a entrada pode fabricar um risco e bloqueia `risco_identificado`;
- se o erro **só melhora** a projeção, a entrada pode esconder um risco e bloqueia `cabe_nas_premissas`;
- se o erro vai **nos dois sentidos**, a entrada bloqueia os dois.

Quando não existe marcador para saber se a entrada é confiável, o veredito é calculado com ela no
**pior caso da direção dele**: o "não recomendo" é calculado sem as saídas duvidosas, e o "cabe"
sem as entradas duvidosas. Se o veredito só sobrevive com a entrada duvidosa, a Piggy faz a
pergunta específica ("o salário deste mês já caiu?", "você já pagou a conta X?") em vez de concluir.

Uma entrada nova no simulador entra nesta tabela antes de entrar no código. Conferido em
`cashflow._starting_balance()`, `cashflow._cashflow_events()` e `decision_simulator`.

| Entrada | Situação que a torna não confiável | Direção do erro | Bloqueia |
| --- | --- | --- | --- |
| Saldo de partida | carteira manual sem Open Finance: `accounts` guarda só `balance`, sem data de atualização nem confirmação, e `_starting_balance()` a aceita como `manual`; um gasto ou receita não lançado deixa o saldo velho. Nesse caso, a Piggy pede que o usuário confirme o saldo de hoje antes do veredito. Também: `balance_source="unavailable"`, bancos excluídos, sincronização do Open Finance acima do limite, ou qualquer conexão fora do estado `updated` de `connection_ui_state()` (`core/services/pluggy_health.py`). Esse classificador é a fonte única do estado da conexão: já trata sincronização parcial (`stale_products`, `PARTIAL_SUCCESS`, com `last_sync_at` recente) e banco religado cujo `last_sync_at` é anterior ao `reconnected_at`. O simulador consulta esse estado, não a idade do sync sozinha | dois sentidos | os dois |
| Conciliação pendente | pendência em `PENDING_RECONCILIATION_SQL` | dois sentidos, item a item | o veredito que não sobreviver à soma na direção que o enfraquece |
| Declaração pendente | `bank_movements.pending_count > 0`, sem efeito quantificado | dois sentidos | os dois |
| Conta ou cartão em moeda estrangeira | `BANK_ACCOUNTS_SQL` (`db/open_finance.py`) só soma conta em BRL, então uma conta em outra moeda nem conta como banco excluído; e `import_open_finance_credit()` importa a fatura sem guardar a moeda, tratando o valor como reais | dois sentidos | os dois, enquanto houver conta ou cartão ativo fora de BRL (sem conversão com cotação datada) |
| Ação financeira pendente de confirmação | `pending_actions` em aberto que mexe em dinheiro: lançamento aguardando "sim" (inclusive `confirm_media_launch`), pergunta de valor ou de forma de pagamento, e as pendências do chat (`db/ai_chat.py`). O valor ainda não está no saldo e pode entrar | dois sentidos | os dois; com valor quantificado no payload, vale o pior caso da direção, como na conciliação |
| Receita fixa ativa em frequência fora da projeção | `once`, `weekly` ou `daily` legados: `_cashflow_events()` pula essas receitas | só piora | risco (ou estender a fonte de eventos) |
| Ocorrência de receita fixa do mês corrente | antecipada: o salário caiu antes do `pay_day`, já está no saldo e a ocorrência futura entra de novo. No dia ou atrasada: hoje é o `pay_day` ou ele já passou e o salário ainda não caiu, e `_recurring_occurrence_dates()` só emite datas estritamente depois de hoje, então ela some. Não há marcador: receita recorrente só prevê, e `last_credited_ym` é resto do cobrador removido que nada escreve (`db/recurring_income.py`) | dois sentidos | cada veredito no seu pior caso: o "cabe" sem a ocorrência do mês e o risco com ela (inclusive a de hoje e a atrasada). Se o veredito depender dela, perguntar se o salário já caiu |
| Ocorrência de gasto fixo automático do mês corrente | antecipada: saiu antes do `due_day`, já está no saldo e a ocorrência futura sai de novo. No dia ou atrasada: hoje é o `due_day` ou ele já passou e a cobrança ainda não saiu, e a ocorrência some pelo mesmo motivo. Não há marcador: `last_charged_ym` também não é escrito (`db/recurring.py`) | dois sentidos | cada veredito no seu pior caso: o risco sem a ocorrência do mês e o "cabe" com ela (inclusive a de hoje e a atrasada). Se o veredito depender dela, perguntar se já foi cobrado |
| Boleto pendente | pode já ter sido pago pelo banco: o débito aparece no saldo consolidado, mas o `bill_instances.status` segue `pending`, porque a importação do Open Finance não marca conta paga (nenhum módulo de Open Finance escreve em `bill_instances`), e `_cashflow_events()` subtrai o boleto de novo | só piora | risco: calcular sem o boleto; se o risco depender dele, perguntar se já foi pago |
| Fatura de cartão em aberto | pode já ter sido paga pelo banco: o débito aparece no saldo consolidado, mas a importação do Open Finance pula o pagamento de fatura (`import_open_finance_credit` em `db/open_finance.py`), e só `pay_bill_amount` (`db/cards.py`) atualiza `paid_amount`/`status`. `_open_card_bills_detail()` subtrai o saldo da fatura de novo | só piora | risco: calcular sem essa fatura; se o risco depender dela, perguntar se já foi paga |
| Calendário do cartão | `get_or_create_open_finance_card()` (`db/cards.py`) usa dia 1 e dia 10 quando a Pluggy não manda `balanceCloseDate`/`balanceDueDate`, não atualiza o calendário de um cartão já ligado e adota um cartão manual sem validar as datas. O calendário pode ser chute ou estar velho, e move tanto as parcelas novas quanto o vencimento das faturas abertas | dois sentidos | os dois, enquanto o calendário não tiver origem conhecida (vindo da Pluggy nesta sincronização ou confirmado pelo usuário); sem isso, perguntar o fechamento e o vencimento |
| Leitura de boletos truncada | `_cashflow_events()` chama `list_bills(..., limit=1000)`, ordenado pelo vencimento mais próximo; acima de 1.000 pendentes, os mais distantes somem mesmo dentro do horizonte | só melhora | cabe: paginar ou tirar o teto, ou tratar leitura truncada como `dados_insuficientes` |
| Parcelas futuras de compra importada do Open Finance | `import_open_finance_credit()` grava `installment_no`/`installments_total` só das parcelas já lançadas e, ao contrário de `add_credit_purchase_installments()`, não cria as faturas futuras; `_open_card_bills_detail()` não vê o restante do compromisso | só melhora | cabe: materializar o restante do grupo ou bloquear enquanto houver grupo importado incompleto |
| Boleto pendente com valor zero ou negativo | `_cashflow_events()` subtrai o boleto com qualquer valor (regra herdada de `project`), e um valor negativo vira entrada de dinheiro | só melhora | cabe: tratar como `dados_insuficientes` até o valor ser corrigido |
| Receita fixa projetada que pode não vir | renda apenas inferida, ou renda irregular (freela, comissão) cadastrada como mensal. Não há campo de confiabilidade: `db/recurring_income.py` guarda valor e calendário, e `_cashflow_events()` conta toda receita mensal ou anual ativa | só melhora | cabe: só entra receita que o usuário confirmou como garantida na própria análise; persistir essa confirmação num campo é decisão da Etapa 3 |
| Gasto fixo automático pago no cartão | `payment_type="credit_card"`: `_cashflow_events()` ignora `payment_type` e tira o valor do caixa no `due_day`, e a mesma função tira a fatura aberta no vencimento dela. Depois que a cobrança é lançada no cartão, o valor sai duas vezes; antes, sai na data da cobrança em vez da data de pagar a fatura | só piora (dupla contagem ou saída antecipada) | risco, até esses gastos passarem pelo calendário da fatura sem duplicar |
| Gasto fixo manual sem boleto gerado | só entra quando o boleto pendente já existe; conferir na implementação se as ocorrências futuras sem boleto ficam fora | só melhora | cabe |
| Valor estimado | `variable_amount=true` no gasto fixo, exposto por `db/bills.py` e `db/recurring.py`: `_cashflow_events()` ignora a flag e subtrai a estimativa como valor exato, tanto no boleto pendente quanto no gasto fixo automático. A conta real pode vir maior ou menor | dois sentidos | os dois, até o valor ser confirmado |
| Gasto variável | fora da projeção até a Etapa 3; depois dela, a estimativa é hipótese e pode vir acima do gasto real | ausente: só melhora; estimada: dois sentidos | a estimativa entra só no cálculo do "cabe". O risco é sempre calculado sem ela, o pior caso da direção dele |
| Custos da oferta ausentes | frete, IOF, seguro, tarifa ou CET não informados | só melhora | cabe |
| Parcela sem valor nem taxa | cronograma cotado ausente | dois sentidos | os dois (`dados_insuficientes`) |
| Datas das parcelas | data presumida fora do cartão | dois sentidos | os dois (`dados_insuficientes`, perguntar a data) |
| Parcelas além da janela | contrato maior que o horizonte avaliado | só melhora | cabe |
| Despesa mensal nova sem fim | `despesa_mensal_nova` não tem fim contratado: estender o horizonte até a última parcela não a cobre, e ela pode furar a reserva depois da janela | só melhora | cabe: exige que a sobra mensal recorrente (receitas fixas − gastos fixos − gasto variável estimado − a despesa nova) continue positiva; senão, sem "cabe" |
| Horizonte que não fecha um ciclo de recorrências | `_cashflow_events()` projeta gastos fixos anuais; um seguro anual pode furar a reserva depois da última parcela mesmo com sobra mensal positiva | só melhora | cabe: a trajetória vai até o mais distante entre a última parcela, 12 meses a partir de hoje e a próxima ocorrência de cada recorrência ativa (um `start_date` futuro pode empurrar a primeira cobrança anual para quase dois anos), e o pior dia dela tem de ficar acima da reserva. Se alguma ocorrência cair além do horizonte confiável, sem "cabe" |
| Reserva mínima | nunca informada | não se aplica | risco baseado em reserva (vale só o risco de saldo negativo) e cabe |

Avaliar **caixa nas datas de pagamento** e **custo total do contrato** como dimensões diferentes.
Parcela menor pode custar mais; à vista pode custar menos e quebrar o caixa. "Quantas vezes?"
exige varrer somente as opções realmente oferecidas, com o mesmo motor e um teto operacional.
Não presumir "sem juros" se a oferta só mostra preço e número de parcelas.

**O contrato atual do simulador não representa uma oferta real e precisa ser estendido** antes de
receber ofertas do chat ou do print:

- **Cronograma cotado.** Hoje `Cenario` só aceita `preco`, `parcelas` e `juros_mensal_pct`, calcula
  as parcelas ele mesmo e põe cada uma um mês depois da anterior (`_add_months` em
  `_decision_events`). "À vista R$ 1.000 ou 12× R$ 100" não cabe: `preco=1200` com taxa 0 reporta
  juros zero, e `preco=1000` não reproduz as parcelas. Um cronograma irregular, como pagamentos em
  15 e 90 dias, também não cabe. O cenário passa a aceitar o cronograma como pares
  **(valor, data)**, em que a data é opcional: parcela sem data informada fica marcada como
  **data presumida** (cadência mensal a partir da compra), e o código do veredito distingue data
  contratual de data presumida. A data presumida serve só para a simulação informativa. O que é exclusivo é o **modo de gerar as parcelas**: pelo cronograma cotado ou
  pela taxa (`juros_mensal_pct`), nunca os dois. A taxa nominal anunciada e o CET, quando aparecem,
  entram como dados informativos ao lado do cronograma, sem gerar parcelas. O custo em relação ao
  à vista e a taxa implícita são calculados a partir do cronograma, e divergência entre a taxa
  implícita e a anunciada é mostrada ao usuário. Sem valor de parcela nem taxa, o estado é
  `dados_insuficientes`, nunca taxa zero. Com data presumida fora do cartão, o estado é
  `dados_insuficientes` e a Piggy pergunta a data (item 2 das falhas). Não há teste de
  sensibilidade: sem um intervalo de datas admissíveis definido, ele não teria como provar que a
  conclusão não muda.
- **Forma de pagamento e cartão.** O cenário passa a levar a forma de pagamento e, no cartão, o
  `card_id`. As datas das parcelas no cartão saem do fechamento e do vencimento **desse** cartão;
  com mais de um cartão e nenhum escolhido, a Piggy pergunta qual antes do veredito. Cartão não é
  data presumida mensal. O calendário do cartão só conta como contratual se tiver origem
  conhecida (linha "Calendário do cartão" da matriz). Compra no dia do fechamento não diz se
  entrou antes ou depois do corte (`_bill_period_for_purchase()` põe sempre na fatura atual): o
  veredito é calculado nos dois ciclos e só sai se valer nos dois; senão, `dados_insuficientes`.
- **Toda saída com data é contratual ou presumida.** Vale para cada fluxo do cenário, não só para
  as parcelas: data da compra, entrada, custos únicos e a `despesa_mensal_nova`, que hoje começa
  sempre um mês após a compra (`_decision_events`). A despesa nova passa a levar data da primeira
  cobrança e cadência. Qualquer data presumida que possa decidir o veredito leva a
  `dados_insuficientes` e à pergunta da data.
- **Mais de três opções numa leitura.** `Simulacao.cenarios` tem `max_length=3`. Uma varredura de
  1 a 12 vezes não cabe, e dividi-la em várias chamadas de `simulate()` compararia as opções
  contra leituras diferentes do saldo. O limite sobe até o teto operacional da varredura, numa
  única chamada de `simulate()`, que já lê saldo e eventos uma vez só para todos os cenários.
  O limite tem cópias no caminho do chat que sobem juntas: `maxItems: 3` e a descrição "1 a 3
  cenários" do schema em `core/services/ai_chat/tools/simulator.py`, e o roteamento em
  `core/services/ai_chat/system_prompt.py` ("Monta 1 a 3 cenários"). Os mesmos dois arquivos
  mandam o modelo não apontar vencedor; isso muda junto com o contrato de veredito. O teste passa
  pelo despacho real da tool com mais de três opções, não só pelo `Simulacao`.

Taxa nominal, taxa efetiva e CET são coisas distintas. Para "juros bons ou ruins", separar custo
versus à vista, encaixe no orçamento e comparação com mercado. A última requer referência
externa atual, mesma modalidade e prazo, com data e fonte. Sem CET e tarifas, a comparação de
custo pode estar incompleta. "Até o próximo salário" usa a próxima receita fixa válida e futura,
mas esse prazo curto não substitui a avaliação até a última parcela.

## Print pelo WhatsApp: fluxo antes de construir desktop

1. Ler **imagem e legenda juntas** e distinguir oferta, comprovante de gasto já realizado e outro
   conteúdo. Se a intenção estiver ambígua, perguntar sem criar pendência.
2. **Gate de plano falha fechado**: erro ao consultar o plano recusa a análise e não chama o
   provedor de visão. Não reaproveitar o `except` fail-open de `_handle_image`.
3. Extrair item, preço à vista, preço parcelado, entrada, frete, taxas/CET, quantidade e datas das
   parcelas e validade da oferta. Exibir prévia para correção; campos ausentes permanecem ausentes.
4. Usar estado de **consulta** separado de `confirm_media_launch`: "sim, o preço está correto" não
   pode registrar despesa. Expirar a prévia e vincular respostas à imagem certa quando houver
   mensagens fora de ordem.
5. Após correção, reler dados financeiros e usar `decision_simulator`. Responder com origem e idade
   dos dados, gasto variável estimado (quando existir), reserva, pior dia, custo total e parcelas
   fora do horizonte. Não persistir print por padrão.

"Retenção zero" só pode ser prometida após mapear WhatsApp/Meta, download, memória do processo,
provedor de visão, banco, logs e backups. Mesmo sem guardar bytes no PigBank, o canal e o
provedor podem ter retenção própria. Validar política e texto ao usuário antes do piloto.

## Etapas e portas de avanço

| Etapa | Entrega planejada | Evidência para avançar |
| --- | --- | --- |
| 0. Casos e dados | Inventariar fontes; montar casos rotulados de renda irregular, cartão, gastos variáveis, saldos parciais e prints | Casos cobrindo cada estado; regra de abstenção escrita |
| 1. Alertas negativos e inconclusivos | Estados `dados_insuficientes`, `dados_desatualizados` e `risco_identificado` montados pelo código no simulador e no chat; confiabilidade de saldo e datas checada antes do veredito; "cabe" desligado | Teste pela conversa `handle_incoming` em duas mensagens com novo lançamento entre elas; saldo parcial e data suposta geram inconclusivo, não "não recomendo"; o modelo não reverte o estado |
| 2. Parcelas e horizonte | Contrato do simulador estendido (cronograma cotado em pares valor/data, mais de três opções numa leitura); varredura das opções oferecidas; horizonte até a última parcela ou o teto | Casos de compra no fechamento, salário antes/depois da parcela, parcelas além de 90 dias, oferta com parcela cotada sem taxa, cronograma irregular, oferta com mais de três opções |
| 3. Caixa honesto e "cabe" | Estimativa variável explícita sem duplicar faturas; meta de falsos "cabe" definida; "cabe" ligado atrás de flag desligável | Taxa de falsos "cabe" medida nos casos rotulados abaixo da meta; dado incompleto nunca aprova |
| 4. Print no WhatsApp | Legenda preservada, gate que falha fechado, prévia corrigível, consulta sem escrita, mesma simulação | OCR errado, recibo, imagem ambígua, "sim", mensagens fora de ordem e erro no gate não causam lançamento, gasto de visão nem orientação indevida; medir demanda e custo |
| 5. Decisão sobre desktop | Comparar uso do print, abandono do fluxo, entrevistas e pedidos por acesso sobre outros apps | Só então especificar macOS/Windows; pouco uso do print sozinho não prova falta de demanda |

Voz de entrada já existe, mas foi desenhada para lançamentos; perguntas de compra por áudio
precisam do fluxo consultivo e da conferência dos números transcritos. Voz de saída e atalho
global ficam depois da validação do núcleo. Jev não é dependência: avaliá-lo apenas se o
roteamento atual apresentar erro medido em casos rotulados e houver ganho comprovado em modo
paralelo.

## Critérios de segurança e operação

- **Isolamento:** toda consulta e agregado filtram `user_id`; testar duas contas e garantir que
  dados, imagem e contexto não se misturem.
- **Planos:** o simulador exige Pro; chat e OCR seguem gates diferentes. Definir quem pode usar
  print antes de divulgar. O gate do print falha fechado.
- **Custo:** medir chamadas, tokens, imagens, áudio, retries e custo por análise, canal e plano.
  Cota de mensagens não mede custo multimodal. Definir teto e comportamento ao excedê-lo.
- **Observabilidade:** registrar versão da política, estado, motivo da recusa, idade das fontes e
  hipóteses, sem imagem ou texto financeiro bruto em logs.
- **Consistência:** na primeira entrega, carimbar a hora do cálculo e reler os dados a cada
  pergunta. Controle de snapshot ou detecção de mudança concorrente fica para quando houver
  incidente medido. Janela conhecida e aceita: pagar conta e pagar fatura fazem dois commits
  (`mark_bill_paid` reserva a conta antes de debitar, de propósito, e `db/cards.py` debita antes de
  atualizar a fatura), então uma simulação que leia entre eles vê um estado intermediário. A janela
  dura o intervalo entre dois commits da mesma requisição, e exige o mesmo usuário pagando e
  perguntando ao mesmo tempo. Se virar incidente, o primeiro gate é a assinatura que o próprio
  `mark_bill_paid` já usa para a reserva (conta `paid` sem `launch_id`).
- **Regressão (inventário, não escopo de um PR só):** gastos variáveis ausentes e duplicados,
  compra no fechamento do cartão, salário antes/depois da parcela, fatura vencida, parcelas além
  de 90 dias, reserva já violada, banco indisponível, oferta sem CET, OCR errado, legenda ignorada,
  pendência de lançamento, receita ativa em frequência legada, boleto ou gasto fixo com valor
  estimado (`variable_amount`), receita ou gasto fixo do mês antecipado ou atrasado em relação ao dia previsto (sem marcador), despesa mensal nova sem fim,
  gasto fixo anual depois da última parcela, carteira manual com saldo não confirmado, boleto ou fatura de
  cartão paga pelo banco e ainda aberta no PigBank, gasto fixo automático pago no cartão
  (antes e depois de a cobrança entrar na fatura), conciliação ou declaração do Open
  Finance pendente (inclusive com
  ajustes de sinais opostos que se cancelam no agregado), saldo alterado
  após novo gasto, erro no gate do print.
- **Lançamento gradual:** alertas negativos e inconclusivos primeiro; "cabe" só após a meta de
  falsos "cabe" definida e atingida. Poder desligar a orientação sem desligar a simulação
  informativa.

## Decisões ainda abertas

1. Qual reserva mínima usar se o usuário nunca informou uma? Bloqueia o "cabe" e o
   `risco_identificado` baseado em reserva (sem ela, só vale o risco de saldo negativo).
2. Que cobertura e método de estimativa de gasto variável bastam, especialmente com cartão e Open
   Finance juntos? Bloqueia o "cabe".
3. Qual sincronização máxima do Open Finance conta como saldo confiável? Bloqueia a Etapa 1.
4. Até que prazo há dados confiáveis para orientar e qual é o limite de parcelas?
5. Qual tier e teto de custo permitem print de oferta? Política igual entre WhatsApp e web.
6. Que métrica de uso do print justifica o cliente de desktop, e qual sistema vem primeiro?

## Faixa do time-dev
Etapas 1 a 4: **Completo** (dinheiro; o print também envolve gate de plano pago e PII).

## Fontes no repositório
`core/services/decision_simulator.py`, `core/services/cashflow.py`,
`core/services/cashflow_forecast.py`, `core/services/ai_chat/tools/simulator.py`,
`core/services/ai_chat/runner.py`, `core/services/media_service.py`, `core/handle_incoming.py`,
`adapters/whatsapp/wa_parse.py`, `frontend/routes/simulator.py`,
`core/services/plan_service.py`; conferidos em `origin/main` `a4a5e251`.

