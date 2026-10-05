# Plano — teto de tamanho e prazo de leitura do corpo das requisições

> **Status:** aprovado pelo dono em 2026-10-03, com a decisão **webhook acima do teto
> responde 413**. Faixa **Completo** (segurança, webhooks públicos, área compartilhada).
> Implementado e testado no working tree em 2026-10-03, ainda sem commit nem PR (o plano
> abaixo é o aprovado; o que mudou na execução está nos testes). Números de linha são de
> `origin/main` em 2026-10-03 (cb14323b) e envelhecem: remeça com `git grep` antes de usar.

## Objetivo

Um middleware ASGI puro que limita o **tamanho** do corpo (413) e o **tempo** para
recebê-lo (408) em toda rota HTTP, em três faixas de limite, sem mudar nenhum handler.
Hoje nada limita: `launch.py` só faz `execv` do uvicorn, sem flag de corpo, e o uvicorn
0.52.4 não tem teto nem prazo de corpo (lido no `.venv`, não medido rodando).
`timeout_keep_alive` só vale entre requisições.

## Por que "exige login" não protege

O FastAPI lê o corpo tipado (`BaseModel`, `Form`, `UploadFile`) **antes** de resolver as
dependências e antes do handler. Um anônimo com `Content-Type: application/json` passa
pelo CSRF e enche a memória em qualquer rota com corpo tipado (cerca de 68 de 124
decorators de escrita; contagem por regex, remeça). Por isso um helper por rota não
resolve: só um middleware cobre essas rotas.

## Inventário (corpo lido à mão)

| arquivo:linha | rota | autenticação antes de ler | faixa |
|---|---|---|---|
| `adapters/whatsapp/wa_app.py:251` | POST `/wa/webhook`, `/webhook` | nenhuma (HMAC depois) | webhook |
| `frontend/routes/open_finance.py:2248` | POST `/open-finance/pluggy/webhook` | nenhuma | webhook |
| `frontend/finance_bot_websocket_custom.py:5805` | POST `/billing/webhook` (Stripe) | nenhuma (assinatura depois) | webhook |
| `frontend/routes/billing_pix.py:251` | POST `/billing/asaas/webhook` | token no header, lido depois | webhook |
| `frontend/routes/quiz_signup.py:121` | POST `/xquiz/webhook` | nenhuma | webhook |
| `finance_bot_websocket_custom.py:8052/8071` | POST `/ofx/import/{id}` | sessão + Pro | OFX |
| `open_finance.py:1767` | POST `/open-finance/{id}/connect-token` | sessão | #774 (4096 B / 5 s) dispara antes |
| `open_finance.py:2419` | POST `/open-finance/{id}/mock-connect` | sessão, só dev/staging | padrão |
| `frontend/routes/simulator.py:29` | POST `/simulator/{id}` | sessão + plano | padrão |
| `finance_bot_websocket_custom.py:2514` | POST `/wa/dev/simulate` | só com `ENABLE_DEV_ENDPOINTS` | padrão |
| `core/admin_dashboard.py:424` (`_json_object_body`) | inclui POST `/admin/auth/login` (público) | nenhuma em 1722 | padrão |
| `core/admin_dashboard.py:2298/2335` | payouts de afiliado | admin | padrão |

Corpo tipado, lido pelo FastAPI antes de qualquer autenticação (na prática, público):
`/auth/register`, `/auth/verify-email`, `/auth/login`, `/auth/forgot-password`,
`/auth/reset-password`, `/auth/mfa/verify-login`, `/auth/google|apple/exchange` e
`complete-signup`, `/contact`, `/auth/quiz/resend`, `/auth/quiz/conta`,
`/api/prospect/status`, mais as rotas com sessão (`/api/push/*`, `/onboarding/state`,
`/billing/pix/checkout`, `/ai/chat`, `/api/v2/*`...). Todas na faixa **padrão**.

**Fora do escopo:** WebSocket `/ws/{user_id}` (exige cookie, 16 MiB padrão do uvicorn;
follow-up de um flag `--ws-max-size`); `adapters/discord/discord_bot.py:159` (anexo do
Discord, e o Discord está morto); `scripts/mock_dashboard.py` e `app/e2e/apoio.py`.
Não existe rota de upload de PDF na `main`: a única é a de OFX, que recusa `application/pdf`.

## Tetos (tamanho / prazo) e a fonte de cada número

| Faixa | Rotas | Teto | Fonte |
|---|---|---|---|
| padrão | todo o resto | **1 MiB / 30 s** | maior corpo legítimo: `AgentChatBody` (`frontend/routes/agents.py:266-267`, `message` ≤ 2000 e `context` ≤ 160000 caracteres; 6 B por caractere escapado dá ≈ 972.040 B). 1 MiB a 750 kbit/s = 11,2 s; 30 s = 2,7× de margem |
| webhook | os 6 caminhos exatos acima | **3 MiB / 10 s** | Meta documenta "Webhook payloads can be up to 3 MB"; Pluggy documenta que sem 2XX em 10 s conta falha. Para Stripe, Pluggy (`transactionIds` sem máximo documentado, ~39 B por id), Asaas e XQuiz o teto é **hipótese ancorada** no único número publicado |
| OFX | prefixo `/ofx/import/` | **`MAX_OFX_BYTES` + 64 KiB / 180 s** | `MAX_OFX_BYTES` = 8 MiB já existe (`:8038`) mas só é checado depois de `upload.read()`. A folga cobre o overhead do multipart (o Tester mede o real). 8,06 MiB a 750 kbit/s ("Slow 4G" do Lighthouse, `docs/throttling.md`) = 90 s; 2× = 180 s, abaixo dos 300 s do Railway |

Railway ("Specs & Limits", consultado em 2026-10-03): "Request bodies must finish
uploading within 5 minutes", "Max 32 KB combined header size"; limite de tamanho de corpo
não documentado.

## Mecanismo: `core/limite_corpo.py`

Arquivo novo ao lado de `core/secure_compare.py`; não importa `frontend/`.

- `MAX_OFX_BYTES = 8 * 1024 * 1024` **movido** do monólito para cá; o monólito, `ofx_import.py` e `ofx_credit_import.py` importam daqui (uma fonte só, §0.7; `test_importadores_de_ofx_usam_o_teto_daqui` cobra). O `MAX_STATEMENT_BYTES` de `statement_import.py` fica: é teto de anexo do WhatsApp, não de corpo HTTP.
- As três faixas e a função pura `limites_da_rota(path) -> (teto, prazo)`. Webhook = um
  `frozenset` de caminhos exatos; OFX = prefixo `/ofx/import/`. Cada número leva um
  comentário de uma linha com a fonte da tabela acima.
- `LimiteCorpoMiddleware`, ASGI puro (não `BaseHTTPMiddleware`):
  - atua só em `scope["type"] == "http"`; websocket e lifespan passam direto;
  - **caminho rápido:** `content-length` numérico acima do teto responde 413 já na
    primeira chamada a `receive`, sem chamar o `receive` de baixo (o uvicorn nem manda o
    `100 Continue`). Content-Length inválido não pode dar 500: segue pela contagem;
  - **contagem:** soma o `body` de cada `http.request`; `> teto` vira 413; exatamente o
    teto passa;
  - **prazo:** o relógio começa na **primeira** chamada a `receive` (handlers como
    connect-token leem o corpo só depois de ir ao banco). Cada `await receive()` roda em
    `asyncio.timeout(restante)`; `TimeoutError` vira 408;
  - **depois de `more_body=False`, passa direto, sem prazo**, senão os ouvintes de
    desconexão (BaseHTTPMiddleware, StreamingResponse) tomariam 408;
  - **resposta:** JSON `{"detail": "..."}` com `Cache-Control: no-store` e
    `Connection: close` (faz o uvicorn fechar em vez de continuar lendo). Sem ramo HTML;
  - **ao estourar:** envia o 413/408 pelo `send` original, devolve `http.disconnect` ao
    app, descarta todo `http.response.*` que o app tente enviar depois e engole a
    exceção do app só se já tiver respondido;
  - **log:** uma linha `info` (método, path, bytes lidos, motivo). Nunca `warning`: o
    `_DashboardHandler` faz INSERT por registro a partir de WARNING. Nada do corpo nem da
    query vai para o log.

**Por que responde ele mesmo.** Protótipo medido (Starlette 1.6.0, FastAPI 0.141.1):
levantar `HTTPException` dentro do `receive` NÃO funciona neste app. O
`BaseHTTPMiddleware` chama `receive` num task group do anyio, a exceção volta como
`ExceptionGroup`, o `ExceptionMiddleware` não a reconhece, e no app real cairia no
`admin_error_logging_middleware`: 500 mais um INSERT em `system_event_logs` por
requisição anônima.

**Registro:** `app.add_middleware(LimiteCorpoMiddleware)` em
`frontend/finance_bot_websocket_custom.py`, **logo depois de `app = FastAPI(...)` e antes
de `app.middleware("http")(admin_error_logging_middleware)`**. O primeiro registrado fica
mais interno, e assim os `BaseHTTPMiddleware` de fora (cabeçalhos de segurança, CORS) veem
uma resposta comum. O comentário no registro diz por quê (o `ExceptionGroup` medido).

**Não muda:** nenhum handler; o helper `_corpo_json_limitado` do #774 (outro contrato:
acima do teto vale "sem o campo", e `tests/test_of_connect_token_volta_app.py` prende
isso; o `asyncio.timeout` de 5 s, mais curto, dispara antes); os `except Exception` do
Asaas e do admin (o 413 agora sai mesmo assim); `launch.py`; CORS e CSRF. A checagem
pós-leitura do OFX (`MAX_OFX_BYTES`) **fica**, para a mensagem amigável "max 8 MB" entre
8 MiB e o teto do multipart.

## Ordem de implementação

1. `core/limite_corpo.py` + `tests/test_limite_corpo.py` (unitários).
2. Registro em `finance_bot_websocket_custom.py` e troca da constante local por import.
3. Testes ponta a ponta no `dashboard.app` real.
4. Rodar as regressões listadas abaixo e comparar com a baseline.

## Testes (`tests/test_limite_corpo.py`)

O Tester invoca a skill `baseline-testes` antes de qualquer `pytest`. Nenhum teste lê o
texto de um arquivo; rotas vêm de `app.routes`.

**A. Unitários** (app mínimo: FastAPI + dois `BaseHTTPMiddleware` externos, um deles com
`except Exception` que registra o que vê; chamadas ASGI cruas com `receive` falso e
`asyncio.wait_for(..., 2)`):
1. Content-Length acima do teto: 413, `connection: close`, **0 bytes puxados** do `receive`.
2. Chunked sem Content-Length, gerador **infinito** de pedaços de 64 KiB: 413, bytes
   puxados ≤ teto + 1 pedaço, termina em menos de 1 s.
3. Content-Length mentindo (declara 10, envia mais que o teto): 413.
4. Corpo exatamente no teto: 200. Teto + 1: 413.
5. Um pedaço e silêncio, prazo de teste 0,05 s: 408 em menos de 1 s, `connection: close`.
6. Pedaços lentos mas dentro do prazo: 200.
7. Handler lê o corpo e depois espera um `receive` mais longo que o prazo (o
   `http.disconnect` chega depois): **200, sem 408**.
8. Handler com `except Exception` que devolveria 400: sai 413.
9. A lista do `except Exception` externo fica **vazia** em todos os casos de estouro.
10. Scope `websocket` e `lifespan` passam intactos.
11. `content-length: abc` e `-1`: nenhum 500, a contagem continua valendo.
12. Tabela de `limites_da_rota`: `/wa/webhook` e `/webhook` caem em webhook; `/ofx/import/7`
    em OFX; `/ofx/importx` e `/auth/login` no padrão.
13. Invariante: `AgentChatBody` no pior caso (`max_length` lido de `model_fields`, 6 B por
    caractere, mais as chaves) ≤ teto padrão.
14. Todo caminho do conjunto de webhooks existe como rota POST em `dashboard.app`.

**B. Ponta a ponta no `dashboard.app` real** (TestClient e uma chamada ASGI crua):
- **Negativos** (têm de ficar **vermelhos** com a linha `add_middleware` comentada):
  - POST `/auth/login` anônimo com 1 MiB + 1: 413, com `x-frame-options` presente (prova a
    posição interna) e `connection: close`; sem o middleware sai 422 ou 401.
  - POST `/api/push/register` anônimo com 1 MiB + 1: 413 (prova que o corpo é lido antes
    da autenticação; sem o middleware sai 401).
  - `/wa/webhook` com 3 MiB + 1: 413.
  - `/billing/asaas/webhook` com token válido e mais de 3 MiB: 413, mesmo com o
    `except Exception`.
  - `/ofx/import/{uid}` com multipart acima de `MAX_OFX_BYTES + 64 KiB`: 413 com o
    `detail` do middleware.
  - chamada ASGI crua em `/wa/webhook`, um pedaço e silêncio, prazo monkeypatchado para
    0,05 s: 408 em menos de 1 s (sem o middleware: timeout do `wait_for`).
  - variante chunked (gerador, sem Content-Length) para `/auth/login`.
- **Positivos** (passam **com e sem** o middleware):
  - `/wa/webhook` assinado, corpo no formato Meta de **exatamente 3 MiB**, e outro de
    1,5 MiB: 200 (o de 1,5 MiB prova também o roteamento para a faixa webhook). Assinatura
    como em `tests/test_wa_webhook_signature.py`.
  - `/open-finance/pluggy/webhook` autorizado, `transactions/deleted` com cerca de 50 mil
    ids (~1,9 MiB): diferente de 413 (banco de teste, padrão de
    `tests/test_of_connection_state.py`).
  - `/billing/webhook` com 1,5 MiB e assinatura ruim: 400 "Assinatura inválida", não 413.
  - `/billing/asaas/webhook` com 1,5 MiB: não 413.
  - OFX com arquivo de exatamente `MAX_OFX_BYTES`: chega ao handler e não recebe o
    `detail` do middleware. O Tester mede o overhead real do multipart
    (`len(corpo httpx) − len(arquivo)`) e confirma que fica bem abaixo de 64 KiB.
  - `/auth/login` normal: mesmo status de antes.
- **Regressão** (verdes, comparados com a baseline):
  `tests/test_of_connect_token_volta_app.py`, `tests/test_wa_webhook_signature.py`,
  `tests/test_billing_webhook_lifecycle.py`, `tests/test_pix_rotas_billing.py`,
  `tests/test_quiz_signup.py`, `tests/test_ofx_import_route.py`,
  `tests/test_error_pages.py`, `tests/test_auth_corpo_venenoso.py`.
- **Medição opcional**, se o sandbox permitir abrir porta local: uvicorn em localhost e
  `curl -H 'Transfer-Encoding: chunked' --data-binary @/dev/zero` contra `/auth/login`,
  comparando o RSS com e sem o middleware. É a única prova com httptools de verdade.

Isolamento por usuário: o middleware não consulta banco nem toca dado de usuário.

## Riscos

- **Webhook legítimo acima do teto** vira 413 e o provedor reenvia (Meta por 7 dias,
  Stripe por 3, Pluggy 3 vezes; a Pluggy trata 413 e 408 como temporários). Para a Meta o
  teto é o máximo documentado; para Stripe, Pluggy, Asaas e XQuiz é hipótese e a produção
  não registra bytes de corpo. Depois do deploy, procurar a linha `info` do middleware nos
  caminhos de webhook.
- **Upload de OFX acima do teto:** com `Connection: close` e o navegador ainda enviando, o
  cliente pode ver reset de conexão em vez do 413, e o `dashboard.js:10786-10823` mostraria
  "Erro ao importar OFX. Tente novamente." em vez do `detail`. Mitigar no cliente com
  `file.size` é follow-up e exige teste comparando a constante JS com a Python (§0.7).
- Rota que lê o corpo já transmitindo a resposta: não existe hoje.
- `tests/test_error_pages.py:501` só confere o `ServerErrorMiddleware` externo; não deve
  quebrar.

## Não dá para verificar aqui

- Se o proxy do Railway bufferiza ou limita corpo antes do app, e como o cliente vê o 413
  com `Connection: close` atrás dele.
- O tamanho real dos maiores webhooks de Stripe, Pluggy, Asaas e XQuiz em produção.
- O comportamento do httptools com Content-Length mentindo (o TestClient não passa por ele).
- Se 10 s bastam para 3 MiB vindos da Meta (hipótese: pelo menos 2,5 Mbit/s entre
  datacenters).

## Fica de fora

`--ws-max-size` no `launch.py`; prazo de cabeçalho no uvicorn (`--timeout-*` não cobre);
página HTML para 413/408; checagem de tamanho do OFX no cliente; migrar o helper do #774.
