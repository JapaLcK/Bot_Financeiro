import { act } from "@testing-library/react-native";
import { Animated, StyleSheet, View } from "react-native";

import { ProgressBar, ProgressBarIndeterminada } from "@/ui/componentes/ProgressBar";
import { claro, escuro } from "@/ui/tokens";

import { renderInterativo, renderNosDoisTemas } from "./_render";

declare const global: typeof globalThis & { __definirReduzirMovimento: (v: boolean) => void };

function escalaX(resultado: ReturnType<typeof renderNosDoisTemas>["claro"]) {
  const preenchimento = resultado.UNSAFE_getAllByType(View)[1]!;
  const transform = preenchimento.props.style.transform as { scaleX: number }[];
  return transform[0]!.scaleX;
}

describe("ProgressBar", () => {
  it("0.5 mantém 0.5", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={0.5} />);
    expect(escalaX(c)).toBe(0.5);
  });

  it("clampa 1.5 para 1", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={1.5} />);
    expect(escalaX(c)).toBe(1);
  });

  it("clampa -1 para 0", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={-1} />);
    expect(escalaX(c)).toBe(0);
  });

  it("NaN vira 0", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={NaN} />);
    expect(escalaX(c)).toBe(0);
  });

  it("Infinity vira 1 (barra cheia, nunca vazia — a guarda antiga zerava aqui)", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={Infinity} />);
    expect(escalaX(c)).toBe(1);
  });

  it("-Infinity vira 0", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={-Infinity} />);
    expect(escalaX(c)).toBe(0);
  });

  it("accessibilityValue expõe now em 0..100 arredondado (o tipo da RN quer inteiro), não 0..1", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={1.5} />);
    expect(c.UNSAFE_getAllByType(View)[0]!.props.accessibilityValue).toEqual({ min: 0, max: 100, now: 100 });
  });

  it("valor fracionário mínimo (0.005) não deixa now decimal", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={0.005} />);
    expect(c.UNSAFE_getAllByType(View)[0]!.props.accessibilityValue).toEqual({ min: 0, max: 100, now: 1 });
  });

  it("trilha é `border`, não `surface`: dentro do Card (`surface`) a trilha sumia", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<ProgressBar valor={0.5} />);
    expect(c.UNSAFE_getAllByType(View)[0]!.props.style.backgroundColor).toBe(claro.border);
    expect(e.UNSAFE_getAllByType(View)[0]!.props.style.backgroundColor).toBe(escuro.border);
  });

  it("tom customizado (positive/warning/danger) pinta o preenchimento", () => {
    const { claro: c } = renderNosDoisTemas(<ProgressBar valor={0.5} tom="warning" />);
    expect(c.UNSAFE_getAllByType(View)[1]!.props.style.backgroundColor).toBe(claro.warning);
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<ProgressBar valor={0.7} tom="positive" />);
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});

describe("ProgressBarIndeterminada", () => {
  let loop: jest.SpyInstance;
  const preenchimento = (r: ReturnType<typeof renderInterativo>) => StyleSheet.flatten(r.UNSAFE_getByType(Animated.View).props.style);

  beforeEach(() => {
    global.__definirReduzirMovimento(false);
    loop = jest.spyOn(Animated, "loop").mockReturnValue({ start: jest.fn(), stop: jest.fn(), reset: jest.fn() } as unknown as Animated.CompositeAnimation);
  });
  afterEach(() => loop.mockRestore());

  it("progressbar com rótulo e busy, SEM accessibilityValue.now (não há porcentagem)", () => {
    const r = renderInterativo(<ProgressBarIndeterminada rotulo="Organizando seus dados" />);
    const barra = r.getByRole("progressbar", { name: "Organizando seus dados" });
    expect(barra.props.accessibilityState).toEqual({ busy: true });
    expect(barra.props.accessibilityValue?.now).toBeUndefined();
  });

  it("o loop inicia no mount e PARA no desmonte; o segmento anda por translateX", async () => {
    const r = renderInterativo(<ProgressBarIndeterminada rotulo="x" />);
    await act(async () => {});
    const instancia = loop.mock.results[0]!.value as { start: jest.Mock; stop: jest.Mock };
    expect(instancia.start).toHaveBeenCalledTimes(1);
    expect(instancia.stop).not.toHaveBeenCalled();
    expect(preenchimento(r).transform).toBeDefined();
    r.unmount();
    expect(instancia.stop).toHaveBeenCalledTimes(1);
  });

  it("com reduzir movimento nada se desloca (sem translateX): a trilha pulsa por opacidade", async () => {
    global.__definirReduzirMovimento(true);
    const r = renderInterativo(<ProgressBarIndeterminada rotulo="x" />);
    await act(async () => {});
    const estilo = preenchimento(r);
    expect(estilo.transform).toBeUndefined();
    expect(estilo.opacity).toBeDefined();
    expect(estilo.flex).toBe(1);
  });
});
