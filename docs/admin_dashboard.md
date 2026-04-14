# Dashboard Administrativo Privado

## Objetivo

Criar um painel interno, separado do dashboard do usuário final, para acompanhar:

- aquisição e crescimento
- atividade e retenção
- volume financeiro e uso das funcionalidades
- sinais operacionais, erros e falhas de autenticação

## Arquitetura recomendada

### Camadas

1. `FastAPI` no backend atual
2. `Postgres` como fonte principal dos dados agregados
3. `HTML + CSS + JS` versionados no próprio projeto para o MVP
4. `Chart.js` para gráficos temporais
5. autenticação admin exclusiva por variável de ambiente

### Motivo dessa escolha

- reaproveita o stack que já existe hoje
- reduz complexidade operacional
- evita criar outro serviço só para observabilidade básica
- permite evoluir depois para um frontend separado, se o volume crescer

## Estrutura de páginas

### `/admin/login`

- login privado do administrador
- credenciais independentes das contas dos usuários

### `/admin`

Módulos principais:

- visão executiva
- crescimento e aquisição
- atividade e uso
- finanças agregadas
- usuários com maior volume
- eventos operacionais e erros

## Métricas recomendadas

### Aquisição

- total de usuários
- contas criadas por dia/semana/mês
- crescimento acumulado
- logins com sucesso e falha
- taxa de sucesso de login

### Atividade

- usuários ativos em 7 e 30 dias
- transações por dia
- usuários com maior volume de uso
- última atividade por usuário

### Financeiro

- receitas agregadas
- despesas agregadas
- total em caixinhas
- total em investimentos
- pendência aberta de cartões
- transações por mês

### Operacional

- falhas de login
- exceções HTTP não tratadas
- eventos internos importantes do sistema

## O que vale implementar agora

- painel admin privado separado
- agregações por janela de tempo
- trilha de login
- captura mínima de erros operacionais

## O que eu evitaria agora

- permissões complexas com múltiplos perfis de admin
- BI pesado ou data warehouse separado
- alertas em tempo real sofisticados antes de validar quais sinais importam
- dashboards por feature muito detalhados sem volume suficiente

## Próximos passos recomendados

1. registrar também erros dos workers de WhatsApp, billing e jobs agendados em `system_event_logs`
2. adicionar filtros por período customizado
3. adicionar funil de onboarding por canal (`discord`, `whatsapp`, `email`)
4. criar alertas simples para queda brusca de login, crescimento anômalo de erro e pico de falhas

## Opinião prática

Sim, é totalmente possível e faz sentido.

Para o estágio atual do bot, o melhor painel não é o mais complexo, e sim o que te mostra rápido:

- se entrou usuário novo
- se os usuários estão voltando
- se o volume financeiro está crescendo
- se algum fluxo quebrou

Se você conseguir enxergar essas quatro coisas com clareza, já vai reconhecer erros muito mais cedo e tomar decisões melhores sem sobrecarregar o projeto.
