// Mesmo motivo de `entrar_tela.test.tsx`: sem este dublê, `useColorScheme()`
// chega `undefined` no `_layout.tsx` real e derruba o render.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => "light",
}));

import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { router } from "expo-router";
import { Linking, TextInput } from "react-native";

import * as criarConta from "@/features/auth/criarConta";
import { GENERICO } from "@/features/auth/entrar";
import * as authService from "@/services/auth";
import { FalhaNoCofre, guardarCredenciais, lerCredenciais } from "@/storage/secure";

import { S, falharEscrita, fetchFalso, prepararCaso, resposta, rotear, segurar } from "./auth_apoio";
import { CORPO, abrir, botao, campo, irAoCodigo, preencher, registers, respirar, rotas, verifies } from "./criar_conta_tela_apoio";

beforeEach(() => {
  prepararCaso();
  rotas();
});
afterEach(() => jest.restoreAllMocks());

describe("(auth)/criar-conta — tela real", () => {
  it("T1 — Entrar leva a Criar conta; o link fica desativado enquanto o login envia", async () => {
    const portao = segurar();
    rotear({ "/auth/login": async () => { await portao.promessa; return resposta(401, { detail: "x" }); } });
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));

    fireEvent.changeText(screen.getByLabelText("E-mail"), "a@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    fireEvent.press(botao("Entrar"));
    expect(botao("Criar conta").props.accessibilityState).toMatchObject({ disabled: true });
    await act(async () => {
      portao.soltar();
      await respirar();
    });

    fireEvent.press(botao("Criar conta"));
    await waitFor(() => expect(screen).toHavePathname("/criar-conta"));
  });

  it("T2 — deep link frio tem /entrar embaixo, e 'Já tem conta? Entrar' volta para lá", async () => {
    await abrir();
    expect(router.canGoBack()).toBe(true);

    await act(async () => {
      fireEvent.press(botao("Entrar"));
      await respirar();
    });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
  });

  it("T3 — com sessão, /criar-conta não abre: cai na tela autenticada", async () => {
    await guardarCredenciais(S);
    renderRouter("./app", { initialUrl: "/criar-conta" });
    await waitFor(() => expect(screen).toHavePathname("/"));
  });

  it("T4 — caminho feliz: register com o telefone só em dígitos, código de 6 dígitos, e 'Olá, Ana'", async () => {
    await irAoCodigo();
    expect(screen.getByText("Enviamos um código de 6 dígitos para ana@x.com.")).toBeTruthy();

    await act(async () => {
      fireEvent.changeText(campo("Código de 6 dígitos"), "123456");
      await respirar();
    });

    await waitFor(() => expect(screen.getByText(/Olá, Ana/)).toBeTruthy());
    expect(registers().map((c) => c.corpo)).toEqual([CORPO]);
    expect(verifies().map((c) => c.corpo)).toEqual([{ email: "ana@x.com", code: "123456" }]);
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-ana", refresh: "rt_ana" });
  });

  // Controle negativo (medido): tirar a checagem de telefone de `validar`
  // deixa os dois vermelhos, com 1 chamada. O positivo é o T4.
  it.each(["", "99999-8888"])("T5 — WhatsApp %j: erro no campo e NENHUMA requisição", async (telefone) => {
    await abrir();
    preencher(telefone);
    fireEvent.press(botao("Criar conta"));

    expect(fetchFalso).not.toHaveBeenCalled();
    expect(campo("WhatsApp").props.accessibilityLabel).toBe("WhatsApp, erro: Informe um número de WhatsApp válido com DDD.");

    // Digitar no campo tira o erro dele.
    fireEvent.changeText(campo("WhatsApp"), "11999998888");
    expect(screen.queryByText("Informe um número de WhatsApp válido com DDD.")).toBeNull();
  });

  it("T6 + T11 — register em voo: tudo desativado, a senha continua no campo, e dois toques no mesmo lote mandam UM register", async () => {
    const portao = segurar();
    rotas({ "/auth/register": async () => { await portao.promessa; return resposta(429, { detail: "Muitas tentativas." }); } });
    await abrir();
    preencher();

    await act(async () => {
      fireEvent.press(botao("Criar conta"));
      fireEvent.press(botao("Criar conta"));
      await respirar();
    });

    expect(registers()).toHaveLength(1);
    expect(botao("Criar conta").props.accessibilityState).toMatchObject({ busy: true });
    for (const rotulo of ["Nome", "E-mail", "WhatsApp", "Senha"]) {
      expect(campo(rotulo).props.accessibilityState).toMatchObject({ disabled: true });
    }
    expect(botao("Entrar").props.accessibilityState).toMatchObject({ disabled: true });
    expect(campo("Senha").props.value).toBe("s3nha-boa");

    await act(async () => {
      portao.soltar();
      await respirar();
    });
    expect(screen.getByText("Muitas tentativas.")).toBeTruthy();
    expect(campo("Senha").props.value).toBe("");
    expect(campo("Nome").props.value).toBe(" Ana ");
  });

  it("T7 — '123 456' colado não envia sozinho; 'Confirmar' + 6º dígito no mesmo lote mandam UM verify", async () => {
    await irAoCodigo();

    fireEvent.changeText(campo("Código de 6 dígitos"), "123 456");
    expect(verifies()).toHaveLength(0);
    expect(campo("Código de 6 dígitos").props.value).toBe("123456");

    await act(async () => {
      fireEvent.press(botao("Confirmar"));
      fireEvent.changeText(campo("Código de 6 dígitos"), "123456");
      await respirar();
    });

    await waitFor(() => expect(screen.getByText(/Olá, Ana/)).toBeTruthy());
    expect(verifies().map((c) => c.corpo.code)).toEqual(["123456"]);
  });

  it("T8 — código errado: aviso, campo vazio e foco devolvido; digitar tira o aviso; o certo autentica", async () => {
    const foco = jest.mocked(TextInput.prototype.focus);
    await irAoCodigo();
    foco.mockClear();

    await act(async () => {
      fireEvent.changeText(campo("Código de 6 dígitos"), "000000");
      await respirar();
    });
    expect(screen.getByText("Código inválido ou expirado.")).toBeTruthy();
    expect(campo("Código de 6 dígitos").props.value).toBe("");
    expect(foco).toHaveBeenCalled();

    fireEvent.changeText(campo("Código de 6 dígitos"), "1");
    expect(screen.queryByText("Código inválido ou expirado.")).toBeNull();

    await act(async () => {
      fireEvent.changeText(campo("Código de 6 dígitos"), "123456");
      await respirar();
    });
    await waitFor(() => expect(screen.getByText(/Olá, Ana/)).toBeTruthy());
  });

  it("T9 — reenviar manda o corpo idêntico, com telefone; 429 no reenvio fica no código", async () => {
    await irAoCodigo();

    await act(async () => {
      fireEvent.press(botao("Reenviar código"));
      await respirar();
    });
    expect(screen.getByText("Enviamos um novo código.")).toBeTruthy();
    const [primeiro, segundo] = registers();
    expect(segundo?.corpo).toEqual(primeiro?.corpo);
    expect(segundo?.corpo).toEqual(CORPO);

    rotas({ "/auth/register": () => resposta(429, { detail: "Muitas tentativas." }) });
    await act(async () => {
      fireEvent.press(botao("Reenviar código"));
      await respirar();
    });
    expect(screen.getByText("Muitas tentativas.")).toBeTruthy();
    expect(campo("Código de 6 dígitos")).toBeTruthy();
  });

  it("T10 — 'Voltar' do código: nome, e-mail e WhatsApp continuam; a senha, não", async () => {
    await irAoCodigo();
    fireEvent.press(botao("Voltar"));

    expect(campo("Nome").props.value).toBe(" Ana ");
    expect(campo("E-mail").props.value).toBe("ana@x.com");
    expect(campo("WhatsApp").props.value).toBe("(11) 99999-8888");
    expect(campo("Senha").props.value).toBe("");
  });

  it("T12 — dicas de preenchimento e a ordem Nome, E-mail, WhatsApp, Senha", async () => {
    await abrir();

    expect(campo("Nome").props).toMatchObject({ autoComplete: "name", textContentType: "name", autoCapitalize: "words", maxLength: 50 });
    expect(campo("E-mail").props).toMatchObject({ autoComplete: "email", textContentType: "username", keyboardType: "email-address", autoCapitalize: "none" });
    expect(campo("WhatsApp").props).toMatchObject({
      keyboardType: "phone-pad",
      textContentType: "telephoneNumber",
      autoComplete: "tel",
      placeholder: "(11) 99999-9999",
      accessibilityHint: "Use o mesmo número com que você vai falar com a Piggy.",
    });
    expect(campo("Senha").props).toMatchObject({
      secureTextEntry: true,
      autoComplete: "new-password",
      textContentType: "newPassword",
      accessibilityHint: "Pelo menos 8 caracteres.",
    });
    // Os dois primeiros campos da árvore são os de /entrar, montada embaixo na pilha.
    const ordem = screen.UNSAFE_getAllByType(TextInput).map((c) => c.props.accessibilityLabel as string);
    expect(ordem).toEqual(["E-mail", "Senha", "Nome", "E-mail", "WhatsApp", "Senha"]);
  });

  it("T13 — FalhaNoCofre: mensagem, sem formulário nem código, e 'Ir para Entrar'", async () => {
    jest.spyOn(authService, "confirmarCadastro").mockRejectedValueOnce(new FalhaNoCofre(new Error("keychain recusou")));
    await irAoCodigo();

    await act(async () => {
      fireEvent.changeText(campo("Código de 6 dígitos"), "123456");
      await respirar();
    });
    expect(screen.getByText(/Sua conta foi criada, mas não conseguimos abrir a sessão/)).toBeTruthy();
    expect(screen.queryByLabelText(/^Código de 6 dígitos/)).toBeNull();
    expect(screen.queryByLabelText(/^Senha/)).toBeNull();

    await act(async () => {
      fireEvent.press(botao("Ir para Entrar"));
      await respirar();
    });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
  });

  // Sem dublê do serviço: o verify responde 200 (conta criada, código gasto) e
  // o cofre REAL recusa gravar. Controle negativo (medido): sem o `try` da
  // gravação inicial em `guardarCredenciaisSe`, a tela fica no código com o
  // aviso genérico e este teste fica vermelho. O positivo é o T4.
  it("T13b — cofre recusa gravar depois do verify 200: erro-cofre com 'Ir para Entrar'", async () => {
    await irAoCodigo();
    falharEscrita(true);

    await act(async () => {
      fireEvent.changeText(campo("Código de 6 dígitos"), "123456");
      await respirar();
    });
    expect(verifies()).toHaveLength(1);
    expect(screen.getByText(/Sua conta foi criada, mas não conseguimos abrir a sessão/)).toBeTruthy();
    expect(screen.queryByLabelText(/^Código de 6 dígitos/)).toBeNull();
    expect(botao("Ir para Entrar")).toBeTruthy();
    await expect(lerCredenciais()).resolves.toBeNull();
  });

  // Controle negativo (medido): sem o `.catch` de `agir()` a tela fica presa
  // em "enviando" e este teste fica vermelho. `cadastrar`/`confirmar` capturam
  // tudo hoje; o dublê simula a rejeição que o código real não produz.
  it("T14 — ação que rejeita: volta à fase de antes com o aviso genérico, e a tela segue utilizável", async () => {
    jest.spyOn(criarConta, "cadastrar").mockRejectedValueOnce(new Error("inesperado"));
    await abrir();
    preencher();
    await act(async () => {
      fireEvent.press(botao("Criar conta"));
      await respirar();
    });
    expect(screen.getByText(GENERICO)).toBeTruthy();
    expect(botao("Criar conta").props.accessibilityState).not.toMatchObject({ busy: true });

    fireEvent.changeText(campo("Senha"), "s3nha-boa");
    await act(async () => {
      fireEvent.press(botao("Criar conta"));
      await respirar();
    });
    await waitFor(() => campo("Código de 6 dígitos"));

    jest.spyOn(criarConta, "confirmar").mockRejectedValueOnce(new Error("inesperado"));
    await act(async () => {
      fireEvent.changeText(campo("Código de 6 dígitos"), "123456");
      await respirar();
    });
    expect(campo("Código de 6 dígitos").props.accessibilityLabel).toBe(`Código de 6 dígitos, erro: ${GENERICO}`);

    await act(async () => {
      fireEvent.changeText(campo("Código de 6 dígitos"), "123456");
      await respirar();
    });
    await waitFor(() => expect(screen.getByText(/Olá, Ana/)).toBeTruthy());
  });

  // Controle negativo (medido): com a falha engolida de novo, o aviso não aparece.
  it("T15 — Termos abre o site; se o openURL falhar, aparece o aviso", async () => {
    const abrirUrl = jest.mocked(Linking.openURL);
    await abrir();

    await act(async () => {
      fireEvent.press(screen.getByText("Termos de Uso"));
      await respirar();
    });
    expect(abrirUrl).toHaveBeenLastCalledWith("http://backend.teste/termos");
    expect(screen.queryByText(/Não conseguimos abrir a página/)).toBeNull();

    abrirUrl.mockRejectedValueOnce(new Error("sem navegador"));
    await act(async () => {
      fireEvent.press(screen.getByText("Política de Privacidade"));
      await respirar();
    });
    expect(abrirUrl).toHaveBeenLastCalledWith("http://backend.teste/privacy");
    expect(screen.getByText("Não conseguimos abrir a página. Tente de novo em instantes.")).toBeTruthy();
  });

  // Controle negativo (medido): sem limpar `avisoLink` em `aplicar`/`digitar`,
  // o aviso volta depois do "Voltar" e continua depois de digitar. O positivo é o T15.
  it("T16 — o aviso do link some ao mudar de fase e ao digitar", async () => {
    const abrirUrl = jest.mocked(Linking.openURL);
    const falharTermos = async () => {
      abrirUrl.mockRejectedValueOnce(new Error("sem navegador"));
      await act(async () => {
        fireEvent.press(screen.getByText("Termos de Uso"));
        await respirar();
      });
      expect(screen.getByText(/Não conseguimos abrir a página/)).toBeTruthy();
    };
    await abrir();
    preencher();
    await falharTermos();

    await act(async () => {
      fireEvent.press(botao("Criar conta"));
      await respirar();
    });
    await waitFor(() => campo("Código de 6 dígitos"));
    fireEvent.press(botao("Voltar"));
    expect(campo("Nome")).toBeTruthy();
    expect(screen.queryByText(/Não conseguimos abrir a página/)).toBeNull();

    await falharTermos();
    fireEvent.changeText(campo("Senha"), "s3nha-boa");
    expect(screen.queryByText(/Não conseguimos abrir a página/)).toBeNull();
  });
});
