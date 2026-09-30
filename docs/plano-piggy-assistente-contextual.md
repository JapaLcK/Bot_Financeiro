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
   calendário contratual ou marcar a hipótese. Se a data puder alterar a conclusão, em qualquer
   sentido, pedir confirmação em vez de concluir.
3. **Contrato maior que a janela.** O custo total inclui parcelas após 90 dias, mas o caixa não.
   Uma compra em 12 ou 36 vezes pode parecer segura por esconder a maior parte dos pagamentos.
   Orientação positiva exige avaliar todo o compromisso até um horizonte confiável explícito.
   Acima dele, mostrar somente a simulação limitada.
4. **Saldo parcial ou desatualizado.** Carteira manual, Open Finance, lançamentos a conciliar e
   renda prevista têm confiabilidades diferentes. `balance_source="unavailable"`, bancos
   excluídos, sincronização antiga ou renda apenas inferida impedem **qualquer** veredito, positivo
   ou negativo: um saldo parcial pode tanto esconder um risco quanto fabricar um. Pendências de
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

- `risco_identificado` exige saldo confiável (fonte disponível, sem bancos excluídos, sincronização
  dentro do limite, sem conciliação nem declaração pendente capaz de mudar o resultado) e datas de
  pagamento conhecidas ou confirmadas. Faltando um deles, o estado é `dados_insuficientes` ou
  `dados_desatualizados`, com o dado que falta. Com pendência de conciliação, o veredito só sai se
  sobreviver ao `delta_se_confirmar` aplicado no sentido que o enfraquece; senão, o estado é
  `dados_desatualizados` e a Piggy pede a conferência. A falta **só** da
  estimativa variável não impede `risco_identificado`, porque ela apenas pioraria o caixa.
  Resposta: "não recomendo nesta condição", com data e valor.
- `cabe_nas_premissas` fica **desligado** até a estimativa variável ser validada (Etapa 3). Quando
  ligado: "pelos dados informados, cabe mantendo a reserva de R$ X; confira Y".
- Se a situação já está ruim sem a compra, explicar separadamente o estado atual e o impacto
  incremental.

Avaliar **caixa nas datas de pagamento** e **custo total do contrato** como dimensões diferentes.
Parcela menor pode custar mais; à vista pode custar menos e quebrar o caixa. "Quantas vezes?"
exige varrer somente as opções realmente oferecidas, com o mesmo motor e um teto operacional.
Não presumir "sem juros" se a oferta só mostra preço e número de parcelas.

**O contrato atual do simulador não representa uma oferta real e precisa ser estendido** antes de
receber ofertas do chat ou do print:

- **Parcela cotada.** Hoje `Cenario` só aceita `preco`, `parcelas` e `juros_mensal_pct` e calcula as
  parcelas ele mesmo. "À vista R$ 1.000 ou 12× R$ 100" não cabe: `preco=1200` com taxa 0 reporta
  juros zero, e `preco=1000` não reproduz as parcelas. O cenário passa a aceitar o cronograma
  cotado (valor de cada parcela), exclusivo com `juros_mensal_pct`. O custo em relação ao à vista
  e a taxa implícita são calculados a partir dele. Sem valor de parcela nem taxa, o estado é
  `dados_insuficientes`, nunca taxa zero.
- **Data da primeira parcela.** Hoje ela é sempre um mês após a compra. O cenário passa a aceitar a
  data informada; sem ela, a hipótese fica marcada (item 2 das falhas).
- **Mais de três opções numa leitura.** `Simulacao.cenarios` tem `max_length=3`. Uma varredura de
  1 a 12 vezes não cabe, e dividi-la em várias chamadas de `simulate()` compararia as opções
  contra leituras diferentes do saldo. O limite sobe até o teto operacional da varredura, numa
  única chamada de `simulate()`, que já lê saldo e eventos uma vez só para todos os cenários.

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
| 2. Parcelas e horizonte | Contrato do simulador estendido (parcela cotada, data da primeira parcela, mais de três opções numa leitura); varredura das opções oferecidas; horizonte até a última parcela ou o teto | Casos de compra no fechamento, salário antes/depois da parcela, parcelas além de 90 dias, oferta com parcela cotada sem taxa, oferta com mais de três opções |
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
  incidente medido.
- **Regressão (inventário, não escopo de um PR só):** gastos variáveis ausentes e duplicados,
  compra no fechamento do cartão, salário antes/depois da parcela, fatura vencida, parcelas além
  de 90 dias, reserva já violada, banco indisponível, oferta sem CET, OCR errado, legenda ignorada,
  pendência de lançamento, conciliação ou declaração do Open Finance pendente, saldo alterado
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

