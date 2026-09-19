# Orquestração de desenvolvimento

Vocabulário do sistema para solicitar e acompanhar alterações de código executadas por agentes. O piloto é operado por uma pessoa; o uso compartilhado pela equipe é uma etapa posterior.

## Linguagem

**Orquestrador**:
Coordenador do trabalho dos agentes de desenvolvimento, da solicitação de uma alteração até sua apresentação para revisão.

**Agente de desenvolvimento**:
Agente que participa do planejamento, da implementação, da verificação ou da revisão de alterações de código.
_Evitar_: agente financeiro, agente do Piggy.

**Tarefa**:
Solicitação de alteração de código criada no painel ou importada de uma origem externa e acompanhada até a revisão do resultado.

**Origem da tarefa**:
Canal pelo qual uma solicitação entra no orquestrador: criação no painel ou importação de um card do Trello.

**Card de origem**:
Card do Trello vinculado à tarefa importada, usado para identificar de onde veio o pedido.

**Equipe**:
Grupo de pessoas que utiliza o orquestrador para solicitar e acompanhar tarefas de desenvolvimento.
_Evitar_: time de agentes, quando a referência for às pessoas.

**Aprovação do plano**:
Decisão humana que permite começar a implementação da tarefa conforme o plano apresentado.
_Evitar_: autorização de merge.

**Validação do Codex**:
Parecer favorável do Codex na revisão do PR, adicional às verificações do time interno e exigido para que a alteração possa ser integrada.
_Evitar_: testes passaram, aprovação humana.

**Revisão interna**:
Verificação realizada pelo Tester e auditoria realizada pelo Manager antes de enviar a alteração para revisão no PR.

**Revisão do PR**:
Avaliação adicional do Codex no GitHub, realizada depois de a alteração passar pelo time interno.
_Evitar_: revisão interna.

**Histórico de achados**:
Registro da evolução dos problemas apontados, das respostas a cada um e das evidências de correção ou contestação desde o início das revisões.

**Rodada de apontamentos do PR**:
Avaliação externa do Codex no PR que contém apontamentos a tratar; os comentários de uma mesma avaliação constituem uma rodada. Revisões do Tester e do Manager não entram nessa contagem.

**Retomada**:
Continuação explicitamente solicitada pelo usuário de uma tarefa pausada ou interrompida, a partir do estado e dos artefatos preservados e sem ignorar bloqueios pendentes.

**Candidato de integração**:
Versão preparada da alteração e da base de destino que será submetida a testes, auditoria e autorização de integração.

**Base validada**:
Versão do projeto de destino considerada ao avaliar a compatibilidade do candidato de integração.

**Contestação**:
Resposta fundamentada que questiona um achado; permanece distinta da aceitação dessa resposta pelo revisor competente.

**Impasse de revisão**:
Situação em que as revisões repetem soluções sem avanço verificável ou exigem comportamentos incompatíveis, necessitando de orientação.

**Pausa**:
Parada recuperável do trabalho solicitada pelo usuário, preservando a intenção de continuar a tarefa.

**Interrupção**:
Parada causada por fechamento, falha ou perda da execução, que deixa trabalho a reconciliar antes de continuar.

**Cancelamento**:
Decisão de abandonar a execução de uma tarefa; não representa aprovação da alteração nem reversão de código já integrado.

**Reabertura**:
Decisão explícita de voltar a trabalhar numa tarefa cancelada, conservando seu histórico e reapresentando o escopo para aprovação.

**Autorização de merge**:
Decisão do usuário que permite integrar a alteração revisada, condicionada à validação do Codex.
_Evitar_: aprovação do plano.
