/**
 * Segurança para conta sem senha (Google/Apple, `has_password: false`): em vez de
 * "Ativar" (que daria 409 no setup), o bloco "Definir senha" manda o link de
 * `/auth/forgot-password` para o e-mail da própria conta. SÓ `false` mostra o
 * bloco; `true` e AUSENTE (backend velho, /auth/me fora) seguem com "Ativar".
 */
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { guardarCredenciais } from "@/storage/secure";

import { chamadas, prepararCaso, resposta, rotear, S, type Rota } from "./auth_apoio";

const MFA_DESLIGADO = { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 };
const NEUTRA = "Se o e-mail estiver cadastrado, enviamos um link para redefinir a senha.";

const drenar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

/** `/auth/me` com `has_password` controlado; `undefined` = o campo nem vem. */
function abrir(hasPassword: { valor: boolean | undefined }, extra: Record<string, Rota> = {}) {
  rotear({
    "/auth/mfa/status": () => resposta(200, MFA_DESLIGADO),
    "/auth/forgot-password": () => resposta(200, { message: "texto do servidor" }),
    "/auth/me": () =>
      resposta(200, { user_id: 1, email: "s@x.com", display_name: "S", app_access: true, ...(hasPassword.valor === undefined ? {} : { has_password: hasPassword.valor }) }),
    ...extra,
  });
  return guardarCredenciais(S).then(() => renderRouter("./app", { initialUrl: "/seguranca" }));
}

const tocar = (rotulo: string) =>
  act(async () => {
    fireEvent.press(screen.getByRole("button", { name: rotulo }));
    await drenar();
  });
const forgot = () => chamadas().filter((c) => c.caminho === "/auth/forgot-password");

const appState = globalThis as unknown as { __dispararAppState: (v: string) => void };
const appVai = (v: "active" | "background") =>
  act(async () => {
    appState.__dispararAppState(v);
    await drenar();
  });

beforeEach(() => {
  prepararCaso();
  appState.__dispararAppState("active");
});

describe("Segurança — conta sem senha", () => {
  it("has_password false: mostra Definir senha, esconde Ativar e manda o link para o e-mail DA CONTA", async () => {
    await abrir({ valor: false });
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Ativar" })).toBeNull();
    expect(screen.queryByLabelText("E-mail")).toBeNull();

    await tocar("Enviar link");

    expect(forgot()).toEqual([expect.objectContaining({ corpo: { email: "s@x.com" } })]);
    expect(screen.getByText(`${NEUTRA} Abra o link no navegador e volte ao app.`)).toBeTruthy();
  });

  it.each([
    ["true", true],
    ["ausente", undefined],
  ])("has_password %s: sem bloco Definir senha, com Ativar", async (_nome, valor) => {
    await abrir({ valor });
    await waitFor(() => expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy());
    expect(screen.queryByText("Definir senha")).toBeNull();
    expect(screen.queryByRole("button", { name: "Enviar link" })).toBeNull();
  });

  it("/auth/me fora do ar: lado permissivo, a tela segue com Ativar", async () => {
    await abrir({ valor: false }, { "/auth/me": () => resposta(500, { detail: "boom" }) });
    await waitFor(() => expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy());
  });

  it("429 do forgot-password: aviso de 1 hora, não a mensagem neutra", async () => {
    await abrir({ valor: false }, { "/auth/forgot-password": () => resposta(429, { detail: "Muitas tentativas." }) });
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());

    await tocar("Enviar link");

    expect(screen.getByText("Você já pediu o link várias vezes. Tente de novo em 1 hora.")).toBeTruthy();
    expect(screen.queryByText(NEUTRA)).toBeNull();
  });

  it("ao ganhar foco com a senha já definida, troca o bloco por Ativar", async () => {
    const conta = { valor: false as boolean | undefined };
    await abrir(conta);
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());

    conta.valor = true; // a pessoa definiu a senha no navegador
    await act(async () => {
      router.push("/mfa-ativar");
      await drenar();
    });
    await act(async () => {
      router.back();
      await drenar();
    });

    await waitFor(() => expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy());
    expect(screen.queryByText("Definir senha")).toBeNull();
  });

  it("volta do Safari (background -> active) com a senha já definida: troca o bloco por Ativar, sem spinner", async () => {
    const conta = { valor: false as boolean | undefined };
    await abrir(conta);
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());
    await tocar("Enviar link");

    await appVai("background");
    conta.valor = true; // a pessoa definiu a senha no navegador
    await appVai("active");

    await waitFor(() => expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy());
    expect(screen.queryByText("Definir senha")).toBeNull();
    expect(screen.queryByLabelText("Carregando")).toBeNull();
  });

  it("voltar ao app com a senha ainda sem definir: o bloco continua, sem novo POST do forgot-password", async () => {
    await abrir({ valor: false });
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());
    await tocar("Enviar link");

    await appVai("background");
    await appVai("active");

    expect(screen.getByText(/Abra o link no navegador/)).toBeTruthy();
    expect(forgot()).toHaveLength(1);
    expect(chamadas().filter((c) => c.caminho === "/auth/me").length).toBeGreaterThanOrEqual(2);
  });
});
