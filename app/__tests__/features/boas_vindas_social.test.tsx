// Mesmo motivo de `entrar_tela.test.tsx`: sem este dublê, `useColorScheme()`
// chega `undefined` no `_layout.tsx` real e derruba o render.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => "light",
}));

import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { router } from "expo-router";

import { FALHA_APPLE } from "@/features/auth/apple";
import { MENSAGEM_ERRO_COFRE } from "@/features/auth/entrar";
import { CADASTRO_GOOGLE_EXPIRADO } from "@/features/auth/google";
import { lerCredenciais } from "@/storage/secure";

import { falhaDaApple, rotasApple, voltaDaApple } from "./apple_apoio";
import { chamadas, credencialDe, falharEscrita, prepararCaso, resposta } from "./auth_apoio";
import { rotasGoogle, voltaDoGoogle } from "./google_apoio";

/** Drena microtarefas sem `setTimeout(0)`, que trava dentro de `act()` com `renderRouter`. */
const respirar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};
const botao = (nome: string) => screen.getByRole("button", { name: nome });
const FRASE = "Sua grana. Tudo mais claro.";
const GOOGLE = "Continuar com Google";
const APPLE = "Continuar com a Apple";

async function tocar(nome: string) {
  await act(async () => {
    fireEvent.press(botao(nome));
    await respirar();
  });
}

async function abrirBV() {
  renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
  await tocar("Começar");
  await waitFor(() => botao(GOOGLE));
}

beforeEach(() => {
  prepararCaso();
  rotasApple();
});

describe("(auth)/boas-vindas — Continuar com Google", () => {
  it("direto: troca o código e abre o app como Ana", async () => {
    voltaDoGoogle("pigbank://auth?code=code-ana");
    await abrirBV();
    await tocar(GOOGLE);
    await waitFor(() => expect(screen.getByText(/Olá, Ana/)).toBeTruthy());
    expect(screen).toHavePathname("/");
  });

  it("folha fechada: a BV fica parada, sem aviso e sem requisição", async () => {
    voltaDoGoogle({ type: "cancel" });
    await abrirBV();
    await tocar(GOOGLE);
    expect(screen.getByText(FRASE)).toBeTruthy();
    expect(screen.queryByText(/Tente de novo/)).toBeNull();
    expect(botao(GOOGLE).props.accessibilityState).toMatchObject({ busy: false });
    expect(chamadas()).toEqual([]);
  });

  // Controle negativo (medido): sem o `setEstado` de `ir()` na BV, o aviso
  // continua lá depois de ir ao Entrar e voltar.
  it("?erro=falha: Banner na BV, que some no toque seguinte", async () => {
    voltaDoGoogle("pigbank://auth?erro=falha");
    await abrirBV();
    await tocar(GOOGLE);
    expect(screen.getByText("Não deu para entrar com o Google. Tente de novo.")).toBeTruthy();
    expect(screen).toHavePathname("/boas-vindas");

    await tocar("Já tenho conta");
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    await act(async () => {
      router.back();
      await respirar();
    });
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
    expect(screen.queryByText("Não deu para entrar com o Google. Tente de novo.")).toBeNull();
  });

  it("conta nova: cadastro social na própria BV (título Criar conta); Voltar volta à BV parada", async () => {
    voltaDoGoogle("pigbank://auth?onboarding=gso_bia");
    await abrirBV();
    await tocar(GOOGLE);
    await waitFor(() => screen.getByLabelText(/^WhatsApp/));
    expect(screen).toHavePathname("/boas-vindas");
    expect(screen.getByText("bia@gmail.com")).toBeTruthy();
    expect(screen.getAllByText("Criar conta")).toHaveLength(2); // o título e o botão
    expect(screen.queryByText(FRASE)).toBeNull();

    fireEvent.press(botao("Voltar"));
    expect(screen.getByText(FRASE)).toBeTruthy();
    expect(screen.queryByLabelText(/^WhatsApp/)).toBeNull();
  });

  it("conta com MFA: o código é pedido na BV (título Entrar) e o certo abre o Início", async () => {
    voltaDoGoogle("pigbank://auth?code=code-ana");
    rotasGoogle({
      "/auth/google/exchange": () => resposta(200, { mfa_required: true, mfa_challenge: "ch-g", email: "ana@x.com" }),
      "/auth/mfa/verify-login": () => resposta(200, credencialDe("ana@x.com")),
    });
    await abrirBV();
    await tocar(GOOGLE);
    await waitFor(() => screen.getByLabelText("Código de 6 dígitos"));
    expect(screen).toHavePathname("/boas-vindas");
    expect(screen.getByText("Entrar")).toBeTruthy();

    await act(async () => {
      fireEvent.changeText(screen.getByLabelText("Código de 6 dígitos"), "123456");
      await respirar();
    });
    await waitFor(() => expect(screen.getByText(/Olá, Ana/)).toBeTruthy());
  });

  it("pré-cadastro que sumiu (404): Banner de cadastro expirado na BV", async () => {
    voltaDoGoogle("pigbank://auth?onboarding=gso_bia");
    rotasGoogle({ "/auth/google/pending/gso_bia": () => resposta(404, { detail: "Cadastro expirado ou inválido." }) });
    await abrirBV();
    await tocar(GOOGLE);
    expect(screen.getByText(CADASTRO_GOOGLE_EXPIRADO)).toBeTruthy();
    expect(screen.getByText(FRASE)).toBeTruthy();
  });
});

describe("(auth)/boas-vindas — Continuar com a Apple", () => {
  it("direto: abre o app como Ana", async () => {
    voltaDaApple();
    await abrirBV();
    await tocar(APPLE);
    await waitFor(() => expect(screen.getByText(/Olá, Ana/)).toBeTruthy());
  });

  it("folha cancelada: BV parada, sem aviso; outro erro da folha: Banner", async () => {
    falhaDaApple("ERR_REQUEST_CANCELED");
    await abrirBV();
    await tocar(APPLE);
    expect(screen.queryByText(FALHA_APPLE)).toBeNull();
    expect(screen.getByText(FRASE)).toBeTruthy();

    falhaDaApple("ERR_REQUEST_FAILED");
    await tocar(APPLE);
    expect(screen.getByText(FALHA_APPLE)).toBeTruthy();
  });

  it("cadastro expirado no complete-signup: volta à BV com o Banner do servidor", async () => {
    const detail = "Cadastro expirado. Inicie novamente o login com a Apple.";
    rotasApple({ "/auth/apple/complete-signup": () => resposta(400, { detail }) });
    voltaDaApple({ identityToken: "id-token-leo" });
    await abrirBV();
    await tocar(APPLE);
    await waitFor(() => screen.getByLabelText(/^WhatsApp/));
    fireEvent.changeText(screen.getByLabelText(/^WhatsApp/), "(11) 99999-8888");
    await tocar("Criar conta");
    expect(screen.getByText(detail)).toBeTruthy();
    expect(screen.getByText(FRASE)).toBeTruthy();
  });

  it("o cofre recusa: erro do cofre na BV; 'Tentar de novo' volta à BV parada", async () => {
    voltaDaApple();
    falharEscrita(true);
    await abrirBV();
    await tocar(APPLE);
    expect(screen.getByText(MENSAGEM_ERRO_COFRE)).toBeTruthy();
    expect(screen.queryByRole("button", { name: GOOGLE })).toBeNull();
    await expect(lerCredenciais()).resolves.toBeNull();

    fireEvent.press(botao("Tentar de novo"));
    expect(screen.getByText(FRASE)).toBeTruthy();
  });
});
