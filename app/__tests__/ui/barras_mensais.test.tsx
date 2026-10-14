import { act } from "@testing-library/react-native";
import { Animated, StyleSheet } from "react-native";

import { alturaBarra, BarrasMensais, type BarraMensal } from "@/ui/componentes/BarrasMensais";
import { contraste } from "@/ui/contraste";
import { claro, escuro } from "@/ui/tokens";

import { renderNosDoisTemas } from "./_render";

declare const global: typeof globalThis & { __definirReduzirMovimento: (v: boolean) => void };

// O desenho é oculto ao VoiceOver de propósito: a query precisa enxergar elementos ocultos.
const H = { includeHiddenElements: true };
const estilo = (el: { props: { style?: unknown } }) => StyleSheet.flatten(el.props.style as never) as Record<string, any>;
const rotulo = (v: number) => `R$ ${v / 100}`;
// Rendas do fixture real (/analytics/1/evolution, 2026-05..2026-10), em centavos; outubro é o mês em andamento.
const RENDAS = [620000, 690000, 640000, 700000, 680000, 720000], NOMES = ["mai", "jun", "jul", "ago", "set", "out"];
const meses = (atual = 5, rendas = RENDAS): BarraMensal[] => rendas.map((valor, i) => ({ chave: `2026-${String(i + 5).padStart(2, "0")}`, rotulo: NOMES[i]!, valor, atual: i === atual }));
const escala = (el: { props: { style?: unknown } }) => estilo(el).transform.find((t: any) => "scaleY" in t).scaleY;

beforeEach(() => { jest.useFakeTimers(); });
afterEach(() => { jest.useRealTimers(); global.__definirReduzirMovimento(false); jest.restoreAllMocks(); });

describe("alturaBarra", () => {
  it("proporcional ao MÁXIMO, mínimo de 4pt para valor > 0, zero e negativo = sem barra", () => {
    expect(alturaBarra(720000, 720000, 120)).toBe(120);
    expect(alturaBarra(360000, 720000, 120)).toBe(60);
    expect(alturaBarra(1, 720000, 120)).toBe(4);
    expect(alturaBarra(0, 720000, 120)).toBe(0);
    expect(alturaBarra(-5, 720000, 120)).toBe(0);
    expect(alturaBarra(5, 0, 120)).toBe(0);
  });
});

describe("BarrasMensais", () => {
  it("6 barras na ordem recebida (mais antigo à esquerda), alturas proporcionais ao máximo (não ao total)", () => {
    const { escuro: r } = renderNosDoisTemas(<BarrasMensais meses={meses()} rotuloValor={rotulo} />);
    expect(r.getAllByTestId("barra", H).map((b) => estilo(b).height)).toEqual([103, 115, 107, 117, 113, 120]);
    expect(r.getAllByTestId("barra-mes", H).map((t) => t.props.children)).toEqual(["mai", "jun", "jul", "ago", "set", "out*"]);
  });
  it("mês atual em rosa da marca cheio e os outros a 50%, na cor do tema; trilho neutro de 120pt, raio 4", () => {
    for (const [esquema, paleta] of [["dark", escuro], ["light", claro]] as const) {
      const r = renderNosDoisTemas(<BarrasMensais meses={meses()} rotuloValor={rotulo} />)[esquema === "dark" ? "escuro" : "claro"];
      expect(r.getAllByTestId("barra", H).map((b) => estilo(b).opacity)).toEqual([0.5, 0.5, 0.5, 0.5, 0.5, 1]);
      for (const b of r.getAllByTestId("barra", H)) expect(estilo(b)).toMatchObject({ backgroundColor: paleta.brand, borderTopLeftRadius: 4, borderTopRightRadius: 4 });
      for (const t of r.getAllByTestId("barra-trilho", H)) expect(estilo(t)).toMatchObject({ width: 26, height: 120, borderRadius: 4, backgroundColor: paleta.surfaceControl });
    }
  });
  it("rótulo do mês atual em brandInk (texto), os outros em inkMuted, o valor segue em ink; a barra cheia dá 3:1 sobre o trilho nos dois temas", () => {
    for (const [esquema, paleta] of [["dark", escuro], ["light", claro]] as const) {
      const r = renderNosDoisTemas(<BarrasMensais meses={meses()} rotuloValor={rotulo} />)[esquema === "dark" ? "escuro" : "claro"];
      expect(r.getAllByTestId("barra-mes", H).map((t) => estilo(t).color)).toEqual([...Array(5).fill(paleta.inkMuted), paleta.brandInk]);
      expect(estilo(r.getByTestId("barra-valor", H)).color).toBe(paleta.ink);
      expect(contraste(paleta.brand, paleta.surfaceControl)).toBeGreaterThanOrEqual(3);
    }
  });
  it("valor compacto SÓ no mês atual, com a marcação 'em andamento' uma vez", () => {
    const { escuro: r } = renderNosDoisTemas(<BarrasMensais meses={meses()} rotuloValor={rotulo} />);
    expect(r.getAllByTestId("barra-valor", H).map((t) => t.props.children)).toEqual(["R$ 7200"]);
    expect(r.getAllByText("* mês em andamento", H)).toHaveLength(1);
  });
  it("controle: sem mês atual na lista, nenhum valor, nenhum '*' e nenhuma legenda", () => {
    const { escuro: r } = renderNosDoisTemas(<BarrasMensais meses={meses(-1)} rotuloValor={rotulo} />);
    expect(r.queryByTestId("barra-valor", H)).toBeNull();
    expect(r.getAllByTestId("barra-mes", H).map((t) => t.props.children)).toEqual(NOMES);
    expect(r.queryByText("* mês em andamento", H)).toBeNull();
    expect(r.getAllByTestId("barra", H).map((b) => estilo(b).opacity)).toEqual([0.5, 0.5, 0.5, 0.5, 0.5, 0.5]);
  });
  it("renda 0 = só o trilho (sem barra), e os outros meses seguem na escala do máximo", () => {
    const { escuro: r } = renderNosDoisTemas(<BarrasMensais meses={meses(5, [0, 690000, 0, 700000, 680000, 720000])} rotuloValor={rotulo} />);
    expect(r.getAllByTestId("barra-trilho", H)).toHaveLength(6);
    expect(r.getAllByTestId("barra", H).map((b) => estilo(b).height)).toEqual([115, 117, 113, 120]);
  });
  it("mínimo de 4pt: renda ínfima ainda aparece", () => {
    const { escuro: r } = renderNosDoisTemas(<BarrasMensais meses={meses(5, [1, 690000, 640000, 700000, 680000, 720000])} rotuloValor={rotulo} />);
    expect(estilo(r.getAllByTestId("barra", H)[0]!).height).toBe(4);
  });
  it("sem meses não renderiza nada", () => {
    const { escuro: r } = renderNosDoisTemas(<BarrasMensais meses={[]} rotuloValor={rotulo} />);
    expect(r.queryByTestId("barras-mensais", H)).toBeNull();
  });
  it("o desenho é oculto ao VoiceOver, mas a legenda do mês em andamento é lida (valor parcial)", () => {
    const { escuro: r } = renderNosDoisTemas(<BarrasMensais meses={meses()} rotuloValor={rotulo} />);
    const oculto = (el: { props: any }) => el.props.accessibilityElementsHidden === true || el.props.importantForAccessibility === "no-hide-descendants";
    const desenho = r.getByTestId("barras-mensais-desenho", H);
    expect(desenho.props.accessibilityElementsHidden).toBe(true);
    expect(desenho.props.importantForAccessibility).toBe("no-hide-descendants");
    const legenda = r.getByText("* mês em andamento", H);
    expect(legenda.props.accessibilityLabel).toBe("Mês atual em andamento: o valor ainda é parcial");
    for (let el: any = legenda; el; el = el.parent) expect(oculto(el)).toBe(false);
  });
  it("sem mês atual não há legenda para o VoiceOver", () => {
    const { escuro: r } = renderNosDoisTemas(<BarrasMensais meses={meses(-1)} rotuloValor={rotulo} />);
    expect(r.queryByLabelText("Mês atual em andamento: o valor ainda é parcial", H)).toBeNull();
  });
  it("cresce de baixo para cima em 450ms (escala 0 -> 1, driver nativo); com reduzir movimento termina parado em 1", async () => {
    const timing = jest.spyOn(Animated, "timing"), setValue = jest.spyOn(Animated.Value.prototype, "setValue");
    const { escuro: r } = renderNosDoisTemas(<BarrasMensais meses={meses()} rotuloValor={rotulo} />);
    await act(async () => {});
    expect(escala(r.getAllByTestId("barra", H)[0]!)).toBe(0);
    // O driver nativo não roda no Jest (o valor fica em 0): a prova do 1 no fim é a configuração da animação.
    expect(timing.mock.calls.at(-1)![1]).toMatchObject({ toValue: 1, duration: 450, useNativeDriver: true });
    r.unmount();
    timing.mockClear(); setValue.mockClear();
    global.__definirReduzirMovimento(true);
    renderNosDoisTemas(<BarrasMensais meses={meses()} rotuloValor={rotulo} />);
    await act(async () => {});
    expect(setValue.mock.calls.at(-1)).toEqual([1]); // a preferência chega async: o 1º quadro pode ter começado a animar, mas termina parado em 1
  });
});
