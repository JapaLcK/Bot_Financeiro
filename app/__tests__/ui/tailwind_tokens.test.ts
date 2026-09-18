import resolveConfig from "tailwindcss/resolveConfig";

import { espaco, raio } from "@/ui/tokens";

import tailwindConfig from "../../tailwind.config";

/**
 * Prova que `tailwind.config.ts` não é uma segunda fonte de verdade: o
 * `theme` resolvido tem de bater com `espaco`/`raio` de tokens.ts, e não pode
 * expor cor nenhuma além de `transparent` (quem pinta é `useTema()`).
 */
/** `{ xs: 4 }` -> `{ xs: "4px" }`, mesma conversão do `emPx` de `tailwind.config.ts`. */
const emPx = (valores: Record<string, number>) =>
  Object.fromEntries(Object.entries(valores).map(([chave, valor]) => [chave, `${valor}px`]));

describe("tailwind.config", () => {
  const { theme } = resolveConfig(tailwindConfig);

  it("usa EXATAMENTE o espaçamento de tokens.ts (nada a mais, nada a menos)", () => {
    // `toEqual`, não um loop de `toBe` por chave: um loop só prova que as
    // chaves de `espaco` existem em `theme.spacing`, e passa verde mesmo se
    // `spacing` estiver dentro de `extend` (que MISTURA com a escala padrão
    // do Tailwind em vez de substituí-la — `p-4` voltaria a valer 1rem).
    expect(theme.spacing).toEqual(emPx(espaco));
  });

  it("usa EXATAMENTE o raio de tokens.ts (nada a mais, nada a menos)", () => {
    expect(theme.borderRadius).toEqual(emPx(raio));
  });

  it("não expõe cor nenhuma além de transparent", () => {
    expect(Object.keys(theme.colors)).toEqual(["transparent"]);
  });

  it("borderColor não herda DEFAULT: currentColor do preset (classe `border` pura)", () => {
    // Controle negativo medido: comentando `borderColor` em
    // tailwind.config.ts, este `toEqual` falha porque o preset resolve
    // `DEFAULT` como `theme('colors.gray.200', 'currentColor')` — como
    // `gray.200` não existe em `colors` (só `transparent`), o fallback é
    // `currentColor`, que teria passado batido num `toContain`/loop de chave.
    expect(theme.borderColor).toEqual({ transparent: "transparent" });
  });

  it("não define fontSize nem fontWeight (só o componente Texto define)", () => {
    // Tamanho/peso de fonte fora do Tailwind por design (comentário em
    // tailwind.config.ts): `font-bold` geraria fontWeight "700" — quebra a
    // Inter no Android — e `text-lg` geraria um fontSize fora de `texto`.
    expect(theme.fontSize).toEqual({});
    expect(theme.fontWeight).toEqual({});
  });

  it("não define lineHeight, letterSpacing nem boxShadow", () => {
    expect(theme.lineHeight).toEqual({});
    expect(theme.letterSpacing).toEqual({});
    expect(theme.boxShadow).toEqual({});
  });
});
