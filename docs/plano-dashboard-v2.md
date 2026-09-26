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
  entra no saldo uma última vez, com aviso.

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
  e o app. **Erro** (Q25): um envelope único com código.
- **Tempo real** (Q19, Q27–Q29): SSE, só servidor → cliente, avisando só *o que* mudou;
  a tela pede o dado de novo. Princípios: o aviso vai só para o dono do dado, só depois de
  gravado, e a tela nunca fica desatualizada em silêncio (reconectar refaz tudo; sessão
  encerrada fecha o stream). Toda escrita de dado financeiro avisa, venha de onde vier.
  Processo único hoje; com mais de um processo, `LISTEN/NOTIFY` do Postgres. A etapa 0
  confirma com o dono se o `bot.py` (Discord) sai do `launch.py`.
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
- **Rendimento × CDI** (Q35): por investimento, sem número da carteira somada. A fonte é a
  rentabilidade que o banco informa pelo Open Finance, gravada a cada sincronização para
  formar histórico. Só compara com o CDI quando se sabe o período exato e que a posição
  existiu nele o tempo todo.
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
- **Plano real** pelo `/auth/me` (hoje o protótipo lê `?plano=`).
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
| 0 | Esqueleto da `/api/v2` (usuário, erro, contrato, SSE), `/painel` com a chave, plano pelo `/auth/me`, TanStack Query, job da foto diária e histórico da rentabilidade do Open Finance | Completo |
| 1 | Resumo (perfil no servidor) | API Completo, tela Leve |
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
- Quando o dado do Open Finance conta como desatualizado (limite por produto) e como a
  tela aberta percebe isso sem escrita.
- Rentabilidade do Open Finance: medir na API real o que o Pluggy manda (mês de
  referência, datas da posição, e se a taxa do banco já desconta aporte e resgate no
  período) antes de decidir o que comparar. A comparação usa a taxa que o banco calcula,
  nunca a diferença entre fotos do rendimento acumulado.

**Etapas de tela (1 a 6)**
- Etapa 2: identidade das transações importadas por conta (conta e cartão); editar a data
  de um lançamento fundido.
- Etapa 3: desde a Q42 o gasto fixo diário, semanal e único entra na Previsão, uma
  ocorrência por data — um diário gera até 90 itens em `compromissos`/`causas`. A tela
  `/previsao` tem de agrupar por nome; o código de hoje não agrega nem limita.
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
- [ ] Protótipo: blocos que expandem na conversa (em outro chat).
- [ ] Pré-requisitos: #594 · Q42 · Q43 · Q40 (regra) · Q41
- [ ] Etapa 0 · [ ] 1 · [ ] 2 · [ ] 3 · [ ] 4 · [ ] 5 · [ ] 6 · [ ] 7
