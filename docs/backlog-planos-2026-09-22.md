# Backlog dos planos PigBank

Derivado da auditoria de 22/09/2026 do planejamento v1.0. Este documento separa entregas de código de decisões de produto; não aprova preços, cotas indefinidas ou datas de lançamento. Atualizar o estado dos PRs antes de iniciar uma frente.

## Entregas da rodada atual

| Frente | Escopo | Condição de conclusão |
|---|---|---|
| Permissões | Essencial: IA de categoria, recorrentes e orçamento básico coerentes; Plus: previsão 30d, resumo semanal automático, Insights e comparações; Pro: 60/90d e trajetória | Matriz aplicada em API, chat, interface e agendadores; testes de planos/downgrade/isolamento; PR revisado |
| PR #496 | Simulador Pro de compra à vista, parcelada ou financiada | Conflitos resolvidos, aviso de saldo incompleto, validação e revisão; atualização do PR, sem merge nesta rodada |
| Backlog | Próximas entregas com dependências e critérios | Documento versionado, sem criar issues ou assumir decisões comerciais pendentes |

## Regras para as próximas entregas

- Reutilizar `plan_service`, `cashflow`, `cashflow_forecast`, recorrentes, agentes, metas e conciliação. Não duplicar motores financeiros.
- Autorizar usuário e plano antes de ler dados financeiros ou chamar IA. O usuário de uma tool vem da sessão/conversa.
- Calcular números em código; a IA explica dados fornecidos e identifica a origem das conclusões.
- Separar valores confirmados, estimativas e simulações; mostrar período e atualização quando afetarem interpretação.
- Testar caminhos permitidos e negados, usuário alheio, dados incompletos, correções tardias e duplicidade.
- Não publicar benefício antes da jornada disponível no canal anunciado e de validação operacional.

## Sequência recomendada

**P0:** concluir a rodada de permissões e a validação operacional; integrar assinaturas; tornar alertas, resumo semanal e Insights coerentes e úteis.

**P1:** comparações consistentes, previsão de recorrências, regras revisáveis, interface de fluxo de caixa e planejamento de metas/compras. O simulador #496 pode ser integrado após revisão, em paralelo ao aprofundamento do Plus.

**P2:** patrimônio líquido, fechamento recalculável, radar e relatório executivo, apoiados em dados conciliados.

As prioridades abaixo são uma proposta executável baseada no PDF, sem prazo contratado. Uma frente só começa quando suas decisões e dependências estiverem resolvidas.

## PRs propostos

### PL-01 — Validar a operação dos recursos anunciados

- **Prioridade/plano:** P0; todos. Itens 10, 17, 18, 24, 27 e 30.
- **Dependências:** ambientes piloto e configurações legítimas dos serviços, sem expor credenciais.
- **Reutilizar:** rotinas WhatsApp, templates, sync Pluggy, preferências, logs e documentação de validação manual existentes.
- **Aceite:** verificar envio real de lembrete e resumo com consentimento/configuração; observar sync e atualização de saldo; conferir conciliação e desfazer; registrar comportamento de falha e última atualização; repetir com contas de teste Essencial/Plus/Pro. Registrar explicitamente o que depende de aparelho.
- **Entrega sugerida:** evidências e roteiro operacional versionados. Se houver defeitos de código, abrir PR separado por causa; não misturar credenciais ou dados pessoais nas evidências.

### PL-02 — Unificar detecção e central de assinaturas

- **Prioridade/plano:** P0; Plus/Pro. Itens 19 e 20.
- **Dependências:** decidir critérios de assinatura versus parcelamento e transferência recorrente.
- **Reutilizar:** Detetive, sugestões de recorrentes, confirmação/descarte existentes e central de gastos fixos.
- **Aceite:** candidato tem origem e cobranças que justificam detecção; usuário confirma ou descarta persistentemente; descarte impede a mesma sugestão reaparecer sem novo motivo; mostrar serviço, frequência, próximo pagamento estimado e custo mensal/anual equivalente; cobrança anual é convertida corretamente; não criar débito automático adicional ao confirmar cobrança já importada.
- **Divisão:** PR A, modelo/estados e unificação dos detectores; PR B, interface e integração com eventos. Ambos precisam de isolamento e teste de reentrega sem duplicidade.

### PL-03 — Alertar mudanças de preço de assinatura

- **Prioridade/plano:** P0/P1, após PL-02; Plus/Pro. Item 21.
- **Reutilizar:** detecção de reajuste manual/OF e preferências de notificações.
- **Aceite:** comparar cobranças compatíveis da assinatura confirmada; mostrar preço anterior, novo, datas e origem; normalizar frequência; integrar resultado ao caminho normal de Insights, não apenas ao fallback; não afirmar causa como câmbio ou fim de desconto sem confirmação; respeitar canal/preferências e deduplicar alerta.
- **Divisão:** um PR de integração de detector, apresentação e envio com testes de idempotência.

### PL-04 — Tornar alertas de anomalia explicáveis e configuráveis

- **Prioridade/plano:** P0; Plus/Pro. Itens 22 e 23.
- **Reutilizar:** Xerife, detector por categoria, eventos e preferências por agente.
- **Aceite:** comparar períodos equivalentes; exigir amostra suficiente e sinalizar histórico incompleto; expor referência e diferença; permitir marcar gasto como esperado; sensibilidade editável e aplicada no backend; configuração persiste; o mesmo evento não gera notificações repetidas; manter lembrete simples disponível no Essencial.
- **Divisão:** PR A, comparação/histórico e estado esperado; PR B, controles e canais. Medir falsos positivos no piloto antes de mudar defaults.

### PL-05 — Completar o resumo semanal

- **Prioridade/plano:** P0; Plus/Pro. Item 24.
- **Reutilizar:** `build_weekly_report_summary`, agendamento atual e claims por período.
- **Aceite:** mostrar datas, atualização, receitas, despesas, resultado, maior categoria e variação contra semana anterior equivalente; transferências/faturas conciliadas não duplicam despesas; distinguir resultado de saldo livre; opção de ativação/horário; decidir se segunda-feira fixa atende à oferta ou adicionar dia configurável; downgrade bloqueia envio e não consome claim indevidamente.
- **Divisão:** um PR para payload/cálculos e templates de apresentação; homologação de template externo registrada separadamente.

### PL-06 — Construir o produto PigBank Insights sobre os componentes atuais

- **Prioridade/plano:** P0; Plus/Pro. Itens 31 e 32; depende de PL-02/04/05 conforme os tipos integrados.
- **Reutilizar:** feed do dashboard, `generate_ai_insights`, padrões, cache e eventos de agentes.
- **Aceite:** cada insight traz período, fonte, referência e momento de cálculo; assinaturas, vencimentos, desvios, evolução positiva e projeções têm entradas tipadas; processamento recorrente não repete notificações; dispensar/considerar útil persiste por usuário; Plus explica o acontecimento, Pro adiciona impactos e ações ligadas aos números; fallback conserva significado e limites de plano; cache não contorna downgrade.
- **Divisão:** PR A, contrato/dados e persistência mínima; PR B, processamento/integrações; PR C, experiência Plus/Pro. Evitar novo motor paralelo ao feed já existente.

### PL-07 — Comparações financeiras com períodos equivalentes

- **Prioridade/plano:** P1; Plus/Pro. Itens 25 e 26.
- **Reutilizar:** agregações de analytics e tool `compare_periods`.
- **Aceite:** mês fechado compara com mês-calendário anterior; mês aberto compara períodos equivalentes e informa a escolha; fevereiro, ano bissexto e virada de ano têm cobertura; delta por categoria inclui categorias novas/sem gasto sem dividir por zero; período e origem visíveis; totais básicos e distribuição atual continuam no Essencial.
- **Divisão:** um PR de contrato/algoritmo e consumidores; agrupar backend/chat/interface para não publicar interpretações divergentes.

### PL-08 — Previsão de contas recorrentes com cobertura explícita

- **Prioridade/plano:** P1; Plus/Pro. Item 28; apoia 27 e 36.
- **Reutilizar:** gerador único de eventos de `cashflow`; não criar um calendário paralelo ao cobrador.
- **Aceite:** frequências suportadas pelo produto têm semântica coerente entre cobrança e previsão; mensal/anual/diária/semanal/única são cobertas ou explicitamente identificadas como não previstas; respeitar início, pausa e vencimento; integrar assinaturas confirmadas sem duplicar recorrentes/OF; Plus termina em 30 dias, Pro em 90; informar premissas e dados faltantes.
- **Divisão:** PR A, contrato de recorrência compartilhado; PR B, integração e transparência na previsão. Provar ausência de dupla contagem contra dados conciliados.

### PL-09 — Regras automáticas revisáveis

- **Prioridade/plano:** P1; Plus/Pro. Item 29.
- **Decisão anterior ao código:** limites por plano, operações permitidas e alcance do desfazer.
- **Reutilizar:** regras palavra→categoria e resolução existentes. Preservar correção/aprendizado básico do Essencial.
- **Aceite:** usuário entende condição/ação, cria, edita, pausa e remove; visualiza por que a regra foi aplicada; simulação prévia mostra lançamentos atingidos; desfazer altera somente aplicações ainda atribuíveis àquela regra, sem sobrescrever correções posteriores; cotas idênticas nos canais; não reclassificar retroativamente sem ação explícita.
- **Divisão:** PR A, contrato e rastreio; PR B, interface e edição; PR C, aplicação retroativa/desfazer, se aprovado.

### PL-10 — Expor a análise de fluxo de caixa já calculada

- **Prioridade/plano:** P1; Pro. Item 36.
- **Dependências:** motor #445 integrado; compatibilidade com seleção de horizonte do PR de permissões e simulador #496.
- **Reutilizar:** `forecast_with_trajectory`, `worst_day`, causas e `threshold`.
- **Aceite:** tela e chat apresentam trajetória/pior dia e compromissos; limite de segurança editável e enviado ao motor; saldo positivo abaixo da reserva é mostrado como insuficiente; premissas/atualização/origem do saldo visíveis; dados indisponíveis têm aviso; gráfico acessível com alternativa textual; mobile sem overflow; Plus não recebe payload detalhado Pro.
- **Divisão:** um PR de interface + resposta de chat, sem reescrever o cálculo.

### PL-11 — Planejar metas e grandes compras com capacidade real

- **Prioridade/plano:** P1; Pro. Itens 34 e 35.
- **Dependências:** PL-07/08/10 conforme as fontes de capacidade; simulador #496 para impacto da compra.
- **Reutilizar:** metas/caixinhas, cálculo de aporte, orçamento e simulator; o plano de acumular dinheiro é distinto da simulação da compra.
- **Aceite:** objetivo tem valor, saldo inicial e prazo; aporte total necessário é calculado deterministicamente; comparar com capacidade disponível e compromissos; sugestão de corte não reduz artificialmente o aporte exigido; usuário revisa prazo/valor/aporte; acompanhamento mostra realizado versus planejado; IA explica cenário sem inventar rentabilidade ou prometer resultado.
- **Divisão:** PR A, cálculo de capacidade e plano; PR B, revisão/acompanhamento e conversa. Cada etapa deve funcionar sem depender de recomendação não confirmada da IA.

### PL-12 — Patrimônio líquido, com ativos e passivos

- **Prioridade/plano:** P2; Pro. Item 38.
- **Dependências:** decidir escopo de dívidas, bens e método de avaliação; conciliação consistente.
- **Reutilizar:** saldo consolidado, investimentos, caixinhas e cartões. Carteira visual do PR #514 é uma entrada de apresentação, não cálculo de patrimônio líquido.
- **Aceite:** ativos menos passivos; bens com valor/data/origem de avaliação; dívidas não desaparecem ao pagar apenas uma parcela; caixinhas/OF/investimentos não contam duas vezes; moeda e atualização explícitas; valores sem fonte atual sinalizados; composição auditável e isolada por usuário.
- **Divisão:** PR A, contrato e cálculo; PR B, cadastro de bens/passivos não importados; PR C, visualização/histórico se necessário ao produto.

### PL-13 — Fechamento mensal recalculável e explicado

- **Prioridade/plano:** P2; Pro. Item 39.
- **Dependências:** conciliação, classificação e comparações consistentes.
- **Reutilizar:** relatório mensal e Repórter.
- **Aceite:** fixar mês-calendário; receita−despesa é resultado e aportes aparecem separados; transferências/faturas não duplicam gasto; correção tardia produz revisão do fechamento e identifica atualização; texto referencia os fatores numéricos calculados; dados insuficientes são explícitos; geração/envio são idempotentes por versão e não ficam presos ao primeiro evento do mês.
- **Divisão:** PR A, cálculo e revisões; PR B, narrativa e apresentação. Não reutilizar cegamente evento imutável do agente como documento de fechamento.

### PL-14 — Radar financeiro explicável

- **Prioridade/plano:** P2; Pro. Item 37.
- **Decisão anterior ao código:** fórmula, pesos, faixas, histórico mínimo e tratamento de dimensão ausente.
- **Dependências:** PL-12/13 e métricas confiáveis conforme dimensões aprovadas.
- **Aceite:** dimensões liquidez/gastos/reserva/dívida/consistência/poupança/evolução documentadas; cálculo determinístico e versionado; mostrar fatores que alteraram a nota; ausência de dados não vira nota arbitrária; histórico comparável entre versões; identificar indicador de organização financeira, sem apresentá-lo como score de crédito.
- **Divisão:** PR A, especificação e fixtures aprovadas; PR B, cálculo e interface. Não implementar pesos inventados pelo desenvolvedor.

### PL-15 — Relatório executivo fundamentado

- **Prioridade/plano:** P2; Pro. Item 40.
- **Dependências:** fechamento PL-13; fluxo de caixa PL-10; radar PL-14 apenas se usado como fonte.
- **Aceite:** diagnóstico, riscos, evolução e ações propostas para próximo mês; toda conclusão aponta a métrica/período correspondente; nenhuma aritmética delegada ao modelo; correção de dados invalida versão anterior; diferenciar observação de sugestão; usuário acessa/exporta resultado sem receber números de outro usuário.
- **Divisão:** um PR para contrato/narrativa e apresentação, reaproveitando exportação existente quando apropriado.

### PL-16 — Agenda de contas a receber

- **Prioridade/plano:** P1 de base, antecipar se impedir pilotos; Essencial e superiores. Item 08.
- **Reutilizar:** receitas e convenções de vencimento/baixa das contas a pagar, sem tratá-las como despesa invertida em todos os fluxos.
- **Aceite:** recebível avulso com valor, descrição, vencimento e estado; recebimento manual não duplica crédito recorrente/importado; editar/cancelar e desfazer baixa preservam saldo; atrasado continua pendente; previsão identifica estimativa de entrada e não saldo confirmado; comandos e UI respeitam o mesmo contrato.
- **Divisão:** PR A, modelo/baixa/idempotência; PR B, interface/chat/previsão.

### PL-17 — Interface própria do simulador, se fizer parte da oferta

- **Prioridade/plano:** P1 opcional por canal; Pro. Item 33.
- **Dependência:** PR #496 integrado e experiência de chat validada; definir necessidade de tela separada.
- **Aceite:** 1–3 cenários, condições fornecidas pelo usuário, entrada/juros/custos explícitos; comparar liquidez e custo integral; distinguir parcelas fora dos 90 dias; reserva configurável e pior saldo considerando hoje após a compra até o dia 90 inclusive (91 datas); aviso de saldo incompleto; nenhum vencedor automático; mesma validação e motor da API; uso móvel acessível.
- **Divisão:** um PR de UI consumindo `/simulator`, sem novo motor ou persistência automática das simulações.

## Decisões que continuam abertas

| Decisão | Estado atual / recomendação para discussão | Bloqueia |
|---|---|---|
| Conexões OF | Código 1/2/5; PDF propõe 1/3/mais e fala em instituições. Decidir unidade e cotas antes de alterar cobrança/oferta | Mudança das cotas do item 16 |
| Regras | Quantidades, ações e reversão ainda sem contrato | PL-09 |
| Agentes | Plus usa energia 4 e Pro 14; não prometer qualquer trio no Plus sem alterar limite | Ajuste de catálogo/copy |
| Orçamento doméstico | Continua Plus; orçamento mensal por categoria cobre o básico. Confirmar se método dos potes deve ser Essencial | Eventual ampliação de acesso |
| Alertas | Confirmar canais, sensibilidade padrão, limites e política de repetição | PL-03/04/06 |
| Resumo semanal | Dia fixo ou escolha; disponibilidade de template homologado | PL-05 |
| Radar | Fórmula, pesos, faixas e dados mínimos | PL-14 |
| Patrimônio | Tipos de bens/passivos e avaliação | PL-12 |
| Política de transição | Comunicar a matriz de acesso e tratar contratos legados antes do deploy que restringir capacidades | Publicação de mudanças restritivas |
| Premium | Espaços financeiros e consolidação continuam futuros; há fundação interna, sem produto lançado | Nova etapa após consolidação dos planos atuais |

## Evidência de avanço

Cada PR deve registrar base e diff revisados, critérios atendidos, testes com resultado local/CI separado e validação operacional pendente. Para os pilotos, medir uso, utilidade dos Insights, assinaturas confirmadas, falsos alertas e compreensão de projeções; definir metas quantitativas depois de obter a linha de base. Não usar contagem de arquivos ou PRs como conclusão de uma funcionalidade.

Referências: planejamento v1.0 fornecido pelo usuário; auditoria `output/auditoria-planos-2026-09-22.md` no workspace original; [PR #496](https://github.com/JapaLcK/Bot_Financeiro/pull/496), [#445](https://github.com/JapaLcK/Bot_Financeiro/pull/445), [#514](https://github.com/JapaLcK/Bot_Financeiro/pull/514), [#470](https://github.com/JapaLcK/Bot_Financeiro/pull/470) e [#481](https://github.com/JapaLcK/Bot_Financeiro/pull/481). O estado remoto dos PRs pode mudar; confira antes de iniciar.
