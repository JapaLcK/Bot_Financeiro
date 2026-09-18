import { StyleSheet } from "react-native";

import { Money } from "@/ui/componentes/Money";
import { claro, escuro } from "@/ui/tokens";

import { renderNosDoisTemas } from "./_render";

function estiloDoTexto(props: { style?: unknown }) {
  return StyleSheet.flatten(props.style as never) as { color?: string; fontVariant?: string[] };
}

// `children` do `Texto` vira array (`[antesDaVirgula, centavos]`) fora do
// `display` — junta sem separador pra comparar o texto visível como uma coisa só.
function textoVisivel(props: Record<string, unknown>): string {
  return ([] as unknown[]).concat(props.children as never).join("");
}

describe("Money — oculto", () => {
  it('mostra "R$ ••••" sem nenhum dígito de centavos na árvore, em NENHUM lugar', () => {
    const { claro: resultado } = renderNosDoisTemas(<Money centavos={987654321} oculto />);
    const arvore = JSON.stringify(resultado.toJSON());
    expect(arvore).not.toMatch(/987|654|321/);
    expect(resultado.getByLabelText("Valor oculto")).toBeTruthy();
    expect(resultado.getByText("R$ ••••")).toBeTruthy();
  });

  it("controle: não oculto formata o mesmo valor (prova que a ausência acima não é por a formatação estar quebrada)", () => {
    const { claro: resultado } = renderNosDoisTemas(<Money centavos={987654321} />);
    // `getByText` junta os children (o "R$ 9.876.543," e o "21" são dois
    // children do MESMO Texto fora do `display`) — checar via `JSON.stringify`
    // quebraria na vírgula que o array de children introduz entre os dois.
    expect(resultado.getByText(/9\.876\.543,21/)).toBeTruthy();
  });
});

describe("Money — valor inválido", () => {
  it('NaN vira "—" com rótulo próprio', () => {
    const { claro: resultado } = renderNosDoisTemas(<Money centavos={NaN} />);
    expect(resultado.getByText("—")).toBeTruthy();
    expect(resultado.getByLabelText("Valor indisponível")).toBeTruthy();
  });
});

describe("Money — zero", () => {
  it.each(["saldo", "entrada", "saida"] as const)("zero em %s nunca leva sinal", (tipo) => {
    const { claro: resultado } = renderNosDoisTemas(<Money centavos={0} tipo={tipo} />);
    const texto = resultado.getByLabelText(/reais/);
    const visivel = textoVisivel(texto.props);
    expect(visivel).not.toContain("+");
    expect(visivel).not.toContain("−");
  });
});

describe("Money — decisão 2 (tipo manda no sinal, usa o módulo)", () => {
  it("saída sempre em −, mesmo com centavos positivo", () => {
    const { claro: resultado } = renderNosDoisTemas(<Money centavos={1230} tipo="saida" />);
    const texto = resultado.getByText(/R\$/);
    expect(textoVisivel(texto.props)).toContain("−R$ 12,");
  });

  it("saída sempre em −, mesmo com centavos negativo", () => {
    const { claro: resultado } = renderNosDoisTemas(<Money centavos={-1230} tipo="saida" />);
    const texto = resultado.getByText(/R\$/);
    expect(textoVisivel(texto.props)).toContain("−R$ 12,");
  });

  it("entrada sempre com +, mesmo com centavos negativo", () => {
    const { claro: resultado } = renderNosDoisTemas(<Money centavos={-1230} tipo="entrada" />);
    const texto = resultado.getByText(/R\$/);
    expect(textoVisivel(texto.props)).toContain("+R$ 12,");
    expect(estiloDoTexto(texto.props).color).toBe(claro.positive);
  });

  it("saldo negativo mostra − e nunca hífen comum", () => {
    const { claro: resultado } = renderNosDoisTemas(<Money centavos={-1230} tipo="saldo" />);
    const texto = resultado.getByText(/R\$/);
    expect(textoVisivel(texto.props)).toContain("−R$ 12,");
    expect(textoVisivel(texto.props)).not.toContain("-R$");
  });

  it("saldo positivo não leva sinal nenhum", () => {
    const { claro: resultado } = renderNosDoisTemas(<Money centavos={1230} tipo="saldo" />);
    const texto = resultado.getByText(/R\$/);
    expect(textoVisivel(texto.props)).toBe("R$ 12,30");
  });
});

describe("Money — cor por tipo", () => {
  it("saída fica em ink, nunca danger, nos dois temas", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<Money centavos={-500} tipo="saida" />);
    expect(estiloDoTexto(c.getByText(/R\$/).props).color).toBe(claro.ink);
    expect(estiloDoTexto(e.getByText(/R\$/).props).color).toBe(escuro.ink);
  });
});

describe("Money — accessibilityLabel usa a fala, não o texto visual", () => {
  it('saída de R$ 12,30 fala "menos 12 reais e 30 centavos"', () => {
    const { claro: resultado } = renderNosDoisTemas(<Money centavos={1230} tipo="saida" />);
    expect(resultado.getByLabelText("menos 12 reais e 30 centavos")).toBeTruthy();
  });

  // M16: só o caso "menos" tinha teste; "mais" (entrada) ficava sem cobertura.
  it('entrada de R$ 12,30 fala "mais 12 reais e 30 centavos"', () => {
    const { claro: resultado } = renderNosDoisTemas(<Money centavos={1230} tipo="entrada" />);
    expect(resultado.getByLabelText("mais 12 reais e 30 centavos")).toBeTruthy();
  });
});

describe("Money — tabular-nums", () => {
  it("todo Texto de valor tem fontVariant tabular-nums", () => {
    const { claro: resultado } = renderNosDoisTemas(<Money centavos={1230} />);
    expect(estiloDoTexto(resultado.getByText(/R\$/).props).fontVariant).toEqual(["tabular-nums"]);
  });
});

describe("Money — variante display", () => {
  it("display trava em 1 linha e reduz a fonte; corpo não", () => {
    const { claro: display } = renderNosDoisTemas(<Money centavos={1230} variante="display" />);
    const textoDisplay = display.getByText(/R\$/);
    expect(textoDisplay.props.numberOfLines).toBe(1);
    expect(textoDisplay.props.adjustsFontSizeToFit).toBe(true);
    expect(textoDisplay.props.minimumFontScale).toBe(0.6);

    const { claro: corpo } = renderNosDoisTemas(<Money centavos={1230} variante="corpo" />);
    const textoCorpo = corpo.getByText(/R\$/);
    expect(textoCorpo.props.numberOfLines).toBeUndefined();
    expect(textoCorpo.props.adjustsFontSizeToFit).toBeUndefined();
  });

  it("decisão 3: display de saldo (tom ink) esmaece os centavos; display de entrada (tom positive) não", () => {
    const { claro: saldo } = renderNosDoisTemas(<Money centavos={1230} variante="display" tipo="saldo" />);
    const centavosSaldo = saldo.getByText("30");
    expect(estiloDoTexto(centavosSaldo.props).color).toBe(claro.inkMuted);

    const { claro: entrada } = renderNosDoisTemas(<Money centavos={1230} variante="display" tipo="entrada" />);
    const centavosEntrada = entrada.getByText("30");
    expect(estiloDoTexto(centavosEntrada.props).color).toBe(claro.positive);
  });
});

describe("Money — snapshot (dois temas)", () => {
  it("saldo negativo em display", () => {
    const { claro, escuro } = renderNosDoisTemas(<Money centavos={-123456789} variante="display" />);
    expect(claro.toJSON()).toMatchSnapshot("claro");
    expect(escuro.toJSON()).toMatchSnapshot("escuro");
  });
});
