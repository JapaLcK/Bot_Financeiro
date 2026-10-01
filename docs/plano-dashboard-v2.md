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
  banco mandar. A fonte do Rendimento × CDI volta ao dono na etapa de tela. Só compara com o
  CDI quando se sabe o período exato e que a posição existiu nele o tempo todo.
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
- Ingestão do Open Finance que devolve dado incompleto com cara de completo, e o sync
  segue como sucesso (achados na revisão do PR #689; afetam saldo, fatura e parcelas em
  toda tela). **Regra para todos os itens abaixo: o conserto inclui o passado.**
  Corrigir a ingestão daqui em diante não arruma o que já foi gravado com o dado errado,
  porque o sync seguinte não revisita esses registros (`sync_imported_open_finance_updates()`
  só move compra quando a data muda). Cada item precisa de um caminho de reprocessamento
  dos registros derivados: lançamento criado com o tipo errado, compra parcelada gravada
  sem grupo (sem `installment_no`, `installments_total` e `group_id`, e sem as faturas
  futuras), compra com `bill_id` escolhido por um calendário errado, valor em moeda errada,
  pagamento de fatura lido como estorno. O que foi descartado sem deixar registro (conta ou
  transação sem `id`) não tem o que reprocessar: ou se busca de novo o histórico pelo
  provedor, ou fica gravado que a cobertura daquele período está incompleta. Além da moeda:
  - `_to_decimal()` aceita `"NaN"` e `"Infinity"` como valor válido, o Postgres grava, e um
    saldo `NaN` envenena o `sum(balance)` do consolidado e as caixinhas e fotos dos
    investimentos. Todo valor de dinheiro exige `Decimal.is_finite()`; o que não passa
    torna a leitura incompleta;
  - `type` de conta que não é `BANK` nem `CREDIT` é guardado como veio, mas o saldo
    (`BANK_ACCOUNTS_SQL`) e os importadores só tratam esses dois: a conta some do produto
    com o sync dando sucesso. Validar o tipo contra uma lista aceita; fora dela, a conta
    fica como desconhecida e a leitura, incompleta;
  - `normalize_pluggy_account()` põe `type` = `BANK` e `balance` = 0 quando faltam, e o
    nome vira o tipo (`"CREDIT"`): como a adoção de cartão manual é pelo nome exato, nasce
    um cartão genérico em duplicidade que o nome certo, quando chega, não renomeia nem junta;
    `normalize_pluggy_transaction()` põe `amount` = 0 e data inválida = hoje (conta de
    crédito gravada como `BANK` já tem as transações importadas como lançamento; quando o
    tipo certo chega, o importador de cartão cria as compras e os lançamentos antigos
    ficam: o conserto inclui reclassificar o que foi criado com o tipo errado); conta e
    transação sem `id` são descartadas em silêncio, e o sync segue `ok=True`;
    `normalize_pluggy_investment()` põe `balance` = 0 e `type` vazio, e a posição, a
    caixinha espelhada e a foto diária são sobrescritas com esse valor;
  - compra parcelada sem `creditCardMetadata.totalInstallments` vira compra única (e
    `extract_installment_info()` aceita `installmentNumber` ausente, 0 ou maior que o
    total: o par só vale com o número entre 1 e o total), e a
    importada nunca cria as faturas futuras (a manual cria, em
    `add_credit_purchase_installments()`). Ao materializar as parcelas restantes, a parcela
    real que chega depois tem outro id externo e entraria ao lado da projetada (a deduplicação
    é por `(user_id, source, external_id)`), cobrando a fatura duas vezes: a parcela
    projetada é substituída pela real por identidade da compra mais número da parcela, ou a
    projeção fica fora das transações importadas;
  - cartão sem as datas da Pluggy ganha fechamento dia 1 e vencimento dia 10
    (`get_or_create_open_finance_card()`); o calendário de cartão já ligado não é
    atualizado, e o cartão manual adotado pelo nome fica com as datas manuais, sem conferir
    as da Pluggy;
  - `list_pluggy_transactions()` para em `max_pages=60` sem conferir o cursor `next`, e
    `list_pluggy_accounts()` lê só a primeira página de `/accounts`, cuja paginação é
    outra (página e total no próprio corpo, não o cursor `next` das transações): ela precisa
    da sua própria validação e de percorrer todas as páginas, separada da regra do cursor. Além do teto, as duas
    aceitam resposta malformada como leitura completa: `results` ausente ou fora de lista
    vira lista vazia, e página vazia com `next` ou cursor ilegível encerra a leitura. Validar
    o formato e o cursor terminal antes de dar o sync como completo, e detectar cursor
    repetido ou sem progresso: ao tirar o `max_pages`, um `next` que volta ao mesmo cursor
    roda para sempre. Um teto de segurança que se mantenha, quando esgotado, marca a
    leitura como incompleta, nunca como sucesso. Tudo isso vem antes de qualquer
    conciliação de ausências (item abaixo), que com leitura parcial apagaria transação
    legítima;
  - conta que some da resposta de `/accounts` segue somada com o saldo antigo (e apagar a
    conta direto leva as transações em cascata sem desfazer lançamentos e compras ligados:
    a conciliação de conta segue o ciclo de `delete_open_finance_transactions()` e
    `disconnect_open_finance_connection()`, com `_rollback_imported_of()`, preservando o
    lançamento manual fundido e desligando o cartão), e
    transação que some de uma sincronização completa também fica: `save_open_finance_sync()`
    só faz upsert do que veio, então um `transactions/deleted` perdido deixa a compra ou o
    estorno (e o lançamento e a fatura ligados) para sempre. A conciliação de transação
    ausente usa o mesmo ciclo da conta: `delete_open_finance_transactions()`, que chama
    `_rollback_imported_of()`, tira a sombra, desfaz o vínculo com o dinheiro em espécie e
    reconcilia os movimentos de banco, nunca um delete direto. Só que hoje
    `_rollback_imported_of()` engole a exceção ao desfazer a compra ou o lançamento, e a linha
    do Open Finance é apagada assim mesmo, sem nada para tentar de novo: a conciliação só
    apaga a linha quando o rollback deu certo; se falhar, a linha fica e é marcada como
    incompleta. Conciliar ausências só com
    coleta saudável e geração estável: mesmo com resposta bem formada e cursor terminal,
    um `PARTIAL_SUCCESS`/`UPDATING` ou uma coleta nova no meio da leitura apagaria
    transação legítima. Reusar o portão que a conciliação de investimentos já tem (produto
    saudável e a mesma geração em duas fotos do item), e conferir a geração de novo **depois**
    de pegar o `pluggy_item_lock`: hoje a segunda foto é tirada antes da trava, e um sync
    antigo que pega a trava depois de um mais novo apagaria como ausentes as contas e
    transações que o novo acabou de gravar. E conciliar só dentro de um intervalo
    de datas explícito: hoje `list_pluggy_transactions()` pede só `accountId` e o cursor, e
    uma resposta vazia não diz nada sobre datas. O intervalo tem de vir do pedido (datas
    `from`/`to` enviadas) ou de uma marca do provedor guardada; tirar das transações
    devolvidas não serve. Fora do intervalo, o que está gravado fica, porque o banco pode
    devolver um histórico mais curto que o anterior;
  - banco religado guarda o `last_sync_at` antigo; `connection_ui_state()` já trata
    `last_sync_at < reconnected_at` como não sincronizado, e é essa a fonte do estado da
    conexão, não a idade do sync.
  - Transação e fatura do cartão:
    - o `status` da transação (PENDING × POSTED) é ignorado: autorização pendente entra
      na fatura e, se for lançada com outro id, conta duas vezes;
    - a moeda da transação (`currencyCode`, `amountInAccountCurrency`) é ignorada: compra
      internacional entra em reais pelo valor na moeda original;
    - a fatura é calculada localmente pelo fechamento do cartão; o `billId` e o endpoint
      `/bills` da Pluggy (total, vencimento, mínimo) nunca são lidos, e nada confere o
      `balance` da conta de crédito;
    - o pagamento de fatura é reconhecido por palavra-chave (`is_credit_card_payment`):
      outro texto vira estorno e reduz a fatura, e estorno com o texto certo é pulado. Como
      nada fecha a fatura importada, o histórico importado aparece como vencido;
    - o sinal do estorno (`amount > 0`) só foi conferido no sandbox;
    - a chave do grupo de parcelas (cartão, descrição, número de parcelas, `totalAmount` e
      mês de origem estimado) erra nos dois sentidos: banco que escreve "01/10", "02/10"
      divide uma compra em vários grupos, e duas compras iguais no mesmo lugar e no mesmo
      mês (ou duas sem descrição, que viram "Transação") caem no mesmo grupo, e
      `undo_installment_group()` mexe nas duas. Precisa de identidade sem colisão, ou o
      grupo ambíguo conta como incompleto.
  - Cartão em duplicidade: adotar um cartão manual mantém as compras já lançadas nele e
    importa as mesmas de novo; OFX e Open Finance podem encher o mesmo cartão (o OFX só
    deduplica `source='ofx'`); reconectar com item novo pode criar um segundo cartão
    "· Open Finance" e deixar o primeiro com as faturas congeladas.
  - Toda exceção engolida dentro de `_sync_pluggy_item_confirmado()` sai como conexão
    `ACTIVE`/`ok=True`, só com log: falha ao ler investimentos, ao gravar o espelho de
    investimentos (`save_open_finance_investments()`), ao espelhar caixinhas e ao gravar a
    foto diária (`grava_fotos_posicoes()`, que só desfaz o savepoint). Regra para a classe:
    qualquer parte do sync que grava dado financeiro e falhe marca a sincronização como
    incompleta, com o produto que faltou. Fica de fora o que não é dado financeiro, como o
    disparo de agentes, que o código já isola de propósito. Sem outro sync no mesmo dia, o histórico fica com um
    buraco permanente, sem nova tentativa (afeta patrimônio e rentabilidade, não a previsão).
- Quando o dado do Open Finance conta como desatualizado (limite por produto) e como a
  tela aberta percebe isso sem escrita.
- Rentabilidade do Open Finance: medida em produção em 2026-09-29 (leitura, pelo dono;
  remeça antes de reusar). `lastMonthRate`, `lastTwelveMonthsRate`, `annualRate`,
  `fixedAnnualRate` e `amountProfit` vieram nulos em todas as posições; no CDB, `rate` +
  `rateType` = `CDI` é a taxa de CONTRATO (100 = 100% do CDI), não rentabilidade; `date` é a
  data da posição informada pelo banco, dias atrás da coleta e diferente entre posições da
  mesma conexão; posições resgatadas (`TOTAL_WITHDRAWAL`) continuam no espelho; e há
  conexão `PARTIAL_SUCCESS` com investimentos não confirmados. Resultado: não há hoje taxa
  do banco para comparar. **Pendência com o dono na etapa de tela:** de onde sai o
  Rendimento × CDI. A regra continua — a comparação usa a taxa que o banco calcula, nunca a
  diferença entre fotos do rendimento acumulado.

**Etapas de tela (1 a 6)**
- Etapa 2: identidade das transações importadas por conta (conta e cartão); editar a data
  de um lançamento fundido.
- Etapa 3: desde a Q42 o gasto fixo diário, semanal e único entra na Previsão, uma
  ocorrência por data — um diário gera até 90 itens em `compromissos`/`causas`. A tela
  `/previsao` tem de agrupar por nome; o código de hoje não agrega nem limita.
- Etapa 3: defeitos da previsão de hoje (`cashflow._cashflow_events()`), achados na
  revisão do `docs/plano-piggy-assistente-contextual.md` (PR #689). Não se consertam no
  painel antigo: a regra reescrita para a Previsão da `/api/v2` passa a servir também o
  simulador e o `check_cashflow` da IA (Q18). A lista de verificação da Etapa 3 é a soma
  das duas fontes: esta seção, que tem itens que a matriz não tem, e a matriz do plano da
  Piggy, que dá a direção de erro de cada entrada. Nenhuma das duas sozinha é completa.
  - Gasto fixo pago no cartão (`payment_type="credit_card"`): sai do caixa no `due_day` e
    de novo dentro da fatura aberta, ou sai antes da data de pagar a fatura.
  - Valor estimado (`variable_amount`) entra como exato, no boleto e no gasto fixo.
  - Conta paga pelo banco sem passar pelo PigBank continua pendente e sai de novo: o
    boleto (nada do Open Finance escreve em `bill_instances`) e a fatura (a importação
    pula o pagamento de fatura; só `pay_bill_amount` a marca paga).
  - `list_bills(..., limit=1000)` corta os boletos mais distantes sem avisar, e boleto
    pendente entra com qualquer valor (um negativo vira entrada de dinheiro).
  - Gasto fixo manual (`payment_mode="manual"`) só entra pelo boleto já gerado, e
    `sync_manual_bills_once()` gera só o próximo ciclo: numa previsão de 90 dias, um
    mensal entra uma vez e some nas duas seguintes, e o semanal e o diário quase somem.
    Projetar as ocorrências (ou gerar o horizonte inteiro) sem contar duas vezes o boleto
    que já existe.
  - O saldo de partida perde as pendências: `get_consolidated_balance()` devolve
    `reconciliation` (conciliação a confirmar, com `delta_se_confirmar`) e
    `bank_movements` (declaração não confirmada), e `_starting_balance()` guarda só o
    número. O mesmo vale para toda pendência que pode criar, pagar ou mover dinheiro, com
    valor conhecido ou não. A lista sai do registro, não de nomes escritos aqui: todo tipo
    de `_REGISTRO` em `db/pending.py` cujo efeito muda dinheiro em qualquer sentido, inclusive
    apagar e desfazer (`delete_launch`, `delete_launch_bulk`, `delete_credit_purchase`,
    `undo_audio`), além de criar, pagar ou mover (lançamento,
    parcelas no cartão, pagamento de conta, débito de uma fonte, recorrente nova; hoje,
    entre outros, `multi_launch_values`, `bill_pay_amount`, `payment_method_choice`,
    `installment_pending`, `pay_bill_choice`, `bill_amount_expected`, `investment_pick`,
    `funding_source_choice` e `confirm_recurring_offer`), mais as escritas propostas pela
    IA em `ai_pending_actions`. Tipo novo no registro entra pela classificação do efeito. A previsão
    compartilhada tem de levar essas pendências e mostrar o resultado como "a conferir",
    não como exato.
  - Receita recorrente mensal ou anual entra pelo valor cheio, sem marcador de
    confiança: renda irregular (freela, comissão) cadastrada como fixa parece garantida.
    Decidir uma política de confirmação ou de confiança da receita projetada.
  - Receita recorrente legada `once`, `weekly` ou `daily` fica fora de toda data:
    `_cashflow_events()` só aceita receita mensal e anual. Decidir o destino dessas linhas.
  - Fatura de cartão manual, ou sem fonte do Open Finance atualizada, entra pelo total
    gravado, que não tem compra não lançada nem parcela restante. Com o cartão manual só
    para leitura (Q37), decidir se essa fatura é confirmada pelo usuário ou sai como "a
    conferir".
  - Ocorrência de recorrente com vencimento hoje ou já passado, ainda não realizada, some
    da previsão (`_recurring_occurrence_dates()` só emite datas estritamente depois de
    hoje), e a que se realizou
    antes do dia entra de novo; não há marcador de realização (`last_charged_ym` e
    `last_credited_ym` não são escritos). Consertar só o `>` estrito deixa de fora as
    atrasadas mais antigas, e projetar desde o `start_date` repete anos já realizados:
    definir uma janela de conferência limitada, com a premissa do que veio antes dela dita
    na tela (é a decisão aberta 7 do plano da Piggy).
  - Gasto variável do dia a dia (mercado, transporte) fica fora: a previsão supõe que ele
    para hoje. O protótipo do v2 já desconta o ritmo dos últimos 60 dias
    (`webapp/src/dashboard/lib/model.js`); a Etapa 3 leva uma estimativa assim para o
    backend, marcada como estimativa, com a medida de variação e a faixa provável
    (`lo`/`hi`, que `TrajectoryChart.tsx` e `Hero.tsx` mostram como "Faixa provável"),
    e sem contar duas vezes: o ritmo exclui todo gasto que
    já entra como evento agendado (gasto fixo, boleto pago, parcela de compra parcelada que
    já existe, cujo restante vai para as faturas futuras) e todo movimento interno
    (`is_internal_movement`: transferência entre contas próprias e pagamento de fatura). A
    compra comum no cartão **fica** na amostra: ela é o comportamento futuro; o que não se
    projeta de novo é a compra já lançada na fatura. A parte do cartão é projetada a partir
    de amanhã, e cada compra estimada cai na fatura atual ou numa seguinte pelo calendário de
    fechamento, inclusive o resto do ciclo atual até o fechamento. O protótipo já exclui os lançamentos
    de recorrente. O denominador é o período coberto de verdade,
    não 60 fixo: conta recém-conectada ou com histórico incompleto (dez dias divididos por
    60 dão um sexto do ritmo real) tem amostra mínima, e abaixo dela a estimativa sai como
    "a conferir".
    O ritmo se divide pela forma de pagamento: o gasto em dinheiro, Pix e débito sai do
    caixa no dia, e o gasto no cartão entra na fatura de cada cartão pelo calendário de
    fechamento e vencimento dele (o protótipo desconta tudo por dia, e isso põe o gasto no
    cartão na data errada e muda o pior dia).
  - A carteira Piggy não tem data de atualização: a confirmação da Q37 vale na primeira
    visita e envelhece. Com a carteira no saldo, pedir confirmação atual ou mostrar "a
    conferir".
  - Recorrente e boleto fora de sincronia:
    - gasto de valor variável sem estimativa é gravado com valor 0 (`db/recurring.py`) e
      some da previsão;
    - desativar o gasto fixo não cancela o boleto pendente (`list_bills` não olha
      `is_active`);
    - trocar de manual para automático conta duas vezes (o boleto pendente fica e a
      ocorrência automática entra);
    - mudar o calendário (dia, frequência, `due_month` ou `start_date`) deixa o boleto
      velho na data antiga, e o novo também entra: toda mudança de calendário cancela ou
      refaz os boletos pendentes;
    - o valor do boleto é copiado ao gerar e não acompanha a edição do gasto fixo;
    - boleto manual de gasto pago no cartão sai como dinheiro na data do boleto;
    - não existe data de fim nem número de parcelas restantes na recorrência, então
      financiamento ou contrato que termina dentro do horizonte segue projetado;
    - recorrência anual legada sem mês some.
  - Conta marcada como paga "pelo banco" sai da previsão na hora, mas o saldo só cai na
    próxima sincronização: por um tempo o dinheiro conta duas vezes. O mesmo vale para o
    pagamento de fatura pelo PigBank com Open Finance (`pay_bill_amount()` grava com
    `apply_delta=False`, sem `bank_movement_declarations`): a fatura some na hora e o saldo
    só cai depois. Até a sincronização seguinte, a previsão leva essa saída pendente ou
    mostra "a conferir".
  - Datas sem dia útil: vencimento no fim de semana ou feriado e salário pago no dia útil
    anterior mudam o pior dia nos dois sentidos.
  - Fatura com total negativo (crédito por estorno) é ignorada, e o `credit_bills.total` é
    um contador (`greatest(0, total - x)` ao desfazer) lido sem reconstruir. O `status`
    também não acompanha: quando uma correção da Pluggy aumenta o valor de uma compra de
    fatura já paga, o total sobe e a fatura segue `paid`, fora da previsão. O "paga ou não"
    tem de sair de `total - paid_amount`, não ficar gravado; o "aberta ou fechada" continua
    vindo do calendário de fechamento, porque `close_bill()`, `list_open_bills()` e
    `list_bills_with_debt()` dependem dele para achar a fatura atual e a atrasada.
  - Saldo de partida: o `BANK_ACCOUNTS_SQL` escolhe a conexão mais nova por `id` antes de
    filtrar as pausadas, então a conta some se a mais nova estiver pausada, mesmo com uma
    antiga ativa; o `balance` do banco é usado sem conferir o que ele inclui (aplicação
    automática, cheque especial); a caixinha do banco que resgata sozinha fica fora.
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
- [ ] Etapa 0 · [ ] 1 · [ ] 2 · [ ] 3 · [ ] 4 · [ ] 5 · [ ] 6 · [ ] 7
