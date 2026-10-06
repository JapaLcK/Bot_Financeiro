# Etapa 3 — PR1: motor único de previsão com qualidade

**Execução autorizada pelo dono em 05/10/2026**: “Então pode começar a etapa3”, depois do plano de três PRs e um quarto condicionado. Este documento torna executável o escopo aprovado, com entrega conservadora onde não há dado ou regra aprovada. Não reabre aprovação geral. Faixa **Completo**: Arquiteto → Coder → Codex local → Tester → Manager. Commit em PTBR; commit/push/produção/reset continuam fora da autorização deste documento.

Base conferida: `dc1d0009075f3688dc4ea80ca02da80c7f073a72`, worktree `etapa3-previsao-plano`, branch atual `Japa/etapa3-pr1-motor-previsao`. Plano e inventário da Etapa 3 continuam referências de alcance. Os avisos antigos de “planejamento não autorizado” desses documentos descrevem a fase anterior ao pedido acima.

## Objetivo

Substituir a leitura/cálculo de saldo previsto por uma única snapshot e um único motor Decimal, compartilhados por previsão legada, IA e simulador, deixando explícitos valor desconhecido, origem, realização e incompletude. Preparar saída tipada para a API v2 do PR2 sem criar a rota ou a tela neste PR1.

## Decisões vigentes e escolhas técnicas desta entrega

| Assunto | Execução no PR1 |
| --- | --- |
| Q36/Q37 | Carteira é dinheiro físico; carteira nova começa em zero e aceita seus movimentos. Não há confirmação Q37, programa de revisão, reset automático nem gate novo de coorte. A allowlist v2 não prova ausência de histórico. Qualidade da carteira deriva do estado/proveniência existente, conforme abaixo. |
| Q42 | Recorrência prevê e avisa. Consulta não cria lançamento, instância, compra, fatura, pagamento ou crédito de salário. Não ressuscitar `last_charged_ym`/`last_credited_ym` como prova de realização. |
| Q18 | Troca de regra e remoção do cálculo substituído são atômicas neste PR, incluindo WhatsApp/IA, rotas antigas e simulador. Adaptadores traduzem formato; não calculam outro conjunto de obrigações. |
| Permissões | Fonte existente `plan_service`: forecast Plus30/Pro30–60–90; cashflow e simulador Pro. Plus não recebe trajetória/pior dia/causas ocultos. Política v1 existente preservada. Gates antes de carregar dados financeiros. |
| Janela de conferência | **Nenhum número novo de dias.** Não escolher 7/30/60, nem reconstruir recorrências desde o início como dívida acumulada. Usar ocorrências explícitas existentes e hoje/futuro; passado recorrente sem evidência vira motivo de realização desconhecida, sem debitá-lo novamente. |
| Receita | Mensal/anual continuam frequências suportadas pelo cadastro; demais receitas legadas ficam visíveis como insuficientes. Projetar calendário e valor válidos como receita prevista, sem promovê-la a garantida. Ausência de fim persiste como premissa, sem coluna ou garantia nova. |
| Calendário | Data contratual específica do ciclo conhecida prevalece; calendário civil existente continua como hipótese identificada quando só há dias configurados. Defaults de ingestão não viram data conhecida. Não deslocar automaticamente por feriado/dia útil nem adicionar provedor. |
| Variável/orientação | Sem estimativa variável compartilhada neste PR. `cabe_nas_premissas` continua desligado. Números condicionais continuam úteis; “a conferir” não é erro geral nem recusa de todos os cálculos. Risco só pode ser afirmado quando sobreviver à direção que o enfraquece, conforme matriz da Piggy. |

Não há pergunta de produto indispensável para iniciar essa entrega conservadora. Método variável, confirmação persistente por ocorrência, fim/garantia da renda, janela rolante e dias úteis só voltam ao dono quando uma entrega quiser **transformar a incerteza em regra**. Não inventar esses valores no Coder.

## Estado verificável da carteira, sem metadado de coorte

O schema tem `accounts.balance`, `users.created_at`, `launches.origem`, `efeitos.delta_conta` e vínculos de espécie/fusão. Não existe certificado de “nunca utilizou”; `created_at` ou acesso ao painel não o substituem. A exceção não recebe o nome técnico de “coorte comprovada”. Ela reconhece composição utilizável da carteira no snapshot atual:

1. Carteira inicial de saldo finito zero, sem movimentos financeiros que tornem sua composição desconhecida, representa o estado inicial aprovado. Linha ausente só pode representar esse estado se as fontes financeiras do usuário também estiverem vazias; linha ausente com histórico gera motivo, não carteira confiável zero.
2. Com movimentos, conferir os efeitos persistidos de carteira: `origem='carteira'`, delta monetário finito e forma suportada pelos escritores existentes; vínculo de espécie válido quando aplicável. A marca identifica escritor, não prova sozinha que o dinheiro era físico: `funding_source.kind='bank'`, origem/caminho legado ambíguo ou contradição do efeito preservam motivo. Reutilizar classificação de carteira/banco de `db/lancamentos.py`/`db/bank_movements.py` e validação de efeitos existente (`db/accounts.py::_validar_efeitos`). **`PODE_SQL` é permissão de edição, não prova de confiança**; não usá-lo como booleano de confiabilidade.
3. Conferir composição do saldo bruto por agregação dos deltas suportados, em SQL quando seguro. Saldo residual, origem antiga com efeito de caixa, efeito ilegível/ausente/não finito, valor que exigiria arredondamento ou movimento não classificável mantêm `carteira_nao_confirmada`. Não lançar ajuste nem trocar saldo por soma reconstruída. Agregado serve apenas à qualidade; o saldo observado continua o valor da base.
4. Aplicar `merged_wallet_delta` uma vez, como já fazem Contas hoje/foto. Pendências, espécie incompleta e bancos continuam motivos independentes. Não confundir lançamentos OF com delta zero com caixa legado nem converter pagamentos de fatura em prova de carteira física.

Extrair **um** leitor puro de qualidade da carteira, usado por `contas_hoje.listar`, `patrimonio.calcular` e snapshot da previsão, substituindo os dois `True` fixos. Não persistir classificação, não backfillar `origem`, não apagar fotos antigas nem regravá-las. Novo estado com dinheiro físico não carrega o motivo apenas por inexistir Q37; legado incerto mantém motivo mesmo que habilitado na v2. NaN/JSON de forma errada não causa cast SQL/500: classificar antes de agregar. Não estender para uma reescrita do livro contábil: se efeito não tem prova existente suficiente, preservar incerteza.

## Contrato interno e uma snapshot

Usar o seam existente `cashflow`/`cashflow_forecast`. Um leitor de snapshot com cursor recebido reúne base, ocorrências e qualidade; uma função pública abre `repeatable read, read only`, obtém data-base/instante uma vez e encerra sem escrita. O padrão já existe em `api/v2/contas.py`. Todos os cenários de uma simulação usam essa mesma snapshot. Gate de plano continua no chamador, antes da leitura.

Contrato interno mínimo, com dataclass/tipos conforme convenção atual, sem framework novo:

```text
Snapshot: hoje, calculado_em, base, ocorrencias, motivos, premissas, cobertura
Base: saldo Decimal|None, origem, of_bank_count, banks_excluded, motivos
Ocorrencia: chave=(fonte,id,ciclo), fonte, origem_id, ciclo,
             data date|None, direcao entrada|saida, valor Decimal|None,
             qualidade_valor conhecido|estimado|desconhecido,
             qualidade_data conhecida|presumida|desconhecida,
             realizacao prevista|realizada|a_conferir, nome, motivos
Motivo: codigo, direção_do_erro só_melhora|só_piora|ambos,
        origem_id/ciclo opcionais, efeito_quantificado Decimal|None
Resultado: numeros condicionais, estado condicional|a_conferir|indisponivel,
           motivos, premissas, cobertura
```

Ids internos não autorizam expor raw/provedor ou dados de outro usuário na API futura. Homônimos não são a mesma identidade. Direção do erro é definida em relação ao caixa nominal; motivo sem efeito quantificável não vira zero nem é compensado por outro motivo. Base sem valor utilizável → saldo/curva `None`, estado indisponível. Uma parcela desconhecida não apaga os compromissos conhecidos: preserva lista/condicional com motivo.

Cada leitor participa da mesma transação. Não utilizar getters que chamam `ensure_user`, `sync_manual_bills_once`, reabertura/rebuild de fatura ou delete de TTL. Query filtra dono em toda perna e join. Leituras locais sem teto silencioso; futuro além do horizonte filtrado por data. Histórico explícito necessário pode ser agregado como cobertura, sem gerar milhares de ocorrências antigas.

## Fontes, realização e deduplicação

| Fonte | Regra executável |
| --- | --- |
| Saldo | Reutilizar recorte/qualidade de Contas hoje (`CONTAS_BANCO_SQL`, depois moeda/pausa, 48h, raw saldo, geração), carteira corrigida pela fusão. Não voltar ao consolidado bruto nem somar limite de cartão/posições/caixinhas. Gate legado de consolidado continua explicitamente refletido em origem/bancos excluídos; v2 futura usará a base canônica de Contas. Mesma política declarada de origem deve dar a mesma base entre chamadores. |
| Receita | Ativa, frequência monthly/annual, valor/data/start válidos: gerar hoje/futuro com identidade por ciclo. Sem confiança garantida: valor entra como previsto na curva nominal, nunca numa alegação de compra segura. Frequência/dia/mês inválidos e start inválido não viram monthly/dia1. Cadastro não muda. |
| Gasto recorrente account | Gerar as cinco frequências existentes, manual e autopay. `variable_amount` marca estimativa; zero variável é desconhecido, não evento exato0. Valor negativo/inválido não vira entrada. Instância com mesmo `recurring_id+due_date` representa essa ocorrência uma vez; sua alteração válida por ciclo prevalece, sem atualizar banco ao ler. |
| Instância | Avulsa por id próprio. Recorrente pela identidade persistida e data do ciclo. Ler pending e realizadas relevantes; não excluir `paid` antes de decidir se o saldo já contém a baixa. Instância desativada/troca de modo/alteração sem vínculo de ciclo inequívoco fica a conferir, sem forçar outra saída. Não fundir por nome/valor próximo. |
| Hoje/atrasados | Data passada nunca prova pagamento. Instâncias/faturas explicitamente pendentes podem compor curva **sob a hipótese de ainda não quitadas**, com realização a conferir; só debitar uma vez. Para recorrentes passados sem realização, carregar motivo/premissa sobre o passado, sem fabricar débito acumulado. Hoje é explícito: incluir obrigação conhecida ainda não realizada; se realização desconhecida, mesma hipótese e direção. |
| Realização | `launch_id` e delta efetivamente aplicado à carteira constituem evidência local quando vínculo/valor/dono fecham. Para banco, status local paid não prova saldo debitado; declaração/vínculo observado e data de sync entram na qualidade. Sem prova de reflexo no saldo, não acrescentar pagamento separado além da dívida. Antecipação comprovada retira a mesma ocorrência futura. |
| Gasto recorrente credit_card | **Nunca** debitar diretamente no due_day do recorrente. Cartão/ciclo conhecido explica a obrigação na fatura. Se a incorporação à fatura tem vínculo comprovado, representa-se uma vez pela fatura. Não existe FK geral recurring→credit_transaction nesta base: sem prova, listar recorrente a conferir e fatura observada, **sem somar valor recorrente ao agregado nem subtrair outra saída**. Calendário/incorporação/cobertura ausentes impedem conclusões afetadas. Não procurar cartão por primeira linha ou compra por label. |
| Fatura | Leitor puro retorna id/card_id, ciclo, total, paid_amount, estado, restante, calendário, origem/moeda e qualidade. Não confundir `total` da tool próxima fatura com restante do fluxo. Estado paid com dívida/total incoerente é motivo; não repará-lo ao consultar. Fatura líquida negativa é crédito a conferir, sem receita de caixa automática. Parcela OF futura não materializada permanece cobertura incompleta; não projetar grupos heurísticos como se exatos. |
| Calendário OF | `open_finance_accounts.raw.creditData.balanceCloseDate/balanceDueDate` fornecem evidência do ciclo que identificarem. Não aplicar data raw de um ciclo a todas as faturas antigas/futuras. `card_bill_due_date` continua calendário civil canônico; valor vindo de defaults1/10 ou sem proveniência é presumido, sem automatizar dia útil. |
| Conciliação/declarações | Ler detalhes com cursor e efeito direcional item a item. Dois sinais opostos não se anulam para declarar confiança. Reflexo no saldo desconhecido permanece motivo. |
| Pendência de conversa | Ler `pending_actions`/`ai_pending_actions` ativas com TTL existente calculado pelo instante da snapshot, sem apagar vencidas. Classificar pela operação/payload do escritor: valor/forma/mídia/lançamento/pagamento/correção de dinheiro contam; conversa/categoria sem impacto não contam. Registro `oferta/suprime_ia/sobrevive_audio` não é classificação financeira. Tipo não reconhecido não ganha efeito0. Enumerar escritores antes de implementar. |
| Ingestão parcial | Leitura local completa não prova paginação bancária completa. Ausência de evidência de cursor terminal/default observado permanece `cobertura_nao_comprovada` no escopo aplicável. Não reparar toda Pluggy neste PR; não anunciar fonte completa só porque o sync diz updated. |

O motor nominal e os casos direcionais usam **a mesma função pura**. Para risco, remover saídas duvidosas que poderiam já estar pagas/duplicadas e incluir receitas incertas conhecidas que enfraquecem o risco. Com valor/data/base desconhecidos capazes de melhorar caixa sem limite conhecido, abster. Falta apenas de saídas futuras pode bloquear “cabe” sem impedir risco sustentado nos dados conhecidos. Não construir segundo motor otimista/pessimista nem ativar veredito positivo.

## Precisão e compatibilidade numérica

Valores do PostgreSQL ficam Decimal desde a leitura: não atravessar `_row()`/listagens que convertem em float. Dados JSON legados são convertidos pela representação decimal validada, sem corrigir efeitos nem alegar recuperar precisão já perdida. Somar valores completos; quantizar **resultado apresentado** em centavos com `ROUND_HALF_EVEN`, compatível com intenção de `round` atual. Não quantizar coluna, evento bruto ou cada adição. Comparações do limite/final usam o mesmo valor em centavos apresentado; normalizar sinal do zero na fronteira.

Simulador preserva seu contrato de entrada: números estritos, centavos, null conhecido→default, nomes únicos, 1..3 cenários, parcelas1..420, limites/taxa/validação de contradições, reserva default0 e calendário da primeira parcela mês+1. Converter para Decimal na entrada do cálculo. Price continua o mesmo produto financeiro, sem nova taxa, encargo ou amortização: portar somente o trecho numérico necessário, preservando soma em centavos e resíduo na última. Usar precisão local suficiente para taxa minúscula, evitando `(1+r)` arredondado a1; não remover `test_installments_juros_minusculos_batem_com_a_formula_exata`. A API pública `installments` pode manter adaptador numérico legado; cálculo interno/contrato/total ficam Decimal. Toda diferença de centavo contra golden congelado deve ser explicada, nunca aceita por contagem de verdes.

Saída interna permanece Decimal. V2 futura serializa texto; adaptadores antigos convertem para JSON number **só depois** de calcular/quantizar. Não aceitar float como fonte do novo cálculo depois dessa conversão. Nenhum cache/arquivo com valores de produção é criado.

Previsão conserva `(hoje, hoje+N]`, incluindo compromissos já pendentes na base ajustada sob premissa explícita. Simulador conserva `hoje..hoje+90` inclusivo (91 datas), compra hoje e mesma base/ocorrências, com âncora ontem. Não igualar os vetores descartando hoje. Paridade é verificada nas datas comparáveis e no saldo final quando as entradas extras são iguais. Pior dia/último pico/patamar/empate mantêm o algoritmo existente.

## Arquivos e sequência para o Coder

1. **Baseline e DTO**: partir dos nomes/status e referência fixa informados abaixo. Definir DTO interno no seam de `core/services/cashflow.py`; se tamanho exigir arquivo coeso, `core/services/cashflow_snapshot.py` para estrutura/leitura, sem cálculo duplicado. Não registrar API v2 de previsão ainda.
2. **Leitores puros**: `db/contas_hoje.py`, `db/patrimonio.py` e mínimo leitor comum da carteira; `db/recurring.py`, `db/recurring_income.py`, `db/bills.py` com variante cursor/Decimal preservando públicos; `db/cards.py` com leitor puro comum ao motor e próxima fatura. Snapshot coordena cursor, inclusive conciliação/declarações/pendências. Evitar copiar SQL completo quando seam parametrizado resolve.
3. **Motor**: `cashflow.py` base/eventos/projection e `cashflow_forecast.py` marcos/trajetória/pior dia recebem DTO; validação do calendário existente antes do helper; qualidade acompanha todas as saídas. Remover tuplas sem identidade/float substituídas. `recurring_charger.py` segue Q42; só tocar calendário comum se houver necessidade comprovada e testes do aviso.
4. **Simulador**: `decision_simulator.py` muda aritmética/eventos para Decimal e mesma snapshot; reserva/calendário/420/contrato inteiro permanecem. Extra cenário recebe identidade própria e não entra em leitura persistida.
5. **Consumidores Q18**: monólito nas rotas `/forecast` e `/recurring-bills/.../projection`; `frontend/routes/simulator.py`; tools `bills.py`, `cards.py`, `simulator.py`; texto aplicável em `system_prompt.py`; render/aviso da previsão em `frontend/dashboard.js`. Todos traduzem saída do novo motor. Ajustes de texto/aviso não criam tela nova. Sem alteração de service-worker salvo necessidade real; se houver, cumprir bump/par obrigatório.
6. **Validação da cadeia**: testes de isolamento/read-only/compatibilidade/qualidade e conversa; Codex local antes do Tester independente; Manager verifica plano/diff/achados. API v2, geração de modelos/fixtures/TS, tela e refetch temporal ficam PR2/PR3.

Q18 inclui `forecast_balance`, `check_cashflow` e simulador no PR1. `forecast_next_bill` consome leitor puro e informa ausência/desconhecido, preservando `total` da fatura. `forecast_month_end` mantém metodologia distinta de fluxo mensal parcial; não usar como saldo livre nem alimentar a Previsão. Simulador de hábitos do protótipo continua demonstrativo até a troca da tela no PR3.

Preservar campos numéricos legados e adicionar `estado/motivos/premissas/cobertura`. `tranquilo` continua booleano compatível de **saldo condicional no alvo >=0**, sem significado de percurso/segurança. Atualizar note/prompt/render que o usam: exibir saldo previsto e motivo; não cor verde/copy de segurança com qualidade insuficiente. Expor mínimo do percurso onde cashflow é autorizado; Plus não recebe mínimo/causas por campo novo. IA é instruída a nunca usar sozinho esse booleano para recomendar compra. Com base indisponível, campos monetários dependentes podem ser null e clientes antigos devem renderizar indisponível, não NaN/0.

`check_cashflow.amount` informado ilegível/não finito → erro compartilhado, nunca0. Ausente mantém “sem cenário extra”. `threshold` negativo existente permanece comparação matemática permitida. Extra negativo legado permanece ajuste assinado condicional explicitamente descrito; não chamar entrada de “boleto novo”, nem ampliar orientação. Alvo passado legado não reconstrói saldo histórico: marcar cálculo inadequado/indisponível para previsão histórica e orientar data atual/futura; testar envelope em ambos canais. Não ampliar contratos válidos do simulador.

Pendências financeiras passam a alterar qualidade sem escrita de saldo. Mapear seus escritores; avisar SSE depois do commit nas alterações relevantes com mecanismo existente (ou extensão justificada da cadeia), sem evento contendo payload financeiro. TTL/virada de dia/frescor também vencem sem escrita: incluir `calculado_em` e próxima fronteira de validade necessária ao PR2/PR3; não adicionar polling backend. Consulta direta sempre recompõe snapshot.

## O que não muda e divisão do PR

Sem schema/metadado novo para coorte, confiança/fim de receita ou confirmação. Sem reset/backfill/produção, contratação/pagamento, conserto integral da ingestão, estimativa variável, calendário de feriados, reconstrução de saldo passado ou regra local do protótipo. Fotos históricas preservadas. Nenhuma alteração dos PRs de outras sessões.

Um PR financeiro atômico é necessário por Q18. Se os leitores puros e variante Decimal exigirem preparação que inviabilize revisão, pode haver PR preparatório **Completo**, só aditivo, mantendo entradas/saídas e algoritmo vigente idênticos; sem endpoint de dinheiro novo, nova regra/qualidade orientando usuário ou motor ativo paralelo. Troca/removal do cálculo continua no PR1. Não separar “novo web agora, IA depois”. A quantidade de arquivos, sozinha, não justifica outra aprovação geral.

## Critério de pronto e baseline

O orquestrador executou baseline local em 05/10, na base fixa acima: **449 passaram, um warning** em 22 arquivos selecionados. Evidência nominal em `/private/tmp/pigbank-etapa3-pr1/baseline-nomes.json`, log/XML/comando no mesmo diretório; remedir se árvore/ambiente mudar. FastAPI local e requirements:0.141.1, medidos nessa execução. O Arquiteto não executou testes nem banco para escrever este plano. Antes de pytest o Coder/Tester devem ler `.claude/skills/baseline-testes/SKILL.md`; banco descartável local, isolamento ligado, tarefas de fundo desligadas, sem ler segredos em saída. Suíte integral só CI.

Preservar testes existentes pelo comportamento legítimo; ajustar expectativas da regra trocada com referência aprovada, não apagar cobertura. Casos discriminantes existentes:

- `test_trajetoria_e_project_batem_com_fracao_de_centavo`, `test_project_tranquilo_olha_o_valor_em_centavos`, `test_daily_trajectory_threshold_igual_ao_saldo_nao_e_aperto`.
- `test_worst_day_causas_so_depois_do_ultimo_pico`, `test_worst_day_pico_empatado_vale_o_mais_recente`, `test_worst_day_nao_altera_o_item_da_trajetoria`, `test_worst_day_saida_compensada_no_meio_do_patamar_nao_e_causa`.
- `test_atual_bate_com_a_previsao_de_saldo_nas_mesmas_fontes`, `test_uma_leitura_das_fontes_por_simulacao`, `test_receita_amanha_nao_esconde_a_reserva_violada_hoje`, `test_periodo_inclui_hoje_e_os_90_dias_seguintes`.
- `test_installments_price_144k_a_1_49_em_48x`, `test_installments_sem_juros_residuo_na_ultima`, `test_installments_juros_minusculos_batem_com_a_formula_exata`, `test_48x_so_as_parcelas_ate_o_horizonte_e_o_resto_no_contrato`, `test_subcentavos_sao_recusados_no_campo_de_origem`.
- `test_forecast_api_e_tool_nao_vazam_horizontes`, `test_projecao_por_data_e_tool_respeitam_limite`, `test_essencial_nao_calcula_projecao_alternativa`; testes Q42 de `test_recorrente_so_preve.py`.

Adicionar provas por comportamento, incluindo:

| Prova | Resultado exigido |
| --- | --- |
| Snapshot intercalada com sync/pagamento | Resultado inteiro anterior ou posterior; nunca saldo anterior com dívida posterior. Assert de read-only com dados reais e ausência de escrita, inclusive TTL/reabertura/ensure. |
| Carteira | Novo vazio0 e movimentos em espécie conhecidos sem motivo Q37; ledger antigo/efeito inválido/residual preserva saldo e motivo; fusão uma vez; flags iguais em contas/foto/previsão; motivo bancário independente permanece. |
| Ocorrências | Manual sem instância gera futuro; instância+recorrente uma vez; diárias/semanais/anuais; frequência inválida não mensal; zero variável null/estimativa; homônimos distintos. Hoje/atrasada/antecipada sem inferir pago pela data. |
| Cartão | Mesmo fixo não sai no due_day e na fatura; vínculo ausente não gera incorporação inventada; raw data de um ciclo não governa outro; parcial/pago sem sync/negativa/moeda/parcelas OF incompletas ficam explicados. |
| Orientação | Final positivo e percurso negativo não viram promessa “positivo até lá”; risco com apenas saída futura omitida pode sobreviver; saída duvidosa removida desfaz falso risco; base/entrada sem valor que pode melhorar bloqueia risco; cabe desligado. |
| Validação e compat | IA e HTTP recusam inválido/NaN/Inf; ausência extra≠inválido; negativo assinado não dívida; Plus payload sem detalhes Pro; 420 parcelas e taxa pequena válidas; Decimal string na preparação futura, number só adaptador antigo. |
| Conversa | Duas mensagens por `handle_incoming`, DB de teste real: forma/valor/mídia financeira pendente→previsão; previsão→assunto novo; desfazer/corrigir→nova previsão. Cobrir consulta com e sem prefixo Piggy. Nenhuma consulta consome pendência de outro assunto. Se `intent_router`/`resolve_bill_amount` abandonam a pergunta por resposta não numérica, corrigir apenas reconhecimento/encaminhamento da consulta de leitura antes desse abandono; manter abandono legítimo para outros assuntos. Não restringir o teste ao caminho prefixado que já evita esse resolver. |
| Comparação Q18 | Fixture/reference congelada do SHA base, cenários por nome e centavo; classificar deltas esperados por cartão, manual futuro, desconhecido/base, precisão. Delta inesperado bloqueia. Não manter algoritmo velho em produção como oracle. |

Para conserto, Tester inclui controles positivo e negativo pertinentes (mutação só no trecho alterado e fixture antes verde); restaura árvore e prova hash/execução positiva. Unidade pura comum não exige mutação cerimonial. CI e Codex remoto no head atual ainda serão gates antes do merge; baseline não equivale a validação do novo diff.

## Referências primárias relidas

`CLAUDE.md`, `docs/CLAUDE.md`, `.claude/commands/time-dev.md`, `.claude/agents/arquiteto.md`, `.claude/skills/baseline-testes/SKILL.md`; `docs/plano-etapa3-previsao.md`, `docs/etapa3-previsao-inventario.md`, matriz de `docs/plano-piggy-assistente-contextual.md`; `cashflow.py`, `cashflow_forecast.py`, `decision_simulator.py`; `db/contas_hoje.py`, `db/patrimonio.py`, `db/accounts.py`, `db/lancamentos.py`, `db/users.py`, `db/cards.py`, `db/bills.py`, `db/open_finance_cash.py`, `db/bank_movements.py`; consumidores/tools e contrato v2 citados acima. Referências correspondem à base fixa; procurar símbolos novamente antes da edição.
