import type { Config } from "tailwindcss";

import { espaco, raio, texto } from "./src/ui/tokens";

// Config do Tailwind roda no Node, fora do bundler; `nativewind/preset` não
// tem tipos, daí o `require()`.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const nativewindPreset = require("nativewind/preset");

// Tokens de `espaco`/`raio` são `dp`, não `rem`: `px` aqui é literal, não
// conversão — o Tailwind exige unidade na string, e o Metro/css-interop lê o
// número antes do sufixo.
const emPx = (valores: Record<string, number>) =>
  Object.fromEntries(Object.entries(valores).map(([chave, valor]) => [chave, `${valor}px`]));

export default {
  presets: [nativewindPreset],
  content: ["./app/**/*.tsx", "./src/**/*.tsx"],
  theme: {
    // SUBSTITUÍDO, não `extend`: a paleta de cor do Tailwind não é fonte de
    // verdade aqui — quem pinta é `useTema()` com `cores.<nome>` de tokens.ts.
    // `transparent` sobrevive porque é a única cor sem estado (claro/escuro).
    colors: { transparent: "transparent" },
    // Também SUBSTITUÍDO: sem isso, o preset resolve `borderColor` como
    // função de `theme('colors')` + `DEFAULT: theme('colors.gray.200',
    // 'currentColor')` — como `gray.200` não existe mais em `colors` acima,
    // o fallback vira `currentColor` e a classe `border` pura (sem cor
    // explícita) pinta com a cor do texto, fora dos tokens.
    borderColor: { transparent: "transparent" },
    spacing: emPx(espaco),
    borderRadius: emPx(raio),
    fontFamily: {
      regular: [texto.corpo.fontFamily],
      medium: [texto.rotulo.fontFamily],
      semibold: [texto.secao.fontFamily],
      bold: [texto.titulo.fontFamily],
    },
    // Vazios de propósito: tamanho e peso de texto são só do componente
    // `Texto` (tokens.ts:112 — `fontWeight` com Inter quebra no Android).
    fontSize: {},
    fontWeight: {},
    // Mesmo motivo: `lineHeight`/`letterSpacing` são parte da tipografia do
    // `Texto` (tokens.ts), e `boxShadow` do design system usa `shadowColor`
    // nativo (`raio`/`Card`), não sombra CSS. `leading-*`/`tracking-*`/
    // `shadow-*` ficariam ativos com a escala padrão do Tailwind se não
    // fossem zerados aqui.
    lineHeight: {},
    letterSpacing: {},
    boxShadow: {},
  },
} satisfies Config;
