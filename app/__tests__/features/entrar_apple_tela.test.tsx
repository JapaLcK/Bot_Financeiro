// Mesmo motivo de `entrar_tela.test.tsx`: sem este dublê, `useColorScheme()`
// chega `undefined` no `_layout.tsx` real e derruba o render. Mutável: o
// estilo do botão da Apple segue o tema.
const mockEsquema: { valor: "light" | "dark" } = { valor: "light" };
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => mockEsquema.valor,
}));

import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import type { WebBrowserAuthSessionResult } from "expo-web-browser";
import { Platform, StyleSheet } from "react-native";

import { lerCredenciais } from "@/storage/secure";

import { credencialApple, folhaApple, rotasApple, voltaDaApple } from "./apple_apoio";
import { chamadas, prepararCaso, resposta, segurar } from "./auth_apoio";
import { abrirFolha } from "./google_apoio";

/** Drena microtarefas sem `setTimeout(0)`, que trava dentro de `act()` com `renderRouter`. */
const respirar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};
const botao = (nome: string) => screen.getByRole("button", { name: nome });
const desativado = (nome: string) => botao(nome).props.accessibilityState.disabled;
const APPLE = "Continuar com a Apple";
/**
 * O invólucro do botão do sistema. `fireEvent.press` chama `onPress` direto e
 * ignora `pointerEvents`: a guarda "Apple inerte em A/E/G" só se mede aqui.
 */
function involucro() {
  const v = screen.getByTestId("apple-involucro");
  return { pointerEvents: v.props.pointerEvents, opacity: StyleSheet.flatten(v.props.style).opacity };
}

beforeEach(() => {
  prepararCaso();
  rotasApple();
  voltaDaApple();
  mockEsquema.valor = "light";
});
afterEach(() => jest.restoreAllMocks());

async function abrirEntrar() {
  renderRouter("./app", { initialUrl: "/entrar" });
  await waitFor(() => expect(screen).toHavePathname("/entrar"));
  // A 1ª montagem da suíte, com o Jest em paralelo, pode vir depois do caminho.
  await waitFor(() => botao("Continuar com Google"));
}

async function tocar(nome: string) {
  await act(async () => {
    fireEvent.press(botao(nome));
    await respirar();
  });
}

describe("(auth)/entrar — Continuar com a Apple", () => {
  it("iOS: botão do sistema CONTINUE, contorno branco no claro, raio e altura do Google; sem 'em breve'", async () => {
    await abrirEntrar();
    expect(botao(APPLE).props).toMatchObject({ buttonType: 1, buttonStyle: 1, cornerRadius: 12, style: { height: 44 } });
    expect(screen.queryByText("Entrar com Apple chega em breve.")).toBeNull();
    expect(involucro()).toEqual({ pointerEvents: "auto", opacity: 1 });
  });

  it("iOS, tema escuro: botão branco", async () => {
    mockEsquema.valor = "dark";
    await abrirEntrar();
    expect(botao(APPLE).props.buttonStyle).toBe(0);
  });

  it("Android: nem botão da Apple nem legenda", async () => {
    jest.replaceProperty(Platform, "OS", "android");
    await abrirEntrar();
    expect(screen.queryByRole("button", { name: APPLE })).toBeNull();
    expect(screen.queryByText(/Apple/)).toBeNull();
  });

  // Controle negativo (medido): `apple: false` no `APAGA_SENHA` deixa a senha
  // no campo durante A.
  it("A — com a folha aberta: senha esvaziada, o resto desativado, indicador ao lado; um 2º toque não abre outra folha; fechar volta sem aviso", async () => {
    let fechar: () => void = () => {};
    folhaApple.mockReset();
    folhaApple.mockReturnValue(
      new Promise((_r, rejeitar) => (fechar = () => rejeitar(Object.assign(new Error("x"), { code: "ERR_REQUEST_CANCELED" })))),
    );
    await abrirEntrar();
    fireEvent.changeText(screen.getByLabelText("E-mail"), "ana@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");

    await tocar(APPLE);
    expect(screen.getByLabelText("Senha").props.value).toBe("");
    expect(screen.getByLabelText("E-mail").props.accessibilityState).toMatchObject({ disabled: true });
    for (const nome of ["Entrar", "Esqueci a senha", "Continuar com Google"]) expect(desativado(nome)).toBe(true);
    expect(screen.getByLabelText("Entrando com a Apple")).toBeTruthy();
    expect(involucro()).toEqual({ pointerEvents: "none", opacity: 1 });
    await tocar(APPLE);
    expect(folhaApple).toHaveBeenCalledTimes(1);

    await act(async () => {
      fechar();
      await respirar();
    });
    expect(screen.queryByLabelText("Entrando com a Apple")).toBeNull();
    expect(desativado("Continuar com Google")).toBe(false);
    expect(involucro()).toEqual({ pointerEvents: "auto", opacity: 1 });
    expect(screen.getByLabelText("E-mail").props.value).toBe("ana@x.com");
    expect(screen.queryByText("Não deu para entrar com a Apple. Tente de novo.")).toBeNull();
    expect(chamadas()).toEqual([]);
  });

  it("G — com a folha do Google aberta, a Apple não responde", async () => {
    let fechar: () => void = () => {};
    abrirFolha.mockReset();
    abrirFolha.mockReturnValue(new Promise((r) => (fechar = () => r({ type: "cancel" } as WebBrowserAuthSessionResult))));
    await abrirEntrar();

    await tocar("Continuar com Google");
    expect(involucro()).toEqual({ pointerEvents: "none", opacity: 0.5 });
    await tocar(APPLE);
    expect(folhaApple).not.toHaveBeenCalled();

    await act(async () => {
      fechar();
      await respirar();
    });
    await tocar(APPLE);
    expect(folhaApple).toHaveBeenCalledTimes(1);
  });

  it("E — com o login em andamento, a Apple fica inerte e apagada; volta ao fim", async () => {
    const portao = segurar();
    rotasApple({
      "/auth/login": async () => {
        await portao.promessa;
        return resposta(401, { detail: "E-mail ou senha incorretos." });
      },
    });
    await abrirEntrar();
    fireEvent.changeText(screen.getByLabelText("E-mail"), "ana@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");

    await tocar("Entrar");
    expect(involucro()).toEqual({ pointerEvents: "none", opacity: 0.5 });
    await act(async () => {
      portao.soltar();
      await respirar();
    });
    expect(involucro()).toEqual({ pointerEvents: "auto", opacity: 1 });
  });

  it("caminho feliz: troca o token e abre o app como Ana", async () => {
    await abrirEntrar();
    await tocar(APPLE);
    await waitFor(() => expect(screen.getByText(/Olá, Ana/)).toBeTruthy());
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-ana", refresh: "rt_ana" });
  });

  it("falha da Apple: o aviso aparece no formulário e o botão volta a responder", async () => {
    folhaApple.mockReset();
    folhaApple.mockResolvedValue(credencialApple({ identityToken: null }));
    await abrirEntrar();
    await tocar(APPLE);
    expect(screen.getByText("Não deu para entrar com a Apple. Tente de novo.")).toBeTruthy();
    expect(screen.queryByLabelText("Entrando com a Apple")).toBeNull();
  });
});
