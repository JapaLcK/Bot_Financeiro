/**
 * `EsqueciSenha`: chama a rota certa, e a mensagem de sucesso é SEMPRE a
 * mesma fixa (nunca o `message` que o servidor devolveu — neutralidade de
 * conta, CLAUDE.md). Falha de rede/limite mostra erro de verdade.
 */
import { act, fireEvent } from "@testing-library/react-native";

import { EsqueciSenha } from "@/features/auth/EsqueciSenha";

import { renderInterativo } from "../ui/_render";
import { chamadas, prepararCaso, resposta, rotear } from "./auth_apoio";

const respirar = () => new Promise((r) => setTimeout(r, 0));

beforeEach(prepararCaso);

describe("EsqueciSenha", () => {
  it("200: chama /auth/forgot-password com o e-mail aparado e mostra a mensagem NEUTRA fixa", async () => {
    rotear({ "/auth/forgot-password": () => resposta(200, { message: "algo bem diferente do texto fixo" }) });
    const { getByLabelText, getByText, queryByText } = renderInterativo(<EsqueciSenha />);

    fireEvent.changeText(getByLabelText("E-mail"), " ana@x.com ");
    await act(async () => {
      fireEvent.press(getByText("Enviar"));
      await respirar();
    });

    expect(chamadas()).toEqual([
      expect.objectContaining({ caminho: "/auth/forgot-password", corpo: { email: "ana@x.com" } }),
    ]);
    expect(getByText("Se o e-mail estiver cadastrado, enviamos um link para redefinir a senha.")).toBeTruthy();
    expect(queryByText("algo bem diferente do texto fixo")).toBeNull();
  });

  it("429 (limite): mostra erro de verdade, não a mensagem neutra", async () => {
    rotear({ "/auth/forgot-password": () => resposta(429, { detail: "Muitas tentativas. Tente de novo em instantes." }) });
    const { getByLabelText, getByText, queryByText } = renderInterativo(<EsqueciSenha />);

    fireEvent.changeText(getByLabelText("E-mail"), "ana@x.com");
    await act(async () => {
      fireEvent.press(getByText("Enviar"));
      await respirar();
    });

    expect(getByText("Muitas tentativas. Tente de novo em instantes.")).toBeTruthy();
    expect(queryByText("Se o e-mail estiver cadastrado, enviamos um link para redefinir a senha.")).toBeNull();
  });

  it("botão Enviar desativado com e-mail vazio", () => {
    const { getByRole } = renderInterativo(<EsqueciSenha />);
    expect(getByRole("button").props.accessibilityState).toMatchObject({ disabled: true });
  });
});
