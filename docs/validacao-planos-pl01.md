# PL-01 — Validação operacional dos planos

Roteiro da frente PL-01 do [backlog de planos](backlog-planos-2026-09-22.md):
conferir em produção, com uma conta de teste, o que os testes automatizados não
alcançam — permissões por plano nos canais reais, entrega de lembrete e resumo
semanal, Open Finance e simulador. A matriz esperada é a de
[permissoes-planos.md](permissoes-planos.md).

**Base:** `main` em `02014e8d` (merge do #518), 23/09/2026.
**Ambiente:** produção, `https://pigbankai.com` — é o único ambiente que existe.
**Estado:** roteiro pronto, nenhum caso executado ainda.

Regras deste roteiro:

- Uma conta de teste só, criada para isto, que percorre os planos. Nada de conta
  real de cliente, nem para "só olhar".
- Evidência sem credencial, token, CPF, telefone ou saldo de conta real. A saída
  da sonda (seção A) só carrega status HTTP e nomes de chave. Captura de tela vai
  anexada ao PR, não versionada.
- Cada caso tem resultado **esperado** e **observado**. "Parece ok" não é
  observado; status, horário e valor são.
- Defeito achado vira PR próprio, um por causa, pelo ciclo do `time-dev`. Este
  arquivo só registra o ID do caso e o link do PR.

---

## 0. Preparação

| # | passo | quem |
|---|---|---|
| P1 | Criar a conta de teste em `/cadastro` com um e-mail próprio para isto (ex.: alias `+pl01`), sem MFA — a sonda de UI (`scripts/smoke_prod_ui.mjs`) não passa por MFA | dono |
| P1b | Depois de pôr a conta em Essencial pelo admin (P6, estado S1), abrir `/app`: a conta nova tem `onboarding_completed_at` vazio e `/app` e `/home` redirecionam para `/onboarding` (`shared.gate_onboarding`). Tocar **"Pular tudo e ir para o app"**. Não escolher opção de resumo nem conectar banco no wizard: o passo de resumo grava as preferências diário/semanal/mensal ("Não quero" desliga o semanal que B5 e E3 precisam encontrar ligado, e ele nasce ligado por padrão), e o banco pertence à seção F. Em seguida, o `/home` mostra uma vez o convite para ativar MFA (`show_mfa_onboarding`): dispensar sem ativar, porque a sonda de UI não passa por MFA (P1) | dono |
| P2 | Vincular um número de WhatsApp de teste à conta (um número só serve para os três planos) | dono |
| P3 | Anotar o `user_id`: `python -m scripts.whoami <email>` ou `GET /auth/dashboard-profile` logado | dono ou Claude |
| P4 | Conferir a configuração da seção **0.1** abaixo, no Railway e nos painéis da Meta, Pluggy e Stripe. Segredo se registra só como "definida / ausente"; valores que não são segredo se registram por extenso | dono |
| P5 | Ligar `PLUGGY_INCLUDE_SANDBOX=1` no Railway **só durante a seção F** e desligar assim que F terminar. A variável é global e lida na subida do processo (`frontend/routes/open_finance.py:74`): enquanto ligada, **todos** os usuários veem os conectores sandbox no widget, e cada troca reinicia o serviço | dono |
| P6 | Painel admin (`/admin`) aberto numa aba: é por ele que o plano muda nas seções A–C (botão de plano do usuário → `POST /admin/api/users/{id}/plan`) | dono |

### 0.1 Configuração de que os casos dependem

Levantada no código dos caminhos que cada seção exercita. Um item que falte ou
esteja diferente do esperado deixa os casos da coluna **bloqueados**, não
reprovados: é configuração, não defeito do produto. D, E e I têm datas ou
cobrança; resolver o que for deles **antes** de começar.

| item | casos | esperado | o que acontece se faltar | onde é lido |
|---|---|---|---|---|
| `PLANS_V2_ENABLED` | todos | ausente ou `1` | matriz legada de permissões | `core/services/plan_service.py` |
| `ACCESS_GATE_ENABLED` | I0, I4 | ausente ou `1` | sem 402, bot não bloqueia | `plan_service.py` |
| `RUN_BACKGROUND_TASKS` | C, D, E, I4 | ≠ `0` | além dos lembretes e resumos, desliga o worker que processa as mensagens recebidas do WhatsApp: o bot fica mudo | `frontend/finance_bot_websocket_custom.py` (lifespan) |
| `DASHBOARD_URL` | F3, I1, I4 | `https://` do domínio de produção | webhook da Pluggy não é montado; links `/d/`, retorno do checkout e do portal apontam para localhost | `frontend/routes/shared.py`, `core/dashboard_links.py` |
| `REPORT_TIMEZONE` / `TZ` | A, D | registrar o **valor** (padrão `America/Sao_Paulo`) | a sonda e o horário dos lembretes usam outro "hoje" | `utils_date.py` |
| Réplicas do serviço no Railway | F3 | 1 | o evento `open_finance_synced` vai só para o processo que sincronizou; o repaint pode não chegar | `frontend/routes/open_finance.py` |
| `WA_ACCESS_TOKEN` (tem precedência) ou `WA_TOKEN`, `WA_PHONE_NUMBER_ID` | C, D, E, I4 | definidos; um `WA_ACCESS_TOKEN` velho vence um `WA_TOKEN` bom | erro só no log, nenhuma mensagem sai | `adapters/whatsapp/wa_client.py` |
| `WA_GRAPH_VERSION` | C, D, E | registrar o **valor** (padrão `v21.0`) e confirmar que a Meta ainda aceita | envios falham | `wa_client.py` |
| `WA_APP_SECRET`, `WA_VERIFY_TOKEN` e webhook da Meta assinando `messages` para `/webhook` | C, D3, I4 | definidos e assinados | bot não recebe nada | `adapters/whatsapp/wa_app.py` |
| `WA_BILL_REMINDER_TEMPLATE_NAME` e `_LANGUAGE`, `WA_BILL_REMINDER_HOUR`, `WA_BILL_REMINDER_DAYS_BEFORE` | D | nome definido; registrar o **valor** da hora **H** (padrão 9) e dos dias **N** (padrão 3) | sem nome, o lembrete fica dormente | `_bill_reminder_tick` |
| `WA_WEEKLY_TEMPLATE_NAME`, `WA_MONTHLY_TEMPLATE_NAME`, `WA_PROACTIVE_TEMPLATE_LANGUAGE`, `WA_PERIODIC_TEMPLATE_STOP_BUTTON` | E | nomes definidos; registrar o valor do botão | resumo não sai | `_periodic_report_tick` |
| Templates na Meta | D, E | cada nome acima existe, está **aprovado**, no idioma configurado e com as variáveis **nomeadas** que o código envia: lembrete = `{{conta}}`, `{{valor}}`, `{{vencimento}}` e um botão de resposta rápida no índice 0 (o "Já paguei" do D3); resumos = `{{periodo}}`, `{{saldo}}`, `{{gastos}}`, `{{receita}}`, `{{lancamentos}}` (modelo em `docs/whatsapp_templates_resumos.md`) e botão só se `WA_PERIODIC_TEMPLATE_STOP_BUTTON=1`. Registrar "aprovado e compatível / pendente / rejeitado / formato diferente / não existe" | falha de `send_template` só vai para o log. Um template recusado só aparece depois de perder a segunda-feira do E1 | Meta |
| Pagamento e categoria dos templates na conta WhatsApp Business (Meta) | D, E | método de pagamento ativo | envio recusado, só no log | Meta |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | C1, C4–C7, G5 | chave definida; modelo com suporte a tools (padrão `gpt-4o-mini`) | chat devolve mensagem de erro; C1 perde a categoria por IA. A sonda A continua 200 (Insights cai na heurística) | `core/services/ai_chat/runner.py`, `core/ai_patterns.py` |
| `AI_CHAT_MONTHLY_LIMIT`, `AI_RATE_LIMIT_MAX_CALLS` / `_WINDOW_SEC` | C | registrar os **valores** (padrões 1000 por mês, com o Essencial em no máximo 200; 10 chamadas em 60 s) | IA cortada com convite de upgrade, ou `out_of_scope` se as mensagens forem rápidas demais. Mandar as mensagens de C com alguns segundos entre elas | `core/services/ai_chat_commands.py`, `core/ai_rate_limiter.py` |
| `PLUGGY_CLIENT_ID` / `_SECRET` (ou `PLUGGY_API_KEY`, que tem precedência e expira) | F | definidos | 503 no widget; com `PLUGGY_API_KEY` vencida, 502 | `core/services/pluggy.py` |
| `PLUGGY_PRODUCTS` | F2, F4 | ausente ou com `ACCOUNTS,TRANSACTIONS` | produto não coletado | `pluggy.py` |
| **`PLUGGY_WEBHOOK_SECRET`** | F3 | definida | o webhook responde **503** | `frontend/routes/open_finance.py` |
| `PLUGGY_WEBHOOK_URL` | F3 | opcional: sem ela o código usa `{DASHBOARD_URL}/open-finance/pluggy/webhook`. A URL é gravada no item ao conectar; trocar depois exige reconectar | — | `open_finance.py` |
| `PLUGGY_INCLUDE_SANDBOX` | F | ver P5 | conector sandbox não aparece | `open_finance.py:74` |
| `OF_MANUAL_REFRESH_COOLDOWN_SEC`, `OF_HEALTH_CHECK_ENABLED` | F2 | registrar os **valores** (padrões 120 s e ligado) | dois toques em "↻ Atualizar" em menos do cooldown voltam `rate_limited` e "Última sync" não muda | `core/services/pluggy_sync.py` |
| `OF_CONSOLIDATED_BALANCE_ENABLED` | F9, G6 | registrar se está ausente/ligado (padrão) ou em `0`. Em `0`, vale a allowlist: env ausente usa uma lista **fixa no código**, e env definida substitui essa lista (`OF_CONSOLIDATED_BETA_EMAILS` / `_USER_IDS`); a comparação de e-mail é exata, então o alias `+pl01` não entra por semelhança. Registrar só se a conta de teste está ou não na lista | ver F9 e G6 | `plan_service.consolidated_balance_enabled` |
| `STRIPE_SECRET_KEY` | I | definida (modo live) | 503 no checkout e no webhook | `finance_bot_websocket_custom.py` |
| `STRIPE_PRICE_ID_PRO_MENSAL` (Plus mensal; ou o legado `STRIPE_PRICE_ID_PRO`) e `STRIPE_PRICE_ID_ESSENCIAL_MENSAL` | I1; I2, I3 | definidos | botão indisponível, 503; sem o preço do Essencial, o agendamento também não aparece | idem |
| `STRIPE_WEBHOOK_SECRET` e endpoint `/billing/webhook` em live, assinando `checkout.session.completed`, `invoice.paid`, `invoice.payment_succeeded`, `invoice.payment_failed`, `customer.subscription.trial_will_end`, `customer.subscription.deleted` | I1, I4 | definidos e assinados | 400 e o plano não muda, sem aviso na tela | idem |
| Customer Portal da Stripe (live) | I4 | configuração padrão salva, com cancelamento **no fim do período** | sem configuração, `/conta` dá 500; com cancelamento imediato, o acesso cai na hora | Stripe |
| `PLANS_TRIAL_DAYS` | I1 | registrar o **valor** (padrão 15). No modo v2 é esta a variável; `PRO_TRIAL_DAYS` só vale no legado | trial de outra duração | `plan_service.trial_days_total()` |
| `ADMIN_DASHBOARD_PASSWORD_HASH` | P6, I0 | definida (produção não aceita senha em texto) | painel admin responde 503 | `core/admin_dashboard.py` |

Limites de taxa que um executor pode encontrar sem ser defeito: Insights e padrões
20/min por IP, perfil 60/min, checkout 20/h, troca e cancelamento de troca 15/h.

---

## Sequência de planos

A conta passa por cinco estados. As seções A, B e C se repetem em cada um.

| estado | plano no admin | valor gravado em `auth_accounts.plan` | o que prova |
|---|---|---|---|
| S1 | Essencial | `essencial` | base |
| S2 | Plus | `pro` | upgrade Essencial → Plus |
| S3 | Pro | `pro_max` | upgrade Plus → Pro |
| S4 | Plus | `pro` | downgrade Pro → Plus: some o que é só Pro |
| S5 | Essencial | `essencial` | downgrade Plus → Essencial: some o que é Plus |

`pro` = Plus e `pro_max` = Pro é o mapeamento legado de
`core/services/plan_service.py` — não é erro de digitação.

O ciclo da Stripe (seção I) fica **por último**: com assinatura Stripe ativa, o
próximo webhook dela prevalece sobre o que o admin gravou, e a reprojeção de
direitos (a cada 60 s, com varredura completa a cada 24 h) pode reverter a troca
manual no meio de outra seção.

---

## A. Sonda de permissões pela API

Rodar no console do navegador, **logado com a conta de teste em `/app`**, uma vez
por estado S1–S5. Só faz leituras, exceto o simulador (não persiste nada). As
duas rotas de Insights chamam o modelo pago; o cache (6 h em `/insights`, 24 h
em `/analytics/.../patterns`) evita custo nas repetições.

```js
(async () => {
  const j = (r) => r.json().catch(() => null);
  const prof = await fetch('/auth/dashboard-profile', {credentials: 'same-origin'}).then(j);
  const uid = prof.user_id;
  // "Hoje" no fuso do servidor (P4: REPORT_TIMEZONE/TZ; padrão America/Sao_Paulo).
  // Em UTC, entre 21h e 23h59 de Brasília, a data já seria a de amanhã e o proj30 pediria 31 dias.
  const TZ_APP = 'America/Sao_Paulo';  // trocar se o P4 mostrar outro fuso
  const hoje = new Intl.DateTimeFormat('en-CA', {timeZone: TZ_APP}).format(new Date());
  const d = (n) => { const t = new Date(hoje + 'T00:00:00Z'); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); };
  const sim = {reserva_minima: 500, cenarios: [
    {nome: 'À vista', preco: 3000},
    {nome: '12x', preco: 3000, entrada: 600, parcelas: 12, juros_mensal_pct: 1.49}]};
  const calls = {
    forecast: ['GET', `/forecast/${uid}`],
    proj30: ['GET', `/recurring-bills/${uid}/projection?date=${d(30)}`],
    proj60: ['GET', `/recurring-bills/${uid}/projection?date=${d(60)}`],
    evolution: ['GET', `/analytics/${uid}/evolution`],
    weekday: ['GET', `/analytics/${uid}/weekday-pattern`],
    insights: ['GET', `/insights/${uid}/current`],
    patterns: ['GET', `/analytics/${uid}/patterns`],
    household: ['GET', `/household-budget/${uid}/status`],
    recurring: ['GET', `/recurring-expenses/${uid}`],
    simulator: ['POST', `/simulator/${uid}`, sim],
  };
  // POST com cookie exige o header CSRF; sem ele o 403 "Token CSRF inválido" se confunde com o de plano.
  const csrf = decodeURIComponent((document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/) || [])[1] || '');
  const out = {plan: prof.plan, gates: prof.feature_gates};
  for (const [k, [m, url, body]] of Object.entries(calls)) {
    const r = await fetch(url, {method: m, credentials: 'same-origin',
      headers: body ? {'Content-Type': 'application/json', 'X-CSRF-Token': csrf} : {},
      body: body ? JSON.stringify(body) : undefined});
    const b = await j(r);
    // Status mais o motivo: um 403 de plano (`pro_required`) não pode passar por um de CSRF.
    out[k] = r.ok ? r.status : `${r.status} ${b?.detail?.error ?? JSON.stringify(b?.detail ?? b)}`;
    if (k === 'forecast' && r.ok) out.forecast_keys = {
      horizons: Object.keys(b.forecast.horizons || {}),
      trajectory: 'trajectory' in b.forecast, worst_day: 'worst_day' in b.forecast};
    if (k === 'simulator' && r.ok) out.simulator_body = b.simulacao;  // evidência de G1–G4
  }
  console.log(JSON.stringify(out, null, 1));
  return out;
})();
```

Esperado. A sonda grava o status e, quando não é 2xx, o motivo (`detail.error`). Recusa por plano = `403 pro_required` (corpo `{"detail": {"error": "pro_required", "feature": "<feature>"}}`; o `proj60` recusado no Plus traz também `message`). Qualquer outro 403, como `"Token CSRF inválido ou ausente."`, é falha da execução, não resultado:

| chave | S1/S5 Essencial | S2/S4 Plus | S3 Pro |
|---|---|---|---|
| `gates.forecast` / `insights` / `financial_comparison` / `weekly_report` / `household_budget` | `false` | `true` | `true` |
| `gates.cashflow` | `false` | `false` | `true` |
| `gates.recurring_expenses` | `true` | `true` | `true` |
| `forecast` | 403 pro_required | 200 | 200 |
| `forecast_keys.horizons` | — | `["30"]` | `["30","60","90"]` |
| `forecast_keys.trajectory` / `worst_day` | — | `false` | `true` |
| `proj30` | 403 pro_required | 200 | 200 |
| `proj60` | 403 pro_required | 403 pro_required | 200 |
| `evolution`, `weekday`, `insights`, `patterns`, `household` | 403 pro_required | 200 | 200 |
| `recurring` | 200 | 200 | 200 |
| `simulator` | 403 pro_required | 403 pro_required | 200 |

| caso | estado | observado (colar a saída) | resultado | PR |
|---|---|---|---|---|
| A1 | S1 | | | |
| A2 | S2 | | | |
| A3 | S3 | | | |
| A4 | S4 | | | |
| A5 | S5 | | | |

A sonda é cega a duas coisas, e por isso B e C existem: o que o dashboard
**mostra** (um 403 bem dado pode virar card quebrado) e o caminho do chat, que
passa por tools diferentes das rotas.

---

## B. Dashboard e ajustes

Em cada estado, desktop **e** mobile (390 px):

| caso | onde | S1/S5 Essencial | S2/S4 Plus | S3 Pro |
|---|---|---|---|---|
| B1 | `/app` → Recorrentes → aba "Contas a pagar": card "Previsão de saldo" e card "Tô tranquilo nesse prazo?" | "Previsão de saldo" travado (`pro-locked`) com a mensagem de bloqueio; o "Simular" do card "Tô tranquilo nesse prazo?" abre o modal de upgrade; sem erro | só 30 dias | 30/60/90 |
| B2 | Insights e padrões (cards `data-plan-content="insights"` e card do Piggy) | **ocultos**, sem espaço vazio no lugar; totais, categorias e estabelecimentos visíveis | visíveis | visíveis |
| B3 | comparações (cards `data-plan-content="financial_comparison"`) | **ocultos**; KPIs aparecem sem variação percentual | visíveis, KPIs com variação | visíveis |
| B4 | Orçamento Doméstico (menu lateral) | item travado; clicar abre o modal de upgrade e não navega | acessível | acessível |
| B5 | Ajustes → notificações → resumo semanal | switch aparece **marcado** com "Pausado no seu plano…" (a preferência nasce ligada); desmarcar funciona e depois o switch fica travado com "disponível nos planos Plus e Pro". A recusa 403 só existe pela API | liga e persiste depois de recarregar a página | liga e persiste |
| B6 | gastos fixos / contas a pagar | criar e listar funcionam | idem | idem |
| B7 | console do navegador | nenhum erro não tratado | idem | idem |

**B5 em S5: só observar, não desmarcar** antes do caso E3 (05/10). Desmarcar
apaga a preferência antiga que o E3 precisa encontrar ligada.

Na transição S3 → S4 e S4 → S5, recarregar a página **sem** sair da conta: o
estado novo tem de aparecer sem logout (os gates vêm de `/auth/dashboard-profile`
a cada carga).

| caso | estado | observado | resultado | PR |
|---|---|---|---|---|
| B-S1 … B-S5 | | | | |

---

## C. WhatsApp

Mensagens enviadas do número de teste. Escritas como o usuário escreve, não
como o teste projetaria. Registrar a resposta resumida (sem valores de conta
real — a conta é de teste, então os valores podem ir).

| caso | mensagem | Essencial | Plus | Pro |
|---|---|---|---|---|
| C1 | `gastei 47,90 no ifood` | registra com categoria (IA liberada no Essencial) | idem | idem |
| C2 | `todo mês pago 39,90 de spotify dia 10` | cria gasto fixo (bloqueio legado removido no #518) | idem | idem |
| C3 | `ligar resumo semanal` | responde "🐷 O resumo semanal automático está disponível nos planos Plus e Pro." e não muda a preferência | liga | liga |
| C4 | `quanto vou ter de saldo daqui 30 dias?` | recusa com convite | responde | responde |
| C5 | `quanto vou ter de saldo daqui 60 dias?` (frase completa, sem depender da mensagem anterior) | recusa | explica o limite de 30 dias, sem inventar número | responde |
| C6 | `compara esse mês com o mês passado` | recusa com convite: a frase vai para a IA, que chama `compare_periods`, e ele devolve `pro_required` no Essencial | compara | compara |
| C6b | `quanto gastei esse mês?` | responde com o total do mês (análise básica, liberada) | idem | idem |
| C7 | `se eu comprar um celular de 3 mil em 12x com 600 de entrada e juros de 1,49% ao mês, como fica meu caixa?` | recusa | recusa com convite ao Pro | simula (conferir contra G2) |
| C8 | `resumo da semana` (pedido manual) | responde | responde | responde |
| C6c | `gastei mais esse mês que no passado?` | **candidato a defeito, todos os planos:** o classificador determinístico lê "gastei" como lançamento (`launches.add`, confiança 0,95, conferido rodando `classify`) e pergunta o valor, deixando uma pendência gravada por 10 min (`db/pending.py`). **Mandar por último no estado e esperar 10 min antes de qualquer outra mensagem**: até lá, a próxima mensagem com número (como o C1 do estado seguinte) vira o valor desse lançamento. Registrar a resposta observada | idem | idem |

C3 e C8 separam o resumo **automático** (Plus+) do resumo **pedido**, que a
matriz mantém em todos os planos.

| caso | estado | resposta observada | resultado | PR |
|---|---|---|---|---|
| C1–C8, depois C6c | S1 | | | |
| C1–C8, depois C6c | S2 | | | |
| C1–C8, depois C6c | S3 | | | |
| C3, C5, C7 | S4 | | | |
| C3, C4, C6, C6b e, por último, C6c | S5 | | | |

---

## D. Lembrete de vencimento (todos os planos)

Como o código decide (`_bill_reminder_tick`): roda a cada 5 min, só a partir de
`WA_BILL_REMINDER_HOUR` (padrão 9 h, no fuso do P4), envia quando faltam
`WA_BILL_REMINDER_DAYS_BEFORE` dias (padrão 3), no dia do vencimento e 1 dia
depois, uma vez por dia por conta (`bill_instances.reminder_last_sent_on`).
Abaixo, **H** = hora e **N** = dias registrados no P4.

| caso | passo | esperado | observado | resultado |
|---|---|---|---|---|
| D1 | Em S1, depois das **H** h, criar conta a pagar `Teste PL01`, R$ 12,34, vencimento hoje + **N** dias | template chega no WhatsApp em até ~5 min com nome, valor e data | | |
| D2 | Esperar 30 min | nenhum segundo envio no mesmo dia | | |
| D3 | Tocar "Já paguei" | conta marcada paga no dashboard **e** uma despesa "Pagamento · Teste PL01" de R$ 12,34 lançada na Carteira (`mark_bill_paid`); o "Já paguei" quita a conta, então nenhum lembrete dela chega depois, inclusive em D0 e D+1 (a janela é só D-**N**, D0 e D+1) | | |
| D4 | Depois das **H** h, nova conta vencendo hoje | lembrete do dia chega em até ~5 min | | |

---

## E. Resumo semanal e mensal (calendário)

O semanal sai **só na segunda-feira**, no horário do report diário do usuário; o
mensal, **só no dia 1**. Não há disparo manual em produção, então estes casos
têm data marcada:

| caso | data | estado da conta | preferência | esperado |
|---|---|---|---|---|
| E1 | seg 28/09/2026 | Plus (S2 ou S4) | semanal ligado | resumo chega uma vez, no horário do report diário; se a conta passar a Plus depois desse horário, sai no ciclo seguinte (até 30 s), ainda uma vez só |
| E2 | qui 01/10/2026 | qualquer plano com acesso ativo (o mensal não depende do plano) | mensal ligado | resumo mensal chega uma vez |
| E3 | seg 05/10/2026 | Essencial (S5), **preferência ainda ligada** desde o Plus | ligado | **nenhum** envio — o downgrade barra o job mesmo com a preferência antiga |

E3 é o caso que o #518 prometeu e só produção prova: a preferência continua
gravada como ligada, e o job tem de recusar pelo plano. Ordem das seções,
portanto: fazer S1–S4 até 27/09, voltar a S2 para E1, e só descer para S5
antes de 05/10.

| caso | observado (horário de chegada, conteúdo resumido) | resultado | PR |
|---|---|---|---|
| E1 | | | |
| E2 | | | |
| E3 | | | |

---

## F. Open Finance com Pluggy sandbox

Com `PLUGGY_INCLUDE_SANDBOX=1` ligado (P5) e a conta em S3 (Pro; a cota de
conexões é 1/2/5 e qualquer plano serve para uma). As credenciais do conector
sandbox são digitadas pelo dono no widget da Pluggy.

Reaproveita o roteiro B de [open_finance_validacao_manual.md](open_finance_validacao_manual.md),
que nunca foi executado. Casos daquele roteiro cobertos aqui: **B7, B8, B9, B10**
(os outros exigem forçar estados de erro na Pluggy e ficam para quando houver
credencial de ambiente próprio).

| caso | passo | esperado |
|---|---|---|
| F1 | Conectar um conector sandbox em Ajustes → Open Finance | conexão aparece; pílula âmbar **"Atualizando…"** com a linha de detalhe "Ainda não sincronizou" até o primeiro sync; nunca a pílula verde "Atualizado" antes dele (análogo ao B8 do roteiro antigo, que trata a reconexão; `core/services/pluggy_health.py`) |
| F2 | Aguardar o sync ou tocar "↻ Atualizar" | "Última sync" muda de horário; contas e saldo aparecem (B7/B9) |
| F3 | Com o dashboard aberto numa segunda aba, esperar o sync disparado pelo **webhook** da Pluggy | dashboard repinta sozinho, cerca de 1,5 s depois do evento `open_finance_synced`, sem recarregar a página (B10). O botão "↻ Atualizar" **não** emite esse evento; sem webhook da sandbox não há repintura, e isso fica **bloqueado**, não reprovado |
| F4 | Comparar o saldo **por conta** em Ajustes → Open Finance → "Contas sincronizadas" com o do conector sandbox | mesmo valor por conta. Não comparar com o saldo total do dashboard: ele soma a Carteira, que já tem os lançamentos de C1 e D3 |
| F5 | Depois de F2, anotar **duas** saídas do sandbox (valor, data, descrição). Desconectar o banco (a desconexão desfaz o que foi importado). Pelo WhatsApp, lançar dois gastos com o mesmo valor (±R$ 0,05), data (±3 dias) e nome de cada saída. Reconectar e sincronizar | cada par aparece como **pendente** no modal **"Conferência com o extrato"**, aberto pelo link no card de saldo da Visão geral (o link só aparece com pendência; `frontend/reconciliations.js`): o importador rebaixa o casamento automático para "perguntar" quando o lançamento é manual (`import_open_finance_launches`). Mesclagem automática não acontece com lançamento manual |
| F5b | Com o banco já sincronizado, lançar pelo WhatsApp um gasto igual a outra saída sandbox **já importada** | **nenhum par é criado**: o casamento só roda quando o importador recebe transação nova, e `reconcile_manual_launch` não tem chamador em produção. Conferir os totais do mês: se o gasto contar duas vezes (manual + banco), registrar como **candidato a defeito** |
| F6 | Confirmar o primeiro par no modal | gasto conta uma vez só nos totais do mês |
| F7 | Desfazer o par confirmado em F6 | o par **sai** da lista "Unidos nos últimos 60 dias" do modal (estado `imported`); a transação do banco volta a ser lançamento próprio e o gasto manual volta à Carteira, então os dois contam separados, como em F8 (`db/reconciliation.py::undo_reconciliation`) |
| F8 | No segundo par, marcar **"São diferentes"** | os dois lançamentos ficam separados e contam como dois |
| F9 | Previsão (S3) antes e depois de F2 | **com saldo consolidado ligado para a conta (seção 0.1, `OF_CONSOLIDATED_BALANCE_ENABLED`):** saldo de partida da previsão muda junto com o saldo sincronizado. **Com o freio ligado e a conta fora da allowlist:** o saldo de partida continua só a Carteira, de propósito (`cashflow._starting_balance`), e o G6 mostra o aviso de bancos fora da soma |

| caso | observado | resultado | PR |
|---|---|---|---|
| F1–F9, F5b | | | |

---

## G. Simulador (Pro)

Em S3, depois de F (saldo sincronizado) e, se possível, também antes de F (sem
banco), para ver o aviso de saldo incompleto.

| caso | passo | esperado |
|---|---|---|
| G1 | Sonda A em S3: `simulator` = 200 e `simulator_body` preenchido | `simulator_body` com `atual`, `cenarios` (2), `premissas`, `reserva_minima` = 500. G2–G4 se conferem nesse mesmo objeto |
| G2 | Conferir o `contrato` do cenário `12x` | `valor_financiado` = **2400**, `pago_na_compra` = **600**, `parcelas` = **12**, `parcela` = **219,9**, `total_pago` = **3238,74**, `juros_totais` = **238,74**; `primeira_parcela`/`ultima_parcela` são **datas**. O valor da última parcela (R$ 219,84, que absorve o arredondamento) não vem na resposta: se quiser conferir, `total_pago − pago_na_compra − 11 × parcela` = 219,84 (valores obtidos executando `installments(2400, 0.0149, 12)`) |
| G3 | Conferir o cenário `À vista` | saída única de R$ 3.000 hoje; pior saldo cai R$ 3.000 contra o `atual` |
| G4 | Nenhum cenário marcado como "melhor" | a resposta compara liquidez e custo sem eleger vencedor |
| G5 | Mesmo pedido pelo chat (C7, que descreve só o 12x) | um cenário só, com os números de G2 |
| G6 | Aviso de saldo no chat, antes e depois de F | sem banco conectado: `aviso_saldo` **vazio** (o saldo manual é tratado como confiável). Com banco conectado e saldo consolidado desligado para a conta (seção 0.1; `consolidated_balance_enabled` falso): aviso "Seus bancos conectados não estão somados…". Com saldo consolidado ligado: sem aviso. Registrar qual dos três ocorreu (`core/services/cashflow.py:203`, `core/services/ai_chat/tools/simulator.py::_aviso_saldo`) |
| G7 | Payload inválido: `parcelas: 0` (pedido abaixo) | 400 com `{"detail": {"error": "invalid_simulation", "errors": [...]}}`, sem 500 |

Pedido do G7, no console, logado em S3:

```js
(async () => {
  const {user_id: uid} = await fetch('/auth/dashboard-profile', {credentials: 'same-origin'}).then(r => r.json());
  const csrf = decodeURIComponent((document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/) || [])[1] || '');
  const r = await fetch(`/simulator/${uid}`, {method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
    body: JSON.stringify({reserva_minima: 500, cenarios: [{nome: 'Inválido', preco: 3000, parcelas: 0}]})});
  const out = {status: r.status, body: await r.json().catch(() => null)};
  console.log(JSON.stringify(out, null, 1));
  return out;
})();
```

| caso | observado | resultado | PR |
|---|---|---|---|
| G1–G7 | | | |

---

## I. Ciclo real da Stripe (último)

Prova o que o admin não prova: checkout, webhook e troca agendada. Se o
telefone da conta ainda não usou o período grátis, o checkout abre um **trial
sem cobrança** com a duração de `PLANS_TRIAL_DAYS` (padrão 15 dias; com os
planos v2 ligados é esta a variável lida, via `plan_service.trial_days_total()`;
`PRO_TRIAL_DAYS` só vale no modo legado). O valor de `PLANS_TRIAL_DAYS` vem do P4. Com trial não há cobrança nem
reembolso, e o I4 cancela dentro dele. Registrar qual dos dois aconteceu.

| caso | passo | esperado |
|---|---|---|
| I0 | No admin, pôr a conta em `free`. A escrita do admin revoga **todos** os grants ativos, inclusive o `admin` das seções A–G (`core/admin_dashboard.py::_gravar_grant_do_admin`) | `/app` redireciona para `/precos`. Rodar a sonda no console de `/precos`: 402 `plan_selection_required` em tudo. A conta nunca passou pelo checkout, então `plan_selected_at` está vazio e esse gate vem antes do `subscription_required` (`frontend/routes/shared.py`, `plan_service.needs_plan_selection`) |
| I1 | Com a conta em `free` (I0) e **sem** assinatura, assinar Plus mensal pelo `/precos` | webhook grava `plan = 'pro'`, com ou sem trial; sonda A igual a S2 em até 1 min |
| I2 | Pedir troca para Essencial em `/precos` (chama `/billing/change-plan`) | troca **agendada** para o fim do período pago; plano atual continua Plus |
| I3 | Cancelar a troca agendada | agendamento some; Plus segue |
| I4 | Mandar `cancelar assinatura` no WhatsApp (o app não tem botão; a resposta traz um link `/d/...?next=/conta` válido por 1 h, que leva ao portal da Stripe) e cancelar **no fim do período** | acesso Plus segue até o fim do período. Quando a assinatura termina, o webhook `customer.subscription.deleted` revoga só os grants `stripe`/`legacy` e reprojeta. Como o I0 já revogou o grant `admin`, sobra `free`: a sonda A dá **402 `subscription_required`** em tudo e o bot bloqueia. **Não** fica igual a S5. Sem o I0, o grant `admin` que sobrevivesse projetaria a conta de volta ao Essencial. Cancelar "agora" ou reembolsar com cancelamento pelo painel da Stripe corta o acesso na hora. Depois do I4, voltar a conta ao Essencial pelo admin se ela ainda for usada |

---

## H. Encerramento (depois de I)

Roda por último, depois do ciclo da Stripe: I1 precisa da mesma conta de teste.

- [ ] `PLUGGY_INCLUDE_SANDBOX` confirmado desligado no Railway (desligado ao fim de F, P5)
- [ ] conexão sandbox removida da conta de teste
- [ ] conta de teste de volta a Essencial pelo admin (depois de I4 ela fica em `free`), ou excluída se não for mais usada
- [ ] contas a pagar e gastos fixos de teste removidos

---

## Defeitos encontrados

| caso | sintoma | causa | PR |
|---|---|---|---|
| C6c | *candidato, visto só no código:* "gastei mais esse mês que no passado?" vira pedido de valor de lançamento | classificador determinístico casa "gastei" com `launches.add` antes da IA | — confirmar em produção |
| F5b | *candidato, visto só no código:* gasto manual lançado depois da transação do banco não vira par | `reconcile_manual_launch` sem chamador em produção; resta medir se o total conta duas vezes | — confirmar em produção |

## O que este roteiro não cobre

- App iOS/PWA: continua no roteiro A de `open_finance_validacao_manual.md`.
- Estados de erro da Pluggy (B1–B6, B12–B15 daquele roteiro): exigem forçar
  falhas no provedor.
- Envio de template pela Meta além do que D e E observam: a seção 0.1 confere a aprovação, mas não testa
  outros idiomas nem outros templates.
- Carga, concorrência e multiusuário: fora do escopo de uma conta de teste.
