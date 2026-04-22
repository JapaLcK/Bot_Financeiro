# Bot Financeiro — Contexto do Projeto

> Leia este arquivo no início de cada sessão antes de fazer qualquer coisa.

---

## O que é o projeto

Bot financeiro pessoal que virou produto SaaS. Permite registrar despesas e receitas
em linguagem natural pelo **WhatsApp** e **Discord**. Tem dashboard web ao vivo,
categorização automática com IA (GPT-4o-mini como fallback), cartões de crédito,
caixinhas, investimentos com CDI e importação OFX.

**Stack:** Python 3.13, FastAPI, PostgreSQL, psycopg3, discord.py, Railway (deploy).

---

## Estrutura de arquivos

```
bot.py                          — bot do Discord (processo 2)
launch.py                       — ponto de entrada Railway (sobe bot + dashboard em paralelo)
db.py                           — TODA a lógica de banco (queries, init_db, auth, etc.)
ai_router.py                    — fallback de IA (GPT-4o-mini)
parsers.py                      — parse de linguagem natural ("gastei 50 mercado")
core/
  handle_incoming.py            — roteador principal de mensagens (Discord e WhatsApp)
  types.py                      — IncomingMessage, OutgoingMessage
  help_text.py                  — textos de ajuda
  services/
    quick_entry.py              — registra despesa/receita rapidamente
    email_service.py            — envio de emails (SMTP, Gmail pessoal com App Password)
    category_service.py         — serviço de categorias
    ofx_service.py              — importação de extratos OFX
    cc_services.py              — cartão de crédito
  reports/
    reports_daily.py            — relatório diário automático
frontend/
  finance_bot_websocket_custom.py  — FastAPI app (dashboard + TODOS os endpoints REST)
  index.html                    — landing page + cadastro + login
  dashboard.html                — dashboard financeiro ao vivo
  reset-password.html           — página de redefinição de senha
adapters/
  whatsapp/wa_webhook.py        — webhook WhatsApp (não-oficial, risco de ban)
tests/                          — pytest
```

---

## Banco de dados — tabelas principais

| Tabela | Função |
|--------|--------|
| `users` | usuário canônico (id interno) |
| `accounts` | saldo da conta corrente |
| `launches` | todos os lançamentos (despesas, receitas, movimentos internos) |
| `auth_accounts` | email + password_hash + plano Stripe |
| `user_identities` | mapeamento platform (discord/whatsapp/email) → user_id |
| `link_codes` | código temporário para vincular plataformas |
| `email_verification_codes` | código de 6 dígitos enviado no cadastro |
| `password_reset_tokens` | token de recuperação de senha (30 min) |
| `dashboard_sessions` | token JWT de acesso ao dashboard |
| `pockets` | caixinhas (poupança por objetivo) |
| `investments` | investimentos com CDI |
| `credit_cards` / `credit_bills` / `credit_transactions` | cartão de crédito |
| `user_category_rules` | regras de categorização automática |
| `pending_actions` | ações pendentes de confirmação (ex: apagar lançamento) |

---

## Endpoints REST (finance_bot_websocket_custom.py)

### Auth
| Método | Rota | Função |
|--------|------|--------|
| POST | `/auth/register` | Inicia cadastro → envia código de verificação por email |
| POST | `/auth/verify-email` | Confirma código de 6 dígitos → cria conta |
| POST | `/auth/login` | Login email+senha → retorna JWT |
| GET | `/auth/validate` | Valida token JWT |
| GET | `/auth/me` | Dados do usuário autenticado |
| POST | `/auth/link-code` | Gera novo link_code para vincular plataforma |
| POST | `/auth/forgot-password` | Envia email de recuperação de senha |
| POST | `/auth/reset-password` | Redefine senha com token do email |

### Dashboard
| Método | Rota | Função |
|--------|------|--------|
| GET | `/d/{code}` | Acessa dashboard via token seguro |
| GET | `/data/{user_id}` | Dados financeiros do mês |
| GET | `/history/{user_id}` | Histórico de meses |
| GET | `/export/{user_id}` | Download CSV |
| WS | `/ws/{user_id}` | WebSocket para updates ao vivo |

### Billing (Stripe — parcialmente implementado)
| Método | Rota | Função |
|--------|------|--------|
| POST | `/billing/create-checkout` | Cria sessão Stripe Checkout |
| POST | `/billing/webhook` | Webhook Stripe (atualiza plano) |
| POST | `/billing/portal` | Portal de gerenciamento Stripe |

---

## Fluxo de cadastro (atual)

```
1. Usuário preenche email + senha na landing page
2. POST /auth/register → gera código 6 dígitos, envia email, retorna {status: "verification_sent"}
3. Frontend mostra campo para digitar o código
4. POST /auth/verify-email → valida código, cria conta, envia email de boas-vindas
5. Frontend mostra tela de sucesso com link_code para vincular o bot
6. Usuário envia "vincular XXXXXX" no Discord ou WhatsApp
```

## Fluxo de recuperação de senha

```
1. Usuário clica "Esqueci minha senha" no login
2. Modal pede o email → POST /auth/forgot-password
3. Email enviado com link: DASHBOARD_URL/reset-password?token=xxx (expira 30 min)
4. Página reset-password.html lê token da URL
5. POST /auth/reset-password → senha atualizada
```

---

## Email (email_service.py)

Usa SMTP padrão (smtplib built-in). Configurado com Gmail pessoal + App Password.

**Variáveis de ambiente:**
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=lucaskuramoti06@gmail.com
SMTP_PASSWORD=<app password de 16 chars>
EMAIL_FROM_NAME=Bot Financeiro
```

**Funções disponíveis:**
- `send_verification_email(to, code)` — código de 6 dígitos no cadastro
- `send_welcome_email(to, link_code, dashboard_url)` — boas-vindas após verificação
- `send_password_reset_email(to, reset_url)` — link de recuperação de senha
- `send_email(to, subject, html_body, text_body)` — genérico

Falha silenciosa: se SMTP não estiver configurado, loga WARNING mas não quebra o fluxo.

---

## Variáveis de ambiente (todas)

```
DATABASE_URL           — PostgreSQL (Railway)
DISCORD_BOT_TOKEN      — token do bot Discord
OPENAI_API_KEY         — GPT-4o-mini (fallback de categorização)
WA_VERIFY_TOKEN        — webhook WhatsApp
WA_TOKEN               — token WhatsApp não-oficial
WA_PHONE_NUMBER_ID     — número WhatsApp
DASHBOARD_URL          — URL pública do Railway (ex: https://xxx.up.railway.app)
DASHBOARD_USER_ID      — user_id do dono (para uso interno)
JWT_SECRET             — secret para assinar JWTs (trocar em produção!)
SMTP_HOST/PORT/USER/PASSWORD/EMAIL_FROM_NAME — email transacional
STRIPE_SECRET_KEY      — Stripe (parcialmente implementado)
STRIPE_WEBHOOK_SECRET  — Stripe webhook
STRIPE_PRICE_ID_PRO    — ID do preço Pro no Stripe
```

---

## Decisões já tomadas (não sugerir alternativas)

- **Sem Google Sheets** — removido, não usar mais
- **Gmail pessoal com App Password** — escolha atual para SMTP (sem CNPJ para Business)
- **WhatsApp não-oficial** — risco conhecido, migração para API oficial planejada para depois
- **Railway** — plataforma de deploy escolhida
- **psycopg3** (não psycopg2) — biblioteca Postgres em uso
- **Sem Redis ainda** — fila de tarefas e cache são Fase 2 do roadmap
- **Rate limiting com slowapi** — já implementado nos endpoints de auth

---

## O que está pendente (roadmap)

### Crítico para lançar
- [ ] Stripe completo (produtos criados + webhook funcionando + middleware de plano)
- [ ] Teste de ponta a ponta do fluxo de cadastro em produção

### Fase 2 (pós-lançamento)
- [ ] Redis para cache do dashboard e fila de IA
- [ ] Worker separado para tarefas pesadas (OFX, relatórios, CDI)
- [ ] Resumo semanal automático (todo domingo)
- [ ] Notificações: alerta de gasto alto, fatura chegando
- [ ] Dashboard mobile-first (redesign responsivo)
- [ ] Métricas de uso (PostHog ou Mixpanel)
- [ ] Row-Level Security no Postgres

### Fase 3 (escala)
- [ ] App mobile (React Native)
- [ ] Open Finance (integração com bancos)
- [ ] Plano Família
- [ ] API pública
- [ ] Migração WhatsApp para API oficial da Meta

---

## Convenções de código

- Queries sempre com `WHERE user_id = %s` — nunca vazar dados entre usuários
- Falhas de email sempre silenciosas (log WARNING, não quebra fluxo principal)
- Endpoints de auth com rate limiting via `@limiter.limit()`
- Modelos Pydantic para todos os bodies de POST
- `ensure_user()` antes de qualquer operação de banco para usuários novos
- Imports dentro das funções nos endpoints FastAPI (evita circular imports)
