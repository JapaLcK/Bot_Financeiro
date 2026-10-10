# Plano: funil de remarketing (checkout abandonado — 30 min / 24 h / 48 h)

Faixa **Completo**: dinheiro, envio irreversível a pessoas reais, consentimento e uma
credencial que deixa comprar sem login. Versão 2 do plano do Arquiteto (2026-10-09),
depois das respostas P1–P5 do dono. O PR 1 (seção 4) está implementado; os demais não.

## Decisões do dono (2026-10-09)

1. Desconto de cada etapa: depois. A V1 manda só lembrete.
2. Relógio (T0) na primeira entrada na `/precos` sem concluir; deslogado: na criação da
   conta na `/assinar`. Anônimo sem contato fica só com o pixel da Meta.
3. Mensagens em 30 min, 24 h e 48 h.
4. Opt-in: frase sob o campo de WhatsApp — "Você vai receber mensagens do PigBank neste
   número. Responda PARAR para sair." Sem checkbox.
5. Pix gerado e não pago entra na régua.
6. O link deixa pagar pela conta sem login completo. Senha por e-mail depois.
- P1: WhatsApp só para o número digitado na `/assinar` depois de a frase entrar no ar.
  Risco de número errado levar a mensagem a um estranho: aceito.
- P2: quem já foi cliente (qualquer grant, trial, cortesia, legado) fica fora.
  Reconquista é outra campanha.
- P3: o link não abre plano: abre a `/precos`, onde a pessoa escolhe plano e forma de
  pagamento (cartão ou Pix).
- P4: o pagamento é a página própria do PigBank (`/assinar`, `ui_mode=elements`, com
  caixas de caderno, cupom e Apple/Google Pay), não o hospedado do Stripe.
- P5: e-mail em todas as etapas, mais WhatsApp quando possível.

## 1. Definições

**T0:** primeira entrada, gravada uma vez e nunca zerada, em `remarketing_regua`
(`insert … on conflict do nothing`). Logado na `/precos`: o GET, `origem='precos'`. Na `/assinar`: quando
`/auth/quiz/conta` responde `criada` (`origem='assinar'`, o número foi digitado com a frase de
opt-in) ou `logado` (a sessão já é da conta; grava `origem='precos'`, só e-mail, porque esse
ramo não salva o número digitado e o telefone guardado não passou pela frase, P1);
`tem_conta`, `cadastro_pendente` e `ocupado` não gravam. O T0 já gravado não é sobrescrito. Não deriva de `viewed_pricing` (quem vem do `/q`
direto à `/assinar` nunca viu a `/precos`).

**Abandonou:** T0 ≥ `REMARKETING_DESDE` e não "comprou". Pix gerado e não pago não é
compra; o job lê `pix_charges` direto.

**Comprou** — `ja_comprou(user_id)` em `db/remarketing.py` é a função e a ÚNICA porta, usada
pelo lote (por candidato), pelas releituras e pelo bot; o fragmento SQL dela é privado e
parcial. Qualquer um basta, todos por `user_id`:
- linha em `plan_grants` (qualquer fonte e status);
- `completed` em `checkout_funnel_events`;
- Pix com dinheiro recebido alguma vez em `pix_charges` (`paid`, `paid_orphan`, `refunded`,
  `refunded_partial`, `chargeback`; fora `draft`, `creating`, `pending`, `canceling`,
  `canceled`, `expired`), mesmo com a janela vencida e sem grant;
- plano pago gravado na conta, vigente ou vencido (a lista do `plan_service`);
- `last_payment_status` em `grandfathered`, `active`, `trialing`, `canceled`, `past_due`,
  `unpaid` (fora `incomplete`, `incomplete_expired` e `inactive`: tentativa sem pagamento
  é abandono, o público da régua);
- `plan_service.tem_direito_hoje` sobre as linhas de `auth_accounts` (plano vigente ou
  carência de inadimplência);
- `plan_service._ACCESS_ALLOWLIST`.

Exclusão agendada e conta sem `auth_accounts` NÃO estão em `ja_comprou`: entram no PR 6,
junto do `estado_fresco`.

| Etapa | Janela | Canais |
|---|---|---|
| E1 | `[T0+30min, T0+12h)` | e-mail + WhatsApp |
| E2 | `[T0+24h, T0+36h)` | e-mail + WhatsApp |
| E3 | `[T0+48h, T0+60h)` | e-mail + WhatsApp |

WhatsApp só quando: `origem='assinar'`, há `phone_e164`, o número não é identidade de
WhatsApp de outra conta (#721), sem opt-out de WhatsApp, template da etapa configurado.
Fora da janela a etapa é pulada (mínimo de 12 h entre mensagens; nada depois de T0+60h).

**Releitura antes de cada canal**, direto do banco: `ja_comprou`, exclusão, opt-out do
canal (`engagement_opt_out` / `whatsapp_updates_opt_out`), endereço ou número atual.
Leitura falhou = não envia.

## 2. Estados × eventos

Estados: S0 fora · S1 esperando · S2 etapa devida · S3 enviando · S4 fechada
(`enviado`/`sem_canal`) · S5 encerrado (T0+60h ou comprou).

| Evento | Efeito |
|---|---|
| tick em S2 | relê; reivindica `(user,E)` com `insert … on conflict do nothing returning`; e-mail (relê); WhatsApp (relê); fecha |
| comprou | S1/S2 → S5; em S3 a releitura corta o canal que falta |
| opt-out de um canal | corta só aquele canal |
| `started` < 60 min sem fim, ou Pix ativo < 60 min | adia sem reivindicar |
| Pix pendente ≥ 60 min | envia; o link leva à `/precos` e o Pix pendente com os mesmos cadernos é reaproveitado |
| sem telefone / número de outra conta / template ausente / origem `precos` | só e-mail |
| e-mail falha | `email_ok=false`, WhatsApp segue |
| WhatsApp falha | `whatsapp_ok=false` (o e-mail já saiu) |
| servidor fora | na janela: envia atrasado; fora: pula |
| processo morre após reivindicar | fica `enviando`; no máximo um envio por etapa |
| duas réplicas | reivindicação atômica: um só envia |
| env vazia | nada; ao religar, janelas vencidas não disparam |
| exclusão agendada / conta apagada | não envia / cascade |
| clique no link | `clicado_em` (prévias de link inflam) |
| link de etapa velha | vale até o token vencer (7 dias) |

## 3. Como o link atravessa /precos → /assinar → cartão ou Pix

**Descartadas:** sessão normal de login (conta com senha daria login completo; conta sem
senha ainda alcança `PATCH /settings/{id}/security/contact`, `exige_credencial=False`, e
quem tem o link trocaria o e-mail e tomaria a conta pelo reset depois que ela pagar);
token em `sessionStorage` + header (`auth-refresh.js` apaga a `sessionStorage` no 401 do
refresh, que a `/precos` anônima provoca).

**Escolhida: cookie de compra de escopo restrito.**
- Token Fernet, chave derivada de `JWT_SECRET`, rótulo `b"pigbank:retomada:v1"` (padrão
  de `core/services/agent_chat.py:47-52`), validade 7 dias, carrega `user_id` e `etapa`.
  Não é JWT, nunca vira sessão.
- Cookie `pb_retomada`: `HttpOnly`, `Secure`, `SameSite=Strict`, `Max-Age` 2 h.
- `GET /retomar?t=…&utm_*` (sem HTML): token inválido/vencido ou conta inexistente/em
  exclusão → 303 `/precos` sem cookie; `ja_comprou` → 303 `/login`; pedido com cookie ou
  header de sessão → 303 `/precos` sem cookie; senão grava o cookie e `clicado_em` e faz
  303 `/precos?utm_…` (o token nunca chega à URL da `/precos`).
- **Quem vence** (dependência única `comprador()` em `frontend/routes/retomada.py`):
  sessão válida vence; o cookie só é lido se o pedido não traz cookie nem header de
  sessão nenhum; cookie de sessão inválido continua 401 (para o `auth-refresh` renovar);
  depois, `_raise_if_account_scheduled_for_deletion`.
- Resíduo declarado: navegador com só o refresh cookie de B + cookie de A entra em
  retomada para A; se B renovar no meio, `bump`/`poll` dão 404. Sem cobrança errada.

| Rota | Em modo retomada |
|---|---|
| `GET /billing/plans-config` | ganha `retomada: null \| {email: "l•••@dominio"}` (máscara no servidor), só com cookie válido e sem sessão |
| `/auth/me`, `/auth/validate`, `/billing/subscription` | não aceitam (401); a `/precos` em retomada nem chama `/billing/subscription` |
| `POST /billing/create-checkout` | `comprador()`; metadata `rm_etapa` (do token, no servidor) na sessão e na assinatura; volta em `/retomar/pronto?sid=…`; `origem` segue `precos`/`assinar` |
| `POST /billing/checkout/bump` | `comprador()`; as 4 condições de dono continuam |
| cupom, Express, `confirm` | Stripe.js no cliente; nada muda |
| `POST /billing/pix/checkout` | `comprador()`; `confirm_cancel_stripe` = 409 `retomada_sem_migracao` |
| `GET /billing/pix-extras`, `GET /billing/pix/{token}` | `comprador()`, filtrando pelo dono |
| `POST /auth/logout` | o `Clear-Site-Data: "cookies"` apaga o `pb_retomada` (saída do modo) |
| `change-plan`, `cancel-change`, `portal`, `/settings/*`, `/api/v2/*`, `/auth/*` | não aceitam |

`_checkout_session_matches` passa a comparar a presença de `rm_etapa` (sessão de
retomada não é reaproveitada pelo fluxo logado, e vice-versa).

**Volta sem sessão:** cartão volta para `/retomar/pronto?sid=…`; Pix: o `pix-poll.js`
consulta com o cookie e, pago, vai para `/retomar/pronto?gw=pix`. A página mostra
"Pagamento recebido" e "Enviar link para criar sua senha" (`POST
/billing/retomada/senha`: se `conta_sem_credencial`, reaproveita
`_reset_de_senha_em_segundo_plano`; conta com senha não recebe nada; resposta sempre
igual; 3/h; expira o cookie), mais "Já tem senha? Entrar" e "Esqueci a senha". Os
cadernos continuam esperando a senha.

**Rastreio:** `rm_etapa` no Stripe; Pix sem coluna nova (atribuição por `clicado_em`
antes do `completed`); o `/retomar` repassa `utm_*` à `/precos`. O Purchase do pixel no
cliente não dispara nessa volta; CAPI e GA4 do servidor disparam como hoje.

## 4. PRs, na ordem de merge

Cada PR fica inerte sozinho; só o PR 6 com a env envia algo.

### PR 1 — Fundação: schema, T0, predicado e frase (inerte)

- `db/schema.py`: `remarketing_regua(user_id pk → users on delete cascade, t0, origem
  check in ('precos','assinar'))`; `remarketing_envios(id, user_id cascade, etapa 1–3,
  status check in ('enviando','enviado','sem_canal'), email_ok, whatsapp_ok, motivo,
  criado_em, fechado_em, clicado_em, unique (user_id, etapa))`.
- `db/remarketing.py` (novo): `registrar_t0` e `ja_comprou` (a porta única; o fragmento SQL é privado).
- `frontend/routes/static_pages.py` (`serve_precos`) e `frontend/routes/quiz_signup.py`
  (`criada` com `origem=assinar`, depois da sessão; `logado` com `origem=precos`): `registrar_t0` em `try` próprio.
- `frontend/assinar.html`: a frase de opt-in (skill `pigbank-frontend`, desktop e mobile).
- Testes: T0 gravado uma vez (controle negativo com `do update`); anônimo não grava; só
  `criada` e `logado` gravam; falha do `registrar_t0` não derruba `/precos` nem cadastro (controle
  positivo); tabela de `ja_comprou` com isolamento A×B (controle negativo sem `user_id =`).
- Pode quebrar: latência da `/precos`; testes de texto do `assinar.html`; conferir
  `db/privacy.py` e `merge_users` com o cascade.

### PR 2 — Bot: "PARAR" desliga o WhatsApp; lead sem compra recebe texto próprio

Hoje `_tratar_opt_out` (`wa_runtime.py:654`) só reage a id interativo; "PARAR" digitado
não casa. Texto normalizado igual a `parar` vale como `parar atualizacoes` nos ramos
`conta_sem_credencial`, `precisa_senha`, `remetente_com_dados` e no fluxo comum (antes
das pendências e do paywall). Lead sem `ja_comprou` recebe texto novo (copy do dono) no
lugar de `PRECISA_SENHA_WA`. Testes pela conversa (`process_message`, Postgres real),
com controles negativo e positivo; "parar report diario" continua em `report.disable`.
Só no aparelho: PARAR de um celular real.

### PR 3 — Abrir espaço no `assinar.js` (sem mudar comportamento)

`frontend/assinar.js` está no teto de 350 linhas (medido 2026-10-09 com `wc -l`;
remeça). Mover os helpers de rede sem estado `post` e `sessaoViva` para
`frontend/assinar-rede.js` (`window.PBAssinarRede`), com rota em `static_pages.py`.
Prova: `tests/frontend/assinar_*` e `pagamento_pagina*` verdes antes e depois; o diff só
move código. `pix-checkout.js` (350) não é tocado.

### PR 4 — Retomada no backend (inerte até haver token)

- `core/services/retomada.py` (novo): `emitir_token`, `ler_token`.
- `frontend/routes/retomada.py` (novo): `GET /retomar`, `POST /billing/retomada/senha`,
  `comprador()`, registro de `GET /retomar/pronto`.
- `frontend/finance_bot_websocket_custom.py`: `billing_create_checkout` usa
  `comprador`; `_billing_checkout_for_user`/`_new_session` recebem `retomada_etapa`;
  `_checkout_session_matches` compara `rm_etapa`; `billing_plans_config` ganha
  `retomada`.
- `frontend/routes/billing_bump.py` e `frontend/routes/billing_pix.py`: `comprador`; o
  POST do Pix recusa `confirm_cancel_stripe` em retomada.
- `tests/test_rotas_senha_obrigatoria.py`: rotas novas como `publica`. `docs/CLAUDE.md`.
- Testes (Postgres real, Stripe e Asaas mockados): token (ida e volta, adulterado,
  vencido, separação de domínio contra unsub/`agent_chat`/JWT — controle negativo com o
  rótulo do `agent_chat`); `GET /retomar` nos 4 ramos; **isolamento**: sessão B + cookie
  A = tudo como B; só cookie A = tudo como A com `rm_etapa`; cookie A + `dashboard_token`
  de B inválido = 401, nunca A (controle negativo sem a guarda); cookie A nas rotas fora
  da lista = 401 (controle positivo: sessão real de A responde); poll e bump de B com
  cookie A = 404; recusas existentes continuam; testes atuais de checkout, bump e Pix
  verdes sem cookie; senha (1 e-mail × 0, corpo igual, cookie expira, rate limit);
  nenhum corpo devolve e-mail completo nem nome.

### PR 5 — Retomada no frontend e página de volta

- `frontend/precos.html` (`loadPlansState`): com `cfg.retomada`,
  `pbPlanAuthState = "retomada"`, sem `loadSubscription`, `publicarPix(null, true)`.
- `frontend/assinar.js` + `assinar-rede.js`: sem sessão e com `retomada`, mostra
  "Assinando como l•••@dominio" e vai direto ao pagamento (sem o formulário); "Sair"
  apaga o cookie e volta ao formulário.
- `frontend/pix-poll.js`: em retomada, destino `/retomar/pronto?gw=pix`.
- `frontend/retomar-pronto.html` + `.js` (novos), rota em `static_pages.py`, shim
  `safe-area.js`, tabela de `docs/armadilhas.md`. Skill `pigbank-frontend`, desktop e
  mobile.
- Testes (`tests/frontend/`): `/precos` em retomada; `/assinar` em retomada (controle:
  sem retomada e sem sessão mostra o formulário como hoje); destino do `pix-poll`;
  estados da `retomar-pronto`.
- Só em staging: link no navegador interno do WhatsApp (iPhone) e no desktop; cartão
  com caderno + cupom; Apple Pay; Pix no sandbox do Asaas; "Definir senha" → login →
  grant; repetir num navegador logado em outra conta (compra para a conta logada).

### PR 6 — Job de envio (inerte sem a env)

- `core/services/remarketing.py` (novo): `ligado()` lê `REMARKETING_DESDE` a cada tick
  (vazia/inválida = desligado, sem consulta); `rodar_uma_vez(agora)`.
- `db/remarketing.py`: `candidatos` (única query sem `user_id`, só chaves),
  `reivindicar`, `fechar`, `estado_fresco`, `em_andamento`, `numero_de_outra_conta`.
- `core/services/email_service.py`: `send_remarketing_email` com
  `unsub_headers(make_unsub_url(…))`.
- `adapters/whatsapp/wa_client.py` (`send_template`): kwarg opcional para botão de URL.
- Envs: `WA_TEMPLATE_REMARKETING_1|2|3`, `WA_TEMPLATE_REMARKETING_LANGUAGE` (`pt_BR`).
- `frontend/finance_bot_websocket_custom.py`: `_remarketing_worker` (300 s,
  `to_thread`). `.env.example`, `docs/CLAUDE.md`.
- Link: `https://pigbankai.com/retomar?t=<token>&utm_source=email|whatsapp&utm_medium=remarketing&utm_campaign=rmN`.
- Testes (relógio congelado, envio mockado): env vazia = zero query (controle negativo);
  janelas e pulos; releitura entre canais (controle negativo); opt-outs independentes;
  adiamentos; número de outra conta; E3 com WhatsApp possível manda os dois; falha não
  derruba o lote; duas threads = um envio; link e descadastro do próprio usuário; forma
  do botão.
- Só em produção: entrega do template de marketing e o primeiro lote. Staging:
  `rodar_uma_vez()` pelo shell.

### PR 7 — Painel (só leitura)

`core/funil_remarketing.py` (novo), chave em `fetch_funil`, bloco em
`frontend/funil.html`. Agregados por etapa e canal: em régua, enviados, sem canal,
falhas, presos em `enviando`, cliques, recuperados (`completed` depois de
`clicado_em`).

## 5. O que o dono faz por fora, nesta ordem

1. Textos: resposta do bot ao lead (PR 2), 3 e-mails (PR 6), página
   `/retomar/pronto` (PR 5).
2. Na Meta: 3 templates MARKETING `pt_BR`, botão de URL no índice 0 com base
   `https://pigbankai.com/retomar?t={{1}}`, "Responda PARAR para sair" no texto.
3. Depois do deploy do PR 6: `WA_TEMPLATE_REMARKETING_1/2/3`.
4. Por último: `REMARKETING_DESDE` com o instante da ativação (-03:00). Para desligar:
   apagar a env.

## 6. Riscos e limites declarados

- Número ou e-mail errado leva a mensagem a um estranho (aceito). Se ele clicar, vê o
  e-mail mascarado, pode pagar pela conta e pedir o "Definir senha", que vai ao e-mail
  do dono. Não lê dado nem troca o contato.
- Resíduo do refresh cookie de outra conta (seção 3).
- Pelo link não se migra assinatura de cartão para Pix (409).
- Sem Purchase do pixel no cliente na volta sem sessão; o servidor cobre.
- `clicado_em` inflado por prévias de link.
- No máximo um envio por etapa.
- "parar" passa a desligar as atualizações para todos os usuários.
- Página de descadastro fala em "dicas e insights" (copy antiga).
- Cupom depois: pelo cupom da página ou por `rm_etapa` no servidor; se for `discounts`
  na sessão, tirar `allow_promotion_codes=True`.
- Só fora daqui: PARAR e templates no aparelho; página própria e Express no navegador
  do WhatsApp; Pix no sandbox; entrega da Meta em produção.
