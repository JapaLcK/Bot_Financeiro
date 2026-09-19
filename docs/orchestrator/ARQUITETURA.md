# Arquitetura e marcos de implementação

Este desenho deriva das 23 decisões confirmadas no [registro do plano](PLANO.md) e incorpora os sete apontamentos posteriores. O [plano final](PLANO_FINAL.md) consolida a entrega; os [contratos de confiabilidade](CONFIABILIDADE.md) detalham os requisitos de base, capacidades, imutabilidade, autoridade e políticas operacionais confirmadas. Não há implementação nem integração ponta a ponta validada.

## Tecnologias recomendadas

| Tecnologia | Motivo e limite |
| --- | --- |
| Electron | Janela própria com frontend web, eventos do aplicativo e controle temporário de energia. Exige comprovar a supervisão de todos os processos; o framework sozinho não oferece essa garantia |
| TypeScript | Usar a mesma linguagem no controle local e nos contratos da interface; manter os dados que atravessam os processos validados em execução |
| React | Construir a interface de tarefas, decisões e evidências com partes reutilizáveis; manter a experiência web separada das capacidades desktop |
| SQLite | Persistir estado e histórico localmente em transações, sem um servidor de banco no piloto; versionar migrações e separar arquivos grandes do banco |

Escolher e fixar versões mantidas na implementação, depois de validar as dependências nativas no Electron. Essa stack é uma proposta de implementação, não uma afirmação de que dependências já foram instaladas. [Fundamentos consultados](EVIDENCIAS.md).

## Estrutura do aplicativo

| Parte | Responsabilidade |
| --- | --- |
| Painel | Cadastro de projetos e tarefas, importação do Trello, acompanhamento, aprovação do plano, análise dos achados e autorização de merge |
| Supervisor e execução | Ciclo de vida vinculado ao painel, fila, duas tarefas simultâneas, etapas do time, contador externo e recuperação |
| Integração Claude Code | Sessões de Arquiteto e Coder, progresso, resultado, perguntas, permissões, interrupção e retomada suportada |
| Integração Codex local | Sessões separadas de Tester e Manager, progresso, achados, parecer, interrupção e retomada suportada |
| Controlador de publicação / GitHub | Preparação dos candidatos/commits, publicação autorizada, CI, revisão externa, threads e integração do candidato validado |
| Integração Trello | Entrada adicional de tarefas, vínculo por card e controle de reimportação |
| Armazenamento | Tarefas, planos e versões, execuções, decisões humanas, sessões, achados, eventos e artefatos |
| Cópias de trabalho e testes | Branch/worktree por tarefa e configuração isolada dos recursos de teste |

O processo principal controla a janela e o início/encerramento do supervisor. O frontend React comunica intenções por uma interface pequena e validada; não executa comandos de shell nem acessa credenciais diretamente. As sessões dos agentes e seus processos descendentes pertencem ao supervisor, que coordena duas tarefas e persiste os eventos.

O contêiner é uma janela desktop com frontend web, escolha confirmada pelo usuário. Fechar normalmente primeiro interrompe e encerra o trabalho local. A implementação deve encerrar o aplicativo ao fechar essa janela, inclusive no macOS; não herdar o comportamento de exemplos que deixam o processo principal aberto no Mac.

Usar isolamento do contexto web, frontend sem Node.js direto e comunicação com validação de origem e parâmetros. Exibir conteúdo vindo de cards, logs e comentários como dados, sem permitir que esse conteúdo invoque capacidades de execução. As credenciais dos provedores ficam no mecanismo de autenticação suportado por eles; o painel recebe somente estado de conexão e capacidades necessárias.

O acesso fica local nesta fase. Exposição ao servidor, identidade de múltiplos usuários e controles de acesso serão um marco separado; publicar o processo local na internet não constitui essa migração.

O orquestrador controla as transições e comprova suas condições. A saída do modelo é uma evidência a interpretar; não substitui autorização humana nem altera, por si só, as regras do fluxo.

A [matriz de capacidades](CONFIABILIDADE.md) é aplicada às ferramentas e ao ambiente de cada papel. Coder edita a própria tarefa após aprovação; Tester avalia candidato selado e propõe testes separadamente; Manager lê e audita. Somente o controlador prepara commits e executa push, criação de PR e merge. Shell, HTTP, helpers, sockets e credenciais herdadas não podem fornecer aos papéis um caminho alternativo para essas ações.

## Dados e transições

| Registro | Informação essencial |
| --- | --- |
| Projeto | Repositório local/remoto, referência base, comandos de validação e configuração de isolamento |
| Tarefa | Pedido, origem, projeto, critérios de aceitação e versão do escopo |
| Plano | Conteúdo versionado, referências e decisão de aprovação |
| Candidato de integração | Base, head, árvore resultante, versão dos comandos/configuração de validação e estado selado |
| Execução e etapa | Estado, papel, provedor, sessão exata, branch/worktree e tentativas |
| Decisão | Pergunta ou ação, contexto exibido, resposta do usuário e versão autorizada |
| Achado e avaliação | Origem interna/externa, candidato, parecer, autoridade de encerramento, evidências, contestação e evolução |
| Autorização de ciclo adicional | Lote externo que gerou a intervenção, ciclo permitido, decisão humana, reserva e consumo recuperável |
| Efeito externo | Intenção, identificador e estado observado de PR, comentário, push ou merge |
| Evento e artefato | Histórico ordenado, testes, relatórios, diffs e referências a arquivos |

Separar a etapa do trabalho de sua condição de execução: por exemplo, uma tarefa pode estar na etapa de testes e interrompida por fechamento do aplicativo. Assim, Retomar não perde o ponto de trabalho ao representar a interrupção. Transições que liberam escrita, publicação, nova rodada ou merge verificam seus requisitos em transação e mantêm registro da decisão.

Tarefas e etapas usam reserva exclusiva. Identificadores externos e chaves de deduplicação impedem que reconexões produzam outra tarefa, outro PR ou uma rodada de review adicional. Uma reserva deixada por um processo morto não é retomada sem conferir o estado real dos arquivos e dos efeitos externos.

## Ciclo de uma tarefa

```text
Painel ou Trello
    → tarefa e projeto associados
    → Arquiteto / Claude Code
    → aguardando aprovação do plano
    → Coder / Claude Code
    → candidato selado pelo controlador, ligado ao código e à base
    → Tester / Codex
    → Manager / Codex, em outra sessão
    → PR em rascunho
    → revisão adicional do Codex no GitHub + CI
    → aguardando autorização de merge
    → integração confirmada
```

Todos os apontamentos entram no histórico, mas somente os pareceres externos do Codex no PR com apontamentos entram no contador de três rodadas. Tester e Manager podem devolver e reavaliar quantas vezes necessário, sem consumir esse contador. O processo pode aguardar informação, permissão, provedor ou intervenção sem declarar aprovação nem descartar o estado anterior.

Não há teto por contagem interna. A decisão 21 determina pausa quando não houver progresso verificável. A decisão 22 determina que cada autorização após a terceira rodada externa libere apenas uma resposta e uma nova avaliação externa; o resultado negativo seguinte exige outra intervenção. Contestação só encerra um achado quando a autoridade correspondente a aceita, conforme [CONFIABILIDADE.md](CONFIABILIDADE.md).

## Ciclo de vida local e energia

| Evento | Comportamento |
| --- | --- |
| Abrir o painel | Carregar histórico e tarefas; não retomar agentes interrompidos automaticamente |
| Acionar Retomar | Somente tarefa pausada/interrompida sem bloqueio pendente: reconciliar sessão, candidato/base, arquivos, decisões e efeitos; continuar do ponto recuperável |
| Fechar o painel normalmente | Impedir novos despachos, interromper tarefas, encerrar seus processos locais, persistir estado e então fechar; sem pedir nova confirmação para encerrar |
| Encerramento forçado ou falha do painel | Interromper a árvore de execução assim que a falha for detectada; o prazo e a ausência de processos órfãos precisam de prova |
| Desligar ou reiniciar o Mac | Depender da persistência contínua; ao reabrir, oferecer Retomar com arquivos e histórico preservados |
| Tela apagada ou sessão bloqueada | Manter execução se o painel estiver aberto e impedir apenas suspensão por inatividade durante o trabalho |
| Suspensão real, bateria crítica ou tampa fechada fora de configuração suportada | Não prometer execução contínua; tratar a interrupção e reconciliar o estado na volta |

No Electron, `powerSaveBlocker` com `prevent-app-suspension` é o mecanismo candidato para manter o sistema ativo permitindo que a tela apague. A proteção fica vinculada à execução e não altera permanentemente preferências de energia. Fechar o painel libera essa proteção junto do encerramento dos processos. Suspensão forçada continua sendo uma interrupção possível.

Trabalho já aceito pelo GitHub, como CI e revisão remota, pode continuar nesse serviço após o fechamento local. Encerrar o painel impede novos despachos locais; não desfaz operações remotas. Ao reabrir, o painel deve consultar o resultado real antes de retomar. Cancelar também operações remotas exigiria uma capacidade e uma política próprias.

Minimizar a janela ou mudar o foco mantém a janela aberta e a execução permitida. Recarregar ou perder o conteúdo pausa o despacho e leva os executores a uma interrupção recuperável; depois de restabelecer a interface, a ação Retomar reconcilia o trabalho. Fechar a janela executa o encerramento completo. Medir esses comportamentos separadamente no primeiro experimento de ciclo de vida.

## Estado durável e recuperação

- A tarefa possui identidade própria, independente da entrada pelo painel ou Trello.
- Plano aprovado, configuração efetiva e permissões são ligados à versão da solicitação; uma edição posterior não muda silenciosamente o escopo em execução.
- Sessões dos provedores são relacionadas explicitamente à tarefa, ao papel e à tentativa. Nunca retomar a última sessão global da máquina.
- Uma execução conserva worktree, branch, candidato/base, processos conhecidos, revisões, resultados e decisões pendentes. O commit local selado é materializado separadamente para teste e auditoria; o controlador publica esse commit exato, sem recolher alterações tardias da pasta viva.
- Efeitos externos precisam ser reconhecíveis após reiniciar. Antes de repetir criação de branch, push, abertura de PR ou comentário, reconciliar o que já aconteceu.
- Pausa e interrupção preservam trabalho para Retomar; bloqueio exige resolver sua causa. A decisão 23 define cancelamento terminal para execução, com artefatos preservados e ação Reabrir distinta. Resultado ambíguo é apresentado para decisão em vez de repetir a ação às cegas.
- Consumo é mostrado somente quando informado pelo provedor; informação indisponível não aparece como custo zero.

## Concorrência

- Permitir duas tarefas progredindo ao mesmo tempo em etapas executáveis; cada tarefa segue a ordem dos papéis.
- Impedir que dois trabalhadores reservem a mesma tarefa ou assumam a mesma etapa.
- Cada tarefa escreve na própria branch/worktree. Isolar também bancos, portas, arquivos temporários e saídas de teste quando o projeto precisar deles.
- Uma solicitação de aprovação ou cancelamento deve atingir somente a tarefa e a sessão correspondentes.
- Toda mudança observada na branch de destino invalida prontidão e autorização anteriores, inclusive sem conflito textual. Incorporar a nova base, criar novo candidato e repetir a validação pertinente. Duas tarefas desenvolvem em paralelo, mas integrações são serializadas por repositório/branch.
- A reserva local não protege contra merges ou pushes externos. Provar uma configuração de proteção no GitHub que rejeite integração fora da identidade autorizada do candidato e da base; se a conta/repositório não permitir sustentar essa garantia, manter merge no aplicativo indisponível até resolver o requisito.
- O ambiente do projeto gerenciado deve ser configurado explicitamente. Instruções e comandos de validação pertencem ao projeto, sem pressupor que o ambiente do Bot Financeiro sirva para todos os demais.

## Condições para considerar o PR pronto

1. Plano aprovado e implementação compatível com seu escopo.
2. Tester e Manager aprovam materializações imutáveis do candidato, com evidências dos testes e da base utilizada.
3. Revisão externa do Codex identificada no PR, com resultado favorável para o candidato atual e seus achados tratados pela autoridade competente.
4. CI exigido pelo projeto concluído com sucesso para o candidato pertinente, sem reaproveitar validação de base anterior.
5. Achados com resposta e estado verificáveis; ausência de comentário não equivale a aprovação.

Merge continua sendo uma ação separada, condicionada à autorização do usuário sobre o candidato revisado e a base. Novo commit, nova base ou mudança nas entradas relevantes de validação exigem reavaliação e não herdam a autorização anterior.

No momento da chamada de merge, reservar a integração e conferir candidato, base, autorização, review e checks. A condição de head esperada do GitHub só protege o head: a base exige proteção no servidor além da última leitura local. Provar esse contrato contra concorrência externa e conferir a árvore realmente integrada. Configuração do método de merge acompanha as opções permitidas pelo repositório, sem contornar proteções. Os detalhes e o tratamento de merge queue estão em [CONFIABILIDADE.md](CONFIABILIDADE.md).

## Marcos de construção e aceitação

| Marco | Entrega | Evidência de aceitação |
| --- | --- | --- |
| 1. Provar integrações e ciclo de vida | Ambos os provedores com assinaturas existentes, janela controlando executores e contrato de review no GitHub | Progresso, perguntas/aprovações, escrita bloqueada antes da aprovação do plano, interrupção e retomada por ID; término de filhos/netos em close/crash do renderer e do processo principal; parecer concluído, identidade da rodada, versão revisada e resultado sem achados, inclusive em draft |
| 2. Tarefa completa no painel | Cadastro local, plano, aprovação e passagem pelos quatro papéis | Nenhuma edição começa antes da aprovação; o contexto chega ao papel certo; Tester e Manager possuem sessões distintas; tarefa mostra alterações e resultados |
| 3. PR e correções | Publicação do PR após o time interno, review adicional, CI, histórico de achados e autorização de merge | Quatro ou mais devoluções internas não consomem o contador externo; comentários do mesmo parecer externo contam uma vez; segunda rodada externa consolida e terceira com problemas chama o usuário; versão antiga não libera a atual |
| 4. Duas tarefas de verdade | Execução concorrente com isolamento e cancelamento independente | Duas tarefas alteram o mesmo arquivo em cópias diferentes e executam testes sobrepostos sem contaminar checkout, banco, portas ou artefatos; cancelar uma preserva a outra |
| 5. Trello como entrada adicional | Importação por link, leitura de título, descrição e checklists e vínculo com tarefa nativa do sistema | Reimportar um card não duplica trabalho; tarefa criada no painel funciona sem Trello; pedido importado conserva a versão usada para aprovação |
| 6. Fechamento, energia e recuperação | Encerramento ao fechar painel, Retomar explícito, estado durável e tratamento de limites | Fechar com duas tarefas e processos filhos ativos e comprovar seu encerramento; exercitar crash/reload/minimização; interromper antes/depois de commit, push e PR e retomar sem duplicar efeitos; validar tela apagada sem confundi-la com suspensão real |

Todos esses marcos compõem o piloto; a concorrência e o segundo provedor não ficam para depois dele. O marco 1 precisa resolver as limitações de integração antes de prometer a experiência do painel. Se uma assinatura não permitir o caminho necessário, apresentar a limitação e decidir a alternativa com o usuário, sem mudar silenciosamente para cobrança via API.

Os [sete ensaios adicionais](CONFIABILIDADE.md) são critérios dos marcos acima: proteção da base e capacidades no marco 1; candidato imutável no 2; autoridade, extensão de ciclo e serialização de merge no 3; incompatibilidade sem conflito textual no 4; pausa/cancelamento/impasse e retomada no 6. Provar limites fora dos prompts e não somente estados desenhados na interface.

Executar a prova de ciclo de vida antes de ampliar as telas, depois repeti-la com os dois executores reais e carga concorrente no marco 6. Medir o tempo de encerramento normal e a detecção de falhas; não anunciar parada instantânea em cenários abruptos sem evidência. Reutilizar testes controlados para falhas/repetições e manter um ensaio real separado para recursos dependentes de conta ou serviço remoto.

Ao adaptar `time-dev`, remover o teto interno de três rodadas e a exceção de aprovação para plano trivial. Substituir a regra de olhar apenas as alterações por investigação do alcance e histórico desde o primeiro achado. O Arquiteto tem capacidades de leitura e produção do plano, sem permissão de edição do projeto antes da decisão humana.

## Critérios do relatório permanente de achados

Para cada apontamento: origem, código revisado, evidência do problema, classe do defeito, resposta adotada, tentativas anteriores, estado atual e prova de resolução ou contestação. Para cada lote de correção: alcance da mudança, chamadores/consumidores, caminhos relacionados, casos semelhantes, regressões possíveis e verificações realizadas.

O relatório do segundo parecer externo com apontamentos consolida a evolução desde o primeiro achado, incluindo os internos. O terceiro parecer externo com problemas ainda a tratar exige intervenção humana antes de outra resposta automática. As revisões internas não possuem teto de rodadas, mas registram e investigam o alcance de cada achado desde o início.

## Evolução da integração Trello

Após a importação por link validada, acrescentar captura automática por labels. Cada regra relaciona quadro/labels ao projeto de destino; reconhecer novamente o mesmo card não cria outra tarefa. A presença de um label não substitui aprovação do plano. Atualização de comentários, listas ou conclusão dos cards precisa de definição própria e não faz parte da importação inicial.

## Etapa posterior: servidor e equipe

Depois do piloto validado, definir autenticação dos membros, papéis de aprovação, identidade de execução por provedor, configuração de repositórios no servidor, rede, dados persistentes e backups, disponibilidade e observabilidade. A infraestrutura de execução deve servir ao mesmo fluxo e painel, mas a migração precisa de validação própria; não é apenas trocar o endereço de hospedagem.
