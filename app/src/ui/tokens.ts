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
  /** plane1 do PDF: Card/bloco, grupo de ListRow, Banner. */
  surface: string;
  /** plane2 do PDF: Chip, trilho do segmentado, Acao, Button secondary, Input, composer, bolha do usuário. */
  surfaceControl: string;
  /** plane3 do PDF: polegar do segmentado, avatar, Card raised, enviar desligado. */
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
  /** Laranja do PDF: barra "Saiu" do Calendário. */
  alert: string;
  danger: string;
  onDanger: string;
  /** Só `shadowColor` (Card raised no claro) — nunca texto nem contorno, por isso sem par em `PARES`. */
  shadow: string;
}

// Valores do escuro = pág. 25 do PDF de identidade visual; o claro é adaptação.
// Contrastes medidos com `contraste()` em 2026-10-10 — remeça se a paleta mudar.
export const claro: Paleta = {
  bg: "#FFFFFF",
  surface: "#F6F6F7",
  surfaceControl: "#EFEFF1",
  surfaceRaised: "#FFFFFF",
  border: "#E6E6E9",
  ink: "#111113",
  inkMuted: "#5F5F6A",
  // 4,59 sobre surfaceControl (o PDF §13 aceita ink-3 como texto).
  inkFaint: "#6B6B75",
  brand: marca,
  brandSoft: "#FFE6F1",
  brandInk: "#C7186B",
  // Botão primário: rosa com texto #0F172A (5,12:1; branco daria 3,49) nos dois temas.
  acao: marca,
  onAcao: "#0F172A",
  positive: "#067647",
  warning: "#B54708",
  alert: "#E8590C",
  danger: "#B42318",
  onDanger: "#FFFFFF",
  shadow: "#000000",
};

export const escuro: Paleta = {
  bg: "#09090B",
  surface: "#101013",
  surfaceControl: "#16161A",
  surfaceRaised: "#1D1D22",
  border: "#24242A",
  ink: "#F4F4F6",
  inkMuted: "#A8A8B3",
  inkFaint: "#8B8B96",
  brand: marca,
  // pink-wash 10% composto sobre bg, em hex sólido (`contraste()` só lê 6 dígitos).
  brandSoft: "#220D18",
  // TEXTO rosa usa só `brandInk`; `brand` fica para ícone/gráfico (não textual, mínimo 3:1).
  brandInk: "#FF5AA6",
  acao: marca,
  onAcao: "#0F172A",
  positive: "#3DDC97",
  warning: "#F5B544",
  alert: "#FF7A45",
  // O PDF não tem vermelho de erro; segue o bg novo (7,14 sobre danger).
  danger: "#F97066",
  onDanger: "#09090B",
  shadow: "#000000",
};

/**
 * Cor de marca dos bancos conhecidos (chave = nome sem acento, minúsculo). Sem verde (reservado a ganho) e sem
 * rosa (a marca): PicPay e afins ficam fora de propósito e caem na escala de tinta (`composicaoContas.ts`).
 */
export const coresBanco: Record<string, string> = {
  nubank: "#820AD1", itau: "#EC7000", bradesco: "#CC092F", santander: "#EC0000", inter: "#FF7A00",
  "banco do brasil": "#FAE128", caixa: "#0070AF", "mercado pago": "#009EE3", xp: "#F5C400", btg: "#001E62",
};

/** Grade de 4pt. */
export const espaco = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24, xxxl: 32, huge: 48 } as const;
/**
 * Escala de raio do PDF, só com os valores em uso. `pilula`: maior que qualquer
 * altura, o RN corta na metade. `bloco`: Card/Banner/faixa do Piggy;
 * `controle`: Button, Input, trilho do segmentado, grupo de ListRow; `chip`: Chip;
 * `celula`: polegar do segmentado; `barra`: ProgressBar; `cartao`: o resto.
 */
export const raio = { pilula: 999, bloco: 16, cartao: 12, controle: 10, chip: 8, celula: 7, barra: 4 } as const;
/**
 * Fundo de chip de tom (`positive`/`warning`): o tom nesta opacidade sobre o fundo; o texto fica no tom cheio (par medido em
 * `contraste.test.ts`). No claro o texto escuro já está a ~4,6:1 do fundo e qualquer tinta o rebaixa (12% dava 4,24 a 4,44; 8%, 4,49): 6%.
 */
export const ALFA_FUNDO_TOM = { light: 0.06, dark: 0.14 } as const;
/** `cor` (hex de 6 dígitos) com o alfa de `ALFA_FUNDO_TOM`: o fundo tingido do chip e dos avisos da pilha. */
export const tingir = (cor: string, esquema: "light" | "dark") => `${cor}${Math.round(ALFA_FUNDO_TOM[esquema] * 255).toString(16).padStart(2, "0")}`;

// `fontFamily`, não `fontWeight`: peso + família custom cai de volta no
// sistema no Android (RN não faz "negrito sintético" de uma TTF nomeada).
// Cada peso da Inter é o arquivo certo, não uma variação do mesmo.
// Escala do PDF; `letterSpacing` em px (em × tamanho).
export const texto = {
  display: { fontSize: 40, lineHeight: 44, fontFamily: "Inter-SemiBold", letterSpacing: -1.4 },
  titulo: { fontSize: 24, lineHeight: 28, fontFamily: "Inter-SemiBold", letterSpacing: -0.6 },
  secao: { fontSize: 20, lineHeight: 26, fontFamily: "Inter-SemiBold", letterSpacing: -0.4 },
  corpo: { fontSize: 14, lineHeight: 21, fontFamily: "Inter-Regular", letterSpacing: 0 },
  rotulo: { fontSize: 13, lineHeight: 18, fontFamily: "Inter-Medium", letterSpacing: 0 },
  legenda: { fontSize: 12, lineHeight: 16, fontFamily: "Inter-Medium", letterSpacing: 0 },
} as const;

/**
 * Cores de categoria (donut e barras do painel), por POSIÇÃO e iguais nos dois
 * temas: as categorias reais não casam com as chaves do protótipo. DECORATIVAS:
 * o nome e o valor estão escritos na linha e a barra é `accessibilityElementsHidden`,
 * então a cor nunca é o único sinal e fica fora do portão de contraste.
 */
export const CORES_CATEGORIA = ["#3987E5", "#D95926", "#2FA0C8", "#C98500", "#9085E9", "#E66767", "#7C8391", "#5A5F6A"] as const;

/** Paleta das telas de acesso: mantém o fundo da tampa biométrica nativa. */
export const acessoClaro: Paleta = {
  ...claro, surface: "#F0F0F5", surfaceRaised: "#F6F6FA",
  ink: "#191921", inkMuted: "#686875",
};
export const acessoEscuro: Paleta = {
  ...escuro, surface: "#24242F", surfaceRaised: "#24242F",
  ink: "#F8F8FC", inkMuted: "#A0A0AF",
};
