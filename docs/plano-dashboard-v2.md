# Plano do dashboard v2

Como o protótipo `webapp/src/dashboard/` (o "dashboard-v2") substitui o dashboard de
produção (`frontend/dashboard.html` + `frontend/dashboard.js`, servido em `/app`).

Decisões tomadas pelo dono em 2026-09-25, numa sessão de perguntas (Q1–Q35). Cada item
cita a pergunta que o decidiu. Sem prazo: o critério é qualidade (Q4). **Cada PR deste
plano marca aqui o que concluiu**, na seção "Andamento".

## 1. Objetivo e como a troca chega aos usuários

- **Meta final: tudo no v2.** O `dashboard.html` e o `dashboard.js` são apagados no fim
  (Q1). No caminho, a tela ainda não migrada abre no painel antigo por link, com um aviso
  discreto de "abre no painel antigo" (Q1, Q15).
- **Rota paralela `/painel`** para o v2; `/app` continua o antigo. Links nos dois sentidos
  ("Experimentar o novo painel" / "Voltar ao painel antigo"), visíveis só para quem está
  na lista de liberados; a escolha não é salva. No corte final, `/app` passa a abrir o v2
  (Q2, Q12).
- **Chave por usuário:** `dashboard_v2_enabled(user_id, email)`, no mesmo padrão das listas
  de liberados que já existem em `core/services/plan_service.py` (`agents_beta_tester`,
  `bank_list_ui_enabled`), com a lista numa variável de ambiente e o valor chegando ao
  navegador pelo `/auth/me` (Q11). A chave vale **no servidor**, não só nos links: a rota
  `/painel` confere a chave e manda para o `/app` quem estiver fora da lista, então abrir
  ou compartilhar o endereço direto não fura a liberação. Teste: usuário fora da lista
  abrindo `/painel` direto cai no `/app`; usuário da lista abre o v2. A rota também manda
  para o `/app` quem chega com o marcador `PigBankApp` no user agent, mesmo liberado: o
  WebView do app atual navega em `pigbankai.com`, e um link compartilhado abriria o v2 lá
  dentro. Aqui o user agent escolhe qual tela, não concede acesso. Teste: `PigBankApp`
  liberado abrindo `/painel` direto cai no `/app`.
- **O app atual (Capacitor) nunca mostra o v2.** Ele carrega o site ao vivo, então os links
  de troca ficam escondidos quando o user agent **contém** `PigBankApp` — a mesma checagem por
  trecho de `_is_pigbank_app` (`frontend/routes/shared.py`) e do `app-mode.js`: o WebView
  anexa o marcador ao user agent do Safari e a versão muda, então comparação exata falha.
  Isso só esconde interface; nunca concede nem nega acesso (o user agent é alegação do
  cliente) (Q10). O app novo (Expo)
  terá telas próprias e consome a mesma API nova (Q17, Q31).
- **Quando abrir para mais gente:** decisão do dono, sem critério automático (Q16).
- **Tema:** só escuro no primeiro corte; o tema claro vem numa fase seguinte (Q9).

### O que a primeira versão precisa ter (Q3)

Resumo, Lançamentos (ver, lançar, editar, apagar), Previsão, Metas e caixinhas (com
depositar e retirar), Para onde vai, Patrimônio e o chat do Piggy com IA real e blocos (Q6).
Pix, conexão do Open Finance, MFA e notificações já moram em `settings.html` /
`precos.html` e continuam lá; o v2 só aponta para elas. O resto (orçamentos, orçamento
doméstico, cartões, categorias, agentes, afiliados, exportar, importar OFX, ajuste de
saldo, vínculo do WhatsApp) abre no antigo até ser migrado, um de cada vez.

## 2. A API nova: `/api/v2`

Escolha do dono: API nova, pensada para manutenção de longo prazo, simplicidade e
tecnologia, podendo refazer o que for preciso, com calma (Q5).

- **Quem usa:** o dashboard v2 e o app novo (Expo). O WhatsApp não chama HTTP: roda no
  mesmo servidor e usa as mesmas regras de negócio direto (Q17).
- **Organização:** pasta própria `api/v2/` com `rotas/`, `esquemas/` (Pydantic) e
  `dominio/` (regras), em arquivos pequenos por assunto. Nada entra no
  `frontend/finance_bot_websocket_custom.py`; ele só registra o roteador (Q26).
- **Regras de negócio num lugar só (Q18).** Cada regra reescrita para a API nova passa a ser
  usada também pelo WhatsApp, e a versão antiga sai no mesmo PR. Nunca duas versões da
  mesma conta. (Hoje saldo e gasto do mês já estão duplicados entre o WhatsApp e o
  dashboard; ver o comentário perto de `get_financial_data` no monólito.)
- **Segurança da migração do WhatsApp (Q30):** todo PR de regra roda os testes de conversa
  (`handle_incoming`, com estado real) e compara os números antes e depois para um conjunto
  fixo de usuários de teste. Número que muda: o PR explica (conserto) ou não entra.
- **Isolamento por construção (Q20, Q23):** nenhuma rota recebe `user_id` de fora. Uma
  dependência única (`Depends(usuario_atual)`) aceita o cookie de sessão (web, com CSRF) e
  o Bearer (app), barra plano inativo e entrega o usuário. Um teste varre todas as rotas da
  `/api/v2` e falha se alguma não tiver a dependência. Cada rota tem teste de "o usuário B
  não recebe dado do A". Sem `user_id` no caminho, o id do recurso (lançamento, caixinha…)
  ainda vem de fora: toda query de escrita filtra também por `user_id` do `usuario_atual`, e
  cada rota que altera ou apaga tem teste de B mandando o id de A — B recebe recusa (404,
  para não revelar que o id existe) e o registro de A continua igual.
- **Plano por recurso (Q20):** estar logado com plano ativo não basta. Rota de recurso pago
  declara também `Depends(recurso("forecast"))` (ou `insights`, `simulator`, `ai_chat`…),
  que chama o `plan_gate_ok` de `core/services/plan_service.py` — a matriz
  `FEATURE_MIN_TIER_V2` continua a fonte única, sem cópia na `/api/v2`. Como o `plan_gate_ok`
  trata nome desconhecido como plano de entrada, o `recurso()` recusa, **ao montar a rota**
  (o servidor nem sobe), nome que não esteja na matriz nem nos casos especiais que ele
  conhece (hoje só `ai_chat`, que é cota mensal e não plano) — um erro de digitação não vira
  acesso liberado em silêncio. Recusa sai no envelope de erro (`plano_insuficiente`, ou
  `cota_esgotada` no `ai_chat`). O teste que varre as rotas exige, em cada uma, ou a
  dependência de recurso ou a presença numa lista explícita de rotas do plano de entrada. Cada
  rota paga tem teste na fronteira: o plano abaixo recebe a recusa, o plano mínimo recebe o
  dado; as do `ai_chat` têm também o teste da cota esgotada. O gate pelo `/auth/me` na tela
  (seção 3) só decide o que mostrar; quem protege é o servidor.
- **Limites por valor, além do sim/não (Q20):** o plano também limita *quanto* se vê, não só
  *se* se vê — a janela de histórico (`history_days`, `history_current_month_only` em
  `core/services/plan_limits.py`) e os tetos (`launches_month_max`, `pockets_max`…). Toda
  rota de leitura com histórico corta a consulta no servidor pelo `history_earliest_date`
  (`core/services/plan_service.py`), como as rotas atuais já fazem, e toda rota de escrita
  com teto confere o teto. Teste por rota: o plano de entrada não recebe nada antes do
  corte, o plano sem corte recebe; e a escrita acima do teto é recusada.
- **Contrato (Q22):** request e response declarados em Pydantic (`response_model` em toda
  rota) → especificação OpenAPI → tipos TypeScript gerados para o v2 e para o app (e o zod
  do app, gerado da mesma especificação). **Exceção: a rota de eventos (SSE)**, que devolve
  um stream e não tem `response_model` útil. O formato de cada evento (`{"mudou": [...]}`) é
  um modelo Pydantic próprio, registrado nos componentes da especificação para os tipos
  saírem gerados como os outros. O teste que varre as rotas (Q23) aceita essa exceção pelo
  nome, e só ela. A especificação fica disponível só em
  desenvolvimento; em produção continua desligada, como hoje.
- **Erro (Q25):** envelope único `{"erro": {"codigo": "...", "mensagem": "...",
  "campo": null}}`, com os códigos listados no contrato.
- **Tempo real (Q19, Q27–Q29):** SSE, só servidor → cliente. O aviso carrega só *o que*
  mudou (`{"mudou": ["lancamentos", "saldo"]}`), nunca o dado; a tela pede de novo à API.
  Um lançamento feito pelo WhatsApp atualiza o painel aberto. **Toda escrita de dado
  financeiro avisa**, não só criar lançamento: editar e apagar lançamento, depositar e
  retirar de caixinha, aporte e resgate, fatura, contas fixas, metas, sincronização do Open
  Finance. Como as regras moram num lugar só (Q18), o aviso sai de dentro da regra, que
  declara os tipos que mudou; cada PR que migra uma regra traz o teste de que ela avisa os
  tipos certos. Mas durante a convivência o código antigo também escreve (o ajuste de saldo
  em `adjust_balance_route`, no monólito, só invalida o cache do painel antigo), e testar
  "cada tipo tem alguma escrita que avisa" passa com o caminho novo enquanto o antigo fica
  mudo. Então na etapa 0 o aviso entra **nas funções que escrevem nas tabelas financeiras**
  (a camada `db/`, por onde passam o painel antigo, o WhatsApp, o v2 e a sincronização), e
  as escritas que hoje estão direto numa rota passam para essa camada. O teste enumera
  **os caminhos de escrita, não os tipos**: varre o código atrás de `insert`, `update` e
  `delete` nas tabelas financeiras e falha se algum estiver fora de uma função que avisa. O aviso não se guarda: se a
  conexão cair, o que mudou nesse meio-tempo se perde. Por isso, **a cada (re)conexão** o
  cliente invalida todas as consultas e a tela pede tudo de novo — cobre a queda, a volta do
  sono do computador e a janela entre a primeira carga e a conexão abrir. A sessão expira em
  15 minutos e só o `fetch` sabe renová-la (`frontend/static/auth-refresh.js`); o
  `EventSource` que recebe 401 fecha de vez. Então, quando a conexão fecha, o cliente faz uma
  chamada leve pela API (o `fetch` renova a sessão se precisar) e recria o stream; se a
  sessão acabou de fato, segue o caminho normal de sessão encerrada. O inverso também vale: a
  dependência de usuário só roda na abertura, então um stream aberto não perceberia sozinho
  a sessão vencida ou revogada. Por isso o servidor **fecha o stream quando vence o token
  que o abriu** (no máximo 15 minutos, e o cliente reconecta pelo caminho acima) e quando
  a sessão é revogada. A revogação não é avisada por quem revoga: há vários caminhos que
  gravam `auth_sessions.revoked_at` (logout e "sair de todos" em `core/sessions.py`, replay
  de refresh e ociosidade em `core/refresh_tokens.py`, o painel de admin), e avisar em cada
  um seria remendo por instância. Em vez disso, **o próprio stream confere a sua sessão no
  banco a cada 30 segundos** (junto do keepalive) e fecha se ela foi revogada — cobre todo
  caminho, os de hoje e os futuros, em qualquer processo, sem canal extra. Teste: revogar
  por um caminho que não é endpoint (replay de refresh) com o stream aberto em outro
  processo — ele fecha em até 30 segundos e não recebe mais nada. O teste de ponta a ponta
  do aviso (Q32) inclui derrubar a conexão, lançar e reconectar, e reconectar com a sessão
  vencida. O aviso sai de uma função única, e **todo processo que grava dado financeiro tem
  de alcançá-la**. Hoje produção roda dois: o `launch.py` sobe o uvicorn e também o
  `bot.py` (o bot do Discord, morto como produto mas vivo no deploy, e que grava pelo
  `core_handle_incoming`). A etapa 0 começa confirmando com o dono se o `bot.py` sai do
  `launch.py`. Se sair e sobrar um processo só, o aviso fica dentro dele; se ficar, ou com
  uma segunda instância, a função usa `LISTEN/NOTIFY` do Postgres desde a etapa 0.
  Duas regras valem nos dois casos:
  - **o aviso só chega depois do commit, e chega sempre.** Aviso antes do commit faz a
    tela pedir de novo e ler o estado velho, sem outro aviso depois; aviso depois do commit
    feito à parte pode não sair se o processo cair entre os dois. Por isso cada caso usa o
    seu mecanismo: com `NOTIFY`, ele é emitido **dentro** da transação da escrita (o
    Postgres só entrega quando ela confirma, e nada se ela desfaz); com o aviso dentro do
    processo, a função roda **depois** do commit com sucesso — ali, se o processo cai, cai
    junto o stream, e a reconexão refaz tudo. Com `NOTIFY` há mais uma queda possível: a
    conexão de `LISTEN` do servidor cai e volta enquanto o navegador segue conectado, e o
    Postgres não reenvia o que foi avisado nesse intervalo. Por isso, quando o `LISTEN`
    cai, o servidor **fecha todos os streams** que dependem dele; cada navegador reconecta e
    refaz tudo pelo caminho normal. Enquanto o `LISTEN` não voltar, **nenhum stream novo
    abre**: a rota responde 503 com um tempo de nova tentativa, e o cliente tenta de novo
    com espera crescente — senão um stream aberto durante a queda pediria tudo uma vez e
    perderia as escritas seguintes. Teste separado: derrubar só a conexão de `LISTEN`,
    fazer duas escritas durante a queda, e ver a tela atualizar com as duas quando ela
    voltar. Teste: uma escrita com o commit atrasado de
    propósito — a tela só pede de novo depois dele e vê o dado novo; e uma escrita que
    desfaz não gera aviso.
  - **só para o dono.** O aviso interno leva o `user_id` de quem teve o dado mudado (o que
    vai para o navegador continua só `{"mudou": [...]}`), e cada stream assina só o usuário
    do `usuario_atual`. Um aviso global deixaria B saber quando A mexe no dinheiro e faria
    todo painel aberto pedir de novo a cada escrita de qualquer um. Teste: B conectado não
    recebe nada quando A lança.

  O `/ws` antigo sai junto com o dashboard antigo.
- **Processo (Q21):** todo PR que cria ou muda endpoint da `/api/v2` é **faixa Completo**,
  com o time inteiro, os testes de isolamento e o Codex.

### Dados que não existem hoje (Q14, Q35)

- **Patrimônio em 12 meses:** um job diário grava uma "foto" do patrimônio de cada usuário.
  Ele entra **na etapa 0**, antes de qualquer tela, para o histórico começar a encher o quanto
  antes; enquanto enche, o gráfico diz que se completa com o tempo. Nada de reconstruir o
  passado (mostraria número errado com cara de certo).
- **Tabela nova por usuário entra no ciclo de privacidade.** As fotos do patrimônio e das
  posições são histórico financeiro do usuário, e `db/privacy.py` enumera as tabelas à mão.
  Toda tabela nova com dado de usuário entra, no mesmo PR que a cria, na exportação
  (`build_user_export_zip`), no "Recomeçar do zero" (`_RESET_TABLES` — senão o painel
  reconstruído mostra pontos velhos) e na exclusão de conta (`delete_user_data`, com a
  chave estrangeira em cascata). Testes do ciclo: o dado aparece na exportação e some no
  reset e na exclusão.
- **Rendimento × CDI:** a série do CDI já existe (`db/investments.py`), mas o lado da
  carteira não tem histórico: `investments`, `investment_lots` e `open_finance_investments`
  guardam só o saldo atual, sobrescrito a cada juro ou sincronização. A regra: **nenhum
  número inferido**. Cada origem usa a fonte que sabe separar rendimento de aporte e resgate:
  - **Open Finance:** a rentabilidade que o próprio banco calcula por posição
    (`lastMonthRate` e `lastTwelveMonthsRate`, que o código já lê em `db/rv.py`). Das fotos
    não dá para tirar isso: dois movimentos que se anulam entre duas sincronizações somem
    no fluxo líquido. A taxa é gravada **a cada sincronização com sucesso**, por posição e
    por mês (a última do mês vale), dentro de `save_open_finance_investments`
    (`db/open_finance.py`) e antes de sobrescrever ou apagar a linha do espelho — não pelo
    job diário, que chegaria tarde para a posição liquidada entre duas rodadas. O histórico
    fica numa tabela própria, que a reconciliação não apaga: a posição que o banco deixou de
    mandar continua na série dos meses em que existiu, marcada como encerrada. A cascata do histórico fica só para o
    usuário, pela regra de privacidade abaixo.
  - **Manuais:** a unidade é o **lote** (`investment_lots`), não o investimento: cada lote
    tem indexador, taxa e cursor de juros próprios (um investimento pode ter um lote de CDI
    e outro de IPCA, cada um em dia até uma data diferente). O job grava, por lote, o valor,
    o principal e a **data efetiva** — o fim do período que a taxa já aplicada cobre, que
    nem sempre é o cursor do banco (no IPCA mensal a chave é o dia 1 e o fator vale o mês
    inteiro, então a data efetiva é o último dia do mês). O histórico tem identidade
    própria (id e nome guardados nele, sem chave estrangeira em cascata para `investments`
    ou `investment_lots`): lote resgatado ou investimento apagado (`delete_investment` apaga
    a linha) continua nos meses em que existiu, marcado como encerrado.

    **Quando se tira a foto.** Uma por dia e mais uma antes e outra depois de cada aporte e
    resgate, no mesmo commit do movimento (`investment_deposit_from_account`,
    `investment_withdraw_to_account` e todo outro caminho que mexa no principal — o
    inventário por `grep` é o primeiro passo do PR do job). Toda foto é tirada **logo
    depois de calcular os juros daquele usuário** (`accrue_all_investments` roda hoje num
    laço próprio em `core/services/investment_scheduler.py`), na mesma operação. O cálculo
    (`_growth_for_period`) compõe de uma vez todas as taxas que faltavam e devolve só a
    última data, então um atraso que atravessa uma ou mais viradas de mês jogaria tudo no
    último mês. Por isso o job **para em cada virada**: chama o cálculo até o último dia de
    cada mês coberto (`accrue_investment_db` já aceita `today=`), tira a foto ali, e só
    então segue para o próximo. Isso vale para **todo** caminho que calcula juros e tira
    foto, não só o job: o aporte e o resgate também chamam o cálculo direto
    (`db/investments.py`, dentro de `investment_deposit_from_account` e
    `investment_withdraw_to_account`). Por isso existe uma função só, "juros em dia com
    foto", que faz o corte por virada, e o job e os movimentos a chamam; ninguém chama o
    cálculo cru antes de uma foto. Teste: laço de juros parado por uma virada e o usuário
    resgata antes de ele voltar — cada mês recebe o seu. Aporte com
    data no passado (`purchase_date`) só conta a partir da primeira foto, já com os juros em
    dia: rendimento de antes dela nunca entra.

    **A conta: CDI no mesmo dinheiro, nos mesmos dias.** Para cada lote, o rendimento é a
    variação do valor entre fotos, tirando o par antes→depois de um movimento (que é o
    próprio dinheiro entrando ou saindo). A comparação é uma **carteira-sombra**: o mesmo
    capital de cada lote, nos mesmos dias cujo rendimento o lote de fato recebeu (até a
    data efetiva dele, não até a data do resgate), rendendo CDI. Resgate de lote de índice
    com taxa atrasada é pago pelo que já foi calculado, e a taxa que sai depois nunca é
    aplicada ao capital que saiu — é a regra atual do produto, e mudá-la é outra decisão;
    a sombra não conta esses dias, então lote e CDI comparam os mesmos dias. O bloco mostra, por investimento, quanto rendeu e quanto teria
    rendido no CDI (em reais) e a razão entre os dois ("% do CDI"). Quando a sombra rende
    zero (lote aberto e resgatado no mesmo dia, ou período sem dia útil de CDI), a razão não
    existe: a API devolve `pct_cdi: null` com o motivo `sem_periodo_cdi`, nunca erro,
    infinito ou NaN, e o bloco mostra só os valores em reais. Isso fecha de uma vez os
    casos que uma média de percentuais erra: aporte e resgate no meio do período, lote aberto
    ou encerrado no meio do mês, investimento que fica zerado e depois recebe aporte (sem
    capital a sombra não rende), lotes com indexadores diferentes (cada um até a sua data
    efetiva) e taxa publicada com atraso (o rendimento cai no período que a taxa cobre; o
    mês cuja taxa ainda não saiu aparece como "em apuração").

    **Pré-requisito no código de hoje** (faixa Completo, dinheiro, antes do job): o resgate
    parcial (`investment_withdraw_to_account`) grava `last_date = hoje` nos lotes que
    continuam abertos mesmo quando o juro parou antes por falta de taxa, e os dias entre um
    e outro nunca rendem. O movimento tem de manter o cursor real; teste: índice atrasado
    mais resgate parcial.

  **O bloco compara cada investimento com o CDI, e não mostra número da carteira inteira**
  (decisão do dono, 2026-09-25). No Open Finance o banco não diz quando o dinheiro entrou ou
  saiu, então não existe rentabilidade exata da carteira somada; um número aproximado com
  cara de exato é o que este plano proíbe. No Open Finance a taxa do banco é do mês e o
  período dela não é informado, então o mês de abertura e o de encerramento aparecem sem
  comparação, com o motivo. Investimento sem rentabilidade informada (o banco não mandou a
  taxa, renda variável, cripto) aparece sem a comparação, com o motivo. Como o patrimônio,
  nada de reconstruir o passado: enquanto o histórico enche, o bloco diz que se completa com
  o tempo. O widget do protótipo (`widgets/Yield.tsx`) mostra a carteira somada; ele passa a
  ser por investimento quando for ligado à API, na etapa do Resumo.

  **Testes do PR do job** — manuais: aporte e resgate rendendo antes e depois; movimento sem
  rendimento (dá 0); dois movimentos no mesmo dia; lote aberto e resgatado no mesmo dia
  (`pct_cdi: null`); resgate parcial e total de lote de IPCA antes de a taxa sair (a sombra
  para na data efetiva); resgate total; resgate total e novo
  aporte semanas depois (a sombra não rende no buraco); lote aberto e encerrado no meio do
  mês; investimento com um lote de CDI e outro de IPCA; IPCA publicado depois da virada
  (cai no mês que cobre); laço de juros parado por mais de uma virada de mês (cada mês
  recebe o seu); aporte com data no
  passado; investimento resgatado e depois apagado (continua no histórico). Open Finance:
  investimento sem taxa; mês de abertura e de encerramento (sem comparação); posição
  liquidada entre duas rodadas do job (a taxa da última sincronização fica no histórico).
- **Reserva em meses:** reserva dividida pelo custo mensal das contas fixas ativas. O custo
  mensal converte cada frequência de `db/recurring.py` (`VALID_FREQUENCIES`): diária × 365/12,
  semanal × 52/12, mensal × 1, anual ÷ 12; pagamento único (`once`) não entra. Conta de valor
  variável sem estimativa (guardada com valor 0) fica fora da soma, e o bloco diz quantas
  ficaram. A condição do vazio é o **total**, e ela se avalia **depois** de tirar o que não
  entra na soma: com total zero a API devolve `meses: null` com o motivo —
  `sem_contas_fixas` (nenhuma conta recorrente ativa; pagamento único não conta) ou
  `sem_valor` (há contas recorrentes, mas nenhuma com valor) —, nunca infinito nem erro, e
  o bloco pede o que falta. Testes de contrato e de tela: cada frequência, a conta variável
  sem estimativa, só pagamento único ativo (`sem_contas_fixas`), e os dois motivos do
  vazio. Hoje nada marca qual caixinha
  é a reserva: só há o palpite pelo nome em `core/services/piggy_agents.py` (`_is_reserva`).
  Por isso a caixinha de reserva passa a ser **designada pelo usuário** (um campo na
  caixinha, no máximo uma por usuário). Sem designação, o bloco pede para escolher, e o
  palpite pelo nome só sugere. Pela regra de uma fonte só (Q18), o agente que usa o palpite
  passa a ler o campo no mesmo PR (etapa 4, Metas).
- **Perfil e layout do Resumo:** perfil no servidor (coluna com `CHECK` nos ids
  `economizar|investir|controlar|dividas|autonomo|padrao`); layout segue no navegador.

## 3. O front

- **Busca de dados:** TanStack Query (cache, recarregar ao voltar para a aba, repetição,
  atualização otimista ao lançar) (Q24). O app novo pode usar a mesma biblioteca.
- **Bundle:** artefato commitado em `frontend/` com trava de rebuild no CI, como
  `precos-app.*` e `chat-app.*` (Q7). Os caminhos de asset passam a ser absolutos.
- **Plano real:** o gate de plano vem do `/auth/me` (hoje o protótipo lê `?plano=` da URL).
- **Um PR por tela** (Q8). Tela que só consome a API é faixa Leve; desde 2026-09-25 a
  faixa Leve vai **com o time, na versão leve** (decisão do dono que substituiu o
  experimento com × sem).
- **Testes (Q32):** pytest com Postgres real (isolamento e contrato por rota); Playwright
  com respostas simuladas a partir de exemplos gerados pelo contrato; ponta a ponta com
  servidor e banco de teste só nos fluxos críticos (abrir o Resumo, lançar um gasto, o aviso
  em tempo real chegar).

## 4. Ordem

**Antes de tudo (Q34):** terminar o protótipo do chat: o PR #584 (página do chat) e o PR 3
dos blocos que expandem na conversa (estado por resposta, "Abrir no painel"). A interface
fica; só as respostas prontas saem quando a IA real entrar.

| Etapa | O que entra | Faixa |
|---|---|---|
| 0 | Esqueleto da `/api/v2` (dependência de usuário, envelope de erro, contrato + tipos gerados, SSE), `/painel` servido com a chave e os links, plano real pelo `/auth/me`, cliente TanStack Query, job da foto diária do patrimônio e das posições de investimento | Completo |
| 1 | Resumo (perfil no servidor entra aqui) | API Completo, tela Leve |
| 2 | Lançamentos: ver, lançar, editar, apagar | idem |
| 3 | Previsão | idem |
| 4 | Metas e caixinhas, com depositar e retirar | idem |
| 5 | Para onde vai | idem |
| 6 | Patrimônio (com o histórico que o job da etapa 0 já vem gravando) | idem |
| 7 | Chat com IA real e blocos: o `/ai/chat` passa a devolver "texto + blocos" a partir das tools que usou | Completo |

**Depois:** migrar as demais telas uma a uma (as "em breve" de Ferramentas e as que abrem
no antigo), tema claro, abrir para mais gente, e o corte final em dois passos, porque o app
atual (Capacitor) carrega o `/app` ao vivo e nunca pode mostrar o v2:

1. `/app` passa a abrir o v2 no navegador; com o marcador `PigBankApp` no user agent ele
   continua servindo o painel antigo. Aqui o user agent escolhe *qual tela* (os dois são
   dados do próprio usuário), não concede acesso — segue valendo a regra de acima.
2. Apagar o `dashboard.html`, o `dashboard.js` e o `/ws` **só depois de o app atual sair
   de circulação**: o app novo publicado e uma versão mínima obrigatória que tire o
   Capacitor de uso. Até lá, o painel antigo e os assets dele ficam. Teste: user agent com
   `PigBankApp` abrindo `/app` recebe o painel antigo; sem ele, o v2.

## 5. Andamento

- [x] Protótipo: perfis do Resumo (#573, #575), faixa do Piggy (#579), navegação com o
  Piggy no meio e Ferramentas (#582).
- [x] Protótipo: página do chat (#584).
- [ ] Protótipo: blocos que expandem na conversa (em outro chat).
- [ ] Etapa 0 · [ ] 1 · [ ] 2 · [ ] 3 · [ ] 4 · [ ] 5 · [ ] 6 · [ ] 7
