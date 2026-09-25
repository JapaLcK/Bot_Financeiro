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
  abrindo `/painel` direto cai no `/app`; usuário da lista abre o v2.
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
  Um lançamento feito pelo WhatsApp atualiza o painel aberto. O aviso não se guarda: se a
  conexão cair, o que mudou nesse meio-tempo se perde. Por isso, **a cada (re)conexão** o
  cliente invalida todas as consultas e a tela pede tudo de novo — cobre a queda, a volta do
  sono do computador e a janela entre a primeira carga e a conexão abrir. A sessão expira em
  15 minutos e só o `fetch` sabe renová-la (`frontend/static/auth-refresh.js`); o
  `EventSource` que recebe 401 fecha de vez. Então, quando a conexão fecha, o cliente faz uma
  chamada leve pela API (o `fetch` renova a sessão se precisar) e recria o stream; se a
  sessão acabou de fato, segue o caminho normal de sessão encerrada. O teste de ponta a ponta
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
    refaz tudo pelo caminho normal. Teste separado: derrubar só a conexão de `LISTEN`,
    lançar, e ver a tela atualizar. Teste: uma escrita com o commit atrasado de
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
    mandar continua na série dos meses em que existiu, marcada como encerrada.
  - **Manuais:** o aporte e o resgate passam pelo nosso código
    (`investment_deposit_from_account` e `investment_withdraw_to_account`, em
    `db/investments.py`, e todo outro caminho que mexa no principal — o inventário por
    `grep` é o primeiro passo do PR do job). O job grava uma foto por dia e mais uma antes
    e outra depois de cada movimento, no mesmo commit dele, marcadas como o par do
    movimento. O rendimento de cada intervalo entre fotos é a variação do valor sobre o
    valor do início, **exceto o par antes→depois de um movimento**, que é o próprio dinheiro
    entrando ou saindo e fica fora; o intervalo seguinte começa da foto de depois. O do mês
    é o encadeamento dos intervalos (rentabilidade ponderada pelo tempo, a mesma régua do
    CDI). Como todo movimento cai entre duas fotos, a conta é exata. Criar investimento e aportar aceitam
    data no passado (`purchase_date` em `create_investment_db`, e o lote nasce com ela), e o
    juro desse passado só entra quando os juros forem atualizados. Por isso a foto de depois
    é tirada **com o investimento já em dia** (juros calculados até hoje) — ela é a base, e
    rendimento de antes da primeira foto nunca entra na conta, como manda a regra de não
    reconstruir o passado.

  **O bloco compara cada investimento com o CDI, e não mostra número da carteira inteira**
  (decisão do dono, 2026-09-25). No Open Finance o banco não diz quando o dinheiro entrou ou
  saiu, então não existe rentabilidade exata da carteira somada; um número aproximado com
  cara de exato é o que este plano proíbe. Investimento sem rentabilidade informada (o banco
  não mandou a taxa, renda variável, cripto) aparece sem a comparação, com o motivo. Como o
  patrimônio, nada de reconstruir o passado: enquanto o histórico enche, o bloco diz que se
  completa com o tempo. Testes do PR do job: aporte e resgate nos manuais, rendendo antes e
  depois do movimento (exato); movimento sem rendimento nenhum (dá 0%); investimento e aporte com data no passado (o juro antigo
  não entra); dois movimentos no mesmo dia; resgate total; investimento do
  Open Finance sem taxa (aparece sem comparação); posição do Open Finance liquidada entre
  duas rodadas do job (a taxa da última sincronização fica no histórico).
  O widget do protótipo (`widgets/Yield.tsx`) mostra a carteira somada; ele passa a ser por
  investimento quando for ligado à API, na etapa do Resumo.
- **Reserva em meses:** reserva dividida pelo custo mensal das contas fixas ativas. O custo
  mensal converte cada frequência de `db/recurring.py` (`VALID_FREQUENCIES`): diária × 365/12,
  semanal × 52/12, mensal × 1, anual ÷ 12; pagamento único (`once`) não entra. Conta de valor
  variável sem estimativa (guardada com valor 0) fica fora da soma, e o bloco diz quantas
  ficaram. A condição do vazio é o **total**, não a existência de conta: com total zero a
  API devolve `meses: null` com o motivo — `sem_contas_fixas` (nenhuma ativa) ou
  `sem_valor` (há contas, mas nenhuma com valor) —, nunca infinito nem erro, e o bloco pede
  o que falta. Testes de contrato e de tela: cada frequência, a conta variável sem
  estimativa, e os dois motivos do vazio. Hoje nada marca qual caixinha
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
