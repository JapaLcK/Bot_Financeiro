// Mesmo motivo de `__tests__/ui/layout.test.tsx`: sem este dublê,
// `useColorScheme()` chega `undefined` no `_layout.tsx` real e derruba o
// render com `renderRouter`.
const estadoDoEsquema: { valor: "light" | "dark" } = { valor: "light" };
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => estadoDoEsquema.valor,
}));

import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { router } from "expo-router";

import * as authService from "@/services/auth";
import { FalhaNoCofre, lerCredenciais } from "@/storage/secure";

import { chamadas, credencialDe, prepararCaso, resposta, rotear, segurar } from "./auth_apoio";

/**
 * Drena a fila de microtarefas SEM `setTimeout(0)`: dentro de `act()` com
 * `renderRouter`, um `setTimeout` real trava (nota da tarefa). 20 voltas de
 * `Promise.resolve()` bastam para qualquer cadeia de `await` deste arquivo.
 */
const respirar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

const MFA_ANA = { mfa_required: true, mfa_challenge: "ch-1", email: "ana@x.com" };

/**
 * A tela `/entrar` de verdade, via `renderRouter("./app", ...)` — mesma
 * receita de `layout.test.tsx`: prova o que só aparece montando a árvore
 * real (busy visível, bloqueio da fase X, deep link).
 */
describe("(auth)/entrar — tela real", () => {
  beforeEach(() => {
    prepararCaso();
    rotear();
  });

  it("B1 — enquanto o login não responde: e-mail, senha e 'Esqueci a senha' desativados; Entrar em carregando", async () => {
    const portao = segurar();
    rotear({ "/auth/login": async () => { await portao.promessa; return resposta(200, { user_id: 1, email: "a@x.com", access_token: "x", refresh_token: "y", dashboard_token: "d", expires_in: 900 }); } });
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));

    fireEvent.changeText(screen.getByLabelText("E-mail"), "a@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    fireEvent.press(screen.getByRole("button", { name: "Entrar" }));

    // Ainda sem resposta (o portão não foi solto).
    expect(screen.getByLabelText("E-mail").props.accessibilityState).toMatchObject({ disabled: true });
    expect(screen.getByRole("button", { name: "Entrar" }).props.accessibilityState).toMatchObject({ busy: true });
    expect(screen.getByRole("button", { name: "Esqueci a senha" }).props.accessibilityState).toMatchObject({ disabled: true });

    await act(async () => {
      portao.soltar();
      await Promise.resolve();
    });
  });

  it("I3 — FalhaNoCofre mostra a fase X, BLOQUEANTE: sem formulário, sem Esqueci a senha, sem Google/Apple", async () => {
    jest.spyOn(authService, "entrar").mockRejectedValueOnce(new FalhaNoCofre(new Error("keychain recusou")));
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));

    fireEvent.changeText(screen.getByLabelText("E-mail"), "a@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Entrar" }));
      await Promise.resolve();
    });

    expect(screen.getByText("Não conseguimos abrir sua sessão neste aparelho. Tente de novo.")).toBeTruthy();
    // Bloqueante: nenhuma das saídas do formulário comum continua na árvore.
    expect(screen.queryByLabelText("E-mail")).toBeNull();
    expect(screen.queryByLabelText("Senha")).toBeNull();
    expect(screen.queryByRole("button", { name: "Esqueci a senha" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Continuar com Google" })).toBeNull();

    // "Tentar de novo" volta ao formulário — o e-mail digitado continua (é
    // estado da TELA, não da máquina X/F), mas nenhuma operação é refeita
    // sozinha: só o formulário reaparece, pronto para um novo toque.
    fireEvent.press(screen.getByRole("button", { name: "Tentar de novo" }));
    expect(screen.getByLabelText("E-mail")).toBeTruthy();
    expect(screen.getByLabelText("E-mail").props.value).toBe("a@x.com");
  });

  it("M3 — botões sociais desativados carregam accessibilityHint 'Em breve'", async () => {
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));

    expect(screen.getByRole("button", { name: "Continuar com Google" }).props.accessibilityHint).toBe("Em breve");
    expect(screen.getByRole("button", { name: "Continuar com Apple" }).props.accessibilityHint).toBe("Em breve");
  });

  it("M1 — deep link frio para /esqueci-senha tem /entrar embaixo na pilha (canGoBack)", async () => {
    renderRouter("./app", { initialUrl: "/esqueci-senha" });
    await waitFor(() => expect(screen).toHavePathname("/esqueci-senha"));

    expect(router.canGoBack()).toBe(true);
  });

  it("código errado e depois o certo autentica sem pedir a senha de novo (400 mfa_code_invalid mantém o desafio)", async () => {
    rotear({
      "/auth/login": () => resposta(200, MFA_ANA),
      "/auth/mfa/verify-login": (o) =>
        (JSON.parse(String(o.body)) as { code: string }).code === "123456"
          ? resposta(200, credencialDe("ana@x.com"))
          : resposta(400, { detail: "Código inválido.", code: "mfa_code_invalid" }),
    });
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));

    fireEvent.changeText(screen.getByLabelText("E-mail"), "ana@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Entrar" }));
      await respirar();
    });
    await waitFor(() => screen.getByLabelText("Código de 6 dígitos"));

    await act(async () => {
      fireEvent.changeText(screen.getByLabelText("Código de 6 dígitos"), "000000");
      await respirar();
    });
    // Regex: com erro o rótulo vira "<rótulo>, erro: <aviso>" (Input.tsx).
    expect(screen.getByLabelText(/^Código de 6 dígitos/)).toBeTruthy();
    expect(screen.queryByLabelText(/^Senha/)).toBeNull();
    expect(screen.getByText("Código inválido.")).toBeTruthy();

    await act(async () => {
      fireEvent.changeText(screen.getByLabelText(/^Código de 6 dígitos/), "123456");
      await respirar();
    });

    await waitFor(() => expect(screen.getByText(/Olá, Ana/)).toBeTruthy());
    expect(chamadas().filter((c) => c.caminho === "/auth/login")).toHaveLength(1);
    const verifies = chamadas().filter((c) => c.caminho === "/auth/mfa/verify-login");
    expect(verifies.map((c) => c.corpo.challenge)).toEqual(["ch-1", "ch-1"]);
  });

  it("I-A — verify em voo + Voltar (mesmo lote) + login de OUTRA conta: a fila libera e a conta nova autentica, sem busy pendurado", async () => {
    const portao = segurar();
    rotear({
      "/auth/login": (o) => {
        const { email } = JSON.parse(String(o.body)) as { email: string };
        return email === "ana@x.com" ? resposta(200, MFA_ANA) : resposta(200, credencialDe(email));
      },
      "/auth/mfa/verify-login": async () => {
        await portao.promessa;
        return resposta(429, { detail: "Muitas tentativas." });
      },
    });
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));

    fireEvent.changeText(screen.getByLabelText("E-mail"), "ana@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Entrar" }));
      await respirar();
    });
    await waitFor(() => screen.getByLabelText("Código de 6 dígitos"));

    // 6º dígito e "Voltar" no MESMO lote — Voltar ainda com props antigas.
    await act(async () => {
      fireEvent.changeText(screen.getByLabelText("Código de 6 dígitos"), "123456");
      fireEvent.press(screen.getByRole("button", { name: "Voltar" }));
      await respirar();
    });
    expect(screen.getByLabelText("Senha")).toBeTruthy();

    fireEvent.changeText(screen.getByLabelText("E-mail"), "bia@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Entrar" }));
      await respirar();
    });
    portao.soltar();
    await act(respirar);

    // A conta nova (Bia) mandou o login de verdade — não ficou engolida — e
    // autenticou (login sem MFA): sem busy pendurado, a sessão avança e o
    // app navega para a tela autenticada.
    expect(chamadas().filter((c) => c.caminho === "/auth/login" && c.corpo.email === "bia@x.com")).toHaveLength(1);
    await waitFor(() => expect(screen.getByText(/Olá, Bia/)).toBeTruthy());
  });

  it("I-B — mesmo cenário, mas o verify antigo (Ana) chega 200 DEPOIS do Voltar: a pessoa autentica como Bia, nunca como Ana", async () => {
    const portao = segurar();
    rotear({
      "/auth/login": (o) => {
        const { email } = JSON.parse(String(o.body)) as { email: string };
        return email === "ana@x.com" ? resposta(200, MFA_ANA) : resposta(200, credencialDe(email));
      },
      "/auth/mfa/verify-login": async () => {
        await portao.promessa;
        return resposta(200, credencialDe("ana@x.com"));
      },
    });
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));

    fireEvent.changeText(screen.getByLabelText("E-mail"), "ana@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Entrar" }));
      await respirar();
    });
    await waitFor(() => screen.getByLabelText("Código de 6 dígitos"));

    await act(async () => {
      fireEvent.changeText(screen.getByLabelText("Código de 6 dígitos"), "123456");
      fireEvent.press(screen.getByRole("button", { name: "Voltar" }));
      await respirar();
    });

    fireEvent.changeText(screen.getByLabelText("E-mail"), "bia@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Entrar" }));
      await respirar();
    });
    // SÓ AGORA o verify antigo (abandonado) da Ana responde 200.
    portao.soltar();
    await act(respirar);
    await act(respirar);

    // O cofre nunca termina com a Ana — mesmo com um 200 chegando depois.
    await expect(lerCredenciais()).resolves.toEqual({ access: "access-bia", refresh: "rt_bia" });
    expect(screen.queryByText(/Olá, Ana/)).toBeNull();
    await waitFor(() => expect(screen.getByText(/Olá, Bia/)).toBeTruthy());
  });

  it("I-B (parado no formulário) — 200 atrasado do verify abandonado NÃO autentica sozinho", async () => {
    const portao = segurar();
    rotear({
      "/auth/login": () => resposta(200, MFA_ANA),
      "/auth/mfa/verify-login": async () => {
        await portao.promessa;
        return resposta(200, credencialDe("ana@x.com"));
      },
    });
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));

    fireEvent.changeText(screen.getByLabelText("E-mail"), "ana@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await act(async () => {
      fireEvent.press(screen.getByRole("button", { name: "Entrar" }));
      await respirar();
    });
    await waitFor(() => screen.getByLabelText("Código de 6 dígitos"));

    await act(async () => {
      fireEvent.changeText(screen.getByLabelText("Código de 6 dígitos"), "123456");
      fireEvent.press(screen.getByRole("button", { name: "Voltar" }));
      await respirar();
    });
    // Parada no formulário — nenhum login novo.
    portao.soltar();
    await act(respirar);
    await act(respirar);

    await expect(lerCredenciais()).resolves.toBeNull();
    expect(screen).toHavePathname("/entrar");
  });
});

// Apontamento do Codex no #494: a tela provisória da Fase 1 passava
// `autoComplete`, e sem ele o Android pode não oferecer a senha salva.
describe("(auth)/entrar — dicas de preenchimento automático", () => {
  beforeEach(() => {
    prepararCaso();
    rotear();
  });

  it("e-mail e senha levam autoComplete e textContentType", async () => {
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));

    expect(screen.getByLabelText("E-mail").props).toMatchObject({ autoComplete: "email", textContentType: "username" });
    expect(screen.getByLabelText("Senha").props).toMatchObject({ autoComplete: "current-password", textContentType: "password" });
  });
});
