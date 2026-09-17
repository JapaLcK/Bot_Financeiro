import { act, renderHook } from "@testing-library/react-native";
import { AccessibilityInfo, Animated } from "react-native";

import { duracoes, facilitador, usePressao } from "@/ui/motion";

declare const global: typeof globalThis & {
  __definirReduzirMovimento: (v: boolean) => void;
  __dispararReduzirMovimento: (v: boolean) => void;
};

describe("usePressao", () => {
  const iniciar = jest.fn();
  let timing: jest.SpyInstance;

  beforeEach(() => {
    global.__definirReduzirMovimento(false);
    iniciar.mockClear();
    // Espiona `Animated.timing` em vez de ler o valor interno do
    // `Animated.Value` (privado, sem leitura pública e assíncrono via RAF —
    // ler o argumento passado é o que o hook realmente decide, sem depender
    // de quantos frames o driver JS levou para chegar lá).
    timing = jest
      .spyOn(Animated, "timing")
      .mockReturnValue({ start: iniciar } as unknown as Animated.CompositeAnimation);
  });

  afterEach(() => {
    timing.mockRestore();
  });

  it("sem reduzir movimento, o estilo usa transform (escala), nunca opacity", async () => {
    const { result } = renderHook(() => usePressao());
    await act(async () => {}); // efeito que lê isReduceMotionEnabled()

    expect(result.current.estilo).toHaveProperty("transform");
    expect(result.current.estilo).not.toHaveProperty("opacity");
  });

  it("ao pressionar sem reduzir movimento, anima a escala para 0.97", async () => {
    const { result } = renderHook(() => usePressao());
    await act(async () => {});

    act(() => result.current.aoPressionar());
    expect(timing).toHaveBeenCalledWith(
      expect.any(Animated.Value),
      expect.objectContaining({
        toValue: 0.97,
        duration: duracoes.feedback,
        easing: facilitador,
        useNativeDriver: true,
      }),
    );
    expect(iniciar).toHaveBeenCalledTimes(1);
  });

  it("ao soltar, a escala volta para 1", async () => {
    const { result } = renderHook(() => usePressao());
    await act(async () => {});

    act(() => result.current.aoSoltar());
    expect(timing).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ toValue: 1 }));
  });

  it("com reduzir movimento, o estilo usa opacity, e o toque anima 0.85 — nunca a escala", async () => {
    global.__definirReduzirMovimento(true);
    const { result } = renderHook(() => usePressao());
    await act(async () => {});

    expect(result.current.estilo).toHaveProperty("opacity");
    expect(result.current.estilo).not.toHaveProperty("transform");

    act(() => result.current.aoPressionar());
    expect(timing).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ toValue: 0.85 }));
  });

  it("reagir ao evento reduceMotionChanged troca o comportamento em voo", async () => {
    const { result } = renderHook(() => usePressao());
    await act(async () => {});
    expect(result.current.estilo).toHaveProperty("transform");

    act(() => global.__dispararReduzirMovimento(true));
    expect(result.current.estilo).toHaveProperty("opacity");
  });

  it("remove o listener de acessibilidade no unmount (sem isso, cada Botão pressionável monta uma assinatura que nunca morre)", async () => {
    const remover = jest.fn();
    const addSpy = jest
      .spyOn(AccessibilityInfo, "addEventListener")
      .mockReturnValue({ remove: remover } as unknown as ReturnType<typeof AccessibilityInfo.addEventListener>);

    const { unmount } = renderHook(() => usePressao());
    await act(async () => {});
    unmount();

    expect(remover).toHaveBeenCalledTimes(1);
    addSpy.mockRestore();
  });
});
