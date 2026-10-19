import { act, fireEvent, render } from "@testing-library/react-native";
import { StyleSheet } from "react-native";

import { conteudoBolha, CurvaAcumulada, indiceDoDia, ladoDaBolha, type CurvaAcumuladaProps } from "@/ui/componentes/CurvaAcumulada";
import { selecao } from "@/ui/haptics";
import { TemaProvider } from "@/ui/tema";

declare const global: typeof globalThis & { __definirReduzirMovimento: (v: boolean) => void };

jest.mock("@/ui/haptics", () => ({ selecao: jest.fn() }));

const H = { includeHiddenElements: true }; // a curva é oculta ao VoiceOver de propósito
const estilo = (el: { props: { style?: unknown } }) => StyleSheet.flatten(el.props.style as never) as Record<string, any>;
// Mês corrente, dia 4 de 31: 4 pontos (R$ 100, 300, 300, 800). A largura medida é 300 -> o dia i fica em x = 12 + i * 276/30.
const base: CurvaAcumuladaProps = { pontos: [10000, 30000, 30000, 80000], anterior: null, diasNoMes: 31, indiceHoje: 3 };
const ANT = Array.from({ length: 31 }, (_, i) => 210000 + i * 1000); // mês passado inteiro, preenchido até o dia 31
const noX = (i: number) => 12 + (i * 276) / 30;
const toque = (x: number, y = 60) => ({ nativeEvent: { locationX: x, locationY: y, pageX: x, pageY: y } });

async function desenhada(props: Partial<CurvaAcumuladaProps> = {}) {
  const r = render(<TemaProvider esquema="dark"><CurvaAcumulada {...base} {...props} /></TemaProvider>);
  await act(async () => {});
  await act(async () => { fireEvent(r.getByTestId("curva-plot", H), "layout", { nativeEvent: { layout: { width: 300, height: 130 } } }); });
  return r;
}
const gesto = (r: Awaited<ReturnType<typeof desenhada>>) => r.getByTestId("curva-scrub", H);
const segurar = (r: Awaited<ReturnType<typeof desenhada>>, x: number) => { fireEvent(gesto(r), "touchStart", toque(x)); act(() => { jest.advanceTimersByTime(250); }); };

beforeEach(() => { jest.useFakeTimers(); (selecao as jest.Mock).mockClear(); });
afterEach(() => { jest.useRealTimers(); global.__definirReduzirMovimento(false); jest.restoreAllMocks(); });

describe("indiceDoDia", () => {
  it("dia mais próximo do x: bordas, meio e arredondamento", () => {
    expect(indiceDoDia(12, 300, 31, 30)).toBe(0);
    expect(indiceDoDia(288, 300, 31, 30)).toBe(30);
    expect(indiceDoDia(150, 300, 31, 30)).toBe(15);
    expect(indiceDoDia(noX(7) + 4.5, 300, 31, 30)).toBe(7); // meio passo = 4,6pt: abaixo disso fica no dia
    expect(indiceDoDia(noX(7) + 4.7, 300, 31, 30)).toBe(8);
  });
  it("clamp: x fora do gráfico (negativo ou além da borda) prende em 0 e no último", () => {
    expect(indiceDoDia(-80, 300, 31, 30)).toBe(0);
    expect(indiceDoDia(900, 300, 31, 30)).toBe(30);
  });
  it("limite de hoje: no mês corrente nunca passa do último ponto com dado, mesmo com o dedo mais à direita", () => {
    expect(indiceDoDia(288, 300, 31, 3)).toBe(3);
    expect(indiceDoDia(noX(2), 300, 31, 3)).toBe(2);
  });
  it("largura ainda não medida ou eixo degenerado = 0, sem NaN", () => {
    expect(indiceDoDia(50, 0, 31, 30)).toBe(0);
    expect(indiceDoDia(50, 300, 1, 0)).toBe(0);
  });
});

describe("ladoDaBolha", () => {
  it("acima só se couber inteira dentro do gráfico (altura da bolha + folga); senão abaixo; 2 linhas pedem mais espaço que 1", () => {
    expect(ladoDaBolha(110, 1)).toBe("acima");
    expect(ladoDaBolha(8, 1)).toBe("abaixo");
    expect(ladoDaBolha(34, 1)).toBe("acima"); // 16 + 8 + 10
    expect(ladoDaBolha(33.9, 1)).toBe("abaixo");
    expect(ladoDaBolha(40, 2)).toBe("abaixo");
    expect(ladoDaBolha(58, 2)).toBe("acima"); // 32 + 8 + 10
  });
});

describe("conteudoBolha", () => {
  it("o valor é EXATAMENTE o ponto da série deste mês, em reais pt-BR, e a linha 2 (mês passado) só existe com a série dele", () => {
    expect(conteudoBolha(3, [10000, 30000, 30000, 123456789], null)).toEqual({ dia: "dia 4", valor: "R$ 1.234.567,89", anterior: null });
    expect(conteudoBolha(1, base.pontos, ANT)).toEqual({ dia: "dia 2", valor: "R$ 300,00", anterior: "mês passado R$ 2.110,00" });
  });
  it("mês passado mais curto que o índice usa o último valor da série", () => {
    expect(conteudoBolha(5, [0, 0, 0, 0, 0, 500], [100, 200])).toMatchObject({ valor: "R$ 5,00", anterior: "mês passado R$ 2,00" });
  });
});

describe("CurvaAcumulada: segurar para ver o dia", () => {
  it("toque curto não abre nada, nem depois do tempo de segurar", async () => {
    const r = await desenhada();
    fireEvent(gesto(r), "touchStart", toque(150));
    act(() => { jest.advanceTimersByTime(100); });
    fireEvent(gesto(r), "touchEnd", toque(150));
    act(() => { jest.advanceTimersByTime(1000); });
    expect(r.queryByTestId("curva-bolha", H)).toBeNull();
    expect(selecao).not.toHaveBeenCalled();
  });
  it("segurar 250ms abre a bolha no dia do dedo (valor da série, 1 linha sem mês passado), com ponto e linha; haptics uma vez", async () => {
    const r = await desenhada();
    segurar(r, noX(1));
    expect(r.getByText("dia 2 · R$ 300,00", H)).toBeTruthy(); // 2 linhas no máximo: dia e valor na mesma
    expect(r.queryByText(/mês passado/, H)).toBeNull();
    expect(r.getByTestId("scrub-linha", H)).toBeTruthy();
    expect(estilo(r.getByTestId("scrub-ponto", H))).toMatchObject({ left: noX(1) - 4 });
    expect(selecao).toHaveBeenCalledTimes(1);
  });
  it("com o mês passado a bolha ganha a 2ª linha, do mesmo dia", async () => {
    const r = await desenhada({ anterior: ANT });
    segurar(r, noX(1));
    expect(r.getByText("mês passado R$ 2.110,00", H)).toBeTruthy();
  });
  it("`rotulos` nomeia a legenda e a 2ª linha da bolha (a comparação de dois períodos quaisquer)", async () => {
    const r = await desenhada({ anterior: ANT, rotulos: { atual: "out", anterior: "set" } });
    expect(r.getAllByTestId("curva-legenda", H)).toHaveLength(2); expect(r.getByText("out", H)).toBeTruthy(); expect(r.queryByText("Mês passado", H)).toBeNull();
    segurar(r, noX(1)); expect(r.getByText("set R$ 2.110,00", H)).toBeTruthy();
  });
  it("arrastar troca o dia (haptics a cada troca, não a cada move) e nunca passa de hoje", async () => {
    const r = await desenhada();
    segurar(r, noX(1));
    fireEvent(gesto(r), "touchMove", toque(noX(1) + 1)); // mesmo dia: sem haptics novo
    expect(selecao).toHaveBeenCalledTimes(1);
    fireEvent(gesto(r), "touchMove", toque(noX(3)));
    expect(r.getByText("dia 4 · R$ 800,00", H)).toBeTruthy();
    fireEvent(gesto(r), "touchMove", toque(290)); // dia 30: não há dado do mês atual depois de hoje (dia 4)
    expect(r.getByText("dia 4 · R$ 800,00", H)).toBeTruthy(); expect(r.queryByText(/dia 30/, H)).toBeNull();
    fireEvent(gesto(r), "touchMove", toque(-40));
    expect(r.getByText("dia 1 · R$ 100,00", H)).toBeTruthy();
    expect(selecao).toHaveBeenCalledTimes(3); // ativar (dia 2), dia 4 e dia 1; o clamp em hoje não repete
  });
  it("mês fechado (sem hoje): o scrub vai até o último dia do mês", async () => {
    const r = await desenhada({ pontos: ANT, indiceHoje: null });
    segurar(r, noX(0));
    fireEvent(gesto(r), "touchMove", toque(290));
    expect(r.getByText(/^dia 31 · /, H)).toBeTruthy();
  });
  it("a bolha fica inteira dentro do gráfico nas duas pontas, com e sem o mês passado", async () => {
    for (const anterior of [null, ANT]) {
      const r = await desenhada({ anterior, pontos: ANT, indiceHoje: null });
      segurar(r, noX(0));
      const w = estilo(r.getByTestId("curva-bolha", H)).width;
      expect(estilo(r.getByTestId("curva-bolha", H)).left).toBe(0);
      fireEvent(gesto(r), "touchMove", toque(290));
      expect(estilo(r.getByTestId("curva-bolha", H)).left).toBe(300 - w);
      r.unmount();
    }
  });
  it("soltar fecha (touchEnd ou touchCancel)", async () => {
    for (const fim of ["touchEnd", "touchCancel"]) {
      const r = await desenhada();
      segurar(r, noX(1));
      expect(r.getByTestId("curva-bolha", H)).toBeTruthy();
      fireEvent(gesto(r), fim, toque(noX(1)));
      expect(r.queryByTestId("curva-bolha", H)).toBeNull();
      r.unmount();
    }
  });
  it("mexer o dedo além da folga antes dos 250ms é rolagem: não abre", async () => {
    const r = await desenhada();
    fireEvent(gesto(r), "touchStart", toque(150, 60));
    fireEvent(gesto(r), "touchMove", toque(150, 90));
    act(() => { jest.advanceTimersByTime(400); });
    expect(r.queryByTestId("curva-bolha", H)).toBeNull();
  });
  it("fechar a tela com o dedo parado: o timer morre junto (nada de haptics nem setState depois do unmount)", async () => {
    const r = await desenhada();
    fireEvent(gesto(r), "touchStart", toque(noX(1)));
    r.unmount();
    act(() => { jest.advanceTimersByTime(400); });
    expect(selecao).not.toHaveBeenCalled();
  });
  it("2º dedo antes de soltar o 1º: só o último toque arma (uma ativação, no x dele) e soltar fecha sem deixar nada preso", async () => {
    const r = await desenhada();
    fireEvent(gesto(r), "touchStart", toque(noX(1)));
    act(() => { jest.advanceTimersByTime(100); });
    fireEvent(gesto(r), "touchStart", toque(noX(3)));
    act(() => { jest.advanceTimersByTime(160); }); // 260ms do 1º toque, só 160 do 2º
    expect(r.queryByTestId("curva-bolha", H)).toBeNull();
    act(() => { jest.advanceTimersByTime(100); });
    expect(r.getByText("dia 4 · R$ 800,00", H)).toBeTruthy();
    expect(selecao).toHaveBeenCalledTimes(1);
    fireEvent(gesto(r), "touchEnd", toque(noX(3)));
    expect(r.queryByTestId("curva-bolha", H)).toBeNull();
    act(() => { jest.advanceTimersByTime(1000); });
    expect(r.queryByTestId("curva-bolha", H)).toBeNull();
  });
  it("os dados encolhem no meio do scrub (índice fora da série): sem erro e sem bolha", async () => {
    const r = await desenhada();
    segurar(r, noX(3));
    expect(r.getByTestId("curva-bolha", H)).toBeTruthy();
    r.rerender(<TemaProvider esquema="dark"><CurvaAcumulada {...base} pontos={[10000, 30000]} indiceHoje={1} /></TemaProvider>);
    expect(r.queryByTestId("curva-bolha", H)).toBeNull();
  });
  it("bolha acima do ponto quando cabe e ABAIXO quando o ponto está perto do topo (não cobre o que está acima do gráfico)", async () => {
    const r = await desenhada();
    segurar(r, noX(0)); // R$ 100 de 1000 no eixo: ponto lá embaixo
    expect(estilo(r.getByTestId("curva-bolha", H))).toHaveProperty("bottom");
    expect(estilo(r.getByTestId("curva-bolha", H))).not.toHaveProperty("top");
    fireEvent(gesto(r), "touchMove", toque(noX(3))); // R$ 800 de 1000: ponto perto do topo
    expect(estilo(r.getByTestId("curva-bolha", H))).toHaveProperty("top");
    expect(estilo(r.getByTestId("curva-bolha", H))).not.toHaveProperty("bottom");
  });
  it("com reduzir movimento a bolha aparece do mesmo jeito, sem animação (é só um View)", async () => {
    global.__definirReduzirMovimento(true);
    const r = await desenhada();
    segurar(r, noX(1));
    expect(estilo(r.getByTestId("curva-bolha", H))).not.toHaveProperty("opacity");
    expect(estilo(r.getByTestId("curva-bolha", H))).not.toHaveProperty("transform");
  });
  it("continua decorativa para o VoiceOver: a bolha mora dentro do container oculto", async () => {
    const r = await desenhada();
    segurar(r, noX(1));
    const raiz = r.getByTestId("curva-acumulada", H);
    expect(raiz.props.accessibilityElementsHidden).toBe(true);
    expect(r.queryByTestId("curva-bolha")).toBeNull(); // sem includeHiddenElements nenhuma query do VoiceOver a enxerga
  });
});
