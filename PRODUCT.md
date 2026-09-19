# PigBank

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Pessoas que querem organizar suas finanças pessoais pelo WhatsApp. A orientação de marca existente (`.claude/skills/pigbank-frontend/SKILL.md`) identifica jovens de 18 a 24 anos como público principal; essa segmentação vem do projeto e não foi revalidada em pesquisa nesta tarefa.

## Product Purpose

Registrar despesas e receitas, consultar saldo e acompanhar a vida financeira conversando com o Piggy. O objetivo confirmado para a landing é transformar visitantes em cadastros.

## Operating Context

O WhatsApp é a entrada principal apresentada pelo produto, com mensagens de texto, áudio e fotos de comprovantes. O dashboard complementa a conversa no navegador. O app iOS existente usa Capacitor para carregar a interface web; não constitui uma linguagem visual nativa independente.

## Capabilities and Constraints

- Preservar funcionalidades reais e os fluxos existentes de cadastro (`/cadastro`) e login (`/login`).
- Preservar o gate de vídeo existente, sua liberação quando o vídeo falha, a memória de conclusão e o acesso direto ao dashboard para usuários autenticados.
- A landing apresenta lançamentos, cartões, caixinhas e acompanhamento financeiro. Referências: `frontend/index.html`, `frontend/funcionalidades.html`, `frontend/comandos.html`.
- Open Finance é apresentado como acesso autorizado somente para leitura. PigBank não é banco nem corretora e não movimenta, transfere ou guarda dinheiro.
- Trabalhar na arquitetura atual: HTML/CSS/JavaScript servido pelo FastAPI. A ilha React de preços tem build separado; não criar um framework ou build na raiz para a landing.
- Preços e condições comerciais devem vir das fontes atuais do projeto; não inventar depoimentos, clientes, métricas ou funcionalidades. Conversas demonstrativas precisam ser identificadas como exemplos.
- Manter descrições curtas na landing. Explicar o período grátis e suas condições somente na página de preços, conforme orientação do usuário.
- Preparar e verificar uma prévia antes de publicar. Publicação não está autorizada nesta tarefa.

## Brand Commitments

Marca PigBank e mascote Piggy, com assets oficiais do repositório. O Piggy é masculino: usar “o Piggy”, “do Piggy” e “o mascote”, com concordância masculina. Português brasileiro. Preservar as cores PigBank definidas em `frontend/brand.css`: rosa principal `#FF2D8E`, preto `#111111`, off-white `#F6F4F1`, rosa de apoio `#F7D7E3` e verde `#C6F11A` associado a sucesso/progresso. O usuário autorizou liberdade para redesenhar a composição mantendo a marca e essas cores.

## Evidence on Hand

- Logo, mascote e stickers: `frontend/brand/`.
- Vídeo de apresentação existente: `frontend/brand/vsl.mp4` e `vsl-poster.jpg`.
- Funcionalidades e exemplos: páginas públicas existentes e documentação em `docs/`.
- Não foram fornecidos depoimentos ou métricas de clientes verificados para este redesign.

## Product Principles

- Demonstrar a utilidade com situações de uso compreensíveis.
- Deixar a ação de cadastro clara e preservar as regras atuais do fluxo.
- Tratar dados financeiros com privacidade e comunicar limites com precisão.
- Usar a personalidade do Piggy sem dificultar leitura ou navegação.
- Priorizar uma experiência funcional no celular e no desktop.

## Open Decisions

A métrica de conversão de referência e a meta numérica de melhoria não foram definidas; o redesign não deve prometer um aumento de conversão sem medição.
