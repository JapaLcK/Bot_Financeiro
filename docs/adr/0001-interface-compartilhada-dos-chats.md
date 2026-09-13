# Interface compartilhada das conversas

Em 13/09/2026, o usuário escolheu adaptar o componente React de mensagens
fornecido à identidade PigBank, nos temas claro e escuro. Ambos os chats usam
painel no desktop, tela inteira no celular e apenas uma conversa visível por
vez; trocar de conversa preserva mensagens, rascunhos e requisições em andamento.

A ilha React de `webapp/` passa a possuir a apresentação dos dois chats, com
controladores distintos para as APIs existentes. O Piggy mantém ações e contexto
persistido no backend; os agentes mantêm contexto efêmero por especialista e
somente consultas. A interface comum não unifica esses contratos nem reproduz
mensagens automaticamente.

O pedido explícito de integrar o componente introduz TypeScript e Tailwind
apenas nesta ilha. Tailwind 3.4 mantém a compatibilidade de navegadores do
projeto; classes prefixadas, ausência de Preflight e processamento exclusivo
do CSS do chat evitam alterar a ilha de preços e o restante do dashboard.
O build continua separado da raiz, com IIFE, arquivos de nome fixo e rotas
explícitas no backend. A alternativa de migrar o dashboard inteiro aumentaria
o alcance sem necessidade para compartilhar estes componentes.
