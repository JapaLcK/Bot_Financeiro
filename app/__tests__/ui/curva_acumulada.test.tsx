import { act, fireEvent, render } from "@testing-library/react-native";
import { Animated, processColor, StyleSheet } from "react-native";

import { LinearGradient, Stop } from "react-native-svg";

import { CurvaAcumulada, type CurvaAcumuladaProps } from "@/ui/componentes/CurvaAcumulada";
import { TemaProvider } from "@/ui/tema";
import { claro, escuro } from "@/ui/tokens";

import { renderNosDoisTemas } from "./_render";

declare const global: typeof globalThis & { __definirReduzirMovimento: (v: boolean) => void };

// A curva é oculta ao VoiceOver de propósito: a query precisa enxergar elementos ocultos.
const H = { includeHiddenElements: true };
const base: CurvaAcumuladaProps = { pontos: [100, 300, 300, 800], diasNoMes: 31, indiceHoje: 3, referencia: 1000 };
// react-native-svg entrega a cor já processada (`extractBrush`: { type: 0, payload: <cor nativa> }) e o tracejado em lista.
const pincel = (cor: string) => ({ type: 0, payload: processColor(cor) });
// Conta os loops que continuam rodando (iniciados e ainda não parados): o primeiro render nasce com `reduzir=false` até o sistema responder.
function contarLoops() {
  const original = Animated.loop, ativos = { n: 0 };
  jest.spyOn(Animated, "loop").mockImplementation((a, c) => {
    const l = original(a, c), { start, stop } = l;
    l.start = (...x) => { ativos.n++; start(...x); };
    l.stop = () => { ativos.n--; stop(); };
    return l;
  });
  return ativos;
}
// Sem `onLayout` o componente não sabe a largura e não desenha nada: o teste "mede" o container.
const medir = (r: ReturnType<typeof render>) => act(async () => { fireEvent(r.getByTestId("curva-acumulada", H), "layout", { nativeEvent: { layout: { width: 300, height: 120 } } }); });
async function desenhada(props: Partial<CurvaAcumuladaProps> = {}, esquema: "light" | "dark" = "dark", avancar = true) {
  const r = render(<TemaProvider esquema={esquema}>{<CurvaAcumulada {...base} {...props} />}</TemaProvider>);
  await act(async () => {}); // efeito que lê isReduceMotionEnabled()
  await medir(r);
  if (avancar) await act(async () => { jest.advanceTimersByTime(600); }); // deixa o desenho de 500ms terminar dentro do act
  return r;
}

beforeEach(() => { jest.useFakeTimers(); });
afterEach(() => { jest.useRealTimers(); global.__definirReduzirMovimento(false); jest.restoreAllMocks(); });

describe("CurvaAcumulada", () => {
  it("é decorativa para o VoiceOver", () => {
    const { escuro: r } = renderNosDoisTemas(<CurvaAcumulada {...base} />);
    const el = r.getByTestId("curva-acumulada", H);
    expect(el.props.accessibilityElementsHidden).toBe(true);
    expect(el.props.importantForAccessibility).toBe("no-hide-descendants");
  });
  it("linha de 2pt na cor de tinta do tema, nos dois temas (nenhuma cor fixa)", async () => {
    for (const [esquema, paleta] of [["dark", escuro], ["light", claro]] as const) {
      const r = await desenhada({}, esquema);
      expect(r.getByTestId("curva-linha", H).props).toMatchObject({ stroke: pincel(paleta.ink), strokeWidth: 2 });
    }
  });
  it("área em degradê vertical de tinta (8% no topo, 0% embaixo), preenchimento por referência ao gradiente e não chapado", async () => {
    for (const [esquema, paleta] of [["dark", escuro], ["light", claro]] as const) {
      const r = await desenhada({}, esquema);
      const [g] = r.UNSAFE_getAllByType(LinearGradient), stops = r.UNSAFE_getAllByType(Stop);
      expect(g!.props).toMatchObject({ x1: "0", y1: "0", x2: "0", y2: "1" });
      expect(stops.map((st) => [st.props.offset, st.props.stopColor, st.props.stopOpacity])).toEqual([["0", paleta.ink, 0.08], ["1", paleta.ink, 0]]);
      expect(r.getByTestId("curva-area", H).props.fill).toMatchObject({ type: 1, brushRef: g!.props.id });
    }
  });
  it("dois gráficos montados juntos usam ids de gradiente distintos, e cada área aponta para o seu", async () => {
    const r = render(<TemaProvider esquema="dark"><CurvaAcumulada {...base} /><CurvaAcumulada {...base} /></TemaProvider>);
    await act(async () => {});
    for (const el of r.getAllByTestId("curva-acumulada", H)) await act(async () => { fireEvent(el, "layout", { nativeEvent: { layout: { width: 300, height: 120 } } }); });
    const ids = r.UNSAFE_getAllByType(LinearGradient).map((g) => g.props.id as string);
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(2);
    expect(r.getAllByTestId("curva-area", H).map((a) => a.props.fill.brushRef)).toEqual(ids);
  });
  it("referência: tracejada em inkFaint quando há; nada quando é null", async () => {
    expect((await desenhada()).getByTestId("curva-referencia", H).props).toMatchObject({ stroke: pincel(escuro.inkFaint), strokeDasharray: ["4", "4"] });
    expect((await desenhada({ referencia: null })).queryByTestId("curva-referencia", H)).toBeNull();
  });
  it("eixo Y comum: a referência acima do fim da curva fica mais alta (y menor) que o último ponto", async () => {
    const ref = (await desenhada({ referencia: 1000 })).getByTestId("curva-referencia", H).props.y1 as number;
    const refPerto = (await desenhada({ referencia: 800 })).getByTestId("curva-referencia", H).props.y1 as number;
    expect(ref).toBeLessThan(refPerto);
  });
  it("a escala do eixo Y inclui a referência: começo de mês (máx. 100) com mês anterior em 4110 mantém o tracejado dentro do SVG e acima da curva", async () => {
    const r = await desenhada({ pontos: [10, 50, 100], indiceHoje: 2, referencia: 4110 });
    const yRef = r.getByTestId("curva-referencia", H).props.y1 as number;
    const yFim = Number(/L[\d.]+,(-?[\d.]+)$/.exec(r.getByTestId("curva-linha", H).props.d as string)![1]); // último ponto da curva
    expect(yRef).toBeGreaterThanOrEqual(0);
    expect(yRef).toBeLessThanOrEqual(120);
    expect(yFim).toBeGreaterThan(yRef);
  });
  it("ponto rosa só quando há indiceHoje (mês corrente)", async () => {
    const r = await desenhada();
    expect(r.getByTestId("ponto-hoje", H)).toBeTruthy();
    expect(StyleSheet.flatten(r.getByTestId("ponto-hoje-centro", H).props.style)).toMatchObject({ backgroundColor: escuro.brand });
    expect((await desenhada({ indiceHoje: null })).queryByTestId("ponto-hoje", H)).toBeNull();
  });
  it("sem reduzir movimento: o anel pulsa em loop, nativo (transform/opacity)", async () => {
    const loops = contarLoops(), timing = jest.spyOn(Animated, "timing");
    const r = await desenhada();
    expect(r.getByTestId("anel-pulso", H)).toBeTruthy();
    expect(loops.n).toBe(1);
    expect(timing).toHaveBeenCalledWith(expect.any(Animated.Value), expect.objectContaining({ duration: 2400, toValue: 1, useNativeDriver: true }));
    expect(timing).toHaveBeenCalledWith(expect.any(Animated.Value), expect.objectContaining({ duration: 500, toValue: 1, useNativeDriver: false })); // o desenho da linha
  });
  it("sem reduzir movimento, a linha nasce por desenhar (deslocamento = comprimento) e termina em 0", async () => {
    const r = await desenhada({}, "dark", false);
    expect(r.getByTestId("curva-linha", H).props.strokeDashoffset).toBeGreaterThan(0);
    await act(async () => { jest.advanceTimersByTime(600); });
    expect(r.getByTestId("curva-linha", H).props.strokeDashoffset ?? 0).toBe(0);
  });
  it("com reduzir movimento: curva pronta no 1º quadro (deslocamento 0), ponto parado, sem anel e sem loop", async () => {
    global.__definirReduzirMovimento(true);
    const loops = contarLoops();
    const r = await desenhada({}, "dark", false); // sem avançar o relógio: já nasce pronta
    expect(r.getByTestId("curva-linha", H).props.strokeDashoffset ?? 0).toBe(0);
    expect(r.getByTestId("ponto-hoje", H)).toBeTruthy();
    expect(r.queryByTestId("anel-pulso", H)).toBeNull();
    expect(loops.n).toBe(0);
  });
  it("desmontar no meio dos 500ms de desenho para a animação", async () => {
    const paradas: number[] = [], original = Animated.timing;
    jest.spyOn(Animated, "timing").mockImplementation((v, c) => {
      const t = original(v, c), { stop } = t;
      t.stop = () => { if (c.duration === 500) paradas.push(1); stop(); };
      return t;
    });
    const r = await desenhada({}, "dark", false);
    await act(async () => { jest.advanceTimersByTime(200); });
    expect(paradas).toHaveLength(0);
    r.unmount();
    expect(paradas).toHaveLength(1);
  });
  it("menos de 2 dias no mês: não desenha (evita dividir por zero no eixo X)", () => {
    expect(render(<TemaProvider esquema="dark"><CurvaAcumulada {...base} diasNoMes={1} /></TemaProvider>).queryByTestId("curva-acumulada", H)).toBeNull();
  });
  it("controle: pontos vazios não renderizam nada", () => {
    expect(render(<TemaProvider esquema="dark"><CurvaAcumulada {...base} pontos={[]} /></TemaProvider>).queryByTestId("curva-acumulada", H)).toBeNull();
  });
});
