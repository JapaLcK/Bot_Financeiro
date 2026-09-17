import * as Haptics from "expo-haptics";

import { aviso, selecao, sucesso } from "@/ui/haptics";

/**
 * `disparar()` engole a rejeição de propósito (comentário em `haptics.ts`):
 * simulador e web recusam a chamada nativa, e sem o `.catch` isso vira um
 * `unhandledRejection` — no app real, um crash report por toque de Chip.
 */
describe("haptics", () => {
  afterEach(() => {
    jest.clearAllMocks();
  });

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
