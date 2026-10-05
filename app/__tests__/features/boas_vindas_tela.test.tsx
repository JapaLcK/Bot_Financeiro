// Mesmo motivo de `entrar_tela.test.tsx`: sem este dublê, `useColorScheme()`
// chega `undefined` no `_layout.tsx` real e derruba o render.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => "light",
}));

import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { router } from "expo-router";

import { guardarCredenciais } from "@/storage/secure";

import { S, prepararCaso, resposta, segurar } from "./auth_apoio";
import { CODIGO_INVALIDO, rotasGoogle, voltaDoGoogle } from "./google_apoio";

/** Drena microtarefas sem `setTimeout(0)`, que trava dentro de `act()` com `renderRouter`. */
const respirar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};
const botao = (nome: string) => screen.getByRole("button", { name: nome });
const desativado = (nome: string) => botao(nome).props.accessibilityState.disabled;
const FRASE = "Sua grana. Tudo mais claro.";

async function tocar(nome: string) {
  await act(async () => {
    fireEvent.press(botao(nome));
    await respirar();
  });
}

async function voltar() {
  await act(async () => {
    router.back();
    await respirar();
  });
}

/** A troca do Google fica presa até `portao.soltar()`; depois responde 400. */
function trocaPresa() {
  voltaDoGoogle("pigbank://auth?code=code-ana");
  const portao = segurar();
  rotasGoogle({
    "/auth/google/exchange": async () => {
      await portao.promessa;
      return resposta(400, CODIGO_INVALIDO);
    },
  });
  return portao;
}

beforeEach(() => {
  prepararCaso();
  rotasGoogle();
});

describe("(auth)/boas-vindas — navegação", () => {
  // Controle negativo (medido): `entrar` declarado antes de `boas-vindas` no
  // `(auth)/_layout.tsx` deixa este vermelho (a rota padrão vira /entrar).
  it("N1 — sem sessão abre /boas-vindas com o nome, a frase e os caminhos após Começar", async () => {
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
    expect(screen.getByRole("header", { name: "PigBank" })).toBeTruthy();
    expect(screen.getByText(FRASE)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Continuar com Google" })).toBeNull();
    await tocar("Começar");
    expect(botao("Continuar com a Apple")).toBeTruthy(); // o botão do sistema não tem `accessibilityState`
    for (const nome of ["Continuar com Google", "Criar conta", "Já tenho conta"]) expect(desativado(nome)).toBe(false);
    expect(router.canGoBack()).toBe(false);
    await tocar("Voltar");
    expect(botao("Começar")).toBeTruthy();
    expect(screen.getByRole("header", { name: "PigBank" })).toBeTruthy();
  });

  it("'Já tenho conta' abre /entrar sem o link de Criar conta, e voltar volta para cá", async () => {
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
    await tocar("Já tenho conta");
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    expect(screen.queryByText("Não tem conta?")).toBeNull();
    expect(screen.queryByRole("button", { name: "Criar conta" })).toBeNull();

    await voltar();
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
  });

  // Controle negativo (medido): sem o `desativado` dos dois links, o toque em
  // "Já tenho conta" com a troca presa leva a /entrar. O positivo é o fim do caso.
  it("N2 — com a troca do Google presa, 'Criar conta' e 'Já tenho conta' ficam desativados e não navegam", async () => {
    const portao = trocaPresa();
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));

    await tocar("Começar");
    await tocar("Continuar com Google");
    expect(botao("Continuar com Google").props.accessibilityState).toMatchObject({ busy: true });
    expect(desativado("Criar conta")).toBe(true);
    expect(desativado("Já tenho conta")).toBe(true);
    await tocar("Já tenho conta");
    await tocar("Criar conta");
    expect(screen).toHavePathname("/boas-vindas");

    await act(async () => {
      portao.soltar();
      await respirar();
    });
    expect(screen.getByText(CODIGO_INVALIDO.detail)).toBeTruthy();
    expect(desativado("Criar conta")).toBe(false);
    await tocar("Já tenho conta");
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
  });

  // Controle negativo (medido): sem o `usePreventRemove` do Entrar, o
  // `router.back()` com a troca em voo sai para /boas-vindas.
  it("N3 — [BV, Entrar] com o Google em voo no Entrar: voltar não sai; depois sai, e o Google da BV entra", async () => {
    const portao = trocaPresa();
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    await waitFor(() => botao("Continuar com Google"));

    await tocar("Continuar com Google");
    await voltar();
    expect(screen).toHavePathname("/entrar");

    await act(async () => {
      portao.soltar();
      await respirar();
    });
    expect(screen.getByText(CODIGO_INVALIDO.detail)).toBeTruthy();
    await voltar();
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));

    // A fila de `entrar.ts` é do módulo: se o Entrar a tivesse deixado presa,
    // a BV ficaria em carregando para sempre.
    rotasGoogle();
    await tocar("Começar");
    await tocar("Continuar com Google");
    await waitFor(() => expect(screen.getByText(/Olá, Ana/)).toBeTruthy());
    expect(screen).toHavePathname("/");
  });

  // Controle negativo (medido): sem o efeito de montagem da BV, a sessão
  // expirada para em /boas-vindas, sem o aviso.
  it("N4 — sessão expirada: /entrar com o aviso por cima da BV; voltar chega à BV", async () => {
    await guardarCredenciais(S);
    rotasGoogle({
      "/auth/me": () => resposta(401, { detail: "expirado" }),
      "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
    });
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
    expect(router.canGoBack()).toBe(true);

    await voltar();
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
    expect(screen.getByText(FRASE)).toBeTruthy();
  });
});
