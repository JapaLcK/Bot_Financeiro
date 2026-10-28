# Open Finance do PigBank — roteiro de conclusão das ondas 4 a 8

Documento preparado em 14/09/2026 para orientar o Claude e permitir retomada em outra conversa.
As cinco ondas abaixo são uma proposta de execução acordada nesta conversa, não uma numeração histórica do repositório. Este documento autoriza planejamento; não constitui autorização de merge, deploy, exclusão remota ou alteração de dados de produção.

> **Nota de 2026-10-09 (decisão do dono; a premissa adotada ainda não vale no código).** O plano “Só Open Finance” (07/10/2026, [#882](https://github.com/JapaLcK/Bot_Financeiro/pull/882), **mergeado em 2026-10-10 às 01:28 UTC**, `gh pr view 882`) foi **descartado como premissa**. A premissa adotada é a deste roteiro: o cliente poderá digitar gasto, receita, caixinha e investimento à mão, e lançamento manual e transação do banco deverão conviver sem contar o mesmo dinheiro duas vezes. **Isso ainda não está implementado para o usuário.** Na `main` de `adfa040b` (2026-10-14; continua valendo em `518494cb`, 2026-10-15), a Q40 recusa “banco” (`core/handlers/forma_pagamento.py`), a Q36 bloqueia a coorte da chave do v2 (`core/services/fonte_unica.py`) e a conciliação de gasto e receita digitados como dinheiro só gera pendência, que conta em dobro até ser confirmada (`db/resumo_mes.py:30`); só o lançamento de fonte `ofx` e a cobrança recorrente em conta (`of_recurring`) fundem sozinhos (nota da Onda 6). O #882 tinha outro conteúdo e foi mergeado: o `docs/plano-so-open-finance.md` já está na `main`; o aviso de que a premissa foi descartada entra no mesmo PR que traz este arquivo. A regra de despesa e receita declaradas “pelo banco” está na [ADR 0006](adr/0006-declaracao-bancaria-despesa-receita.md). O PR-A dela (a conciliação, #969) foi mergeado em 2026-10-15 e é inerte até o PR-B; PR-A2, PR-B, PR-B1 e PR-C estão pendentes. Do plano descartado, ficam o #866 (a Previsão lê as recorrências de despesa do banco; a receita passou a vir do extrato no #981) e o #851 (total investido só do banco, a ajustar no PR-C da ADR 0006). **Sobre este arquivo:** o PR que o traz para a `main` passa a **versionar** um documento de 14/09/2026 que era arquivo não rastreado; só as notas de 2026-10-09 foram auditadas, o resto do texto foi mantido como estava. **Aviso no plano Só OF: incluído neste PR.** O `docs/plano-so-open-finance.md` ganhou no topo um bloco dizendo “premissa descartada em 2026-10-09; ver ADR 0006”; o resto do documento não foi reescrito. **Respostas do dono de 2026-10-13** (congelamento aos 45 dias, casas decimais, OFX, “A conferir”, “foi em dinheiro”, Q43) estão registradas na ADR 0006, seção “Decisões do dono em 2026-10-13”.

## 1. Objetivo e definição de conclusão

Entregar o fluxo completo: conectar banco, importar dados, representar corretamente o patrimônio, atualizar, explicar falhas, recuperar interrupções e respeitar desconexão e exclusão de conta.

“Concluído” exige evidência de que:

- Nenhum usuário acessa, adota ou altera item bancário de outro usuário.
- Interrupções deixam um estado identificável, com uma saída segura; não apagam o único rastro do item.
- Banco removido deliberadamente não reaparece por webhook atrasado ou recuperação automática.
- A tela distingue coleta em andamento, dados antigos, falha parcial, autorização necessária e atualização concluída.
- Reprocessar a mesma movimentação não duplica gastos, receitas, compras ou patrimônio.
- Registro manual e transação importada seguem uma regra explícita de conciliação.
- Caixinhas, investimentos, Carteira e bancos não contam o mesmo dinheiro duas vezes nem perdem valor por erro de registro.
- Os fluxos críticos foram verificados com o provedor e nas interfaces reais utilizadas pelo produto.

Indisponibilidade do banco/provedor pode continuar existindo. O critério é detectá-la, preservar dados, informar corretamente e oferecer recuperação; não prometer disponibilidade externa absoluta.

## 2. Ponto de partida: conferir antes de executar

Referência da elaboração: `git log -1 --format='%H %s'` retornou `0964d088dd0bb1d6d7af2a3498860e4c61e10ba3` (#417). Havia alterações preexistentes de outra tarefa no checkout; este documento não as incorpora. A versão real deve ser consultada novamente em cada retomada.

| Referência | Situação consultada em 14/09/2026 | Consequência para o trabalho |
|---|---|---|
| [PR #166](https://github.com/JapaLcK/Bot_Financeiro/pull/166) | Onda 3 histórica integrada | Não repetir sua implementação por causa de um handoff antigo. |
| [PR #313](https://github.com/JapaLcK/Bot_Financeiro/pull/313) | Adoção por webhook e script operacional integrados | Partir das proteções existentes. |
| [PR #349](https://github.com/JapaLcK/Bot_Financeiro/pull/349) | Mergeado em 12/09/2026, head `f979ec79`: posse relida sob lock e diagnóstico de conflito emitido depois da liberação | Preservar a recusa de outro dono e a reconexão legítima; não reintroduzir o resgate rejeitado. |
| [PR #386](https://github.com/JapaLcK/Bot_Financeiro/pull/386) | Aberto, head `40212acfa4d281baa1de4547cd9c403869686d3f`, confirmado pela API | Já contém trabalho de sync retroativo e robustez do script. Conferir seu resultado antes da onda 4. |
| [PR #180](https://github.com/JapaLcK/Bot_Financeiro/pull/180) | Correção do fuso já integrada | Manter regressões; não recriar a correção histórica. |

O usuário está chamando a frente que o Claude concluirá de “onda 3”. Ao retomar, identificar o PR/commit entregue por ele; não assumir que esse nome significa exclusivamente o #166 ou o #386.

**Passagem obrigatória da frente atual:** pedir ou localizar o relatório final com commit, PR, arquivos, testes executados, resultados, revisão, pendências e validação real. Uma correção presente num PR aberto ainda não está entregue na `main`.

As issues [#149](https://github.com/JapaLcK/Bot_Financeiro/issues/149), [#294](https://github.com/JapaLcK/Bot_Financeiro/issues/294), [#188](https://github.com/JapaLcK/Bot_Financeiro/issues/188) e [#286](https://github.com/JapaLcK/Bot_Financeiro/issues/286) estavam abertas na consulta. Seus relatos são entradas de investigação: conferir o código e a alcançabilidade antes de declarar que todo diagnóstico antigo ainda se aplica.

**Nota de 2026-10-09 sobre a tabela acima.** O [PR #386](https://github.com/JapaLcK/Bot_Financeiro/pull/386) consta como “Aberto” na consulta de 14/09/2026; hoje está **mergeado** (2026-09-15, `gh pr view 386`). A tabela é a fotografia daquela data e não foi reescrita.

### Retificação do fechamento da Onda 3

O ciclo da Onda 3 tentou implementar um resgate por `scripts/of_resgate.py`, com `--resgatar` e um discriminador em duas camadas. Esse código **não** entrou na `main` e foi reprovado porque podia apagar o rastro antes de uma adoção que depois falharia, deixando o item sem conexão e sem porta de operação. A ausência do rastro com dono também não distingue adoção interrompida de banco removido deliberadamente. O resgate antigo deve ser tratado como material rejeitado, mesmo que exista em stash ou em branch histórica.

O #349 fechou a parte de posse: a adoção e o `POST /pluggy-item` releem o dono sob o lock; conflito vira recusa; o contexto é levado para fora do lock e o diagnóstico é gravado depois. O CI do head aprovado passou. Não entraram o teto no `unregister_item`, o parâmetro `timeout_ms` no logger síncrono nem o resgate. Não repetir esses itens como se fossem código parcialmente entregue.

Antes da Onda 4, a próxima chamada ao time deve abrir **dois trilhos independentes e dois PRs** [nota de 2026-10-09: a tabela do §4 já dá as Ondas 4 e 5 como integradas; esta instrução é de 14/09/2026, não reconferi se os dois trilhos foram abertos como descritos e ela não deve ser lida como pendência atual sem essa conferência]:

1. **Teto global do log:** impedir que o `_DashboardHandler` prenda uma thread de aplicação numa tabela travada. Enumerar o caminho central e seus consumidores, definir o que acontece quando o prazo expira e evitar recursão/perda silenciosa de erros. Provar com banco/lock real que uma operação não fica bloqueada pelo logger; provar também o caminho normal em que o evento é persistido.
2. **Caixa sem `health`:** corrigir a primeira tela de um item `OUTDATED` com `executionStatus=USER_AUTHORIZATION_PENDING` quando o sync de saúde ainda não gravou `health`. A Arquiteta deve decidir se a fonte segura é um campo derivado, um snapshot ampliado ou outra opção; não devolver o payload bruto da Pluggy ao navegador por conveniência. Testar `health` presente e ausente, autorização em andamento, erro, `UPDATING`, dados antigos e usuário sem acesso.

Os dois trilhos devem começar de um worktree limpo baseado na `main` atual. Não aplicar o stash da tentativa rejeitada nem editar a árvore suja de outra tarefa. Cada trilho precisa de baseline própria, controles negativo/positivo, Tester e Manager independentes. Não alterar automaticamente o alvo do `ON CONFLICT`: a proteção de posse do #349 é deliberada. As perguntas de produção — existência do índice `uq_of_conn_provider_item`, duplicatas atuais, `REPORT_TIMEZONE` e `OF_PROACTIVE_ENABLED` — são leituras operacionais separadas e devem ser respondidas sem mudar dados.

## 3. Instruções gerais para o Claude

1. Ler `CLAUDE.md`, `docs/CLAUDE.md`, `docs/armadilhas.md` e as instruções específicas aplicáveis. Este roteiro complementa as regras do projeto.
2. Consultar `git status --short`, branch, HEAD, `main` remota e PRs relacionados. Atualizar as referências sem sobrescrever mudanças alheias. Trabalhar em branch/worktree próprio, baseado num commit identificado.
3. Reclassificar cada item como: já entregue, implementado em PR aberto, defeito reproduzido, hipótese, decisão de produto ou verificação externa. Não misturar essas categorias.
4. Executar uma onda por vez. Antes de codificar, escrever o plano daquela onda com entradas, saídas, arquivos, invariantes, testes e critérios de aceite. Uma onda pode ser dividida em PRs verificáveis.
5. Aplicar a skill `time-dev` às alterações de código, conforme o projeto: Arquiteto, Coder, Tester e Manager/revisor independente. Para frontend, aplicar também `.claude/skills/pigbank-frontend/SKILL.md`. Para pytest, ler `.claude/skills/baseline-testes/SKILL.md` antes da execução.
6. Usar a ficha persistente prevista pela skill; vincular a ficha a este roteiro. Subagentes devem receber escopo e arquivos próprios; não editar a mesma árvore enquanto outro agente mede baseline ou revisão.
7. Procurar e reutilizar funções existentes. Não trocar framework, redesenhar todo o app ou refatorar o monólito como pré-requisito sem necessidade comprovada.
8. Preservar isolamento por usuário. Operações de inventário entre usuários precisam de contexto administrativo explícito; não transformar queries operacionais em endpoints acessíveis a usuários comuns.
9. Se duas rodadas de correção atingirem a mesma máquina de estados, voltar à enumeração de estados × eventos e revisar o plano. Seguir os limites de retomada da skill vigente.
10. Ao terminar, atualizar esta tabela de progresso e o relatório da onda. Não começar a próxima automaticamente se a autorização recebida cobrir somente uma onda.

**Não fazer:** apagar rastros para forçar adoção; considerar ausência de auditoria prova de que nunca houve conexão; tratar origem desconhecida como recuperável; trocar o alvo do `ON CONFLICT` global sem preservar posse; fabricar resultado de teste; usar CI como primeira validação; esconder falhas com exclusões; executar scripts operacionais contra produção como se fossem testes.

## 4. Ordem e acompanhamento

| Onda | Resultado principal | Dependência | Situação em 2026-10-09 |
|---|---|---|---|
| 4 | Conexões com recuperação segura | Conferir entrega da frente atual | **Integrada** na `main`: [#478](https://github.com/JapaLcK/Bot_Financeiro/pull/478), [#539](https://github.com/JapaLcK/Bot_Financeiro/pull/539), [#542](https://github.com/JapaLcK/Bot_Financeiro/pull/542), [#655](https://github.com/JapaLcK/Bot_Financeiro/pull/655) (PR-E da onda) e [#656](https://github.com/JapaLcK/Bot_Financeiro/pull/656), todos mergeados entre 2026-09-18 e 2026-09-27. Não reconferi o escopo da onda caso a caso nem a validação no ambiente real. |
| 5 | Estados, sincronização e avisos coerentes | Contrato de conexão da onda 4 | **Integrada** na `main`: [#692](https://github.com/JapaLcK/Bot_Financeiro/pull/692), [#718](https://github.com/JapaLcK/Bot_Financeiro/pull/718), [#727](https://github.com/JapaLcK/Bot_Financeiro/pull/727), [#755](https://github.com/JapaLcK/Bot_Financeiro/pull/755), [#810](https://github.com/JapaLcK/Bot_Financeiro/pull/810), [#834](https://github.com/JapaLcK/Bot_Financeiro/pull/834), [#850](https://github.com/JapaLcK/Bot_Financeiro/pull/850) e [#903](https://github.com/JapaLcK/Bot_Financeiro/pull/903) (PR-E da onda, mergeado em 2026-10-09). Não reconferi o escopo ponto a ponto nem a validação no ambiente real. |
| 6 | Extrato e categorias integrados | Coleta utilizável; não depende de todo acabamento da 5 | **Parcial.** #149 fechada (feita no #713 e no #726); #294 aberta e superada na forma literal; #894 mergeada em 2026-10-10 (sinal da compra do cartão do OF); a declaração de despesa e receita (PR-A da [ADR 0006](adr/0006-declaracao-bancaria-despesa-receita.md), #969) foi mergeada em 2026-10-15; o PR-A2 (A2a, A2b-1 e A2b-2 mergeados; o A2c, lembrete de 7 dias, está em revisão no #1291) e o PR-B, que destrava o lançamento, ainda faltam. Detalhe na nota da Onda 6. |
| 7 | Caixinhas/investimentos com regra de conciliação | Decisão de produto e contratos de importação | **Parcial.** ADR de “guardei/saquei” existe (ADR 0003), #188 fechada, #286 aberta; a [ADR 0006](adr/0006-declaracao-bancaria-despesa-receita.md) a estende para despesa e receita (PR-A mergeado em 2026-10-15, #969; o investimento é o PR-C, sem código). Detalhe na nota da Onda 7. |
| 8 | Evidência real e operação após deploy | Critérios das ondas anteriores atendidos | **Validação manual não executada:** `docs/open_finance_validacao_manual.md:8` diz que os casos A e B nunca foram executados, e não encontrei registro de execução. Preparar ambiente desde o início continua valendo. |

Validação real deve acontecer desde o primeiro fluxo disponível. A onda 8 consolida a evidência; não deixa toda descoberta de aparelho/provedor para o fim. Defeito financeiro reproduzido que cause perda ou duplicação deve ganhar prioridade sobre acabamento visual, mesmo que isso altere a ordem.

Para cada onda, registrar separadamente: `planejada → em implementação → verificada localmente → revisada → integrada → validada no ambiente real`. Usar `bloqueada` com causa e próximo passo quando necessário. “Integrada” e “validada” não são sinônimos.

## 5. Onda 4 — conexões e recuperação segura

**Objetivo:** nenhuma interrupção deve deixar o usuário sem saída, nem permitir que uma conexão removida volte involuntariamente.

**Escopo:** inventário dos itens com e sem conexão; adoção, desconexão, reset e exclusão; marcação persistente da intenção de remover; recuperação de falhas antes da escrita e de resultado ambíguo; diagnóstico do operador. Aproveitar a entrega do #386, se já integrada.

**Áreas para investigar:** `frontend/routes/open_finance.py`, `db/open_finance.py`, `db/open_finance_state.py`, `db/privacy.py` e `frontend/routes/settings.py`; seguir seus chamadores e eventuais módulos extraídos. O `scripts/adotar_items_of_orfaos.py` que esta linha listava foi **apagado** em `924aee3f` (2026-09-23), junto com `scripts/adotar_items_lista.py` e os arquivos de teste dele (a contagem está na mensagem do commit `924aee3f`, de 2026-09-23) — ele já tinha rodado em produção e o `--apply` não tinha guarda contra item duplicado da mesma conta. Volta do histórico com `git checkout bda3ee7 -- scripts/adotar_items_of_orfaos.py scripts/adotar_items_lista.py` se alguém precisar lê-lo.

**Decisões antes da escrita:** onde fica a intenção de remoção e por quanto tempo; qual evidência autoriza recuperar; como lidar com casos antigos que nunca receberam essa marca; como conciliar essa evidência com exclusão da conta. Um novo `origin='disconnect'` não resolve retroativamente a ambiguidade histórica.

| Caso a provar | Resultado obrigatório |
|---|---|
| Aba fechada antes do callback do widget | Item permanece identificável e pode ser adotado de maneira legítima. |
| Timeout remoto, usuário inexistente ou limite do plano | Recusa preserva a possibilidade de diagnóstico e nova tentativa quando aplicável. |
| Falha antes da escrita × commit de resultado desconhecido | Tratamentos distintos; não apagar proteção quando a escrita pode ter ocorrido. |
| Duas adoções; adoção × reconexão; adoção × remoção/reset/exclusão | Sem posse cruzada, recriação de usuário ou ressurreição de banco removido. |
| Remoção após adoção com auditoria ausente | Ausência do log não autoriza recuperação. |
| Origem desconhecida ou registro legado ambíguo | Não recuperar automaticamente sem evidência suficiente. |
| ID vazio/inválido, modo lista, Ctrl+C e thread de sync presa | Não ampliar os alvos; informar limites sem afirmar operações não comprovadas. |
| Erro 503 com logger lento | Medir a ordem e o prazo do desfazimento; preservar as correções do #349. |

**Aceite:** novo fluxo interrompido tem saída segura demonstrada; casos históricos ambíguos têm diagnóstico e procedimento explícito; item removido continua removido após reentrega; comandos de escrita têm alvos inequívocos. Nenhuma limpeza de produção faz parte do aceite automatizado.

**Testes:** grupo A da seção 10, acrescido de cenários novos ausentes. Concorrência exige barreiras e Postgres real; mocks de `db` não comprovam atomicidade.

**2026-09-23 — o que a PR-A mudou no inventário (ler antes da PR-E).** O `POST /open-finance/{id}/pluggy-item` passou a devolver **200** quando só o `register_item` falha (antes era 500 com a conexão já commitada). Consequência para esta onda: **"conexão que existe sem rastro `pluggy_item`" deixou de ser desfecho de erro e passou a ser estado alcançável em produção** — conexão saudável, auditada e sincronizada, sem linha no registry. Esse estado **não tem hoje nenhuma ferramenta que o enxergue**: a que esta nota citava (`scripts/adotar_items_of_orfaos.py`) foi apagada em `924aee3f` no mesmo 2026-09-23 — e, mesmo viva, ela enumerava a partir do registry, que é justamente o que falta nesse estado. O único rastro é o evento `of_item_registry_failed` com `origin='pluggy_item_route'` em `system_event_logs` (com `user_id` na coluna). **Dar diagnóstico a esse estado é pendência da PR-E**, e agora ela é do zero: enumerar pelas CONEXÕES, não pelo registry, sem base para reaproveitar do one-shot removido (`git checkout bda3ee7 -- scripts/adotar_items_of_orfaos.py` para consultar o que ele fazia). A ressurreição por banco removido continua fechada pela marca `origin='removed'`. A de conta excluída **não estava**: o `user_exists` responde `True` durante a exclusão agendada (`db/users.py:34`), e a adoção nessa janela pendurava conexão que saía pela cascata do `delete from users` sem passar pelo `RETURNING` que alimenta o delete remoto — item vivo e pago na Pluggy depois de uma exclusão LGPD (P2 do Codex na PR-C #539, reproduzido pelo Tester). Quem fecha isso é a PR-C: `is_account_scheduled_for_deletion` na adoção, a reconsulta de `open_finance_connections` antes do `delete from users`, e os dois ramos de recusa (conta agendada e conta já apagada) apagando o item na Pluggy.

**Nota de 2026-10-09 sobre o parágrafo acima.** (1) A referência `db/users.py:34` ficou velha: `user_exists` está hoje em `db/users.py:38` (`git grep -n "def user_exists" -- db/users.py`). (2) O PR-E da Onda 4, citado ali como pendência, foi o [#655](https://github.com/JapaLcK/Bot_Financeiro/pull/655) (“ferramenta de operador para casos históricos ambíguos”), **mergeado em 2026-09-27** (`gh pr view 655`). Não reconferi se ele cobre o diagnóstico específico da “conexão sem rastro `pluggy_item`” descrito no parágrafo; o texto original foi mantido.

## 6. Onda 5 — sincronização, estado da tela e avisos

**Objetivo:** a tela e as notificações representam o mesmo estado observado, distinguindo atualidade dos dados de atividade do provedor.

**Escopo:** “Atualizando…” permanente; Caixa sem `health` no snapshot inicial; expiração da supressão device/QR; falhas parciais; refresh manual/agendado; sincronização retroativa; atualização de dashboard e Ajustes. Inventariar as flags reais e seu efeito, sem ativá-las silenciosamente em produção.

**Áreas:** `core/services/pluggy_health.py`, `pluggy_sync.py`, `open_finance_proactive.py`, snapshot/reconnect em `db/open_finance.py`, rotas e consumidores em `frontend/`.

**Definir antes:** fontes e precedência de `status`, `executionStatus`, `health`, última tentativa, último sucesso e autorização atual; validade de cada observação; ações possíveis para dado antigo. Não transformar vencimento do diagnóstico em sucesso nem mandar reconectar durante autorização ainda válida.

| Caso a provar | Resultado obrigatório |
|---|---|
| Caixa `OUTDATED` + `USER_AUTHORIZATION_PENDING`, com e sem `health` | Instrução correta desde o primeiro snapshot. |
| Device/QR recente × expirado × consulta de saúde falhou | Supressão e orientação têm prazo definido; não duram para sempre por valor congelado. |
| Provedor `UPDATING` com dados importados × sem dados | Mostrar a situação dos dados sem fingir que o provedor concluiu. |
| Conta saudável, corretora apenas com investimentos, resposta vazia | Distinguir produto não aplicável de falha e de ausência real de dados. |
| Falha em um produto durante sync parcial | Preservar dados válidos e expor quais dados não foram atualizados. |
| Webhook atrasado após reconexão ou item removido | Não substituir estado recente por resultado antigo. |
| Flag de refresh desligada e entrega de evento ausente | Conhecer a saída disponível; não depender de job inexistente. |
| Refresh, perda de rede e atualização por evento | Sem sucesso falso, spinner permanente ou repetição descontrolada de requests. |

**Aceite:** tabela estados × eventos documentada, todos os ramos relevantes discriminados por teste, ação correta na tela e no aviso. A idade limite precisa ser definida e justificada; não copiar arbitrariamente um timeout do HTTP.

**Testes:** grupo B, frontend focal e suíte completa. Reproduzir a entrada da Pluggy até o snapshot e a renderização; montar `health` à mão sozinho não cobre esse caminho.

## 7. Onda 6 — extrato, categorias e duplicidade

**Objetivo:** dados importados participam das mesmas regras de categorias e totais do restante do PigBank.

**Escopo:** verificar e corrigir #149; conciliação manual/importado nas duas ordens; reentrega/reimportação; contas e cartões; transferências internas; sinais e datas. Avaliar #294 sem apresentá-la como duplicação já ocorrendo em toda conta: seu relato depende de entrada manual legada.

**Áreas:** importadores em `db/open_finance.py`, normalização em `core/services/pluggy_sync.py`, resolvedor canônico de categorias e leitores de extrato, relatórios e saldo consolidado.

**Regras:** preservar categoria customizada e decisão explícita do usuário; não fundir compras distintas só por coincidirem valor/data; não traduzir apenas na tela deixando o dado incoerente na escrita. Levantar os valores realmente presentes antes de criar mapas ou migrações.

| Caso a provar | Resultado obrigatório |
|---|---|
| Manual → OF e OF → manual | Mesma operação reconhecida nas duas ordens; total não dobra. |
| Duas compras reais de mesmo valor e dia | Continuam duas quando a evidência não sustenta a fusão. |
| Mesmo webhook/sync repetido e concorrente | Sem nova movimentação financeira por repetição. |
| Categoria estrangeira/desconhecida e categoria customizada | Resolução canônica, fallback definido e escolha do usuário preservada. |
| Transferência própria, compra, estorno, receita e investimento | Sinal e participação nos totais seguem o domínio de cada movimento. |
| Data sem hora, horário real, virada do dia/mês | Datas e filtros coerentes; manter regressões de fuso já corrigidas. |
| Conta/cartão de outro usuário ou produto indisponível | Sem cruzar titularidade nem apagar histórico válido por ausência temporária. |

**Histórico:** separar correção da escrita futura de reparo retroativo. Se houver reparo, projetar dry-run, contagem por usuário, amostra sem PII, idempotência e reversão/compensação; executar em produção somente com autorização específica.

**Aceite:** totais e identidades conferidos no banco e nos consumidores; falsos positivos de conciliação cobertos; histórico afetado inventariado e tratado ou registrado como pendência explícita.

**Nota de 2026-10-09, reconferida em 2026-10-14 (GitHub e código de `origin/main` `adfa040b`).** Comandos: `gh issue view <n>` / `gh pr view <n> --repo JapaLcK/Bot_Financeiro`; remeça antes de reusar.

| Item | Situação | Consequência |
|---|---|---|
| #149 (categoria crua da Pluggy) | **Fechada** em 2026-10-01 pelo [#726](https://github.com/JapaLcK/Bot_Financeiro/pull/726); a tradução da categoria foi feita no [#713](https://github.com/JapaLcK/Bot_Financeiro/pull/713) (mergeado em 2026-09-30) e o catálogo foi fundido no #726. | Sai do escopo desta onda. |
| #294 (dedupe reverso não casa manual legado) | **Aberta**, mas a forma literal está superada: `reconcile_manual_launch` não existe mais no código (`git grep` não acha). O dedupe hoje é `propose_manual_reconciliation` / `_propose_manual_reconciliation` (`db/open_finance.py:2186` e `:2212`, linhas de `518494cb`), com o tipo legado (`saida`/`entrada`) canonizado na leitura por `TIPO_CANON_SQL`. | A classe de defeito voltaria se o manual com banco fosse destravado sem o PR-A da ADR 0006 (mergeado em 2026-10-15, #969). |
| [#894](https://github.com/JapaLcK/Bot_Financeiro/pull/894) | **Mergeado** em 2026-10-10, 00:50 UTC (`fix/sinal-compra-cartao-of`): corrige o sinal da compra do cartão do OF pela convenção da Pluggy. | Segundo o corpo do PR, o import atual grava compra como estorno e a maioria dos leitores filtra `is_refund = false`. O corpo diz que “o 1º sync de cada item depois do deploy corrige o legado sozinho” e que um script de operador (`scripts/corrigir_sinal_cartao_of.py`) cobre a conexão pausada que o sync não alcança; ambos não verificados por mim em produção. |
| Conciliação manual→OF e OF→manual | Para o lançamento digitado como dinheiro (`source='manual'`) existe só como **pendência** (o usuário confirma ou rejeita). Fundem **sozinhos** hoje o lançamento de fonte `ofx` e a cobrança recorrente em conta (`of_recurring`), com candidato único, data a até 1 dia e comerciante parecido (`db/open_finance.py:1951-1952` e `:2130-2134`, linhas de `518494cb`), e a declaração de caixinha e aporte (`bank_movement_declarations`, ADR 0003). | A declaração de despesa e receita “pelo banco”, nas duas ordens, entrou com o PR-A da [ADR 0006](adr/0006-declaracao-bancaria-despesa-receita.md) (#969, mergeado em 2026-10-15), inerte até o PR-B. |

**Nota de 2026-10-14.** A `main` trouxe o #981 (receita da Previsão detectada pelo extrato, só com o documento do pagador), o #983 (a lista da v2 mostra o “guardado” como linhas internas fora do total) e o #984 (falha pós-commit em cartão em `core/handlers/credit.py`). O #981 lê o extrato, não os lançamentos; o #983 não se sobrepõe à declaração de despesa e receita; detalhes na ADR 0006, seção “O que a main trouxe em 2026-10-14”.

Decisão do dono, mesma data: a conciliação de despesa e receita vem **antes** de destravar o lançamento manual com banco conectado (ordem PR-A, PR-A2, PR-B).

Decisão do dono de 2026-10-13: o PR-A2 (lembrete de 7 dias, “foi em dinheiro” com ação confirmada, “trocar o par” e “desfazer a confirmação”) é **pré-condição para ligar a flag do PR-B**; ligar a flag continua sendo ato operacional separado e autorizado por ele. Detalhes na ADR 0006, seção “Decisões do dono em 2026-10-13”. **Estado em 2026-10-26:** o PR-A2c (#1291) está mergeado e o PR-B (a) (WhatsApp) entrega o caminho “banco grava declaração” atrás de `DECLARACAO_BANCO`, desligada; o chat IA (b) e o /app e /painel (c) seguem pendentes.

**Testes:** grupo C, testes novos de sequência e os consumidores alterados. A lista existente não é prova de cobertura completa.

## 8. Onda 7 — caixinhas, investimentos e conciliação financeira

**Objetivo:** o que o usuário registra, o que ocorreu no banco e o patrimônio mostrado seguem o mesmo contrato.

**Decisão de produto obrigatória, antes do código:** escrever uma ADR curta respondendo o significado de “guardei/saquei R$ X” com OF: declaração de operação já feita, intenção pendente ou alocação virtual. A #188 descreve essa ambiguidade. Apresentar exemplos e impactos ao dono; não escolher implicitamente na implementação.

**Escopo condicionado à ADR:** associar movimento local à confirmação bancária quando houver; tratar confirmação atrasada, ausente, parcial ou duplicada; definir disponibilidade enquanto pendente; origem/destino de saques mistos (#286); cancelamento e desfazimento; comportamento de dados legados sem origem conhecida.

**Áreas:** `core/services/funding.py`, `db/open_finance.py`, `db/pockets.py`, `db/investments.py`, handlers de WhatsApp, ferramentas do chat, rotas do dashboard e suas regras de desfazimento. Inspecionar o trabalho de outras tarefas antes de modificar esses arquivos.

| Sequência a provar | Resultado obrigatório |
|---|---|
| Depositar → sacar, com e sem banco | Cada parcela tem origem/destino correto; nenhuma perda ou criação fictícia. |
| Mistura Carteira + banco e saque parcial/total | Aplicar regra explícita por origem, sem atribuir tudo ao destino dominante. |
| Registro local → transação chega depois | Conciliar uma vez segundo a ADR, sem contar duas operações iguais. |
| Transação nunca chega | Estado e ação definidos; não declarar confirmação porque passou um sync. |
| Cancelar/desfazer antes e depois da confirmação | Resultado coerente e recuperável, respeitando operações dependentes. |
| Dois pedidos simultâneos usando a mesma disponibilidade | Sem consumir saldo/reserva duas vezes. |
| Lote legado sem origem ou informação inconsistente | Não inventar origem; aplicar política decidida e informar limitações. |

**Prova financeira:** comparar componentes e total antes/depois com taxas, rendimentos e fluxos externos explicitamente contabilizados. “Total igual” sozinho pode esconder dinheiro devolvido ao componente errado. Não exigir igualdade quando houve rendimento ou entrada/saída real.

**Aceite:** ADR aprovada, implementação nas portas alcançáveis, testes de sequência com banco real e critério para histórico. A existência da #286 não autoriza executar reparo nos usuários citados nela.

**Nota de 2026-10-09.** A ADR pedida acima **já existe**: [`docs/adr/0003-semantica-caixinhas-open-finance.md`](adr/0003-semantica-caixinhas-open-finance.md) (“guardei/saquei” é declaração, conferida com o extrato). A [#188](https://github.com/JapaLcK/Bot_Financeiro/issues/188) está **fechada** (2026-09-14, pelo [#418](https://github.com/JapaLcK/Bot_Financeiro/pull/418)). A [#286](https://github.com/JapaLcK/Bot_Financeiro/issues/286) (multi-destino) segue **aberta**. A [ADR 0006](adr/0006-declaracao-bancaria-despesa-receita.md) estende a 0003 para despesa e receita declaradas “pelo banco” e, no PR-C, trata o investimento digitado × a posição do banco (vínculo escolhido pelo usuário; vinculado não soma). O PR-A dela (despesa e receita, #969) foi mergeado em 2026-10-15 e é inerte até o PR-B; o PR-C (investimento) ainda não tem código.

**Testes:** grupo D; trajetos pelo `handle_incoming`, endpoints e ferramentas do chat. Mocks do LLM podem verificar lógica intermediária, mas não comprovam entendimento e envio reais do WhatsApp.

## 9. Onda 8 — validação real, operação e entrega

**Objetivo:** comprovar os fluxos após integração/deploy e deixar um procedimento operacional reproduzível.

Usar `docs/open_finance_validacao_manual.md` como roteiro existente, atualizando afirmações vencidas. Não copiar aqui o catálogo de status da Pluggy; conferir documentação oficial atual e payloads observados, sem guardar segredos ou dados bancários pessoais no Git.

- Preparar conta de teste e ambiente Pluggy apropriado, com autorização para as operações. Teste automatizado nunca usa banco de produção.
- Executar os casos A/B do roteiro manual e os casos novos das ondas 4–7; um banco saudável sozinho não cobre QR, corretora, cartão ou falha parcial.
- Conferir navegador desktop, Safari mobile, PWA instalada e app iOS/WKWebView. Distinguir simulação de viewport de aparelho real.
- Verificar conexão, cancelamento, rede interrompida, reconexão, consentimento, remoção, retorno ao app e atualização sem F5.
- Verificar troca/expiração de plano e retomada, respeitando a política vigente de acesso e dados; não recriar o antigo plano Grátis.
- Medir conexões sem sync recente, duração de sync, falhas por motivo, 503 e casos ambíguos. Reutilizar logs/painel existentes antes de criar infraestrutura.
- Definir limites e janela de observação com dados medidos. Registrar taxa por denominador e período; número absoluto de erro sozinho não estabelece saúde.
- Ensaiar rollback do código e compatibilidade do schema. Reverter código não desfaz importações, adoções ou exclusões já realizadas.
- Separar procedimento de suporte para “sem autorização”, “sem dados”, “falha temporária”, “item removido” e “recuperação ambígua”.

**Aceite:** cada cenário tem data, versão, ambiente, resultado e evidência; bloqueios externos continuam abertos. Se o lote suportado de bancos/produtos for limitado, declarar esse alcance. Não afirmar funcionamento universal com uma amostra de um banco.

## 10. Plano de testes e comandos

### 10.1 Preparação segura e fonte dos comandos

A fonte do setup é `.claude/skills/baseline-testes/SKILL.md`, junto de `tests/conftest.py` e `.github/workflows/tests.yml`. Ler os três na versão executada. `docs/ambiente.md` contém descrições antigas de exclusões: não copiar suas listas de `--ignore`.

Confirmar que `DATABASE_URL` aponta para Postgres de teste descartável, sem imprimir senha/URL em logs. O pytest cria e remove seu banco isolado; se a criação falhar, corrigir o ambiente, nunca desligar a proteção. Até coleta pode inicializar esse mecanismo.

Configurar a URL conforme a skill e confirmar o ambiente antes dos comandos abaixo. Não executar `source .env`, nem presumir que o `.env` de outra máquina é seguro. Não exportar segredos de produção para suprir chaves de teste; o conftest fornece seus defaults.

```bash
# Na raiz do checkout de teste; escolher UM interpretador conforme o ambiente.
export PYTHONPATH=.
export PYTEST_DB_ISOLATION=1
OF_PY="$(pwd)/.venv/bin/python"  # local; confirmar existência e dependências
# No Claude remoto configurado pelo hook, usar em vez da linha anterior:
# OF_PY="$(command -v python3)"
OF_EVID="$(mktemp -d "${TMPDIR:-/tmp}/pigbank-of-evidencias.XXXXXX")"
"$OF_PY" -m pytest -q --junitxml="$OF_EVID/baseline.xml" >"$OF_EVID/baseline.log" 2>&1
OF_RC=$?
printf 'Exit code da baseline: %s\n' "$OF_RC"
```

Ler os arquivos gerados e registrar o exit code; a última linha do shell imprimir sucesso não transforma pytest vermelho em verde. O diretório temporário não é arquivo permanente: guardar evidências úteis, sanitizadas, no relatório antes de encerrar a sessão.

No remoto com dependências reduzidas, usar somente o opt-in já previsto no conftest/hook, se aplicável, e relatar cada exclusão/skip. Não habilitá-lo para encobrir falha de código. O CI completo permanece necessário.

### 10.2 Grupos focais existentes

Executar da raiz, com `OF_PY` e ambiente preparados. Os caminhos abaixo foram conferidos no checkout de referência; procurar renomeações e os novos testes da frente atual antes de reutilizar. Arquivo desaparecido não é motivo para omitir a cobertura.

```bash
# A — onda 4: adoção, concorrência, propriedade, segurança e remoção
"$OF_PY" -m pytest -q tests/test_of_webhook_adopt*.py tests/test_of_item_ownership*.py tests/test_of_concurrency.py tests/test_of_prazo_leituras_sob_o_lock.py tests/test_of_log_fora_do_lock.py tests/test_open_finance_disconnect_route.py tests/test_account_reset.py tests/test_privacy_deletion.py tests/test_pluggy_webhook_security.py tests/test_account_deletion_pluggy.py tests/test_account_deletion_pii_logs.py tests/test_account_deletion_adocao_corrida.py tests/test_of_pluggy_items_a_deletar.py

# B — onda 5: saúde, atualização, resposta, plano e autorização
"$OF_PY" -m pytest -q tests/test_of_health.py tests/test_of_connection_state.py tests/test_of_refresh_response.py tests/test_of_refresh_schedule.py tests/test_of_connect_token_gate.py tests/test_of_trial_expiry.py

# C — onda 6: importação, categorias, dedupe e consumidores
"$OF_PY" -m pytest -q tests/test_pluggy_sync_normalize.py tests/test_open_finance_mock.py tests/test_category_normalization.py tests/test_tipo_legado_no_dedupe_do_of.py tests/test_category_launches_query.py tests/test_credit_transactions_endpoints.py tests/test_consolidated_balance.py tests/test_routes_categories.py tests/test_pluggy_resposta_venenosa.py

# D — onda 7: origem/destino, caixinhas, patrimônio e desfazimento
"$OF_PY" -m pytest -q tests/test_funding_source.py tests/test_of_caixinha_autoimport.py tests/test_consolidated_balance.py tests/test_pending_rollback.py tests/test_efeitos_allowlist.py tests/test_cashflow_forecast.py

# Frontend focal para conexão, estado, atualização e integração de página
node --test tests/frontend/of_connect_settings.test.mjs tests/frontend/of_refresh_ui.test.mjs tests/frontend/app_mode_gate.test.mjs
"$OF_PY" -m pytest -q tests/test_frontend_assets_e_rotas.py
```

Esses grupos são ponto de partida, não substitutos dos casos descritos em cada onda. Descobrir testes novos com `rg --files tests`; adicionar cobertura que atravesse o fluxo real. Se mexer em schema, limites ou navegação, incluir os respectivos testes existentes e seus consumidores.

### 10.3 Baseline, prova da correção e fechamento

1. Medir baseline antes da edição. Se o trabalho já chegou editado, reconstruir a base em worktree isolado; não chamar a versão anterior do próprio patch de baseline.
2. Fixar SHA da base e do candidato, dependências, ambiente, flags e comandos. Se mudar a base por merge/rebase, renovar a comparação.
3. Rodar grupos focais durante a implementação. Para cada grupo de regressão, demonstrar falha sem a correção e sucesso com ela; também provar que o caso legítimo continua funcionando.
4. Fazer a mutação em árvore isolada. Restaurar e conferir o diff; não deixar código sabotado, alterações de outro agente ou resultados misturados. Falha de import/fixture não prova o defeito de comportamento.
5. Testar concorrência com barreiras/locks reais e falhas injetadas nas fronteiras relevantes. Distinguir corrida entre tarefas de disputa entre processos. `sleep` e repetição aleatória não substituem uma intercalação controlada.
6. Rodar a suíte completa no candidato final e comparar falhas por node ID, motivo e fase, incluindo skips/xfails. Contagens iguais não provam zero regressão. Não testar enquanto a árvore continua mudando.

```bash
"$OF_PY" -m pytest -q --junitxml="$OF_EVID/candidato.xml" >"$OF_EVID/candidato.log" 2>&1
OF_RC=$?
printf 'Exit code do candidato: %s\n' "$OF_RC"
npm run lint
npm run test:frontend
git diff --check
```

Instalar dependências no ambiente de teste, quando faltarem, conforme o CI: `npm ci` e Chromium via Playwright. Não tratar navegador não instalado como falha de produto. Se tocar `webapp/` ou seus artefatos, executar também `npm --prefix webapp ci` e `npm --prefix webapp run build`, inspecionando o diff gerado. Não adicionar um build na raiz para isso.

Antes de usar `scripts/coluna_dupla.py`, ler seu contrato e limites atuais. Seu carimbo não substitui traceback e causa: arquivo novo ausente na base pode produzir `ModuleNotFoundError`, que não demonstra o bug. A evidência deve identificar o caso que executou e a asserção discriminante.

Falha preexistente precisa de prova na mesma base; falha nova precisa ser resolvida. Se a suíte abortar ou um teste necessário não puder rodar, o resultado é inconclusivo para aquela garantia. Em PR, conferir os checks exigidos no head atual, inclusive lint/frontend/build quando presentes; não reutilizar CI de um head anterior.

## 11. Revisão, entrega e continuidade

**Revisão interna:** Tester executa e Manager/revisor examina independentemente o candidato final, cenários negativos, caminho legítimo, possibilidade de perda, isolamento, prazo e limites das evidências. Resultado: aprovado, aprovado com ressalvas não bloqueantes, reprovado ou inconclusivo. Uma verificação necessária ausente impede chamar a onda de pronta.

**PR:** descrever problema, comportamento resultante, validação e limites. Seguir revisão externa prevista no projeto, verificar todos os apontamentos e responder nas próprias threads. Cota de revisão esgotada não significa aprovação; registrar e combinar a alternativa. Merge exige autorização explícita do dono, conforme `CLAUDE.md`.

**Deploy e dados:** conferir autorização e destino antes de publicar; identificar commit realmente servido e executar smoke após deploy. Mudança de schema precisa considerar versão antiga do app, rollback e custo de locks. Reparos/exclusões em produção exigem plano próprio com alvos, dry-run e autorização; não incluí-los num comando genérico de conclusão.

Modelo obrigatório do relatório de cada onda:

```text
Onda / objetivo / ficha:
Base SHA / candidato SHA / PR / versão implantada (se houver):
Defeitos reproduzidos e decisões de produto aprovadas:
Alterações entregues e invariantes preservadas:
Testes: comando, ambiente, código de saída, resultados e evidências:
Comparação nominal com baseline e explicação de cada diferença:
Controles negativos/positivos e limites do que eles medem:
Revisão interna / revisão externa / CI do head:
Validação real: banco/produto, interface, data, versão e resultado:
Histórico afetado / migrações / plano de rollback ou compensação:
Pendências, responsável, bloqueio e critério para retomar:
Próximo passo exato, sem pressupor que a próxima onda foi autorizada:
```

### Prompt para iniciar ou retomar com o Claude

> *Nota de 2026-10-09:* o prompt abaixo é de 14/09/2026 e manda “identificar a primeira onda ainda pendente”. Hoje a tabela do §4 dá as Ondas 4 e 5 como integradas, então a primeira pendente seria a 6. O prompt vale com as notas de 2026-10-09 acima: a premissa é a do roteiro (manual e banco convivem), a regra de despesa e receita está na ADR 0006 (PR-A mergeado em 2026-10-15) e a ordem dos PRs de código é PR-A, PR-A2, PR-B, PR-B1 e PR-C.

> Leia `docs/open_finance_roteiro_ondas_4_a_8.md` e as instruções do projeto. Confira a entrega da frente atual, a main remota, os PRs relacionados e as alterações preexistentes; não reutilize o estado de uma conversa antiga. Identifique a primeira onda ainda pendente, apresente seu plano específico e execute somente o escopo autorizado nesta tarefa, usando time-dev e baseline-testes conforme o repositório. Preserve isolamento por usuário, não apague rastros para forçar adoção e não decida semântica financeira implicitamente. Prove as correções pelos fluxos reais, compare a suíte por nome e causa, conclua revisão independente e atualize o relatório. Diferencie implementação, integração e validação real. Não faça merge, deploy ou reparo de produção sem a autorização correspondente.

**Manutenção deste roteiro:** registrar data e fonte de novas constatações. Remover do escopo o que já foi entregue, preservando link da evidência. Não acrescentar uma onda por cada achado; agrupar por contrato e verificabilidade. Estimativas de prazo só depois do inventário da onda e das decisões necessárias.
