/**
 * Semente do design system. Tokens SEMÂNTICOS, não nomes de cor: a tela pede
 * `cores.ink`, nunca `#111`. É o que torna o modo escuro uma tabela a mais, e
 * não uma caça a literais espalhados.
 *
 * Conjunto completo da Fase 2 (plano §Tokens). `negative` saiu: virou `danger`
 * / `onDanger`, com o par de contraste (texto sobre fundo) já resolvido — o
 * claro antigo (`#D92D20`) passa AA sobre `bg` (4,83:1) mas reprova sobre
 * `surface` (4,47:1, abaixo do mínimo de 4,5), e `danger` aparece sobre os dois
 * fundos (banner, texto de erro em card). Virou `#B42318`.
 */
export const marca = "#FF2D8E";

/**
 * Forma da paleta, não os valores: `string` em cada chave (não o hex literal
 * de `claro`) é o que permite `escuro` — com hex diferente em toda chave —
 * carregar o MESMO tipo. É a anotação abaixo, e não `typeof claro`, que faz
 * `escuro` provar "mesmas chaves" no compilador.
 */
export interface Paleta {
  bg: string;
  surface: string;
  surfaceRaised: string;
  border: string;
  ink: string;
  inkMuted: string;
  inkFaint: string;
  brand: string;
  brandSoft: string;
  brandInk: string;
  acao: string;
  onAcao: string;
  positive: string;
  warning: string;
  danger: string;
  onDanger: string;
}

export const claro: Paleta = {
  bg: "#FFFFFF",
  surface: "#F6F6F7",
  surfaceRaised: "#FFFFFF",
  border: "#E6E6E9",
  ink: "#111113",
  inkMuted: "#5F5F6A",
  inkFaint: "#8A8A94",
  brand: marca,
  brandSoft: "#FFE6F1",
  brandInk: "#C7186B",
  acao: "#C7186B",
  onAcao: "#FFFFFF",
  positive: "#067647",
  warning: "#B54708",
  danger: "#B42318",
  onDanger: "#FFFFFF",
};

export const escuro: Paleta = {
  bg: "#0E0E10",
  surface: "#17171A",
  surfaceRaised: "#202024",
  border: "#2A2A2F",
  ink: "#F2F2F3",
  inkMuted: "#9B9BA6",
  inkFaint: "#6E6E78",
  // O rosa sobe de luminância no escuro: #FF2D8E sobre #0E0E10 não alcança o
  // contraste de texto. Mesma matiz, não outra cor.
  brand: "#FF4FA0",
  brandSoft: "#351827",
  brandInk: "#FF4FA0",
  acao: "#FF4FA0",
  onAcao: "#0E0E10",
  positive: "#47CD89",
  warning: "#FDB022",
  danger: "#F97066",
  onDanger: "#0E0E10",
};

/** Grade de 4pt. */
export const espaco = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24, xxxl: 32, huge: 48 } as const;
export const raio = { sm: 8, md: 12, lg: 20 } as const;

// `fontFamily`, não `fontWeight`: peso + família custom cai de volta no
// sistema no Android (RN não faz "negrito sintético" de uma TTF nomeada).
// Cada peso da Inter é o arquivo certo, não uma variação do mesmo.
export const texto = {
  display: { fontSize: 40, lineHeight: 44, fontFamily: "Inter-Bold" },
  titulo: { fontSize: 28, lineHeight: 34, fontFamily: "Inter-Bold" },
  secao: { fontSize: 20, lineHeight: 26, fontFamily: "Inter-SemiBold" },
  corpo: { fontSize: 16, lineHeight: 22, fontFamily: "Inter-Regular" },
  rotulo: { fontSize: 14, lineHeight: 18, fontFamily: "Inter-Medium" },
  legenda: { fontSize: 12, lineHeight: 16, fontFamily: "Inter-Regular" },
} as const;
