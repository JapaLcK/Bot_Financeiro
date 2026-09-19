---
name: "PigBank — landing pública"
description: "Tratamento visual da landing, construído sobre a identidade oficial PigBank."
colors:
  pig-pink: "#FF2D8E"
  pig-pink-support: "#F7D7E3"
  pig-neon: "#C6F11A"
  pig-black: "#111111"
  pig-offwhite: "#F6F4F1"
  landing-card: "#1a1a1a"
  landing-card-raised: "#222222"
  landing-text-muted: "#bebcba"
  landing-text-subtle: "#aaa7a5"
  landing-line: "#343434"
  landing-line-strong: "#565656"
  message-received: "#fff"
  receipt-tag-text: "#FFA9D0"
  receipt-tag-background: "rgba(255,45,142,.22)"
typography:
  display:
    fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
    fontSize: "clamp(3.2rem, 5.65vw, 5.4rem)"
    fontWeight: 800
    lineHeight: 1.08
    letterSpacing: "-.04em"
  headline:
    fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
    fontSize: "clamp(2rem, 3.5vw, 3.3rem)"
    fontWeight: 700
    lineHeight: 1.12
    letterSpacing: "-.035em"
  title:
    fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
    fontSize: "1.55rem"
    fontWeight: 700
    letterSpacing: "-.03em"
  body:
    fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.65
  label:
    fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif'
    fontSize: ".9rem"
    fontWeight: 700
rounded:
  action: "8px"
  message: "10px"
  conversation: "12px"
  receipt: "14px"
  surface: "16px"
  tag: "999px"
spacing:
  inline: "8px"
  compact: "12px"
  inset: "16px"
  content: "24px"
  card: "26px"
  plan: "32px"
  gutter-desktop: "40px"
components:
  button-primary:
    backgroundColor: "{colors.pig-pink}"
    textColor: "{colors.pig-black}"
    typography: "{typography.label}"
    rounded: "{rounded.action}"
    padding: "11px 20px"
  button-primary-hover:
    backgroundColor: "{colors.pig-pink-support}"
    textColor: "{colors.pig-black}"
  button-outline:
    backgroundColor: "transparent"
    textColor: "{colors.pig-offwhite}"
    typography: "{typography.label}"
    rounded: "{rounded.action}"
    padding: "11px 20px"
  navigation:
    backgroundColor: "{colors.pig-black}"
    textColor: "{colors.landing-text-muted}"
    padding: "22px 0"
  receipt-tag:
    backgroundColor: "{colors.receipt-tag-background}"
    textColor: "{colors.receipt-tag-text}"
    rounded: "{rounded.tag}"
    padding: "3px 9px"
  agent-card:
    backgroundColor: "{colors.landing-card}"
    textColor: "{colors.pig-offwhite}"
    rounded: "{rounded.surface}"
    padding: "26px"
  conversation-message:
    backgroundColor: "{colors.pig-pink-support}"
    textColor: "{colors.pig-black}"
    rounded: "{rounded.message}"
    padding: "10px 14px"
  step-card:
    backgroundColor: "{colors.landing-card}"
    textColor: "{colors.pig-offwhite}"
    rounded: "{rounded.surface}"
    padding: "28px 26px 30px"
---

# Design System: PigBank — landing pública

## Overview

**Creative North Star: "Clareza com a personalidade do Piggy"**

A identidade PigBank combina contraste nítido, tipografia direta e a presença acolhedora do Piggy. A landing aplica essa identidade com títulos em frase, áreas de respiro, ações rosas e superfícies escuras simples. Conversas ilustrativas tornam a personalidade da marca concreta sem comprometer a leitura dos valores.

Este documento registra o tratamento implementado em `frontend/index.html` e `frontend/site-redesign.css`, com a herança de `frontend/site.css`. `frontend/brand.css` continua sendo a fonte canônica da identidade de todo o produto. Os tokens com prefixo `landing-`, as medidas e os padrões desta página têm escopo local: não redefinem o dashboard, temas compartilhados nem outras páginas. Os tokens acima são normativos para reproduzir esta landing; alterações futuras da identidade devem começar em `brand.css` e depois atualizar esta documentação.

**Key Characteristics:**

- Contraste entre preto, off-white e rosa oficial.
- Inter existente, títulos em frase e números fáceis de comparar.
- Piggy e imagens oficiais com origem preservada.
- Superfícies simples, ações claras e estados acessíveis.

## Colors

O rosa marca ações e personalidade; neutros organizam a leitura, e o verde indica progresso ou sucesso.

### Primary

- **Rosa PigBank** (`pig-pink`): ações principais, palavras em destaque e ícones de apoio. A landing usa texto preto sobre botões rosas.
- **Rosa de apoio** (`pig-pink-support`): superfícies de conversa, respostas visuais suaves e hover do botão principal.

### Secondary

- **Verde de progresso** (`pig-neon`): conclusão de etapas, confirmação do vídeo e indicadores positivos. Seu papel continua semântico.

### Neutral

- **Preto PigBank** e **off-white PigBank**: fundo principal e texto; a relação se inverte na conversa clara.
- **Superfície escura** e **superfície ativa** (`landing-card`, `landing-card-raised`): agrupamentos e etapa aberta.
- **Texto de apoio** e **texto discreto** (`landing-text-muted`, `landing-text-subtle`): explicações, rótulos e notas.
- **Divisor** e **borda forte** (`landing-line`, `landing-line-strong`): separação entre blocos e contorno de mídia, planos ou estados ativos.
- **Branco de mensagem** (`message-received`): resposta recebida na demonstração clara. O rosa do marcador de categoria (`receipt-tag-*`) pertence ao recibo ilustrativo.

**The Brand Source Rule.** As cores oficiais vêm de `frontend/brand.css`; os aliases locais da landing não substituem os tokens semânticos do produto.

As faixas tonais do sidecar são amostras de visualização sintetizadas em OKLCH. Elas não acrescentam cores aprovadas à marca nem substituem os valores do frontmatter ou de `brand.css`.

## Typography

**Display Font:** Inter, com os fallbacks do site.
**Body Font:** Inter, com os mesmos fallbacks.

Inter é a fonte já hospedada pelo projeto, carregada por `brand.css`. Não é uma nova escolha de identidade. Os pesos fortes e o tracking negativo dão presença aos títulos; textos de apoio usam uma composição mais aberta.

### Hierarchy

- **Display**: título principal; valores desktop no frontmatter, com redução responsiva. Mantém capitalização de frase.
- **Headline**: títulos de seção e explicações principais, com quebra balanceada.
- **Title**: títulos das etapas; títulos de funcionalidades usam a escala menor observada na implementação.
- **Body**: parágrafos de introdução às seções. O corpo base herda entrelinha (1.5); explicações mais longas usam (1.65). Introduções têm largura máxima de (580px), e o subtítulo principal de (440px).
- **Label**: botões; navegação e legendas usam os tamanhos compactos próprios do componente.

Valores financeiros usam algarismos tabulares nos recibos e indicadores. Legendas de exemplo e horários permanecem como texto HTML legível, sem serem incorporados às imagens.

## Layout

O conteúdo se organiza em um contêiner central de até (1280px), com gutters de (40px), e navegação de até (1200px). A grade principal usa colunas (1.08fr / 1fr); funcionalidades e preços também usam duas colunas no desktop. Seções têm espaçamento vertical de (88px), reduzido para (58px) no celular. Essas proporções são específicas desta landing; a sequência narrativa está no briefing `.impeccable/surfaces/frontend-index-html.md`.

Até (1250px), navegação e composição ficam mais compactas. Até (1100px), a grade principal vira uma coluna e o palco da conversa fica centralizado, com largura máxima de (620px). Até (1000px), os links passam a uma segunda linha. Até (760px), as demais grades viram uma coluna, os gutters caem para (24px), a navegação pode rolar horizontalmente e a conversa mostra apenas o primeiro pedido e sua resposta. Até (370px), os gutters usam (18px). O trilho de etapas tem seu próprio limite: horizontal a partir de (900px), vertical abaixo disso.

O palco usa grid com conversa e mascote em células separadas, seguidas pela legenda no desktop. Até (760px), a conversa ocupa a primeira linha inteira; legenda e Piggy dividem a segunda. Os elementos ficam no fluxo normal, sem sobreposição do mascote sobre o texto.

As medidas em `rem` do trilho acompanham o tamanho de fonte configurado pelo usuário. Notas comerciais e legendas ficam no fluxo de leitura e próximas ao conteúdo que qualificam.

## Elevation & Depth

Por pedido do usuário, o fundo preserva a fumaça rosa original do PigBank: duas camadas fixas de gradiente radial com desfoque de (120px), opacidade de (.30 / .28) e cores oficiais. O efeito é estático, decorativo e não recebe cliques; conteúdo, navegação e rodapé ficam acima dele. A landing usa principalmente contraste tonal e linhas. Navegação, botões, cartões de agentes e trilho de etapas permanecem sem sombra. A profundidade da conversa tem função de separar seus elementos: janela de mensagens com `box-shadow: 0 16px 36px #11111124` e Piggy com `filter: drop-shadow(0 12px 15px #11111128)`.

**The Local Depth Rule.** A fumaça rosa fica restrita ao fundo e o uso de sombra permanece pontual nesta landing; o vocabulário de elevação compartilhado em `brand.css` permanece válido nas demais superfícies.

## Shapes

Os botões têm cantos curtos; os agrupamentos maiores usam cantos moderados. O frontmatter registra os raios reais da landing, incluindo mensagens, janela de conversa e marcadores de categoria. As mensagens possuem uma ponta discretamente assimétrica: canto inferior do lado do remetente reduzido a (3px). Os indicadores de conclusão e espera são círculos de (74px).

O tratamento local de botões não altera os botões pill oferecidos por `brand.css`. A navegação e as perguntas frequentes usam linhas retas e divisores, com agrupamento dado pela posição e espaçamento.

## Components

### Buttons

Diretos e fáceis de reconhecer. O primário combina rosa e texto preto; seu hover usa rosa de apoio. O botão de contorno usa off-white sobre transparência e borda forte. A altura mínima geral é (48px); a variante principal grande chega a (54px), e a ação compacta da navegação usa (44px).

O feedback do primário comprime a escala para (0.97), com transição de (160ms) e `ease-out`. Foco visível usa contorno rosa de (2px), afastado em (2px). Os links sujeitos ao vídeo mantêm contraste total e continuam acionáveis; a lógica do gate controla o destino, e `aria-describedby` associa a ação à explicação em `vsl-status`. O CTA final tem a variante escura sobre o campo rosa de apoio.

### Navigation

Logo oficial, links de peso médio e ação principal compacta. A barra permanece sticky no desktop. Seu fundo fica em uma camada de largura total, com preto translúcido, desfoque de (18px) e máscara que o dissolve na borda inferior. Assim não há recorte retangular nas laterais sobre a fumaça. A máscara não afeta links nem foco. No celular a barra entra no fluxo, transparente e sem blur. Os links recebem rosa no hover de ponteiros precisos. O link de pular para conteúdo aparece ao receber foco.

### Chips

O marcador de categoria aparece dentro do recibo demonstrativo: cápsula pequena com rosa claro sobre rosa translúcido. É informativo e não representa um filtro interativo.

### Cards / Containers

Cartões de agentes usam superfície escura, imagem oficial em proporção (2 / 1) e texto com padding de (24px), reduzido para (20px) no celular. Planos usam borda forte e padding de (32px), reduzido para (26px) no celular. O vídeo mantém proporção (16 / 9), contorno discreto e controles reais. Imagens oficiais são dimensionadas com proporção preservada.

### Problem Statement

Entre o hero e o vídeo, uma seção tipográfica liga pequenos gastos cotidianos à proposta de registro pelo WhatsApp. Usa duas colunas no desktop e empilha até 760px, com a pergunta em rosa e a explicação em texto de apoio; não representa depoimento de cliente. Hero, funcionalidades e encerramento usam descrições curtas. A explicação do período grátis e da ativação com cartão fica somente na página de preços.

### Agent Carousel

O catálogo apresenta sete agentes disponíveis e o Aviador com indicação “Em breve”. O carrossel usa rolagem horizontal nativa e scroll snap, com três cartões no desktop, dois até (1000px) e um cartão com prévia do seguinte até (760px). Setas, contador, foco visível e teclas direcionais/Home/End complementam o deslize e o trackpad. Não há avanço automático. Teclado e movimento reduzido navegam instantaneamente; os botões recebem feedback de pressão em (160ms). Sem JavaScript, os oito cartões continuam disponíveis pela rolagem nativa e os controles extras ficam ocultos.

### Conversation

A janela clara contrasta com o cabeçalho preto. Mensagens enviadas usam rosa de apoio; respostas recebidas usam branco. Os valores e horários são texto, com recibo simples e alinhamento tabular; a categoria do recibo usa fonte de (.6875rem), equivalente a (11px) na base padrão. A indicação de que a conversa é ilustrativa acompanha a demonstração em célula própria, e o mascote permanece separado das mensagens. A composição completa pertence ao briefing da superfície, não a uma regra global do produto.

### Step Rail

Uma etapa fica aberta por vez; as anteriores mostram conclusão verde, e as próximas um anel tracejado. Foco e clique dão acesso aos estados; hover também funciona em dispositivos adequados. No desktop, a etapa aberta cresce na proporção (2.7), com transição de (600ms) e `cubic-bezier(.45,0,0,1)`. Abaixo de (900px), os cartões se empilham e a mudança de altura não usa transição. As pequenas demonstrações têm animações próprias de entrada e desenho.

### FAQ and Motion

Perguntas são botões sobre linhas divisórias, com resposta expansível e indicador de estado. O conteúdo da resposta não anima sua altura neste tratamento. O site mantém seus reveals como melhoria progressiva: sem JavaScript o conteúdo permanece disponível.

`prefers-reduced-motion` desativa o scroll suave e as transições de botões e FAQ, além de reduzir as animações do trilho a duração praticamente instantânea. Nenhuma informação depende de completar uma animação.

## Do's and Don'ts

### Do:

- **Do** usar os tokens oficiais de `brand.css` para as cores PigBank e manter ajustes desta landing sob o escopo `body.rd`.
- **Do** preservar Inter, capitalização de frase, foco visível e indicadores financeiros legíveis.
- **Do** reutilizar Piggy, logo e imagens oficiais com proporção e origem preservadas.
- **Do** manter a identificação de exemplos ilustrativos junto às conversas e aos dados demonstrados.
- **Do** manter conteúdo e estados utilizáveis com teclado, no celular e com movimento reduzido.

### Don't:

- **Don't** promover os raios, a composição ou as regras locais de sombra desta landing a uma redefinição global do dashboard.
- **Don't** duplicar a fonte canônica das cores ou substituir a tipografia oficial nesta superfície.
- **Don't** alterar os arquivos oficiais para adicionar metadados: sua origem está no briefing da superfície, preservando os hashes públicos dos assets com cache immutable.
- **Don't** transformar demonstrações em evidência de clientes, depoimentos ou resultados reais.
