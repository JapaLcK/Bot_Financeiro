import type { ExpoConfig } from "expo/config";

/**
 * Um binário por ambiente, com id e nome próprios — dá para ter dev, staging e
 * produção instalados no MESMO aparelho sem um sobrescrever o outro. Sem isso,
 * testar staging significa desinstalar a produção, e alguém acaba testando no
 * binário errado.
 */
const AMBIENTE = process.env.APP_ENV ?? "development";

const POR_AMBIENTE: Record<string, { sufixoId: string; nome: string }> = {
  development: { sufixoId: ".dev", nome: "PigBank Dev" },
  staging: { sufixoId: ".staging", nome: "PigBank Staging" },
  production: { sufixoId: "", nome: "PigBank" },
};

const atual = POR_AMBIENTE[AMBIENTE] ?? POR_AMBIENTE.development!;

// O `mobile/` (Capacitor) ainda usa `com.pigbankai.app` e segue instalado nos
// aparelhos até a Fase 12. O app novo nasce com id PRÓPRIO para os dois
// poderem conviver; a troca de id é decisão de lançamento, não de fundação.
const ID_BASE = "com.pigbankai.mobile";

const config: ExpoConfig = {
  name: atual.nome,
  slug: "pigbank-mobile",
  scheme: "pigbank",
  version: "0.1.0",
  orientation: "portrait",
  userInterfaceStyle: "automatic",
  ios: {
    bundleIdentifier: `${ID_BASE}${atual.sufixoId}`,
    supportsTablet: false,
  },
  android: {
    package: `${ID_BASE}${atual.sufixoId}`,
  },
  plugins: ["expo-router", "expo-secure-store"],
  experiments: { typedRoutes: true },
  extra: {
    ambiente: AMBIENTE,
    apiUrl: process.env.EXPO_PUBLIC_API_URL ?? "http://localhost:8000",
  },
};

export default config;
