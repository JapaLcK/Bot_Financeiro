# Conversas com agentes — decisões de produto

Status: primeira versão implementada e validada localmente; avaliação com a IA real e publicação não realizadas.

## Decisões confirmadas

- Cada agente disponível terá um botão de conversa na aba Agentes do dashboard, abrindo seu chat específico.
- Cada agente responderá exclusivamente dentro do seu tema e redirecionará perguntas de outros temas ao agente responsável.
- Xerife: gastos fora do padrão e limites de categorias.
- Detetive: assinaturas, cobranças recorrentes e detecção e análise de lançamentos duplicados no chat.
- Carteiro: contas, boletos e vencimentos.
- Repórter: resumo financeiro, entradas, saídas e saldo.
- Banqueiro: caixinhas, metas e aportes.
- Barão: dinheiro parado e rendimento em renda fixa.
- Faria Limer: ações, FIIs e composição da carteira.
- Aviador permanece em breve.
- Os agentes poderão ensinar conceitos do próprio tema e estimular reflexão com base nos dados observados, sem indicar operações ao usuário. Exemplo permitido: sugerir avaliar diversificação ao identificar concentração da carteira. Não indicar compra de um ativo, nem com linguagem indireta como "você poderia comprar".
- A primeira versão será somente de consultas e orientações. Ações que alterem dados ficam para uma etapa posterior, após os testes.
- O chat exigirá agente ativo. Energia continuará representando capacidade de agentes ativos, sem consumo por mensagem.
- Agentes inativos poderão ser ativados quando houver capacidade. Falta de acesso ou energia deverá oferecer upsell.
- Histórico e contexto separados por agente: fechar e reabrir o painel mantém a conversa; recarregar a página limpa tanto as mensagens quanto o contexto recebido pela IA.
- Redirecionamento com botão para abrir o agente correto, levando apenas a pergunta original preenchida para o usuário enviar, sem transferir todo o histórico. Se o destino estiver inativo, oferecer ativação; sem acesso ou energia, apresentar upsell.
- Perguntas com múltiplos temas recebem resposta à parte do agente atual e encaminhamento para a outra parte. Quando nenhum agente atender ao assunto, explicar o limite sem upsell. Saudações e dúvidas sobre o próprio chat são permitidas.
- O maior plano terá energia suficiente para todos os agentes disponíveis ativos ao mesmo tempo.
- As conversas dos agentes compartilharão a cota existente do chat geral, sem consumo de energia por mensagem.

## Implementação

- Endpoint autenticado `POST /agents/{user_id}/{kind}/chat`, com o gate de Agentes, validação de acesso/energia e limite de 12 requisições por minuto.
- Contexto criptografado e autenticado, vinculado ao usuário e ao agente, mantido apenas na memória da página. Não utiliza nem modifica o histórico ou as ações pendentes do chat geral. A janela de contexto inclui até 20 mensagens e o token expira após 24 horas.
- Classificação de tema antes da resposta. Em perguntas mistas, apenas a parte própria segue para o especialista; os destinos são validados contra o catálogo.
- Ferramentas de consulta permitidas explicitamente por agente. Despacho bloqueia ferramentas de outros temas e ferramentas de escrita, mesmo que o modelo as solicite.
- Verificação adicional da resposta gerada quanto ao tema, alterações e indicações de operações. Essa verificação é feita por IA e não representa garantia absoluta de classificação semântica.
- Detetive consulta os mesmos sinais de duplicidades e recorrências dos alertas, sem emitir eventos. A detecção atual de duplicidades cobre a janela e o valor mínimo do detector existente; ausência de indícios não prova ausência de duplicidades.
- Barão lê o último saldo registrado, sem aplicar juros durante a consulta. Barão e Faria Limer filtram renda fixa em BRL antes de agregar, limitam detalhes e informam a cobertura das listas. Faria Limer recebe o total integral da renda fixa manual separado da amostra. Caixinhas e outras moedas não entram nesses retratos de renda fixa: zero não prova ausência de renda fixa, nem o cadastro representa necessariamente todo o patrimônio.
- Cota compartilhada descontada atomicamente. O chat geral reserva a vaga antes de gravar o turno ou despachar ferramentas; uma requisição sem vaga não executa alterações nem cria ações pendentes. A leitura da cota não reseta nem grava o contador: o reset ocorre no consumo atômico, e uma reserva com mês antigo não pode retroceder o período. Falhas sem tentativa de escrita ou sincronização devolvem a reserva somente no mês correspondente, inclusive se a resposta foi gerada mas não pôde ser salva no histórico. Após tentativa de escrita, a reserva é mantida para não liberar capacidade sobre uma alteração possivelmente já efetivada. Os especialistas, que só consultam, descontam após responder. Redirecionamentos puros, assuntos sem agente e falhas do chat especialista não descontam mensagens. Perguntas mistas com resposta própria descontam uma mensagem.
- Carteiro consulta contas já registradas sem gerar instâncias; Banqueiro lê caixinhas com aplicação de juros desabilitada. Argumentos do modelo não podem habilitar essas gravações. As versões do chat geral que sincronizam dados ou aplicam juros são identificadas como tendo efeitos colaterais para proteger a reserva.
- A reserva identifica o mês e os IDs das contas que receberam o incremento. A restituição filtra essas contas e esse mês, preservando contas já esgotadas, criadas posteriormente e consumo de outras conversas. A implementação de cota fica em `db/ai_quota.py`, com a API existente preservada por `db/ai_chat.py`.
- A variante pura do Banqueiro informa a data disponível do saldo e que não atualizou juros; progresso e metas usam o último saldo registrado. A prateleira informa `can_chat` pelo mesmo gate do backend, separado da ativação gratuita permitida no legado. Respostas antigas de abertura são descartadas antes de alterar a sessão ou o cache.
- Barão e Faria Limer usam a listagem leve de investimentos, sem consultar ou materializar lotes. Saldo, taxa e cobertura vêm dos registros dos investimentos; os demais consumidores da listagem continuam recebendo lotes por padrão.
- Uma validação recusada ou um erro ao preparar o resumo não marca tentativa de escrita. Essa marca começa antes da gravação da confirmação, da execução automática ou de uma consulta com efeitos; permanece ativa se outra ferramenta for recusada depois no mesmo turno.
- O histórico de alertas consulta os 20 eventos recentes do próprio agente, filtrando o tema antes do limite. Eventos obsoletos ficam fora; leitura ou supressão de e-mail não retiram o evento do histórico. O feed geral mantém sua consulta sem filtro por agente.
- Ações financeiras permanecem fora desta versão. Ativar um agente é uma ação explícita do botão de acesso.

## Fatos do sistema atual

- A integração ao chat reutiliza e testa a detecção de duplicidades do serviço dos agentes.
- O chat geral tem histórico compartilhado por usuário e ferramentas de múltiplos temas, incluindo alterações com confirmação. O chat especialista possui isolamento de contexto e acesso somente a consultas.
- Os alertas automáticos do Faria Limer continuam factuais. A conversa também permite reflexão educativa, sem indicação de operações específicas.

## Verificação da interface

- O painel amplia a linguagem existente do dashboard: fonte da marca herdada, cores e superfícies pelos tokens locais, avatares oficiais e botões de conversa nos cartões disponíveis. `PRODUCT.md`, `DESIGN.md` e seu sidecar permanecem com o escopo original da landing, sem redefinir o dashboard.
- O código mantém foco visível, rótulo do campo, anúncio de mensagens e estados, retorno de foco ao fechar, alvos de ação de pelo menos 44px e dimensões adaptadas à área útil no celular. O corpo da conversa usa 14px, o campo 16px e o título 17px.
- A revisão visual independente aprovou as capturas desktop e celular sem achados materiais. As capturas usaram dados simulados com o HTML/CSS reais do painel; essa evidência não substitui a validação da integração com a API e a IA.


## Validação técnica

- 170 testes Python únicos aprovados entre o chat especialista, detectores, energia, ícones e regressões do chat geral; incluem concorrência na cota e consulta real de duplicidades em Postgres descartável.
- 6 testes de navegador do chat aprovados: histórico por agente, recarga, encaminhamento, recuperação de erro, ativação/upsell, adaptação desktop/celular e resposta em andamento durante a troca de agente.
- 2 testes de integração de scripts/handlers do dashboard aprovados, sem ampliar a baseline de handlers inline.
- JavaScript sem erros de lint; permanecem avisos de complexidade/tamanho de funções. Python compilado e diff sem erros de whitespace.
- Chamadas à IA foram simuladas nos testes. O ambiente de execução precisa das configurações existentes de OpenAI e JWT. Não houve publicação.

- Revisão de cota e consultas: 176 testes aprovados, incluindo leitura atrasada na virada do mês, consultas sem sincronização/aplicação de juros e restituição após falha de persistência.

O inventário completo das alterações, estados testados e impactos da revisão de 13/09/2026 está em [revisao_conversas_agentes_pr412.md](revisao_conversas_agentes_pr412.md). As contagens anteriores acima são registros históricos da implementação, não uma baseline para reutilizar; remedir com os comandos do inventário.
