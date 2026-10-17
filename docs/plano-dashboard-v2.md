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
  **Revista em 2026-10-09: ver [ADR 0006](adr/0006-declaracao-bancaria-despesa-receita.md)**
  (o PR-A dela, #969, mergeado em 2026-10-15, não toca a Q36; o código ainda vale como está até o PR-B1 e o PR-C). O texto acima fica como
  histórico. A Q36 deixará de ser trava de escritor para investimento (o manual poderá
  existir e ser vinculado à posição do banco, PR-C) e para compra manual em cartão **não**
  sincronizado (PR-B1). Continua valendo para importação de extrato (OFX/CSV/PDF), fatura OFX e
  compra manual em cartão sincronizado pelo Open Finance, porque não há dedupe do OFX contra a
  sombra do banco. **Coorte:** a Q36 vale hoje só para quem tem a chave do v2
  (`core/services/fonte_unica.py`); quem está fora dela e tem banco conectado, sendo Pro, já pode
  importar OFX. O dono decidiu em 2026-10-13 **preservar esse comportamento neste pacote**; qualquer
  novo bloqueio de arquivo exige decisão separada.
  **Trava #498** (decisão de 2026-09-18, PR #498 mergeado em 2026-09-19, “Lançamentos manuais
  exclusivos para dinheiro”): filtro do painel em `frontend/finance_bot_websocket_custom.py:7690-7694`,
  rebaixamento auto→ask em `db/open_finance.py:2108-2128` e pendência na ordem inversa em
  `db/open_finance.py:2180`. A ADR 0006 a revê no PR-B.
- **Q37 — o que já existe de manual fica só para leitura**, como "registro manual antigo",
  com convite para conectar o banco. Na primeira vez no v2, o usuário confirma quanto da
  carteira é dinheiro vivo e revisa o que o sistema lançou sozinho no passado, para o
  antigo não contar duas vezes com o que vem do banco.
  **Revisão do dono (2026-10-05), vigente para a coorte de lançamento:** quem terá acesso
  ao dashboard v2 é novo, nunca usou/conectou o PigBank e não tem histórico para revisar.
  Nessa coorte, Q37 não terá etapa, diálogo de confirmação nem gate antes da Previsão;
  carteira nova parte de zero (padrão existente), com movimentos em dinheiro normalmente.
  Isso substitui nesse escopo a decisão de 03/10 registrada no §8. Se houver problema,
  a recuperação definida é Recomeçar do zero voluntário → reconectar banco → novo sync.
  Não é reset automático, autorização para apagar usuários ou presunção de que dados
  antigos fora dessa coorte devem ser zerados. A allowlist atual não comprova ausência de
  histórico; o ajuste técnico do motivo hardcoded da carteira será tratado na regra
  compartilhada da Etapa 3, preservando outras fontes de incerteza.
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
  **Revista em 2026-10-09: ver [ADR 0006](adr/0006-declaracao-bancaria-despesa-receita.md)**
  (o PR-A dela, #969, foi mergeado em 2026-10-15 e é inerte; o código ainda recusa “banco” até o PR-B). O texto acima fica como histórico.
  A pergunta “dinheiro vivo ou banco?” continua. “Banco” passará a **gravar** como declaração,
  conferida com o extrato: o PR-B entrega esse caminho atrás de uma flag que nasce desligada, e
  ligá-la é um ato operacional separado. A conciliação (PR-A) e o lembrete de 7 dias (PR-A2; “foi em dinheiro” só age por ação e confirmação do usuário, e o PR-A2 inclui “trocar o par” e “desfazer a confirmação”, pela decisão do dono de 2026-10-13) vêm antes (ordem decidida pelo dono); o PR-A2 é pré-condição para ligar a flag, por decisão do dono. **A Q40 tem coorte
  própria** (quem tem Open Finance conectado, `regra_ativa`), diferente da Q36 (chave do v2).
- **Q41 — saque e depósito em espécie** que o Open Finance trouxer mexem na carteira
  sozinhos, com um aviso que o usuário pode desfazer. Dinheiro mudando de lugar não é gasto
  nem receita.
- **Q42 — recorrente só prevê**, para todo mundo: alimenta a Previsão e o aviso de
  vencimento, e deixa de lançar sozinha na carteira (também no painel antigo), antes da
  etapa 0.
  **Revista em 2026-10-09: ver [ADR 0006](adr/0006-declaracao-bancaria-despesa-receita.md)** —
  sem mudança. O cobrador não é restaurado; a Previsão lê as recorrências de despesa do banco (#866), a receita detectada pelo extrato
  (#981, só com o documento do pagador) e o fixo manual.
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
  **Revisão de 2026-10-05 para a coorte nova acima:** confirmação Q37 deixa de ser requisito
  de exatidão por si só; carteira inicial zero e os movimentos registrados seguem a regra
  normal. A flag `carteira_nao_confirmada=True` ainda existe no código e precisa ser
  alinhada nesse escopo, com a mesma regra para contas/foto/previsão. Q41, frescor, moeda,
  conciliação, pendências e completude continuam determinando a confiabilidade da foto;
  não tornar usuários antigos ou outras fontes incertas exatos pela nova decisão.
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
- **Um PR por tela** (Q8); tela que só consome a API e **não calcula dinheiro no cliente** é faixa Leve (regra no §4 do plano do app), com o time na versão
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
(seção 1). Entre as demais telas, a de **categorias** (criar, renomear, trocar emoji e cor,
arquivar e apagar), hoje só no painel antigo: a Etapa 5 só lê emoji e cor, e até essa tela
existir o editar abre o painel antigo com o aviso discreto (Q1/Q15). Decisão do dono,
2026-10-13; detalhes em `docs/plano-etapa5-gastos.md`.

### Paridade com o app

Regra do dono, 2026-10-16: o `/painel` e o app nativo **andam juntos, nos dois sentidos**, com as mesmas características e
funções, adaptadas à plataforma. A matriz completa (cada função e tela, rota, profundidade, veredito) mora em
**`docs/plano-app-paridade.md`** (uma fonte só, §0.7); aqui ficam o resumo do que **este lado** deve, a ordem e a regra.
Medido em 2026-10-16 na `main` `b7c8232d`, por leitura estática e grep; os comandos para remedir estão no §2 daquele
documento. O plano do card "Seu dia" no app é `docs/plano-seu-dia-app.md` (PR #1050) e não é repetido.

**O que o web ainda deve ao app** (o app já faz, o web mostra dado de exemplo ou não tem):

| ID | O que falta no `/painel` | O que o servidor pede | Faixa | Esforço |
|---|---|---|---|---|
| W1 | **Patrimônio, "Onde está o dinheiro" e Rendimento reais** (a Etapa 6): hoje `NetWorth`, `Wealth` e `Yield` leem `lib/api.ts` sem rede e saem como exemplo; a janela do histórico é a do plano (`history_earliest_date`: mês corrente, 90, 365 ou 730 dias), não um valor fixo, e o critério de aceite tem de valer por plano. **"Rendimento × CDI" (`Yield.tsx`) não fica real só com a rota `rendimento`**: ela devolve a taxa contratada e a hora da observação, não o valor e a taxa realizados da carteira nem o CDI do mês e de 12 meses. O widget passa a mostrar a taxa contratada, como o app, e a comparação com o CDI **sai** (dado de exemplo) até haver contrato de servidor, que só entra se o dono pedir | `patrimonio` e `rendimento` só existem em `api/nativo/`: montar também no namespace v2, como o #1033 faz com `comparacao` (`api/v2/app.py`); regenerar `api-v2.gen.ts` | Completo (dinheiro) | M |
| W2 | **Chat real** (a Etapa 7): `CHAT_REAL = false`, só o assunto `investido` é real | `/ai/chat/stream` já existe e o app o consome, **mas o servidor não serve cards ao `/painel` como está**: `core/services/ai_chat/cards.py` `montar` chama `exigir_acesso_do_app`, que exige o onboarding do app (`exigir_onboarding`, `open_finance_onboarding_completed_at`), e o `/painel` e o `/api/v2` não exigem esse marcador; W2 precisa de uma mudança de servidor que aplique o gate do chamador (web/v2 ou app) a `montar` e à elegibilidade dos cards. **O chamador não pode vir do corpo nem de um campo que o cliente manda**: o corpo de `POST /ai/chat/stream` tem só `message` e nada diz de onde veio. O jeito recomendado é uma **rota de stream própria no `/api/v2`**, com o seu portão (sem `exigir_onboarding`), reaproveitando o núcleo do stream, enquanto `/ai/chat/stream` e o namespace do app seguem com `exigir_acesso_do_app`; trocar o gate de `montar` para todos tiraria o onboarding do app. Um teste mostra os dois caminhos: o do app recusa quem não concluiu o onboarding e o do `/painel` serve o card; **a interação do app é o alvo**: parar a resposta, copiar resposta, avaliar 👍👎 (`/ai/messages`) e cancelar a pendência (`DELETE /ai/pending`); **os cards do chat**: decodificar `show_card` e ter um renderizador por id que o servidor emite (os do catálogo, `periodo` e `caixinhas`; os dois últimos não têm widget web), **com todas as abas** (comparar 2 ou 3 meses vem como um bloco com várias `abas`, que o app mostra com um seletor); **a cota mensal de IA**: ler `usage` de `GET /ai/messages?limit=1`, mostrar o contador e travar o envio quando `used >= limit`. **No teto, hoje essa leitura e o 👍👎 tomam 403**: `GET /ai/messages` (`finance_bot_websocket_custom.py:7287`) e `POST /ai/messages/{id}/feedback` usam `require_pro_feature("ai_chat")`, que chega a `ai_chat_allowed()` e recusa com `used >= limit`; o cliente não consegue ler o contador nem o histórico nem avaliar a última resposta (o app tem o mesmo problema hoje). A mudança de servidor do W2 troca esse portão nas duas rotas de leitura e de avaliação por um que **não depende da cota**, e **mantém a cota só no início de um turno** (`/ai/chat/stream`), com teste no teto; **o alvo do W2 é o conjunto do chat nativo (`app/src/features/painel/conversa*.tsx`), não esta lista**, que o Codex já ampliou várias vezes; o chat de exemplo do web (`parts/PiggyChat.tsx`) só tem os chips de perguntas | Completo | G |
| W3 | **Simulador real**: `widgets/Simulator.tsx` e a página são de exemplo; o do app calcula no cliente sobre gastos observados. O web de exemplo tem mais controles que o app: **novo gasto fixo**, economia em 12 meses, saldo em 90 dias e data da meta que recebe a economia (`Simulator.tsx:76-119`), mais a página `/simulador` com a trajetória de 90 dias e as metas afetadas (`pages.tsx`), e o app só corta categorias e soma a economia (`gastos.tsx`). A **faixa provável** do exemplo sai: `/previsao` traz um `saldo` por ponto e nenhum limite, e só volta com contrato novo de servidor. **O saldo em 90 dias, a trajetória, a data da meta e as metas afetadas também saem do W3**: o exemplo os calcula com um ritmo diário de 60 dias inventado (`lib/model.js::trajectory`), e a linha de base real de `/api/v2/previsao` **não inclui gasto variável** (`cobertura.inclui_estimativa_variavel` é falso; é a pergunta aberta da Previsão), e `/gastos` é gasto passado, não débito futuro. Somar a economia de uma categoria a essa base subiria o saldo por um gasto que nunca foi descontado e adiantaria a meta. Ficam o corte por categoria e a economia por mês e em 12 meses, contas sobre o gasto observado, como no app, e o novo gasto fixo como custo mensal e anual. Os resultados de saldo e meta só voltam com um contrato de simulação que defina o gasto variável e o horizonte (Completo, dinheiro), decidido junto da estimativa variável da Previsão. A **A17** só traz ao app o que ficar | nenhum para o que fica (lê `/gastos`); contrato novo de servidor, Completo, para saldo, trajetória e metas afetadas, **só se o dono pedir** | **Completo** (dinheiro: calcula economia e custo fixo, e a área decide antes do tamanho, CLAUDE.md §0) | M |
| W4 | **Fatura, Parcelas futuras, Renda mês a mês e "Piggy notou" reais**: o app já os lê, mas pelo legado (`/cards/{uid}/summary`, `/installments/{uid}/list`, `/analytics/{uid}/evolution`, `/insights/{uid}/current`) | contrato novo em `/api/v2`, montado nos dois namespaces. **A Fatura leva a união dos dois lados**: a lista das compras que entram na fatura (hoje só o web, `Invoice.tsx`) e, por cartão, dia de fechamento, vencimento, limite usado e valor já pago da fatura aberta (hoje só o app, `Faturas`); cada cliente passa a mostrar os dois (o do app, junto da migração dos quatro recursos do legado, é a **A21**). Depende das fontes do plano "só Open Finance" (a fatura manual saiu) | Completo | G |
| W5 | **Saldo do mês e Guardado líquido reais** no Resumo do mês (hoje só Entrou e Saiu são reais; "Guardado no mês" saiu por ser inventado) | `mes-detalhes` só no app: montar no v2, **com o campo aditivo `saldo` (entrou menos saiu) calculado no servidor**: a rota só devolve `entrou` e `saiu`, e o app calcula a subtração em `numerosMes.tsx`, o que seria dinheiro no cliente (regra do §4 do plano do app) | Completo (rota), Leve (tela, que só exibe o `saldo`) | P |
| W6 | **Modo privacidade** (esconder valores): o app o tem em todo lugar e o painel antigo também (`toggleHideBalance`); o `/painel` não. **O alvo é o `oculto` do app em todo lugar, inclusive o chat**: no app o turno inteiro some (a pergunta e a resposta, `conversaFalas.tsx`), o envio fica bloqueado (`conversaEstado.tsx`) e o campo fica desabilitado e limpo (`conversa.tsx`); quando o W2 ligar o chat real, o W6 tem de cobrir isso, senão o modo privacidade deixaria valores no texto e nos cards | nenhum | Leve | P |
| W7 | Compromissos do **Resumo** (`compromissos: Bills`) ainda de exemplo, embora a versão real exista na página `/previsao` (`Compromissos.tsx`) | nenhum | Leve | P |
| W8 | **Conta no shell do `/painel`**: links para Configurações (`/settings`) e Suporte (`/suporte`) e o **Sair**; hoje o shell só monta o bundle e não tem nenhum dos três (matriz do plano do app, linha 50) | nenhum para os links; o Sair reutiliza `/auth/logout` e a limpeza central do `auth-refresh` | links **Leve**, Sair **Completo** (sessão) | P |
| W9 | **Tema claro** (e seguir a preferência do sistema): o `/painel` fixa `color-scheme: dark` (`webapp/src/dashboard/styles/base.css`); o app escolhe a paleta clara ou escura pelo sistema (`app/app/_layout.tsx`). Já está em "Depois" (§6, "tema claro"); entra aqui só para a paridade não ser dada por completa com o web escuro | tokens de cor claros em `base.css` (`color-scheme: dark` e `--bg: #09090b`), revisão de cada bloco **e o shell `frontend/painel.html`**: `<meta name="color-scheme" content="dark">`, `<meta name="theme-color" content="#09090b">` e o `html { background: #09090b; }` inline (medido em 2026-10-16 por `git grep` nesses dois lugares); nenhum servidor | Leve | G |
| W10 | **Layout da Home persistente**: `lib/profiles.js` guarda perfil e ordem no `localStorage` por perfil, sem `user_id`, e o `/painel` carrega `auth-refresh.js`, cuja limpeza de fim de sessão (`_limpaEstadoDoDispositivo`, `frontend/static/auth-refresh.js`) apaga todo `localStorage` fora de `_PRESERVA`, e as chaves `pigbank.dashboard.layout.*` não estão lá. Então, no logout e no fim de sessão por refresh, **duas contas normalmente não herdam a ordem** (o vazamento só ocorreria se a limpeza não rodasse, por exemplo fetch que rejeita ou cookie trocado sem passar por ela), **mas cada logout apaga a ordem de quem a personalizou**; o app guarda ordem e perfil por `uid` e a mantém. A diferença real é de **persistência**. Caminhos: (a) **layout no servidor**, junto de `/perfil` (campo novo e opcional, por usuário **e por perfil**, com migração por perfil, como os dois clientes já guardam; **recomendo**: resolve a perda no logout e o isolamento de uma vez, e vale em outro aparelho); (b) chave com `user_id` ou id opaco dentro de `_PRESERVA`, o que exige um id de conta que o bundle não tem (`/api/v2/me` devolve só `plan_tier`, "Sem PII e sem id") e mexer na limpeza central de logout (cuidado: ela existe para não deixar resíduo privado). Na opção (a) o app **também** passa a ler e gravar o layout pelo servidor (**A22**), senão a ordem divergiria entre o web e o app do mesmo usuário. **Os dois escritores preservam todo id que não mostram, desconhecido ou escondido pelo plano** (o web hoje filtra por `KNOWN` (`parts/Board.tsx`) e esconde o `metas` do app; ao salvar, tem de devolver ao servidor o que não reconhece, e o mesmo vale para o bloco que o plano esconde, que `Board.tsx` hoje apagaria ao gravar só os `ids` filtrados), ou o servidor faz o merge, com teste nos dois sentidos. **Toda gravação do layout é condicional, não só a primeira**: o servidor guarda uma versão por perfil e a gravação vale só sobre a versão que o cliente leu, com **merge atômico no servidor contra o layout mais recente** (ou CAS e nova tentativa), para que um salvar a partir de um retrato velho não desfaça a posição que o outro cliente acabou de mudar (por exemplo o `metas` do app); teste de duas gravações concorrentes; na (b) o app mantém o SecureStore e a ordem fica local nos dois. Decidir com o Arquiteto antes de codar (matriz do plano do app, linha 1) | (a): campo novo em `/perfil`; (b): campo opaco em `/me` e ajuste em `auth-refresh.js` | **Completo** (dado por usuário e limpeza de logout) | P |
| W11 | **Reconectar banco e desconectar um banco só** no `/settings?view=open-finance`: hoje o widget conecta (sempre com `connect-token` de corpo vazio) mas não reconecta (o servidor já aceita `item_id` e a Pluggy recusa o banco repetido sem ele), e só há "desconectar todos" (matriz do plano do app, linha 32; o app já tem os dois) | nenhum: `connect-token` já aceita `item_id` e `DELETE /open-finance/{user_id}/connections/{id}` existe | **Completo** (Open Finance) | M |
| W12 | **Seletor de mês até o início do histórico do plano**: `MESES` fixa os 6 últimos meses; o app enumera até `history_earliest_date`. O `/api/v2/me` não traz o campo; `GET /auth/me` (legado) traz (`frontend/finance_bot_websocket_custom.py`, campo `history_earliest_date`). Caminho: acrescentar o campo em `/api/v2/me` (**só acréscimo**, opcional) e gerar o seletor a partir dele. **Mês do corte:** o servidor já aplica o corte do plano em `resumo-do-mes` (`db/resumo_mes.py`: soma de `max(inicio, corte)`, `anterior` suprimido e motivo `inicio_do_historico` quando a janela corta o mês), e o web já tem o selo `inicio_do_historico` (`parts/Selos.tsx`); então o W12 só mostra o mês do corte com os totais parciais e o selo que o servidor manda, sem mudança de servidor além do campo do seletor | `history_earliest_date` em `/api/v2/me` (campo novo, opcional) | **Completo** (campo novo em `/api/v2/me`, Q21), Leve (tela) | P |
| W13 | **Espelho de plano por bloco incompleto**: `WIDGET_FEATURE` em `lib/profiles.js` mapeia só `hero`, `piggy` e `simulador`; `assinaturas` (`subscriptions`), `compromissos` (`cashflow`, **Pro**) e `renda` (`financial_comparison`, rota de evolução, Plus), ficam no layout do Essencial, e `tests/frontend/dashboard_v2_profiles.test.mjs` compara só o que está no mapa. O que cada um mostra hoje ao Essencial difere: `assinaturas` é real e mostra "Ver planos" dentro do bloco (a rota devolve 403); `compromissos` e `renda` no layout são **de exemplo e sem portão** (`Bills` e `Income` leem `lib/api` sem rede, W7 e W4), então mostram números inventados a quem não tem o plano. Por isso `compromissos` (`cashflow`, Pro: `/previsao` devolve `compromissos: null` sem ele, `api/v2/previsao.py:140`) e `renda` (`financial_comparison`) **entram no filtro já no W13**, antes de o W7 e o W4 os tornarem reais; só depois disso o estado dentro do bloco pode substituir o filtro, e só se for deliberado. De qualquer modo o campo de blocos liberados do servidor (A19 do plano do app) substitui o espelho | campo aditivo de blocos liberados em `/me` | **Completo** (campo novo em `/me`, Q21), Leve (tela) | P |
| W14 | **Contas do Resumo com frescor e composição**: mostrar por conta a hora da última sincronização (`sincronizado_em`, já em `GET /contas`) a barra de composição do saldo e o **detalhe por conta** (tocar numa conta do banco abre os lançamentos do mês dela, `conta=<id>`, via `LancamentosDe` no app; as linhas do web são inertes, embora o filtro por conta já exista na página de lançamentos) que o app tem (matriz do plano do app, linha 2) | nenhum | **Completo** (a barra de composição filtra saldos, suprime total incerto, soma os segmentos e exige que batam com o total: conta de dinheiro no cliente, ver a regra no §4 do plano do app) | P |

**Alvo do W4, por bloco (a união do que o web e o app mostram hoje).** Medido lendo `widgets/Invoice.tsx`, `Installments.tsx`, `Income.tsx`, `Piggy.tsx` e `complementos.tsx` (`Faturas`, `Parcelas`, `Renda`, `Notou`). O contrato v2 traz tudo o que está aqui, e os **dois** clientes mostram tudo, salvo o marcado como "fora":

- **Fatura**: lista das compras que entram na fatura (só o web hoje); por cartão, fechamento, vencimento, limite, usado, valor já pago da aberta (só o app hoje) e **livre e porcentagem usada, calculados no servidor** para os clientes só exibirem. **Qual fatura é essa**: o resumo de cada cartão usa `db.cartoes_resumo.resumo_cartoes` com `fatura_a_pagar`, que devolve a fatura do período **anterior** enquanto estiver em aberto, mesmo com uma do período atual já correndo; por isso o contrato traz, por cartão, o **id e o período da fatura resumida** e a **lista de compras amarrada a esse mesmo id e período**, e os clientes nunca juntam a lista de um período com o total de outro. **Limite ausente ou não positivo**: `livre` e a porcentagem usada são **nulos** (`credit_available` já é nulo sem limite; limite zero deixaria a porcentagem indefinida), a tela mostra um estado "indisponível" em vez de zero inventado ou divisão por zero, e há teste de contrato com o limite nulo e com o zero. Faturas fechadas (o web navega por mês): o plano do W4 decide se o contrato as traz; se não trouxer, o web mostra só a aberta.
- **Parcelas**: o resumo (valor já preso em cada uma das próximas 6 faturas e o mês da última parcela, só no web) **e** a lista por parcelamento com a situação de cada parcela e o progresso (só no app). O resumo sai do servidor, não é somado em cada cliente.
- **Renda**: entradas e saídas por mês (só o app mostra as saídas); média de 6 meses e pior mês (só o web hoje), **calculados no servidor e devolvidos no contrato**, para os dois clientes só exibirem e não haver conta de dinheiro repetida em dois clientes; o plano do W4 define a janela da média e do pior mês: **os 6 meses já encerrados, sem o mês corrente**, que ainda está em curso (antes de o salário cair é um balde parcial ou zerado, não comparável aos cinco fechados, e baixaria a média e viraria o "pior mês"); o mês corrente aparece à parte, como o mês em andamento; os meses **montados com o fuso do produto (Brasília), não com `date.today()` do servidor em UTC**: `db/analytics.py::compute_evolution` hoje cria, nas últimas 3 h do mês em Brasília, um balde futuro zerado que derrubaria o mês mais antigo, baixaria a média e viraria o "pior mês" (o `complementos.tsx` já documenta o defeito); teste do limite de mês, com relógio congelado dentro dessa janela. **Fora até haver definição**: "a reserva cobre N meses de gasto fixo", porque exige dizer qual meta é a reserva e o que é gasto fixo; a linha sai do web (dado de exemplo) e só volta com essa definição, para os dois lados.
- **Piggy notou**: a fonte é `insights/current` do servidor (`key`, `title`, `message`, `icon`, `severity`, `action_label`, `action_view`; `core/ai_patterns.py::_sanitize_insight_items` e `db/insights.py`). O contrato carrega os sete campos, **incluindo `key`**, que o schema do app exige e `Notou` usa só como chave de lista, por isso **o contrato garante `key` única dentro de cada resposta** (o servidor deduplica ou sintetiza uma chave local à resposta, também quando duas colidem depois do corte de 120 caracteres, sem prometer estabilidade entre gerações), com teste de chave duplicada; **ela não é identidade estável para dispensar**: quando a IA omite o `key`, `_sanitize_insight_items` põe `ai-{idx}`, posicional, e não rejeita duplicata, então o W4 **não** promete "dispensar insight" (o legado guarda a dispensa por 24 h pela `key`); se o dono pedir, o servidor tem de derivar e deduplicar uma chave determinística do insight, e essa regra entra então no contrato, e os dois clientes mostram o `icon` (emoji do servidor) e o tom pela `severity` (`info`, `warning`, `critical`). O `action_view` que chega aos clientes tem os seis valores de `_ALLOWED_VIEWS`: `_sanitize_insight_items` roda também sobre o fallback heurístico (`generate_ai_insights`) e **zera `action_view` e `action_label` do `history`** que `db/insights.py` emite ("Ver na timeline"), então o `history` não chega e **não é prometido** aqui. Destino de cada valor, igual nos dois clientes quando existe: **`goals` e `pockets` sem botão**: as duas ações nascem sobre as caixinhas legadas, `db/insights.py::_detect_goals_behind` (`list_pockets`) no caminho heurístico e, no da IA, `_collect_insights_data` → `_compact_goals_status`, que replica `/goals/{uid}/status` e também lê `list_pockets`; são outro modelo e outros ids que o das Metas v2 (A18), então a tela de Metas não mostra o item que o insight cita. O botão só volta quando o gerador de insights migrar para o modelo de metas v2 ou houver tela que mostre a caixinha; `analytics` para Gastos ("Para onde vai"); `recurring` para Assinaturas e `fixed` para Compromissos **no web, e só para quem tem `cashflow` (Pro)**: o insight é do Plus, mas `/previsao` devolve `compromissos: null` sem esse portão (`api/v2/previsao.py:140`), então no Plus o botão não aparece (o cliente lê o plano canônico, não adivinha); (`/assinaturas` e `/previsao` são páginas próprias); **no app, os dois ficam sem botão**, porque o app só tem Resumo, Gastos, Piggy, Metas e Extrato como destinos (`(painel)/_layout.tsx`) e Assinaturas e Compromissos são blocos que existem só se estiverem no layout do usuário; o botão chega ao app quando houver destino autônomo, dívida declarada em §9 do plano do app; **`budgets` não tem destino em nenhum dos dois** (orçamento só existe no painel antigo), então o botão não aparece, como num valor desconhecido. **Fora**: só `ask`/`topic` (a pergunta que vai para o chat), que existem nos insights de exemplo do web.

Cada item acima que o app ainda não tem entra na **A21** (`docs/plano-app-paridade.md`), e cada um que o web ainda não tem entra no W4.

**Layout da Home no servidor (W10 opção (a) e A22): a máquina de estados inteira, num lugar só.** Fechada de uma vez porque várias rodadas seguidas bateram nesse subsistema (CLAUDE.md §4). O servidor guarda, **por usuário e por perfil**, um registro `reivindicado` com o layout e uma `versão`. O cliente guarda um cache local (SecureStore ou `localStorage`) que **só se lê**.

| Situação | Regra |
|---|---|
| Leitura, servidor sem registro, cache local **personalizado** (definição abaixo) | sobe o layout com gravação "só se ainda não houver registro"; quem ganha a corrida vale, quem perde adota o do servidor |
| Leitura, servidor sem registro, cache **vazio ou padrão** (por exemplo depois do logout do web) | **não grava nada**: o perfil continua sem registro, e um aparelho com layout personalizado que ler depois é quem o reivindica; um padrão nunca ganha a migração |
| Leitura, servidor com registro | o servidor vale; o cache local é reescrito com ele |
| Salvar (online) | grava sobre a `versão` que o cliente leu; **no primeiro salvar de um perfil sem registro a `versão` esperada é `0`** ("esperado ausente"), e a gravação é **criar se ausente**: de dois aparelhos que organizam o mesmo perfil sem registro ao mesmo tempo, um cria e o outro cai no conflito de versão e remescla; o cliente só **confirma na tela depois do ack**; conflito de versão faz reler, mesclar contra o layout mais recente e tentar de novo uma vez |
| Salvar que falha com **resposta definitiva** (4xx, recusa do servidor) | **volta ao último layout confirmado**, avisa que não salvou e deixa o salvar repetível; nada fica só no cache |
| Salvar com **resultado ambíguo** (a rede cai ou dá 5xx e o cliente não sabe se o servidor gravou) | **não volta nem avisa de falha antes de ter um resultado definitivo**; **recibo ausente não prova que não gravou**, porque a gravação original pode estar ainda em execução e confirmar logo depois: a consulta do recibo é **sincronizada com o escritor** (espera a transação em voo daquele token terminar ou bloqueia o token) ou o cliente **repete o mesmo token até receber um desfecho definitivo**, e só então decide; o salvar leva um `token` por tentativa e o servidor guarda um **recibo durável por token**, **único e consultado sempre por `(user_id, perfil, token)`** (na gravação, na repetição e na limpeza, com `WHERE user_id = %s`: o token é escolhido pelo cliente e **nunca** pode cruzar usuário ou perfil, regra de isolamento do CLAUDE.md §0), em tabela própria e não no registro corrente (o registro só tem o último), com o resultado, a `versão` que a gravação produziu e um **resumo do conteúdo enviado, com todo campo que muda o merge (`base`, `layout` e `removidos`)**, **por 24 h**; **o mesmo token com outro conteúdo é recusado (422)**, como na A4, e o cliente só reutiliza um token para o **mesmo** conteúdo (inclusive a mesma `base`: se a `base` muda por conflito ou recarga, é tentativa nova e leva token novo): uma edição nova depois de um salvar ambíguo espera a reconciliação e sai com **token novo**; o cliente só repete o mesmo token por **no máximo 10 min** desde a tentativa, **bem dentro da vida do recibo**, e passado esse prazo **descarta o token**: relê o servidor, adota o layout mais recente e não reaplica a edição (o usuário refaz, se quiser), de modo que um token nunca é repetido depois de o recibo poder ter expirado. O cliente consulta o recibo pelo token: se existe, a gravação valeu, e o cliente **adota o layout mais recente do servidor**, que pode já ser o de outro aparelho que salvou depois, sem reaplicar a edição velha; se não existe, volta ao último confirmado, avisa e deixa repetir com o mesmo token, e o servidor, achando o recibo numa repetição, devolve o resultado guardado em vez de aplicar de novo; testes da resposta perdida **com um salvar de outro aparelho no meio**, **do plano que chega ou cai entre a leitura e o salvar** (o bloco escondido continua no servidor, porque só `removidos` tira bloco), **do mesmo token com conteúdo diferente (recusado)**, **do mesmo token em dois usuários e em dois perfis** (cada um aplica o seu layout) e **do primeiro salvar concorrente de um perfil sem registro** e de **um commit que termina depois da primeira consulta**, e do **token descartado depois dos 10 min**, que adota o servidor e não reaplica |
| Sem rede desde o começo | "Organizar" fica desligado; sem fila de gravações pendentes, salvo pedido do dono |
| Restaurar o padrão | grava o registro com o padrão **explícito**, que conta como reivindicado: nenhum aparelho com layout antigo o ressuscita. **Restaurar é um salvar como os outros**: o cliente põe em `removidos` todo id visível da `base` que o preset omite (de `[A, C]` para `[A]`, `removidos=[C]`), e o servidor segue a regra de sempre, **preservando id desconhecido ou escondido pelo plano**; teste do restaurar de `[A, C]` para `[A]`, em que `C` sai e o id escondido fica |
| Id de bloco que o escritor não mostra (desconhecido, escondido pelo plano) ou que **outro aparelho acabou de acrescentar** | preservado em toda gravação. **O corpo do salvar leva cinco coisas**: o `token`, a `versão` lida, a **`base`** (os ids que o escritor leu nessa versão, o que dá a base de três vias para ids), o `layout` novo (a ordem dos blocos que o escritor tem) e **`removidos`, o conjunto de ids que a pessoa tirou de propósito nesta edição**, calculado da ação dela (clicar em remover), nunca por diferença entre conjuntos: o servidor não adivinha removido por ausência, o que também dispensa o conjunto de ids visíveis, o refiltro de plano que chega no meio (A19) e o bloco que outro aparelho acrescentou durante um conflito de versão. Regra do servidor: **só o que está em `removidos` sai**; todo id do layout mais recente que não está em `layout` nem em `removidos` (escondido pelo plano, de cliente mais novo, ou acrescentado concorrentemente) **fica**. O merge é do servidor contra o layout mais recente, com **âncora determinística**: esse id que ficou vai junto do bloco do `layout` novo que o **precede** no layout mais recente (se não houver, fica no início) e acompanha esse bloco quando a ordem muda. **Se o bloco âncora foi removido**, passa para o bloco mais próximo antes dele que sobreviveu (andando para trás no layout mais recente) e, se nenhum sobreviveu, para o início; o que ficou nunca é descartado nem ressuscita o removido. Exemplo: no servidor `[A, metas, B]`, o web, que só vê `[A, B]`, salva `layout=[B, A]`, e o resultado é `[B, A, metas]`; se a pessoa remover `A` (`removidos=[A]`, `layout=[B]`), o resultado é `[metas, B]`; no conflito em que outro aparelho acrescentou `C` (`[A, C]`) e este salva `layout=[A]` sem remover nada, `C` fica; testes do cruzamento, da remoção, da remoção com id desconhecido e da adição concorrente durante o conflito |
| **Dois aparelhos reordenam blocos visíveis ao mesmo tempo** (de `[A,B,C,D]`, um grava `[B,A,C,D]` e o outro quer `[A,B,D,C]`) | **a ordem dos blocos que o escritor mostra é do último a gravar** (ganha o `layout` do salvar que chega depois, depois do conflito de versão e da repetição): sem base de três vias nem operações de mover, de propósito, porque reordenar não perde dado e a pessoa refaz o gesto. O que **nunca** se perde por esse caminho é bloco: adição, remoção explícita e id preservado seguem as regras da linha acima, e a âncora só se aplica a ids que o escritor não mostra. Limite declarado: a segunda ordem apaga a primeira. **Remoção concorrente contra reordenação** (dois aparelhos leem `[A,B]`, um remove `B` e grava `[A]`, o outro reordena para `[B,A]`): o servidor compara com a `base` do escritor, e **id que está na `base`, não está no layout mais recente e o escritor não removeu foi removido por outro aparelho: continua removido** e sai do `layout` do escritor; **id que está no `layout` do escritor e não está na `base` foi acrescentado por ele: entra**. Resultado do exemplo: `[A]`. Limite declarado: uma pessoa que remove e reacrescenta o mesmo bloco na mesma edição, enquanto outro aparelho o remove, vê o bloco sair. Testes: os dois reordenamentos disjuntos (resultado é o `layout` do segundo salvar, mesmo conjunto de blocos) e a remoção concorrente contra reordenação (`B` não ressuscita) |

**Privacidade do layout no servidor** (só na opção (a)): a tabela do registro e a tabela dos recibos são dado do usuário e entram, **no mesmo PR que as criar**, nos três caminhos de `db/privacy.py`: `build_user_export_zip` (o layout por perfil entra; o recibo é detalhe operacional de 24 h e fica fora da exportação, declarado), `_RESET_TABLES` (o "Recomeçar do zero" apaga os dois: o próximo aparelho volta a reivindicar) e `delete_user_data` (os dois com `user_id` e `ON DELETE CASCADE`, que o teste confere contra o schema real, porque as listas são nominais e uma tabela nova não entra sozinha). Testes: exportação com layout salvo, reset e exclusão sem linha restante nas duas tabelas, e um teste que reprova tabela com `user_id` fora das três listas.

**O que é "personalizado"** (sem isso um padrão velho ganharia a migração): o cache passa a guardar `{ids, origem}`, e `origem: "usuario"` só é gravada quando a pessoa organiza ou restaura. Um cache **sem** `origem`, escrito por cliente anterior, é só uma lista de ids: conta como personalizado **só se for diferente de todos os presets que já existiram para aquele perfil**, numa lista **versionada de presets históricos, num lugar só** (CLAUDE.md §0.7), com teste de paridade entre o web e o app que a compara; igual a algum deles, é padrão e não reivindica.

Testes: migração concorrente, padrão que não ganha, **preset histórico que não ganha**, reset entre aparelhos, gravação com falha de rede, duas gravações concorrentes com id desconhecido. A **A22** cumpre esta tabela no app.

**O que o app deve ao web** é a tabela do §4 do `docs/plano-app-paridade.md` (A1 a A22), com título, faixa e dependências de cada item. A lista não é repetida aqui de propósito (CLAUDE.md §0.7): uma cópia resumida já ficou para trás três vezes na revisão deste PR. **Ambos atrás:** gerenciar categorias (só no painel antigo).

**Ordem recomendada do lado do web** (fonte única da ordem; o §6 do plano do app e o checklist do §8 apontam para cá, não a repetem; justificada no §6 do plano do app). 1) **W1**: a Etapa 6 é a de menor risco, porque as rotas, a foto diária e a regra única já existem. 2) **W11**: sem ele o web não recupera uma conexão que pede reautorização (a frequência disso em produção não foi medida; Completo, Open Finance). 3) Os pequenos: **W5**, **W6** (espera a decisão P5 do plano do app), **W7** (só troca o exemplo pelo `Compromissos` real da `/previsao`), **W8** (o Sair é Completo), **W12**, **W13** e **W14**. 4) **W10**, Completo: a ordem da Home se perde a cada logout; decidir o caminho (recomendado: layout no servidor, junto de `/perfil`) antes de codar. 5) **W3**, cliente puro. 6) **W2** (Etapa 7) e **W4**, os grandes e Completos; W4 depende de decisão do dono sobre as fontes. 7) **W9** (tema claro), por último: é grande e já estava em "Depois". **Não** copiar o `/investido` para o app como item solto: o consumidor real dele é a
escolha da fonte da meta (`MetaFonte.tsx`), que entra com a escrita de metas no app.

**Regra daqui para a frente** (aprovada pelo dono em 2026-10-16; o texto para o `CLAUDE.md` raiz está no §7.2 do plano do app e entra em PR próprio):
todo PR de feature de um lado declara no corpo `Par no outro lado: já existe | plano/issue #N | específico da plataforma
porque …`; rota nova de dado nasce em `api/v2` e é montada nos dois namespaces (`api/nativo/` só para o que for do app
de verdade, com a razão escrita); o servidor só **acrescenta** campo, porque app instalado não atualiza com o deploy.

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
  etapa 0 (seção 4). **Questão superada para a coorte nova pelo dono em 2026-10-05:**
  não haverá essa tela/etapa nem gate para Previsão; permanece apenas o ajuste coerente da
  flag da carteira no escopo previsto, sem apagar outros motivos ou dados antigos.

**Convivência com o painel antigo (desde a etapa 0)**
- Quem usa o v2 ainda alcança o painel antigo (links nos dois sentidos, e o app atual fica
  nele), onde dá para criar lançamento de banco e investimento manual — o que a Q36 tira do
  v2. Decidir se esses caminhos antigos são bloqueados ou adaptados durante a convivência,
  com teste cruzando as duas telas.
  **Decidido pelo dono: bloquear para quem tem a chave, em todos os canais** — PR D
  da etapa 1 (§8). Esconder os botões no `/app` fica para depois, se o dono pedir.
  **Nota de 2026-10-09:** a decisão acima vale para a coorte da chave, mas a Q36 está sendo
  revista para investimento manual e cartão manual não sincronizado; ver a [ADR 0006](adr/0006-declaracao-bancaria-despesa-receita.md).

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
  **Revisão de P1 pelo dono (2026-10-05):** para a coorte nova do dashboard v2, sem
  uso/conexão anterior no PigBank, Q37 deixa de ser etapa própria ou gate para a Etapa 3.
  O registro de 03/10 acima é histórico, superado nesse escopo; P2 e demais decisões
  continuam. Carteira inicial zero e movimentos manuais em dinheiro seguem normalmente.
  Problema eventual usa Recomeçar do zero voluntário, reconexão do banco e novo sync;
  não houve reset por esta decisão nem autorização para limpeza automática/usuários antigos.
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
    toda linha é calculado antes do filtro de texto). O depósito em caixinha e o aporte
    (`deposito_caixinha`/`aporte_investimento`) saíram FORA da lista neste PR, como na soma;
    o dono decidiu em 2026-10-13 a direção e entram desde o PR #983: marcados `guardado`,
    depósito/aporte como saída, saque/resgate como entrada, fora de todo total; em 2026-10-14
    decidiu o valor mostrado bruto (`launches.valor`) e o rótulo "Guardado" no front na lista.
    O guardado é opt-in (`guardado=1`; o `/painel` manda, o `/api/app` não): o app nativo monta o
    mesmo router e apps instalados não se atualizam com o backend, então sem o parâmetro a lista é a de antes. O que entra
    marcado como interno é o de tipo despesa/receita (saque em dinheiro, pagamento de
    fatura, transferência do banco). A lista do `/app` e o "últimos N" do WhatsApp seguem
    as regras deles.
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
    Vínculos manuais `bank_movement_confirmed` podem compartilhar lançamento; transferir
    uma identidade não remove vínculos irmãos nem aplica campos de fusão ao movimento.
    Reconectar a mesma identidade transfere o vínculo para a transação na conexão mais nova,
    preservando pendência/fusão e snapshot; o sync antigo não o retoma. Só há transferência
    quando a transação está no espelho novo: resposta parcial não descarta histórico antigo.
    Cartão é reutilizado pela identidade da conta; a associação só avança para conexão mais
    nova, mesmo ao importar histórico exclusivo da antiga. Havendo cartões duplicados do
    legado, prioriza o dono da conta exata, depois quem já tem compras e o menor id;
    revalida a ocupação antes de reassociar e relê o dono depois da tentativa, tanto
    no vínculo existente quanto na adoção do cartão órfão, sem mover/consolidar compras ou
    faturas existentes. O estado ativo de cada cartão considera a conexão mais nova
    entre sua FK e os vínculos das compras/estornos; pausa/exclusão nova prevalece sobre
    conexão antiga ainda ativa. Assim os cartões legados separados mantêm a proteção
    contra compra manual após desconectar as conexões antigas. Sem FK nem vínculo,
    o cartão continua manual. Limpar a conexão velha relê os vínculos e
    não apaga a representação transferida, inclusive na janela concorrente do cleanup.
    Desconectar a conexão dona preserva a representação no alias da mesma identidade
    que sobreviver (ativo ou pausado, nunca DELETED), inclusive edição, fatura e snapshot.
    Transferência e decisão de limpeza usam o lock da compra/usuário; se houver prova
    bancária ainda ligada, suas referências acompanham a troca. O reconciliador de
    movimentos mantém sua regra prévia de invalidar prova fora do recorte e exigir
    reconfirmação na reconexão. `transactions/deleted` não transfere para alias sem
    vínculo: exclusão pelo provedor não deve ressuscitar a transação. Cartão renomeado
    sem FK é reutilizado pelos backlinks e reassociado quando a conta está livre.
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
  - PR 4 (#836): a página de
    Lançamentos do `/painel` consome `/api/v2/lancamentos`, categorias e contas reais,
    com mês da barra, busca no histórico do plano, filtros do contrato e cursor opaco
    por “Carregar mais”, sem somar páginas. Criação só na Carteira (espécie), detalhes,
    edição e confirmação de apagar seguem `pode`; o POST leva somente campos tocados
    desde a abertura e valores decimais em texto. Escritas não repetem automaticamente;
    resposta perdida conserva contexto e rascunho nesta aba. A recuperação abre GET fresco
    sem filtros no mês da data enviada ou no mês retornado pelo servidor quando a data
    foi omitida; editar data permite conferir origem e destino, e cartão usa a fatura.
    A guarda só libera após confirmação manual na lista, com paginação disponível.
    Busca histórica efetiva exibe anos nos grupos e linhas. Sucesso reinicia na primeira
    página; a conferência reinicia somente a consulta alvo. Erro de escrita relê as páginas
    carregadas, preservando o rascunho e atualizando `pode`, sem confundir página descartada
    com item removido. Sucesso e erro invalidam categorias, contas e todos os resumos em cache.
    Modal reutiliza dialog/fallback Safari 14 e coordena Cmd-K. Os exemplos do chat e do
    protótipo seguem demonstrativos; no painel real os CTAs abrem o extrato sem filtros
    do exemplo, e Cmd-K omite lançamentos/categorias fictícios. Mantidos fora P8 (depósitos
    e aportes ausentes na API; entrou na P8, #983), filtro de cartão individual sem catálogo, busca real em
    Cmd-K, backend/schema e service worker. Validação local usa fixtures e
    bundle servido; não prova backend vivo, produção, PWA/aparelho ou callback Pluggy.
- Etapa 3 — execução autorizada em 05/10/2026; PR1 motor (#842) concluído localmente em
  06/10, com commit/push/PR autorizados pelo dono. Merge somente após nova
  autorização explícita do dono. Plano executável:
  [plano-etapa3-pr1-motor.md](plano-etapa3-pr1-motor.md). Previsão, IA e simulador
  compartilham uma snapshot read-only e cálculo Decimal; carteira física nova dispensa
  Q37, enquanto dados contraditórios mantêm os motivos de incerteza. Recorrências e
  faturas usam identidade/ciclo comum, sem descontar o cartão duas vezes. Consulta
  não cria lançamentos nem resolve pendências. PR2 entrega a API v2 abaixo; PR3 permanece a tela real;
  estimativa variável continua condicionada a decisão posterior.
  - Manager final aprovou localmente, zero bloqueios abertos. Duas passadas Tester;
    reparos finais conferidos pelo Manager, sem terceira passada. Área inicial:
    **1559 passed/2 xfailed**, nomes/status anteriores preservados; frontend:
    **56 passed**, prova visual **12/12** em desktop/mobile, claro/escuro.
    Controle do reparo da base: 14 verdes → 7 falhas/7 verdes sem fix → 14 verdes
    após restauração por cópia e identidade de bytes/SHA. Conferência independente
    final: 279 testes pertinentes e 2 reproduções aprovados.
  - Revisão do #842 em 06/10: cenário explícito de entrada/saída passa o valor à
    tool existente; ambiguidade pede esclarecimento e preserva a pergunta pendente.
    Corrigidos dois gates de CI: inventário aponta a unidade SQL real e o leitor de
    boletos mantém o teto sem variável intermediária. Manager aprovou o delta;
    **1629 passed/2 xfailed**, com nomes anteriores preservados. Controle do cenário:
    53 falhas/6 consultas puras verdes sem fix; restaurado e aprovado no conjunto final.
    Primeiro CI tinha também falhas de Pix e tooltip presentes na main; comparação
    não equivale a CI verde. O head corrigido ainda requer CI e revisão remota.
  - Segunda revisão do #842 em 06/10: prazo aceita “daqui a N dias” e usa a
    mesma expressão para extração, remoção e contagem; múltiplos alvos são
    explicitamente recusados sem consumir pendências. Próxima fatura exclui
    ciclos closed antes da seleção, preservando a dívida e valores incoerentes.
    Manager fresh aprovou os quatro arquivos congelados e os controles causais;
    combinado local: **1789 passed/2 xfailed** em 46 arquivos, todas as
    identidades anteriores preservadas. Comandos/XML em scratch; remedir antes
    de reutilizar os números. Sem terceira passada Tester.
    CI do head anterior: backend/audit aprovados; frontend falha no tooltip
    também vermelho na main. Novo head requer nova revisão remota e CI.
  - Terceira revisão do #842 em 06/10: o atalho de previsão aplica a política
    existente de investimento antes de interpretar compra/cenário. Recusa
    canônica e MANTEM preservados; OF/billing e roteamento geral inalterados.
    Manager fresh aprovou três arquivos congelados e controle causal
    23 falhas/5 positivos sem o reparo. Combinado local: **1878 passed/2 xfailed**
    em 47 arquivos, identidades anteriores e baseline de política preservadas.
    CI do head anterior: frontend/audit aprovados; backend falha somente no
    teste Pix de cached plan também vermelho na main. Novo head requer CI
    e Codex remotos. Sem terceira passada Tester.
  - Quarta revisão do #842 em 06/10: reconhecimento textual compartilhado entre
    core e WhatsApp, uma coleta de alvos e distinção de obrigações existentes
    versus cenário novo explícito. Calendário/dinheiro/tools canônicos mantidos.
    Reproduções no adapter confirmaram consumo/pagamento/categoria indevidos
    antes do core; consultas agora cedem ao core preservando a linha no banco.
    Manager fresh aprovou os três arquivos congelados e os controles finais:
    59 falhas core/12 adapter, 71 integral; restaurado 101 testes aprovados.
    Área local: **1979 passed/2 xfailed** em 48 arquivos, todos os 1880
    nomes/status anteriores preservados. Comandos/XML e freeze finais
    correspondem à versão de ordem revalidada; sem terceira passada Tester.
    CI do head anterior 52e6120f aprovado em todos os jobs. Novo head ainda
    requer CI e Codex remotos. Merge exige nova autorização explícita do dono.
  - Quinta revisão do #842: horizonte finito próximo(s) N dia(s) adicionado à
    mesma extração/remoção/contagem; predicado/datas/tools/gates preservados.
    Harness passou a exportar a dependência do core simulado, sem importar
    bordas reais ou relaxar guardas. Reproduções antes da fonte e controles
    separados: 22 falhas de prazo e 11 falhas de import; restaurado164passed.
    Manager fresh aprovou fonte2+teste1. Área: **2042 passed/2 xfailed**,
    50 arquivos e 103 subtests; os1981 nomes/status anteriores preservados.
    CIe3be teve11falhas próprias de import corrigidas localmente; frontend
    falhou no tooltip com mesma asserção da main93 e fontes sem diff.
    Novo head requer CI e Codex remotos; merge só autorização humana.
  - Sexta revisão do #842: o renderizador legado da projeção até uma data
    prioriza disponibilidade e qualidade. Valores finitos são condicionais,
    título/card/total neutros; campos null/ausentes/não finitos mostram
    Indisponível, preservando números conhecidos e formulário/gates.
    Manager fresh aprovou JS e teste de perfil congelados. Baseline frontend
    56 nomes/status preservados + 6 casos novos: 62 passaram. Controle causal
    da função antiga: 6 falhas; restauração exata e 62 aprovados. Medição focal
    de 12 renders desktop/mobile, claro/escuro, sem corte ou sobreposição.
    Motor/DTO/rota intactos. CI anterior 47004b65: backend/audit aprovados,
    frontend falhou no tooltip com mesma asserção da main e fontes sem diff.
    Novo head requer CI e Codex remotos; merge só autorização humana.
  - Sétima revisão do #842: ocorrências com realização comprovada continuam
    na snapshot, mas saem das coleções de compromissos/causas e da contagem de
    boletos. Apenas três guardas do motor; paid sem prova e valores incertos
    continuam a conferir. Saldo e aritmética permanecem inalterados.
    Reprodução real pela carteira antes da fonte: base 80 e saldo final 70 já
    corretos; chave paga indevida e contagem 2. Controles separados retirando
    cada guarda: três falhas discriminantes com positivos preservados.
    Manager fresh aprovou fonte2+teste1 congelados. Área financeira:
    **2046 passed/2 xfailed**, 103 subtests em 50 arquivos; todos os 2044
    nomes/status anteriores preservados + 4 novos. JS/12 renders intactos.
    Novo head exige CI/Codex remotos; merge só autorização humana.
  - Oitava revisão do #842: consultas com boletos ou contas a pagar sem
    artigo usam as obrigações existentes, sem valor extra. Ajuste limitado
    ao contexto plural completo; qualificadores, exclusões e cenários
    ambíguos continuam recusados. Reconhecimento e adapter inalterados.
    Baseline pertinente 136 verdes; reprodução e negativo com cleanup
    anterior geram 10 falhas discriminantes/140 positivos; restauração
    por SHA e repetição 150 verdes. Área financeira: **2060 passed/2 xfailed**,
    103 subtests em 50 arquivos, todas as 2048 identidades anteriores
    preservadas + 14 novas. Manager independente conferiu o reparo.
    CI do head 417b2791: backend/audit aprovados, frontend 1666/1 falha de tooltip também
    presente na main 93db7c2a, sem diff nas fontes. Novo head exige CI/Codex
    remotos; merge somente com nova autorização humana.
  - CI completo e Codex remoto no head publicável seguem pendentes. Codex local
    foi tentado, mas a CLI recusou o modelo configurado e não produziu parecer.
    Produção, WhatsApp real e aparelho não verificados; sem deploy/TestFlight/reset.
- Guia do `/painel` (#728) em 2 PRs: A `GET`/`POST /api/v2/guia` + tabela `guia_painel` (contrato e consulta de medição em `docs/CLAUDE.md`, "API v2") · B a tela (Piggy, balão, Ajuda).
- [x] Etapa 0 · [x] 1 · [x] 2 · [ ] 3 · [ ] 4 · [x] 5 · [ ] 6 · [ ] 7

Situação em 2026-10-13, conferida contra os PRs mergeados (a 5 foi marcada concluída em 2026-10-15). Ficam abertas: a 3, porque
simulador v2 e estimativa variável não entraram (ver o PR2 da Etapa 3); e a 4, porque falta
o PR C.

| Etapa | PRs mergeados |
|---|---|
| 0 | #632, #659, #669, #675, #678, #688, #691, #723 |
| 1 | #750 (A), #754 (B), #757 (D), #764 (C), #781 (C2) |
| 2 | #778 (1), #792 (2a), #800 e #812 (2b), #831 (3), #836 (4), #983 (P8: guardado) |
| 3 | #842 (PR1), #843 (PR2), #847 (PR3) |
| 4 | #950 (PR A), #954 (PR A2), #961 (PR B) |
| 5 e 6 | 5: #998 (PR 1a, API do mês), #1002 (PR 1b, API da categoria), #1008 (PR 2, a tela ligada à API), #1019 (o card de compras na mesma tela); 6: nenhum PR de etapa. A Etapa 5 está concluída: o dono conferiu o Saiu em produção em 2026-10-15 (ele respondeu "Bateu" na sessão) |
| 7 | só o lado do app nativo e do backend do chat, abaixo; o `/painel` ainda não consome |

- Etapa 4, PR A (#950) e PR A2 (#954): a tabela `metas` e `GET`/`POST /api/v2/metas`
  (`/metas/editar`, `/metas/apagar`), com a fonte do `atual` escolhida pelo usuário (tudo
  investido, um banco ou uma conta), a reserva de emergência (no máximo uma, alvo = meses ×
  custo mensal das recorrências do banco) e, no A2, o `ritmo` (crescimento atual da fonte em
  R$/mês, medido nas fotos por posição) e o `chega_em`. O contrato, os motivos e os limites
  declarados estão em `docs/CLAUDE.md`, "API v2", parágrafo de `/api/v2/metas`. Faltam o PR
  C (IA e WhatsApp usarem a mesma regra) e a conferência pós-deploy.
- Chat do Piggy (Etapa 7, lado do backend e do app nativo): `POST /ai/chat/stream` com
  parar e 👍👎 (#943), `show_card` com a mesma fonte do app (#946, #947), chips de
  continuação (#949), períodos de até 12 meses e card `periodo` (#957), e o consumo no app
  (#927, #953, #956). O `/painel` web ainda não chama `/ai/chat/stream`: a Etapa 7 do web
  continua inteira.

### Etapa 3 — PR2: API v2 da previsão (implementação local, 06/10/2026)

`GET /api/v2/previsao` adapta a snapshot/motor do PR1: sessão e plano antes da
leitura, horizonte 30/60/90 pela política existente, base bancária canônica v2,
uma transação repeatable read/read only. Plus recebe só marcos30; Pro recebe
trajetória futura, âncora hoje, pior dia e compromissos conhecidos. Dinheiro fonte
é Decimal/texto sem arredondar; somente saldos calculados seguem o motor.
Sem escrita/sync/reparo/TTL/SSE, sem regra financeira ou orientação nova; qualidade
e validade permanecem explícitas, estimativa variável e cabe_nas_premissas false.

Tipos TS gerados e fixtures herméticas validáveis preparam PR3. Os limites são
horizonte solicitado/autorizado (até 90 pontos, até 3 marcos) e capacidade do plano.
Grupos/ocorrências completos, sem truncamento; **não há teto global de bytes
comprovado ou introduzido**, e o requisito global de tamanho do esboço da Etapa3
não é marcado como concluído. Esses limites não limitam quantidade de fontes, nomes
ou motivos. Um teto futuro exige necessidade real de transporte/consumidor.

PR2 aguarda revisão independente, CI e Codex no head. Etapa3 inteira permanece
aberta: PR3 terá consumidor/tela/refetch temporal; simulador v2, estimativa variável,
calendário novo e Q37 não entram nesta entrega. Merge requer autorização do dono.

### Etapa 3 — PR3: tela real da previsão (implementação local, 07/10/2026)

Com backend, `/painel#/previsao` e o card "Saldo previsto" do Resumo (Q1 = a) mostram a
`GET /api/v2/previsao` como chega: marco do horizonte, base, âncora (hoje), pior dia,
gráfico a partir da âncora, compromissos agrupados (Q2 = a: sem total, "N × valor" ou
"valores diferentes", ocorrências na expansão), estado, motivos com direção e
premissas. Plus: marco de 30 dias e convite ao Pro, sem gráfico nem número atrás do
convite. Sem faixa provável, estimativa variável nem "cabe". O protótipo não muda; o
Cmd-K real perde os atalhos de horizonte e a `/previsao` real sai do seletor de mês.
Pendência financeira nova sem aviso SSE fica como limite declarado (`docs/CLAUDE.md`,
API v2); fechá-la é PR Completo à parte.

### Etapa 4 — PR B: tela real de metas

Com backend, `/painel#/metas` mostra e escreve a `GET/POST /api/v2/metas` (#950, #954): o
cartão de cada meta com os dois ritmos lado a lado (o que precisa × o seu ritmo), o convite
da reserva de emergência no topo, o formulário de criar, editar só o que mudou, converter a
reserva em meta comum e apagar. Decisões do dono: o card "Metas e caixinhas" SAIU do Resumo
(layout padrão e presets; layout salvo com o id `metas` é filtrado pelo `readLayout`) e o
hook `useDialogo` (`parts/dialog.ts`) é o esqueleto de modal do `LancamentoForm` e do
`MetaForm`: o clique no fundo só vale passada a `JANELA_DO_FUNDO`, então o 2º clique de um
duplo clique no botão que abriu o diálogo não o fecha (vale também para o lançamento da
Carteira). Meta batida não mostra "Precisa" nem "Seu ritmo". Em plano sem investimentos a
fonte padrão da meta nova é a conta (`MetaFonte`). Validação e mensagem do valor em dinheiro
são uma só para lançamento e meta (`lib/format.js`); `DIAS_MINIMOS` e `MESES_PADRAO` de
`lib/metas-textos.ts` copiam constantes do servidor e `tests/test_metas_textos_constantes.py`
compara as duas. "Guardado no mês" saiu do Resumo do mês (era dado inventado; o "Guardado líquido"
do app nativo é outra coisa e não muda). O protótipo e o chat do Piggy (PR C) não mudam.
Limite declarado: sem idempotência no POST, duas metas iguais são possíveis (a reserva tem
o 409).


### Paridade com o app (2026-10-16)

- [x] Matriz de paridade `/painel` × app nativo levantada e a regra do dono registrada, **só em plano** (nenhum código):
  `docs/plano-app-paridade.md`, com o resumo do que o web deve ao app no §6 acima (W1 a W14).
- [x] Decisões do dono (§10 do plano do app, 2026-10-16): ordem aprovada, texto da regra aprovado para o `CLAUDE.md`
  (em PR próprio), guia existe no app, sem data de App Store. Seguem abertas: modo privacidade no `/painel` (W6) e
  Guardado líquido no Resumo (W5).
- [ ] Executar as lacunas, uma por PR: o lado do app na ordem do §6 do plano do app; o lado do web na **Ordem recomendada** do §6 deste plano (W1 a W14, fonte única).
