import { capturarItemBancario } from "@/storage/secure";
import { itemDoLink, tentativaDoLink, widgetAberto } from "@/features/openFinance/volta";

/**
 * A rota da volta, e só ela. No iOS o expo-router entrega a URL completa:
 * esquema do app (`pigbank://`, `pigbank-staging://`, `pigbank-dev://`) ou
 * Expo Go (`exp://host:porta/--/…`); e, defensivamente, o caminho cru que a
 * suíte de teste usa. Regex e não `URL`: o polyfill do Hermes é incerto aqui.
 * Premissa: o servidor só emite esses esquemas, em minúscula e sem espaço.
 * Esquema fora desta lista, `pigbank:///…` e `PIGBANK://…` caem na allowlist
 * abaixo (descartados): não saem do fluxo real. O destino devolvido é montado
 * aqui (`/open-finance-volta?…`), nunca o texto do link, então `/..` no link
 * não leva a outra tela.
 */
const VOLTA = [
  /^pigbank(?:-staging|-dev)?:\/\/open-finance-volta(?:[/?#]|$)/,
  /^exp:\/\/[^/?#]+\/--\/open-finance-volta(?:[/?#]|$)/,
  /^\/open-finance-volta(?:[/?#]|$)/,
];

/**
 * Só no desenvolvimento: Expo Go (`exp://`), dev client (`exp+pigbank…://`) e a
 * indireção `<scheme>://expo-development-client/?url=…`, que o roteador segue
 * até a URL de dentro. Em produção nada disso é um link legítimo.
 */
const DEV = /^(?:exp|exp\+pigbank(?:-staging|-dev)?):\/\/|^pigbank(?:-staging|-dev)?:\/\/expo-development-client(?:[/?#]|$)/;

/**
 * O expo-router passa TODO link de entrada por aqui; `null` = não navega.
 * ALLOWLIST (#604): o app não emite deep link a não ser a volta do Open Finance
 * (`open-finance-volta`), e o retorno do Google é lido por `openAuthSessionAsync`,
 * não pelo roteador. Então só passam a raiz (`""`/`"/"`), a volta do Open Finance
 * e, em `__DEV__`, os links de desenvolvimento. Todo o resto — `pigbank://entrar`,
 * `pigbank://auth?code=…`, qualquer grafia que o roteador normalize depois (`////`,
 * `%61`, `..`, `@`, `\`) — é `null` com o app aberto e `/` na abertura a frio
 * (`initial`). A volta do OAuth com o app aberto e o widget da Pluggy em foco
 * também é `null` (ver `definirWidgetAberto`).
 */
export function redirectSystemPath({ path, initial }: { path: string; initial: boolean }): string | null {
  if (VOLTA.some((r) => r.test(path))) {
    let consulta: URLSearchParams;
    try {
      const semFragmento = path.split("#")[0] ?? "";
      const separador = semFragmento.indexOf("?");
      const query = separador < 0 ? "" : semFragmento.slice(separador + 1);
      // O construtor string do RN trunca '='. Pares preservam valores e
      // delimitadores codificados; decode acontece uma vez em cada parte.
      const pares = query.split("&").filter(Boolean).map((par): [string, string] => {
        const igual = par.indexOf("=");
        const chave = igual < 0 ? par : par.slice(0, igual);
        const valor = igual < 0 ? "" : par.slice(igual + 1);
        return [decodeURIComponent(chave.replace(/\+/g, " ")), decodeURIComponent(valor.replace(/\+/g, " "))];
      });
      consulta = new URLSearchParams(pares);
    } catch {
      if (!initial && widgetAberto()) return null;
      return "/open-finance-volta?itemId=&modo=";
    }
    const encontrados = consulta.getAll("itemId");
    const valor = encontrados.length === 1 ? encontrados[0] : undefined;
    const modos = consulta.getAll("modo");
    const observacao = consulta.has("modo");
    const modo = modos.length === 1 && modos[0] === "acompanhar" ? "acompanhar" : "";
    const segmento = path.split(/[?#]/)[0]?.match(/\/open-finance-volta\/([^/]+)$/)?.[1];
    // Só o path configurado no token carrega a origem. Query fornecida pelo
    // link não ganha vínculo com a tentativa atualmente no cofre.
    const origem = consulta.has("tentativaId") || consulta.has("attempt_id") ? null : tentativaDoLink(segmento);
    let item: string | null = null;
    try {
      item = itemDoLink(valor);
      if (item && origem && !observacao) void capturarItemBancario(item, origem).catch(() => {});
    } catch { /* Link inválido não impede abrir o app. */ }
    if (!initial && widgetAberto()) return null;
    const parametros = [
      ...(encontrados.length ? [`itemId=${item ? encodeURIComponent(item) : ""}`] : []),
      ...(origem && !observacao ? [`tentativaId=${origem}`] : []),
      ...(observacao ? [`modo=${modo}`] : []),
    ];
    return `/open-finance-volta${parametros.length ? `?${parametros.join("&")}` : ""}`;
  }
  if (path === "" || path === "/" || (__DEV__ && DEV.test(path))) return path;
  return initial ? "/" : null;
}
