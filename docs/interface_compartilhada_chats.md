# Interface dos chats do PigBank

Integração do componente de mensagens fornecido pelo usuário em 13/09/2026.
Decisões e limites estão no [ADR](adr/0001-interface-compartilhada-dos-chats.md).

## O que muda

1. Piggy e agentes usam o mesmo componente de apresentação: cabeçalho com
   mascote, bolhas com autoria, indicador de resposta, ações e campo de texto.
   A composição do exemplo foi adaptada aos temas claro/escuro e ao rosa PigBank.
2. Desktop mantém painel flutuante; no celular, inclusive em paisagem com
   apontador de toque e até 960px, a conversa ocupa a área visível. Safe areas e mudanças do teclado ajustam a altura e o topo.
3. Abrir um chat recolhe o outro. Mensagens, rascunhos e requisições em andamento
   continuam vinculados ao atendimento original. Respostas tardias não abrem
   outro painel nem sobrescrevem a conversa visível.
4. Entrada e saída das mensagens têm animações breves, com preferência de
   movimento reduzido respeitada inclusive quando muda com a página aberta.
   O indicador representa uma requisição real, sem atrasos artificiais.
5. O leitor pode subir no histórico sem ser arrastado para o fim a cada resposta.
   Trocar de especialista abre seu histórico no fim; fechar e reabrir o mesmo
   preserva a rolagem. Um controle permite voltar às mensagens recentes. Enter envia, Shift+Enter
   insere uma linha e composição de texto não dispara envio.
6. O Piggy mantém formatação simples. Links só ficam clicáveis com protocolo
   HTTP(S) ou caminho local; React trata os demais conteúdos como texto.
   Os especialistas continuam exibindo texto, preservando quebras de linha.

O exemplo enviado incluía mensagens fictícias, resposta automática, autoplay e
replay. Esses modos pertencem à demonstração, não à integração: o Piggy pode
executar ações financeiras, por isso a interface não repete pedidos sozinha.
Os retries explícitos dos agentes preservam seu contrato anterior.

## Organização e efeitos

`webapp/src/components/ui/chat-messages.tsx` é o componente compartilhado.
`webapp/src/chat/` contém os tipos, a montagem e a coordenação de visibilidade.
O alias `@/` aponta para `webapp/src`, de modo que `@/components/ui` representa
a pasta de componentes da ilha, não uma pasta nova na raiz do backend Python.
`components.json` registra a estrutura compatível com shadcn.

Os controladores `dashboard-chat.js` e `dashboard-agent-chat.js` mantêm seus
transportes separados. React é o único proprietário dos elementos internos dos
painéis. O FAB e a prateleira de agentes continuam pertencendo ao dashboard.
O Piggy preserva o FAB arrastável no app e o contador a partir de 80% da cota.
Os handlers inline `closePiggy`, `piggyAsk`, `piggyAutoresize`, `piggyKeyDown`
e `piggySend` saíram do markup; o inventário nominal foi atualizado apenas
nesses nomes. Seus comportamentos são exercitados pela integração React real.

O bundle `chat-app.js` e sua folha `chat-app.css` são compilados separadamente
da ilha de preços, servidos por rotas explícitas e versionados no HTML. As
classes de Tailwind recebem prefixo e escopo; não há reset global. O CSS antigo
dos painéis foi removido junto dos overrides de geometria do modo app. A folha
da ilha cuida dos dois painéis e de suas áreas seguras.

O backend conserva histórico, contexto, cobrança, autorização e ações. O Piggy
limpa a tela ao recarregar, mas seu contexto de backend continua persistido;
agentes reiniciam tanto a tela quanto o contexto. O visual compartilhado não
significa compartilhamento das mensagens entre esses atendimentos.

## Compilar e verificar

```sh
npm --prefix webapp ci
NODE_ENV=production npm --prefix webapp run build
npm --prefix webapp run typecheck
npm run test:frontend
python -m pytest -q
```

O ambiente de pytest deve usar PostgreSQL descartável conforme a skill
`baseline-testes`. A variável NODE_ENV explícita evita que um ambiente de
desenvolvimento produza bundles de depuração. Os artefatos compilados fazem
parte do commit; o build do CI confere sua reprodução.

Os testes de navegador usam os bundles reais e APIs simuladas. Cobrem
alternância, rascunhos, respostas tardias, recuperação dos agentes, ausência de
reenvio automático do Piggy, formatação, teclado, temas e geometria mobile.
A verificação HTTP cobre entrega, cache e ordem dos scripts da página real.
Essa validação não substitui a conferência no site após publicação.
