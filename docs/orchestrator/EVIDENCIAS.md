# Evidências para o plano do orquestrador

Consulta: 2026-09-13. Inspeção de arquivos, ajuda das CLIs instaladas e documentação oficial. Não foram iniciadas sessões de agentes, lidas credenciais nem testadas integrações ponta a ponta.

## Repositório existente

- [time-dev](../../.claude/commands/time-dev.md): fluxo dos quatro papéis, contexto repassado em prompts, três rodadas de Coder/Tester e auditoria pelo Manager. As devoluções do Manager não têm limite global declarado.
- [Papéis](../../.claude/agents/): prompts, ferramentas e critérios reutilizáveis após separar as regras específicas do Bot Financeiro.
- [Triagem](../../.agents/skills/triage/SKILL.md), [tickets](../../.agents/skills/to-tickets/SKILL.md) e [handoff](../../.agents/skills/handoff/SKILL.md): especificações de procedimentos, sem motor de execução nesses arquivos.
- [Hook de início](../../.claude/hooks/session-start.sh): prepara ambiente remoto e Postgres isolado por sessão; não agenda nem distribui tarefas de desenvolvimento.
- [Hooks locais do Codex](../../.codex/hooks.json): verificações do Impeccable; não implementam o loop de desenvolvimento.
- O agendamento e os agentes financeiros do produto atual possuem outra finalidade.

Na inspeção, `.claude/` e `CLAUDE.md` são idênticos entre `HEAD` e a referência local `origin/main`, embora as referências tenham divergido. `.agents/` e `.codex/` estão presentes localmente e não versionados. Não houve atualização da referência remota nesta inspeção. Esses fatos devem ser conferidos novamente antes de uma implementação.

### Revisão de PR já descrita no projeto

O [`CLAUDE.md`, seção 4](../../CLAUDE.md), descreve revisão do Codex no GitHub, inclusive pedido por comentário `@codex review`. O ciclo documentado inclui conferir cada apontamento, corrigir ou contestar com evidência, responder na própria thread e solicitar revisão do novo código. Tester e Manager antecedem o push; parecer favorável e CI verde deixam o PR pronto, mas o merge exige autorização do dono.

O arquivo usa uma frase de resposta como exemplo de aprovação. Isso é uma convenção do procedimento atual, não um formato de resultado estruturado implementado neste repositório.

O [workflow de testes](../../.github/workflows/tests.yml) roda em PRs e pushes na `main`. Não foi encontrado nos workflows ou scripts inspecionados um motor que dispare o review do Codex, interprete o parecer e acompanhe suas threads. A inspeção não consultou o GitHub: instalação do aplicativo, proteção de branches, revisão de PR em rascunho, identidade do revisor e comportamento real continuam sem comprovação.

## Interfaces de agentes candidatas

### Codex

A ajuda local consultada corresponde a `codex-cli 0.153.0`.

- O [App Server](https://learn.chatgpt.com/docs/app-server) documenta autenticação, histórico, eventos, aprovações, início e retomada de sessões e interrupção de turnos por um protocolo bidirecional. O comando está marcado como experimental na versão consultada. `stdio` é candidato para um processo local; o transporte WebSocket também é experimental.
- O [SDK](https://learn.chatgpt.com/docs/codex-sdk) e `codex exec --json` atendem automações programáticas. A ajuda instalada inclui retomada por identificador e saída estruturada.
- A [autenticação](https://learn.chatgpt.com/docs/auth) diferencia acesso por conta ChatGPT de uso da API. Não se deve inferir equivalência de cobrança, limites ou recursos entre esses modos.

**Implicação proposta:** validar uma versão específica do executor e preservar seus identificadores de sessão. A integração com um cliente próprio não demonstra sincronização com as conversas existentes no aplicativo desktop.

### Claude Code

A ajuda local consultada corresponde a `Claude Code 2.1.247`.

- O [uso programático da CLI](https://code.claude.com/docs/en/headless) permite execução sem interface interativa, eventos `stream-json` e retomada de sessão. A ajuda local confirma as opções de saída, retomada e ferramentas permitidas.
- O [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) oferece sessões e ferramentas. A [entrada do usuário](https://code.claude.com/docs/en/agent-sdk/user-input) documenta callbacks de aprovação e perguntas. Regras previamente autorizadas podem evitar o callback de aprovação; ele sozinho não representa uma política universal.
- A [documentação de autenticação e conformidade](https://code.claude.com/docs/en/legal-and-compliance) distingue login do usuário no binário Claude Code não modificado de intermediação de tokens Claude.ai por um aplicativo. O produto não deve assumir que autenticação de assinatura e autenticação do SDK são intercambiáveis.
- A [documentação de sessões](https://code.claude.com/docs/en/agent-sdk/sessions) distingue contexto de conversa de isolamento dos arquivos.

**Implicação proposta:** comprovar o caminho de execução com assinatura e interação de aprovações antes de escolher o executor. Não armazenar credenciais copiadas da sessão do usuário como mecanismo próprio de login.

## Requisitos técnicos candidatos, ainda sujeitos às decisões do produto

- Guardar estado de tarefas, tentativas, sessões dos provedores, decisões e artefatos fora do contexto do modelo.
- Relacionar retomadas a identificadores exatos; nunca retomar implicitamente a última sessão de uma máquina compartilhada.
- Separar quem solicita uma tarefa de quem fornece a identidade e a capacidade de execução.
- Reconciliar processos, arquivos e estado persistido após interrupções; retomar contexto não torna segura a repetição de efeitos externos.
- Isolar cópias de trabalho e ambientes de teste de tarefas concorrentes.
- Capturar progresso e resultados sem depender de analisar texto de terminal destinado a pessoas.
- Provar uma tarefa pequena com progresso, decisão humana, interrupção, reinício e retomada antes de desenvolver todas as telas.

## O que continua sem validação

- Funcionamento efetivo de cada executor com as contas do usuário.
- Forma adequada de titularidade das execuções em uso compartilhado pela equipe.
- Paridade dos mecanismos de aprovação entre os provedores.
- Limites e métricas de uso realmente disponíveis para cada conta.
- Comportamento de retomada após queda e a interação com worktrees e processos filhos.

As implicações e os requisitos candidatos são propostas de arquitetura a partir das fontes, não capacidades já implementadas no repositório.

## Trello como entrada de tarefas

A [API oficial de cards](https://developer.atlassian.com/cloud/trello/rest/api-group-cards/) permite consultar cards, checklists, ações/comentários e anexos, além de atualizar cards e adicionar comentários mediante autenticação e permissões adequadas. Essas operações permitem projetar importação de tarefas e associação ao PR; não comprovam que a conta do usuário já esteja conectada.

O [guia de webhooks](https://developer.atlassian.com/cloud/trello/guides/rest-api/webhooks/) documenta notificações de alterações para uma URL de callback e validação desse endereço. Como proposta para o piloto local, seleção manual ou consulta periódica evita exigir um endpoint público no Mac. No servidor, webhooks são uma opção para receber mudanças.

Nenhum quadro, card ou credencial do usuário foi acessado e nenhuma escrita no Trello foi realizada. O usuário escolheu posteriormente o Trello como entrada adicional, com importação por link no piloto e reconhecimento automático por labels como evolução futura.

## Repouso do Mac e ciclo de vida do painel

Requisito do usuário: fechar o painel encerra execuções locais; desligamento ou reinício preserva estado e exige Retomar. Após a pesquisa, o usuário confirmou funcionamento com tela apagada/bloqueada e Mac acordado durante o trabalho, além de uma janela própria com frontend web.

A documentação Apple de [`kIOPMAssertionTypePreventUserIdleSystemSleep`](https://developer.apple.com/documentation/iokit/kiopmassertiontypepreventuseridlesystemsleep) permite impedir a suspensão por inatividade enquanto a tela pode apagar. Ela não impede suspensão forçada por tampa, menu ou bateria baixa. [Apple QA1340](https://developer.apple.com/library/archive/qa/qa1340/_index.html) diferencia esses tipos de suspensão. O manual local de `caffeinate` também confirma a opção de impedir idle sleep; foi somente lido, sem executar o comando ou mudar configurações.

**Conclusão técnica:** podemos propor execução com tela apagada/bloqueada mantendo o Mac acordado, não execução contínua durante suspensão real. O [uso com tampa fechada documentado pela Apple](https://support.apple.com/en-gb/102501) depende de configuração com dispositivos externos; não é garantia geral para um Mac fechado e sem acessórios.

O [ciclo de vida de páginas do Chrome](https://developer.chrome.com/docs/web-platform/page-lifecycle-api) documenta congelamento, descarte e a falta de confiabilidade dos eventos finais de uma página. Assim, uma aba comum com conexão/heartbeat permite detectar a perda do painel, mas não comprova encerramento instantâneo de todos os processos quando a aba fecha.

Como exemplo de contêiner com frontend web, Electron documenta eventos próprios de [janela](https://www.electronjs.org/docs/latest/api/browser-window) e [falha do conteúdo](https://www.electronjs.org/docs/latest/api/web-contents#event-render-process-gone). Isso fundamenta a proposta de janela desktop supervisionando o trabalho; Electron não foi escolhido nem instalado, e o encerramento real dos processos precisa de testes. [A documentação de processos filhos do Node.js](https://nodejs.org/api/child_process.html) também distingue enviar um sinal de comprovar o encerramento do processo.

Operações já submetidas a serviços remotos têm ciclo de vida próprio. O [cancelamento de GitHub Actions](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation) tem procedimento separado; fechar o painel local não comprova cancelamento de CI nem da revisão remota do Codex. O plano deve distinguir interromper o trabalho local de cancelar uma operação remota já aceita.

Essas consultas foram somente leitura. Não se alterou energia, não se executou um serviço persistente do orquestrador e não se validou seu ciclo de vida em runtime.

## Proposta técnica consolidada

O [modelo de processos do Electron](https://www.electronjs.org/docs/latest/tutorial/process-model) separa o processo principal, que controla as janelas, do processo que renderiza o conteúdo web. Essa estrutura fundamenta a recomendação de frontend React/TypeScript e supervisão local separada. A [documentação de segurança](https://www.electronjs.org/docs/latest/tutorial/security) orienta isolamento de contexto, limitação de capacidades e validação de comunicação entre processos; o frontend não deve receber acesso irrestrito ao shell.

O [`powerSaveBlocker`](https://www.electronjs.org/docs/latest/api/power-save-blocker) oferece `prevent-app-suspension`, que mantém o sistema ativo permitindo apagar a tela, e permite liberar a proteção por identificador. É o mecanismo candidato para concretizar Q19; não foi executado nesta etapa.

A [API de merge do GitHub](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request) permite informar o `sha` esperado do head. Essa precondição rejeita um head diferente, mas não recebe uma condição equivalente de igualdade para o commit da base. Conferir a base antes de chamar a API deixa uma janela de concorrência; uma reserva local não bloqueia integrações de terceiros.

A documentação de [branches protegidas](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches) distingue checks estritos, que exigem branch atualizada, de checks sem essa exigência. As regras também precisam cobrir a identidade de publicação, considerando permissões de bypass. **Inferência para o plano:** exigir atualização e checks é parte da proteção proposta, mas não constitui, isoladamente, prova de igualdade atômica do `base_sha` autorizado. A configuração real e o caso de mudança da base entre leitura e merge precisam ser comprovados no primeiro marco.

A [merge queue do GitHub](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue) valida alterações contra a base atual e os PRs anteriores na fila, com execução de CI para o candidato de integração, incluindo suporte ao evento `merge_group` quando usado GitHub Actions. Sua disponibilidade depende do tipo de repositório e plano. **Inferência para o plano:** uma fila pode oferecer outro caminho de integração, mas reconstituir o grupo pode mudar o candidato; ainda é necessário vincular suas evidências e a autorização humana ao código que será integrado.

Os [contratos de confiabilidade](CONFIABILIDADE.md) exigem validação ligada ao candidato e à base, isolamento efetivo de capacidades e publicação do conteúdo aprovado. São requisitos a demonstrar, não garantias já implementadas nem proteções verificadas na conta do usuário. Se o mecanismo remoto disponível não assegurar o contrato, o aplicativo não habilita merge até resolver essa condição.

Electron, TypeScript, React e SQLite são a proposta de implementação para o piloto; versões e biblioteca de acesso ao banco serão fixadas após validar compatibilidade no novo aplicativo. Não foram adicionadas dependências ao Bot Financeiro.
