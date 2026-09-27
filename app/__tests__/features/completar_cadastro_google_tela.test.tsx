// Mesmo motivo de `entrar_tela.test.tsx`: sem este dublê, `useColorScheme()`
// chega `undefined` no `_layout.tsx` real e derruba o render.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => "light",
}));

import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { Linking } from "react-native";

import { LEGENDA_WHATSAPP } from "@/features/auth/criarConta";
import { ERRO_COFRE_GOOGLE } from "@/features/auth/google";
import { confirmarCadastro } from "@/services/auth";
import { lerCredenciais } from "@/storage/secure";

import { chamadas, credencialDe, falharEscrita, prepararCaso, resposta, segurar } from "./auth_apoio";
import { PENDENTE, rotasGoogle, voltaDoGoogle } from "./google_apoio";

const respirar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};
const botao = (nome: string) => screen.getByRole("button", { name: nome });
// Regex: com erro o rótulo vira "<rótulo>, erro: <aviso>" (Input.tsx).
const campo = (rotulo: string) => screen.getByLabelText(new RegExp(`^${rotulo}`));
const cadastros = () => chamadas().filter((c) => c.caminho === "/auth/google/complete-signup");

beforeEach(() => {
  prepararCaso();
  rotasGoogle();
  voltaDoGoogle("pigbank://auth?onboarding=gso_bia");
});
afterEach(() => jest.restoreAllMocks());

/** Entrar → Google → conta nova: chega em C. */
async function irAoCadastro() {
  renderRouter("./app", { initialUrl: "/entrar" });
  await waitFor(() => expect(screen).toHavePathname("/entrar"));
  await waitFor(() => botao("Continuar com Google"));
  fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
  await act(async () => {
    fireEvent.press(botao("Continuar com Google"));
    await respirar();
  });
  await waitFor(() => campo("WhatsApp"));
}

async function criarConta(telefone = "(11) 99999-8888") {
  fireEvent.changeText(campo("WhatsApp"), telefone);
  await act(async () => {
    fireEvent.press(botao("Criar conta"));
    await respirar();
  });
}

describe("(auth)/entrar — cadastro pelo Google (C/K)", () => {
  it("C — mesma rota; e-mail só leitura, nome do Google, WhatsApp vazio, Termos em texto e nenhum campo de senha", async () => {
    await irAoCadastro();
    expect(screen).toHavePathname("/entrar");
    expect(screen.getAllByText("Criar conta")).toHaveLength(2); // o título e o botão
    expect(campo("E-mail").props.value).toBe(PENDENTE.email);
    expect(campo("E-mail").props.editable).toBe(false);
    expect(campo("Nome").props.value).toBe(PENDENTE.name_hint);
    expect(campo("WhatsApp").props.value).toBe("");
    expect(screen.getByText(LEGENDA_WHATSAPP)).toBeTruthy();
    expect(screen.getByText("Termos de Uso")).toBeTruthy();
    expect(screen.getByText("Política de Privacidade")).toBeTruthy();
    expect(screen.queryByLabelText(/^Senha/)).toBeNull();
  });

  it("10b — WhatsApp inválido: erro no campo e NENHUMA requisição", async () => {
    await irAoCadastro();
    await criarConta("99999-8888");
    expect(screen.getByText("Informe um número de WhatsApp válido com DDD.")).toBeTruthy();
    expect(cadastros()).toEqual([]);
  });

  it("10c/11 — Criar conta: K com o botão em carregando e Voltar desativado; depois abre o app como Bia", async () => {
    const portao = segurar();
    rotasGoogle({
      "/auth/google/complete-signup": async () => {
        await portao.promessa;
        return resposta(200, credencialDe("bia@gmail.com"));
      },
    });
    await irAoCadastro();
    fireEvent.changeText(campo("Nome"), "Bia S.");
    await criarConta();
    expect(botao("Criar conta").props.accessibilityState).toMatchObject({ busy: true });
    expect(botao("Voltar").props.accessibilityState).toMatchObject({ disabled: true });
    expect(campo("WhatsApp").props.editable).toBe(false);

    await act(async () => {
      portao.soltar();
      await respirar();
    });
    await waitFor(() => expect(screen.getByText(/Olá, Bia/)).toBeTruthy());
    expect(cadastros().map((c) => c.corpo)).toEqual([
      { token: "gso_bia", name: "Bia S.", phone: "11999998888", accepted_terms: true },
    ]);
  });

  it("11a — 400 do servidor: fica em C com o aviso e com o que foi digitado", async () => {
    rotasGoogle({ "/auth/google/complete-signup": () => resposta(400, { detail: "O nome deve ter entre 2 e 50 caracteres." }) });
    await irAoCadastro();
    fireEvent.changeText(campo("Nome"), "Bia S.");
    await criarConta();
    expect(screen.getByText("O nome deve ter entre 2 e 50 caracteres.")).toBeTruthy();
    expect(campo("Nome").props.value).toBe("Bia S.");
    expect(campo("WhatsApp").props.value).toBe("(11) 99999-8888");

    fireEvent.changeText(campo("WhatsApp"), "(11) 99999-8887");
    expect(screen.queryByText("O nome deve ter entre 2 e 50 caracteres.")).toBeNull();
  });

  it("11b — cadastro expirado: volta ao formulário de Entrar com o aviso", async () => {
    const detail = "Cadastro expirado. Inicie novamente o login com Google.";
    rotasGoogle({ "/auth/google/complete-signup": () => resposta(400, { detail }) });
    await irAoCadastro();
    await criarConta();
    expect(screen.getByText(detail)).toBeTruthy();
    expect(botao("Continuar com Google")).toBeTruthy();
  });

  it("11e — o cofre recusa: X diz que a conta foi criada e manda entrar com o Google de novo", async () => {
    falharEscrita(true);
    await irAoCadastro();
    await criarConta();
    expect(screen.getByText(ERRO_COFRE_GOOGLE)).toBeTruthy();
  });

  it("Termos abre o site", async () => {
    const abrirUrl = jest.mocked(Linking.openURL);
    await irAoCadastro();
    await act(async () => {
      fireEvent.press(screen.getByText("Termos de Uso"));
      await respirar();
    });
    expect(abrirUrl).toHaveBeenLastCalledWith("http://backend.teste/termos");
  });

  // Controle negativo (medido): o Voltar de C chamando `voltar()` (que chama
  // `abandonarEntrada()`) faz o cadastro em voo virar `EntradaSuperada` e o
  // cofre fica vazio. #592: o contador de tentativas é compartilhado.
  it("10 — Voltar: formulário de Entrar com a senha vazia, sem abandonar um cadastro em voo em outra rota", async () => {
    const portao = segurar();
    rotasGoogle({
      "/auth/verify-email": async () => {
        await portao.promessa;
        return resposta(200, credencialDe("ana@x.com"));
      },
    });
    const emVoo = confirmarCadastro("ana@x.com", "123456").catch((e: Error) => e.name);
    await irAoCadastro();

    fireEvent.press(botao("Voltar"));
    expect(botao("Continuar com Google")).toBeTruthy();
    expect(screen.getByLabelText("Senha").props.value).toBe("");
    expect(screen.queryByLabelText(/^WhatsApp/)).toBeNull();

    await act(async () => {
      portao.soltar();
      await respirar();
    });
    await expect(emVoo).resolves.toMatchObject({ email: "ana@x.com" });
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-ana", refresh: "rt_ana" });
  });
});
