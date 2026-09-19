# Plano do orquestrador

Status: as 23 decisões estão confirmadas, incluindo os sete ajustes de confiabilidade. Este documento preserva o registro da entrevista; a versão consolidada para leitura está no [plano final](PLANO_FINAL.md). Por instrução expressa do usuário, a implementação não deve ser iniciada nesta etapa.

## Resultado do piloto

Um aplicativo para Mac, em repositório próprio, com frontend web numa janela desktop. O usuário cria tarefas no painel ou importa links do Trello, aprova seus planos e acompanha duas tarefas simultâneas. Claude Code planeja e implementa; Codex testa e audita em sessões distintas; o PR recebe uma revisão adicional do Codex no GitHub. A integração do código exige CI verde, parecer favorável e autorização do usuário vinculados ao candidato atual e à base validada.

O aplicativo só executa trabalho local com o painel aberto. Pode manter o Mac acordado com a tela apagada durante a execução. Fechar a janela encerra os agentes; reiniciar o aplicativo restaura o histórico sem executar trabalho automaticamente. Retomar fica restrito às tarefas pausadas/interrompidas sem bloqueio pendente.

Documentos complementares: [arquitetura e marcos](ARQUITETURA.md), [contratos de confiabilidade](CONFIABILIDADE.md), [interface](INTERFACE.md), [glossário](CONTEXT.md), [evidências técnicas](EVIDENCIAS.md) e [decisão sobre o ciclo de vida](adr/0002-janela-controla-execucao.md).

## Objetivo confirmado

Planejar um sistema inspirado na função de orquestração do Scape, com frontend para acompanhar e dirigir agentes de desenvolvimento, aproveitando o processo já descrito no Bot Financeiro.

Este diretório guarda os documentos de planejamento durante a entrevista. O aplicativo terá repositório próprio e gerenciará repositórios locais cadastrados, começando pelo Bot Financeiro. Nome e localização física do novo repositório serão definidos quando houver solicitação de implementação.

## Base existente

- [`time-dev`](../../.claude/commands/time-dev.md): coordenação por instruções dos papéis Arquiteto, Coder, Tester e Manager; passagem de contexto entre etapas; ciclo de correção e revisão; escalada após três rodadas com bloqueios.
- [Definições dos agentes](../../.claude/agents/): responsabilidades, ferramentas permitidas e critérios de avaliação de cada papel.
- [`CLAUDE.md`](../../CLAUDE.md): regras do projeto, validação e referências a aprendizados persistidos em documentação.
- Skills locais de [triagem](../../.agents/skills/triage/SKILL.md), [criação de tickets](../../.agents/skills/to-tickets/SKILL.md) e [handoff](../../.agents/skills/handoff/SKILL.md): procedimentos que podem informar o desenho, sujeitos à verificação de configuração e compatibilidade.

Esses arquivos descrevem comportamento esperado. Sua presença não comprova um processo ativo, um agendador de desenvolvimento nem recuperação automática de execuções.

O [agendador de insights](../../core/services/proactive_ai_scheduler.py) e os [agentes financeiros](../../db/agents.py) pertencem ao produto Bot Financeiro. O glossário existente na raiz descreve esse domínio; o vocabulário do orquestrador está no [glossário próprio](CONTEXT.md).

## Decisões confirmadas — primeira rodada

1. **Público:** uso compartilhado com a equipe como destino do produto; o piloto será de uso individual, conforme a segunda rodada.
2. **Primeiro resultado completo:** descrever uma tarefa no painel e receber uma alteração testada para revisar.
3. **Local de execução:** começar pelo Mac; existe intenção de migrar a execução para um servidor posteriormente.
4. **Acesso aos modelos:** priorizar as assinaturas existentes de Claude Code e Codex. Ambos precisam estar presentes na primeira versão.

O acesso das outras pessoas e a titularidade das execuções compartilhadas pertencem à futura etapa de servidor. No piloto, apenas o usuário opera o sistema no próprio Mac.

## Decisões confirmadas — segunda rodada

5. **Uso inicial:** somente o usuário acessa a versão no Mac. Após validar o piloto, a intenção é hospedar o sistema em servidor e abrir acesso à equipe.
6. **Revisão do plano da tarefa:** o sistema aguarda aprovação humana do plano antes de implementar. Depois disso, código, testes e revisão podem avançar até o resultado ou um bloqueio.
7. **Entrega para revisão:** PR em rascunho no GitHub, com alterações, testes e resumo; o fluxo inclui criação de branch, commits e push. Merge exige autorização do usuário e validação pelo Codex no PR, além do CI verde previsto no processo do projeto. Mensagens de commit seguem PT-BR.
8. **Provedores da primeira versão:** Claude Code e Codex desde o início, com a divisão de papéis confirmada na terceira rodada.

## Fluxo já acordado

1. O usuário cria uma tarefa no painel ou importa um card do Trello.
2. Claude Code, como Arquiteto, prepara um plano; o sistema aguarda a aprovação do usuário.
3. Claude Code, como Coder, implementa a alteração.
4. O controlador sela um candidato imutável ligado ao código e à base. Codex, como Tester, verifica esse candidato; Codex, como Manager, audita o trabalho em uma sessão separada.
5. Após passar pelo time inteiro, o sistema prepara o PR em rascunho com as evidências de teste.
6. O Codex faz uma revisão adicional no próprio PR do GitHub. Achados retornam para verificação, correção ou contestação fundamentada e nova avaliação.
7. A integração da alteração depende da validação do Codex no PR, CI verde e autorização do usuário.

O painel oferece autorização explícita de merge para o candidato revisado e sua base. O controlador, único componente com capacidade de publicar e integrar, verifica condições locais e proteções remotas no momento da ação. O contrato de detecção do parecer do Codex e a rejeição de base superada serão comprovados no primeiro marco. A decisão de exigir autorização de merge no produto não equivale a autorizar merges nesta conversa.

## Limites do piloto confirmados

- Um operador no Mac; colaboração remota fica para a etapa de servidor.
- Ambos os provedores fazem parte da primeira entrega funcional.
- Revisão humana do plano e autorização humana de merge são pontos distintos.
- A validação do Codex é uma condição adicional à autorização humana de merge.
- Duas tarefas simultâneas fazem parte do piloto, inclusive para verificar o próprio funcionamento da concorrência.
- Criação de tarefas no painel e importação do Trello são entradas complementares.
- A interface web local será apresentada em uma janela própria de aplicativo no Mac, com execução vinculada à existência dessa janela.

## Decisões confirmadas — terceira rodada

9. **Divisão dos papéis:** Claude Code como Arquiteto e Coder; Codex como Tester e Manager, em sessões separadas.
10. **Revisão final do Codex:** depois de passar pelo time inteiro, a alteração ainda precisa de revisão adicional do Codex no PR do GitHub. A ativação e o comportamento da integração, especialmente em PRs em rascunho, precisam de validação técnica.
11. **Correções e investigação permanente:** desde o primeiro apontamento, enumerar os erros e sua evolução, verificar o alcance da mudança no código e investigar os comportamentos e caminhos relacionados. Essa investigação é permanente, e não uma ação que começa somente após duas rodadas. O limite de intervenção humana foi esclarecido na decisão 15: três rodadas de apontamentos do Codex no PR, sem limitar as revisões internas por número de rodadas.
12. **Concorrência no piloto:** duas tarefas em execução simultânea, para validar o funcionamento do sistema em paralelo, com cópias de trabalho isoladas.
13. **Escopo de repositórios:** aplicativo em repositório próprio, capaz de cadastrar repositórios locais, tendo o Bot Financeiro como primeiro projeto de validação. [Decisão registrada](adr/0001-repositorio-proprio.md).

## Decisão confirmada — entradas de tarefas

**Decisão 14:** permitir criação de tarefas no próprio painel e usar o Trello como entrada adicional. O Trello não substitui o cadastro de tarefas no sistema nem é requisito para trabalhar com tarefas nativas.

As duas entradas levam ao mesmo processo de aprovação, execução, teste e revisão. Uma tarefa importada mantém seu vínculo com o card de origem; o histórico de execução pertence ao orquestrador.

Fluxo da importação: selecionar card → associar ao repositório → gerar plano → obter aprovação do usuário → implementar e testar → preparar PR → obter validação do Codex → aguardar autorização de merge.

Escopo inicial confirmado e regras propostas para a integração:

- Importação manual por link no piloto, com título, descrição e checklists. Inclusão de comentários e conteúdo dos anexos tem escopo próprio.
- Captura automática por labels é uma evolução futura confirmada. A combinação de quadro, labels e repositório será configurada nessa etapa. Um label de entrada não substitui a aprovação do plano.
- Relação persistente entre card e tarefa para que sincronizações não iniciem trabalho duplicado.
- Registro do conteúdo usado para gerar o plano; alterações posteriores no card precisam ser avaliadas antes de mudar o escopo aprovado.
- O Trello guarda o pedido; o orquestrador guarda execuções, decisões e evidências. Evitar duas filas independentes com estados conflitantes.
- Leitura no piloto. Escrever comentários com o link do PR, mover cards e marcar conclusão seriam capacidades explicitamente definidas em etapa posterior.
- Para captura automática no Mac, consultas periódicas são uma alternativa a receber eventos num endereço público. No servidor, avaliar webhooks e reconciliação periódica.

## Decisões confirmadas — quarta rodada

15. **Contagem de rodadas:** contar somente rodadas de apontamentos do Codex no PR. Tester e Manager podem apontar e provocar novas verificações quantas vezes forem necessárias, sem consumir esse contador. A investigação do alcance é permanente; o histórico é consolidado ao segundo ciclo externo e a terceira rodada externa com problemas requer intervenção humana.
16. **Interface do piloto:** painel web local com visão das duas tarefas e detalhe de cada tarefa com conversa, plano, aprovações, alterações, testes, histórico de achados e acompanhamento do PR. Usar skills apropriadas de interface; `impeccable` orienta o briefing e a implementação futura. Editor de código e terminal interativo completos ficam para uma etapa posterior, caso necessários.
17. **Alcance inicial do Trello:** começar pela importação de links. Depois, implementar captura automática dos cards em que o sistema deve atuar a partir dos labels.
18. **Fechamento e retomada:** fechar o painel encerra o sistema e as execuções locais. Desligamento e reinício preservam arquivos e histórico; ao abrir novamente, o usuário terá um botão Retomar. O funcionamento com tela apagada foi esclarecido e confirmado na decisão 19.

## Decisões confirmadas — comportamento do Mac

19. **Significado de dormente:** continuar com tela apagada ou sessão bloqueada, mantendo o Mac acordado durante o trabalho. Impedir somente a suspensão por inatividade enquanto o painel estiver aberto e houver execução; liberar esse bloqueio ao encerrar. Suspensão real pausa processos locais; funcionamento geral com tampa fechada não está prometido.
20. **Ciclo de vida do painel:** frontend web numa janela própria de aplicativo no Mac, com supervisão das execuções ligada ao fechamento dessa janela. Primeiro encerrar o trabalho local e salvar o estado, depois fechar a janela. CI e revisões já enviados ao GitHub podem continuar remotamente.

## Política de apontamentos e correção

O contador de três rodadas é exclusivo da revisão externa do Codex no PR, conforme decisão 15. Essa regra substitui a proposta anterior de somar revisores internos e externos.

- Uma avaliação externa concluída do Codex no PR com apontamentos acionáveis corresponde a uma rodada. O primeiro parecer externo com apontamentos é a rodada 1. Vários comentários do mesmo parecer pertencem à mesma rodada; entregas duplicadas do evento não incrementam novamente.
- Tester e Manager possuem histórico próprio, sem teto por quantidade de revisões. Podem devolver trabalho ao Coder e reavaliar até passar pelo time inteiro; isso não incrementa nem reinicia o contador externo.
- Correções decorrentes do PR passam novamente pelas verificações internas pertinentes antes de solicitar o próximo review externo.
- Espera de resposta, review sem apontamentos, falha de comunicação e limite de uso não criam uma rodada de apontamentos. Parecer antigo é preservado como histórico e não valida código novo.
- Reiniciar o aplicativo, trocar sessão ou enviar novos commits não zera o contador de apontamentos do PR. A decisão 22 determina que uma autorização após escalada libere um ciclo adicional, preservando a contagem e o histórico anteriores.
- Todos os achados conservam identificador, origem, versão revisada, evidência, resposta, estado atual e relação com as correções.
- Em todo lote, investigar a classe do problema, chamadores e consumidores afetados, caminhos vizinhos, casos semelhantes e risco de regressão; registrar o que foi ou não verificado.
- Ao segundo parecer externo com apontamentos, consolidar achados corrigidos, pendentes, contestados e introduzidos pelas próprias correções. A investigação de alcance já ocorre desde o primeiro achado, também nas revisões internas. Quando houver repetição no mesmo subsistema, enumerar estados e eventos aplicáveis.
- Ao terceiro parecer externo com problemas ainda a resolver, pausar e chamar o usuário com o histórico completo e o alcance da mudança antes de iniciar outra resposta automática. Parecer favorável, sem apontamentos acionáveis pendentes e com os demais critérios cumpridos, leva ao pedido de autorização de merge.
- Ausência de teto interno não autoriza continuar após fechar o painel, ignorar falha de autenticação, consumir API como alternativa não autorizada ou ultrapassar o escopo aprovado. Essas condições são estados operacionais distintos do contador de reviews.
- Investigar casos relacionados não autoriza ampliar silenciosamente o escopo do plano aprovado.

Os [contratos de confiabilidade](CONFIABILIDADE.md) definem quem pode encerrar cada achado e como detectar impasse sem introduzir teto nas revisões internas. Contestação registrada não equivale a contestação aceita, e concordância do time interno não substitui o parecer externo do Codex.

## Ajustes técnicos após os apontamentos adicionais

1. **Base e código:** validar o candidato com o commit da base, o head e a árvore testada. Mudança na base invalida prontidão e autorização anteriores, mesmo sem conflito textual. Serializar integrações por repositório/branch e comprovar proteção também contra mudanças remotas.
2. **Capacidades:** aplicar permissões a ferramentas e ambiente de cada papel. Somente o controlador prepara commits, publica, abre PRs e executa merges após verificar as condições; os modelos não herdam suas credenciais.
3. **Imutabilidade:** testar e auditar materializações do candidato selado. Mudanças de código, testes ou entradas relevantes geram outro candidato. Um patch de teste proposto pelo Tester volta ao fluxo de implementação e validação.
4. **Autoridade de achados:** Coder propõe resposta; o papel que apontou valida o encerramento. Achado externo precisa de reavaliação verificável do Codex no PR. Discordância persistente exige orientação humana, sem dispensa automática de revisão externa.

Cada ajuste tem contrato e ensaio de aceitação em [CONFIABILIDADE.md](CONFIABILIDADE.md). Nada disso comprova implementação ou autoriza mudar permissões e configurações de contas nesta etapa.

## Decisões operacionais confirmadas — 21 a 23

21. **Impasse interno:** conforme confirmado pelo usuário, sem progresso, pausar. Permitir rodadas ilimitadas enquanto houver progresso verificável; reincidência comprovada sem evidência nova, reversão de solução sem justificativa nova ou exigências incompatíveis são exemplos de impasse. Exibir as provas e pedir orientação; o contador externo não muda.
22. **Continuação após a terceira externa:** cada autorização libera uma resposta aos achados e uma nova avaliação externa concluída. Se a quarta ou outra avaliação adicional voltar com problemas, pedir intervenção novamente antes da resposta seguinte. Não zerar histórico nem contador.
23. **Cancelamento e reabertura:** cancelar abandona a tarefa local e não oferece Retomar. Preservar arquivos e PR, exibindo quando ele continua aberto; oferecer ação separada para também fechar o PR. Reabrir é decisão explícita, com reconciliação e nova aprovação do plano vigente. Pausa e interrupção continuam recuperáveis por Retomar quando não houver bloqueio pendente.

## Critérios técnicos para a revisão

- O resultado de uma revisão precisa ser relacionado ao candidato imutável, à base e às entradas/comandos de validação pertinentes.
- Novo commit, mudança de conteúdo ou base exigem novo candidato e reavaliação dos resultados anteriores.
- A ausência de apontamentos ou a falta de resposta do revisor não é uma aprovação por si só.
- Respostas do Codex devem ser verificadas antes de atribuir um estado favorável; procurar uma frase em texto não basta como contrato de integração.
- A autorização humana de merge referencia o candidato e a base efetivamente revisados; mudanças posteriores não herdam essa autorização. Um lock local e a condição de head da API não bastam para provar ausência de corrida com alterações externas.
- No Bot Financeiro, o fluxo já documentado exige Tester e Manager antes do push e CI verde antes de considerar o PR pronto.

Esses critérios comprovam as condições escolhidas pelo usuário e precisam aparecer nos testes da integração, inclusive contra mudança de código entre a autorização e a execução do merge.

## Cobertura das decisões e validações de implementação

As escolhas originais e as três políticas operacionais adicionais foram confirmadas. O usuário solicitou somente a entrega do plano final, sem iniciar implementação.

| Área | Definição e validação |
| --- | --- |
| Operação no Mac | Janela própria, encerramento local ao fechar, tela apagada durante trabalho e Retomar explícito |
| Entradas | Criação nativa e importação de link Trello, ligadas ao mesmo fluxo |
| Provedores | Dois provedores com assinaturas existentes; provar autenticação, eventos, decisões e retomada |
| Workflow | Quatro papéis, aprovação humana do plano, revisões internas sem teto e limite externo de três rodadas com apontamentos |
| Entrega | PR do candidato aprovado internamente, review externo e CI vinculados a código/base; merge autorizado separadamente |
| Concorrência | Duas tarefas, reservas exclusivas, isolamento de arquivos/testes e uma integração por branch de destino, com proteção remota |
| Memória | Estado e histórico duráveis; reconstruir o que ocorreu antes de repetir efeitos externos |
| Condições operacionais | Pausa/cancelamento pelo usuário; falha de autenticação, quota ou indisponibilidade visíveis e recuperáveis, sem troca automática para API paga |
| Evolução | Labels do Trello e servidor/equipe após validar o piloto |

O [desenho de arquitetura e marcos](ARQUITETURA.md) detalha a construção e a proposta técnica de Electron, TypeScript, React e SQLite. O [briefing da interface](INTERFACE.md) define a experiência e seus estados. Versões de dependências e detalhes internos serão fixados na implementação, com as provas de compatibilidade do primeiro marco.

## Prova de conclusão do piloto

Criar uma tarefa no painel e importar outra por link do Trello, ambas no Bot Financeiro. Aprovar os planos e executar as duas simultaneamente, com sessões e ambientes isolados. Demonstrar as revisões internas, PRs, revisão externa do Codex, contador de apontamentos e autorização de merge por versão. Durante o ensaio, fechar e reabrir o aplicativo, comprovar que os processos locais terminaram e retomar sem perder histórico ou duplicar PRs. Validar também a execução com tela apagada, a interrupção forçada e a indisponibilidade de um provedor.

Nenhuma integração é considerada comprovada por prompts ou screenshots simulados. O relatório de validação distingue testes automatizados com executores controlados de ensaios reais dos provedores e do GitHub.

## Evidências técnicas

O [levantamento técnico](EVIDENCIAS.md) registra o inventário do repositório e as interfaces oficiais candidatas, consultados em 2026-09-13. Nenhum agente real foi iniciado para validar a integração.

- `time-dev` pode fornecer o workflow inicial, mas precisa de implementação executável com estado persistente.
- As skills de tickets e triagem estão presentes localmente, sem configuração de um executor demonstrada.
- O `time-dev` atual limita Coder/Tester; o usuário escolheu uma regra diferente para o novo produto: revisões internas sem teto de rodadas e contador exclusivo dos apontamentos externos no PR. Reaproveitar o workflow exige essa adaptação explícita.
- Remover também a exceção do `time-dev` que dispensa aprovação de planos triviais e a interpretação de revisar somente as linhas modificadas: todo plano requer aprovação e cada apontamento exige investigação do entorno e do histórico.
- A escolha de assinatura não elimina limites de uso nem resolve a titularidade das execuções da equipe.
- O primeiro marco técnico deve provar progresso, aprovação, cancelamento e recuperação nos dois provedores, antes de ampliar o frontend.

## Documentos entregues

- [Plano final](PLANO_FINAL.md): especificação consolidada para leitura e revisão.
- Este documento: escopo e registro das decisões da entrevista.
- [Arquitetura e marcos](ARQUITETURA.md): desenho técnico, fases e critérios de aceitação.
- [Contratos de confiabilidade](CONFIABILIDADE.md): tratamento dos sete apontamentos, autoridades, estados e ensaios adicionais.
- [Interface](INTERFACE.md): tarefas, estados, hierarquia e critérios de UX.
- [Glossário do orquestrador](CONTEXT.md): apenas termos acordados e suas definições.
- ADRs: [repositório próprio](adr/0001-repositorio-proprio.md) e [janela controlando a execução](adr/0002-janela-controla-execucao.md).

O escopo original está preservado; as decisões 21–23 fecham as políticas levantadas pela revisão adicional. A implementação é uma etapa posterior; estes documentos não criaram o aplicativo, um repositório novo, automações ou integrações nas contas do usuário.
