import { capturarItemBancario } from "@/storage/secure";
import { itemDoLink, tentativaDoLink, widgetAberto } from "@/features/openFinance/volta";

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
 * As rotas de entrada (`(auth)`) e a `auth` do retorno do Google (#604). Um
 * link `pigbank://` de FORA nunca deve levar até elas: a pessoa abriria a tela
 * de login/cadastro por um link de terceiro (e no Android, `auth` sem rota vira
 * "Unmatched Route"). Só o esquema do app e o caminho cru; `exp://` e o dev
 * client (`exp+pigbank://`) não casam de propósito.
 */
const ENTRADA = new Set(["auth", "entrar", "criar-conta", "esqueci-senha", "boas-vindas"]);

/**
 * O expo-router normaliza o link DEPOIS deste filtro (`new URL`: `//` repetido,
 * `(auth)` repetido, `%61`, `.`/`..`), então testar a string crua deixava
 * `pigbank:////entrar` e `/(auth)//entrar` passarem. Normaliza à mão, sem `URL`
 * (o do Hermes é incerto, como no `VOLTA` acima) e compara o primeiro segmento.
 */
function levaAEntrada(path: string): boolean {
  const resto = /^(?:pigbank(?:-staging|-dev)?:|(?=\/))/i.exec(path);
  if (!resto) return false;
  let caminho = path.slice(resto[0].length).split(/[?#]/, 1)[0] ?? "";
  try {
    caminho = decodeURIComponent(caminho);
  } catch {
    return false; // malformado: o roteador também não o resolve para uma rota
  }
  const segmentos: string[] = [];
  for (const seg of caminho.replace(/[\t\n\r]/g, "").split("/")) {
    if (seg === "..") segmentos.pop();
    else if (seg && seg !== "." && seg.toLowerCase() !== "(auth)") segmentos.push(seg);
  }
  return ENTRADA.has((segmentos[0] ?? "").toLowerCase());
}

/**
 * O expo-router passa TODO link de entrada por aqui; `null` = não navega. Descarta
 * a volta do OAuth que chega com o app aberto e o widget da Pluggy em foco (ver
 * `definirWidgetAberto`) e os links para `ENTRADA`: `null` com o app aberto, `/`
 * na abertura a frio (`initial`). Qualquer outro link segue sem mudança.
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
  if (levaAEntrada(path)) return initial ? "/" : null;
  return path;
}
