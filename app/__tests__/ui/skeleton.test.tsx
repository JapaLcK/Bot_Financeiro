import { act } from "@testing-library/react-native";
import { Animated } from "react-native";

import { Skeleton } from "@/ui/componentes/Skeleton";
import { claro, escuro } from "@/ui/tokens";

import { renderInterativo, renderNosDoisTemas } from "./_render";

declare const global: typeof globalThis & { __definirReduzirMovimento: (v: boolean) => void };

describe("Skeleton", () => {
  let loop: jest.SpyInstance;

  beforeEach(() => {
    global.__definirReduzirMovimento(false);
    loop = jest.spyOn(Animated, "loop").mockReturnValue({
      start: jest.fn(),
      stop: jest.fn(),
      reset: jest.fn(),
    } as unknown as Animated.CompositeAnimation);
  });

  afterEach(() => loop.mockRestore());

  it("largura, altura e raio do chamador viram o estilo (forma real, não bloco genérico)", () => {
    const { claro: c } = renderNosDoisTemas(<Skeleton largura={120} altura={16} raio={4} />);
    const estilo = c.UNSAFE_getByType(Animated.View).props.style;
    expect(estilo.width).toBe(120);
    expect(estilo.height).toBe(16);
    expect(estilo.borderRadius).toBe(4);
  });

  it("sem reduzir movimento, inicia o pulso (Animated.loop) e não para", async () => {
    renderInterativo(<Skeleton largura={100} altura={16} />);
    await act(async () => {});
    expect(loop).toHaveBeenCalledTimes(1);
    const instancia = loop.mock.results[0]!.value as { stop: jest.Mock };
    expect(instancia.stop).not.toHaveBeenCalled();
  });

  // `useReduzirMovimento` nasce `false` (só sabe o valor real depois de um
  // microtask assíncrono — mesmo comportamento já testado em
  // `motion.test.ts` para `usePressao`), então o loop chega a INICIAR uma
  // vez antes de "reduzir" resolver; o que prova que o pulso desliga é o
  // `stop()` do MESMO loop assim que o valor assíncrono chega, e nenhuma
  // segunda chamada a `Animated.loop` depois disso.
  it("com reduzir movimento, o pulso PARA assim que o valor chega, e não reinicia", async () => {
    global.__definirReduzirMovimento(true);
    renderInterativo(<Skeleton largura={100} altura={16} />);
    await act(async () => {});
    expect(loop).toHaveBeenCalledTimes(1);
    const instancia = loop.mock.results[0]!.value as { stop: jest.Mock };
    expect(instancia.stop).toHaveBeenCalledTimes(1);
  });

  it("fundo é `border` nos dois temas", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<Skeleton largura={100} altura={16} />);
    expect(c.UNSAFE_getByType(Animated.View).props.style.backgroundColor).toBe(claro.border);
    expect(e.UNSAFE_getByType(Animated.View).props.style.backgroundColor).toBe(escuro.border);
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<Skeleton largura={200} altura={24} raio={8} />);
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
