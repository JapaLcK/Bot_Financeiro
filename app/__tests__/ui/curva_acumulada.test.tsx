import { act, fireEvent, render, within } from "@testing-library/react-native";
import { Animated, processColor, StyleSheet } from "react-native";

import { LinearGradient, Stop } from "react-native-svg";

import { CurvaAcumulada, rotuloCompacto, ticksY, type CurvaAcumuladaProps } from "@/ui/componentes/CurvaAcumulada";
import { TemaProvider } from "@/ui/tema";
import { claro, escuro } from "@/ui/tokens";

import { renderNosDoisTemas } from "./_render";

declare const global: typeof globalThis & { __definirReduzirMovimento: (v: boolean) => void };

// A curva é oculta ao VoiceOver de propósito: a query precisa enxergar elementos ocultos.
const H = { includeHiddenElements: true };
const base: CurvaAcumuladaProps = { pontos: [10000, 30000, 30000, 80000], anterior: null, diasNoMes: 31, indiceHoje: 3 };
const ANT = Array.from({ length: 30 }, (_, i) => 20000 + i * 5000); // mês passado inteiro, mais alto que o atual
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
const medir = (r: ReturnType<typeof render>) => act(async () => { fireEvent(r.getByTestId("curva-plot", H), "layout", { nativeEvent: { layout: { width: 300, height: 130 } } }); });
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
  it.each([["ink"], ["positive"], ["warning"]] as const)("linha de 2pt na cor do tom %s, nos dois temas, e o degradê (15% no topo, 0% embaixo) é da mesma cor, por referência ao gradiente", async (tom) => {
    for (const [esquema, paleta] of [["dark", escuro], ["light", claro]] as const) {
      const r = await desenhada({ tom }, esquema);
      expect(r.getByTestId("curva-linha", H).props).toMatchObject({ stroke: pincel(paleta[tom]), strokeWidth: 2 });
      const [g] = r.UNSAFE_getAllByType(LinearGradient), stops = r.UNSAFE_getAllByType(Stop);
      expect(g!.props).toMatchObject({ x1: "0", y1: "0", x2: "0", y2: "1" });
      expect(stops.map((st) => [st.props.offset, st.props.stopColor, st.props.stopOpacity])).toEqual([["0", paleta[tom], 0.15], ["1", paleta[tom], 0]]);
      expect(r.getByTestId("curva-area", H).props.fill).toMatchObject({ type: 1, brushRef: g!.props.id });
    }
  });
  it("tom padrão é ink", async () => {
    expect((await desenhada()).getByTestId("curva-linha", H).props.stroke).toEqual(pincel(escuro.ink));
  });
  it("dois gráficos montados juntos usam ids de gradiente distintos, e cada área aponta para o seu", async () => {
    const r = render(<TemaProvider esquema="dark"><CurvaAcumulada {...base} /><CurvaAcumulada {...base} /></TemaProvider>);
    await act(async () => {});
    for (const el of r.getAllByTestId("curva-plot", H)) await act(async () => { fireEvent(el, "layout", { nativeEvent: { layout: { width: 300, height: 130 } } }); });
    const ids = r.UNSAFE_getAllByType(LinearGradient).map((g) => g.props.id as string);
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(2);
    expect(r.getAllByTestId("curva-area", H).map((a) => a.props.fill.brushRef)).toEqual(ids);
  });
  it("mês passado: linha tracejada cinza (inkFaint), sem degradê próprio; sem a série, nada dela (nem legenda)", async () => {
    const r = await desenhada({ anterior: ANT });
    expect(r.getByTestId("curva-anterior", H).props).toMatchObject({ stroke: pincel(escuro.inkFaint), strokeDasharray: ["4", "4"] });
    expect(r.UNSAFE_getAllByType(LinearGradient)).toHaveLength(1);
    expect(r.getAllByTestId("curva-legenda", H).map((l) => within(l).getByText(/mês/i, H).props.children)).toEqual(["Este mês", "Mês passado"]);
    const sem = await desenhada();
    expect(sem.queryByTestId("curva-anterior", H)).toBeNull();
    expect(sem.getAllByTestId("curva-legenda", H).map((l) => within(l).getByText(/mês/i, H).props.children)).toEqual(["Este mês"]);
  });
  it("escala Y COMUM: a série mais alta cabe no eixo (a do mês passado aqui), e as duas usam o mesmo y para o mesmo valor", async () => {
    const r = await desenhada({ pontos: [100, 200, 300, 400], anterior: ANT, indiceHoje: 3 }); // ANT chega a 165000; o atual só a 400
    const topo = ticksY(165000).at(-1)!;
    expect(topo).toBeGreaterThanOrEqual(165000);
    expect(r.getAllByTestId("curva-rotulo-y", H).map((t) => t.props.children)).toEqual(ticksY(165000).map(rotuloCompacto));
    const ys = (d: string) => [...d.matchAll(/[ML][\d.]+,(-?[\d.]+)/g)].map((m) => Number(m[1]));
    const yAnt = ys(r.getByTestId("curva-anterior", H).props.d as string), yAtual = ys(r.getByTestId("curva-linha", H).props.d as string);
    expect(Math.min(...yAnt)).toBeGreaterThanOrEqual(0); // nenhum ponto sai pelo topo do SVG
    expect(Math.max(...yAtual)).toBeLessThan(130); // 400 de 165000 fica colado embaixo, não esticado para o próprio máximo
    expect(yAtual.every((y) => y > yAnt.at(-1)!)).toBe(true);
  });
  it("mês passado MAIS LONGO que o corrente (28 × 31 dias): o tracejado é cortado no último dia do eixo e os dias excedentes não esticam a escala Y", async () => {
    const longo = [...Array.from({ length: 28 }, (_, i) => 20000 + i * 1000), 900000, 900000, 900000]; // dias 29 a 31 muito acima de tudo
    const r = await desenhada({ pontos: [10000, 20000, 30000, 40000], anterior: longo, diasNoMes: 28, indiceHoje: 3 });
    const d = r.getByTestId("curva-anterior", H).props.d as string, xs = [...d.matchAll(/[ML]([\d.]+),/g)].map((m) => Number(m[1]));
    expect(xs).toHaveLength(28); // sem o corte seriam 31
    expect(Math.max(...xs)).toBeCloseTo(300 - 12, 1); // o último ponto cai na margem direita, nenhum passa dela
    expect(r.getAllByTestId("curva-rotulo-y", H).map((t) => t.props.children)).toEqual(ticksY(47000).map(rotuloCompacto)); // 47.000 = máximo dos 28 primeiros; com os excedentes seria R$ 9k+
  });
  it("eixo Y: um rótulo e uma linha de grade por degrau, de R$ 0 ao topo; eixo X: dias 1, 15, 25 e o último", async () => {
    const r = await desenhada({ pontos: [100000, 400000, 900000, 1150000], diasNoMes: 31 });
    expect(r.getAllByTestId("curva-rotulo-y", H).map((t) => t.props.children)).toEqual(["R$ 0", "R$ 3k", "R$ 6k", "R$ 9k", "R$ 12k"]);
    expect(r.getAllByTestId("curva-grade", H)).toHaveLength(5);
    expect(r.getAllByTestId("curva-rotulo-x", H).map((t) => t.props.children)).toEqual([1, 15, 25, 31]);
    expect((await desenhada({ diasNoMes: 28 })).getAllByTestId("curva-rotulo-x", H).map((t) => t.props.children)).toEqual([1, 15, 25, 28]);
  });
  it("os rótulos do eixo X ficam proporcionais: dia 1 à esquerda e o último à direita, ordenados", async () => {
    const lefts = (await desenhada()).getAllByTestId("curva-rotulo-x", H).map((t) => StyleSheet.flatten(t.props.style).left as number);
    expect(lefts).toEqual([...lefts].sort((a, b) => a - b));
    expect(lefts[0]).toBeLessThan(lefts[1]!);
    expect(lefts[0]! + 12).toBeCloseTo(12, 5); // centro do dia 1 = margem
    expect(lefts.at(-1)! + 12).toBeCloseTo(300 - 12, 5); // centro do último = largura - margem
  });
  it("legenda: amostra sólida na cor do tom (Este mês) e tracejada cinza (Mês passado)", async () => {
    const r = await desenhada({ anterior: ANT, tom: "positive" });
    expect(StyleSheet.flatten(r.getByTestId("amostra-solida", H).props.style).backgroundColor).toBe(escuro.positive);
    const tr = r.getAllByTestId("amostra-tracejada", H);
    expect(tr).toHaveLength(3);
    expect(StyleSheet.flatten(tr[0]!.props.style).backgroundColor).toBe(escuro.inkFaint);
  });
  it("ponto de hoje só quando há indiceHoje (mês corrente), na cor do tom", async () => {
    const r = await desenhada({ tom: "warning" });
    expect(r.getByTestId("ponto-hoje", H)).toBeTruthy();
    expect(StyleSheet.flatten(r.getByTestId("ponto-hoje-centro", H).props.style)).toMatchObject({ backgroundColor: escuro.warning });
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

describe("rotuloCompacto", () => {
  it.each([
    [720000, "R$ 7,2k"], [700000, "R$ 7k"], [1200000, "R$ 12k"], [95000, "R$ 950"], [0, "R$ 0"], [99950, "R$ 1k"], [723456, "R$ 7,2k"], [100000, "R$ 1k"], [1234500, "R$ 12,3k"], [-1500000, "R$ -15k"], [-95000, "R$ -950"], [-723456, "R$ -7,2k"], [99900, "R$ 999"], [15050, "R$ 151"],
  ])("rotuloCompacto(%i) = %s (vírgula pt-BR, uma casa, sem ruído de arredondamento)", (centavos, esperado) => {
    expect(rotuloCompacto(centavos)).toBe(esperado);
  });
});

describe("ticksY e rotuloCompacto", () => {
  it.each([
    [30000, ["R$ 0", "R$ 100", "R$ 200", "R$ 300"]], // centenas (R$ 300)
    [75000, ["R$ 0", "R$ 200", "R$ 400", "R$ 600", "R$ 800"]],
    [140000, ["R$ 0", "R$ 500", "R$ 1k", "R$ 1,5k"]], // R$ 1.400
    [423539, ["R$ 0", "R$ 2k", "R$ 4k", "R$ 6k"]], // R$ 4.235,39 (milhares)
    [1150000, ["R$ 0", "R$ 3k", "R$ 6k", "R$ 9k", "R$ 12k"]], // dezenas de milhares
    [2500000, ["R$ 0", "R$ 10k", "R$ 20k", "R$ 30k"]],
  ])("máximo %i centavos: degraus redondos que cobrem o máximo", (maximo, rotulos) => {
    const t = ticksY(maximo);
    expect(t.map(rotuloCompacto)).toEqual(rotulos);
    expect(t.at(-1)!).toBeGreaterThanOrEqual(maximo);
    expect(t.length - 1).toBeLessThanOrEqual(4);
  });
  it("máximo 0 (nada gasto ainda) ainda dá um eixo com degrau, sem dividir por zero", () => {
    expect(ticksY(0)).toEqual([0, 100]); // R$ 1
  });
});
