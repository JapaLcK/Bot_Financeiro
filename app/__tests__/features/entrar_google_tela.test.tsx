// Mesmo motivo de `entrar_tela.test.tsx`: sem este dublê, `useColorScheme()`
// chega `undefined` no `_layout.tsx` real e derruba o render.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => "light",
}));

import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import type { WebBrowserAuthSessionResult } from "expo-web-browser";

import { abandonarEntrada } from "@/services/auth";
import { lerCredenciais } from "@/storage/secure";

import { chamadas, credencialDe, falharEscrita, prepararCaso, resposta, segurar } from "./auth_apoio";
import { abrirFolha, rotasGoogle, voltaDoGoogle } from "./google_apoio";

/** Drena microtarefas sem `setTimeout(0)`, que trava dentro de `act()` com `renderRouter`. */
const respirar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};
const botao = (nome: string) => screen.getByRole("button", { name: nome });
const desativado = (nome: string) => botao(nome).props.accessibilityState.disabled;

beforeEach(() => {
  prepararCaso();
  rotasGoogle();
});

async function abrirEntrar() {
  renderRouter("./app", { initialUrl: "/entrar" });
  await waitFor(() => expect(screen).toHavePathname("/entrar"));
  // A 1ª montagem da suíte, com o Jest em paralelo, pode vir depois do caminho.
  await waitFor(() => botao("Continuar com Google"));
}

async function tocarGoogle() {
  await act(async () => {
    fireEvent.press(botao("Continuar com Google"));
    await respirar();
  });
}

describe("(auth)/entrar — Continuar com Google", () => {
  it("o botão do Google está ativo; o 'em breve' é só da Apple", async () => {
    await abrirEntrar();
    expect(desativado("Continuar com Google")).toBe(false);
    expect(desativado("Continuar com Apple")).toBe(true);
    expect(screen.getByText("Entrar com Apple chega em breve.")).toBeTruthy();
    expect(screen.queryByText("Em breve")).toBeNull();
  });

  // Controle negativo (medido): `google: false` no `APAGA_SENHA` deixa a senha
  // no campo durante G. O positivo é o caminho feliz abaixo.
  it("G — com a folha aberta: senha esvaziada, formulário e links desativados, Google em carregando; fechar a folha volta sem aviso", async () => {
    let fechar: () => void = () => {};
    abrirFolha.mockReset();
    abrirFolha.mockReturnValue(new Promise((r) => (fechar = () => r({ type: "cancel" } as WebBrowserAuthSessionResult))));
    await abrirEntrar();
    fireEvent.changeText(screen.getByLabelText("E-mail"), "ana@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");

    await tocarGoogle();
    expect(screen.getByLabelText("Senha").props.value).toBe("");
    expect(screen.getByLabelText("E-mail").props.accessibilityState).toMatchObject({ disabled: true });
    expect(screen.getByLabelText("Senha").props.accessibilityState).toMatchObject({ disabled: true });
    for (const nome of ["Entrar", "Esqueci a senha", "Criar conta"]) expect(desativado(nome)).toBe(true);
    expect(botao("Continuar com Google").props.accessibilityState).toMatchObject({ busy: true });

    await act(async () => {
      fechar();
      await respirar();
    });
    expect(botao("Continuar com Google").props.accessibilityState).toMatchObject({ busy: false });
    expect(desativado("Esqueci a senha")).toBe(false);
    expect(screen.getByLabelText("Senha").props.accessibilityState).toMatchObject({ disabled: false });
    expect(screen.getByLabelText("E-mail").props.value).toBe("ana@x.com");
    expect(chamadas()).toEqual([]);
  });

  it("caminho feliz: troca o código e abre o app como Ana", async () => {
    voltaDoGoogle("pigbank://auth?code=code-ana");
    await abrirEntrar();
    await tocarGoogle();

    await waitFor(() => expect(screen.getByText(/Olá, Ana/)).toBeTruthy());
    expect(screen).toHavePathname("/");
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-ana", refresh: "rt_ana" });
  });

  it("conta com MFA: pede o código TOTP e só então entra", async () => {
    voltaDoGoogle("pigbank://auth?code=code-ana");
    rotasGoogle({
      "/auth/google/exchange": () => resposta(200, { mfa_required: true, mfa_challenge: "ch-g", email: "ana@x.com" }),
      "/auth/mfa/verify-login": () => resposta(200, credencialDe("ana@x.com")),
    });
    await abrirEntrar();
    await tocarGoogle();
    await waitFor(() => screen.getByLabelText("Código de 6 dígitos"));
    await expect(lerCredenciais()).resolves.toBeNull();

    await act(async () => {
      fireEvent.changeText(screen.getByLabelText("Código de 6 dígitos"), "123456");
      await respirar();
    });
    await waitFor(() => expect(screen.getByText(/Olá, Ana/)).toBeTruthy());
    expect(chamadas().filter((c) => c.caminho === "/auth/mfa/verify-login").map((c) => c.corpo.challenge)).toEqual(["ch-g"]);
  });

  it("erro do Google: o aviso aparece no formulário", async () => {
    voltaDoGoogle("pigbank://auth?erro=email_nao_verificado");
    await abrirEntrar();
    await tocarGoogle();
    expect(screen.getByText("Seu e-mail no Google ainda não foi verificado. Verifique no Google e tente de novo.")).toBeTruthy();
    expect(desativado("Continuar com Google")).toBe(false);
  });

  // Controle negativo (medido): `EntradaSuperada` devolvendo `null` em G deixa
  // este vermelho — o Google fica em carregando e o formulário travado.
  it("6f — troca superada no meio: a tela volta ao formulário, nunca fica presa em G", async () => {
    voltaDoGoogle("pigbank://auth?code=code-ana");
    const portao = segurar();
    rotasGoogle({
      "/auth/google/exchange": async () => {
        await portao.promessa;
        return resposta(200, credencialDe("ana@x.com"));
      },
    });
    await abrirEntrar();
    await tocarGoogle();
    expect(botao("Continuar com Google").props.accessibilityState).toMatchObject({ busy: true });

    await act(async () => {
      abandonarEntrada();
      portao.soltar();
      await respirar();
    });
    expect(botao("Continuar com Google").props.accessibilityState).toMatchObject({ busy: false });
    expect(desativado("Criar conta")).toBe(false);
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  it("6e — o cofre recusa: fase X; 'Tentar de novo' volta ao formulário", async () => {
    voltaDoGoogle("pigbank://auth?code=code-ana");
    falharEscrita(true);
    await abrirEntrar();
    await tocarGoogle();
    expect(screen.getByText("Não conseguimos abrir sua sessão neste aparelho. Tente de novo.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Continuar com Google" })).toBeNull();

    fireEvent.press(botao("Tentar de novo"));
    expect(desativado("Continuar com Google")).toBe(false);
  });
});
