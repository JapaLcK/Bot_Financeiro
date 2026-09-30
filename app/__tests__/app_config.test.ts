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

  it("produção liga Entrar com a Apple (capability e plugin)", () => {
    const config = configEm("production");
    expect(config.ios?.usesAppleSignIn).toBe(true);
    expect(config.plugins).toContain("expo-apple-authentication");
  });

  it.each(["development", "staging"])("%s não liga Entrar com a Apple", (ambiente) => {
    const config = configEm(ambiente);
    expect(config.ios?.usesAppleSignIn).toBeUndefined();
    expect(config.plugins).not.toContain("expo-apple-authentication");
  });

  it.each(["production", "staging", "development"])("%s: plugin do Face ID com o texto da permissão", (ambiente) => {
    expect(configEm(ambiente).plugins).toContainEqual([
      "expo-local-authentication",
      { faceIDPermission: "O PigBank usa o Face ID para proteger seu app quando você volta para ele." },
    ]);
  });

  it.each(["production", "development"])("%s: time, build e criptografia isenta", (ambiente) => {
    expect(configEm(ambiente).ios).toMatchObject({
      appleTeamId: "S849YDA49P",
      buildNumber: "12",
      config: { usesNonExemptEncryption: false },
    });
  });
});
