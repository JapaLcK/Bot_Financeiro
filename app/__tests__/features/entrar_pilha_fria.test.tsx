// Mesmo motivo de `entrar_tela.test.tsx`: sem este dublê, `useColorScheme()` quebra o render.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => "light",
}));
// Um link que chega ao roteador SEM passar pelo filtro de entrada (#604): dev client,
// Expo Go ou qualquer forma que `+native-intent.ts` não reconheça. O filtro real tem os
// casos dele em `google_deep_link.test.tsx`; aqui, o que a PILHA faz quando o link chega.
jest.mock("../../app/+native-intent", () => ({ redirectSystemPath: ({ path }: { path: string }) => path }));

import { router } from "expo-router";
import { renderRouter, screen, waitFor } from "expo-router/testing-library";

import { prepararCaso } from "./auth_apoio";

beforeEach(prepararCaso);

/**
 * Controle negativo (medido): remover `unstable_settings` de `(auth)/_layout.tsx`
 * deixa este vermelho (`canGoBack()` falso, sheet sem base). Antes do #604 o caso
 * era o link frio `pigbank://esqueci-senha`, que o filtro agora descarta.
 */
it("M1 — chegada fria em /esqueci-senha tem boas-vindas embaixo na pilha (canGoBack)", async () => {
  renderRouter("./app", { initialUrl: "/esqueci-senha" });
  await waitFor(() => expect(screen).toHavePathname("/esqueci-senha"));

  expect(router.canGoBack()).toBe(true);
});
