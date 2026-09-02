---
name: pigbank-frontend
description: Audita, cria e refina interfaces do PigBank. Use em tarefas relacionadas a páginas, componentes, landing pages, dashboard, responsividade, tipografia, animações e aparência visual. Não use para tarefas exclusivamente de backend.
---

# PigBank Frontend Design

Tarefa atual:

$ARGUMENTS

## Objetivo

Criar interfaces reconhecíveis como PigBank, evitando aparência genérica de template de fintech, SaaS ou site produzido por IA.

O resultado deve ser jovem, marcante, confiável e específico para um assistente financeiro integrado ao WhatsApp.

## Antes de alterar

1. Leia todos os arquivos `CLAUDE.md` aplicáveis.
2. Inspecione a implementação atual da página.
3. Procure componentes, funções, estilos, tokens e assets existentes.
4. Entenda o objetivo da página e sua ação principal.
5. Preserve o comportamento funcional existente.
6. Se o pedido for apenas análise ou planejamento, não altere arquivos.
7. Se o pedido autorizar implementação, faça a menor alteração suficiente.

Nunca faça afirmações sobre arquivos que não foram abertos.

## Identidade do PigBank

- Produto: assistente financeiro integrado ao WhatsApp.
- Público principal: jovens de 18 a 24 anos.
- Personalidade: inteligente, jovem, amigável, irreverente e confiável.
- Cor principal: `#FF2D8E`.
- Base visual: branco, preto e tons neutros.
- Verde deve representar principalmente sucesso, crescimento ou valores positivos.
- Mascote principal: Piggy.
- Os agentes do PigBank podem participar da narrativa visual.
- Utilize somente logos, personagens e assets oficiais do projeto.
- O visual pode ser divertido, mas não infantil.
- Evite utilizar rosa em todas as superfícies.

## Landing pages e marketing

Podem ser mais expressivos e narrativos.

Priorize:

- Demonstração real da experiência pelo WhatsApp.
- Piggy e os agentes integrados à composição.
- Hierarquia tipográfica clara.
- Composições que expliquem o produto visualmente.
- Uma ação principal evidente.
- Movimento concentrado em momentos importantes.
- Personalidade da marca acima de tendências visuais.

A primeira seção deve comunicar rapidamente:

1. o que é o PigBank;
2. que ele funciona pelo WhatsApp;
3. por que isso simplifica a vida financeira;
4. qual ação o visitante deve realizar.

## Dashboard e produto

Devem ser mais discretos e funcionais.

Priorize:

- Leitura rápida dos valores.
- Hierarquia entre saldo, entradas, saídas e alertas.
- Comparações fáceis de compreender.
- Densidade adequada de informação.
- Estados vazios úteis.
- Consistência visual.
- Responsividade e acessibilidade.

No dashboard, clareza financeira é mais importante que impacto visual.

## Tipografia

- Preserve a Inter nas áreas do produto onde ela já é utilizada.
- Não substitua globalmente a fonte sem solicitação explícita.
- Páginas de marketing podem usar uma fonte de destaque nos títulos.
- Não escolha fontes apenas porque estão populares em sites feitos por IA.
- Evite títulos gigantes sem função real.

## Evitar aparência genérica

Evite:

- Hero centralizado com badge, título em gradiente e dois botões genéricos.
- Gradientes roxos ou azuis sem relação com a marca.
- Glassmorphism puramente decorativo.
- Fileiras de cards praticamente idênticos.
- Um ícone dentro de um quadrado colorido em cada funcionalidade.
- Excesso de pills, sombras e bordas arredondadas.
- Numeração decorativa como `01`, `02` e `03`.
- Textos genéricos como “revolucione sua vida financeira”.
- Animações espalhadas sem hierarquia.
- Seções criadas apenas para preencher espaço.
- Depoimentos, números ou funcionalidades inventadas.

Esses recursos podem ser utilizados quando o conteúdo realmente os justificar.

## Implementação

- Reutilize componentes, estilos, tokens e funções existentes.
- Siga as regras de organização presentes no `CLAUDE.md`.
- Escreva o mínimo de código necessário.
- Não adicione um novo framework apenas para uma mudança visual.
- Não crie abstrações para operações utilizadas uma única vez.
- Não concentre páginas inteiras em arquivos gigantes.
- Preserve rotas, integrações e regras de negócio.
- Não altere o backend em tarefas exclusivamente visuais.
- Respeite a arquitetura atual do PigBank.
- Garanta funcionamento em desktop e mobile.
- Respeite `prefers-reduced-motion`.
- Preserve contraste, foco visível e navegação por teclado.

## Verificação

Depois de implementar:

1. Execute o projeto.
2. Abra a página no navegador.
3. Verifique desktop e mobile.
4. Confirme que o comportamento anterior continua funcionando.
5. Procure overflow, desalinhamento e textos quebrados.
6. Corrija os problemas encontrados.
7. Informe quais arquivos foram modificados e como o resultado foi verificado.

Não considere uma alteração visual concluída apenas porque o código compilou.
