import { capturarItemBancario } from "@/storage/secure";
import { itemDoLink, widgetAberto } from "@/features/openFinance/volta";

/**
 * A rota da volta, e só ela. No iOS o expo-router entrega a URL completa:
 * esquema do app (`pigbank://`, `pigbank-staging://`, `pigbank-dev://`) ou
 * Expo Go (`exp://host:porta/--/…`); e, defensivamente, o caminho cru que a
 * suíte de teste usa. Regex e não `URL`: o polyfill do Hermes é incerto aqui.
 * Premissa: o servidor só emite esses esquemas, em minúscula e sem espaço;
 * `pigbank:///…` ou `PIGBANK://…` não são descartados (não saem do fluxo real).
 */
const VOLTA = [
  /^[a-z][a-z0-9+.-]*:\/\/open-finance-volta(?:[/?#]|$)/,
  /^[a-z][a-z0-9+.-]*:\/\/[^/?#]+\/--\/open-finance-volta(?:[/?#]|$)/,
  /^\/open-finance-volta(?:[/?#]|$)/,
];

/**
 * O expo-router passa TODO link de entrada por aqui; `null` = não navega. Só
 * descarta a volta do OAuth que chega com o app aberto e o widget da Pluggy em
 * foco (ver `definirWidgetAberto`); qualquer outro link segue sem mudança.
 */
export function redirectSystemPath({ path, initial }: { path: string; initial: boolean }): string | null {
  if (VOLTA.some((r) => r.test(path))) {
    const encontrados = [...path.matchAll(/[?&]itemId=([^&#]*)/g)];
    const valor = encontrados.length === 1 ? encontrados[0]?.[1] : undefined;
    try {
      const item = itemDoLink(valor ? decodeURIComponent(valor) : undefined);
      if (item) void capturarItemBancario(item).catch(() => {});
    } catch { /* Link inválido não impede abrir o app. */ }
  }
  return !initial && widgetAberto() && VOLTA.some((r) => r.test(path)) ? null : path;
}
