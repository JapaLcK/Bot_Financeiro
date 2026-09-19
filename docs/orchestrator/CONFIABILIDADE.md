# Contratos de validação, autoridade e interrupção

Este documento detalha os sete apontamentos apresentados pelo usuário após a consolidação inicial. Os itens 1–3 e a autoridade dos revisores fecham lacunas técnicas do plano. As políticas de impasse, autorização adicional e cancelamento foram confirmadas nas decisões 21–23 do [registro do plano](PLANO.md) e integram o [plano final](PLANO_FINAL.md).

## 1. Candidato validado: código e base

Uma validação identifica o repositório, a branch de destino, o commit da base (`base_sha`), o commit candidato (`head_sha`), a árvore de arquivos resultante e a versão dos comandos/configuração de teste. A aprovação humana de merge referencia essa identidade completa.

No piloto, qualquer mudança observada na base invalida a prontidão de integração e a autorização de merge anteriores, mesmo sem conflito de texto. A branch da tarefa incorpora a base corrente antes de gerar um novo candidato. Tester, Manager, CI e review externo avaliam novamente o candidato pertinente; o histórico permanece disponível. Reviews sem apontamentos não incrementam o contador externo.

Duas tarefas podem desenvolver e testar ao mesmo tempo. A integração tem uma reserva exclusiva por repositório e branch de destino. Depois de integrar uma tarefa, a seguinte precisa conferir a nova base e repetir a validação necessária antes de obter nova autorização.

### Concorrência com outros usuários e processos

A reserva local impede concorrência entre integrações do aplicativo, mas não bloqueia mudanças remotas feitas por terceiros. A garantia depende também de proteção no GitHub: no caminho inicial proposto, branch atualizada e checks obrigatórios, sem bypass para a identidade executora nem escrita direta na branch de destino.

O primeiro marco deve provar que uma mudança remota entre a última leitura da base e a chamada de merge não permite integrar um candidato fora da identidade completa autorizada, incluindo a base. A condição `sha` da API de merge cobre somente o head; ler a base antes da chamada não cria uma transação com o GitHub. Proteção com branch atualizada também não comprova, por si só, igualdade atômica de `base_sha`: uma mudança para outro ancestral do candidato pode manter a branch atualizada. Se a configuração disponível não sustentar a garantia, a ação de merge no aplicativo fica indisponível até resolver esse requisito, sem usar push direto como atalho.

Uma merge queue pode ser uma alternativa, condicionada à disponibilidade na conta e ao tratamento do candidato `merge_group`. Não basta habilitar uma fila: reconstituições do grupo podem mudar a base ou a árvore, e as evidências e a autorização devem corresponder ao candidato que será integrado. Essa alternativa exige prova própria antes de substituir o caminho inicial.

## 2. Capacidades efetivas por papel

| Papel ou componente | Leitura e escrita permitidas | Efeitos remotos |
| --- | --- | --- |
| Arquiteto / Claude Code | Ler o projeto e produzir plano/artefatos fora do código | Nenhuma publicação, criação de PR ou merge |
| Coder / Claude Code | Após aprovação do plano, editar código/testes somente na área da tarefa; executar validações restritas | Solicitar ações estruturadas ao controlador, sem executá-las diretamente |
| Tester / Codex | Ler candidato selado; executar testes em ambiente descartável; produzir achados e patches de teste separados | Sem push, PR, merge ou encerramento de achado externo |
| Manager / Codex | Ler candidato, planos e evidências; solicitar verificações restritas e registrar achados | Sem escrita no candidato nem publicação |
| Controlador de publicação | Preparar commits e branches e publicar o candidato autorizado pelas etapas internas | Push e PR após os gates internos; review/comentários rastreáveis; merge somente após validar candidato, base, CI, parecer e autorização humana |

O controlador recebe operações estruturadas, com tarefa, fase e candidato identificados. Não oferece aos modelos um comando genérico executado com as credenciais de publicação. As restrições são aplicadas às ferramentas, ao ambiente de execução, aos caminhos e ao transporte de rede permitido; prompts e nomes de papéis não constituem isolamento.

Os comandos dos papéis e dos testes não herdam credenciais de GitHub/Trello, socket de SSH agent, credential helpers, acesso geral ao Keychain, MCPs/configurações arbitrários ou escrita na área Git compartilhada. Também não podem obter esses acessos lendo outro diretório do usuário nem publicar por HTTP/SDK em substituição ao shell. O Coder não pode alterar a política do controlador, seus registros ou a tarefa vizinha.

A autenticação dos provedores de modelo continua no caminho oficialmente suportado, separada das credenciais de publicação. O primeiro marco deve provar que os dois executores com assinatura permitem essa separação e a restrição efetiva das ferramentas. Se não permitirem, registrar a incompatibilidade e decidir uma alternativa com o usuário, sem afirmar que um prompt ou uma flag resolveu o requisito.

## 3. Código testado, revisado e publicado

1. Ao terminar a implementação, suspender o Coder e encerrar processos capazes de escrever antes de selar o candidato.
2. O controlador examina arquivos rastreados, staged, não rastreados e entradas ignoradas que participam do build/teste. Toda entrada necessária é versionada ou fixada por conteúdo/versão na configuração de validação, com digest ou equivalente quando aplicável; apenas caminho ou nome não basta. Segredos usam referência à versão fornecida pelo mecanismo de credenciais, sem expor seu conteúdo em relatórios. Arquivos privados e resíduos não entram por um `git add` amplo.
3. O controlador cria um commit local de candidato, ainda sem publicar. Seu conteúdo é imutável; alterar arquivos produz outro candidato, não atualiza silenciosamente o anterior.
4. Tester e Manager usam materializações separadas desse candidato. As entradas de código/teste ficam protegidas contra escrita; logs, temporários e saídas declaradas ficam em outros caminhos. Testes que modificam fixtures usam cópias descartáveis, sem substituir as entradas auditadas.
5. No início e no fim de cada etapa, conferir a identidade e as entradas efetivamente usadas. Uma tentativa de modificar o código validado não pode resultar em aprovação, mesmo que o comando retorne sucesso. Uma checagem de hash apenas no final não substitui impedir alterações transitórias durante a execução.
6. O Tester pode propor testes por patch separado. Para incluir o patch no produto, ele retorna ao Coder/controlador, gera novo candidato e percorre novamente testes e auditoria. O Manager propõe achados, sem editar o candidato.
7. Publicar exatamente o commit aprovado internamente, sem gerar outro commit a partir da pasta viva depois das verificações. Qualquer mudança de conteúdo, base ou configuração pertinente invalida as evidências afetadas e a prontidão anterior.
8. Registrar o método e o resultado do merge. Squash ou merge commit pode produzir outro identificador de commit; o conteúdo final precisa corresponder à árvore validada, sem introduzir alterações adicionais.

## 4. Detectar falta de progresso sem impor teto interno

Tester e Manager continuam sem limite de rodadas por contagem. O histórico registra a cada avaliação os achados abertos, a solução candidata, evidências novas e avanços verificáveis.

Decisão 21 confirmada: sem progresso, pausar a tarefa. Registrar a causa como `bloqueada_por_impasse` quando ficar comprovado que o mesmo estado de achados/solução reapareceu sem evidência nova, que houve reversão A → B → A sem justificativa nova, ou que dois revisores exigem comportamentos incompatíveis. Esses são exemplos verificáveis de falta de progresso; número de rodadas, texto parecido, duração isolada ou espera legítima de teste não bastam para declarar impasse.

Uma única reincidência comprovada pode disparar a pausa, apresentando os estados comparados e o alcance das mudanças. O usuário orienta a próxima hipótese, esclarece o critério ou revê o plano. O bloqueio não é contornado por Retomar genérico nem altera o contador externo.

## 5. Autorização depois da terceira rodada externa

Decisão 22 confirmada: cada autorização libera uma resposta ao lote externo que originou a intervenção e uma nova avaliação externa concluída, passando antes pelas verificações internas necessárias. A autorização é registrada por tarefa e lote, não é transferível e não equivale a aprovação de plano novo nem de merge.

Falhas de rede e entrega duplicada não consomem outra autorização. O controlador reserva a autorização para o ciclo e registra seu andamento; depois de reiniciar, retoma esse mesmo ciclo em vez de liberar um segundo por engano.

Se a avaliação adicional for favorável, o fluxo segue para os demais requisitos de integração. Se a quarta, quinta ou outra rodada externa adicional trouxer problemas, volta a exigir intervenção antes da resposta seguinte. O contador histórico permanece em 4, 5 etc.; nunca volta a zero.

## 6. Achados, contestação e autoridade de encerramento

Estados de um achado: `aberto`, `correcao_proposta`, `contestado`, `aguardando_reavaliacao`, `resolvido`, `contestacao_aceita` e `duplicado`. Duplicado exige vínculo ao achado principal. Reaberturas preservam o identificador e registram a nova evidência e o candidato relevante.

| Origem do achado | Quem propõe resposta | Quem valida o encerramento |
| --- | --- | --- |
| Tester | Coder ou responsável pela investigação | O papel Tester, em reavaliação registrada |
| Manager | Coder/Arquiteto conforme a questão | O papel Manager, em reavaliação registrada |
| Codex no PR | Time interno prepara resposta; controlador publica | Codex na revisão externa, com evidência verificável de aceitação ou resolução |

O Coder não encerra os achados que está contestando ou corrigindo. O Manager pode mediar uma discordância com o Tester, mas sua opinião não fabrica aprovação do Tester. Resolver uma conversa no GitHub, a concordância interna com uma contestação ou o silêncio do revisor externo não constituem validação externa.

A identidade do papel revisor e a evidência ficam registradas, mesmo se a sessão original tiver terminado. O mecanismo que relaciona uma nova avaliação externa aos achados anteriores precisa ser comprovado no primeiro marco; ausência de correlação confiável mantém o achado pendente.

Discordância persistente bloqueia para orientação humana sobre evidência, abordagem ou escopo. O usuário pode pedir investigação, ajustar o plano ou cancelar. Essas decisões não convertem parecer externo desfavorável em aprovação e não dispensam a validação do Codex. Aprovação antiga conserva valor histórico, mas não valida automaticamente outro candidato.

## 7. Estados operacionais e destino dos artefatos

| Condição | Efeito local | Continuação e artefatos |
| --- | --- | --- |
| Pausada | Pedido do usuário interrompe execução e novos despachos | Retomar manual após reconciliar; preservar branch, worktree, histórico e PR |
| Interrompida | Fechamento, falha, reinício ou perda de sessão deixam uma etapa possivelmente inconclusa | Retomar manual depois de verificar efeitos incertos e evidências; preservar arquivos |
| Bloqueada | Existe decisão pendente, impasse ou requisito não atendido | Oferecer a ação que resolve a causa; Retomar não ignora a condição |
| Cancelada | Intenção de abandonar; tarefa sai do agendamento e não aceita novos despachos | Estado terminal para execução; sem Retomar. Reabrir tarefa é decisão explícita, cria novo episódio e reapresenta o plano após reconciliação |
| Concluída | Integração confirmada com todos os requisitos | Somente histórico; novos pedidos criam outra tarefa |

Decisão 23 confirmada: cancelar preserva arquivos e o PR aberto, com o aviso **Tarefa cancelada — PR ainda aberto**. Uma ação separada **Cancelar e fechar PR** explicita o efeito remoto e registra o resultado, inclusive se o fechamento falhar. Não excluir branches nem worktrees automaticamente; limpeza é uma ação posterior identificada.

Reabrir não apaga o cancelamento, o histórico de apontamentos ou as decisões. Exige confirmar o escopo/plano vigente; PR ainda aproveitável pode ser reconciliado, e qualquer PR novo mantém vínculo ao anterior. Reabrir ou trocar o PR não serve para zerar uma intervenção externa pendente.

CI e review já aceitos remotamente podem terminar após pausa ou cancelamento. Seus eventos atualizam o histórico, mas não reativam a tarefa. Se a reconciliação mostrar que o merge já ocorreu, registrar a integração: cancelar não implica desfazer código publicado. Cancelamento solicitado durante merge bloqueia novos despachos e aguarda identificar o resultado da operação em curso.

## Critérios de aceitação acrescentados

| Apontamento | Ensaio necessário |
| --- | --- |
| 1. Base e serialização | Duas mudanças incompatíveis sem conflito textual: após integrar uma, a outra perde prontidão. Alterar base remotamente entre verificação e tentativa de merge e comprovar rejeição no servidor; testar dois controladores disputando a mesma integração |
| 2. Capacidades | Por cada papel e por um comando de teste: tentar push, merge por CLI/HTTP, leitura de credenciais, alteração de políticas e escrita em outra tarefa; operações devem ser impedidas fora dos prompts |
| 3. Imutabilidade | Modificação tardia por processo filho, atualização de fixture, arquivo untracked necessário, patch novo de teste e mudança durante publicação não podem herdar aprovação do candidato anterior |
| 4. Impasse | Ciclo A → B → A bloqueia com provas; investigação longa com evidência nova continua sem teto; nenhuma avaliação interna incrementa o contador externo |
| 5. Extensão | Autorizar depois da terceira libera um ciclo; quarta negativa bloqueia novamente; reinício e reenvio não duplicam nem perdem a autorização |
| 6. Contestação | Manager aceitar contestação externa não libera merge; fechar thread não encerra achado sozinho; reavaliação do papel competente registra o encerramento |
| 7. Cancelamento | Cancelada não mostra Retomar e não acorda por evento remoto; PR preservado fica sinalizado; falha ao fechar PR é visível; reabertura e merge já ocorrido são reconciliados sem apagar histórico |

Esses ensaios fecham o contrato do plano. Eles não foram executados nesta etapa de documentação.
