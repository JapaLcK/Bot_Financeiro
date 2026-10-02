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
(deploy) · Cloudflare (borda). Frontend em HTML/CSS/JS escritos à mão, **com
ilhas React delimitadas** (`webapp/` → bundles IIFE em `frontend/`); o
`package.json` da raiz continua sem script `build`, e o porquê está em "Decisões
tomadas". App iOS em Capacitor carregando o próprio site.

---

## Mapa do repositório

```
launch.py                 — entrypoint do Railway: carrega o ambiente e vira o uvicorn ($PORT)
bot.py                    — bot do Discord (fora do launch.py desde o PR 5a; não roda)
ai_router.py              — chamada à OpenAI (modelo em OPENAI_MODEL, default gpt-4o-mini)
parsers.py                — parse de linguagem natural ("gastei 50 mercado")
statement_import.py       — importação de extrato (OFX/CSV/PDF)

core/
  handle_incoming.py      — roteador principal de mensagens (Discord e WhatsApp)
  intent_classifier.py    — classificação de intenção
  intent_router.py        — despacho por intenção
  handlers/               — handlers por domínio do fluxo de mensagem
  services/               — e-mail, Pluggy, planos, push, agentes, agendadores,
                            OFX, PIX, categorias, cartão… (quantos: `ls
                            core/services/*.py | wc -l`; o número que estava
                            aqui dizia 35 e envelheceu no PR seguinte — §2)
  reports/                — relatório diário (reports_daily.py)
  crypto.py, audit.py     — PII cifrada e trilha de auditoria

api/v2/                   — a /api/v2 do dashboard v2: sub-app FastAPI montado pelo
                            monólito em /api/v2, com envelope de erro próprio
                            (erros.py), a dependência única do usuário (sessao.py)
                            e um router por assunto (me.py, eventos.py)

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
`open_finance`, `push`, `simulator`.

`POST /simulator/{user_id}` (`frontend/routes/simulator.py`) é o simulador de compra
do Pro: 1 a 3 cenários (à vista, parcelado, financiado pela tabela Price) comparados
com o atual sobre a mesma leitura da previsão de saldo, 90 dias + resumo do contrato.
O pior dia e a reserva incluem hoje após a compra e os 90 dias seguintes (91 datas);
o saldo final continua sendo o do dia 90. A tool avisa antes dos números se os
bancos conectados estiverem excluídos ou o saldo consolidado não for confirmado.
Preço, entrada, custos, despesa mensal nova e reserva aceitam no máximo duas casas
decimais; frações de centavo são recusadas na validação comum da API e da tool.
Taxas percentuais mantêm precisão livre, inclusive valores muito pequenos.
Sem persistência e sem tela ainda; lógica em `core/services/decision_simulator.py`.

**Rota nova vai para um router de `frontend/routes/`**, não para o monólito — exceto
rota da `/api/v2`, que vai para `api/v2/` (ver "API v2" abaixo). Ao procurar uma rota
existente, procure nos dois lugares:

```bash
grep -rn '@\(app\|router\)\.\(get\|post\|put\|patch\|delete\)("/caminho' --include="*.py" frontend/ adapters/
```

São ~198 rotas. Os grupos maiores: `/auth` (27), `/open-finance` (11), `/settings`
(10), `/billing` (8), `/agents` (7), `/cards`, `/categories`, `/pockets`,
`/recurring-bills` (6 cada), `/analytics` (6), `/investments`, `/installments`,
`/recurring-incomes`, `/recurring-expenses` (5 cada), `/budgets` (4).

### API v2 (`api/v2/`)

Sub-app FastAPI (`api/v2/app.py`) montado pelo monólito com `app.mount("/api/v2", ...)`.
É o backend do dashboard v2 (`docs/plano-dashboard-v2.md`, §3). Regras, presas por
`tests/test_api_v2_rotas.py`:

- **Nenhuma rota recebe `user_id`** — nem path, query, header, cookie ou corpo. O
  usuário vem da dependência `usuario_atual` (`api/v2/sessao.py`): sessão (Bearer ou
  cookie `dashboard_token`) → conta agendada para exclusão (403) → gate de plano
  (`_enforce_subscription_gate`, 402) → chave `dashboard_v2_enabled` (404
  `dashboard_v2_disabled`). O user agent não entra.
- Toda rota tem `response_model` (a de SSE, o tipo do item do stream; ver `/eventos`).
- **Erro** sai no envelope `{"error": {"code", "message", "details"?}}`
  (`api/v2/erros.py`), com os headers da exceção preservados (`WWW-Authenticate` do
  401, `Allow` do 405). A exceção não tratada sai 500 `internal_error` ou, se for
  timeout/queda de conexão de banco, 503 `service_unavailable` — a regra é a
  `status_do_erro` (`core/admin_dashboard.py`), a mesma do pai — e registra
  `log_system_event` ali mesmo: o `admin_error_logging_middleware` do pai não enxerga
  exceção que o sub-app já respondeu. `ClientDisconnect` é levantada de novo para o
  pai, que responde 499 sem evento. O `ServerErrorMiddleware` do sub-app re-levanta a
  exceção depois de responder; o `erros.sem_reraise`, por fora dele (`_AppV2` em
  `api/v2/app.py`), a engole quando a resposta já começou, e o `TestClient` padrão não
  a vê. Erro no meio de um stream SSE chega num `ExceptionGroup` e é desembrulhado
  antes de classificar. Ficam **fora** do envelope o 403 do CSRF e o 422 do
  `query_venenosa_middleware`, que nascem nos middlewares do pai e saem `{"detail": ...}`.
- `GET /api/v2/me` devolve `{"plan_tier": "free"|"essencial"|"plus"|"pro"}`, sem PII.
- `GET /api/v2/eventos` (`api/v2/eventos.py`): SSE, `data: {"recurso": "open_finance"|"tudo"}`
  (sem dado financeiro, sem id, sem `id:`/replay) e `: ping` a cada 15 s. Quem avisa chama
  `eventos.avisar(user_id, recurso)` na thread do loop, depois do commit; hoje são o fim
  do sync do Open Finance e o "Recomeçar do zero". `usuario_atual` roda de novo antes de
  cada envio e a cada 30 s: sessão ou plano caídos fecham o stream sem aviso. Teto de 5
  streams por usuário (429 no envelope). Rota SSE tipa o item pela anotação de retorno
  (`-> AsyncIterable[Aviso]`), e a varredura aceita isso no lugar do `response_model`. O
  cliente (`webapp/src/dashboard/lib/eventos.ts`) invalida todas as consultas a cada
  aviso e a cada conexão aberta.
- `GET /api/v2/assinaturas` e `POST /api/v2/assinaturas/marca` (`api/v2/assinaturas.py`):
  a lista do Recurring Payments da Pluggy (`core/services/assinaturas.py`) e a marcação
  do usuário por chave do comerciante (`assinatura`/`ignorar`/`nenhuma`; chave fora da
  lista dá 404). Gate `subscriptions` em `FEATURE_MIN_TIER_V2` (Plus ou Pro).
- **Contrato:** o envelope entra no OpenAPI como resposta `default` (`ErroV2`, em
  `api/v2/erros.py`; a resposta real continua saindo de `_envelope`). Os tipos TS saem de
  `python scripts/gerar_tipos_api_v2.py` para `webapp/src/dashboard/lib/api-v2.gen.ts`
  (gerado e commitado; construção fora da lista aceita levanta `ValueError`), e
  `tests/test_api_v2_contrato.py` compara o arquivo com o `openapi()` de hoje e valida as
  fixtures dos testes de navegador (`tests/frontend/api_v2_respostas.json`). Mudou modelo:
  rode o gerador e depois o build do `webapp/`.
- Chave: `DASHBOARD_V2_BETA_EMAILS` (sem a env = os e-mails de teste do beta de
  Agentes; definida e vazia = ninguém) e `DASHBOARD_V2_BETA_USER_IDS`.
- A página é `/painel` (`frontend/painel.html` + o artefato `frontend/dashboard-app.*`,
  de `webapp/src/dashboard`): sessão por `auth_token` ou `dashboard_token`
  (`_resolve_page_user_id`), senão `/login?next=/painel`; UA do app ou fora da chave
  (ou a chave falhando) vai para `/app`; depois os gates de plano e onboarding do `/app`.
  O `/auth/me` devolve `dashboard_v2_enabled`, que revela o link no menu do `/app`
  (fora do app). O `/painel` carrega o `/static/auth-refresh.js` antes do bundle: o 401
  de autenticação da `/api/v2` (só aceita `dashboard_token`/Bearer) é renovado e repetido
  por ele.
- **Erro no cliente** (`webapp/src/dashboard/parts/Entrada.tsx`): nada do painel monta
  antes do `/me`; qualquer erro é uma tela só, com texto fixo em português (a `message`
  do envelope não vai para a tela: em 402/404 ela sai em inglês), Recarregar e "Painel
  antigo", **sem redirecionamento no cliente** — o Recarregar passa pelo `serve_painel`, que já manda cada
  caso ao lugar certo. Rede e 5xx tentam 3 vezes (com `networkMode: "always"`, para o evento `offline`
  não pausar o `/me` em "Carregando…"); 4xx (inclusive 429) nunca repete. Limite
  conhecido: conta agendada para exclusão leva 403 da `/api/v2` e o Recarregar serve a
  mesma tela, porque o `serve_painel` não barra exclusão (herdado do #659; o `/app`
  também não) — a única saída visível é o "Painel antigo".

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
dígitos) → `login`; **quiz de venda**: o webhook `POST /xquiz/webhook` (fora de
`/auth`, token `XQUIZ_WEBHOOK_TOKEN`) grava a verificação SEM senha e manda o código,
e a `/q` chama o mesmo `verify-email` depois de o usuário confirmar o e-mail na tela
(`quiz/resend` reenvia; `frontend/routes/quiz_signup.py`); `forgot-password`/`reset-password`; **Google OAuth**
(`google/start`, `google/callback`, `google/complete-signup`, `google/pending/{token}`,
e `google/exchange`, que troca por Bearer o código que o callback devolve ao app nativo
quando o login começa em `google/start?app=2`); **Apple**, só no app nativo iOS
(`apple/exchange`, que verifica o identity token pelo JWKS da Apple e devolve sessão,
desafio de MFA ou cadastro pendente, e `apple/complete-signup`; o pendente mora na
mesma `pending_google_signups`, com `provider='apple'`);
`dashboard-link`/`dashboard-token` (link mágico); `link-code` (vincula WhatsApp e
Discord à conta); `logout`; `refresh`; `account` (exclusão) e `account/export`.

**Conta pela `/assinar` (funil v3 do quiz): `POST /auth/quiz/conta`**
(`frontend/routes/quiz_signup.py`, com CSRF). Recebe e-mail, nome, WhatsApp
(obrigatório) e o aceite dos termos, e cria a conta **sem senha e sem código** na
mesma requisição (`db/signup_quiz.criar_conta_sem_codigo`, que NÃO passa por
`email_verification_codes`), já logada. Responde `criada`, `logado` (a sessão do
pedido já é dessa conta), `tem_conta`, `cadastro_pendente` (há código de
`/auth/register` vivo para o e-mail: alguém está no meio do cadastro, e o código dele
não é tocado) ou `ocupado` (409: outro pedido do mesmo e-mail está com a trava; a rota
não espera, para uma rajada não segurar o pool de conexões). Só `criada` escreve e dá
sessão. É o único lugar do site que diz se um e-mail tem conta (aceito pelo dono), com
10/h por IP (balde `quiz`) e 3/h por e-mail (balde `quiz-conta`, separado do
`register` para o anônimo não gastar o teto do cadastro da vítima). A prova do e-mail vem depois, no "Crie sua senha".

**Conta sem credencial: 403 `password_required`.** Conta sem senha (`''` conta como sem)
e sem identidade Google/Apple (`db.conta_sem_credencial`, a fonte única, a mesma que o
job do e-book usa; sem linha em `auth_accounts`, o só-WhatsApp, é False) não lê nem grava dado, mesmo
paga, até criar a senha pelo link do e-mail. Vale no servidor: a perna da credencial do
`_enforce_subscription_gate` (depois das duas do 402; não lê `ACCESS_GATE_ENABLED` nem
`PLANS_V2_ENABLED`, porque é segurança e não cobrança) e `shared.exigir_credencial` nos
pontos fora dele; o `/ws` fecha com 4403, o `/conta` manda para a `/home`, e o bot não
liga o número pelo telefone (responde com o texto fixo). O bot também barra toda
mensagem de número já ligado a conta sem credencial; no auto-vínculo, remetente que já
tem dados financeiros segue na própria conta, sem vínculo nem mescla
(`remetente_com_dados`); o vazamento da mescla por telefone digitado está na #711.
A exceção do bot são os botões de opt-out de `_WA_INTERACTIVE_ISENTOS` (relatórios diário,
semanal e mensal, e atualizações): quem não pode usar tem de conseguir parar de receber
mensagem, então eles funcionam no número já ligado, no `precisa_senha` (desligam a
preferência da conta sem credencial) e no `remetente_com_dados` (a do remetente e a da
conta que digitou o número), e nada além da preferência é gravado.
Saem livres as rotas da própria conta (`authorize_account_access`), o `PATCH /settings/{id}/security/contact`
(`exige_credencial=False`), o `/auth/me` (campo `precisa_criar_senha`), login,
logout, refresh e o reset. Quem bloqueia e quem libera, rota a rota, está em
`tests/test_rotas_senha_obrigatoria.py`, que reprova rota nova sem linha. Na tela, a
`/home` e o `/app` carregam `frontend/criar-senha.js`: overlay que não fecha, também
disparado por qualquer 403 `password_required`. A `/settings` não o carrega (é a saída),
e o convite do MFA fica calado no servidor enquanto não há credencial.

**Os três criadores de conta** (o `confirm` do register, o `complete-signup` do
Google/Apple e a `/assinar`) gravam pelo mesmo `db_support.inserir_conta_nova`:
trava por e-mail + `on conflict (email) do nothing`. O e-mail que ganhou conta no meio
é **recusado**, nunca fundido; o `verify-email` responde "Este e-mail já tem conta" e a
saída é o "Esqueci a senha". A sessão, as atribuições (afiliado, prospecção, quiz) e o
CAPI CompleteRegistration de conta nova moram num helper só, `_sessao_de_conta_nova`
no monólito, usado pelas três rotas.

### MFA

TOTP (`pyotp`) com códigos de backup: `/auth/mfa/setup`, `enable`, `disable`,
`verify-login`, `status`, `regenerate-backup-codes`, `onboarding-seen`. Tabelas
`user_mfa`, `user_mfa_backup_codes`, `mfa_login_challenges`; segredo cifrado com
`MFA_ENCRYPTION_KEY`.

**Os códigos de backup aparecem uma única vez.** Qualquer tela ou refresh que passe
por cima deles perde os códigos do usuário — já quase aconteceu (registro no §4 do
`CLAUDE.md` da raiz).

### WebSocket

`ConnectionManager` + endpoint `@app.websocket("/ws/{user_id}")` no monólito. O
dashboard pede dados por ele (pergunta e resposta); empurrar algo sem o cliente pedir
só acontece em `open_finance_synced`, do fim do sync do Open Finance e do "Recomeçar do
zero" — confira com `grep -rn "broadcast_to_user(" --include="*.py" frontend/ core/`. Os
mesmos 2 avisos também saem pelo `/api/v2/eventos` (`eventos.avisar`).
**Lançamento feito pelo WhatsApp não avisa o dashboard.** Mudou o formato
de mensagem? Os dois lados mudam junto — o consumidor está no `dashboard.js`.

### Pagamentos

Stripe: `/billing/create-checkout`, `webhook`, `portal`, `subscription`,
`change-plan`, `cancel-change`, `plans-config` e `select-free` (esta só RECUSA
com 410: a escolha do plano Grátis saiu da /precos em 2026-09-02; a rota
sobrevive pra devolver `detail.message` a cliente antigo em cache).

**`/billing/create-checkout` serve a `/precos` e a `/assinar`.** O corpo ganha
`origem` (`"precos"` default | `"assinar"`; outro valor é 400) e `embutido` (default
`false`). Hospedado responde `{checkout_url, interval, plan}`; embutido responde
`{client_secret, publishable_key, trial_days, interval, plan}` (`ui_mode="embedded_page"`,
`return_url` = a mesma URL de sucesso do hospedado). O `session_id`
nunca vai no corpo. A sessão grava `origem` e `td` (dias de trial) no metadata e no
da assinatura; uma sessão aberta só é reaproveitada pelo mesmo plano × intervalo ×
origem × modo (sessão sem `origem` = `/precos`), e a embutida reaproveitada devolve o
trial com que nasceu (`td`). Só a `/assinar` fixa BRL (`adaptive_pricing` off), volta
para `/assinar?plano=&ciclo=` no abandono e oferece o e-book (`optional_items`); a
`/precos` segue com os kwargs de antes. Toda sessão da `/assinar` (embutida **e**
hospedada), e todo embutido, expira em 1 h (`expires_at`): o default de 24 h do Stripe
deixaria aberta a janela de cobrança dupla (Pix numa aba, cartão na outra); o
hospedado da `/precos` segue sem. Envs:
`STRIPE_PUBLISHABLE_KEY` (sem ela o embutido é 503, antes de tocar no Stripe),
`STRIPE_PRICE_ID_EBOOK` e `EBOOK_URL` — o e-book só é oferecido com **as duas**
preenchidas (preço sem URL venderia o que o webhook não tem como entregar). Quando
oferecido, a sessão grava `ebook_price` e `ebook_url` (o preço e a URL do e-book no
nascimento) no metadata e no da assinatura; sem e-book as chaves não existem. O webhook
identifica o e-book por essa foto, nunca pela env do momento. `EBOOK_URL` tem no máximo
**500 caracteres** (limite de metadata do Stripe, medido): acima disso o e-book não é
oferecido e sai o warning `ebook_nao_oferecido` (com o tamanho, **nunca a URL** — ela é
o acesso ao PDF pago). As duas envs só entram em produção **depois do merge do #708**.

**Entrega do e-book (#708).** O `checkout.session.completed` com `ebook_price` grava
uma linha em `ebook_entregas` (`db/ebook_entregas.py`, PK `user_id + session_id`, com a
foto) logo depois do grant e ANTES dos outros efeitos, sem try: falha → 5xx e a
reentrega refaz tudo. Sessão sem a foto `ebook_url` grava assim mesmo e loga
`ebook_sem_url`. Quem entrega é o job `_ebook_worker` (abaixo, "Tarefas de fundo"):
só envia com `not conta_sem_credencial(uid)` (`db/google_auth.py`: senha não vazia ou
identidade Google/Apple — a prova do e-mail; sem linha em `auth_accounts` a função dá
False, e o job não envia porque não acha e-mail), confirma a compra pelo
`checkout.Session.list_line_items` (senão fecha `nao_comprou`), manda
`send_ebook_email` para o e-mail ATUAL da conta e fecha `enviado` na linha. O claim
(`reivindicada_ate`, 10 min dobrando a cada tentativa até 1 dia, contadas em
`tentativas`; a linha nunca fecha sozinha) não segura transação durante o Stripe/Resend; entrega é
"pelo menos uma vez". A tabela fica fora do export LGPD e sai com a conta (cascade).

**E-mail trocado chega ao Stripe (PR 4b).** A `PATCH /settings/{uid}/security/contact`
que troca o e-mail de conta com `stripe_customer_id` grava, na MESMA transação, uma linha
em `stripe_email_pendente` (`db/stripe_email_pendente.py`, PK `user_id`, só `versao` —
sem PII; troca de novo sobe a versão). O job `_stripe_email_worker` manda
`stripe.Customer.modify(email=<e-mail ATUAL da conta>)` e apaga a linha só se a versão
não mudou durante o envio. A troca no app nunca é desfeita: falha transitória espera o
claim (mesma régua do e-book); `InvalidRequestError` (cliente apagado, e-mail recusado)
fecha e loga `stripe_email_sync_recusado`, sem o e-mail. Fora do export LGPD; cascade.

**Fatura com e-book:** no `invoice.paid`/`payment_succeeded`, `amount_cents` é só o
plano: `amount_paid` menos o líquido das linhas cujo `pricing.price_details.price` é
o `ebook_price` da metadata da assinatura (`amount` da linha é BRUTO; o cupom vem em
`discount_amounts`). É esse valor que vai para o e-mail de cobrança, a comissão de
afiliado e o rastreio da fatura — a 1ª fatura de trial + e-book dá 0 e pula os três
(a comissão, que só paga a 1ª fatura paga, fica para a do plano).

**Rastreio do checkout:** o Meta `Purchase` sem trial leva o `amount_total` da sessão
(com cupom e e-book — o mesmo número do GA4; sem o campo, cai no `unit_amount`). Com
trial e `amount_total > 0` (o e-book), saem um Meta `Purchase` e um GA4 `purchase`
server-only com id `ebook_<sid>` (`meta_capi.ebook_event_id`) e item `ebook`; o
`StartTrial` não muda.

A **escada de planos é `free < essencial < plus < pro`**, atrás do flag
`PLANS_V2_ENABLED` (lido dinamicamente, sem redeploy; `0`/`false` é freio de
emergência e colapsa no binário legado). **A fonte de verdade é
`core/services/plan_service.py`** — não duplique a tabela de tiers, limites ou nomes
em outro lugar (§0.7 da raiz). Limites por plano em `core/services/plan_limits.py`.

**Inadimplência de cartão** (`core/services/billing_dunning.py`): a coluna
`auth_accounts.past_due_since` guarda a **primeira falha de cobrança do ciclo**,
carimbada pelo webhook `invoice.payment_failed` (`db.dunning.claim_past_due_since`,
idempotente no SQL **e condicionada ao status atual**) e zerada por
pagamento/cancelamento (`clear_past_due_since`, que os ramos `checkout` e
`invoice.paid` só chamam quando `_materializar_assinatura` disse que o evento
decidiu o acesso). Os três helpers moram em `db/dunning.py`, não em `db/plans.py`.
`DUNNING_GRACE_DAYS = 7` é a carência.

**Ninguém perde acesso POR INADIMPLÊNCIA** — o relógio só CONCEDE tempo. Desde
o corte do Grátis (#274/#354) existe gate de acesso, e a autoridade dele é o
DIREITO pago (`plan_service.has_app_access` → `tem_direito_hoje`), nunca o
status de cobrança. A coluna alimenta **três** coisas: o **lembrete de pagamento
do 6º dia**
(`core/services/payment_reminder.py`, no tick de `engagement_scheduler`; e-mail
sempre, WhatsApp só se `WA_TEMPLATE_PAYMENT_REMINDER` apontar para um template
aprovado na Meta — vazio por padrão → caminho dormente), a janela de dedupe do
e-mail de falha no webhook e o predicado `carencia_aberta`, lado DIREITO do OR
de `plan_service.tem_direito_hoje` (o relógio só CONCEDE tempo; a autoridade é
o direito pago), consumido pelo aviso de corte
(`scripts/aviso_fim_do_gratis.py`) **e pelo GATE DE ACESSO** — `has_app_access`,
e por ele os quatro enforcements (HTML, rotas de dados, WebSocket, bot) mais o
filtro dos relatórios proativos. Freio de emergência do gate:
`ACCESS_GATE_ENABLED=0`, gêmeo exato do `PLANS_V2_ENABLED`. O lembrete fica atrás de `PAYMENT_REMINDER_ENABLED`
(**default off**, lida a cada tick, sem redeploy; a guarda é a 1ª linha de
`check_payment_reminder`, então desligada nem consulta o funil). Grant
`pix`/`admin` vigente pula o lembrete (`legacy` não). **A copy do LEMBRETE
continua proibida de prometer pausa ou perda de acesso** — ele sai no 6º dia,
dentro da carência, com o acesso ainda de pé. Quem PODE falar em perda são o
e-mail de falha e o de cancelamento, reescritos no PR do corte (#354).

**A INVARIANTE**: `past_due_since` não nulo só existe em conta com
`last_payment_status` em `PAST_DUE_PAYMENT_STATUSES`. Ela é mantida na ESCRITA,
e os dois writers da coluna de status são `db_support.set_payment_status_impl` e
o SQL cru de `core/admin_dashboard.set_account_plan` — mexeu num, leia o outro.

**A máquina inteira está enumerada em `docs/dunning_estados_eventos.md`**:
estados (o par relógio × status) × eventos (os quatro webhooks de cobrança, o
`recompute_entitlement`, o `set_account_plan` e o tick do lembrete) × validade
do evento (novo / reentrega / velho), com o que cada célula faz hoje, o que
deveria fazer, e as células deixadas abertas de propósito. **Leia antes de
tocar em qualquer writer do relógio** — o subsistema levou VÁRIAS rodadas de
revisão porque cada conserto foi feito como transição isolada, e a tabela existe
para a próxima não repetir o método (raiz §4, registro do PR #60). Quais rodadas
apontaram o quê está na coluna "quem achou" da tabela do fim daquele arquivo; a
contagem não vive aqui de propósito, porque ela sobe a cada rodada (§2).

### Open Finance

Via **Pluggy**. Endpoints em `frontend/routes/open_finance.py`
(`/open-finance/{user_id}` e `connect-token`, `connectors`, `sync`, `refresh`,
`pluggy-item`, `caixinhas`, `caixinhas/bind`, `mock-connect`) mais o webhook
`/open-finance/pluggy/webhook`. Serviços em `core/services/pluggy*.py` e
`open_finance*.py`; tabelas `open_finance_connections/accounts/transactions/investments`,
`open_finance_investment_snapshots` (foto diária por posição, `db/of_snapshots.py`) e
`open_finance_item_registry` — o rastro de todo item que passou por aqui, inclusive o
que nunca virou conexão (token emitido e abandonado, webhook de item desconhecido); o
`GET /items` da Pluggy devolve 401, então sem ela o universo remoto não é enumerável;
ela guarda também a marca de remoção deliberada (`origin='removed'`), escrita na mesma
transação do delete pelo disconnect e pelo reset.

Assinaturas vêm do **Recurring Payments** da Pluggy (`db/of_recurring.py`):
`of_recurring_payments` guarda o resultado por conexão, substituído inteiro a cada
sync — falha na Pluggy mantém o anterior; `subscription_marks` guarda a marcação do
usuário por `merchant_key` (vale para todos os itens da chave).
`open_finance_connections.recurring_fetched_at` e `recurring_seed_silent` controlam o
silêncio da 1ª busca do Detetive numa conexão que já existia: as chaves dela — a foto
guardada em `recurring_seed_descricoes`, não a atual — viram lápide por `record_agent_event(silencioso=True)`, que grava o evento já com
`stale_at` (não aparece no feed nem vai por e-mail).

Boa parte do comportamento é regida por flags `OF_*` (beta por e-mail/user_id, limite
de bancos no free, refresh proativo). Antes de mexer, leia as flags — o
comportamento em produção pode não ser o do seu ambiente.

### IA

`ai_router.py` chama a OpenAI (`OPENAI_MODEL`, default `gpt-4o-mini`) como fallback da
categorização determinística. Há rate limiting próprio (`core/ai_rate_limiter.py`),
limite mensal de chat (`AI_CHAT_MONTHLY_LIMIT`), chat "Piggy" no dashboard
(`core/services/ai_chat/`) e agentes proativos (`core/services/piggy_agents.py`,
atrás de `AGENTS_ENABLED` + listas de beta).
A tool `simulate_purchase` (`core/services/ai_chat/tools/simulator.py`) usa o mesmo
simulador e a mesma validação da rota `/simulator`, com gate soft de Pro.

Categorização tem uma armadilha própria: **categoria e regra de categoria são tabelas
diferentes** (`user_categories` × `user_category_rules`) e a regra ganha da categoria
na inferência.

### E-mail

**Resend** (`RESEND_API_KEY`), em `core/services/email_service.py` — **não é mais
SMTP/Gmail**. Além dos transacionais (verificação, boas-vindas, reset), há e-mails de
ciclo de vida (reengajamento, downsell de trial, relatório de
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

A segunda escrita do drill-down libera novo trial
(`POST /admin/api/users/{id}/trial-reset` → `db.plans.reset_trial_for_user`):
apaga a linha de `plan_trials` do **telefone** da conta e zera
`trial_started_at`/`trial_downsell_sent_at`. **Também não fala com a Stripe** —
por isso recusa com 409 quando `last_payment_status` é `trialing|active|past_due`.

### Tarefas de fundo

Sobem no startup do app quando `RUN_BACKGROUND_TASKS != "0"`: rendimento de
investimento, Open Finance (abaixo), contas a pagar dos recorrentes, agendadores de
engajamento e de IA proativa, retenção de eventos de login, poda das tabelas de
refresh token / challenge de MFA / cadastro Google pendente
(`core/services/table_cleanup.py`), e a entrega do e-book da `/assinar`
(`_ebook_worker` → `core/services/ebook_entrega.entregar_pendentes`, a cada 5 min, a
1ª volta sem delay; inerte sem `STRIPE_SECRET_KEY` no ambiente), o e-mail trocado em
`/settings` levado ao cliente do Stripe (`_stripe_email_worker` →
`core/services/stripe_email_sync.sincronizar_pendentes`, mesma cadência e mesma guarda da
chave), e a foto diária do
patrimônio (`_patrimonio_foto` → `core/services/patrimonio_foto.py`, a cada hora, a partir
das 18h do fuso do app, uma por usuário com acesso por dia em `patrimonio_fotos`; atrás de
`PATRIMONIO_FOTO_ENABLED`, desligada por padrão e lida a cada volta — desligada, não
consulta nada). Ficam desligadas só onde
`RUN_BACKGROUND_TASKS=0` é forçado: `dashboard_dev.py` e
`scripts/whatsapp_qa_vault_harness.py`. O `tests/conftest.py` **não** força, então
teste que sobe o `app` herda o default (`1`) — `tests/test_table_cleanup.py` passa
`"1"` de propósito, para ver a tarefa subir.

O Open Finance tem **quatro** trabalhos: expiração de trial
(`_open_finance_trial_expiry`), **job de saúde**, **retentativa** e refresh proativo —
os três últimos no mesmo tick de `_open_finance_refresh`, nessa ordem. O 1º tick
roda 10 min depois do boot (`_PRIMEIRO_TIQUE_SEC`) só com saúde e retentativa (GET); o
PATCH periódico não roda no boot e entra do 2º tick em diante, a cada
`OF_REFRESH_INTERVAL_SEC` (6 h). O refresh
proativo depende de `OF_REFRESH_ENABLED` (off por padrão em produção); o job de saúde
roda MESMO com ele desligado e ESCREVE `status`/`status_reason`/`health` na conexão do
usuário. É de propósito: ele só faz `GET /items` (não consome cota de coleta) e é o que
tira do "Atualizado" a conexão cujo item sumiu da Pluggy — sem refresh e sem webhook,
nada mais faria essa verificação. A retentativa (Onda 5, PR-B2,
`frontend/routes/of_retentativa.py`) vem logo depois: relê a Pluggy, também só com GET,
para até `OF_RETRY_MAX_PER_TICK` conexões com dado atrás (default 20; só `0` ou
negativo desliga só ela, valor que não é inteiro cai no padrão), uma de cada vez, pelo mesmo caminho de sync do webhook. Quem entra e por quê:
`docs/open_finance_estados.md` §2.2. Kill switch dos dois: `OF_HEALTH_CHECK_ENABLED=0`
(default `1`).
(Há ainda `_open_finance_proactive`, que retorna na hora sem `OF_PROACTIVE_ENABLED`.)

---

## Frontend

O detalhamento das armadilhas está no **§5 do `CLAUDE.md` da raiz** (é lá que ele
mora; não duplicar aqui). O essencial de domínio:

- **O site é HTML/CSS/JS à mão, com ilhas React delimitadas.** O
  `package.json` da raiz continua servindo só ao harness de testes de frontend —
  e continua **sem script
  `build`** de propósito: é a ausência dele que mantém a detecção automática do
  Railway apontando para o Python. O build de JS que existe é o de `webapp/`,
  projeto npm separado, com package.json e lockfile próprios.
- **Páginas públicas e área logada são as duas MPA.** Existe um POC de navegação
  client-side (`pb-nav.js`) **desligado por padrão**, restrito ao modo app e a duas
  rotas. Não trate a área logada como SPA: a migração para React que existe é por
  ILHA (abaixo, em "Decisões tomadas") e não alcança a navegação.
- **`dashboard.js` tem 10.587 linhas e 414 funções globais**, e `dashboard.html` tem
  139 handlers `onclick=` que dependem disso. Funcionalidade nova de dashboard deve
  nascer em arquivo próprio (§0.5 da raiz), com rota própria em `static_pages.py`.
- **Segurança de borda** (medida em produção): CSP com allowlist explícita
  (`cdnjs`, `jsdelivr`, `cdn.pluggy.ai`, `connect.facebook.net`,
  `static.cloudflareinsights.com`; o Stripe em `script-src` — `js.stripe.com`,
  `*.js.stripe.com`, `checkout.stripe.com` — e em `frame-src` — os mesmos mais
  `hooks.stripe.com` —, para o checkout embutido da `/assinar`), HSTS, `X-Frame-Options: DENY`,
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
| Discord | fora do `launch.py` desde o PR 5a do dashboard v2: o código segue e não roda | `adapters/discord/`, `bot.py` |
| OpenAI | categorização, chat, agentes | `ai_router.py`, `core/services/ai_chat/` |
| Stripe | assinaturas | billing no monólito |
| Pluggy | Open Finance | `core/services/pluggy*.py` |
| Resend | e-mail transacional e de ciclo de vida | `core/services/email_service.py` |
| APNs | push do app iOS | `core/services/push_service.py` |
| Meta Pixel / CAPI | marketing (só páginas públicas) | `inject_tracking`, `core/services/meta_capi.py` |
| Google Analytics 4 | medição de funil (mesmas páginas do pixel) | `ga4_snippet`/`inject_tracking`; eventos ao lado de cada `fbq` |
| GA4 Measurement Protocol | receita server-side (compra, fim do trial, renovação) | `core/services/ga4_mp.py`, no webhook do Stripe |

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
`META_PIXEL_ID` · `RUN_BACKGROUND_TASKS`/`SKIP_INIT_DB`/`ENABLE_DEV_ENDPOINTS` ·
`ACCOUNT_DELETION_JOB_LIMIT`/`TABLE_CLEANUP_INTERVAL_HOURS` (os dois kill switches
de job que apaga linha; `TABLE_CLEANUP_INTERVAL_HOURS=0` desliga a poda).

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
- **A migração para React COMEÇOU, e a decisão está tomada.** O padrão é **ilha**,
  não SPA: Vite + React 19 em `webapp/` (o único projeto de build do frontend),
  bundles IIFE de escopo delimitado, saída de nome FIXO e sem hash em `frontend/`,
  **artefato commitado** —
  porque não há `StaticFiles` mount e cada asset precisa de rota escrita à mão.
  As ilhas de preços e dos chats convivem com os scripts clássicos. A convenção
  é IIFE, mount síncrono com `flushSync` e propriedade exclusiva do trecho
  renderizado pelo React: controladores publicam estado, sem alterar seus nós.
  Preços continuam vindo do markup, evitando duplicar regras comerciais.
  A ilha de preços usa CSS comum e os tokens da página. A integração dos chats,
  solicitada explicitamente pelo usuário, usa TypeScript e Tailwind 3.4 com
  prefixo, sem Preflight e com processamento de CSS exclusivo dessa ilha;
  [a decisão](adr/0001-interface-compartilhada-dos-chats.md) registra o alcance.
  O piloto público de `/como-funciona` também está decidido, mas ainda não
  implementado: terá mount exclusivo, fallback no markup legado e entrará no
  mesmo `webapp/`, build, gate e deploy; ver o
  [ADR 0002](adr/0002-piloto-como-funciona-como-ilha-react.md).
  Ao alterar o build, preserve o alvo Safari 14 nos artefatos JS e CSS e
  `emptyOutDir: false`: o destino é o diretório do site.
  A ilha do v2 (`/painel`) busca dados com **TanStack Query v5**. O alvo safari14 só
  rebaixa sintaxe (os `this.#x` viram WeakMap), não faz polyfill de API: por isso
  `tests/frontend/dashboard_v2_safari14.test.mjs` varre o `dashboard-app.js` commitado
  atrás das APIs que o Safari 14 não tem (`.at(`, `structuredClone`, `Object.hasOwn(`,
  `WeakRef`, `static{`, `this.#` e outras) e monta o `/painel` no Chromium com elas
  apagadas.
  O gate do CI recompila `webapp/` e exige artefatos idênticos aos commitados.
  Dependências novas exigem rebuild e inclusão dos artefatos afetados no commit.
  **O que isto NÃO autoriza:** transformar a área logada em SPA, adicionar
  framework em página nova por gosto, criar outro projeto/pipeline de build ou
  deploy, ou pôr script `build` na raiz.
