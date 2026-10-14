// Mesmo motivo de `entrar_tela.test.tsx`: sem este dublê, `useColorScheme()` quebra o render.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => "light",
}));

import { act, renderRouter, screen } from "expo-router/testing-library";

import { lerCredenciais } from "@/storage/secure";

import { chamadas, prepararCaso } from "./auth_apoio";
import { abrirFolha, rotasGoogle } from "./google_apoio";

/**
 * O filtro (#604) com o ROTEADOR de verdade e o harness FIEL: a abertura a frio
 * entrega o link INTEIRO (`expo-linking.getLinkingURL`, como o app real; o
 * `initialUrl` do `renderRouter` tiraria o esquema e o `new URL` normalizaria
 * antes do filtro), e com o app aberto o evento `url` do `expo-linking`.
 * Controle negativo (medido): com o filtro por denylist do commit anterior,
 * B1–B6 abrem /entrar, /criar-conta ou "Unmatched Route"; com a allowlist, nada.
 */
const respirar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};
// eslint-disable-next-line @typescript-eslint/no-require-imports
const Linking = require("expo-linking") as { getLinkingURL: () => string | null; addEventListener: unknown };
const { getLinkingURL, addEventListener } = Linking;

beforeEach(() => {
  prepararCaso();
  rotasGoogle();
  abrirFolha.mockReset();
});
afterEach(() => {
  Linking.getLinkingURL = getLinkingURL;
  Linking.addEventListener = addEventListener;
});

const URLS = [
  "pigbank://entrar", "pigbank://criar-conta", "pigbank://esqueci-senha", "pigbank://auth?code=code-ana", "pigbank://auth?onboarding=gso_bia",
  "pigbank:////entrar", "pigbank:///(auth)//entrar", "pigbank://%61uth?code=code-ana",
  // B1
  "pigbank://entrar/..", "pigbank://entrar/x/../..", "pigbank-dev://criar-conta/..", "pigbank://auth/..?code=code-ana",
  // B2
  "pigbank://x@entrar", "pigbank://u:p@criar-conta", "pigbank://entrar:",
  // B3, B4, B6
  "pigbank:///%ZZ/../entrar", "pigbank://x/..\\entrar", "pigbank:\\\\entrar", "pigbank://entrar ", "pigbank://entrar%00",
  // B5: indireção do dev client (em produção; em __DEV__ ela passa de propósito)
  "pigbank://expo-development-client/?url=pigbank%3A%2F%2Fentrar",
];

/** Em produção: `__DEV__` falso só dentro do caso. */
async function emProducao<T>(f: () => Promise<T>): Promise<T> {
  (globalThis as { __DEV__?: boolean }).__DEV__ = false;
  try {
    return await f();
  } finally {
    (globalThis as { __DEV__?: boolean }).__DEV__ = true;
  }
}

describe("abertura a frio", () => {
  it.each(URLS)("%j: cai na raiz (boas-vindas), nada empilha, nenhuma requisição", (url) =>
    emProducao(async () => {
      Linking.getLinkingURL = () => url;
      const r = renderRouter("./app", { initialUrl: null as unknown as string });
      await act(respirar);

      expect(r.getPathname()).toBe("/boas-vindas");
      expect(screen.queryByText(/Unmatched Route/i)).toBeNull();
      expect(chamadas()).toEqual([]);
      expect(abrirFolha).not.toHaveBeenCalled();
      await expect(lerCredenciais()).resolves.toBeNull();
    }),
  );

  it("controle: a raiz abre boas-vindas", async () => {
    Linking.getLinkingURL = () => "pigbank:///";
    const r = renderRouter("./app", { initialUrl: null as unknown as string });
    await act(respirar);
    expect(r.getPathname()).toBe("/boas-vindas");
  });
});

describe("app aberto", () => {
  it.each(URLS)("%j: a tela não muda", (url) =>
    emProducao(async () => {
      let ouvinte: ((e: { url: string }) => unknown) | undefined;
      Linking.addEventListener = (_: string, f: typeof ouvinte) => {
        ouvinte = f;
        return { remove() {} };
      };
      const r = renderRouter("./app");
      await act(respirar);
      const antes = JSON.stringify(r.getSegments());

      await act(async () => {
        await ouvinte?.({ url });
        await respirar();
      });

      expect(ouvinte).toBeDefined();
      expect(JSON.stringify(r.getSegments())).toBe(antes);
      expect(screen.queryByText(/Unmatched Route/i)).toBeNull();
      expect(chamadas()).toEqual([]);
    }),
  );
});
