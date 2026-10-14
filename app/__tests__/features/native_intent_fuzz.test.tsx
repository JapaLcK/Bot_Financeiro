// Mesmo motivo de `entrar_tela.test.tsx`: sem este dublê, `useColorScheme()` quebra o render.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => "light",
}));

import { renderRouter } from "expo-router/testing-library";

import { prepararCaso } from "./auth_apoio";

const { redirectSystemPath } = jest.requireActual("../../app/+native-intent") as typeof import("../../app/+native-intent");
const { extractExpoPathFromURL } = jest.requireActual("expo-router/build/fork/extractPathFromURL");
const { getRouteInfoFromState } = jest.requireActual("expo-router/build/global-state/getRouteInfoFromState");
const { store } = jest.requireActual("expo-router/build/global-state/store");

/**
 * Oráculo do filtro (#604): gera links por tokens (grafias que o roteador normaliza
 * depois do filtro: `//`, `..`, `%61`, `\`, `@`, `(auth)` repetido, controle, unicode) e
 * exige, para TODO link, que o filtro em PRODUÇÃO (`__DEV__` falso):
 *   1. só deixe passar intacto `""`, `"/"` e o caminho cru `/open-finance-volta…` (que ele remonta);
 *   2. o que sobra (`"/"` ou `/open-finance-volta…` montado pelo filtro) o roteador
 *      resolva para uma tela fora de entrar/criar-conta/esqueci-senha e fora de "+not-found".
 * Semente fixa: reprodutível. Reprodução ampla: `SEED=2 N=100000 npx jest native_intent_fuzz`.
 */
const PRE = ["pigbank://", "pigbank-dev://", "pigbank-staging://", "/", "/", "pigbank:///", "pigbank:/", "pigbank:", "exp+pigbank://", "exp://"];
const TOK = ["pigbank:", "PIGBANK:", "//", "/", "/", "/", "(auth)", "(AUTH)", "(app)", "entrar", "ENTRAR", "criar-conta", "esqueci-senha", "boas-vindas", "auth", "x", "resumo", "open-finance-volta",
  "%61", "%2F", "%5C", "\\", "..", ".", "%2e", "%2e%2e", "?", "#", "\t", "\n", "%09", " ", "%20", "\u0000", "%00", "%", "%ZZ", "%E0%A4%A", "%25", "%2561", "=", "&", "url=", "expo-development-client",
  "exp+pigbank:", "%3F", "%23", "ｅ", "​", ";", ":", "@", "%28auth%29", ",", "+", "itemId=a", "code=c"];
const PROIBIDAS = ["entrar", "criar-conta", "esqueci-senha"];

function mulberry(a: number) {
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Para onde o roteador levaria a string, sem as guardas de sessão (mesmo caminho do `useStore`). */
function destino(url: string): string {
  let p = extractExpoPathFromURL([], url);
  if (!p.startsWith("/")) p = "/" + p;
  const st = store.linking.getStateFromPath(p, store.linking.config);
  return st ? getRouteInfoFromState(st).segments.join("/") : "";
}

it("oráculo x allowlist: nenhum link abre entrada ou +not-found, e só '' e '/' passam intactos", () => {
  prepararCaso();
  renderRouter("./app");
  (globalThis as { __DEV__?: boolean }).__DEV__ = false;
  try {
    const rnd = mulberry(Number(process.env.SEED ?? 1));
    const falhas: string[] = [];
    const vistos = new Set<string>();
    const N = Number(process.env.N ?? 6000);
    for (let i = 0; i < N; i++) {
      let u = PRE[Math.floor(rnd() * PRE.length)] ?? "";
      for (let j = 1 + Math.floor(rnd() * 9); j > 0; j--) u += TOK[Math.floor(rnd() * TOK.length)];
      if (vistos.has(u)) continue;
      vistos.add(u);
      for (const initial of [true, false]) {
        const f = redirectSystemPath({ path: u, initial });
        if (f === null) {
          if (initial) falhas.push(`null no initial: ${JSON.stringify(u)}`);
          continue;
        }
        if (f === u && u !== "" && u !== "/" && !u.startsWith("/open-finance-volta")) {
          falhas.push(`passou intacto: ${JSON.stringify(u)}`);
          continue;
        }
        if (f === u && (u === "" || u === "/")) continue;
        if (f !== "/" && !f.startsWith("/open-finance-volta")) falhas.push(`reescrita inesperada: ${JSON.stringify(u)} -> ${f}`);
        const d = destino(f);
        if (d.split("/").some((s) => PROIBIDAS.includes(s)) || d.includes("+not-found")) falhas.push(`abre ${d}: ${JSON.stringify(u)} -> ${f}`);
      }
    }
    expect(vistos.size).toBeGreaterThan(N * 0.8);
    expect(falhas.slice(0, 10)).toEqual([]);
  } finally {
    (globalThis as { __DEV__?: boolean }).__DEV__ = true;
  }
});
