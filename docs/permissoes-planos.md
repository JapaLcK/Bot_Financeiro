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
O dashboard Essencial mantém as análises básicas e não solicita os endpoints
avançados. O resumo semanal é conferido na ativação e em cada envio automático;
preferência antiga ligada não contorna um downgrade. Desligar continua permitido.
No job do WhatsApp o plano é checado ANTES do claim da semana, então o downgrade não
consome o envio (`tests/test_resumo_semanal.py`, banco real). A linha de comparação com a
semana anterior no texto do resumo semanal segue o gate `financial_comparison` (Plus+); o
Essencial que pede "resumo semanal" vê resultado e maior categoria, sem a variação.
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
