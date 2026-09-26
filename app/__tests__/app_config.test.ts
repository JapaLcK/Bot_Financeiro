import type { ExpoConfig } from "expo/config";

// O `app.config.ts` lê o ambiente ao ser importado: cada caso importa de novo.
function configEm(ambiente: string): ExpoConfig {
  const antes = { ...process.env };
  process.env.APP_ENV = ambiente;
  process.env.EXPO_PUBLIC_API_URL = "https://pigbankai.com";
  let config!: ExpoConfig;
  jest.isolateModules(() => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- import síncrono: o isolateModules não espera promise.
    config = (require("../app.config") as { default: ExpoConfig }).default;
  });
  process.env = antes;
  return config;
}

describe("app.config — iOS", () => {
  it("produção associa o domínio para o app Senhas", () => {
    expect(configEm("production").ios?.associatedDomains).toEqual(["webcredentials:pigbankai.com"]);
  });

  it.each(["development", "staging"])("%s não associa o domínio", (ambiente) => {
    expect(configEm(ambiente).ios?.associatedDomains).toBeUndefined();
  });

  it.each(["production", "development"])("%s: time, build e criptografia isenta", (ambiente) => {
    expect(configEm(ambiente).ios).toMatchObject({
      appleTeamId: "S849YDA49P",
      buildNumber: "6",
      config: { usesNonExemptEncryption: false },
    });
  });
});
