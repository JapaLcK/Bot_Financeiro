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
  navegador pelo `/auth/me` (Q11).
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
  dado; as do `ai_chat` têm também o teste da cota esgotada. O gate pelo `/auth/me` na tela (seção 3) só decide o que mostrar; quem protege é o
  servidor.
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
  uma segunda instância, a função usa `LISTEN/NOTIFY` do Postgres desde a etapa 0. O `/ws` antigo sai junto com o
  dashboard antigo.
- **Processo (Q21):** todo PR que cria ou muda endpoint da `/api/v2` é **faixa Completo**,
  com o time inteiro, os testes de isolamento e o Codex.

### Dados que não existem hoje (Q14, Q35)

- **Patrimônio em 12 meses:** um job diário grava uma "foto" do patrimônio de cada usuário.
  Ele entra **na etapa 0**, antes de qualquer tela, para o histórico começar a encher o quanto
  antes; enquanto enche, o gráfico diz que se completa com o tempo. Nada de reconstruir o
  passado (mostraria número errado com cara de certo).
- **Rendimento × CDI:** a série do CDI já existe (`db/investments.py`), mas o lado da
  carteira não tem histórico: `investments`, `investment_lots` e `open_finance_investments`
  guardam só o saldo atual, sobrescrito a cada juro ou sincronização, e sem histórico não dá
  para separar rendimento de aporte e resgate. Por isso **o job da etapa 0 grava também, por
  posição, o valor e o rendimento acumulado** — nos do Open Finance, o `amountProfit` que o
  banco manda; nos manuais, calculado dos lotes, contando também o que já saiu em resgate.
  O rendimento de cada dia é a variação do acumulado sobre o valor do início do dia, e o do
  mês é o encadeamento dos dias (rentabilidade ponderada pelo tempo, a mesma régua do CDI).
  Como a foto é diária, aporte e resgate mudam o valor da base no dia seguinte, e dinheiro
  novo não infla nem dilui o percentual. A fórmula exata do acumulado dos manuais se fecha no
  PR do job, com teste de aporte grande e de resgate no meio do mês: o percentual tem de sair
  igual ao do caso sem movimento. Posição sem rendimento informado (renda variável, cripto sem
  `amountProfit`) fica fora da conta, e o bloco diz quais ficaram. Como o patrimônio, nada
  de reconstruir o passado: enquanto o histórico enche, o bloco diz que se completa com o
  tempo.
- **Reserva em meses:** reserva dividida pelas contas fixas. Hoje nada marca qual caixinha
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
no antigo), tema claro, abrir para mais gente, corte final (`/app` abre o v2) e apagar o
dashboard antigo e o `/ws`.

## 5. Andamento

- [x] Protótipo: perfis do Resumo (#573, #575), faixa do Piggy (#579), navegação com o
  Piggy no meio e Ferramentas (#582).
- [x] Protótipo: página do chat (#584).
- [ ] Protótipo: blocos que expandem na conversa (em outro chat).
- [ ] Etapa 0 · [ ] 1 · [ ] 2 · [ ] 3 · [ ] 4 · [ ] 5 · [ ] 6 · [ ] 7
