/**
 * Semente do design system. Tokens SEMÂNTICOS, não nomes de cor: a tela pede
 * `cores.ink`, nunca `#111`. É o que torna o modo escuro uma tabela a mais, e
 * não uma caça a literais espalhados.
 *
 * O conjunto completo (§H do plano) entra na Fase 2. Aqui está só o que a
 * Fase 1 usa — criar os 40 tokens antes de existir tela seria adivinhar.
 */
export const marca = "#FF2D8E";

export const claro = {
  bg: "#FFFFFF",
  surface: "#F6F6F7",
  border: "#E6E6E9",
  ink: "#111113",
  inkMuted: "#5F5F6A",
  brand: marca,
  negative: "#D92D20",
} as const;

export const escuro = {
  bg: "#0E0E10",
  surface: "#17171A",
  border: "#2A2A2F",
  ink: "#F2F2F3",
  inkMuted: "#9B9BA6",
  // O rosa sobe de luminância no escuro: #FF2D8E sobre #0E0E10 não alcança o
  // contraste de texto. Mesma matiz, não outra cor.
  brand: "#FF4FA0",
  negative: "#F97066",
} as const;

export type Paleta = typeof claro;

/** Grade de 4pt. */
export const espaco = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32 } as const;
export const raio = { sm: 8, md: 12, lg: 20 } as const;

export const texto = {
  display: { fontSize: 40, lineHeight: 44, fontWeight: "700" },
  titulo: { fontSize: 28, lineHeight: 34, fontWeight: "700" },
  corpo: { fontSize: 16, lineHeight: 22, fontWeight: "400" },
  rotulo: { fontSize: 14, lineHeight: 18, fontWeight: "500" },
} as const;
