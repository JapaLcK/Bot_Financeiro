# Plano final do orquestrador

Consolidado em 2026-09-13. Incorpora as 23 decisões confirmadas e os sete ajustes de confiabilidade. Entrega exclusivamente de planejamento: a implementação não foi iniciada e não será iniciada nesta etapa, conforme solicitado pelo usuário. As tecnologias abaixo são recomendações; suas integrações precisam passar pelas provas definidas neste plano.

## 1. Produto e resultado esperado

Criar um aplicativo para Mac, com frontend web em janela própria, para coordenar agentes de desenvolvimento desde o pedido até um PR testado, auditado e revisado, com merge condicionado à autorização do usuário.

O aplicativo terá repositório próprio e permitirá cadastrar projetos locais. O Bot Financeiro será o primeiro projeto gerenciado. O piloto será operado somente pelo usuário no Mac; depois de validado, o produto evoluirá para servidor e acesso da equipe.

A primeira versão inclui Claude Code e Codex, usando as assinaturas existentes por mecanismos suportados pelos provedores. Não haverá troca automática para API paga. O sistema deve executar duas tarefas simultaneamente em ambientes isolados.

O processo atual de `time-dev` fornece os papéis e a sequência inicial. Sua adaptação deve exigir aprovação de todo plano, remover o teto de revisões internas e acrescentar investigação do entorno desde o primeiro apontamento. O motor, a persistência e o painel serão construídos no novo aplicativo; os agentes financeiros do Bot Financeiro continuam pertencendo ao produto financeiro.

## 2. Entradas e fluxo completo

Uma tarefa pode ser criada diretamente no painel ou importada por link de card do Trello. Ambas seguem o mesmo fluxo:

```text
Pedido e projeto associados
    → Arquiteto / Claude Code prepara o plano
    → Usuário aprova a versão do plano
    → Coder / Claude Code implementa na área da tarefa
    → Controlador sela um candidato de código e base
    → Tester / Codex testa o candidato
    → Manager / Codex audita em sessão separada
    → Controlador publica o candidato e abre PR em rascunho
    → Codex faz revisão adicional no GitHub; CI executa
    → Usuário autoriza merge do candidato validado
    → Controlador verifica as condições e confirma a integração
```

Apontamentos internos ou externos retornam para investigação, correção ou contestação fundamentada. Uma correção gera outro candidato e passa novamente pelas verificações pertinentes. Alterar o escopo exige atualizar o plano e obter nova aprovação; investigar o entorno não autoriza ampliar silenciosamente a tarefa.

O usuário aprova planos antes de qualquer implementação, inclusive em tarefas triviais. Aprovar o plano, autorizar um ciclo adicional e autorizar merge são decisões distintas e registradas separadamente. Os commits usam mensagens em português brasileiro.

## 3. Papéis e permissões

| Papel | Responsabilidade | Limite efetivo |
| --- | --- | --- |
| Arquiteto — Claude Code | Ler o projeto, esclarecer o pedido e produzir plano e critérios de aceitação | Sem edição do código e sem publicação |
| Coder — Claude Code | Implementar código e testes no escopo aprovado | Escrita somente na área da tarefa; sem push, criação de PR ou merge |
| Tester — Codex | Executar testes, investigar regressões e propor testes adicionais | Avalia candidato protegido contra escrita; novos testes são patches separados que retornam ao fluxo |
| Manager — Codex | Auditar escopo, qualidade, evidências e alcance das mudanças | Sessão distinta do Tester; não edita o candidato nem publica |
| Codex no GitHub | Revisar adicionalmente o PR depois da aprovação interna | Parecer externo necessário para liberar integração |
| Controlador do aplicativo | Preparar commits/branches, publicar, abrir PR, acompanhar revisão e executar merge | Código do sistema verifica fase, evidências e autorizações antes de cada efeito |

As restrições existem nas ferramentas e no ambiente de execução. Os agentes e os comandos de teste não recebem credenciais de publicação, acesso indireto a essas credenciais, escrita nas políticas do controlador ou na tarefa vizinha. Shell, HTTP, helpers e sockets não podem oferecer um caminho alternativo para publicar ou integrar código.

O frontend transmite ações estruturadas ao controlador; não recebe shell nem credenciais. Conteúdo de cards, comentários e logs é tratado como dado, sem poder alterar permissões. A compatibilidade desse isolamento com as assinaturas dos dois provedores será comprovada no primeiro marco.

## 4. Código testado e base de destino

Cada validação identifica projeto, branch de destino, commit da base, commit candidato, árvore de arquivos e versões das entradas e comandos de teste. A autorização de merge referencia essa identidade.

Antes de selar o candidato, o controlador encerra escritores e examina arquivos rastreados, staged, não rastreados e entradas ignoradas necessárias ao build/teste. Entradas externas precisam de conteúdo ou versão fixada; nome ou caminho isolado não identifica uma versão. Segredos não são copiados para relatórios ou commits.

Tester e Manager recebem cópias do candidato com entradas protegidas contra escrita. Temporários, logs e saídas ficam em locais separados. Verificações no início e no fim de cada etapa complementam essa proteção. Um patch novo de teste volta ao Coder/controlador, produz novo candidato e repete a validação. O controlador publica o commit aprovado, sem recolher alterações posteriores da pasta de trabalho.

Qualquer mudança observada na base invalida prontidão e autorização de merge anteriores, mesmo sem conflito textual. A tarefa incorpora a base atual, gera novo candidato e repete testes, auditoria, CI e revisão externa pertinentes. Os resultados anteriores permanecem no histórico.

Desenvolvimento e testes podem ocorrer em paralelo; as integrações são serializadas por repositório e branch de destino. O controle local deve ser complementado por proteção remota que impeça integrar um candidato fora da identidade autorizada. A configuração e a corrida entre leitura da base e merge serão comprovadas no primeiro marco. Enquanto essa garantia não estiver demonstrada, a ação de merge permanece indisponível.

O resultado do merge também é reconciliado. Um método que gera outro commit, como squash, deve conservar o conteúdo validado. Os detalhes sobre identidade da base, proteção remota e eventual merge queue estão em [CONFIABILIDADE.md](CONFIABILIDADE.md).

## 5. Revisões, progresso e intervenção

Tester e Manager podem devolver o trabalho quantas vezes forem necessárias. Essas avaliações nunca incrementam o contador externo. **Sem progresso verificável, a execução pausa e apresenta o impasse ao usuário**, conforme Q21.

O sistema registra evidências de progresso ou estagnação: achados encerrados, evidências novas, regressões, soluções revertidas e exigências incompatíveis. Repetir o mesmo estado sem evidência nova ou alternar A → B → A são exemplos de impasse. Uma investigação longa ou um teste ainda em execução não significa, isoladamente, falta de progresso.

O contador externo se chama **Rodadas de apontamentos no PR**:

| Evento | Regra |
| --- | --- |
| Primeiro parecer concluído do Codex no PR com apontamentos acionáveis | Rodada 1 |
| Vários comentários do mesmo parecer | Uma única rodada; entregas duplicadas são deduplicadas |
| Segunda rodada com apontamentos | Consolidar a evolução completa dos achados e das correções |
| Terceira rodada com problemas pendentes | Pausar antes da próxima resposta automática e pedir intervenção |
| Autorização para continuar, conforme Q22 | Liberar uma resposta ao lote e uma nova avaliação externa, com validações internas necessárias |
| Quarta ou qualquer rodada posterior novamente com problemas | Exigir outra intervenção antes da próxima resposta |
| Parecer sem apontamentos, espera, falha de comunicação ou limite de uso | Não incrementar o contador de apontamentos |

Reinício, troca de sessão, novo commit, reabertura ou substituição do PR não apagam o histórico nem contornam uma intervenção pendente. A autorização de ciclo adicional é persistida e reservada para um único ciclo; reenvios e recuperação não geram uma autorização extra.

Desde o primeiro apontamento, interno ou externo, registrar identificador, origem, evidência, versão avaliada, tentativas, resposta e estado. Em cada correção, examinar chamadores, consumidores, caminhos próximos, problemas semelhantes e possíveis regressões. Informar também o que não foi verificado.

## 6. Contestação e aprovação

Um achado pode estar aberto, com correção proposta, contestado, aguardando reavaliação, resolvido, com contestação aceita ou identificado como duplicado de outro achado.

Coder propõe correções e justificativas; o papel que originou o apontamento valida seu encerramento em reavaliação registrada. Para achados do PR, isso exige evidência verificável da revisão externa do Codex. Manager aceitar uma justificativa ou alguém fechar uma conversa no GitHub não substitui essa avaliação.

Discordância persistente exige orientação humana sobre evidência, abordagem ou escopo. A intervenção não transforma parecer externo desfavorável em aprovação. Silêncio do revisor e ausência de correlação confiável com os achados anteriores não liberam merge.

Um PR somente fica pronto para autorização de merge quando o plano está atendido, Tester e Manager aprovaram o candidato, CI obrigatório passou, a revisão externa é favorável para o candidato atual e os achados estão encerrados pela autoridade correspondente. A autorização humana e a conferência da base continuam necessárias no momento da integração.

## 7. Pausa, interrupção, cancelamento e retomada

| Condição | Comportamento |
| --- | --- |
| Pausada pelo usuário | Interromper execução e novos despachos; preservar artefatos e permitir Retomar após reconciliação |
| Interrompida por fechamento, falha ou reinício | Preservar o ponto conhecido; verificar arquivos, sessões e efeitos incertos antes de Retomar |
| Bloqueada por impasse, decisão ou requisito | Mostrar a causa e a ação necessária; Retomar não ignora o bloqueio |
| Cancelada | Encerrar a tarefa para execução, removê-la do agendamento e não oferecer Retomar |
| Reaberta explicitamente | Reconciliar o estado, preservar histórico e reapresentar o plano vigente para aprovação |
| Concluída | Manter histórico da integração; novos pedidos formam outra tarefa |

Conforme Q23, cancelar preserva branch, worktree, arquivos e PR. Se houver PR aberto, mostrar **Tarefa cancelada — PR ainda aberto**. A ação separada **Cancelar e fechar PR** inclui o fechamento remoto e informa eventuais falhas. Não excluir branches ou worktrees automaticamente.

Eventos remotos recebidos após pausa ou cancelamento atualizam o histórico sem reativar a tarefa. Se uma operação de merge já tiver sido enviada, interromper novos despachos e reconciliar seu resultado; cancelamento não desfaz uma integração já ocorrida.

## 8. Janela, energia e persistência no Mac

O trabalho local depende da janela própria do aplicativo aberta. Minimizar ou mudar o foco mantém a execução permitida. Fechar normalmente impede novos despachos, encerra agentes e processos descendentes, persiste o estado e então fecha o aplicativo, sem deixá-lo trabalhando em segundo plano.

Com a janela aberta e trabalho ativo, manter o Mac acordado para permitir tela apagada ou sessão bloqueada. Ao encerrar, liberar essa proteção. Suspensão real interrompe processos locais; não se promete execução geral com o computador dormente ou a tampa fechada.

Persistir continuamente tarefas, planos aprovados, sessões por papel, candidatos, achados, decisões, eventos e referências aos artefatos. Após reiniciar o aplicativo ou o computador, restaurar o histórico e oferecer continuação manual às tarefas elegíveis. Abrir o painel nunca inicia agentes automaticamente.

Retomar significa continuar do ponto recuperável após reconciliar o estado real; não supõe restaurar um processo exatamente no ponto em que a máquina parou. Antes de repetir push, criação de PR, comentário ou merge, verificar se o efeito já aconteceu.

Falha do painel ou do supervisor também deve encerrar a execução local. O tempo de detecção e o término de filhos/netos serão medidos. CI e revisões já aceitos pelo GitHub podem terminar remotamente enquanto o aplicativo estiver fechado.

## 9. Interface do piloto

O painel será em português brasileiro e orientado por skills de interface, começando por `impeccable`. Terá contexto visual próprio para o orquestrador, sem assumir a identidade do produto financeiro.

| Área | Conteúdo e ações |
| --- | --- |
| Primeiro uso e projetos | Cadastrar repositório, configurar validações e verificar o estado real das integrações |
| Visão geral | Pendências de decisão, tarefas em fila e as duas tarefas ativas, com controles independentes |
| Criar/importar tarefa | Pedido nativo ou link do Trello, projeto associado e critérios de aceitação |
| Detalhe da tarefa | Conversa, plano versionado, etapa e agente atual, alterações, testes, histórico e artefatos |
| Decisões | Aprovar plano, responder impedimentos, autorizar um ciclo adicional e autorizar merge |
| Revisão do PR | PR, CI, candidato/base, contador externo e achados com evidências e autoridade de encerramento |
| Recuperação | Pausa, interrupção, bloqueio, cancelamento, Retomar ou Reabrir conforme o estado |

Decisões e evidências terão acesso direto, sem depender de procurar mensagens no histórico. Mudança da base retira a indicação de pronto e mostra a necessidade de revalidação. Tester/Manager locais aparecem separados do Codex no GitHub.

Prever teclado, foco visível, estados compreensíveis sem depender de cor, leitura de textos longos, preservação da rolagem e carregamento sob demanda de logs/diffs. A composição deve funcionar em janelas largas e estreitas. Editor de código e terminal completos ficam fora do primeiro escopo. [Briefing detalhado](INTERFACE.md).

## 10. Trello e concorrência

A importação inicial lê título, descrição e checklists a partir do link e relaciona o card à tarefa e ao projeto. Reimportar o mesmo card não inicia outra execução por engano. Guardar a versão do conteúdo usada no plano; alterações posteriores no card não mudam silenciosamente o escopo aprovado.

Tarefas nativas funcionam sem Trello. O piloto não escreve comentários, move cards ou marca conclusão no Trello. Captura automática por labels será acrescentada depois da importação validada, com regras por quadro/label/projeto e a mesma aprovação humana de plano.

Cada tarefa concorrente usa sua própria branch/worktree e recursos de teste isolados: banco, portas, temporários e saídas quando necessários. Uma etapa não pode ser assumida por dois executores. Pausar ou cancelar uma tarefa não afeta a outra; integrações obedecem à serialização e revalidação da base descritas anteriormente.

## 11. Arquitetura recomendada

| Componente | Proposta |
| --- | --- |
| Aplicativo e janela | Electron, com supervisão vinculada ao ciclo de vida da janela |
| Interface | React e TypeScript, sem acesso direto ao shell ou às credenciais |
| Motor | Controlador de estados, fila de duas tarefas, reservas, políticas e reconciliação |
| Provedores | Adaptadores separados de Claude Code e Codex, com sessões, eventos, perguntas, interrupção e retomada |
| Publicação | Controlador exclusivo de Git/GitHub, evidências, revisão externa e autorizações |
| Dados locais | SQLite para registros transacionais; arquivos grandes em armazenamento de artefatos separado |
| Entrada adicional | Adaptador Trello inicialmente de leitura por link |

O estado pertence ao aplicativo, e não somente à conversa de um agente. Os registros incluem projeto, tarefa, plano, execução, candidato, avaliação, achado, decisão, autorização de ciclo e efeito externo. O painel apresenta esse estado; não decide sozinho se uma transição está autorizada.

Versões de dependências e detalhes dos adaptadores serão fixados durante a implementação após as provas de compatibilidade. O desenho completo e as fontes consultadas estão em [ARQUITETURA.md](ARQUITETURA.md) e [EVIDENCIAS.md](EVIDENCIAS.md).

## 12. Marcos e critérios de aceitação

| Marco | Entrega futura | Critério para avançar |
| --- | --- | --- |
| 1. Viabilidade das integrações e controles | Provar assinaturas dos dois provedores, isolamento por papel, revisão externa, proteção de base e ciclo de vida | Autenticação suportada, eventos correlacionados, perguntas/aprovações, interrupção/retomada, publicação bloqueada aos agentes, interpretação verificável do review e concorrência remota tratada |
| 2. Tarefa completa no painel | Projeto cadastrado, pedido, plano, aprovação, implementação, candidato imutável, Tester e Manager | Nenhuma implementação antes da aprovação; evidências pertencem ao candidato; sessões distintas e código protegido durante validação |
| 3. PR, correções e integração | Publicar candidato, acompanhar CI/Codex, achados, contador e autorização de merge | Contagem exclusiva externa, intervenção na terceira, um ciclo por autorização, contestação sem aprovação indevida e rejeição de candidato/base desatualizados |
| 4. Duas tarefas simultâneas | Executar com isolamento de arquivos e testes e controles independentes | Alterar o mesmo arquivo em worktrees distintas sem contaminação; detectar incompatibilidade sem conflito textual e revalidar após a primeira integração |
| 5. Entrada Trello | Importar link e vincular card à tarefa | Título, descrição e checklists preservados; reimportação sem execução duplicada; tarefa nativa funciona sem Trello |
| 6. Recuperação e validação do piloto | Exercitar energia, fechamento, falhas, impasse, cancelamento e reinício sob carga | Ausência de processos órfãos demonstrada, histórico preservado, nenhuma retomada automática, nenhum efeito externo duplicado e estados operacionais respeitados |

Os seis marcos integram o piloto. O primeiro resolve as dependências de viabilidade antes de ampliar a interface; os controles de ciclo de vida são repetidos depois com os dois provedores e duas tarefas reais. Limitação de assinatura, ausência de correlação confiável do review ou proteção remota insuficiente deve produzir resultado explícito da prova, sem presumir sucesso nem trocar silenciosamente os requisitos.

Os [sete ensaios de confiabilidade](CONFIABILIDADE.md) fazem parte dos critérios acima, incluindo escrita tardia, comandos tentando publicar fora do controlador, repetição sem progresso, quarta revisão negativa, contestação indevida e evento remoto em tarefa cancelada.

## 13. Demonstração de conclusão e evolução

O piloto estará concluído quando uma tarefa criada no painel e outra importada do Trello puderem percorrer o fluxo no Bot Financeiro, em paralelo, com planos aprovados, alterações testadas, auditorias, PRs e revisão externa verificáveis. A demonstração deve incluir autorização de merge ligada à validação, mudança de base, fechamento/reabertura, retomada manual e preservação do histórico sem duplicação de efeitos. Resultados de testes controlados e ensaios reais serão identificados separadamente.

Depois da validação local, evoluir a captura do Trello por labels e preparar servidor/equipe: autenticação dos membros, papéis, titularidade das execuções por provedor, repositórios e ambientes no servidor, persistência, backups e operação. A política de execução do servidor será definida nessa etapa, sem presumir que o fechamento de uma janela de um membro encerre o trabalho de todos.

Nome e localização do novo repositório serão definidos na etapa de implementação. Esta entrega consolida o plano; não inicia a criação do aplicativo.

Documentos de apoio: [decisões da entrevista](PLANO.md), [arquitetura](ARQUITETURA.md), [confiabilidade](CONFIABILIDADE.md), [interface](INTERFACE.md), [glossário](CONTEXT.md), [evidências](EVIDENCIAS.md) e ADRs de [repositório próprio](adr/0001-repositorio-proprio.md) e [janela controlando a execução](adr/0002-janela-controla-execucao.md).
