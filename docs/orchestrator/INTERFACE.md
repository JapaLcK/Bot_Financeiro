# Briefing da interface do orquestrador

Planejamento de UX com a skill `impeccable`, em modo **Operate**. Escopo funcional, janela própria com frontend web e comportamento de energia foram aprovados pelo usuário. Este documento orienta a implementação futura; não representa uma interface já implementada nem uma direção visual final escolhida.

A revisão adicional acrescentou identidade do candidato/base, autoria do encerramento de achados e distinções operacionais descritas em [CONFIABILIDADE.md](CONFIABILIDADE.md). Os comportamentos de impasse, ciclo adicional e cancelamento foram confirmados nas decisões 21–23 e integram o [plano final](PLANO_FINAL.md).

## Pessoa, contexto e resultado

Um operador no próprio Mac coordena duas tarefas de desenvolvimento simultâneas. Precisa saber o que cada agente está fazendo, o que precisa de sua decisão e quais evidências permitem confiar no resultado. A equipe passará a acessar o produto depois da validação local e migração para servidor.

O resultado principal é conduzir uma solicitação, criada no sistema ou importada por link do Trello, até um PR revisado pelo time interno, revisado adicionalmente pelo Codex e pronto para autorização humana de merge.

## Estrutura funcional e complementos da revisão

- **Projetos e tarefas:** cadastro de repositórios, criação de tarefa, importação de link do Trello e visão das duas tarefas em execução. A origem da tarefa fica visível e não cria um fluxo de trabalho distinto.
- **Detalhe da tarefa:** conversa e artefatos do trabalho, plano, estado e papel atual, intervenções pendentes, alterações, testes, histórico dos achados e acompanhamento do PR.
- **Decisões:** aprovação de uma versão do plano; perguntas e permissões durante a execução; intervenção na terceira rodada externa com apontamentos; autorização de merge referente à versão revisada.
- **Recuperação:** tarefas pausadas/interrompidas apresentam Retomar quando não há bloqueio pendente. Bloqueadas apresentam a decisão necessária; canceladas não apresentam Retomar. Abrir o painel não recomeça trabalho automaticamente.
- **Janela e energia:** executar somente com a janela aberta; permitir tela apagada mantendo o Mac acordado durante trabalho; ao fechar, mostrar o encerramento local e então terminar o aplicativo.

## Hierarquia proposta

A visão inicial prioriza pendências de decisão e as duas tarefas ativas. O detalhe mantém projeto, título e estado da tarefa identificáveis enquanto o usuário consulta planos longos, alterações ou achados. A conversa fornece contexto; decisões e evidências também têm acesso direto, sem exigir procurar a mensagem certa no histórico.

O fluxo do time aparece como sequência compreensível: Planejamento → Implementação → Testes → Auditoria → Revisão do PR. Mostrar o nome do papel e o provedor separadamente. Tester e Manager locais não devem aparecer como a mesma revisão do Codex no GitHub.

O contador deve se chamar **Rodadas de apontamentos no PR**. Resultados internos possuem histórico próprio e não ocupam esse contador. A segunda rodada externa destaca a consolidação dos achados; a terceira apresenta o bloqueio e a decisão necessária.

Prontidão de merge mostra código candidato e base validada. Se a base avançar, mostrar **Base alterada — nova validação necessária**, sem conservar o estado de pronto. Nos achados, distinguir contestação proposta de aceita e identificar qual revisor confirmou o encerramento.

## Estados que a interface precisa representar

| Situação | Informação e ação |
| --- | --- |
| Primeiro uso | Cadastrar repositório e verificar integrações, com indicação real do que está conectado |
| Sem tarefas | Criar tarefa e importar link do Trello |
| Aguardando plano | Progresso do Arquiteto e possibilidade de interromper |
| Aguardando aprovação | Plano identificado por versão, critérios de aceitação e ação de aprovar ou pedir ajuste |
| Duas tarefas em andamento | Papel/provedor, progresso e controles independentes por tarefa |
| Apontamentos internos | Evidências, correções/contestações e evolução, sem teto numérico imposto à revisão interna |
| Aguardando review no PR | Link do PR, código submetido, CI e estado real da revisão, sem presumir aprovação pelo silêncio |
| Terceira ou posterior rodada externa com problemas | Histórico completo e alcance; ação Autorizar um ciclo adicional, com seu alcance explícito |
| Impasse interno comprovado | Estados/evidências do ciclo ou discordância; pedir orientação sem alterar contador externo |
| Achado contestado | Justificativa, autoridade que deve reavaliar e estado pendente; não usar indicação de resolvido antes da aceitação |
| Base alterada | Identificar base anterior/atual, invalidar prontidão e apresentar revalidação necessária |
| Limite ou falha do provedor | Motivo conhecido, trabalho preservado e próxima ação; consumo indisponível não é exibido como zero |
| Pronto para merge | Evidências do candidato e da base, autoridade dos pareceres e autorização humana separada das aprovações anteriores |
| Encerrando | Interrupção local e persistência em curso; parar novos despachos e encerrar sem uma confirmação adicional |
| Pausada | Pedido de pausa, artefatos preservados e Retomar após reconciliação |
| Interrompida | Fechamento/falha, etapa possivelmente inconclusa e Retomar após reconciliar candidato e efeitos |
| Bloqueada | Causa e ação específica para resolvê-la; Retomar não contorna decisões pendentes |
| Cancelada | Sem Retomar; mostrar artefatos e PR ainda aberto, com ações explícitas de Reabrir ou fechar o PR |

## Conteúdo, leitura e acessibilidade

Português brasileiro. Usar termos do [glossário](CONTEXT.md), rótulos concretos e estados que não dependam só de cor. Interface operável por teclado, foco visível e navegação previsível; progresso não deve mover o foco nem fazer a leitura saltar ao chegar uma mensagem nova. Preservar a rolagem de quem está lendo histórico e oferecer acesso explícito às novidades.

Planos, comentários e achados podem ser extensos; títulos de tarefa, caminhos e nomes de branches podem ultrapassar a largura de uma linha. Tratar essas situações sem ocultar a ação principal. Alterações e logs grandes devem carregar sob demanda, preservando busca e identificação das evidências.

O piloto serve ao uso no Mac. A composição deve funcionar em janelas largas e estreitas; abaixo da largura necessária para comparar duas tarefas lado a lado, oferecer alternância clara sem perder o contexto de cada uma. A implementação web deverá ser verificada em desktop e largura móvel conforme a skill, sem prometer operação remota pelo celular no piloto.

## Direção visual e implementação futura

Priorizar leitura, comparação e decisão, com componentes consistentes e movimento usado para comunicar mudanças de estado. A identidade do PigBank encontrada no contexto do repositório pertence ao produto financeiro e não é uma identidade aprovada para o orquestrador.

Quando a interface for implementada no novo repositório, criar seu contexto visual próprio usando as skills apropriadas, ler o critério de qualidade da `impeccable` antes de editar a UI e verificar os fluxos com dados de demonstração identificados. Realizar uma inspeção visual agrupada e uma confirmação após corrigir os problemas encontrados. Não construir um editor ou terminal completo como parte desse primeiro escopo.
