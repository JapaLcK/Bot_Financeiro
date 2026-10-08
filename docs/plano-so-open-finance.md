# Só Open Finance: inventário do input manual e roteiro

> **Roteiro aprovado pelo dono em 2026-10-07 (respostas no §8).** Ele dá a ordem das fases;
> cada fase ainda tem plano e PR próprios, na faixa do `CLAUDE.md` §0.
>
> O inventário (§2) e os `arquivo:linha` dos §2–§4 são uma conferência estática na base
> `42a8cfc9` (`main` logo depois do #847). Eles envelhecem a cada commit: releia os
> símbolos e refaça os `grep` na base de cada PR. O único dado de produção está no §9,
> com data e comando.

## Estado das fases

| Fase | Situação |
|---|---|
| 0. Decisões e medição | P1–P4 e P6 respondidas; **P5 e P7 em aberto** (§8). Medição das recorrências feita em 2026-10-08 (§9); o resto da medição do §4 não foi feito. |
| 1a. Previsão lê as recorrências do OF | **Feita** no #866 (§4). Falta o 1a-2 (ignorar receita). |
| 1b. Fatura OF paga pelo extrato | A fazer. Vem antes de travar o "pagar fatura" do cartão OF. |
| 1c. Custo mensal do OF | A fazer, junto da Fase 4. |
| 2. Parar de aceitar | A fazer. Depende da 1b (para o #5) e da P7 (para o #10 e o #19). |
| 3. Esconder o legado | A fazer. |
| 4. Metas sobre o OF | Espera a P5. |

Também já mergeados: #851 (total investido só do OF, com divisão por tipo e banco) e #864
(texto invertido da direção do erro na Previsão, web e app nativo).

## 1. A decisão e o que ela supera

**Dono, 2026-10-07:** "O aplicativo vai ser uma extensão do Open Finance: todo dado tem de
vir do Open Finance, sem input de dado manual."

Alcance combinado:
- Vale para **o produto inteiro**: `/painel`, `/app`, WhatsApp, IA (`/ai/chat` e as tools,
  inclusive pelo app nativo) e o app nativo.
- **A Carteira é a exceção.** Ela é o dinheiro vivo e continua aceitando lançamento manual,
  porque o banco não enxerga dinheiro físico.
- **As metas ficam**, mas sobre dado do banco. O usuário define só o alvo e a data. O
  progresso vem do Open Finance, e não existe mais depositar nem retirar à mão.
- **O legado manual sai da tela e fica no banco de dados.** Nada novo é aceito, nada é
  apagado. Apagar é uma decisão para o futuro.

| Decisão anterior | Situação |
|---|---|
| Q36 (OF é a fonte única; o único manual é a Carteira) e o PR D da Etapa 1 (trava `fonte_unica` só para quem tem a chave `dashboard_v2_enabled`) | **Ampliada.** Passa a valer para todos e cobre mais casos. O mecanismo é reaproveitado (§4, Fase 2). |
| Q37 (o manual antigo fica só para leitura, como "registro manual antigo") | **Superada.** O legado sai da tela (Fase 3). |
| Q38 (a caixinha manual continua, com depositar e retirar) | **Superada.** |
| Q40 decisão A2 (sem banco conectado, todo lançamento vai para a Carteira) | **Superada** pela P1. A Carteira passa a ser só dinheiro vivo para todo mundo. |
| Q42 (recorrente só prevê) | **Superada.** O recorrente manual sai. O que é previsto passa a vir das recorrências que o OF detecta (Fase 1). A receita manual depende da P7. |
| Etapa 4, perguntas próprias dela: P2 (custo mensal pelos fixos manuais), P6 (depositar pela Carteira), P7 (histórico de movimentos de caixinha), P-reserva (valor informado) | **Superadas ou em conflito.** Ver §6. |
| Q41 (saque e depósito em espécie do OF mexem na Carteira) | **Continua.** É dado do OF. |

## 2. Inventário: onde o dado manual entra

Legenda dos canais:
- **WA**: WhatsApp, via `core/intent_router.py::_execute` e os handlers.
- **IA**: tools de `core/services/ai_chat/tools/`. Atendem o `/ai/chat` do `/app`, o
  WhatsApp e o **app nativo**, que só escreve por `/ai/chat` (`app/src/services/painel.ts:14`).
- **/app**: rotas do monólito (`frontend/finance_bot_websocket_custom.py`, abreviado `mono`) e
  de `frontend/routes/`.
- **v2**: `/api/v2` e `/api/app`, que é o mesmo sub-app com outra sessão
  (`api/nativo/app.py:28`).

O Discord não roda (fora do `launch.py` desde o PR 5a). Os cogs dele ficam fora do
inventário.

### 2.1 Dado financeiro manual: tem de parar

| # | O que é | Canais e pontos de entrada | Escritor e tabelas | Hoje já travado? |
|---|---|---|---|---|
| 1 | **Gasto/receita manual fora da Carteira** (Pix, débito, cartão "lançado à mão"). Só existe hoje para quem **não tem OF**: `decidir()` devolve CARTEIRA sem consultar nada (A2). | WA `core/handlers/launches.py:1764` → `:1113`/`:1157`; IA `add_launch` (`tools/launches.py:344`, `:901`); /app `POST /launches` `mono:7494` → `core/services/carteira.py:24`; v2 `POST /lancamentos/carteira` (`api/v2/lancamentos.py:177`, sempre como dinheiro) | `db/accounts.py:75` `add_launch_and_update_balance` → `launches` + `accounts.balance` | Com OF, a Q40 já pergunta "dinheiro ou banco" e "banco" não grava (`core/handlers/forma_pagamento.py:82-88`). **Sem OF, grava tudo na Carteira.** |
| 2 | **Lançamento na Carteira (dinheiro vivo)**, saldo inicial e ajuste | os mesmos do #1, mais /app `mono:8622` (initial-balance) e `mono:8657` (adjust-balance) | o mesmo do #1 | **Fica** (é a exceção) |
| 3 | **Cartão de crédito manual**: criar, editar dias, limite, apagar | WA `credit.py:1548` (fluxo), criação em `:1574`, `:1832`, `:1954`, `:2231`; limite `:2018`, `:2737`; apagar `:1710`, `:1876`. /app `frontend/routes/cards.py:35`, `:234`, `:318` | `db/cards.py:157` `create_card`, `:193` `update_card_meta`, `:457`, `:307` → `credit_cards` | **Não** |
| 4 | **Compra manual no cartão** (à vista e parcelada) | WA `credit.py:976` → `:924`/`:1042`; IA `add_credit_purchase` (`tools/cards.py:537`); /app `POST /launches` tipo `credito` `mono:7617`/`:7659` | `db/cards.py:902`, `:944` → `credit_transactions`, `credit_bills` | Só para quem tem a chave (Q36). Sem a chave e com OF, grava se o cartão for manual (`credit.py:773`). |
| 5 | **Pagar fatura à mão** (do cartão manual **e** do cartão OF) | WA `credit.py:118`, `:161`, `:2799`; IA `pay_bill` (`tools/cards.py:590`, chamada em `:378`); /app `frontend/routes/cards.py:645` | `db/cards.py:1671` `pay_bill_amount` → `credit_bills.paid_amount/status` (+ `launches`; com OF, delta 0) | **Não.** É hoje o **único** jeito de uma fatura OF ficar paga (§3.3). |
| 6 | **Parcelamento**: antecipar, editar, apagar grupo, desfazer compra | /app `cards.py:410`, `:465`, `:427`; WA `credit.py:2079`, `:2100`; IA `delete_launch` (`tools/launches.py:521`, `:535`) | `db/cards.py:1358`, `:1560`, `:1188`, `:1121` | Não |
| 7 | **Importar fatura OFX** | WA `core/handle_incoming.py:999`, `:1009`; /app `mono:8121` → `:8164` | `core/services/ofx_service.py:154` → `db/cards.py:2151` | Só para quem tem a chave |
| 8 | **Importar extrato OFX/CSV/PDF**. É input manual: é arquivo, não OF. | WA `handle_incoming.py:998`, `:1013`, `:1058`; /app `mono:8166` | `ofx_service.py:74`, `statement_service.py:95` → `db/accounts.py:2453` (`launches`) + `set_balance` (`ofx_import.py:266`, `statement_import.py:714`) | Só para quem tem a chave |
| 9 | **Gasto fixo / recorrente manual** (autopay e "conta a pagar" `payment_mode='manual'`) | WA `core/handlers/recurring.py:63` → `:178`; WA, aceitar sugestão de fixo `core/handlers/pending.py:159` (a oferta nasce em `launches.py:1044`); /app `mono:8934`, `:8956`, `:9006` | `db/recurring.py:159`, `:255`, `:378` → `recurring_expenses` | Não |
| 10 | **Receita fixa manual** | WA `recurring.py:152`; /app `mono:9078`, `:9101`, `:9149` | `db/recurring_income.py:106`, `:163`, `:258` → `recurring_incomes` | Não |
| 11 | **Boleto / conta avulsa** | IA `add_boleto` (`tools/bills.py:407`, `:279`); /app `mono:8874`, `:8890`, `:8909` | `db/bills.py:93`, `:118`, `:148` → `bill_instances` | Não |
| 12 | **Instância de conta a pagar gerada sozinha** (o próximo ciclo do #9 manual) | job `core/services/recurring_charger.py:135` | `db/bills.py:161` → `bill_instances` | Não (deriva do #9) |
| 13 | **Marcar conta como paga** | WA `core/handlers/bills.py:99` → `forma_pagamento.py:198`/`:207`; IA `mark_bill_paid` (`tools/bills.py:372`); /app `mono:8797`; WA botões `adapters/whatsapp/wa_runtime.py:1155`, `:1326` | `db/bills.py:176` → `bill_instances` + `launches` (Carteira se for dinheiro) | Não (some junto com o #9 e o #11) |
| 14 | **Caixinha manual**: criar | WA `core/handlers/pockets.py:27`; IA `create_pocket` (`tools/pockets.py:139`); /app `frontend/routes/pockets.py:39` | `db/pockets.py:599` → `pockets`, `pocket_lots`, `launches` | Não |
| 15 | **Depositar / retirar de caixinha** | WA `pockets.py:121`, `:254`, `:277`; IA `tools/pockets.py:251`, `:292`; /app `pockets.py:326`, `:361` | `db/pockets.py:688`, `:380` → `pockets`, `pocket_lots`, `launches`, `accounts`, `bank_movement_declarations` (`db/bank_movements.py:197`, `:299`) | Não. O espelho do banco já recusa (`_is_of_mirror`). |
| 16 | **Meta**: alvo e data (`pockets.target_*`), apagar caixinha | /app `pockets.py:114`, `:290`; WA `pending.py:281`; IA `tools/pockets.py:324` | `db/pockets.py:274`, `:780` | — (a **meta** fica; o formato muda, §6) |
| 17 | **Vincular meta a posição do banco** | /app `frontend/routes/open_finance.py:1721` | `db/open_finance.py:1364` | Superado pela Etapa 4 nova |
| 18 | **Investimento manual**: criar, aportar, resgatar, apagar | WA `core/handlers/investments.py:319` (só recusa ou manda ao painel), `:400`, `:518`; WA apagar `pending.py:314`; IA `tools/investments.py:160`, `:195`, `:247`, `:277`; /app `mono:9214`, `:9275`, `:9318`, `:9352` | `db/investments.py:1002`, `:1073`, `:1396`, `:1545`, `:1244` → `investments`, `investment_lots`, `launches` | Criar e aportar: só para quem tem a chave. Resgatar e apagar: liberados. |
| 19 | **Renda do mês informada** (orçamento doméstico 50/30/20) | /app `mono:8440` | `db/household_budget.py:163` → `household_budget_income` | Não |
| 20 | **Apagar ou editar lançamento importado do banco** | editar: só categoria e descrição em todo canal (data travada em `db/accounts.py:517`; v2 pelo `PODE_SQL`, `db/lancamentos.py:84`). Apagar sombra: v2 recusa (409). **O "apagar tudo" da IA apaga as sombras do banco, e elas não voltam** (`tools/launches.py:580` → `db/accounts.py:2267`; `tests/test_apagar_fundida_desfaz_juncao.py:148`). | `launches` | Editar é organização e fica. **O "apagar tudo" apagando dado do OF contradiz a decisão.** |

### 2.2 Organização sobre dado do banco: proposta é ficar (§7, premissa)

| O que é | Onde |
|---|---|
| Categoria e regra de categoria (criar, renomear, arquivar, regra) | /app `mono:8490`-`:8566`; IA `tools/categories.py:156`, `:176`, `:195`; WA `intent_router.py:1380`, `:1383`; `db/categories.py:176`, `:591` |
| Orçamento por categoria (limite) | /app `mono:8311`, `:8360`; IA `tools/budgets.py:225`, `:302`; `db/budgets.py:83` |
| Percentuais do 50/30/20 | /app `mono:8423`; `db/household_budget.py:131` |
| Marcar assinatura / ignorar | v2 `api/v2/assinaturas.py:66` (também no app nativo, `painel.ts:15`) |
| Confirmar ou recusar conciliação, movimento e saque em espécie | /app `frontend/routes/open_finance.py:2698`, `:2725`, `open_finance_cash.py:30` |
| Nome, cor e ordem de cartão | `frontend/routes/cards.py:215`, `:234` (sem os dias nem o limite) |
| Simulador de compra (hipótese, não grava) | `frontend/routes/simulator.py:18`; IA `simulate_purchase` |

### 2.3 Quem consome o dado manual

| Consumidor | Lê | Onde |
|---|---|---|
| **Previsão / motor** (v2, IA `check_cashflow`/`forecast_balance`, simulador) | Carteira; `recurring_expenses` (#9); `recurring_incomes` (#10); `bill_instances` (#11–13); `credit_bills` do cartão manual (#3–5) e do OF | `core/services/cashflow_snapshot.py:91-101`, `:149-180`, `:182-214`, `:216-245` |
| **Resumo do mês** (`resumo-do-mes`, consulta 5 do /app, "Gastos em" do WA, relatório mensal, KPIs) | `launches` não internos de toda origem (o manual antigo também) e `credit_transactions` (o cartão manual também) | `db/resumo_mes.py:32-57` |
| **Lançamentos v2** | o manual antigo como `registro_antigo` | `db/lancamentos.py:42`, `:129`, `:164` |
| **Patrimônio / foto diária / bloco de contas** | Carteira, caixinhas manuais (`of_investment_id is null`), investimentos manuais | `db/patrimonio.py:143`, `:171`, `:184`; `db/contas_hoje.py` |
| **Piggy**: Carteiro (vencimentos), Cofre/Banqueiro (caixinhas, palpite de reserva), insights, padrões da IA | `bill_instances` + `recurring_expenses`; `pockets`; `list_recurring_expenses` | `core/services/piggy_agents.py:318-340`, `:670-760`; `db/insights.py:239`, `:400`; `core/ai_patterns.py:290`, `:337` |
| **Avisos** | lembrete de conta (`bill_instances`), aviso de autopay (`recurring_expenses`) | `adapters/whatsapp/wa_app.py:542-565`; `recurring_charger.py:169`, `:228` |
| **IA, leitura** | total investido **só do manual**; caixinhas; contas a pagar; próxima fatura | `tools/investments.py:75`; `tools/pockets.py:87`; `tools/bills.py:64`; `tools/cards.py:246` |
| **WhatsApp, leitura** | "minhas caixinhas", "meus investimentos" (total só do manual), saldo | `core/handlers/pockets.py:14`; `core/handlers/investments.py:261-292`; `core/handlers/balance.py:11` |
| **Plano/limites** | contagem de lançamentos do mês, de caixinhas e de cartões | `core/services/plan_service.py:702`, `:752`, `:766` |
| **Qualidade da Carteira** | todas as tabelas manuais | `db/carteira_qualidade.py` |
| **Privacidade** | exportação, reset, exclusão | `db/privacy.py` (**continua igual**: o dado fica) |

## 3. Pontos que pediram atenção

### 3.1 Previsão: o que sobra sem o manual

Na base deste inventário, os compromissos do motor vinham **todos** de tabela manual: os
fixos (#9), as receitas fixas (#10) e os boletos e contas a pagar (#11–13). A exceção era a
fatura do cartão OF (`cashflow_snapshot.py:149-245`).

**Desde o #866 (Fase 1a), o motor também lê o `of_recurring_payments`** (Recurring Payments
da Pluggy), pela fonte `recorrencia_banco` (`core/services/previsao_recorrencias.py`). A
regra está no `docs/CLAUDE.md`, em `/api/v2/previsao`. A Pluggy só detecta recorrência de
cerca de 30 dias (`core/services/assinaturas.py`), de despesa e de receita.

**A receita é o buraco.** O inventário supunha que o salário viria como recorrência
positiva. A medição de 2026-10-08 (§9) achou 1 recorrência de entrada em produção, já
parada. Quando o fixo e a receita manuais saírem (Fases 2 e 3), sobram na Previsão o saldo,
as faturas dos cartões OF, as recorrências de despesa detectadas e as pendências, **quase
sempre sem receita nenhuma**. Isto é a P7 (§8).

O que o OF não detecta:
- Recorrência anual, semanal ou única: IPVA, IPTU, matrícula, anuidade.
- Boleto antes de ser pago. Não pedimos produto de boletos nem de empréstimos:
  `PLUGGY_PRODUCTS` = ACCOUNTS, TRANSACTIONS, CREDIT_CARDS, INVESTMENTS
  (`core/services/pluggy.py:302-310`).
- Parcela de financiamento ou empréstimo fora do cartão. Só aparece quando o débito cai, e
  então como recorrência se for mensal.

### 3.2 WhatsApp: "gastei 50 no mercado"

- **Com OF:** sem forma declarada, o bot pergunta "dinheiro vivo ou banco?" e espera a
  resposta. "Dinheiro" grava na Carteira. "Banco" não grava e mostra o que já chegou no
  extrato (`launches.py:1764-1790`, `forma_pagamento.py:82-88`).
- "No cartão" com OF fica com o OF quando o cartão é sincronizado ou quando não existe
  cartão manual (`credit.py:773`, `:976`). **Com um cartão manual, grava nele.**
- **Sem OF:** `decidir()` devolve CARTEIRA sem perguntar (`forma_pagamento.py:82-88`). Pix,
  débito e transferência caem na Carteira. "No cartão" grava no cartão manual ou pede para
  criar um (`credit.py:998`).
- **O que muda:** a Carteira passa a ser só dinheiro vivo **para todos**. Com ou sem OF,
  qualquer coisa que não seja dinheiro recebe o texto de "chega pelo banco". Sem OF, o texto
  é "conecte seu banco" (P1). Recusar sem dizer o que fazer gera retentativa e frustração.
  Por isso todo texto de recusa leva a saída.
- Também mudam: "criar cartão", "gastei no cartão X" manual, "paguei a fatura", "paguei a
  luz" (conta manual), "todo mês pago X" (fixo), "guarda 100 na caixinha", "aportei", o envio
  de OFX/CSV/PDF e o "sim" à oferta de fixo (`pending.py:159`). Todos viram recusa com
  orientação, por uma fonte única de textos (`core/services/fonte_unica.py::MENSAGENS`).

### 3.3 Cobertura do Open Finance e lacunas

| Item | O OF cobre? | Lacuna |
|---|---|---|
| Conta corrente: saldo e transações | Sim | Transação pendente vira dois registros (§7 do plano do v2) |
| Cartão: compras e parcelas | Sim (conta CREDIT → `credit_transactions`) | A fatura é **calculada localmente**, sem o `/bills` da Pluggy. O calendário vem do `creditData` e cai em 1/10 sem ele (`db/cards.py:612-617`). |
| **Fatura paga** | **Não.** O pagamento no extrato é reconhecido como interno (`db/open_finance.py:1951-1976`), mas **nada grava `credit_bills.paid_amount`**. | Sem o "pagar fatura" manual (#5), toda fatura OF já paga fica a conferir na Previsão. Precisa de detector (Fase 1b). |
| Investimentos | Sim (`/investments`) | A rentabilidade não chega (medição de 2026-09-29). |
| Caixinha do banco | Em parte. Nubank: cada aporte vira uma posição, sem nome. Só o CDB da NU FINANCEIRA é reconhecido (`db/open_finance.py:1188-1215`). Cerca de 2/3 do valor vem sem subtipo e não é reconhecido (medição de 2026-10-07 em uma conta; remeça). | Meta presa a "caixinha" herda esse buraco. Meta presa a "investido na instituição" não herda (§6). |
| Recorrências | Mensais, despesa e receita; entram na Previsão desde o #866 | Receita quase nunca detectada (§3.1, §9). Recorrência no cartão fica fora do cálculo. |
| Saldo de partida | Sim (bancos) e Carteira | Conta em outra moeda fica fora (decisão "só reais") |

**Sem equivalente no OF hoje** (o que fazer com cada um saiu da P1 e da P4):
- **Dinheiro vivo:** fica na Carteira.
- **Banco ou cartão sem conector na Pluggy:** a lista responde por `GET /connectors`
  (`core/services/pluggy.py:225`). Não foi consultada aqui.
- **Vale-refeição e vale-alimentação** (Alelo, Pluxee, VR, Caju, Flash): **não verificado**
  se há conector. A mesma consulta responde.
- Boleto antes de pagar, contas anuais, empréstimo e financiamento fora do cartão (§3.1).
- Dívida entre pessoas, conta conjunta de outro titular, conta no exterior em outra moeda
  (já fica fora das somas).
- **Usuário sem nenhum banco conectado.** O app nativo já barra esse usuário
  (`api/nativo/app.py:17-21`, `open_finance_onboarding_required`). O `/app` e o WhatsApp
  não barram.

## 4. Roteiro em fases

A ordem segue uma regra: **primeiro trocar a fonte de quem consome, depois parar de aceitar,
por último esconder.** Parar antes de trocar esvazia a Previsão. Esconder antes de parar
deixa o usuário gravar o que não vê.

### Fase 0: decisões e medição (só leitura)

**Estado:** respostas no §8 (P5 e P7 abertas). Das medições abaixo, só a das recorrências foi
feita (§9).

- **Faz:** colhe as respostas às perguntas do §8. Com autorização do dono e aviso antes,
  roda uma medição **só leitura** em produção, por script:
  - pagantes sem conexão OF viva;
  - uso de cada item #1–19 nos últimos 90 dias, por usuário;
  - recorrências positivas no `of_recurring_payments`;
  - quantos usuários têm cartão manual com compra recente, caixinha manual com saldo e
    investimento manual com saldo.
- **Não faz:** nenhuma escrita. Toca dinheiro? Não. Motor? Não. Migração? Não. WhatsApp? Não.
- **Pronto:** P1–P6 respondidas. Números com data e comando, para remedir antes de reusar.

### Fase 1: trocar a fonte (antes de parar de aceitar)

**1a. A Previsão passa a ler as recorrências do OF. Faixa Completo. FEITA no #866
(2026-10-08).**
- **Fez:** `cashflow_snapshot.ler` gera ocorrências da fonte `recorrencia_banco` ("Detectado
  no banco"), em `core/services/previsao_recorrencias.py`. A regra é uma só para `/painel`,
  `/app`, IA, WhatsApp e simulador, porque todos passam por `ler`.
- Decisões do dono no PR:
  - o fixo manual casado conta no lugar do do banco, a partir do 1º ciclo dele;
  - a receita do banco entra como "não garantida";
  - o valor é o último cobrado, não a média;
  - a regra vale para todos os usuários com banco, não só para a chave do v2.
- **Limites declarados:**
  - autotransferência com descrição genérica entra como recorrência;
  - duas conexões do mesmo banco dobram a cadeia;
  - recorrência de conta fora do saldo de partida fica fora.
- **Não fez:** remover o fixo manual, estimar o gasto variável, criar recorrência anual.
- **Ficou para um PR seguinte (1a-2, Completo):** ignorar uma **receita** detectada e
  ignorar a partir da Previsão. Hoje a marca `ignorar` vale só para saída.

**1b. Fatura OF paga pelo extrato. Faixa Completo: dinheiro e ingestão do OF.**
- **Faz:** o pagamento de fatura reconhecido no extrato quita a fatura certa do cartão OF da
  mesma instituição. Isso pode ser por casamento valor × data × cartão ou pela leitura do
  `/bills` da Pluggy (escolher no PR, medindo). Ambíguo vira motivo, nunca quitação
  silenciosa.
- **Não faz:** cartão manual.
- **Pronto:** fatura paga no banco sai da Previsão sem ação do usuário. Pagamento parcial.
  Duas faturas abertas. Estorno. Reconexão. O mesmo pagamento não quita duas faturas.

**1c. Custo mensal do OF.** Entra na Etapa 4 (§6). O Carteiro e o aviso de vencimento passam
a ler a recorrência do OF ou saem (P4). Faixa Leve, e Completo se mexer no motor.

### Fase 2: parar de ACEITAR, por canal (reaproveita a trava da Q36)

A trava já mora nos escritores que todos os canais chamam (`fonte_unica.exigir`, presa por
`tests/test_fonte_unica_q36.py:351`, que reprova escritor novo sem classificação). A fase
**estende a trava, não cria outra.**

**2a. Novos casos na trava. Faixa Completo: dinheiro, WhatsApp, IA, /app.**
- **Faz:** novos casos em `fonte_unica.MENSAGENS`:
  - `cartao_manual`: `create_card` e editar dias/limite de cartão manual; `pay_bill_amount`
    do cartão manual. O do cartão OF só depois da 1b. O parcelamento do cartão manual
    (#6): antecipar, editar, apagar grupo e desfazer compra.
  - `recorrente`: `create_recurring_expense`/`_income` e `update_*`. O "sim" à sugestão de
    fixo (`pending.py:159`) e a oferta em `launches.py:1044` saem.
  - `conta`: `create_boleto`/`update_boleto` e marcar conta manual como paga
    (`db/bills.py:176`, #13).
  - `caixinha`: `create_pocket`, depositar, e retirar e apagar caixinha manual com saldo
    (P3: congelada).
  - `investimento`: resgatar e apagar investimento manual com saldo (P3: congelado). Isto
    supera a liberação da Q36, que deixava os dois livres.
  - `renda_informada`: `set_income_override`.
- Regra de alcance: **todo escritor do §2.1 que muda legado** entra na trava ou tem motivo
  escrito para ficar livre. Esconder a tela (3a) não desliga o WhatsApp, a IA nem a rota
  direta.
- O escopo deixa de ser "tem a chave" e passa a ser a regra da P2.
- O caso `recorrente` da receita (`create_recurring_income`) espera a P7.
- Os textos da IA (`system_prompt.py`) e o `validate` das tools recusam antes de pedir
  confirmação, como já fazem em `tools/investments.py:130`.
- **Não faz:** esconder, apagar, mexer em leitura.
- **Pronto:** para cada caso e canal (WA pelo `handle_incoming` com conversa de duas
  mensagens, IA, /app, v2), três provas: recusa sem gravar, com o texto e a saída; a Carteira
  em dinheiro segue gravando (controle positivo); a chave falhando segue a política da P2. O
  teste-portão é atualizado.

**2b. A Carteira é só dinheiro vivo para todos. Faixa Completo: WhatsApp e dinheiro.**
- **Faz:** `forma_pagamento.decidir` deixa de devolver CARTEIRA para quem não tem OF.
  - "Banco", "cartão" e "Pix" viram recusa com "conecte seu banco".
  - Forma desconhecida pergunta, como já acontece com OF.
  - /app `POST /launches` e IA `add_launch` vêm juntos, porque passam por `decidir`.
- **Não faz:** mexer no saldo existente da Carteira (P3).
- **Pronto:** a conversa real pelo `handle_incoming`. Por exemplo, "gastei 50 no mercado" →
  pergunta → "pix" → não grava, e "dinheiro" → grava. Áudio. Duas transações na frase.
  Negação. "Gastei 50 no cartão" sem cartão nenhum.

**2c. O "apagar tudo" não apaga dado do OF. Faixa Completo: dinheiro.**
- **Faz:** `delete_all_launches_and_rollback` passa a apagar só a Carteira (#20).
- **Pronto:** sombras e fundidas intactas depois do "apagar tudo". Controle negativo.

**2d. Textos. Faixa Direto.**
- **Faz:** ajuda e comandos (`core/help_text.py`, `core/commands_catalog.py`,
  `core/handlers/help_handler.py`, `frontend/comandos.html`, `funcionalidades.html`,
  `como-funciona.html`) param de ensinar o input manual.

### Fase 3: ESCONDER o legado da tela

Segue a P3: o legado sai da tela **e** dos totais. Os números que deixam de contar o legado
são dinheiro, então essa parte é Completo.

**3a. Telas. Faixa Leve.**
- **Faz:** o `/app` some com as abas e os botões de:
  - recorrentes, contas a pagar e receitas fixas;
  - cartão manual;
  - caixinhas manuais;
  - investimentos manuais;
  - importar.

  O WhatsApp e a IA param de listar o legado: "minhas caixinhas", "meus investimentos",
  "contas a pagar". O `/painel` decide se `registro_antigo` some de Lançamentos.
- Service worker e `CACHE_NAME` pelo portão (`docs/armadilhas.md`).

**3b. Totais sem o legado. Faixa Completo: dinheiro e foto diária.**
- **Faz:** Patrimônio, foto e contas sem caixinha nem investimento manual. Resumo do mês sem o
  lançamento manual não-Carteira e sem o cartão manual. Previsão sem os fixos, as contas e as
  faturas manuais.
- A foto diária **quebra a linha** quando a composição muda (regra do §4 do plano do v2), em
  vez de registrar perda.
- **Não faz:** apagar linha. A exportação LGPD continua levando o legado.
- **Pronto:** comparação antes × depois por usuário de teste, com cada diferença explicada.
  Controle negativo. Exportação inalterada.

### Fase 4: Etapa 4 reescrita (metas sobre o OF). Ver §6.

### Fase 5 (futura, fora deste plano)
- Apagar o legado e o código morto dos escritores manuais. Decisão do dono, depois de
  medir que nada mais lê.

**Dependências:**
- 0 → 1a → (2a ∥ 2b ∥ 2c ∥ 2d) → 3a → 3b.
- A 1b vem antes de travar o "pagar fatura" do cartão OF.
- A P7 vem antes de travar a receita manual e a renda informada (2a) e antes de tirá-las da Previsão
  (3b).
- A Fase 4 pode correr em paralelo à Fase 2 depois da P5 e da P6.
- A P2 manda aplicar 2a–2c **primeiro só à coorte do v2** (a chave atual), depois a todos.
  A chave é uma lista de e-mail e id (`plan_service.dashboard_v2_enabled`) e **não prova
  que a conta não tem legado** (`docs/etapa3-previsao-inventario.md`, "Coorte e
  recuperação"). Antes de ligar, meça o legado dessa coorte (cartão manual, caixinha,
  investimento, fixo e receita manuais) e trate quem tiver pela P3, como os demais.

**O que cada fase toca:**

| Fase | Dinheiro | Motor | Migração/schema | WhatsApp |
|---|---|---|---|---|
| 1a | lê | **sim** | não | indireto (IA) |
| 1b | **sim** | lê | talvez (marca de quitação) | não |
| 2a, 2b, 2c | **sim** | não | não | **sim** |
| 3b | **sim** | **sim** | não (foto: só a quebra) | **sim** (listas) |
| 4 | lê | não | **sim** (fonte da meta) | **sim** (metas no WA) |

## 5. O que pode quebrar

- **Pagante antigo sem OF perde o uso principal.** Hoje ele registra tudo na Carteira (A2).
  A P1 e a P2 decidem isso. Sem aviso prévio, o efeito é churn e mensagens de recusa em massa.
- **A Carteira de usuários antigos carrega lançamento que não é dinheiro** (pré-Q40). Esconder
  esses lançamentos não corrige o saldo. É o problema da Q37, dispensado só para a coorte nova.
- **Previsão vazia** se a Fase 2 vier antes da 1a.
- **Previsão sem receita** para quase todos, se a receita manual sair antes da P7 (§3.1,
  §9).
- **Fatura OF eternamente "a conferir"** se o #5 for travado antes da 1b.
- **Contagem dupla na transição** entre o fixo manual e a recorrência do OF (1a).
- **Foto diária:** o salto no dia em que o legado sai do total. A regra é quebrar a linha.
- **Limites de plano** que contam caixinha e cartão (`plan_service.py:752`, `:766`) passam a
  contar só o que sobra. Conferir que o cartão OF não consome o teto.
- **A IA promete o que a tool recusa** se `system_prompt.py` não mudar junto.
- **O app Capacitor carrega o `/app` ao vivo** (§5 das armadilhas): a mudança de tela chega
  sem build, e o cache do SW tem de virar.

## 6. Etapa 4 reavaliada: o que sobra

| Peça da Etapa 4 (rascunho de 2026-10-07) | Com a decisão nova |
|---|---|
| PR A: custo mensal pelos fixos manuais (P2) e reserva informada (P-reserva) | **O custo muda de fonte**: recorrências do OF (1a) mais, se o dono quiser, o gasto médio de 90 dias do OF. A **reserva informada é um número digitado** e conflita com a decisão (P6). "Banqueiro sem palpite de reserva" (§1.3.1) **continua**. |
| PR B: regra única das metas (meta = caixinha manual com lotes) | **Reescrito.** A meta deixa de ter saldo próprio e passa a ser `alvo + data + fonte do banco` (P5). As cópias de progresso (`/goals`, IA, insights, Banqueiro) passam a ler a regra nova. As metas antigas viram legado (escondidas, P3). |
| PR C: depositar e retirar | **Sai.** Criar, editar e apagar a meta continuam: é só alvo, data e fonte. |
| PR D: histórico e movimentos internos em Lançamentos | **Sai.** Não há mais movimento manual. O "guardado" vem do OF. |
| PR E: desfazer movimento | **Sai** (já estava fora). |
| PR F: total investido | **Feito no #851**, só do OF, com divisão por tipo e banco (`/api/v2/investido`). É a fonte natural das metas. O investimento manual já não entra. |
| P4 (meta vinculada a posição) | **Sai.** No Nubank, uma posição é um aporte, não a caixinha. |
| P5 (dois ritmos) | Continua, com o ritmo pelo **crescimento da fonte** em 90 dias. Falta histórico: a foto por posição existe desde o #675. |
| Painel sem caixinha | Continua. |

**Qual dado do banco serve de progresso** (P5):
1. **Total investido** (o PR F): simples e estável. Várias metas na mesma fonte não somam;
   cada uma mostra "R$ X de Y".
2. **Investido numa instituição**: separa "reserva no Nubank" de "ações na corretora". Não
   depende de reconhecer caixinha, porque conta toda posição da instituição.
3. **Saldo de uma conta**.
4. Só "caixinhas do banco": **não recomendado**. Herda o buraco de cerca de 2/3 sem subtipo
   (§3.3).

Precisa de schema: a fonte da meta é uma coluna ou tabela nova. A escolha (tabela `metas`
nova ou `pockets` com `fonte`) fica para o PR. Faixa Completo.

## 7. Premissas que este plano assume

O dono não respondeu estas uma a uma. Confirme a que a fase tocar antes de implementá-la.

- Categoria, regra, orçamento por categoria, percentuais do 50/30/20, marcação de
  assinatura, confirmar conciliação e saque em espécie, nome e cor de cartão: **ficam**. São
  organização ou alvo sobre dado do banco, como a meta.
- **Renda do mês informada** (`household_budget_income`): **sai**. A renda vem do OF. Depende
  da P7.
- Saldo inicial e ajuste da Carteira: **ficam**. É contagem do dinheiro vivo.
- Editar categoria e descrição de linha do banco: fica. Data e valor seguem travados.
- O simulador de compra fica (hipótese, não grava).

## 8. Perguntas ao dono

As respostas de 2026-10-07 estão na tabela do fim desta seção. As opções e a recomendação
ficam para registrar o que cada resposta descartou.

**P1. Usuário sem banco conectado (ou cujo banco a Pluggy não atende): o que ele pode fazer?**
- a) Só Carteira (dinheiro vivo). O resto é recusado com "conecte seu banco".
- b) Não usa o produto até conectar. É o gate que o app nativo já tem
  (`open_finance_onboarding_required`), estendido ao `/app` e ao WhatsApp.
- c) Continua como hoje (tudo na Carteira) até conectar.

**Recomendo a.** Muda: a vira a 2b para todos; b bloqueia a conta inteira e é decisão de
cobrança e acesso; c deixa a regra valer só para quem tem banco e mantém a A2.

**P2. Para quem e quando a trava (Fase 2) liga?**
- a) Primeiro a coorte do v2 (a chave atual), depois todos, com data e aviso.
- b) Todos de uma vez.
- c) Só usuários novos (cadastro depois de uma data). Os antigos ficam como estão.

**Recomendo a.** Muda: a reaproveita a chave e mede antes; b expõe todo pagante no mesmo
deploy; c mantém dois produtos por tempo indeterminado.

**P3. "Some da tela": o legado também sai dos TOTAIS? E o saldo da Carteira dos antigos?**
- a) Sai da tela **e** dos totais (Patrimônio, Resumo, Previsão). A Carteira antiga segue
  como está, com o motivo `carteira_nao_confirmada`. Caixinha e investimento manual com saldo
  ficam congelados, sem resgatar.
- b) Sai da tela, mas segue nos totais até o usuário confirmar.
- c) Como a, mas oferece uma vez "confirmar quanto da Carteira é dinheiro vivo" (a tela da
  Q37) aos antigos.

**Recomendo a.** O dinheiro real da caixinha e do investimento manual quase sempre está no
banco e aparece pelo OF. Contar os dois dobra o valor. Muda: a mexe na foto (quebra a linha);
b deixa o número com fonte invisível; c custa uma tela e um fluxo novos.

**P4. Previsão sem fixos e contas manuais: a fonte passa a ser só o que o OF detecta?**
- a) Sim: recorrências mensais do OF (despesa **e** receita, como salário), estimadas e
  ignoráveis, mais as faturas. Contas anuais e boletos ficam fora, com o aviso "o que não se
  repete todo mês não está na previsão".
- b) Como a, mais um "lembrete de conta" com nome e data, sem valor, só para aviso de
  vencimento.
- c) Os fixos e contas manuais continuam, como segunda exceção ao lado da Carteira.

**Recomendo a.** Muda: a define a Fase 1a e tira o Carteiro manual; b mantém um input
(data) e o lembrete; c contradiz a decisão e mantém o problema de dobrar com o banco.

**P5. Metas: de onde vem o progresso?**
- a) O usuário escolhe a fonte entre "tudo investido", "investido no banco X" e "saldo da
  conta Y". Progresso = valor atual da fonte.
- b) Sempre "tudo investido" (uma fonte só).
- c) Só "caixinhas do banco".

**Recomendo a.** Muda: a pede schema da fonte e uma tela de escolha; b é mais simples, mas
toda meta mostra o mesmo número; c herda o buraco do Nubank (cerca de 2/3 não reconhecido) e
o conserto da classificação.

**P6. Reserva de emergência, que hoje é valor informado:**
- a) Vira uma meta como as outras: alvo = N meses de custo (o custo do OF), fonte pela P5.
- b) Continua valor informado, por ser declaração de qual dinheiro é reserva e não dinheiro
  novo.
- c) Sai do produto por enquanto.

**Recomendo a.** Muda: a apaga o PR A da reserva (colunas em `accounts`) e o custo mensal
vira insumo da meta; b mantém um número digitado (exceção explícita); c tira o bloco de
reserva do `/painel`.

**P7 (nova, 2026-10-08). De onde vem a receita da Previsão sem o cadastro manual?**

A P4 supunha que o salário viria como recorrência do OF. A medição do §9 mostrou que quase
nunca vem.
- a) Detectar receita recorrente pelo extrato, com regra própria: mesmo pagador, valor
  parecido, cerca de 30 dias.
- b) Aceitar a Previsão sem receita, com aviso.
- c) Manter a receita manual como segunda exceção, ao lado da Carteira.
- d) Outra, a propor.

Muda: a mexe no motor (Completo) e precisa de medição do extrato antes; b deixa a Previsão
pessimista para quase todos; c contradiz a decisão de 2026-10-07 numa peça só.

### Respostas do dono (2026-10-07)

| # | Decisão |
|---|---|
| P1 | Sem banco conectado (ou banco que a Pluggy não atende): **só a Carteira**; o resto é recusado com "conecte seu banco". |
| P2 | A trava liga **primeiro para quem tem a chave do painel novo**; depois para todos, com data e aviso. |
| P3 | O legado manual **sai da tela e dos totais** (Patrimônio, Resumo, Previsão); caixinha e investimento manual com saldo ficam congelados. |
| P4 | A Previsão passa a ter **só o que o Open Finance detecta** (recorrências + faturas); contas anuais e boletos ficam fora, com aviso. |
| P5 | De onde vem o progresso da meta: **em aberto** — o dono quer pensar melhor. |
| P6 | A reserva **vira uma meta**: alvo = N meses de custo mensal, com o custo vindo das recorrências do Open Finance. |
| P7 | **Em aberto** (pergunta nova de 2026-10-08). |

## 9. Medição em produção

**2026-10-08, 18:28 UTC.** Autorizada pelo dono. Só leitura e só contagens, sem dado de
usuário. Cada consulta rodou numa transação `repeatable read, read only`. **Remeça antes de
reusar estes números:** eles mudam a cada sync e a cada conexão nova.

| Consulta | Resultado |
|---|---|
| A, entrada | 1 recorrência, 1 usuário, 0 ativas em 40 dias, nenhuma no cartão, mediana de 3 ocorrências |
| A, saída | 9 recorrências, 3 usuários, 2 ativas em 40 dias, 8 no cartão, mediana de 3 ocorrências |
| B | 6 conexões vivas de 6 usuários; nenhuma sem busca e nenhuma com busca velha (> 48 h) |
| C | saída: 3 usuários (1 com fixo manual, 1 com receita manual); entrada: 1 usuário (nenhum com manual) |
| D | nenhum par manual × banco do mesmo usuário com dia ±5 |

Leitura: hoje a 1a muda pouco (cerca de uma despesa de conta corrente entra na conta, porque
a recorrência no cartão fica fora). A receita detectada é rara, e daí vem a P7.

Comando (rodar com o dono, pelo `railway`):

```sql
begin transaction isolation level repeatable read read only;

-- A. Recorrências por direção, só de conexões vivas.
with rp as (
  select rp.id, rp.connection_id, c.user_id, rp.average_amount, rp.occurrences
    from of_recurring_payments rp
    join open_finance_connections c on c.id = rp.connection_id
   where upper(coalesce(c.status, '')) not in ('PAUSED', 'DELETED')
), occ as (
  select rp.id, count(t.id) as casadas, max(t.transaction_date) as ultima,
         bool_or(a.type = 'CREDIT') as cartao,
         bool_or(upper(a.currency) <> 'BRL') as outra_moeda
    from rp
    join open_finance_accounts a on a.connection_id = rp.connection_id
    join open_finance_transactions t on t.account_id = a.id
         and t.provider_transaction_id = any(rp.occurrences)
   group by rp.id
)
select case when rp.average_amount > 0 then 'entrada' else 'saida' end as direcao,
       count(*) as itens,
       count(distinct rp.user_id) as usuarios,
       count(*) filter (where occ.id is null) as sem_ocorrencia_casada,
       count(*) filter (where occ.cartao) as no_cartao,
       count(*) filter (where occ.outra_moeda) as outra_moeda,
       count(*) filter (where occ.ultima >= current_date - 40) as ativas_40d,
       percentile_cont(0.5) within group (order by occ.casadas) as mediana_ocorrencias
  from rp left join occ on occ.id = rp.id
 group by 1;

-- B. Frescor da lista por conexão viva.
select count(*) as conexoes_vivas,
       count(*) filter (where recurring_fetched_at is null) as nunca_buscou,
       count(*) filter (where recurring_fetched_at < now() - interval '48 hours') as busca_velha,
       count(distinct user_id) as usuarios
  from open_finance_connections
 where upper(coalesce(status, '')) not in ('PAUSED', 'DELETED');

-- C. Convivência: quem tem recorrência do OF E fixo ou receita manual ativos.
with of_users as (
  select distinct c.user_id, (rp.average_amount > 0) as entrada
    from of_recurring_payments rp
    join open_finance_connections c on c.id = rp.connection_id
   where upper(coalesce(c.status, '')) not in ('PAUSED', 'DELETED')
), man as (
  select u.id as user_id,
         exists (select 1 from recurring_expenses e where e.user_id = u.id and e.is_active) as fixo,
         exists (select 1 from recurring_incomes i where i.user_id = u.id and i.is_active) as receita
    from users u
)
select o.entrada, count(*) as usuarios,
       count(*) filter (where m.fixo) as com_fixo_manual,
       count(*) filter (where m.receita) as com_receita_manual
  from of_users o join man m using (user_id)
 group by 1;

-- D. Calibração do casamento: pares manual × OF do mesmo usuário, direção e dia ±5.
with ofr as (
  select c.user_id, rp.average_amount > 0 as entrada, abs(rp.average_amount) as v,
         (select extract(day from max(t.transaction_date))::int
            from open_finance_accounts a
            join open_finance_transactions t on t.account_id = a.id
                 and t.provider_transaction_id = any(rp.occurrences)
           where a.connection_id = rp.connection_id) as dia
    from of_recurring_payments rp
    join open_finance_connections c on c.id = rp.connection_id
   where upper(coalesce(c.status, '')) not in ('PAUSED', 'DELETED')
), man as (
  select user_id, false as entrada, amount as v, due_day as dia
    from recurring_expenses where is_active and frequency = 'monthly'
  union all
  select user_id, true, amount, pay_day
    from recurring_incomes where is_active and frequency = 'monthly'
)
select o.entrada, count(*) as pares_dia_5,
       count(*) filter (where abs(o.v - m.v) <= 0.05) as valor_exato,
       count(*) filter (where abs(o.v - m.v) <= 0.10 * m.v) as valor_10pct
  from ofr o
  join man m on m.user_id = o.user_id and m.entrada = o.entrada
   and least(abs(o.dia - m.dia), 31 - abs(o.dia - m.dia)) <= 5
 group by 1;

rollback;
```
