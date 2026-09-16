import * as Sentry from "@sentry/react-native";
import Constants from "expo-constants";

/**
 * Crash reporting. Sem DSN, não inicializa — e o app roda igual.
 *
 * O site hoje tem ZERO observabilidade de cliente: 11 mil linhas de JS e
 * nenhum erro reportado, então um `TypeError` num handler só aparece como
 * "o botão não faz nada". É o primeiro problema a não repetir.
 */
export function iniciarLogging(): void {
  const dsn = process.env.EXPO_PUBLIC_SENTRY_DSN;
  if (!dsn) return;
  Sentry.init({
    dsn,
    environment: String(Constants.expoConfig?.extra?.ambiente ?? "development"),
    release: Constants.expoConfig?.version,
    // Breadcrumb de rede guarda a URL; a query pode carregar token (o site já
    // vazou URL com token para o GA4 uma vez). Sem corpo, sem query.
    sendDefaultPii: false,
    beforeBreadcrumb(b) {
      if (b.category === "fetch" || b.category === "xhr") {
        const url = b.data?.url;
        if (typeof url === "string") b.data = { ...b.data, url: url.split("?")[0] };
      }
      return b;
    },
  });
}
