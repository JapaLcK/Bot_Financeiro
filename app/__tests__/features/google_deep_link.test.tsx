// Mesmo motivo de `entrar_tela.test.tsx`: sem este dublê, `useColorScheme()`
// chega `undefined` no `_layout.tsx` real e derruba o render.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => "light",
}));

import { act, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { lerCredenciais } from "@/storage/secure";

import { redirectSystemPath } from "../../app/+native-intent";
import { chamadas, prepararCaso } from "./auth_apoio";
import { abrirFolha, rotasGoogle } from "./google_apoio";

const respirar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

beforeEach(() => {
  prepararCaso();
  rotasGoogle();
  abrirFolha.mockReset();
});

/**
 * Linha 13: `pigbank://auth?code=…` vindo DE FORA (um link num e-mail, noutro
 * app) não faz nada. O código só é lido do retorno do `openAuthSessionAsync`;
 * não existe rota `auth`. Sem isto, qualquer um logaria o aparelho de outra
 * pessoa na conta dele mandando um link (CSRF de login).
 */
describe("deep link pigbank://auth de fora", () => {
  // Controle negativo (medido): uma rota `(auth)/auth.tsx` que troca o `code`
  // do link deixa o caso do `code` vermelho, com a troca chamada.
  it.each(["/auth?code=code-ana", "/auth?onboarding=gso_bia"])("%s: nenhuma requisição, nenhuma sessão", async (url) => {
    renderRouter("./app", { initialUrl: url });
    await act(respirar);

    expect(chamadas()).toEqual([]);
    expect(abrirFolha).not.toHaveBeenCalled();
    await expect(lerCredenciais()).resolves.toBeNull();
  });
});

/**
 * #604: `+native-intent.ts` descarta os links de ENTRADA para as rotas `(auth)`
 * e para `auth`. A abertura a frio cai na raiz (`/`, que é `boas-vindas`) em vez
 * de empilhar login/cadastro por um link de terceiro, e nenhuma troca sai.
 * Controle negativo (medido): sem o filtro em `redirectSystemPath`, os casos de
 * `/entrar`, `/criar-conta` e `/esqueci-senha` ficam vermelhos (a tela abre).
 */
describe("link frio para as rotas de entrada", () => {
  it.each(["/entrar", "/criar-conta", "/esqueci-senha", "/auth?code=code-ana"])(
    "%s: cai na raiz, nada empilha, nenhuma requisição",
    async (url) => {
      renderRouter("./app", { initialUrl: url });
      await act(respirar);

      await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
      expect(chamadas()).toEqual([]);
      expect(abrirFolha).not.toHaveBeenCalled();
      await expect(lerCredenciais()).resolves.toBeNull();
    },
  );
});

describe("redirectSystemPath: links de entrada", () => {
  const ALVOS = [
    "pigbank://auth?code=code-ana",
    "pigbank://auth?onboarding=gso_bia",
    "pigbank://entrar",
    "pigbank://criar-conta?x=1",
    "pigbank://esqueci-senha",
    "pigbank://boas-vindas#f",
    "pigbank-dev://entrar",
    "pigbank-staging://auth?code=c",
    "pigbank:///entrar",
    "pigbank://(auth)/entrar",
    "PIGBANK://ENTRAR",
    "/entrar",
    "/auth?code=c",
    "/(auth)/criar-conta",
  ];

  it.each(ALVOS)("%s: null com o app aberto, '/' na abertura a frio", (path) => {
    expect(redirectSystemPath({ path, initial: false })).toBeNull();
    expect(redirectSystemPath({ path, initial: true })).toBe("/");
  });

  // Positivo: o filtro não pode engolir o resto (dev client, Expo Go, rotas de dentro, outro esquema).
  it.each([
    "pigbank://seguranca",
    "pigbank://piggy?x=1",
    "pigbank://entrarx",
    "pigbank://authx?code=c",
    "pigbank://autenticar",
    "pigbank://x/entrar",
    "pigbankai://auth?code=c",
    "exp://192.168.0.5:8081/--/entrar",
    "exp+pigbank://expo-development-client/?url=http%3A%2F%2F192.168.0.5%3A8081",
    "/seguranca",
    "/",
  ])("%s: segue sem mudança", (path) => {
    expect(redirectSystemPath({ path, initial: false })).toBe(path);
    expect(redirectSystemPath({ path, initial: true })).toBe(path);
  });
});
