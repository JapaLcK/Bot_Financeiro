# PigBank — contexto de domínio

> **Antes de escrever qualquer código, leia o `CLAUDE.md` da raiz.** Ele traz as
> **regras permanentes de desenvolvimento** (§0: procurar antes de criar, escrever o
> mínimo, mudança cirúrgica, uma fonte de verdade, organização de arquivos), o
> processo de teste e PR, e as armadilhas que já custaram caro. Este arquivo aqui é
> **só o domínio**: o que o produto é, onde cada coisa mora e o que existe hoje.
>
> As duas regras que mais se aplicam a este arquivo: **não repita aqui o que o código
> já declara** (aponte para o arquivo-fonte) e **não invente o que não está no
> repositório**.

---

## O que é o projeto

Assistente financeiro pessoal usado por **WhatsApp** (canal principal), **Discord** e
por um **dashboard web**. Registra despesa e receita em linguagem natural, categoriza
com IA, e cobre cartão de crédito, parcelamentos, caixinhas, metas, orçamentos,
boletos, recorrentes, investimentos com CDI, importação de extrato (OFX/CSV/PDF) e
Open Finance.

**Stack:** Python 3.13 · FastAPI · PostgreSQL (psycopg 3) · discord.py · Railway
(deploy) · Cloudflare (borda). Frontend em HTML/CSS/JS escritos à mão, **sem build e
sem framework**. App iOS em Capacitor carregando o próprio site.

---

## Mapa do repositório

```
launch.py                 — entrypoint do Railway: sobe uvicorn ($PORT) + bot.py em paralelo
bot.py                    — bot do Discord (processo 2)
ai_router.py              — chamada à OpenAI (modelo em OPENAI_MODEL, default gpt-4o-mini)
parsers.py                — parse de linguagem natural ("gastei 50 mercado")
statement_import.py       — importação de extrato (OFX/CSV/PDF)

core/
  handle_incoming.py      — roteador principal de mensagens (Discord e WhatsApp)
  intent_classifier.py    — classificação de intenção
  intent_router.py        — despacho por intenção
  handlers/               — handlers por domínio do fluxo de mensagem
  services/               — 35 módulos + 2 pacotes: e-mail, Pluggy, planos, push,
                            agentes, agendadores, OFX, PIX, categorias, cartão…
  reports/                — relatório diário (reports_daily.py)
  crypto.py, audit.py     — PII cifrada e trilha de auditoria

db/                       — PACOTE com ~30 módulos, um por domínio
  schema.py               — DDL de TODAS as tabelas (init_db) — fonte de verdade
  connection.py           — pool psycopg3

frontend/
  finance_bot_websocket_custom.py — app FastAPI (~14,5k linhas): auth, MFA, billing,
                                    WebSocket, dashboard. AINDA é o monólito.
  routes/                 — routers já extraídos: static_pages, settings, pockets,
                            cards, analytics, open_finance, push, agents, affiliates,
                            shared (html_file, stamp_asset_versions, página de erro)
  *.html                  — servidas por static_pages.py (quantas: `ls frontend/*.html
                            | wc -l`; o número que estava aqui dizia 26 e eram 27);
                            ver o §5 do CLAUDE.md da raiz
  *.css / *.js            — cada arquivo tem uma rota própria; não há StaticFiles mount.
                            Página ou asset sem rota é código morto que ninguém alcança:
                            tests/test_frontend_assets_e_rotas.py reprova os dois casos

adapters/
  whatsapp/               — webhook + cliente da API oficial (Cloud API)
  discord/                — bot e cogs

mobile/                   — app iOS (Capacitor) que carrega https://pigbankai.com/login
scripts/                  — utilitários operacionais e de build de assets
tests/                    — pytest (backend) e tests/frontend/*.mjs (node --test)
docs/refactor_plan.md     — plano de quebra do monólito FastAPI
docs/open_finance_validacao_manual.md — o que do Open Finance só se valida em
                            aparelho ou com Pluggy real, e o roteiro para isso
```

---

## Backend

### App e rotas

O `app` FastAPI vive em `frontend/finance_bot_websocket_custom.py`. Parte das rotas já
saiu para routers em `frontend/routes/`, registrados com `include_router`:
`static_pages`, `settings`, `pockets`, `cards`, `analytics`, `affiliates`, `agents`,
`open_finance`, `push`.

**Rota nova vai para um router de `frontend/routes/`**, não para o monólito. Ao
procurar uma rota existente, procure nos dois lugares:

```bash
grep -rn '@\(app\|router\)\.\(get\|post\|put\|patch\|delete\)("/caminho' --include="*.py" frontend/ adapters/
```

São ~198 rotas. Os grupos maiores: `/auth` (27), `/open-finance` (11), `/settings`
(10), `/billing` (8), `/agents` (7), `/cards`, `/categories`, `/pockets`,
`/recurring-bills` (6 cada), `/analytics` (6), `/investments`, `/installments`,
`/recurring-incomes`, `/recurring-expenses` (5 cada), `/budgets` (4).

### Autenticação

Sessão por **JWT em cookie `HttpOnly`** + **refresh token** (tabela
`auth_refresh_tokens`), com **CSRF por cookie `csrf_token`** (`SameSite=strict`) e
rate limiting via `slowapi` nos endpoints sensíveis.

No cliente, `frontend/auth-refresh.js` faz *monkey-patch* de `window.fetch`: em 401
que não seja o próprio `/auth/refresh`, dispara o refresh, deduplica chamadas
paralelas e repete a request original. Se o refresh falhar, o 401 passa para o
chamador decidir. **Esse interceptor é global nas páginas autenticadas** — considere-o
antes de tratar 401 na mão em qualquer tela.

Caminhos de entrada, todos em `/auth/*`: `register` → `verify-email` (código de 6
dígitos) → `login`; `forgot-password`/`reset-password`; **Google OAuth**
(`google/start`, `google/callback`, `google/complete-signup`, `google/pending/{token}`);
`dashboard-link`/`dashboard-token` (link mágico); `link-code` (vincula WhatsApp e
Discord à conta); `logout`; `refresh`; `account` (exclusão) e `account/export`.

### MFA

TOTP (`pyotp`) com códigos de backup: `/auth/mfa/setup`, `enable`, `disable`,
`verify-login`, `status`, `regenerate-backup-codes`, `onboarding-seen`. Tabelas
`user_mfa`, `user_mfa_backup_codes`, `mfa_login_challenges`; segredo cifrado com
`MFA_ENCRYPTION_KEY`.

**Os códigos de backup aparecem uma única vez.** Qualquer tela ou refresh que passe
por cima deles perde os códigos do usuário — já quase aconteceu (registro no §4 do
`CLAUDE.md` da raiz).

### WebSocket

`ConnectionManager` + endpoint `@app.websocket("/ws/{user_id}")` no monólito. É o que
mantém o dashboard ao vivo quando o bot registra algo pelo WhatsApp. Mudou o formato
de mensagem? Os dois lados mudam junto — o consumidor está no `dashboard.js`.

### Pagamentos

Stripe: `/billing/create-checkout`, `webhook`, `portal`, `subscription`,
`change-plan`, `cancel-change`, `plans-config` e `select-free` (esta só RECUSA
com 410: a escolha do plano Grátis saiu da /precos em 2026-09-02; a rota
sobrevive pra devolver `detail.message` a cliente antigo em cache).

A **escada de planos é `free < essencial < plus < pro`**, atrás do flag
`PLANS_V2_ENABLED` (lido dinamicamente, sem redeploy; `0`/`false` é freio de
emergência e colapsa no binário legado). **A fonte de verdade é
`core/services/plan_service.py`** — não duplique a tabela de tiers, limites ou nomes
em outro lugar (§0.7 da raiz). Limites por plano em `core/services/plan_limits.py`.

### Open Finance

Via **Pluggy**. Endpoints em `frontend/routes/open_finance.py`
(`/open-finance/{user_id}` e `connect-token`, `connectors`, `sync`, `refresh`,
`pluggy-item`, `caixinhas`, `caixinhas/bind`, `mock-connect`) mais o webhook
`/open-finance/pluggy/webhook`. Serviços em `core/services/pluggy*.py` e
`open_finance*.py`; tabelas `open_finance_connections/accounts/transactions/investments` mais
`open_finance_item_registry` — o rastro de todo item que passou por aqui, inclusive o
que nunca virou conexão (token emitido e abandonado, webhook de item desconhecido); o
`GET /items` da Pluggy devolve 401, então sem ela o universo remoto não é enumerável.

Boa parte do comportamento é regida por flags `OF_*` (beta por e-mail/user_id, limite
de bancos no free, refresh proativo). Antes de mexer, leia as flags — o
comportamento em produção pode não ser o do seu ambiente.

### IA

`ai_router.py` chama a OpenAI (`OPENAI_MODEL`, default `gpt-4o-mini`) como fallback da
categorização determinística. Há rate limiting próprio (`core/ai_rate_limiter.py`),
limite mensal de chat (`AI_CHAT_MONTHLY_LIMIT`), chat "Piggy" no dashboard
(`core/services/ai_chat/`) e agentes proativos (`core/services/piggy_agents.py`,
atrás de `AGENTS_ENABLED` + listas de beta).

Categorização tem uma armadilha própria: **categoria e regra de categoria são tabelas
diferentes** (`user_categories` × `user_category_rules`) e a regra ganha da categoria
na inferência.

### E-mail

**Resend** (`RESEND_API_KEY`), em `core/services/email_service.py` — **não é mais
SMTP/Gmail**. Além dos transacionais (verificação, boas-vindas, reset), há e-mails de
ciclo de vida (reengajamento, nudge de upgrade, downsell de trial, relatório de
agente, mudança de plano), com link de descadastro (`make_unsub_url` + `unsub_headers`).

Falha de e-mail é silenciosa por contrato: loga e não quebra o fluxo principal.

### Push (app iOS)

APNs direto, sem serviço intermediário: `core/services/push_service.py` +
`frontend/routes/push.py`, tabela `push_tokens` (token único por aparelho, com
`environment` separando sandbox de produção — o mesmo token não vale nos dois).
Configuração em `APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_AUTH_KEY`, `APNS_TOPIC`.

### Admin e afiliados

Área administrativa própria (`/admin`, `/admin/login`, `/admin/api/*`) com sessão
separada (`ADMIN_DASHBOARD_*`), visão de usuários, overview, auditoria de acesso a PII
e gestão do programa de afiliados (comissões, payouts, PIX). As páginas
`admin-login.html` e `admin-dashboard.html` **não carregam app-mode nem o shim de área
segura**, de propósito.

O drill-down de uma conta troca o plano à mão (`POST /admin/api/users/{id}/plan`
→ `set_account_plan`, a mesma escrita do `/admin/grant-pro`): grava
`plan`/`plan_expires_at` no banco e **não fala com a Stripe** — assinatura viva
continua lá e o próximo webhook dela sobrescreve.

### Tarefas de fundo

Sobem no startup do app quando `RUN_BACKGROUND_TASKS != "0"`: rendimento de
investimento, Open Finance (abaixo), cobrança de recorrentes, agendadores de
engajamento e de IA proativa, retenção de eventos de login. Em teste e no
`dashboard_dev.py` ficam desligadas.

O Open Finance tem **três** trabalhos, não dois: expiração de trial
(`_open_finance_trial_expiry`), refresh proativo e **job de saúde** — os dois últimos no
mesmo tick de `_open_finance_refresh`. O refresh proativo depende de
`OF_REFRESH_ENABLED` (off por padrão em produção); o job de saúde roda MESMO com ele
desligado e ESCREVE `status`/`status_reason`/`health` na conexão do usuário. É de
propósito: ele só faz `GET /items` (não consome cota de coleta) e é o que tira do
"Atualizado" a conexão cujo item sumiu da Pluggy — sem refresh e sem webhook, nada mais
faria essa verificação. Kill switch: `OF_HEALTH_CHECK_ENABLED=0` (default `1`).
(Há ainda `_open_finance_proactive`, que retorna na hora sem `OF_PROACTIVE_ENABLED`.)

---

## Frontend

O detalhamento das armadilhas está no **§5 do `CLAUDE.md` da raiz** (é lá que ele
mora; não duplicar aqui). O essencial de domínio:

- **Sem build, sem bundler, sem framework.** O `package.json` da raiz serve só ao
  harness de testes de frontend.
- **Páginas públicas e área logada são as duas MPA.** Existe um POC de navegação
  client-side (`pb-nav.js`) **desligado por padrão**, restrito ao modo app e a duas
  rotas. Não trate a área logada como SPA e não presuma migração para framework.
- **`dashboard.js` tem 10.587 linhas e 414 funções globais**, e `dashboard.html` tem
  139 handlers `onclick=` que dependem disso. Funcionalidade nova de dashboard deve
  nascer em arquivo próprio (§0.5 da raiz), com rota própria em `static_pages.py`.
- **Segurança de borda** (medida em produção): CSP com allowlist explícita
  (`cdnjs`, `jsdelivr`, `cdn.pluggy.ai`, `connect.facebook.net`,
  `static.cloudflareinsights.com`), HSTS, `X-Frame-Options: DENY`,
  `Permissions-Policy` zerando câmera/microfone/geolocalização,
  `Referrer-Policy: strict-origin-when-cross-origin`, `X-Content-Type-Options: nosniff`.
  O `'unsafe-inline'` do `script-src` só sai quando os handlers inline saírem.
- **PWA**: `manifest.json` (`start_url: /login`) + `service-worker.js` (HTML e auth
  nunca cacheados; assets network-first; API e WS passam direto).
- **App iOS**: `mobile/` (Capacitor) aponta para `https://pigbankai.com/login` com
  `allowNavigation` do domínio inteiro — **qualquer rota do site abre dentro do app**.

---

## Banco de dados

**A fonte de verdade do schema é `db/schema.py::init_db()`** — DDL de ~62 tabelas.
Não mantenha uma segunda lista de tabelas ou colunas em documentação (§0.7 da raiz);
para saber o que existe:

```bash
grep -ohiE "create table if not exists ([a-z_]+)" db/*.py | awk '{print $NF}' | sort -u
```

Os agrupamentos, para orientar a busca: **core** (`users`, `accounts`, `launches`) ·
**auth** (`auth_accounts`, `auth_identities`, `auth_sessions`, `auth_refresh_tokens`,
`auth_login_events`, `auth_rate_limits`, `user_identities`, `link_codes`,
`password_reset_tokens`, `email_verification_codes`, `pending_google_signups`) ·
**MFA** (`user_mfa`, `user_mfa_backup_codes`, `mfa_login_challenges`) ·
**crédito** (`credit_cards`, `credit_bills`, `credit_transactions`) ·
**planejamento** (`category_budgets`, `pockets`, `pocket_lots`, `financial_spaces`,
`recurring_*`, `bill_instances`) · **investimentos** (`investments`,
`investment_lots`, `market_rates`) · **Open Finance** (`open_finance_*`) ·
**IA** (`ai_messages`, `ai_pending_actions`, `ai_fallback_log`, `ai_proactive_cache`,
`agents`, `agent_events`) · **afiliados** (`affiliates`, `affiliate_*`) ·
**privacidade/auditoria** (`audit_events`, `pii_access_log`, `data_export_tokens`).

**Isolamento por usuário é regra dura**: toda query com `WHERE user_id = %s`.

---

## Integrações externas

| Serviço | Para quê | Onde |
|---|---|---|
| WhatsApp **Cloud API oficial** (`graph.facebook.com`) | canal principal | `adapters/whatsapp/` |
| Discord | canal secundário | `adapters/discord/`, `bot.py` |
| OpenAI | categorização, chat, agentes | `ai_router.py`, `core/services/ai_chat/` |
| Stripe | assinaturas | billing no monólito |
| Pluggy | Open Finance | `core/services/pluggy*.py` |
| Resend | e-mail transacional e de ciclo de vida | `core/services/email_service.py` |
| APNs | push do app iOS | `core/services/push_service.py` |
| Meta Pixel / CAPI | marketing (só páginas públicas) | `inject_meta_pixel`, `core/services/meta_capi.py` |

O webhook do WhatsApp **verifica assinatura** (`X-Hub-Signature-256` com
`WA_APP_SECRET`) e se recusa a subir em `APP_ENV=prod` sem o segredo.

---

## Variáveis de ambiente

São ~130, lidas com `os.getenv` espalhado pelo código (só `APP_ENV` passa por
`config/env.py`). Para a lista real:

```bash
grep -rhoE 'os\.(getenv|environ(\.get)?)\(\s*"([A-Z][A-Z0-9_]{2,})"' --include="*.py" . \
  | grep -oE '"[A-Z][A-Z0-9_]{2,}"' | tr -d '"' | sort -u
```

Obrigatórias para o app subir: `DATABASE_URL` e `JWT_SECRET` — sem elas o import faz
`sys.exit(1)`. Para rodar a suíte, ver o §3 do `CLAUDE.md` da raiz.

Grupos: `DATABASE_URL`/`DB_POOL_*` · `JWT_SECRET`/`DASHBOARD_*` ·
`PII_ENCRYPTION_KEY`/`PII_HASH_PEPPER`/`PII_AUDIT_DISABLED` · `MFA_ENCRYPTION_KEY` ·
`WA_*` · `DISCORD_BOT_TOKEN` · `OPENAI_*`/`AI_*`/`AGENTS_*` · `STRIPE_*`/`PLANS_V2_ENABLED` ·
`PLUGGY_*`/`OF_*` · `RESEND_API_KEY`/`EMAIL_FROM*` · `APNS_*` · `ADMIN_DASHBOARD_*` ·
`META_PIXEL_ID` · `RUN_BACKGROUND_TASKS`/`SKIP_INIT_DB`/`ENABLE_DEV_ENDPOINTS`.

---

## Convenções de código

- Query sempre com `WHERE user_id = %s`. Nunca vazar dado entre usuários.
- Falha de e-mail é silenciosa (log, não quebra o fluxo principal).
- Endpoints sensíveis com rate limit (`@limiter.limit()`).
- Modelo Pydantic para todo body de POST.
- `ensure_user()` antes de operação de banco para usuário novo.
- Import dentro da função nos endpoints, quando necessário para evitar import circular.
- PII cifrada (`core/crypto.py`) e acesso registrado (`pii_access_log`, `core/audit.py`).
- Nunca passe segredo em `details` de auditoria (senha, TOTP, código de backup).

---

## Decisões tomadas (não sugerir alternativa sem pedido explícito)

- **Railway** para deploy, **Cloudflare** na borda.
- **psycopg 3**, não psycopg2.
- **WhatsApp Cloud API oficial** — a migração planejada já aconteceu.
- **Resend** para e-mail — SMTP/Gmail foi abandonado.
- **Sem Google Sheets.**
- **Sem Redis** até hoje: não há fila nem cache externo no repositório.
- **Frontend sem build** — HTML/CSS/JS à mão, servidos pelo FastAPI. Não há React,
  Vite, bundler nem processo de build de JS neste repositório, e a discussão de
  migração **não** tem decisão tomada.
