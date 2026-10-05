# Plano do dashboard v2

Como o protótipo `webapp/src/dashboard/` (o "dashboard-v2") substitui o dashboard de
produção (`frontend/dashboard.html` + `frontend/dashboard.js`, servido em `/app`).

**Este documento é a base, não a especificação.** Ele guarda as decisões do dono e a
direção geral. Cada PR de etapa faz o próprio plano (com o time, pela faixa da etapa) e
resolve ali o detalhe de implementação, com teste. A seção 7 lista o que já se sabe que
cada etapa vai ter de resolver, sem ditar a solução.

Decisões do dono em 2026-09-25 (Q1–Q43). Sem prazo: o critério é qualidade (Q4). Cada PR
marca na seção 8 o que concluiu.

## 1. Objetivo e como a troca chega aos usuários

- **Meta final: tudo no v2** (Q1). O painel antigo é apagado no fim. No caminho, a tela
  ainda não migrada abre no painel antigo por link, com um aviso discreto (Q1, Q15).
- **Rota paralela `/painel`**; `/app` continua o antigo até o corte (Q2, Q12). Links nos
  dois sentidos, visíveis só para quem está liberado; a escolha não é salva.
- **Chave por usuário** `dashboard_v2_enabled`, no padrão das listas de liberados de
  `core/services/plan_service.py`, chegando ao navegador pelo `/auth/me` (Q11). A chave
  vale no servidor, e nos dois lugares: `/painel` manda para `/app` quem não está
  liberado, e a `/api/v2` recusa quem não está (no padrão de `_require_agents_beta`, em
  `frontend/routes/agents.py`) — senão a API furaria a liberação gradual. O app novo terá a
  sua própria liberação quando chegar.
- **O app atual (Capacitor) nunca mostra o v2** (Q10). Ele carrega o site ao vivo, então
  `/painel` e os links respeitam o marcador `PigBankApp` do user agent (a mesma checagem
  de `_is_pigbank_app`). O user agent só escolhe a tela; nunca concede acesso. O app novo
  (Expo) terá telas próprias e usa a mesma API nova (Q17, Q31).
- **Quando abrir para mais gente:** decisão do dono (Q16). **Tema:** só escuro no primeiro
  corte (Q9).
- **Corte final em dois passos:** `/app` passa a abrir o v2 no navegador (o app atual
  continua no antigo); o painel antigo só é apagado depois que o app atual sair de
  circulação (app novo publicado e versão mínima obrigatória).

### O que a primeira versão tem (Q3, Q6)

Resumo, Lançamentos, Previsão, Metas e caixinhas, Para onde vai, Patrimônio e o chat do
Piggy com IA real e blocos. Pix, conexão do Open Finance, MFA e notificações continuam em
`settings.html`/`precos.html`. O resto abre no antigo até ser migrado, um de cada vez.

O Resumo tem um bloco de **contas**: o saldo de hoje no total e o de cada conta conectada
no Open Finance, mais a carteira Piggy (dono, 2026-09-30: o protótipo não mostrava o saldo
em lugar nenhum).

## 2. Fonte da verdade: Open Finance (Q36–Q43)

- **Q36 — Open Finance é a fonte única** de Pix, contas, cartões, investimentos, aportes,
  resgates e saques. **O único lançamento manual é a carteira Piggy, e ela é só dinheiro
  físico.** No v2 não existe investimento manual, e transação do Open Finance não se cria
  nem se apaga à mão.
- **Q37 — o que já existe de manual fica só para leitura**, como "registro manual antigo",
  com convite para conectar o banco. Na primeira vez no v2, o usuário confirma quanto da
  carteira é dinheiro vivo e revisa o que o sistema lançou sozinho no passado, para o
  antigo não contar duas vezes com o que vem do banco.
- **Q38 — a caixinha manual continua**, com depositar e retirar: é dinheiro que o usuário
  separou. A caixinha do banco vem do Open Finance.
- **Q39 — os defeitos de dinheiro do código atual achados na revisão deste plano são
  consertados** no PR #594 (resgate que pulava juro atrasado, desfazer que criava dinheiro,
  desfazer sem trava). Regra combinada: só se desfaz o último movimento do investimento.
- **Q40 — WhatsApp, pela forma de pagamento**, como na conversa real que o dono mostrou:
  sem saber como foi pago, o Piggy pergunta ("dinheiro vivo ou banco?"); em dinheiro, vira
  lançamento na carteira; Pix, cartão ou débito, não registra nada e oferece buscar a
  transação no extrato. Isso vira regra no código (a forma de pagamento é um estado do
  lançamento e só "dinheiro" grava na carteira), não só instrução para a IA. Marcar conta
  como paga segue a mesma regra.
- **Q41 — saque e depósito em espécie** que o Open Finance trouxer mexem na carteira
  sozinhos, com um aviso que o usuário pode desfazer. Dinheiro mudando de lugar não é gasto
  nem receita.
- **Q42 — recorrente só prevê**, para todo mundo: alimenta a Previsão e o aviso de
  vencimento, e deixa de lançar sozinha na carteira (também no painel antigo), antes da
  etapa 0.
- **Q43 — o manual para de render**: caixinha manual e investimento manual antigo deixam
  de ganhar juro simulado, antes da etapa 0 e também no painel antigo. O ganho já acumulado
  entra no saldo uma última vez. Sem aviso ao usuário: o dono decidiu em 2026-09-26 (#623)
  que ele não será enviado por canal nenhum.

## 3. A API nova: `/api/v2`

Escolha do dono: API nova, pensada para manutenção longa, simplicidade e tecnologia, com
calma (Q5).

- **Quem usa:** o v2 e o app novo. O WhatsApp usa as mesmas regras direto, no mesmo
  servidor (Q17).
- **Organização:** pasta própria `api/v2/` com rotas, esquemas (Pydantic) e domínio, em
  arquivos pequenos por assunto; o monólito só registra o roteador (Q26).
- **Regras num lugar só** (Q18): regra reescrita para a API nova passa a servir também o
  WhatsApp, e a versão antiga sai no mesmo PR. Todo PR de regra compara os números antes e
  depois num conjunto fixo de usuários de teste, pelos testes de conversa (Q30).
- **Segurança por construção** (Q20, Q23): nenhuma rota recebe `user_id` de fora; uma
  dependência única entrega o usuário (cookie ou Bearer) e barra plano inativo; toda
  leitura e toda escrita filtram pelo usuário; o plano é conferido no servidor, por
  recurso e por limite (janela de histórico, tetos), usando a matriz que já existe em
  `plan_service.py`. Um teste varre as rotas e falha se alguma escapar dessas regras. Toda
  rota tem teste de "B não vê nem mexe no que é de A".
- **Contrato** (Q22): Pydantic em toda rota → OpenAPI → tipos TypeScript gerados para o v2
  e o app. **Erro** (Q25): um envelope único com código,
  `{"error": {"code": str, "message": str, "details"?: [...]}}` (decisão do dono,
  2026-09-26). `details` só no 422, com `loc`/`msg`/`type` e nunca o `input`. O 403 do
  CSRF e o 422 de query venenosa nascem nos middlewares do monólito e saem
  `{"detail": ...}`; o cliente cai no status quando não houver `error.code`.
- **Tempo real** (Q19, Q27–Q29): SSE, só servidor → cliente, avisando só *o que* mudou;
  a tela pede o dado de novo. Princípios: o aviso vai só para o dono do dado, só depois de
  gravado, e a tela nunca fica desatualizada em silêncio (reconectar refaz tudo; sessão
  encerrada fecha o stream). Toda escrita de dado financeiro avisa, venha de onde vier.
  Construído com `LISTEN/NOTIFY` do Postgres: um trigger nas tabelas financeiras faz
  `pg_notify` na transação de quem grava (sai só no commit; serve para thread e para
  outro processo) e cada processo web mantém uma conexão `LISTEN` que repassa aos streams
  dele (lista em `db/schema.py::TABELAS_QUE_AVISAM`, laço em `api/v2/eventos.py`). O
  `bot.py` (Discord) sai do `launch.py`: decidido pelo dono, feito no PR 5a da etapa 0.
- **Processo** (Q21): todo PR que cria ou muda endpoint da `/api/v2` é faixa Completo.

## 4. Dados e números

Princípio geral: **nenhum número inventado e nenhum número incerto com cara de exato.** O
que não se sabe aparece como "sem comparação", "a conferir", "desatualizado" ou
"sincronizando", com o motivo.

- **Patrimônio em 12 meses** (Q14): um job diário grava uma foto do patrimônio de cada
  usuário, a partir da etapa 0, para o histórico começar a encher cedo; nada de reconstruir
  o passado. Uma função só calcula o patrimônio (a foto e a tela usam a mesma). A foto
  guarda o que entrou nela, e o gráfico quebra a linha quando isso muda (banco entrou ou
  saiu, conta em outra moeda, correção de base), em vez de mostrar um salto como ganho ou
  perda. **A foto só é exata quando a base do usuário é confiável**: carteira confirmada
  (Q37) e a transferência em espécie (Q41) funcionando com o ciclo de vida inteiro. Antes
  disso — inclusive para quem ainda não abriu o v2 — a foto é gravada, mas marcada como
  incerta; o histórico enche desde a etapa 0 sem afirmar nada que depois não se sustente.
- **Rendimento × CDI** (Q35): por investimento, sem número da carteira somada. A fonte
  prevista era a rentabilidade que o banco informa pelo Open Finance, e **ela não chega
  hoje** (medição de 2026-09-29, abaixo, §7). O que já se grava a cada sincronização é a
  foto diária por posição (`open_finance_investment_snapshots`): saldo, aplicado, data da
  posição, taxa de contrato e as três taxas do banco em colunas próprias, vazias até algum
  banco mandar. **Decisão do dono (2026-10-01):** o bloco de rendimento mostra SÓ o
  contratado ("100% do CDI contratado"), sem afirmar que rendeu; a fonte é `rate`/`rateType`
  de `open_finance_investment_snapshots`, e entra na etapa de tela do bloco de rendimento. Se
  algum banco passar a mandar `lastMonthRate` e as outras taxas, as colunas já as guardam.
  Só compara com o CDI quando se sabe o período exato e que a posição existiu nele o tempo
  todo.
- **Reserva em meses**: a caixinha de reserva é designada pelo usuário (hoje só existe o
  palpite pelo nome, `_is_reserva`); a conta divide pelo custo mensal das contas fixas.
- **Só reais**: o que estiver em outra moeda fica fora das somas, com aviso. Câmbio fica
  para quando alguém pedir.
- **Dado do Open Finance desatualizado nunca aparece como exato**, em bloco nenhum.
- **Perfil do Resumo** no servidor; o layout segue no navegador.
- **Privacidade:** toda tabela nova com dado do usuário entra, no mesmo PR, na exportação,
  no "Recomeçar do zero" e na exclusão de conta (`db/privacy.py`).

## 5. O front

- **Busca de dados:** TanStack Query (Q24).
- **Bundle** commitado em `frontend/` com trava de rebuild no CI (Q7).
- **Plano real** pelo `GET /api/v2/me` tipado (decisão do dono, 2026-09-26). O bundle
  nunca lê a URL; só o protótipo (`dashboard-v2/index.html`, sem backend) define
  `window.PIGBANK_DEMO_PLAN` pelo `?plano=` dele. O `/auth/me` só leva
  `dashboard_v2_enabled`, para o link do `/app`.
- **Tipos** gerados do OpenAPI da `/api/v2` por um gerador próprio
  (`scripts/gerar_tipos_api_v2.py` → `webapp/src/dashboard/lib/api-v2.gen.ts`), e não pelo
  `openapi-typescript`: ele exige typescript@^5 e o webapp usa o TS 7 nativo, sem a API
  JS da qual ele depende (o `npm install` recusa com ERESOLVE).
- **Erro no cliente** é uma tela só ("Não deu para carregar o painel", texto fixo em
  português — nunca a `message` do envelope, que em 402/404 sai em inglês —, "Recarregar"
  e "Painel antigo"). Sem redirecionamento no cliente (decisão do dono): Recarregar passa
  de novo pelo portão do servidor (`serve_painel`), que manda cada caso ao lugar certo.
  O 401 comum o `auth-refresh.js` renova e repete.
- **Um PR por tela** (Q8); tela que só consome a API é faixa Leve, com o time na versão
  leve.
- **Testes** (Q32): pytest com Postgres real para isolamento e contrato; Playwright com
  respostas geradas do contrato; ponta a ponta só nos fluxos críticos.

## 6. Ordem

**Antes de tudo:** terminar o protótipo do chat (PR 3, blocos que expandem na conversa)
(Q34), e ter no ar, **antes do job da foto da etapa 0**, tudo da seção 2 que mexe no
dinheiro do produto atual — senão as primeiras fotos gravam ganho ou perda que não houve,
e esse histórico não se refaz:
- o PR #594;
- a Q42 (recorrente só prevê) e a Q43 (manual para de render);
- a regra da forma de pagamento da Q40 em todos os caminhos que gravam hoje (marcar conta
  como paga, WhatsApp, IA, painel antigo) — só a interface nova do chat espera a etapa 7;
- a transferência entre banco e carteira da Q41 (saque e depósito em espécie), **com o
  ciclo de vida inteiro** (o banco corrigir ou apagar, reconectar, o usuário desfazer), ou,
  até ela existir, a foto marcada como incerta (seção 4).

| Etapa | O que entra | Faixa |
|---|---|---|
| 0 | Esqueleto da `/api/v2` (usuário, erro, contrato, SSE), `/painel` com a chave, plano pelo `GET /api/v2/me`, TanStack Query, job da foto diária e histórico da rentabilidade do Open Finance | Completo |
| 1 | Resumo (perfil no servidor), com o bloco de contas: saldo de hoje por conta | API Completo, tela Leve |
| 2 | Lançamentos: ver tudo; lançar, editar e apagar na carteira (Q36) | idem |
| 3 | Previsão | idem |
| 4 | Metas e caixinhas | idem |
| 5 | Para onde vai | idem |
| 6 | Patrimônio | idem |
| 7 | Chat com IA real: o `/ai/chat` devolve texto + blocos | Completo |

**Depois:** as demais telas, uma a uma; tema claro; abrir para mais gente; corte final
(seção 1).

## 7. O que cada etapa vai ter de resolver

Pontos levantados na revisão deste plano (Codex, PR #586). São perguntas para o plano do
PR de cada etapa, não soluções prontas. Cada PR confere se ainda valem, decide e testa.

**Etapa 0 (API, tempo real, foto diária)**
- Tempo real: aviso depois do commit, só para o dono, reconexão (inclusive com a sessão
  vencida), sessão revogada fechando o stream, e o `LISTEN` caindo com mais de um
  processo.
- O 500 da v2 escapa do app depois da resposta (o starlette re-levanta): decidir o
  conserto junto com o desenho do stream.
- Todas as escritas financeiras avisarem, inclusive as do código antigo (a camada `db/`
  como lugar do aviso).
- Foto diária: leitura consistente, uma por dia, concorrência entre instâncias, "Recomeçar
  do zero" no meio, sincronização do Open Finance pela metade, conexão pausada ou
  desatualizada, movimento de banco pendente.
- Patrimônio sem contar duas vezes: caixinha espelhada, lançamento fundido com o banco,
  carteira antiga com saldo de banco misturado, investimento e caixinha manuais que o banco
  também traz.
- Moeda: o import grava tudo como `BRL` hoje (inclusive cartão); moeda omitida pelo
  conector; moeda corrigida depois.
- Ingestão do Open Finance (defeitos achados na revisão dos PRs #689 e #720; detalhes e
  casos nas threads do #720). Pergunta geral: como a ingestão marca uma leitura como
  incompleta em vez de gravar dado incompleto com cara de completo, e como o conserto
  alcança o que já foi gravado errado?
  - Valor padrão no lugar do dado ausente ou inválido (`normalize_pluggy_account`,
    `normalize_pluggy_transaction`, `normalize_pluggy_investment`, `_to_decimal`): tipo,
    saldo, valor, data, nome, `NaN`/`Infinity`, texto ilegível. Como distinguir
    "desconhecido" de zero e de `BRL`?
  - Registro sem `id` ou com `id` malformado (branco, objeto: `str(raw.get("id") or "")`
    aceita os dois), ou com `type`/`subtype` que o código não trata, some, colide ou é
    rotulado errado com o sync dando sucesso (conta, transação e investimento;
    `pluggy_rv_kind()` trata todo `EQUITY` que não é FII como ação). Que forma de `id` e
    que pares `(type, subtype)` são aceitos?
  - Leitura truncada ou malformada: `max_pages=60` sem conferir o cursor, `/accounts` só na
    primeira página, `results` ausente, cursor repetido. Quando uma leitura conta como
    completa?
  - Exceção engolida em `_sync_pluggy_item_confirmado()` (investimentos, espelho de
    caixinhas, foto diária) sai como `ok=True`. Que falhas marcam o sync incompleto?
  - Ausências: conta ou transação que some da resposta fica para sempre. Se a conciliação
    for criada, como ela sabe que a ausência é real (intervalo de datas, data corrigida,
    sync concorrente depois da trava — o furo da trava já existe na conciliação de
    investimentos) e como desfaz o que foi derivado (`_rollback_imported_of()` engole erro)?
  - Transação PENDING que depois é lançada com outro id vira dois registros, em conta e em
    cartão (`normalize_pluggy_transaction()` descarta o `status`). Como casar a pendente com
    a lançada?
  - Cartão: moeda por transação, fatura calculada localmente sem o
    `/bills` da Pluggy, pagamento de fatura por palavra-chave, sinal do estorno, calendário
    padrão 1/10, grupo de parcelas (chave que divide e que colide, metadado incompleto,
    parcelas futuras não criadas e a troca da projetada pela real).
  - Cartão em duplicidade: adoção de cartão manual, OFX e Open Finance no mesmo cartão,
    reconexão com item novo, nome padrão `"CREDIT"`.
  - Banco religado guarda o `last_sync_at` antigo; a fonte do estado da conexão é
    `connection_ui_state()`.
- Quando o dado do Open Finance conta como desatualizado (limite por produto) e como a
  tela aberta percebe isso sem escrita.
- Rentabilidade do Open Finance: medida em produção em 2026-09-29 (leitura, pelo dono;
  remeça antes de reusar). `lastMonthRate`, `lastTwelveMonthsRate`, `annualRate`,
  `fixedAnnualRate` e `amountProfit` vieram nulos em todas as posições; no CDB, `rate` +
  `rateType` = `CDI` é a taxa de CONTRATO (100 = 100% do CDI), não rentabilidade; `date` é a
  data da posição informada pelo banco, dias atrás da coleta e diferente entre posições da
  mesma conexão; posições resgatadas (`TOTAL_WITHDRAWAL`) continuam no espelho; e há
  conexão `PARTIAL_SUCCESS` com investimentos não confirmados. Resultado: não há hoje taxa
  do banco para comparar. **Decidido pelo dono (2026-10-01, §4):** o bloco de rendimento
  mostra só o contratado ("100% do CDI contratado"), de `rate`/`rateType` das fotos por
  posição, sem afirmar que rendeu; entra na etapa de tela do bloco de rendimento. A regra
  continua — a comparação de rendimento usa a taxa que o banco calcula, nunca a diferença
  entre fotos do rendimento acumulado.

**Etapas de tela (1 a 6)**
- Etapa 2: identidade das transações importadas por conta (conta e cartão); editar a data
  de um lançamento fundido.
- Etapa 3: desde a Q42 o gasto fixo diário, semanal e único entra na Previsão, uma
  ocorrência por data — um diário gera até 90 itens em `compromissos`/`causas`. A tela
  `/previsao` tem de agrupar por nome; o código de hoje não agrega nem limita.
- Etapa 3: defeitos da previsão de hoje (`cashflow._cashflow_events()`), achados na
  revisão dos PRs #689 e #720 (detalhes e casos nas threads do #720). Não se consertam no
  painel antigo: a regra reescrita para a Previsão da `/api/v2` passa a servir também o
  simulador e o `check_cashflow` da IA (Q18). A matriz do
  `docs/plano-piggy-assistente-contextual.md` dá a direção de erro de cada entrada; esta
  lista e a matriz se completam. Perguntas para o plano da Etapa 3:
  - Gasto fixo pago no cartão sai do caixa no `due_day` e de novo na fatura. Como ele entra
    pelo calendário da fatura?
  - Valor estimado (`variable_amount`), valor 0 de gasto variável sem estimativa e boleto
    com valor negativo entram como exatos. Como a previsão mostra o que é estimado?
  - Conta ou fatura paga fora do PigBank segue pendente, e a paga pelo PigBank sai antes
    de o saldo cair. Como a previsão trata o intervalo até o banco confirmar?
  - Gasto fixo manual só entra pelo boleto já gerado (`sync_manual_bills_once()` gera só o
    próximo ciclo), e boleto e recorrência saem de sincronia (desativar, trocar de modo,
    mudar calendário ou valor). Quem é a fonte das ocorrências futuras?
  - Ocorrência que vence hoje, atrasada ou adiantada: não há marcador de realização. Qual
    janela de conferência (decisão aberta 7 do plano da Piggy)?
  - Receita: irregular cadastrada como fixa, legada `once`/`weekly`/`daily` fora da
    previsão, recorrência sem data de fim. Que política de confiança e de fim?
  - Gasto variável do dia a dia fica fora. Que estimativa (o protótipo usa o ritmo de 60
    dias e a faixa provável) sem contar duas vezes o agendado, separando cartão de caixa,
    e com que amostra mínima?
  - Saldo de partida: pendências (`reconciliation`, `bank_movements`, `pending_actions` e
    `ai_pending_actions` que mudam dinheiro), carteira Piggy sem data, conta escolhida pelo
    `BANK_ACCOUNTS_SQL` antes de filtrar pausadas, o que o `balance` inclui. Quando o
    resultado sai como "a conferir"?
  - Fatura: total negativo ignorado, `credit_bills.total` como contador, `status` gravado
    que não acompanha correção, cartão manual sem as compras não lançadas, `list_bills`
    com teto de 1.000. De onde sai o valor e o estado de cada fatura?
  - Datas sem dia útil mudam o pior dia.
- Etapa 4: reserva designada, custo mensal por frequência, reserva só em reais; caixinha
  manual versus a do banco.
- Etapa 6: variação do período só dentro de um trecho sem quebra.
- A tela da confirmação da carteira e da revisão dos lançamentos antigos (Q37): em que
  etapa entra e o que derruba a confirmação. O estado "não confirmado" existe desde a
  etapa 0 (seção 4).

**Convivência com o painel antigo (desde a etapa 0)**
- Quem usa o v2 ainda alcança o painel antigo (links nos dois sentidos, e o app atual fica
  nele), onde dá para criar lançamento de banco e investimento manual — o que a Q36 tira do
  v2. Decidir se esses caminhos antigos são bloqueados ou adaptados durante a convivência,
  com teste cruzando as duas telas.
  **Decidido pelo dono: bloquear para quem tem a chave, em todos os canais** — PR D
  da etapa 1 (§8). Esconder os botões no `/app` fica para depois, se o dono pedir.

**Etapa 7 (chat)**
- A interface nova do chat sobre a regra da forma de pagamento (a regra em si vem antes,
  seção 6).

## 8. Andamento

- [x] Protótipo: perfis do Resumo (#573, #575), faixa do Piggy (#579), navegação com o
  Piggy no meio e Ferramentas (#582), página do chat (#584).
- [x] Protótipo: blocos que expandem na conversa, com estado por resposta e "Abrir no painel" (PR 3 do chat).
- [x] Pré-requisitos: ~~#594~~ ✓ · Q42 (#620) · Q43 (#623 e #634) · Q40 (#633) · Q41
  (#627 e o PR B, #706). Os quatro mergeados, deployados e conferidos no ar pelo dono em
  2026-10-01, com `OF_CASH_ENABLED` ligada.
- Etapa 0 em andamento, em 6 PRs (divisão aprovada pelo dono em 2026-09-26): 1 esqueleto
  (#632) · 2a `/painel` (#659) · 2b contrato TS + TanStack (#669) · 3 foto diária por
  posição do Open Finance (#675) · 4 SSE básico com os 2 avisos de hoje + conserto do
  re-raise (#678) · 5 toda escrita financeira avisa + o Discord sai do `launch.py` · 6 job da
  foto diária desligado por chave. O 5 foi dividido depois pelo dono: 5a o Discord sai do
  `launch.py` · 5b toda escrita financeira avisa, por trigger do Postgres + `LISTEN`.
  - PR 1 (#632, mergeado): esqueleto da `/api/v2` (`usuario_atual`, envelope de erro,
    `GET /api/v2/me`, varredura de rotas).
  - PR 2a: a página `/painel` (gate de sessão, chave, UA do app e os gates do `/app`),
    `dashboard_v2_enabled` no `/auth/me`, links "Painel novo (beta)" no `/app` e "Painel
    antigo" no v2, e o bundle servido de `frontend/dashboard-app.*` com gate no CI. O v2
    ainda não chama a API e o `?plano=` continua.
  - PR 2b (feito): contrato OpenAPI → TS, TanStack Query, fim do `?plano=`, erros,
    Safari 14. As telas seguem com dados sintéticos e a etiqueta de demonstração.
  - PR 3: foto diária por posição do Open Finance (`open_finance_investment_snapshots`,
    gravada no sync; coleta não confirmada entra marcada e a confirmada do mesmo dia vence;
    desconectar apaga; entra na exportação junto com as posições). Só a gravação: nada lê
    ainda, e a fonte do Rendimento × CDI ficou para o dono (§4, §7). Conferência
    pós-deploy: `scripts/conferir_fotos_of.py`.
  - PR 4 (#678, mergeado): `GET /api/v2/eventos` (SSE), com os 2 avisos que o `/ws` já dá (fim do sync do
    Open Finance e "Recomeçar do zero"); sessão rechecada antes de cada envio e a cada
    30 s, teto de 5 streams por usuário; o `/painel` invalida as consultas a cada aviso.
    E o sub-app para de re-levantar a exceção que já respondeu. O `LISTEN/NOTIFY` veio
    no PR 5b.
  - PR 5a (#688, mergeado): o `launch.py` vira o uvicorn por `os.execv` e o `bot.py` do
    Discord não sobe mais.
  - PR 5b (#691, mergeado): trigger `pg_notify('pb_escrita', dono)` nas tabelas de
    `db/schema.py::TABELAS_QUE_AVISAM` (núcleo financeiro, categorias e regras,
    orçamentos, Open Finance; `auth_accounts` só quando `plan`/`plan_expires_at` mudam;
    `pix_*` fora) e `escutar_banco()` com `LISTEN` no lifespan (`api/v2/eventos.py`),
    que repassa "tudo" ao SSE. `LISTEN` caído reloga a cada 10 min; o backoff só zera
    depois de um `select 1` de pé.
  - PR 6 (#723, mergeado): foto diária do patrimônio (`patrimonio_fotos`, uma por usuário por dia do app,
    a partir das 18h), pela conta única `db/patrimonio.calcular` que a tela da etapa 6
    vai reusar; job `core/services/patrimonio_foto.py` atrás de
    `PATRIMONIO_FOTO_ENABLED` (desligado). Carteira com a fusão devolvida, contas BANK
    e posições do banco em reais, uma por identidade do provedor (a da conexão mais nova;
    moeda, pausa e resgate decididos depois desse recorte; outra moeda, resgatada e
    posição de conexão pausada ficam fora e contadas em `base.fora`, a conta em outra
    moeda também em `base.fora.moeda`), caixinhas manuais e investimentos
    manuais; cartão fora. Toda foto sai com `motivos` (`carteira_nao_confirmada` até a
    Q37, e mais os de banco desatualizado, espécie, pendências, moeda presumida, saldo
    ausente e `caixinha_espelhada_fora` — a caixinha do banco cuja posição ficou fora
    não entra no total nem como caixinha, e é contada em `base.fora`; e
    `conta_fora_do_ultimo_sync` — conta ou posição do banco com `updated_at` abaixo do
    máximo da mesma conexão na mesma tabela não veio no último save: cada save do sync
    carimba a chamada com um `now` só; o saldo velho fica na soma e o motivo marca a
    dúvida). Entra na exportação, no reset e na exclusão. Fora: reconstrução do
    passado, câmbio, poda, a confirmação da Q37, leitura por rota ou tela e script de
    conferência pós-deploy.
    Espelho do Open Finance lido pela foto (`db/patrimonio.calcular`; testes em
    `tests/test_patrimonio_foto.py`, "rec." = `tests/test_of_investimento_reconciliacao.py`):

    | estado da linha | conta | posição | teste |
    |---|---|---|---|
    | fresca | soma | soma | `composicao_exata` |
    | 2 conexões, mesma moeda | a mais nova, 1× | idem | `conta_em_outra_moeda`, `posicao_em_duas_conexoes` |
    | 2 conexões, moeda diferente | vale a mais nova (USD → fora e contada; a BRL velha não soma) | idem | idem |
    | outra moeda | fora, `fora.moeda` | idem | `conta_em_outra_moeda`, `composicao_exata` |
    | conexão mais nova pausada/apagada | fora, **não contada** | fora, `fora.pausada` | idem |
    | resgatada | — | fora, `fora.resgatada` | `composicao_exata` |
    | saldo ausente/malformado/±Inf/NaN (raw ou coluna) | soma o finito da coluna + `saldo_ausente` | idem | `saldo_ausente_*`, `saldo_nao_finito_na_coluna_soma_zero` |
    | omitida do último sync | soma + `conta_fora_do_ultimo_sync` (`updated_at` < máximo da conexão na tabela) | idem; leitura completa poda (sai sem motivo) | `_sync`: `syncs_em_sequencia`, `leitura_completa_poda_a_posicao`, `conexao_mais_nova_nao_envelhece_a_outra`; rec. `posicao_que_some_leva_a_caixinha` |
    | sync pela metade (tentativa > sync) | `banco_desatualizado` | idem | `banco_desatualizado` |
    | sync nunca feito | `banco_desatualizado` | idem | `banco_desatualizado` |
    | outro usuário com os mesmos ids | não entra | idem | `outro_usuario_com_os_mesmos_ids_nao_muda_a_foto`, `_sync`: `nao_cruza_usuario` |

    Limites em aberto: (1) `conta_fora_do_ultimo_sync` é cego quando o último sync
    omitiu TODAS as contas (ou todas as posições) da conexão — o máximo segue sendo o da
    geração anterior; e um caminho que carimbe `updated_at` de só uma linha faz as
    outras da conexão parecerem velhas (falso positivo: só o motivo, o total não muda);
    hoje só os três saves de `db/open_finance.py` (sync, investimentos e o mock) gravam
    `updated_at`, cada um com um `now` por chamada; (2) a conta da
    BRL velha (2 conexões, moeda diferente) ainda entra na fusão (`merged_wallet_delta`)
    e na conciliação, que seguem o `BANK_ACCOUNTS_SQL`; (3) conexão velha ainda viva
    da mesma conta liga `banco_desatualizado` quando envelhece (sem teste); (4) conta de
    conexão pausada não é contada em `base.fora`, a posição é.
- Etapa 1 (Resumo) em 4 PRs (divisão aprovada pelo dono em 2026-10-01), na ordem
  A → (B ∥ D) → C: A perfil e bloco de contas (API) · B uma função só para o mês + rota
  `resumo-do-mes` + o WhatsApp passa a usá-la · D bloqueio da Q36 no painel antigo pela chave
  `dashboard_v2_enabled` · C a tela. Rendimento × CDI: decidido (§4), entra na etapa de tela
  do bloco de rendimento.
  - PR A: `GET`/`PUT /api/v2/perfil` (perfil do Resumo no servidor, `"padrao"` no CHECK de
    `dashboard_profile`, NULL = nunca escolheu; o PUT é a primeira escrita da v2, com CSRF) e
    `GET /api/v2/contas` (`db/contas_hoje.py`): Carteira e contas BANK pelo recorte e pelos
    critérios da foto (`desatualizada`, `sem_saldo`, `fora_do_sync` viraram funções de
    módulo em `db/patrimonio.py`), cada conta com `no_total` e `motivos`, `fora_do_total`
    e o total igual a `carteira + bancos` da foto. Desatualizado = 48 h, como a foto, sem
    timer na tela. Dinheiro sai como texto decimal (contrato da v2, `docs/CLAUDE.md`). A
    tela decide o "expandir" das contas fora do total no PR C. Divergência declarada com o
    saldo consolidado: USD novo × BRL velho e saldo não finito na coluna
    (`tests/test_api_v2_contas.py`).
  - PR B: a regra única do mês (`db/resumo_mes.TOTAIS_SQL`, a da consulta 5 do /app:
    lançamentos não internos por `criado_em` + cartão pela fatura, com `user_id` em cada
    perna, inclusive a fatura) e `GET /api/v2/resumo-do-mes?mes=AAAA-MM` (Entrou, Saiu, o
    mês anterior inteiro ou `null`, `ate` = último dia do mês, o corrente também, `motivos`: os do bloco de contas que valem para
    o mês e `inicio_do_historico` quando a janela do plano corta o mês). Decisões do dono:
    cartão pela fatura (N1); migram só as telas de mês-calendário — a rota, o "Gastos em
    <mês>" do WhatsApp, o relatório mensal, a consulta 5 do /app e `compute_kpis`
    das Análises (N2; `compute_evolution` segue com a consulta própria e um teste a compara com a regra, mês a mês); só Entrou e Saiu (N3); anterior
    inteiro, sem porcentagem (N4). Ficam na regra antiga, sem cartão (divergência
    conhecida): relatório diário e semanal, ferramentas da IA de período livre, projeção
    de fechamento e o Repórter. O mês corta `criado_em` pela data ingênua, igual a antes,
    de propósito. `scripts/comparar_resumo_mes.py` mostra antigo × novo por usuário e mês,
    só lendo.
  - PR D: bloqueio da Q36 fora do v2 para quem tem a chave `dashboard_v2_enabled`, em todos
    os canais (`/app`, WhatsApp, IA): criar e aportar em investimento manual, importar
    extrato (OFX/CSV/PDF) e fatura OFX, compra manual no cartão. Resgatar e apagar
    investimento manual (e desfazer o apagar, que restaura o que já existia — decisão do
    dono), caixinha e Carteira seguem livres. Trava, textos e tabela em
    `core/services/fonte_unica.py` e `docs/CLAUDE.md` ("Q36 fora do v2"); a chave que falha
    libera. Os botões do `/app` continuam à vista: o servidor recusa e a tela mostra o texto.
  - PR C: a tela do Resumo no `/painel` com dado real. O perfil vem do servidor
    (`GET`/`PUT /api/v2/perfil`): o modal só abre com `null`, a escolha é otimista e desfaz
    com aviso no erro, e o seletor fica `aria-disabled` enquanto o PUT está em voo (sem
    dois PUT concorrentes). O bloco `contas` entra no topo do painel padrão e dos 5
    presets: total com a nota "carteira a confirmar" e a contagem das contas fora do total
    (a lista que a tela abre, e não o campo declarado), lista curta com a Carteira Piggy e
    as contas que entram no total, as demais atrás de "ver contas fora do total", saldo
    ausente ou ilegível = "—", nunca R$ 0,00, e moeda fora de três letras maiúsculas sai
    só o número. Entrou e Saiu vêm de `resumo-do-mes`, com o mês anterior inteiro como
    referência e os `motivos` como selos. O mês da página é o corrente de São Paulo, com
    seletor dos últimos 6 meses reais (`s.mes`, separado de `s.month`, que os blocos de
    exemplo ainda leem e fica em setembro). Continuam sintéticos, cada um com o selo
    "demonstração" no próprio bloco: Fatura, Guardado, Rendimento e os demais blocos;
    no modo real o selo também marca o extrato, a faixa do Piggy e o chat (o do título do
    Resumo saiu no PR C2). A etiqueta única da página saiu. O protótipo
    (`dashboard-v2/index.html`) segue como antes. Limites que seguem: o Cmd-K mostra
    transações de exemplo sem selo; "Recomeçar do zero" com o `/painel` aberto reabre o
    modal. Os outros limites que este PR deixou foram decididos ou corrigidos no PR C2.
  - PR C2: as decisões do dono sobre a tela do PR C e três acabamentos. D1: o título
    "Resumo de <mês>" não leva mais o selo "demonstração"; só os blocos inventados levam
    (extrato, faixa do Piggy e chat não mudaram). D2: o seletor de mês fica como está,
    também em `/lancamentos`, `/gastos` e `/previsao`. D3: Entrou e Saiu, e o mês anterior
    de referência, saem com centavos, iguais ao texto decimal do servidor e ao bloco de
    contas; Fatura e Guardado, sintéticos, seguem sem casas. O sinal negativo é o hífen
    nos dois (o valor e o mês anterior); o resto do app segue com o menos tipográfico do
    `money` (`lib/format.js`). D4: a conta paga sem senha
    (nem Google/Apple) ganha o botão "Criar senha", que leva à `/home`, onde o overlay
    "Crie sua senha" sobe sozinho (o `/painel` não carrega o `criar-senha.js`). Toda a
    `/api/v2` dá a ela 403 `password_required`, inclusive o `/me`, então é no portão
    (`Entrada.tsx`) que ela cai: lá o texto vira "Para abrir o painel novo, crie a sua
    senha. Depois de criar, volte para o painel novo.", com o botão, Recarregar e "Painel antigo"; os outros erros do portão seguem
    com a tela de antes, sem o botão. O mesmo 403 no PUT do perfil, se chegar, mostra "Crie sua senha para salvar o seu painel.
    Depois de criar, volte para o painel novo." com o botão, no aviso do painel e no do modal da 1ª visita (o aviso do painel fica atrás
    do modal); 500 e rede seguem com o aviso genérico. O destino e o texto do botão
    moram numa constante só (`CRIAR_SENHA`, em `Entrada.tsx`). Limite: o "Criar senha" leva
    à `/home` e não volta sozinho ao `/painel` (o overlay recarrega a `/home`, que não tem
    link para o painel novo; retorno automático é outro fluxo), por isso os dois textos
    mandam voltar. Acabamentos: "Para onde
    vai" não vaza mais da célula — no Resumo a lista rola por dentro, nenhuma categoria
    some (decisão do dono), e é alcançável por Tab com o rótulo "Categorias do mês"; sem
    `@container` por altura e sem pista de "tem mais" além da barra de rolagem fina; na
    `/gastos` e no celular nada rola nem sai do lugar (no celular a lista segue como
    parada de Tab, sem rolar: o bloco é o mesmo do Resumo); a legenda diz o total gasto no mês, sem
    contar categorias. No preset Dívidas, Parcelas futuras sobe de 4º para 2º; em troca a
    Fatura desce de 2º para 3º e os Compromissos de 3º para 4º: decisão do dono, porque
    Parcelas pesam mais para quem está em dívida e o `contas` grande no topo só deixa a
    casa (0,3) para um bloco de uma coluna; o seletor de perfil tem 44 px (só
    ele, o `.field` global segue igual) e a seta de abrir cada bloco tem alvo de 44 px sem
    mudar o desenho. A busca da barra no celular segue menor que 44 px (a barra encolhe o
    botão e não tem folga para um alvo maior sem invadir os vizinhos).
- Etapa 2 (Lançamentos) em 5 PRs: 1 leitura · 2a escrita da carteira (inclui gravar a marca
  `launches.origem`; faixa Completo) · 2b regras novas de dinheiro (travar data e valor da
  linha fundida em todo canal; apagar a fundida desfaz a junção na hora; editar valor só na
  carteira pura) · 3 identidade por conta e cartão das importadas (começa por medição
  só-leitura em produção, com autorização do dono) · 4 a tela (faixa Leve). A Q37 vira
  etapa própria depois desta, antes da Previsão.
  Decisões do dono (2026-10-03): **P1** Q37 fora da Etapa 2, etapa própria antes da
  Previsão; **P2** lançamento anterior ao deploy do PR 2a é só leitura no v2 (marca na
  coluna nova `launches.origem`; NULL = antigo, sem backfill); **P3** linha fundida com o
  banco: o banco é o dono de data e valor; **P5** transação do banco e cartão do Open
  Finance: só categoria e descrição; **P6** cartão entra pelo mês da fatura e mostra a data
  da compra; **P7** busca varre a janela inteira do plano; **P8** interno (saque em
  dinheiro, depósito em caixinha, pagamento de fatura) entra marcado, fora de todo total.
  (P4 não foi passado ao PR 1.)
  - PR 1: `GET /api/v2/lancamentos` (`db/lancamentos.py`) e `GET /api/v2/categorias`, só
    leitura (contrato em `docs/CLAUDE.md`, "API v2"). O mês sai das pernas de `TOTAIS_SQL`
    extraídas para `MES_LANCAMENTOS_SQL`/`MES_CARTAO_SQL` (o Resumo não mudou um número): a
    soma dos itens não internos é o Entrou/Saiu, provada com a matriz do PR B e com
    controle negativo (perna do cartão pela data da compra). Keyset com cursor opaco,
    `conta` pela identidade do provedor, `pode`/`origem`/`motivos` por linha numa regra só
    (`PODE_SQL`), coluna `launches.origem` criada vazia (ninguém grava ainda: toda carteira
    sai `registro_antigo`, `pode: []`). A busca do `list_history` virou
    `termos_busca`/`clausula_busca`, usadas pelas duas listas. Medido em 2026-10-03,
    `EXPLAIN (ANALYZE, BUFFERS)` num `pytest_*` local (4 usuários × 6000 lançamentos + 1440
    compras; remeça antes de reusar): mês 5,5 ms, busca na janela inteira 250 ms (o estado de
    toda linha é calculado antes do filtro de texto). Em aberto para o dono: o depósito em
    caixinha e o aporte gravados pelo PigBank têm `tipo` `deposito_caixinha`/
    `aporte_investimento` e ficam FORA da lista neste PR, como na soma — a P8 os cita, mas
    mostrá-los pede decidir se são entrada ou saída; o que entra marcado hoje é o interno
    com tipo despesa/receita (saque em dinheiro, pagamento de fatura, transferência do
    banco). A lista do `/app` e o "últimos N" do WhatsApp seguem as regras deles.
  - PR 2a: a escrita da carteira (contrato em `docs/CLAUDE.md`, "API v2"):
    `POST /api/v2/lancamentos/carteira`, `/editar` e `/apagar`, e a marca `launches.origem =
    'carteira'` gravada pelo escritor da carteira em todo canal e pelo saque/depósito
    automático da Q41 (decisão do dono, 2026-10-03). O `pode` vira a guarda da escrita, relido
    sob o lock do usuário e da linha (`db/lancamentos.pode_da_linha`); o miolo do `POST
    /launches` do `/app` saiu para `core/services/carteira.lancar`, usado pelas duas rotas.
    Decisões do dono (2026-10-03): pagamento de conta pela Carteira não apaga pelo v2 (o `pode`
    fica `[categoria, data]`; o de fatura do cartão manual cai no mesmo ramo e sai junto, por
    conservadorismo); saldo inicial e ajuste ficam marcados e editáveis/apagáveis, como no
    `/app`. Antecipar parcela e estorno de fatura do cartão manual gravam sem a marca (só
    leitura: o `efeitos` não guarda o que desfazer). Ficou fora: editar valor, travar e
    apagar a fundida (PR 2b), WhatsApp e quick_entry pelo serviço novo, backfill, chave de
    idempotência, motivo no 409, a tela (PR 4) e o conserto do apagar pagamento de conta no
    `/app`/WhatsApp (devolve o dinheiro e a conta segue paga). O pagamento de conta pela
    carteira (`db/bills.mark_bill_paid`) nasce sem a marca e a ganha no mesmo statement que o
    liga à conta: entre os dois commits a linha parecia carteira pura, o v2 a apagava e o
    passo seguinte quebrava a FK, deixando a conta paga sem lançamento. A mesma janela por um
    apagar do `/app`/WhatsApp já existia e fica para o conserto do apagar pagamento de conta.
    Riscos aceitos: dois POST iguais gravam dois lançamentos (comentário `ponytail:` na rota);
    o apagar antigo do `/app` (trava `launches` e depois `accounts`) e o do v2 (`accounts` e
    depois `launches`) na mesma linha do mesmo usuário ao mesmo tempo dão deadlock, e o
    Postgres aborta um dos dois sem perda. O que mudou no `/app`: o PATCH de cartão passou a
    travar a linha (`update_credit_transaction_fields` usa `for update` sempre, a mesma linha
    que o UPDATE já travaria); no `POST /launches`, `infer_category` entrou no `try` (um
    `ValueError` dele vira 400 em vez de 500) e a falha do aprendizado depois do commit passou
    de 500 (cuja retentativa duplicava o gasto) a só log. Diferença declarada: `categoria: ""`
    na criação pelo v2 dá 422 (`null` = inferir), e o `/app` infere.
  - PR 2b-1: `valor` no `/editar` (só a carteira pura, P4: troca `valor`, `efeitos.delta_conta`
    e o saldo da Carteira pela diferença, na mesma transação) e a data da linha fundida travada
    em todo canal (P3): a guarda de `db.accounts.update_launch_fields` lê
    `db/lancamentos.FUNDIDO_SQL` (o predicado `fundido` da lista, agora fonte única) sob o lock
    do usuário e da linha, que o PATCH do `/app` com data passou a tomar também. O `pode` só
    mudou no pagamento de conta fundido, que perde 'data' (fica `[categoria]`). Um
    teste-portão classifica toda função de produção que faz `update launches` com
    `valor =`/`criado_em =` (`tests/test_api_v2_lancamentos_valor_data.py`). Decisões do dono
    (2026-10-03): a fundida não acompanha correção do banco neste PR (PR 3); corrigir o valor
    da carteira não procura par novo no banco; o aviso ao apagar a fundida e o apagar que
    desfaz a junção na hora são do PR 2b-2, que vem depois. Limite declarado: linha com par
    pendente NÃO acionável (conexão pausada, outra moeda) conta como carteira pura, aceita
    valor novo, e o par volta depois com o valor velho.
  - PR 2b-2: apagar a linha fundida desfaz a junção na hora, em todo canal (P3). Todos apagam
    por `db.accounts.delete_launch_and_rollback`, que agora, antes do `delete`, devolve a
    transação do banco à lista como linha `banco` pelo mesmo miolo do desfazer
    (`db/reconciliation._desfaz`, extraído de `undo_reconciliation`). Antes, o `on delete set
    null` soltava o vínculo, o status ficava `confirmed`/`auto_merged` e o gasto sumia do
    Resumo até o próximo sync. A linha ligada ao banco (fundida OU com par pendente,
    `_LIGADO_SQL`) passa a travar `accounts` antes da linha, relida num statement separado
    depois do `for update`; se ligou no meio, o apagar recusa com `mudou_durante`. Ordem:
    `accounts` → lançamento → transação do banco. A função devolve a frase do aviso
    (`aviso_banco_voltou`) ou None: WhatsApp (singular e lote) e IA a acrescentam à resposta,
    o DELETE /launches do `/app` a devolve em `aviso` e o `dashboard.js` abre um alerta
    (decisão do dono, 2026-10-03), e o v2 segue respondendo `{id}`. O "apagar tudo" não
    desfaz (decisão do dono: as linhas do banco voltam no próximo sync). Ficou fora: a
    fundida acompanhar correção do banco (PR 3), limpar `pending` com `match` NULL, o
    deadlock antigo apagar × sync em linha ainda não ligada (segue o xfail estrito) e a #793.
    Limite declarado: duas transações do banco fundidas no mesmo lançamento (só com dado
    corrompido; o confirmar recusa `ALREADY_LINKED`) voltam as duas.
  - PR 3: identidade de importação por `(user_id, provedor, conta no provedor, transação)`;
    `external_id` novo usa a tupla JSON, sem depender da conexão local. Legado inequívoco
    conserva lançamento/compra, categoria, descrição e fatura; vínculo entre identidades
    diferentes recusa com `OF_IDENTITY_AMBIGUOUS`, sem reparação automática nem DDL.
    Reconectar a mesma identidade transfere o vínculo para a transação na conexão mais nova,
    preservando pendência/fusão e snapshot; o sync antigo não o retoma. Só há transferência
    quando a transação está no espelho novo: resposta parcial não descarta histórico antigo.
    Cartão é reutilizado pela identidade da conta; a associação só avança para conexão mais
    nova, mesmo ao importar histórico exclusivo da antiga. Havendo cartões duplicados do
    legado, prioriza quem já tem compras, depois o menor id, sem mover/consolidar compras ou
    faturas existentes. O estado ativo de cada cartão considera a conexão mais nova
    entre sua FK e os vínculos das compras/estornos; pausa/exclusão nova prevalece sobre
    conexão antiga ainda ativa. Assim os cartões legados separados mantêm a proteção
    contra compra manual após desconectar as conexões antigas. Sem FK nem vínculo,
    o cartão continua manual. Limpar a conexão velha relê os vínculos e
    não apaga a representação transferida, inclusive na janela concorrente do cleanup.
    Confirmar, fusão automática e sync aplicam valor/sinal/data/hora do banco à fundida.
    `posted_at`, `criado_em` e presença de hora mudam juntos, então lista e Resumo trocam de
    mês juntos; cartão segue o ciclo da fatura. `efeitos.of_original` guarda uma única vez
    os campos anteriores; `delta_conta`, categoria e descrição editadas são preservados.
    Desfazer restaura valor/data/tipo originais e cria a sombra atual do banco. Apagar a
    fundida continua devolvendo só o delta original à Carteira. Decisão do dono (2026-10-04):
    desconectar e `transactions/deleted` também restauram o original; pausa conserva vínculo
    e snapshot, sem desfazer. Sem escrita/reparo de dados em produção, deploy ou tela PR 4.
    Testes locais: `tests/test_of_identidade_e_campos_bancarios.py`, além das famílias de
    reconciliação, Open Finance, dinheiro em espécie, cartão e lançamentos v2. Não provam
    callback real da Pluggy, WhatsApp nem comportamento no aparelho após deploy.
- Guia do `/painel` (#728) em 2 PRs: A `GET`/`POST /api/v2/guia` + tabela `guia_painel` (contrato e consulta de medição em `docs/CLAUDE.md`, "API v2") · B a tela (Piggy, balão, Ajuda).
- [ ] Etapa 0 · [ ] 1 · [ ] 2 · [ ] 3 · [ ] 4 · [ ] 5 · [ ] 6 · [ ] 7
