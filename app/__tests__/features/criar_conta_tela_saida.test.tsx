// Mesmo motivo de `entrar_tela.test.tsx`: sem este dublê, `useColorScheme()`
// chega `undefined` no `_layout.tsx` real e derruba o render.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => "light",
}));

import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";
import { router } from "expo-router";

import { prepararCaso, resposta, segurar } from "./auth_apoio";
import { botao, campo, irAoCodigo, preencher, respirar, rotas, verifies } from "./criar_conta_tela_apoio";

beforeEach(() => {
  prepararCaso();
  rotas();
});
afterEach(() => jest.restoreAllMocks());

/** Sair da rota com o verify em voo (`usePreventRemove` só em "verificando"). */
describe("(auth)/criar-conta — saída da rota durante a verificação", () => {
  // Controle negativo (medido): sem o `usePreventRemove`, o primeiro
  // `router.back()` sai para /entrar e este teste fica vermelho. O positivo é
  // o fim dele: depois do 400, voltar funciona.
  it("T17 — com o verify em voo, voltar não sai da rota; depois do 400, sai", async () => {
    const portao = segurar();
    rotas({
      "/auth/verify-email": async () => {
        await portao.promessa;
        return resposta(400, { detail: "Código inválido ou expirado." });
      },
    });
    await irAoCodigo();

    await act(async () => {
      fireEvent.changeText(campo("Código de 6 dígitos"), "000000");
      await respirar();
    });
    expect(verifies()).toHaveLength(1);

    await act(async () => {
      router.back();
      await respirar();
    });
    expect(screen).toHavePathname("/criar-conta");

    await act(async () => {
      portao.soltar();
      await respirar();
    });
    expect(screen.getByText("Código inválido ou expirado.")).toBeTruthy();

    await act(async () => {
      router.back();
      await respirar();
    });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
  });

  // Duas instâncias da rota: o verify de A em voo, B confirma primeiro
  // (`ultimaTentativa` é global) e a resposta de A vira `EntradaSuperada`.
  // Controle negativo (medido): com `confirmar()` devolvendo `null` na
  // superada, A fica em "verificando" e o último `router.back()` não sai.
  it("T18 — confirmação superada por outra instância da rota: volta ao código e voltar sai", async () => {
    const portao = segurar();
    let primeiro = true;
    rotas({
      "/auth/verify-email": async () => {
        if (!primeiro) return resposta(400, { detail: "Código inválido ou expirado." });
        primeiro = false;
        await portao.promessa;
        return resposta(400, { detail: "Código inválido ou expirado." });
      },
    });
    await irAoCodigo();
    await act(async () => {
      fireEvent.changeText(campo("Código de 6 dígitos"), "000000");
      await respirar();
    });

    await act(async () => {
      router.push("/criar-conta");
      await respirar();
    });
    preencher();
    await act(async () => {
      fireEvent.press(botao("Criar conta"));
      await respirar();
    });
    await act(async () => {
      fireEvent.changeText(campo("Código de 6 dígitos"), "111111");
      await respirar();
    });
    expect(verifies()).toHaveLength(2);

    await act(async () => {
      portao.soltar();
      await respirar();
    });
    await act(async () => {
      router.back();
      await respirar();
    });
    expect(screen).toHavePathname("/criar-conta");
    expect(campo("Código de 6 dígitos").props.editable).toBe(true);

    await act(async () => {
      router.back();
      await respirar();
    });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
  });
});
