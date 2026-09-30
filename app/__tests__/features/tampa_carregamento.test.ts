/**
 * O `tampa.ts` real (o `jest.setup.js` o dubla para o resto da suíte): onde ele
 * carrega o módulo nativo e onde vira no-op.
 *
 * Arquivo à parte de propósito: só importar `expo-router/testing-library` já faz
 * o `jest.doMock` abaixo ser ignorado (medido), então este teste não pode morar
 * junto dos que usam `renderRouter`.
 */
describe("tampa.ts — onde o nativo é carregado", () => {
  function carregar(os: string, ambiente: string) {
    const requireNativeModule = jest.fn(() => ({ descobrir: jest.fn(), pular: jest.fn() }));
    jest.isolateModules(() => {
      jest.doMock("react-native", () => ({ Platform: { OS: os } }));
      jest.doMock("expo", () => ({ requireNativeModule }));
      jest.doMock("expo-constants", () => ({
        __esModule: true,
        default: { executionEnvironment: ambiente },
        ExecutionEnvironment: { Bare: "bare", Standalone: "standalone", StoreClient: "storeClient" },
      }));
      jest.requireActual("@/features/bloqueio/tampa");
    });
    // O `doMock` vale para o arquivo todo, não só para o registro isolado: sem
    // desfazer, o `react-native` de mentira chega à limpeza do jest-expo.
    jest.dontMock("react-native");
    jest.dontMock("expo");
    jest.dontMock("expo-constants");
    return requireNativeModule;
  }

  it.each([
    ["Android", "android", "bare"],
    ["Expo Go", "ios", "storeClient"],
  ])("%s: não carrega o nativo", (_nome, os, ambiente) => {
    expect(carregar(os, ambiente)).not.toHaveBeenCalled();
  });

  it.each(["bare", "standalone"])("iOS (%s, nosso binário): carrega PigBankTampa", (ambiente) => {
    expect(carregar("ios", ambiente)).toHaveBeenCalledWith("PigBankTampa");
  });
});
