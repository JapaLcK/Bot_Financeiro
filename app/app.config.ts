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

/**
 * URL do backend. O localhost só vale em desenvolvimento.
 *
 * Num aparelho, `localhost` é o PRÓPRIO aparelho: um build de staging ou de
 * produção que caísse nesse padrão sairia da esteira sem conseguir falar com o
 * backend, e o sintoma seria "o app não carrega nada" — sem erro de build, sem
 * aviso, e já instalado em alguém. Os perfis do EAS definem só o `APP_ENV`, e
 * os arquivos `.env` não viajam no build, então o caso é alcançável de verdade.
 *
 * Fora de desenvolvimento a ausência é ERRO, e é na hora de gerar a config —
 * antes de existir binário.
 */
function apiUrl(): string {
  const url = process.env.EXPO_PUBLIC_API_URL;
  if (url) return url;
  if (AMBIENTE !== "development") {
    throw new Error(
      `EXPO_PUBLIC_API_URL é obrigatória em ${AMBIENTE}: sem ela o build sai ` +
        "apontando para o próprio aparelho. Defina no perfil do eas.json ou no " +
        "ambiente do build.",
    );
  }
  return "http://localhost:8000";
}

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
    appleTeamId: "S849YDA49P",
    buildNumber: "2",
    config: { usesNonExemptEncryption: false },
    // Salvar senha e código no app Senhas: só produção, que é o único id em
    // `/.well-known/apple-app-site-association` (frontend/routes/static_pages.py,
    // `_APPLE_APP_IDS`). Dev e staging ficam fora para não misturar credenciais
    // nem exigir a capability nos App IDs deles.
    ...(AMBIENTE === "production" ? { associatedDomains: ["webcredentials:pigbankai.com"] } : {}),
  },
  android: {
    package: `${ID_BASE}${atual.sufixoId}`,
  },
  // O plugin do Sentry não é opcional num build de produção: é ele que liga a
  // integração nativa e sobe os source maps. Sem ele, o empacotamento do Hermes
  // deixa a pilha de erro ilegível, e a camada de observabilidade relata sem
  // dizer ONDE — que é metade do valor dela.
  // `enableSceneSupport`: app compilado com o SDK do iOS 27 (Xcode 27) só abre
  // com o ciclo de vida por scenes do UIKit; o SDK 57 o traz como opt-in
  // (expo/fyi, "Staying on SDK 57 with Xcode 27"). No SDK 58 vira no-op: sai
  // na atualização.
  plugins: [
    "expo-router",
    "expo-secure-store",
    "@sentry/react-native/expo",
    ["expo-build-properties", { ios: { enableSceneSupport: true } }],
  ],
  experiments: { typedRoutes: true },
  extra: {
    ambiente: AMBIENTE,
    apiUrl: apiUrl(),
  },
};

export default config;
