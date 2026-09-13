# Recuperação e qualidade da conversa dos agentes

Registro de 13/09/2026, comparado ao commit `7903c309` (publicação do PR #412).
Os resultados abaixo são medições desta revisão; remedir antes de reutilizá-los
como baseline.

## Problema reproduzido

As perguntas dos screenshots foram executadas com IA real e dados sintéticos.
A OpenAI retornava HTTP 200, mas o roteador tratava perguntas do Repórter como
pertencentes ao Carteiro e reescrevia pedidos como “Quais ações devo investir?”
com o texto de exemplo do contrato: “parte do próprio tema ou vazio”. O verificador
também recusava respostas educativas. Essas falhas diferentes viravam o mesmo 503.
A perda da pergunta atual favorecia a repetição do resumo da carteira.

## Inventário das alterações e impactos

1. **Roteamento** (`agent_chat.py`, `agent_chat_policy.py`): contrato JSON Schema
   estrito com uma lista de partes, cada uma atribuída a um responsável. Uma
   pergunta inteiramente do agente preserva o texto original. Perguntas mistas
   passam apenas o trecho próprio à resposta; os outros trechos mantêm os botões
   de encaminhamento e seus gates de acesso. O roteamento informa quando a
   resposta exige consulta dos registros pessoais.
2. **Resposta e verificação**: instruções para responder à intenção atual e
   aprofundar continuações, ensinar critérios e respeitar o recorte cadastrado.
   Consultas necessárias usam `tool_choice=required`. Uma resposta recusada pode
   ser corrigida uma vez e passa novamente pela verificação antes de ser exibida.
   A correção não entra no contexto como um turno adicional nem consome outra
   mensagem. O verificador recebe pergunta, resposta e evidências das consultas.
3. **Falhas de dados**: uma exceção ou erro retornado por uma consulta permitida
   interrompe a resposta; não pode se transformar em “você não possui dados”.
   As consultas e limites dos snapshots foram movidos para `agent_chat_data.py`
   sem mudar os cálculos, filtros ou efeitos dos consumidores existentes.
4. **Contrato de erro** (`agent_chat_errors.py`, rota HTTP): causa, status,
   possibilidade de tentar novamente e identificador local de diagnóstico.
   Logs registram etapa, classe e código, sem pergunta, resposta, corpo do erro
   do provedor, credenciais ou traceback. A cobrança continua depois da resposta
   validada. Se falhar a confirmação da gravação da cota, não há promessa de
   restituição nem incentivo a uma nova tentativa imediata.
5. **Painel** (`dashboard-agent-chat.js`, CSS limitado a `.agent-chat-*`): bolhas
   para usuário e agente, incluindo espera e falha. A pergunta aparece ao enviar
   e fica na conversa se houver erro. A tentativa explícita reutiliza o turno e
   seu contexto, preservando um rascunho novo. O estado é isolado por agente.
6. **Recuperação**: erros com e sem JSON, queda de rede, cota, ativação, upsell e
   contexto expirado têm caminhos próprios. A interface não afirma que a cota
   foi preservada quando perdeu a resposta da rede. Um contexto expirado pode
   ser reiniciado sem apagar o rascunho atual. Ativação pendente mostra progresso
   e desabilita o botão.
7. **Avaliação reproduzível** (`scripts/eval_agent_chat.py`): usa a API real com
   registros inteiramente sintéticos, bloqueia conexões ao banco e substitui
   acesso/cota por fixtures. Não busca credenciais. Os controles automáticos
   verificam resposta, destinos, consultas e cobrança; o conteúdo precisa também
   de leitura semântica. Passar os controles não equivale a aprovar a resposta.

O chat geral conserva seu modelo e seu fluxo de ações. O chat especialista
continua sem ferramentas de escrita, com histórico efêmero separado, sem mudar
energia, planos, notificações automáticas, tabelas ou migrações.

## Modelo e custo operacional

O padrão exclusivo do especialista passa a ser `gpt-5.4-mini`, configurável por
`AGENT_CHAT_MODEL`. A classificação e a revisão usam raciocínio `low`; a etapa
que disponibiliza ferramentas usa `none`, combinação aceita pelo provedor nos
testes reais. Essa etapa rejeitou `low` com HTTP 400 no parâmetro
`reasoning_effort`; usar apenas um mock do SDK não detectava a incompatibilidade.
Os limites usam `max_completion_tokens`, incluindo espaço para raciocínio nas
etapas sem ferramentas. Os modelos anteriores mantêm os parâmetros existentes.
Uma troca de configuração exige nova avaliação semântica, além dos testes do SDK.

Na [tabela oficial do modelo](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
consultada em 13/09/2026, tokens custam US$ 0,75 por milhão de entrada e US$ 4,50
por milhão de saída, sem considerar desconto por cache. O custo por conversa
depende do histórico, das consultas e de eventual correção da resposta. A cota
do produto continua sendo por mensagem respondida, não por chamada ao provedor.
Timeout e tentativas de transporte continuam definidos pelo runner existente;
esse timeout vale por chamada, não como prazo total de um turno.

Na execução final de `python scripts/eval_agent_chat.py --suite extended`, em
13/09/2026, os 19 casos passaram pelos controles automáticos e pela leitura
semântica independente. Foram 57 chamadas ao provedor, 58.066 tokens de entrada
e 6.989 de saída: aproximadamente US$ 0,075 sem desconto de cache. Houve 16
mensagens cobradas na fixture e três encaminhamentos puros sem cobrança. Essa
medição descreve a amostra, não uma previsão de custo mensal nem uma garantia
universal de resposta correta.

As perguntas cobrem os exemplos do relato, educação nos sete temas, os
encaminhamentos Carteiro → Repórter, Detetive → Barão e Barão → Carteiro,
continuação após objetivo/prazo/reserva, pedido de compra específica e explicação
de P/L. O Detetive diferenciou periodicidade de autorização; o Faria Limer
aprofundou critérios sem escolher ativos ou sugerir aumento de exposição.

## Estados e recuperação

| Situação | Resultado esperado |
| --- | --- |
| Consulta concluída | Resposta na bolha, contexto atualizado e uma mensagem descontada |
| Encaminhamento puro | Botão do destino; sem consulta de dados e sem desconto |
| Parte própria e outro tema | Resposta própria e encaminhamento; um desconto |
| Nenhum agente atende | Explicação do limite; sem upsell e sem desconto |
| Falha antes da cobrança | Pergunta preservada; mensagem da causa informa ausência de desconto |
| Timeout, resposta inválida ou indisponibilidade | Nova tentativa explícita no mesmo turno |
| Falha ao confirmar cobrança | Mensagem de incerteza; sem botão de repetir |
| Falha de rede no navegador | Sem afirmar se houve desconto; usuário decide nova tentativa |
| Agente pausado, sem energia ou sem plano | Ativação ou upsell conforme o mesmo gate do backend |
| Contexto expirado | Reinício explícito preserva o rascunho |
| Fechar e reabrir / trocar agente | Conversa, erro, rascunho e pedido em andamento preservados por agente |
| Recarregar / trocar usuário | Histórico e contexto anteriores descartados |

## Verificação e limites

A reprodução anterior e a avaliação real usam dados sintéticos: não afirmam
que uma carteira real esteja correta. A revisão visual usa o HTML/CSS do painel
em Chromium, em desktop/celular e temas claro/escuro. O endpoint real em produção
precisa ser conferido após a publicação deste reparo.

Comandos de referência, em ambiente com dependências instaladas e PostgreSQL
descartável configurado conforme a skill `baseline-testes`:

```sh
python -m pytest -q
npm run test:frontend
python -m pytest tests/test_agent_chat*.py tests/test_ai_chat_*.py -q
python scripts/eval_agent_chat.py --suite extended --output /tmp/agent-chat-eval.json
git diff --check
```

No PostgreSQL 15 local, as mesmas quatro falhas de normalização de acentos de
`tests/test_budget_category_accent.py` ocorrem na base e no reparo. A comparação
foi feita pelos nomes dos testes, incluindo os dois testes do donut com categorias
gêmeas, o CRUD com outra grafia e o upsert determinístico. Não são regressões
introduzidas nesta alteração. O CI usa seu próprio ambiente PostgreSQL.

O teste de navegador `home_upgrade_success_nao_isenta.test.mjs`, caso
“webhook em 21000 ms”, também falhou isoladamente na base e no reparo com a
mesma asserção de navegação para `/precos`. Esse cenário temporal da Home não
carrega o módulo de chat alterado. A suíte completa anterior passou; na repetição
com a regressão de ativação, somente esse caso oscilou.

Os testes de regressão da intenção, consulta obrigatória e correção da resposta
foram executados antes do conserto, produzindo falhas; os caminhos permitidos
também são exercitados. Mocks cobrem estados e invariantes, enquanto a avaliação
real cobre erros de interpretação que esses mocks não detectam.

Resultados finais medidos em 13/09/2026: `python -m pytest -q` teve 7.972 testes
aprovados, quatro falhas de acentos já presentes na base e quatro xfails.
`npm run test:frontend` teve 636 aprovados e a falha temporal da Home descrita
acima; os 28 testes específicos do chat passaram. Nenhuma nova falha permaneceu
na comparação por nome com a base. Reexecutar os comandos para obter a baseline
de outro commit ou ambiente.
