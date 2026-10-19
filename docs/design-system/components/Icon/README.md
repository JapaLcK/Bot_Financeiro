Ícone Phosphor regular desenhado em SVG, com os ícones que o app usa (o tipo `NomeIcone`) (`app/src/ui/componentes/Icone.tsx`, paths do `phosphor-react-native` 3.0.6).

**Quem fornece:** `name` (nome em PascalCase, como no app: `Wallet`, `CaretRight`; a lista está em `PigBank.iconNames`), `size` (padrão 24) e `tone` (token de cor, padrão `ink`). `color` aceita uma cor de dado (`var(--serie-1)`) e vence o `tone`.

O ícone é decorativo (`aria-hidden`): o rótulo vem do texto ao lado ou do `aria-label` do botão. Nunca ponha ícone dentro de quadrado colorido, e não pinte ícone de chrome com `brand`. Nome desconhecido desenha `Question`.
