import { renderHook } from "@testing-library/react-native";
import * as Haptics from "expo-haptics";

import { aviso, selecao, sucesso, useAvisoAoErrar } from "@/ui/haptics";

/**
 * `disparar()` engole a rejeição de propósito (comentário em `haptics.ts`):
 * simulador e web recusam a chamada nativa, e sem o `.catch` isso vira um
 * `unhandledRejection` — no app real, um crash report por toque de Chip.
 */
// No nível do arquivo, não dentro de um `describe`: um `afterEach` preso a
// só um `describe` (o "haptics" original) não alcança os `describe` irmãos
// que vieram depois (`useAvisoAoErrar`) — medido: sem isto, uma chamada de
// `notificationAsync` vazava de um teste para o próximo e mascarava a
// contagem seguinte (nunca zero quando devia ser).
afterEach(() => {
  jest.clearAllMocks();
});

describe("haptics", () => {
  it("uma rejeição do motor nativo não escapa como unhandledRejection", async () => {
    (Haptics.selectionAsync as jest.Mock).mockRejectedValueOnce(new Error("sem suporte no simulador"));
    (Haptics.notificationAsync as jest.Mock).mockRejectedValueOnce(new Error("sem suporte no simulador"));

    const naoTratada = jest.fn();
    process.on("unhandledRejection", naoTratada);

    selecao();
    aviso();
    // Esvazia a fila de microtasks: é quando uma promessa rejeitada sem
    // `.catch` vira `unhandledRejection`.
    await new Promise<void>((resolver) => setImmediate(resolver));

    process.off("unhandledRejection", naoTratada);
    expect(naoTratada).not.toHaveBeenCalled();
  });

  it("selecao/sucesso/aviso chamam o motor de haptics certo", () => {
    selecao();
    expect(Haptics.selectionAsync).toHaveBeenCalledTimes(1);

    sucesso();
    expect(Haptics.notificationAsync).toHaveBeenCalledWith(Haptics.NotificationFeedbackType.Success);

    aviso();
    expect(Haptics.notificationAsync).toHaveBeenCalledWith(Haptics.NotificationFeedbackType.Warning);
  });
});

describe("useAvisoAoErrar", () => {
  it("não dispara na montagem, mesmo já nascendo com erro", () => {
    renderHook(() => useAvisoAoErrar(true));
    expect(Haptics.notificationAsync).not.toHaveBeenCalled();
  });

  it("dispara 1 vez na transição falso→verdadeiro; trocar o texto do erro (A→B) não dispara de novo", () => {
    const { rerender } = renderHook((props: { comErro: boolean }) => useAvisoAoErrar(props.comErro), {
      initialProps: { comErro: false },
    });
    expect(Haptics.notificationAsync).not.toHaveBeenCalled();

    rerender({ comErro: true }); // falso → verdadeiro: dispara
    expect(Haptics.notificationAsync).toHaveBeenCalledTimes(1);

    rerender({ comErro: true }); // mesmo valor de novo (é o "A"→"B" do texto do erro, já reduzido a bool): não dispara
    expect(Haptics.notificationAsync).toHaveBeenCalledTimes(1);
  });

  it("não dispara a cada render sem mudança de erro", () => {
    const { rerender } = renderHook(() => useAvisoAoErrar(false));
    rerender(undefined);
    rerender(undefined);
    expect(Haptics.notificationAsync).not.toHaveBeenCalled();
  });

  it("undefined→x dispara 1 vez; x→undefined→y dispara mais 1 (total 2)", () => {
    const { rerender } = renderHook((props: { comErro: boolean }) => useAvisoAoErrar(props.comErro), {
      initialProps: { comErro: false },
    });
    rerender({ comErro: true }); // undefined/"x" reduzido a bool: false → true
    expect(Haptics.notificationAsync).toHaveBeenCalledTimes(1);

    rerender({ comErro: false }); // "x" → undefined
    expect(Haptics.notificationAsync).toHaveBeenCalledTimes(1);

    rerender({ comErro: true }); // undefined → "y"
    expect(Haptics.notificationAsync).toHaveBeenCalledTimes(2);
  });
});
