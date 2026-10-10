/**
 * Segurança para conta sem senha (Google/Apple, `has_password: false`): em vez de
 * "Ativar" (que daria 409 no setup), o bloco "Definir senha" manda o link de
 * `/auth/forgot-password` para o e-mail da própria conta. SÓ `false` mostra o
 * bloco; `true` e AUSENTE (backend velho, /auth/me fora) seguem com "Ativar".
 */
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { guardarCredenciais } from "@/storage/secure";

import { chamadas, prepararCaso, resposta, rotear, S, segurar, type Rota } from "./auth_apoio";

const MFA_DESLIGADO = { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 };
const NEUTRA = "Se o e-mail estiver cadastrado, enviamos um link para redefinir a senha.";

const drenar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

/** `/auth/me` com `has_password` controlado; `undefined` = o campo nem vem. */
function abrir(hasPassword: { valor: boolean | undefined; email?: string }, extra: Record<string, Rota> = {}) {
  rotear({
    "/auth/mfa/status": () => resposta(200, MFA_DESLIGADO),
    "/auth/forgot-password": () => resposta(200, { message: "texto do servidor" }),
    "/auth/me": () =>
      resposta(200, { user_id: 1, email: hasPassword.email ?? "s@x.com", display_name: "S", app_access: true, ...(hasPassword.valor === undefined ? {} : { has_password: hasPassword.valor }) }),
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
const appVai = (v: "active" | "inactive" | "background") =>
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

  it("A1 — resposta VELHA (has_password false) chegando depois da nova (true) não repinta o bloco", async () => {
    const conta = { valor: false as boolean | undefined };
    const lenta = segurar();
    let cargas = 0; // 0 = carga do foco inicial, 1 = a lenta, 2 = a rápida
    await abrir(conta, {
      "/auth/me": async () => {
        const vez = cargas;
        if (vez === 1) await lenta.promessa;
        return resposta(200, { user_id: 1, email: "s@x.com", display_name: "S", app_access: true, has_password: vez === 1 ? false : conta.valor });
      },
    });
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());

    cargas = 1;
    await appVai("background");
    await appVai("active"); // carga 1: sai com a senha ainda indefinida e fica presa
    cargas = 2;
    conta.valor = true; // a senha foi definida no navegador
    await appVai("background");
    await appVai("active"); // carga 2: já vê a senha
    await waitFor(() => expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy());

    lenta.soltar(); // a resposta velha chega por último
    await act(drenar);

    expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy();
    expect(screen.queryByText("Definir senha")).toBeNull();
  });

  it("A2 — recarga que FALHA com dados na tela mantém 'Verifique seu e-mail' e não reabre o envio", async () => {
    let fora = false;
    await abrir({ valor: false }, { "/auth/mfa/status": () => (fora ? resposta(500, { detail: "x" }) : resposta(200, MFA_DESLIGADO)) });
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());
    await tocar("Enviar link");
    expect(screen.getByText("Verifique seu e-mail")).toBeTruthy();

    fora = true; // a rede ainda está acordando
    await appVai("background");
    await appVai("active");

    expect(chamadas().filter((c) => c.caminho === "/auth/mfa/status").length).toBeGreaterThanOrEqual(2); // a recarga rodou
    expect(screen.getByText("Verifique seu e-mail")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Tentar de novo" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Enviar link" })).toBeNull();
    expect(forgot()).toHaveLength(1);
  });

  it("A2 — a carga INICIAL que falha ainda mostra o erro com Tentar de novo", async () => {
    await abrir({ valor: false }, { "/auth/mfa/status": () => resposta(500, { detail: "x" }) });
    await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy());
  });

  it("M3 — ir para background (e o inactive da volta) NÃO recarrega; só o active depois do background", async () => {
    await abrir({ valor: false });
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());
    const me = () => chamadas().filter((c) => c.caminho === "/auth/me").length;
    const base = me();

    await appVai("inactive"); // Central de Controle: não saiu do app
    await appVai("active");
    expect(me()).toBe(base);

    await appVai("background");
    expect(me()).toBe(base);
    await appVai("inactive"); // a volta do iOS passa por aqui, com a rede ainda acordando
    expect(me()).toBe(base);
    await appVai("active");
    expect(me()).toBe(base + 1);
  });

  it("M8 — has_password false com MFA já ativo: sem bloco Definir senha", async () => {
    await abrir({ valor: false }, { "/auth/mfa/status": () => resposta(200, { enabled: true, has_pending_secret: false, backup_codes_remaining: 7 }) });
    await waitFor(() => expect(screen.getByText("Ativa · 7 de 10 códigos de backup")).toBeTruthy());
    expect(screen.queryByText("Definir senha")).toBeNull();
  });

  it("A4 — e-mail só com espaços cai no mesmo texto do e-mail vazio, sem botão nem Ativar", async () => {
    await abrir({ valor: false, email: "   " });
    await waitFor(() => expect(screen.getByText(/Defina uma senha pelo site/)).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Enviar link" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Ativar" })).toBeNull();
  });

  it("A3 — antes do envio o texto está no futuro; 'Enviamos' só depois", async () => {
    await abrir({ valor: false });
    await waitFor(() => expect(screen.getByText(/Vamos enviar um link para s@x\.com/)).toBeTruthy());
    expect(screen.queryByText(/Enviamos um link para/)).toBeNull();
  });

  it("X3 — background, active, active: UMA recarga só (a marca de 'foi para o fundo' zera depois de usada)", async () => {
    await abrir({ valor: false });
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());
    const me = () => chamadas().filter((c) => c.caminho === "/auth/me").length;
    const base = me();

    await appVai("background");
    await appVai("active");
    await appVai("active");
    await appVai("inactive");
    await appVai("active");

    expect(me()).toBe(base + 1);
  });

  it("X1 — sessão expirada numa recarga SILENCIOSA (tela em pronto) ainda leva a /entrar com o aviso", async () => {
    let morta = false;
    await abrir(
      { valor: false },
      {
        "/auth/mfa/status": () => (morta ? resposta(401, { detail: "expirado" }) : resposta(200, MFA_DESLIGADO)),
        "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
      },
    );
    await waitFor(() => expect(screen.getByText("Definir senha")).toBeTruthy());

    morta = true; // revogada em outro aparelho enquanto o app estava no fundo
    await appVai("background");
    await appVai("active");

    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
  });

  // Comportamento registrado, NÃO é defeito a corrigir: a regra "recarga que falha mantém a tela"
  // vale também para o foco de navegação. Se o MFA mudou e a recarga falha por rede, a tela fica com
  // o status velho e sem aviso; a autoridade é o servidor (/auth/mfa/setup responde 409).
});
