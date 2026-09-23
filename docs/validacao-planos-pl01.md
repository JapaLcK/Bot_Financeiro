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
| P2 | Vincular um número de WhatsApp de teste à conta (um número só serve para os três planos) | dono |
| P3 | Anotar o `user_id`: `python -m scripts.whoami <email>` ou `GET /auth/dashboard-profile` logado | dono ou Claude |
| P4 | Conferir no Railway, sem copiar valores para cá, que existem: `WA_BILL_REMINDER_TEMPLATE_NAME`, `WA_WEEKLY_TEMPLATE_NAME`, `WA_MONTHLY_TEMPLATE_NAME`, `RUN_BACKGROUND_TASKS` ≠ `0`, `PLANS_V2_ENABLED` ausente ou `1`. Registrar só "definida / ausente" | dono |
| P5 | Ligar `PLUGGY_INCLUDE_SANDBOX=1` no Railway **só durante a seção F** e desligar ao fim (seção H) | dono |
| P6 | Painel admin (`/admin`) aberto numa aba: é por ele que o plano muda nas seções A–C (botão de plano do usuário → `POST /admin/api/users/{id}/plan`) | dono |

Se P4 mostrar template ausente, os casos D e E ficam **bloqueados**, não
reprovados: o código fica dormente sem template (`adapters/whatsapp/wa_app.py`,
`_bill_reminder_tick`) e isso é configuração, não defeito.

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
próximo webhook dela prevalece sobre o que o admin gravou, e a varredura de
downgrade (a cada 6 h) pode reverter a troca manual no meio de outra seção.

---

## A. Sonda de permissões pela API

Rodar no console do navegador, **logado com a conta de teste em `/app`**, uma vez
por estado S1–S5. Só faz leituras, exceto o simulador (não persiste nada). As
duas rotas de Insights chamam o modelo pago; o cache de 6 h evita custo nas
repetições.

```js
(async () => {
  const j = (r) => r.json().catch(() => null);
  const prof = await fetch('/auth/dashboard-profile', {credentials: 'same-origin'}).then(j);
  const uid = prof.user_id;
  const d = (n) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
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
  const out = {plan: prof.plan, gates: prof.feature_gates};
  for (const [k, [m, url, body]] of Object.entries(calls)) {
    const r = await fetch(url, {method: m, credentials: 'same-origin',
      headers: body ? {'Content-Type': 'application/json'} : {},
      body: body ? JSON.stringify(body) : undefined});
    const b = await j(r);
    out[k] = r.status;
    if (k === 'forecast' && r.ok) out.forecast_keys = {
      horizons: Object.keys(b.forecast.horizons || {}),
      trajectory: 'trajectory' in b.forecast, worst_day: 'worst_day' in b.forecast};
    if (k === 'simulator' && r.ok) out.simulator_body = b.simulacao;  // evidência de G1–G4
  }
  console.log(JSON.stringify(out, null, 1));
  return out;
})();
```

Esperado (403 = recusa por plano, `{"error":"pro_required"}`):

| chave | S1/S5 Essencial | S2/S4 Plus | S3 Pro |
|---|---|---|---|
| `gates.forecast` / `insights` / `financial_comparison` / `weekly_report` / `household_budget` | `false` | `true` | `true` |
| `gates.cashflow` | `false` | `false` | `true` |
| `gates.recurring_expenses` | `true` | `true` | `true` |
| `forecast` | 403 | 200 | 200 |
| `forecast_keys.horizons` | — | `["30"]` | `["30","60","90"]` |
| `forecast_keys.trajectory` / `worst_day` | — | `false` | `true` |
| `proj30` | 403 | 200 | 200 |
| `proj60` | 403 | 403 | 200 |
| `evolution`, `weekday`, `insights`, `patterns`, `household` | 403 | 200 | 200 |
| `recurring` | 200 | 200 | 200 |
| `simulator` | 403 | 403 | 200 |

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
| B1 | card de previsão no `/app` | card travado (`pro-locked`) com a mensagem de bloqueio; o botão "Simular" abre o modal de upgrade; sem erro | só 30 dias | 30/60/90 |
| B2 | Insights e padrões (cards `data-plan-content="insights"` e card do Piggy) | **ocultos**, sem espaço vazio no lugar; totais, categorias e estabelecimentos visíveis | visíveis | visíveis |
| B3 | comparações (cards `data-plan-content="financial_comparison"`) | **ocultos**; KPIs aparecem sem variação percentual | visíveis, KPIs com variação | visíveis |
| B4 | Orçamento Doméstico (menu lateral) | item travado; clicar abre o modal de upgrade e não navega | acessível | acessível |
| B5 | Ajustes → notificações → resumo semanal | ligar é recusado com mensagem; desligar funciona | liga e persiste após F5 | liga e persiste |
| B6 | gastos fixos / contas a pagar | criar e listar funcionam | idem | idem |
| B7 | console do navegador | nenhum erro não tratado | idem | idem |

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
| C3 | `ligar resumo semanal` | recusa com convite, sem ligar | liga | liga |
| C4 | `quanto vou ter de saldo daqui 30 dias?` | recusa com convite | responde | responde |
| C5 | `e daqui 60 dias?` | recusa | explica o limite de 30 dias, sem inventar número | responde |
| C6 | `gastei mais esse mês que no passado?` | recusa com convite: a IA chama `compare_periods`, que devolve `pro_required` no Essencial | compara | compara |
| C6b | `quanto gastei esse mês?` | responde com o total do mês (análise básica, liberada) | idem | idem |
| C7 | `se eu comprar um celular de 3 mil em 12x com 600 de entrada e juros de 1,49% ao mês, como fica meu caixa?` | recusa | recusa com convite ao Pro | simula (conferir contra G2) |
| C8 | `resumo da semana` (pedido manual) | responde | responde | responde |

C3 e C8 separam o resumo **automático** (Plus+) do resumo **pedido**, que a
matriz mantém em todos os planos.

| caso | estado | resposta observada | resultado | PR |
|---|---|---|---|---|
| C1–C8 | S1 | | | |
| C1–C8 | S2 | | | |
| C1–C8 | S3 | | | |
| C3, C5, C7 | S4 | | | |
| C3, C4, C6, C6b | S5 | | | |

---

## D. Lembrete de vencimento (todos os planos)

Como o código decide (`_bill_reminder_tick`): roda a cada 5 min, só a partir de
`WA_BILL_REMINDER_HOUR` (padrão 9 h, fuso `America/Sao_Paulo`), envia quando
faltam `WA_BILL_REMINDER_DAYS_BEFORE` dias (padrão 3), no dia do vencimento e 1
dia depois, uma vez por dia por conta (`bill_instances.reminder_last_sent_on`).

| caso | passo | esperado | observado | resultado |
|---|---|---|---|---|
| D1 | Em S1, criar conta a pagar `Teste PL01`, R$ 12,34, vencimento hoje + 3 dias, depois das 9 h | template chega no WhatsApp em até ~5 min com nome, valor e data | | |
| D2 | Esperar 30 min | nenhum segundo envio no mesmo dia | | |
| D3 | Tocar "Já paguei" | conta marcada paga no dashboard; nenhum lembrete no dia seguinte | | |
| D4 | Nova conta vencendo hoje | lembrete do dia chega | | |

---

## E. Resumo semanal e mensal (calendário)

O semanal sai **só na segunda-feira**, no horário do report diário do usuário; o
mensal, **só no dia 1**. Não há disparo manual em produção, então estes casos
têm data marcada:

| caso | data | estado da conta | preferência | esperado |
|---|---|---|---|---|
| E1 | seg 28/09/2026 | Plus (S2 ou S4) | semanal ligado | resumo chega uma vez, no horário escolhido |
| E2 | qui 01/10/2026 | qualquer | mensal ligado | resumo mensal chega uma vez |
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
| F1 | Conectar um conector sandbox em Ajustes → Open Finance | conexão aparece; pílula âmbar "Ainda não sincronizou" até o primeiro sync, nunca "Tudo em dia!" antes dele (B8) |
| F2 | Aguardar o sync ou tocar "↻ Atualizar" | "Última sync" muda de horário; contas e saldo aparecem (B7/B9) |
| F3 | Com o dashboard aberto numa segunda aba, disparar F2 | dashboard repinta sozinho, sem F5 (B10) |
| F4 | Comparar saldo exibido com o do conector sandbox | mesmo valor |
| F5 | Pelo WhatsApp, registrar um gasto com **mesmo valor** (±R$ 0,05) e **data** (±3 dias) de uma transação sandbox, com o nome do estabelecimento dela | vira par em Conciliação: automático se candidato único, data ±1 e nome parecido; senão "pendente" pedindo decisão (`pick_reconciliation_match`) |
| F6 | Confirmar o par | gasto conta uma vez só nos totais do mês |
| F7 | Desfazer | volta a pendente/importado; totais corretos, sem sumir nem duplicar |
| F8 | Rejeitar outro par | os dois lançamentos ficam separados e contam como dois |
| F9 | Previsão (S3) antes e depois de F2 | saldo de partida da previsão muda junto com o saldo sincronizado |

| caso | observado | resultado | PR |
|---|---|---|---|
| F1–F9 | | | |

---

## G. Simulador (Pro)

Em S3, depois de F (saldo sincronizado) e, se possível, também antes de F (sem
banco), para ver o aviso de saldo incompleto.

| caso | passo | esperado |
|---|---|---|
| G1 | Sonda A em S3: `simulator` = 200 e `simulator_body` preenchido | `simulator_body` com `atual`, `cenarios` (2), `premissas`, `reserva_minima` = 500. G2–G4 se conferem nesse mesmo objeto |
| G2 | Conferir o cenário `12x` | financiado R$ 2.400 em 12 parcelas a 1,49% a.m. (Price) ≈ **R$ 219,89**/mês; custo total ≈ **R$ 3.238,7x** com a entrada (a última parcela absorve o arredondamento) |
| G3 | Conferir o cenário `À vista` | saída única de R$ 3.000 hoje; pior saldo cai R$ 3.000 contra o `atual` |
| G4 | Nenhum cenário marcado como "melhor" | a resposta compara liquidez e custo sem eleger vencedor |
| G5 | Mesmo pedido pelo chat (C7) | números iguais aos de G2/G3 |
| G6 | Sem conexão bancária (antes de F) | `aviso_saldo` presente na resposta do chat |
| G7 | Payload inválido: `parcelas: 0` (pedido abaixo) | 400 `invalid_simulation` com `errors`, sem 500 |

Pedido do G7, no console, logado em S3:

```js
(async () => {
  const {user_id: uid} = await fetch('/auth/dashboard-profile', {credentials: 'same-origin'}).then(r => r.json());
  const r = await fetch(`/simulator/${uid}`, {method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'},
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

## H. Encerramento

- [ ] `PLUGGY_INCLUDE_SANDBOX` desligado no Railway
- [ ] conexão sandbox removida da conta de teste
- [ ] conta de teste de volta a Essencial (ou excluída, se não for mais usada)
- [ ] contas a pagar e gastos fixos de teste removidos

---

## I. Ciclo real da Stripe (último)

Cobrança real no cartão do dono, com reembolso pelo painel da Stripe depois.
Prova o que o admin não prova: checkout, webhook e troca agendada.

| caso | passo | esperado |
|---|---|---|
| I1 | Com a conta em Essencial pelo admin e **sem** assinatura, assinar Plus mensal pelo `/precos` | webhook grava `plan = 'pro'`; sonda A igual a S2 em até 1 min |
| I2 | Pedir troca para Essencial em `/precos` (chama `/billing/change-plan`) | troca **agendada** para o fim do período pago; plano atual continua Plus |
| I3 | Cancelar a troca agendada | agendamento some; Plus segue |
| I4 | Cancelar a assinatura e reembolsar pela Stripe | acesso segue até o fim do período; depois disso, sonda A igual a S5 |

---

## Defeitos encontrados

| caso | sintoma | causa | PR |
|---|---|---|---|
| | | | |

## O que este roteiro não cobre

- App iOS/PWA: continua no roteiro A de `open_finance_validacao_manual.md`.
- Estados de erro da Pluggy (B1–B6, B12–B15 daquele roteiro): exigem forçar
  falhas no provedor.
- Aprovação dos templates pela Meta: pré-requisito verificado em P4, não testado.
- Carga, concorrência e multiusuário: fora do escopo de uma conta de teste.
