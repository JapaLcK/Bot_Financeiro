// Mesmo motivo de `entrar_tela.test.tsx`: sem este dublê, `useColorScheme()`
// chega `undefined` no `_layout.tsx` real e derruba o render.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => "light",
}));

import { act, renderRouter } from "expo-router/testing-library";

import { lerCredenciais } from "@/storage/secure";

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
