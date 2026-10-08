# Etapa 3 — Previsão com dados reais

> **Histórico do planejamento original, anterior à autorização de execução.** Em 05/10/2026, após revisar a divisão em três PRs e um quarto condicionado e dispensar Q37 para a coorte nova, o dono pediu “Então pode começar a etapa3”. A execução do PR1 está autorizada no escopo conservador de [plano-etapa3-pr1-motor.md](plano-etapa3-pr1-motor.md), que prevalece sobre avisos históricos de não implementação abaixo. Isso não aprova janela30d, estimativa variável, reset ou produção.
> Conferência estática em 05/10/2026, worktree `etapa3-previsao-plano`, branch
> `Japa/etapa3-pr1-motor-previsao` (renomeada ao iniciar a execução), base `dc1d0009075f3688dc4ea80ca02da80c7f073a72`.
> Nenhum teste, banco de produção, callback bancário ou mensagem financeira foi executado
> para escrever este plano. Repetir a varredura na base da implementação.

## 1. Objetivo e decisões que já valem

Mostrar o saldo previsto por data, os compromissos que o explicam e o que ainda precisa
ser conferido, usando uma regra única para a `/api/v2`, a previsão atual, o simulador e a
IA. Previsão é condicionada aos dados e premissas; um número futuro nunca é saldo realizado.

**Aprovado anteriormente:** Q36 (banco pelo Open Finance; manual só dinheiro físico), Q42
(recorrente só prevê e avisa; não lança nem paga), Q18 (a regra nova substitui a antiga no
mesmo PR, inclusive no WhatsApp), API v2 com usuário da sessão, planos no servidor,
`Decimal` no cálculo e texto no JSON, isolamento por usuário, SSE depois do commit e
motivos de incerteza. A matriz direcional do plano da Piggy continua sendo a fonte da
abstenção: risco também precisa de dados confiáveis; `cabe_nas_premissas` continua desligado
até validar estimativa variável e seus outros requisitos.

**Decisão vigente do dono em 05/10/2026:** a coorte prevista para o dashboard v2 é de
usuários novos, que nunca usaram/conectaram o PigBank; não há histórico a revisar. Para
essa coorte, **Q37 não será uma etapa nem gate de lançamento da Etapa 3**. Isso substitui,
nesse escopo, a decisão de 03/10 de fazê-la em etapa própria antes da Previsão. Carteira
nova começa em zero, padrão existente; movimentos manuais em dinheiro alteram seu saldo
normalmente. Não haverá programa de revisão nem diálogo de confirmação da carteira.

Se ocorrer problema, a recuperação definida pelo dono é **Recomeçar do zero → reconectar o
banco → novo sync**. É opção acionada pelo usuário, não reset automático nem autorização
para apagar dados de usuários. O reset existente preserva conta/login/plano, apaga dados
financeiros e remove as conexões; um refresh não recria sozinho o banco removido.
Nenhum reset ou sync foi executado neste planejamento. Usuários com dados antigos, fora
da coorte prevista, não são presumidos novos, zerados ou financeiramente confiáveis.

**Propostas deste documento:** divisão de PRs, contrato e política para representar
incerteza. **Decisões abertas:** janela de conferência, horizonte confiável, estimativa
variável compartilhada, confiança/fim das receitas e calendário de dias úteis. Nenhum
exemplo de 7, 60 ou 90 dias resolve essas decisões por si só.

Faixa futura: domínio de dinheiro, schema, compatibilidade e toda API v2 = **Completo**;
a tela que só consome contrato aprovado = **Leve**. Se ela passar a decidir cálculo,
permissão ou escrita financeira, sobe de faixa. Este planejamento não inicia Coder,
Tester, Manager, commit, PR, deploy ou TestFlight.

## 2. Fluxo atual, reutilização e limite do que foi verificado

| Caminho existente | O que reutilizar / mudar |
| --- | --- |
| `cashflow._starting_balance()` → `_cashflow_events()` → `_projection()` | Seam existente. Trocar o leitor incompleto e as tuplas em float por uma entrada monetária tipada e com qualidade; não copiar a regra para o router. |
| `cashflow_forecast._horizons()` e `_trajectory()` | Mesmos eventos alimentam marcos, série e pior dia. Preservar as invariantes e a explicação por queda desde o último pico, estendendo os metadados. |
| `decision_simulator.simulate()` | Usa saldo/eventos compartilhados, acrescenta eventos de cenário e chama trajetória. Continuará consumidor, sem nova fonte de compromissos. |
| Rotas antigas do monólito `/forecast/{user_id}` e `/recurring-bills/{user_id}/projection`; tools `forecast_balance` e `check_cashflow` | Adaptar formato e gates no mesmo PR que trocar a regra. `tranquilo` não pode permitir a IA afirmar segurança quando a resposta está a conferir. |
| `db/contas_hoje.listar()`, `db/patrimonio` e SQLs canônicos do Open Finance | Reusar recorte, identidade, moeda, saldo e qualidade; extrair o mínimo comum quando for necessário, sem recalcular um “consolidado da previsão”. A seleção de pausadas após a identidade já está protegida no SQL atual: reler, não implementar como bug pendente do §7. Saldo e eventos em uma leitura consistente. |
| `db/cards.card_bill_due_date()`; `_recurring_occurrence_dates()` | Calendário já existe; também é usado pelo agendador de avisos. Estender sob testes dos dois consumidores, sem eliminar helper que ainda serve à Q42. |
| `/painel#/previsao`: `pages.tsx::Forecast`, `Hero`, `Bills`, `Invoice`, `TrajectoryChart` | App alcança a página sem gate específico. Hero usa trajetória demonstrativa/ritmo de 60 dias; Bills usa 60 dias fixos, valor ausente como 0 e data passada como “pago”; Invoice usa um CARD sintético e vencimento calculado. Reusar aparência, substituir dados/estado e cobrir múltiplos cartões. |
| `plan_service.forecast_horizons_for()`, `FEATURE_MIN_TIER_V2` | Política existente: Plus recebe 30 dias e Pro 30/60/90; simulador é Pro. Preservar sem reabrir aprovação de tier/dias. O acesso não se decide escondendo botão. |
| `api/v2/sessao.py`, `erros.py`, `eventos.py`, `lib/v2.ts` | Sessão, envelope, fetch e invalidação já existem. Nova rota fica no sub-app e usa os mesmos mecanismos. |

O inventário complementar em [`etapa3-previsao-inventario.md`](etapa3-previsao-inventario.md)
registra **campo, default, filtro, teto, early-return e consumidor**, com NEW/COVERED
contra os planos anteriores. É parte da leitura do Coder e do Tester, não um conjunto de
exemplos opcionais. As políticas abaixo não dão como reparada a ingestão: marcador de
qualidade só ajuda quando cobre também ausência, truncamento e dados históricos.

## 3. Entrada do motor e política proposta

Hoje ler cada fonte uma vez não é uma snapshot: as consultas abrem conexões separadas.
Proposta: uma leitura consistente por usuário com **base**, **ocorrências**, **qualidade** e
**premissas**, separando fato observado, valor estimado e valor desconhecido. Uma ocorrência
precisa de identidade de origem + data contratual/ciclo, fonte, direção, valor `Decimal | null`,
qualidade de valor/data e realização (`prevista`, `realizada`, `a_conferir`). O formato exato
fica no PR do motor; nome/valor igual não é identidade. Não gerar lançamentos, faturas ou
pagamentos como efeito colateral da consulta. Não acrescentar tabela antes de decidir que
metadado precisa persistir e conferir estruturas existentes.

| Entrada/fonte atual | Default ou corte a conferir | Política recomendada e efeito na conclusão |
| --- | --- | --- |
| Carteira (`accounts` + delta das fusões) | Schema usa saldo 0 por padrão; leitor sem linha também devolve 0. Hoje motivos de contas/foto fixam carteira não confirmada. | Coorte nova definida pelo dono parte de carteira zero e registra movimentos em espécie normalmente, sem confirmação Q37. Ajustar a flag compartilhada para esse escopo, preservando pendências e qualidade das demais fontes. Ausência de linha em usuário com histórico não autoriza tratá-lo como novo nem fazer reset. |
| Contas bancárias (`contas_hoje`/SQL canônico) | Pausadas, outra moeda, moeda presumida, saldo não finito/ausente, fora do sync, idade da conexão; seleção por identidade. | Só BRL conhecido entra como caixa. Saldo excluído/incompleto pode errar nos dois sentidos. Mostrar total disponível com motivo, nunca chamar de completo; não usar limite de cartão, investimentos ou caixinhas como caixa. Preservar 48h, política já aprovada para Contas hoje/foto; frescor do saldo não torna confiável toda ocorrência futura. |
| Conciliação, espécie e declarações (`open_finance`, `bank_movements`) | Contagens e deltas não são lidos hoje pelo motor. | Transportar os motivos e deltas quantificados, quando conhecidos. Conciliação item a item: pior caso de cada direção; declaração sem efeito conhecido bloqueia ambos. Confirmar/desfazer/corrigir/apagar/reconectar invalida a previsão. |
| `pending_actions` e `ai_pending_actions` financeiras | Uma pendência pode nascer depois de uma consulta; payload pode não conter valor. | Enumerar tipos que mudam dinheiro, inclusive pergunta de valor/forma e mídia. Com efeito conhecido, usar pior caso direcional; sem efeito conhecido, ambos inconclusivos. Não tratar toda pendência de conversa como financeira nem limpar pendência ao ler. |
| Receita recorrente (`recurring_incomes`) | Ativa; hoje só mensal/anual, frequência ausente vira mensal, dia ausente vira 1, valor não positivo some; sem fim/confiabilidade. | Não inventar data/valor nem ignorar silenciosamente receita legada. Valor/data/frequência inválidos viram motivo. Receita não garantida só pode melhorar caixa: excluída da base para “cabe”; risco deve sobreviver ao caso em que venha. Extensão para daily/weekly/once e fim requer decisão e teste, sem voltar a creditar automaticamente. |
| Gasto recorrente (`recurring_expenses`) | Só autopay; modos ausentes viram autopay; `payment_type`, `variable_amount` e realizações ignorados. | Gerar ocorrências por calendário desde o início válido; manual e autopay são formas de acompanhamento, não duas dívidas. Todas as frequências aprovadas pela Q42 entram. Falta de valor não é zero; estimativa marcada e de duas direções. |
| Conta manual/avulsa (`bill_instances`) | `list_bills(limit=1000)`; status pending; boleto negativo vira entrada; só próximo ciclo materializado. | Leitor do motor sem truncamento silencioso: recorte por usuário/data e completude verificável. Avulso continua compromisso datado; recorrente representa a mesma ocorrência, não mais uma saída. Preservar instâncias históricas e alterações por ciclo; não somar instância e recorrência. Zero/negativo inválido não cria receita. |
| Realização de recorrência | Datas estritamente depois de hoje; `last_charged_ym`/`last_credited_ym` não marcam realização atual. | Próxima e todas as ocorrências na janela recente permanecem a conferir, inclusive hoje, atrasadas, várias diárias/semanais e virada do ano. Antes da janela, assumir já refletidas no saldo e declarar a premissa; não reconstruir anos desde start_date. Receita incerta: risco com ela/cabe sem ela; despesa incerta: risco sem ela/cabe com ela. Antecipação precisa retirar a ocorrência futura já refletida no saldo quando o vínculo for comprovado. |
| Pagamento de boleto/fatura | Fora do PigBank pode manter pending; pago no PigBank pode sair da lista antes de o banco baixar saldo. | Estado local “pago” não prova débito incluído no saldo. Expor intervalo a conferir e evidência observada. Pagamento externo pendente só piora se subtraído de novo: risco precisa sobreviver sem essa saída. Não conciliar automaticamente por nome/valor/data aproximados. |
| Cartão/fatura (`credit_cards`, `credit_bills`, `credit_transactions`, OF) | Defaults de fechamento/vencimento; total contador, paid/status, estorno/total negativo, moeda, grupo/parcelas incompletos. | Calendarizar a saída na fatura, não na compra. Usar valor/estado com origem e cobertura conhecidos; total negativo não é receita presumida. Saldo de fatura, restante de parcelas e calendário desconhecidos viram motivos próprios. Cartão manual sem compras completas só melhora e bloqueia “cabe”. Pagamento da fatura no banco e dívida não se somam como duas saídas futuras. |
| Gasto fixo pago no cartão | Hoje sai no due_day e de novo na fatura. | Uma única representação no caixa: compra/ocorrência vai à fatura contratual; se já compõe fatura observada, não somar a parte prevista outra vez. Sem vínculo/calendário/cobertura comprovados, mostrar a conferir e abster. Não escolher cartão pela primeira linha nem fundir gastos iguais. |
| Datas | date.today(), clamp de fim de mês; sem ajuste por dia útil. | Uma data-base do fuso do app por cálculo; `utils_date.align_process_tz` já alinha processo/sessão, então `date.today()` sozinho não prova bug de fuso; testar madrugada/mês/ano/bissexto. Data contratual conhecida prevalece. Dia útil depende de política e fonte de calendário aprovadas; não substituir automaticamente por próximo dia útil sem decisão. |
| Gasto variável histórico | Hoje não entra; protótipo usa ritmo de 60 dias e faixa. | Ausência só melhora: não habilita “cabe”. Estimativa é hipótese; validação exige cobertura/amostra/atípicos/reembolsos/cartão vs caixa e exclusão dos compromissos já agendados. Não publicar faixa estatística demonstrativa como dado real. Recomenda-se primeira tela sem estimativa compartilhada, até decisão específica. Não há hoje estimativa variável no `decision_simulator`; a referência de 60 dias é protótipo sintético. |
| Cenário do simulador | 90 dias, até três cenários, primeira parcela mensal presumida, reserva default 0; custo após janela separado. | Reusar motor e conservar formato com adaptador. Horizonte limitado não significa contrato avaliado inteiro. Zero de reserva é comparação com saldo negativo, não prudência. Veredito de compra, oferta/print, juros e extensão de horizonte pertencem ao plano Piggy e seus gates, sem ativação nesta etapa. |

**Regra operacional:** ausência, `NaN`/Inf, dado inválido, default de ingestão e leitura incompleta
não viram dinheiro zero nem calendário válido. Motivo com direção errada também é defeito:
uma única flag genérica de “estimativa” não basta para orientação. Deltas de
conciliação com sinais opostos não se compensam para declarar confiabilidade. A tela pode mostrar uma
projeção condicional com premissas mesmo quando a orientação se abstém; se nem a base tiver
valor utilizável, mostrar indisponível, sem desenhar uma curva de zero.

Deduplicação é por vínculo/identidade persistida e ciclo, não heurística genérica. Enumerar
recorrência × boleto × compra × parcela × fatura × pagamento × saldo observado. Desativar,
editar modo/calendário/valor, excluir, antecipar, estornar e corrigir banco não podem deixar
uma ocorrência fantasma. Não migrar dados financeiros históricos por este plano; correção
ou backfill exige PR próprio, simulação/leitura prévia autorizada e controle reversível.


### Consolidação do inventário NEW/COVERED

Todos os C1–C12 estão representados na matriz de entrada e nos grupos de testes; C9 é
**premissa corrigida** (pausa já protegida), não conserto autorizado. N1–N4 ficam no motor:
snapshot consistente, precisão antes do DTO, significado final/percurso e identidade.
N5 exige validação de `check_cashflow.amount`/HTTP em fronteira comum: ilegível não vira 0;
recusar não finito e esclarecer extra negativo/data passada antes do cálculo. Um extra
opcional ausente pode significar “sem cenário extra”, diferente de número fornecido inválido.
N6 exige inclusividade explícita: forecast hoje→amanhã…N, simulador hoje…hoje+90 com compra
hoje; preservar a âncora e testar paridade nas datas comparáveis sem “corrigir” um dia sem
contrato. N7 inclui getters com `ensure_user`, reabertura de fatura e delete de TTL: não
servem diretamente como leitores de snapshot read-only. N8 é atualização temporal/SSE.
N10 exige política para frequência/desconhecida, anual mês 0/inválido, dia inválido e
start inválido, conservando as validações normais e sem alegar corrupção histórica medida.

**N9 — fronteira das projeções irmãs, proposta explícita:**

- `forecast_month_end` permanece fora da troca do motor de saldo: é extrapolação parcial
  do fluxo do mês, com metodologia própria já descrita, não caixa por datas nem histórico
  completo. Não alimentar Previsão real com seu `saldo_projetado` nem chamá-lo de saldo
  livre. Ajustar identificação no texto se houver ambiguidade; refazer seu método é etapa própria.
- `forecast_next_bill` passa a consumir o **leitor canônico puro de fatura** extraído no PR 1,
  em vez de `list_open_bills` que reabre status. Preservar o significado de `total` como
  valor da fatura, distinguindo `restante a pagar`, estado, data e qualidade quando aprovados
  no adaptador; não trocar “total” silenciosamente por total menos pago. Ausência de fatura
  conhecida não prova fatura de valor 0. Mesma fatura observada usa mesmos dados que o motor.
- O simulador de hábitos do protótipo e o simulador de compra backend têm contratos
  diferentes. Esta etapa não migra produto de hábitos; sobreposição local sai da tela
  Previsão real e continua demonstrativa onde explicitamente identificada. Não prometer que
  toda ferramenta cujo nome começa com forecast calcula a mesma grandeza.

## 4. Contrato de leitura proposto

`GET /api/v2/previsao?dias=<permitido>`: router `api/v2/previsao.py`, registrado em
`api/v2/app.py`, `response_model` Pydantic. Sem `user_id` externo. `usuario_atual` mantém
sessão, plano ativo e chave; recurso/horizonte também conferidos antes de ler/calcular.
Default recomendado: menor horizonte permitido do plano, devolvido explicitamente.
Preservar também o corte **por recurso**, não só por dias: Plus recebe marcos de 30 dias;
trajetória diária, pior dia e suas causas/compromissos detalhados ficam no cashflow Pro,
como no caminho atual. Pro recebe 30/60/90; simulador continua Pro. Liberar gráfico de 30
dias no Plus seria decisão nova e não está presumida aqui. A resposta informa capacidades
e usa `null` no modelo v2 para campos indisponíveis pelo recurso, sem detalhes Pro em
payload oculto. O adaptador legado preserva a ausência de `trajectory`, `worst_day`,
`period`, `threshold` e `vencidos` no Plus, como prova
`tests/test_plan_permissions.py:103` (`test_forecast_api_e_tool_nao_vazam_horizontes`).
Fora do tier = erro de recurso no envelope; horizonte fora da capacidade = recusa clara,
sem devolver dias extra ou cortar silenciosamente. Não aceitar data/horizonte ilimitados.

Esboço **para aprovação**, sem prometer estes nomes como API pronta:

```text
hoje, calculado_em, periodo: {inicio, fim}|null, dias_permitidos[], capacidades[]
base {saldo: decimal_text|null, motivos[]}
estado: condicional|a_conferir|indisponivel
motivos[]: códigos estáveis, não erro livre do provedor
premissas[]: fatos de cobertura, período considerado e hipóteses
marcos[] {data, saldo: decimal_text|null, motivos[]}
trajetoria: [{data, saldo: decimal_text|null, motivos[]}] | null
pior_dia: {data, saldo, desde, causas[]} | null
compromissos: [{chave, fonte, nome, tipo, valor: decimal_text|null,
               qualidade_valor, qualidade_data, realizacao,
               primeira_data, ultima_data, ocorrencias, motivos[]}] | null
cobertura {fontes_incompletas[], janela_conferencia_inicio,
           inclui_estimativa_variavel: boolean}
```

Dinheiro é `Decimal` desde a leitura, sem atravessar `_row()` que transforma em float.
Serialização v2 em texto conserva escala e não arredonda a coluna. Definir no motor onde
um **resultado calculado** precisa de quantização; mesma regra para todos os consumidores,
com fronteiras de centavo, subcentavo e sinal de zero nos testes. Conversão para `number`
na tela serve só à geometria do SVG; labels/totais usam formatador de texto monetário.
Sem campos `raw`, PII, ids de provedor ou dados de outro usuário. A chave é referência
opaca da ocorrência/origem, não autorização para editar.

Marcos e trajetória, quando permitida, têm os mesmos eventos e horizonte efetivo. Série começa no saldo de
hoje, com vencidos/a conferir identificados, sem mover silenciosamente a data de origem.
Grupo de recorrente reduz a lista visual, **não** as datas que pesam no cálculo/pior dia.
Se a resposta limitar detalhes, devolver a indicação de truncamento e caminho completo;
um teto de UI não pode truncar o motor. O payload final deve ter limite explícito e
consulta de detalhes só se a necessidade real justificar outro endpoint.

Gerar `api-v2.gen.ts` com `scripts/gerar_tipos_api_v2.py`, atualizar fixtures
`tests/frontend/api_v2_respostas.json`, rodar contrato e build. Query tipada também deriva
do OpenAPI. Não escrever tipos duplicados à mão ou monetário `number` no contrato v2.
Erro de fonte não sai 200 com valores inventados; indisponibilidade estrutural usa o
tratamento existente. Qualidade parcial conhecida sai como resposta tipada com motivos.

## 5. PRs propostos, ordem e critérios de avanço

### Coorte nova e motivo atual da carteira — sem etapa Q37

Hoje `contas_hoje.py:51` e `patrimonio.py:194` fixam `carteira_nao_confirmada=True`.
O PR 1 deve alinhar essa regra compartilhada à decisão de 05/10 para a coorte nova,
sem criar diálogo/programa Q37 nem tornar base incompleta confiável. Remover esse motivo
indevido nessa coorte não remove conciliação, espécie, moeda, banco antigo/incompleto ou
outras pendências. Q41 e seus efeitos de espécie continuam funcionando normalmente.

`usuario_atual`/`dashboard_v2_enabled` hoje verificam sessão, assinatura e allowlist de
e-mail/uid; **não verificam ausência de histórico**. A coorte é definida pelo produto e
operação do dono, não uma propriedade já comprovada pelo acesso v2. O Coder deve conferir
como representar esse escopo com o mecanismo atual; eventual metadado é escolha técnica
justificada pelo inventário, não novo gate de produto. Não aplicar a exceção globalmente
às superfícies financeiras legadas ou inferir que todo usuário habilitado tem saldo zero.
Casos de histórico antigo preservam dados e motivos aplicáveis; reset não é pressuposto.

### PR 1 — motor e convergência de todos os consumidores (Completo)

1. Reler inventário e aplicar a decisão de coorte nova/flag da carteira; fechar as políticas
   restantes que bloqueiam o cálculo e as mudanças de dados, sem dependência de Q37.
2. Estender/extrair os leitores canônicos **puros**, com cursor comum; não chamar listagem
   ou rebuild de fatura que grava como efeito colateral; montar base/ocorrências/qualidade
   e calendário no seam de `cashflow`/`cashflow_forecast`, usando `Decimal`.
3. Cobrir fontes, realização desconhecida, cartão/fatura, recorrência/instância e pendências
   com política aprovada; onde falta evidência, conservar motivo e abstenção.
4. Migrar `project`, `forecast_horizons`, `forecast_with_trajectory`, simulador, tools e
   rotas antigas e o leitor da próxima fatura no mesmo PR; adaptar texto/prompt que hoje transforma `tranquilo` em
   afirmação de segurança. Hoje ele só compara saldo final e a note promete “positivo até lá”:
   proposta é expor separadamente saldo final e mínimo do percurso, com qualidade, eliminando
   essa promessa. Nome/compatibilidade do booleano requer decisão no contrato; teste saldo
   final positivo após pior dia negativo e positivo legítimo sem queda. Apagar a lógica substituída no mesmo diff.

**Pronto:** todos os consumidores usam os mesmos eventos/base; comparação nominal dos
cenários antes/depois mostra mudanças previstas por nome e centavo; diferenças esperadas
são explicadas. Nenhum caminho mantém o algoritmo antigo atrás de flag ou fallback.
Conversas reais pelo `handle_incoming` com estado de teste provam que pendência de outro
assunto não é consumida nem omitida. Ler previsão não grava, paga, cria fatura ou lança.

Se persistência de confiança/realização/fim for aprovada e indispensável aqui, inclui
schema, consumidores, privacidade, aviso SSE e migração compatível no mesmo PR; não
introduzir tabela só para organizar o plano. Se esse diff não couber numa revisão
compreensível, separar **preparação de dados que ainda não troca regra** num PR anterior
Completo, com seu gate próprio. A troca financeira e remoção da regra antiga continuam
atômicas no PR 1. Inventário de escrita/leitura/concorrência determina essa separação,
não um número artificial de arquivos.

### PR 2 — API v2 de Previsão e contrato gerado (Completo)

Adicionar router/modelos sobre o motor pronto, gate de plano/horizonte, qualidade e grupos
para apresentação. Gerar tipos/fixtures; documentar rota em `docs/CLAUDE.md`. Verificar
isolamento A/B, erro e varredura das rotas; consulta única consistente com saldo e eventos.

**Pronto:** contrato valida JSON real/fixtures; nenhum dinheiro `number`; tier baixo não
recebe informação calculada além do permitido nem por query adulterada; não há acesso
externo a uid. Exemplos de teste são dados herméticos, não consulta financeira de produção.

### PR 3 — tela real e blocos alcançáveis (Leve)

Aplicar `.claude/skills/pigbank-frontend/SKILL.md`. Separar página se necessário, usando
`Hero`, `TrajectoryChart`, lista de compromissos e detalhes de fatura existentes quando
seus contratos servirem. Consumir TanStack Query/fetch v2; revisar também card do Resumo,
links, Cmd-K, ação “Ver 90 dias” da Piggy, tópicos/chat demonstrativo e seletor global.
O seletor “mês” poderá significar fim do mês corrente apenas se dentro do teto permitido;
proposta é restringir esta entrega a hoje→futuro. Mês fechado não tem histórico de caixa
aprovado e não será reconstruído de lançamentos. Ajustar Topbar/guia/Cmd-K sem confundir
a decisão antiga de manter seletor no protótipo com aprovação de previsão do passado.
No Plus, mostrar marcos permitidos e convite claro aos recursos Pro; ausência por gate
não é ausência de compromissos. Nenhuma trajetória/causa Pro chega escondida ao cliente.
Somente blocos efetivamente conectados ganham dados reais; demais continuam identificados
como demonstrativos e não emprestam seus números à previsão.

**Pronto:** desktop/mobile mostram origem/premissas/motivos; nenhuma faixa provável fictícia
ou história de saldo reconstruída. Diários/semanal agrupam por identidade/nome com quantidade,
primeira/última data e valor total sem esconder divergências de valor/certeza/cartão.
Expandir revela ocorrências e calendário. Data passada não prova “pago”/“recebido”;
limite disponível do cartão não significa caixa disponível para pagar. Falha não vira curva zerada ou lista vazia de
sucesso; dados anteriores ficam identificados como antigos. Navegação, teclado, leitor
de tela, estados de vazio/erro/carregamento e atualização são conferidos no bundle real.

### PR 4 condicionado — estimativa variável, se aprovada (Completo; UI Leve)

Especificar método/cobertura/amostra e exclusões de duplicidade; medir contra histórico de
teste representativo antes de expor valores. Não há estimativa variável implementada no simulador atual. Recomenda-se que uma futura
estimativa comece como hipótese da simulação, até decisão de promovê-la ao motor compartilhado; a promoção exige migração
de contrato própria e comparação dos consumidores. Tela sem estimativa continua útil,
mas não habilita “cabe”. Não incluir print, recomendador de compra ou desktop neste PR.

**Pronto:** método e qualidade mensuráveis, casos de caixa/cartão, transferências, reembolso,
compromisso agendado, amostra curta e mudança de conexão cobertos. Meta de falso “cabe”,
reserva, calendário e horizonte confiável ainda são gates da Piggy; estimativa pronta
sozinha não liga orientação positiva.

## 6. Compatibilidade, atualização e reversibilidade

Adaptadores podem manter campos/formato de clientes antigos durante convivência; devem
traduzir saída do motor, nunca recalcular compromissos. Resultado antigo sem campo de
qualidade não pode continuar sendo usado para orientar segurança: adaptar tool, nota e
texto determinístico juntos. Mudança intencional de número deve aparecer na comparação,
não ser escondida como “compatibilidade”.

Reversão da tela/rota é retirar a conexão/voltar ao estado anterior sem mexer em dinheiro.
Uma migração eventual é aditiva e preserva dados antigos como desconhecidos; versão antiga
não deve apagar metadados novos. Voltar ao algoritmo financeiro removido é rollback do
PR, não um segundo algoritmo mantido em produção. Nenhuma rotina de escrita de carteira
ou de cobrança será acionada para fabricar os dados que faltam à previsão.

SSE existente invalida consultas em aviso e reconexão; query key inclui horizonte e sessão
é limpa no logout/downgrade. Confirmar, editar, apagar, desativar, mudar forma/calendário,
reconciliar, sync/estorno/PENDING→lançado/correção e recuperação voluntária devem chegar
depois do commit; nenhuma rotina de reset será iniciada pela previsão.
Conferir `TABELAS_QUE_AVISAM` campo a campo: a lista atual não inclui `pending_actions`
nem `ai_pending_actions`; ação financeira pendente pode mudar a confiabilidade sem alterar
saldo. Incluir ambos indiscriminadamente pode avisar conversa não financeira: resolver a
classificação/aviso no escritor responsável ou trigger conforme o inventário completo. Se não há aviso para uma dependência, completar a cadeia
no PR que a introduz; não manter tela desatualizada em silêncio.

Idade do banco, vencimento e virada de dia mudam sem escrita: reconsulta programada na
fronteira de validade, volta à aba/app e reconexão, sem loop curto e sem requisição fora
da permissão. Resposta antiga não pode substituir horizonte novo; queda de sessão fecha
stream e limpa dado privado. Cache de query conserva dados só com indicação de antiguidade.
Backend não ganha cache financeiro persistente sem necessidade medida e contrato de invalidação.

## 7. Testes previstos e comparação com baseline

Não houve execução de testes neste planejamento. Antes de pytest, aplicar a skill
`baseline-testes`; usar venv da raiz, ambiente de teste e tarefas de fundo sob controle.
Rodar só a área local; suíte inteira no CI. Guardar nodeids e status da baseline na base
fixa, com SHA, ambiente e comando. Comparar por **nome/status**, não total de verdes;
repetir baseline se base, ambiente ou contrato mudarem. Não ignorar coleta inesperada.

Base existente para leitura/execução conforme diff: `test_cashflow_project.py`,
`test_cashflow_forecast.py`, `test_cashflow_trajectory.py`, `test_cashflow_worst_day.py`,
`test_cashflow_forecast_rotas.py`, `test_cashflow_receita_frequencia.py`,
`test_decision_simulator.py`, `test_decision_simulator_calculo.py`,
`test_simulator_rotas.py`, `test_simulator_centavos.py`, `test_simulator_reserva_hoje.py`,
`test_recorrente_so_preve.py`, `test_plan_permissions.py`, `test_api_v2_rotas.py`,
`test_api_v2_contrato.py`, `test_api_v2_isolamento.py`, `test_api_v2_eventos_escrita.py`
e testes dos leitores de contas/espécie/conciliação/fatura tocados, enumerados no inventário.
Teste que codifica o comportamento errado atual não vira oracle: sua mudança precisa
estar ligada à política aprovada e a caso que discrimina, sem eliminar cobertura legítima.

| Grupo | Provas necessárias |
| --- | --- |
| Dinheiro/calendário | Mesmos centavos na projeção, série, marcos e cenário atual; ordem de eventos, subcentavos, zero/sinal, overflow/não finito; fuso, fim de mês, bissexto, ano, hoje/vencido/antecipado e início/fim. |
| Recorrências/realização | Todas as frequências; manual/autopay, cartão/caixa; mesma ocorrência com instância; editar/desativar/mudar modo/valor/data; múltiplas ocorrências na janela; realização ligada e unknown; Q42 sem lançamento/juros/fatura novos. |
| Cartão/fatura | Calendário conhecido/presumido, pagamento externo/interno ainda não refletido, parcial, estorno, total negativo, moeda, pending/posted com ids distintos, parcelas projetadas/reais/grupo incompleto, reconexão e compartilhamento legítimo sem fusão de identidades distintas. |
| Qualidade/base | Coorte nova sem histórico com carteira inicial 0 e movimentos em espécie; usuário fora dela com dados preservados; flag da carteira coerente sem ocultar outros motivos; carteira 0 legítima/ausente, conexão pausada/parcial/velha, conta omitida, moeda desconhecida, leitura truncada/default, conciliação e ação financeira pendente quantificada/desconhecida. |
| Isolamento/gates | Usuário A/B em todas as fontes/joins/detalhes; sem sessão, plano inativo, chave ausente, tiers, downgrade, horizonte/limite adulterado, exceção de gate antes do cálculo, sem vazamento em erro/log/SSE/cache. |
| Conversa Q18 | Duas mensagens de assuntos diferentes via `handle_incoming`: deixar lançamento/forma/mídia/confirmação pendente → consultar previsão; consulta → assunto novo; desfazer/corrigir → repetir consulta. DB de teste real; mock só LLM/rede/relógio fronteira. Não mockar leitores/deduplicação/gates para provar que a regra compartilhada funciona; mesmos números entre v2/web/IA para igual horizonte/entrada. Teste de handler isolado não substitui essa prova. |
| API/UI | Tipos/fixtures/modelo, Decimal texto/null/motivos, grupos e detalhes completos, sem truncamento silencioso; telas 1440/760/390/320, SVG/texto, teclado/foco, reduced-motion, erro/retentativa, troca de horizonte, SSE/reconexão/virada do dia e dado privado após logout/downgrade. |

Comparação Q18: conjunto fixo de usuários/cenários de teste com nomes explícitos, cobrindo
carteira, banco, cartão e pendências; guardar antigo e novo antes de eliminar código.
Classificar cada delta por causa aprovada; diferenças inesperadas bloqueiam. Não depender de
`git show HEAD` dinâmico como oracle após commit: fixture ou referência congelada.

Para **conserto de defeito**, grupo inclui positivo do caminho legítimo e negativo desligando
só o conserto numa entrada antes verde. Backup em disco, restauração em `finally`, `cmp`/hash
antes e depois e nova execução positiva; não mutar árvore compartilhada nem testes para
abrir negativo. Unidade nova/calendário puro não precisa de mutação cerimonial. Coder
produz diff, Codex local antes do Tester; Completo tem até duas passadas e Leve uma,
Manager independente e medição pelo `time-dev`. CI/Codex remoto no head atual são gates
antes de merge futuro; nenhuma contagem de sucesso é inferida deste documento.

## 8. Decisões para o dono e gates ainda abertos

| Decisão | Proposta para revisar | Onde bloqueia |
| --- | --- | --- |
| Janela de conferência e invalidadores | Recomenda-se janela rolante de 30 dias + próxima ocorrência de cada recorrência, com premissa sobre o passado. Cobre o ciclo mensal anterior/virada; gera mais perguntas para frequências curtas. 7 dias reduz atrito, mas presume realizadas atrasadas há 8+ dias. Vincular à confirmação só serviria se cobrisse todas realizações; não há confirmação Q37 prevista para essa coorte. Nenhuma opção está aprovada. | PR 1: tratamento de hoje/atrasado/antecipado e conversa. |
| Confiabilidade/fim da receita | Garantia confirmada na análise; persistir só se houver necessidade/decisão. Legado fora de mensal/anual permanece visível como insuficiente até política. Definir término/validade de recorrências. | PR 1: inclusão e qualidade de eventos. |
| Dias úteis/calendário | Usar data contratual; se faltar, marcar dúvida. Definir deslocamento por produto e fonte antes de automatizar. | PR 1: datas e pior dia. |
| Estimativa variável na primeira tela | Primeira entrega com compromissos e premissas, sem faixa fictícia; eventual hipótese de simulação em PR condicionado (inexiste no simulador atual). Promover ao motor só mediante decisão própria. | PR 4 e ativação do “cabe”; não impede proposta/tela condicional. |
| Horizonte da tela e orientação | Preservar política aprovada Plus marcos30/Pro trajetória e30-60-90. Para tela, recomendar hoje→futuro; horizonte confiável de orientação e contrato completo são decisões separadas. | Política de mês fechado se solicitada; positivo da Piggy. |
| Confirmação de realização/fatura/calendário | Primeira consulta só lê e aponta o que conferir. Se o dono quiser confirmação persistente na tela, especificar comando/API e privacidade em PR Completo próprio. | Não criar escrita tácita no PR da tela. |
| Reserva/amostra/validade futura | Preservar frescor de 48h aprovado para o saldo inicial. Isso não responde à validade de receitas/dívidas futuras. Reserva não informada impede juízo por reserva; definir método/amostra e meta de falso “cabe” antes da ativação. | Orientação segura, não autorização de implementação. |

A revisão deste plano deve fechar as decisões necessárias ao PR 1 ou aprovar explicitamente
uma entrega conservadora que preserva a incerteza onde faltam dados. **Planejar agora não
é autorização para implementar Etapa 3 nem executar reset.** Após aprovação, revalidar
fontes na main atual, atualizar inventário e apresentar o escopo concreto do primeiro PR.

Fora desta etapa: reparar ingestão inteira, backfill de produção, câmbio, reconstrução de
saldo passado, migração de patrimônio/metas/Para onde vai, contratação/pagamento, prints,
chat com IA novo, recomendações de ativos e cliente de desktop. Se uma falha de ingestão
impedir classificar a fonte com honestidade, ela vira gate/PR específico, não número
presumido nem expansão silenciosa do motor.

## 9. Fontes primárias nesta base

Referências abaixo são da base `dc1d0009075f3688dc4ea80ca02da80c7f073a72`; linhas mudam,
portanto reler símbolos na implementação. Inventário complementar discrimina os campos.

- `docs/plano-dashboard-v2.md:46` (Q36–43), `:78` (API/Q18), `:113` (incerteza),
  `:270` (perguntas originais Etapa 3), `:496` (decisão histórica de 03/10;
  revisão de 05/10 neste documento e no plano base prevalece para a coorte nova).
- `docs/plano-piggy-assistente-contextual.md:31` (seam/hipótese), `:110` (matriz),
  `:285` (decisões abertas). As perguntas do §7 do dashboard não eram soluções aprovadas.
- `docs/CLAUDE.md:122` (API v2), `:331` (dinheiro/tipos), `CLAUDE.md:1`,
  `.claude/agents/arquiteto.md`, `.claude/commands/time-dev.md`,
  `docs/armadilhas.md:238`, `:262`, `:347`, `docs/ambiente.md:15` (processo/limites).
- `core/services/cashflow.py:36` (datas), `:80` (faturas), `:118` (eventos),
  `:179` (base), `:226` (projeção); `cashflow_forecast.py:48` (trajetória),
  `:135` (uma leitura); `decision_simulator.py:209`, `:228` (90 dias/seam).
- `core/services/plan_service.py:619`, `:653` e `tests/test_plan_permissions.py:103`
  (gates por recurso/horizonte),
  `core/services/ai_chat/tools/bills.py:287` (check_cashflow),
  `core/services/ai_chat/tools/cards.py:246` (próxima fatura),
  `core/services/ai_chat/tools/launches.py:217` (fluxo mensal separado),
  `core/services/ai_chat/system_prompt.py:115` (texto que orienta segurança),
  `frontend/finance_bot_websocket_custom.py:8830` (rotas consumidoras).
- `db/contas_hoje.py:25`, `:35` (qualidade/leitura), `db/bills.py:23`, `:39`
  (float/teto), `db/cards.py:63` (calendário), `db/open_finance.py:2422`
  (cartão), `api/v2/contas.py:53` (snapshot), `api/v2/app.py:30` (routers).
- `webapp/src/dashboard/pages.tsx:56` (tela), `widgets/TrajectoryChart.tsx:36`
  (faixa demonstrativa), `widgets/Piggy.tsx:51` (ação 90 dias),
  `parts/Command.tsx:26` (horizontes), `lib/api.ts:2` (`data.js`/`model.js` sintéticos), `lib/eventos.ts` (invalidação),
  `scripts/gerar_tipos_api_v2.py` (fonte TS).
- `api/v2/sessao.py:18`, `core/services/plan_service.py:399` (acesso não comprova
  ausência de histórico), `db/schema.py:141` (carteira default zero),
  `db/privacy.py:581` (reset preserva conta; reconexão necessária).

Memórias de processo lidas: `piggy-assistente-contextual.md`, `plano-dashboard-v2.md`
(estado final PR4 e histórico Q37, revisado pelo dono em 05/10), `varrer-antes-de-escrever-plano.md`. Elas orientam
varredura e status; os contratos e fatos técnicos acima vêm do código/documentos da base.
