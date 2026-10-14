import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { chamadas, credencialDe, resposta, rotear, type Rota } from "./auth_apoio";

/** Ajudantes da tela Criar conta, divididos entre `criar_conta_tela*.test.tsx` (teto de 350 linhas). */

/** Drena microtarefas sem `setTimeout(0)`, que trava dentro de `act()` com `renderRouter`. */
export const respirar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

const ENVIADO = { status: "verification_sent", email: "ana@x.com" };
export const CORPO = { email: "ana@x.com", password: "s3nha-boa", name: "Ana", phone: "11999998888" };
export const registers = () => chamadas().filter((c) => c.caminho === "/auth/register");
export const verifies = () => chamadas().filter((c) => c.caminho === "/auth/verify-email");
export const botao = (nome: string) => screen.getByRole("button", { name: nome });
// Regex: com erro o rótulo vira "<rótulo>, erro: <aviso>" (Input.tsx).
export const campo = (rotulo: string) => screen.getByLabelText(new RegExp(`^${rotulo}`));

export function rotas(extra: Record<string, Rota> = {}) {
  rotear({
    "/auth/register": () => resposta(200, ENVIADO),
    "/auth/verify-email": (o) =>
      (JSON.parse(String(o.body)) as { code: string }).code === "123456"
        ? resposta(200, credencialDe("ana@x.com"))
        : resposta(400, { detail: "Código inválido ou expirado." }),
    ...extra,
  });
}

export async function abrir() {
  renderRouter("./app", { initialUrl: "/criar-conta" });
  await waitFor(() => expect(screen).toHavePathname("/criar-conta"));
}

export function preencher(telefone = "(11) 99999-8888") {
  fireEvent.changeText(campo("Nome"), " Ana ");
  fireEvent.changeText(campo("E-mail"), "ana@x.com");
  fireEvent.changeText(campo("WhatsApp"), telefone);
  fireEvent.changeText(campo("Senha"), "s3nha-boa");
}

export async function irAoCodigo() {
  await abrir();
  preencher();
  await act(async () => {
    fireEvent.press(botao("Criar conta"));
    await respirar();
  });
  await waitFor(() => campo("Código de 6 dígitos"));
}
