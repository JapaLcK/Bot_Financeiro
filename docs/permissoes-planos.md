# Permissões dos planos

Matriz aplicada com `PLANS_V2_ENABLED=1`, alinhada ao planejamento dos planos em
22/09/2026. As rotas continuam exigindo sessão, dono e direito de acesso vigente.
O modo legado (`PLANS_V2_ENABLED=0`) mantém as permissões anteriores.

| Recurso | Essencial | Plus | Pro |
| --- | --- | --- | --- |
| Classificação e categorização por IA | Sim | Sim | Sim |
| Gastos/receitas recorrentes e contas a pagar | Sim | Sim | Sim |
| Orçamento mensal por categoria | Sem teto de categorias | Sem teto de categorias | Sem teto de categorias |
| Totais, categorias e estabelecimentos nas análises | Sim | Sim | Sim |
| Correção de categoria e preferências aprendidas | Sim | Sim | Sim |
| Resumos diário/mensal e semanal pedido manualmente | Sim | Sim | Sim |
| Resumo semanal automático | — | Sim | Sim |
| Insights personalizados, padrões e comparação de períodos | — | Sim | Sim |
| Previsão de saldo | — | 30 dias | 30/60/90 dias |
| Projeção por data, inclusive pela IA | — | Até 30 dias | Até 90 dias |
| Trajetória diária e pior dia do caixa | — | — | Sim |
| Orçamento Doméstico (método dos potes/percentuais) | — | Sim | Sim |

`core/services/plan_service.py` concentra o tier mínimo e os horizontes. A API
de previsão seleciona o resultado permitido antes do cálculo: Plus não recebe
trajetória, pior dia ou dados de 60/90 dias. A projeção por data e a tool
`check_cashflow` têm o mesmo limite. As respostas da IA explicam o limite e
orientam a não extrapolar os horizontes recebidos.

Insights são barrados antes de cache ou geração, inclusive no pré-processamento.
O Plus recebe a explicação de cada insight (período, fonte, referência) e o link de
navegação; o Pro recebe também `impacto` e `acoes` (feature `insights_acoes`), calculados em código.
O cache é um só e o plano vale na leitura: a rota e o card `piggy` do chat passam pela mesma projeção.
O insight do mês no vermelho (`month_projection`) exige também `forecast` (Plus), na geração e na leitura.
`PUT /insights/{uid}/estado` (dispensar/útil) passa pelo mesmo gate `insights` antes de ler ou gravar.
O dashboard Essencial mantém as análises básicas e não solicita os endpoints
avançados. O resumo semanal é conferido na ativação e em cada envio automático;
preferência antiga ligada não contorna um downgrade. Desligar continua permitido.
No job do WhatsApp o plano é checado ANTES do claim da semana, então o downgrade não
consome o envio (`tests/test_resumo_semanal.py`, banco real). A linha de comparação com a
semana anterior no texto do resumo semanal segue o gate `financial_comparison` (Plus+); o
Essencial que pede "resumo semanal" vê resultado e maior categoria, sem a variação.
A comparação por períodos equivalentes e por categoria (`compare_periods`, PL-07) é Plus+: o
gate `financial_comparison` é a primeira instrução do serviço e o Essencial recebe `pro_required`
sem nenhum dado lido. O "mês anterior" do Resumo do mês (`anterior` de `GET /api/v2/resumo-do-mes`:
dois totais, sem percentual) não muda e segue no Essencial. Na interface, a mesma regra é Plus+ em
`GET /api/v2/comparacao` (bloco "Comparado ao mês anterior" da tela v2: o Essencial o vê com cadeado no
Organizar e ele nunca entra no layout) e no KPI "vs" das Análises do painel antigo (o Essencial não recebe nem lê o
período anterior).

**Pendência conhecida (decisão do dono, 2026-10-16, D4 do PL-07 PR B):** a política é fechar para Plus+ toda
variação % que o Essencial ainda vê e unificá-la no serviço de comparação. Hoje o Essencial ainda vê variação em
dois lugares, ambos sem gate: o "Para onde vai" da tela v2 (`GET /api/v2/gastos`, "x% a mais que no mesmo dia do mês
anterior", com outra regra de cartão: data da compra) e o "Ritmo de gastos" do app nativo (`/api/app/mes-detalhes`,
chip de %). A implementação fica para issue/PR próprio, fora do PR B.
A newsletter de curiosidades genéricas continua separada dos insights pessoais.

O alerta de gasto fora do padrão do Xerife (referência, diferença, amostra e a marcação
"era esperado", PL-04) é do agente: Plus e Pro, pelo orçamento de energia. Essencial e Grátis não
recebem evento de anomalia (o runner confere o plano antes de ler ou gravar) e o `PUT` de "esperado"
responde 403 `pro_required`. O lembrete simples do Essencial é o alerta de orçamento por categoria
(80/100/120%, `core/budget_alerts.py`), que não mudou.

Orçamento Doméstico é o método específico de dividir a renda em potes, diferente
do orçamento mensal por categoria. As regras existentes de correção/aprendizado
continuam disponíveis; automações avançadas e quantidades comerciais indefinidas
estão no [backlog de planos](backlog-planos-2026-09-22.md).

Cobertura executável: `tests/test_plan_permissions.py`,
`tests/test_plan_permissions_jobs.py`, `tests/frontend/plan_permissions.test.mjs`
e casos de resumo semanal em `tests/frontend/settings_sem_plano.test.mjs`.
Os testes de WhatsApp mantêm classificador e roteador reais, simulando somente
o fornecedor de IA. Os jobs simulam apenas o transporte externo e os candidatos
ao envio. Não há migração nem alteração de assinaturas nesta mudança.
