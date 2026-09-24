/**
 * A Segurança pelo roteador de verdade (`renderRouter("./app")`): a guarda de
 * sessão, o caminho a partir do Início, o recarregamento no foco depois das
 * sheets, e a sheet presa desde o envio do enable/regenerate até "Já guardei".
 *
 * Nada de `setTimeout(0)` dentro de `act` aqui (ver o comentário de
 * `layout.test.tsx`): microtarefas drenadas à mão, e `waitFor` para o resto.
 */
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { Alert, type AlertButton } from "react-native";

import { guardarCredenciais } from "@/storage/secure";

import { prepararCaso, resposta, rotear, S, type Rota } from "../features/auth_apoio";

/** base64 de `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><path d="M0 0h1v1H0z"/></svg>`. */
const SVG_BASE64 =
  "PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA0IDQiPjxwYXRoIGQ9Ik0wIDBoMXYxSDB6Ii8+PC9zdmc+";
const SETUP = {
  secret: "JBSWY3DPEHPK3PXP",
  uri: "otpauth://totp/PigBank:s%40x.com?secret=JBSWY3DPEHPK3PXP&issuer=PigBank",
  qr_code: `data:image/svg+xml;base64,${SVG_BASE64}`,
};
const CODIGOS = Array.from({ length: 10 }, (_, i) => `ABCDE-FGH${String(i).padStart(2, "0")}`);

const drenar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

/** Servidor de MFA com estado: ativar liga, desativar desliga. */
function servidorMfa(inicial: { ligado: boolean; restantes?: number }, extra: Record<string, Rota> = {}) {
  const mfa = { ligado: inicial.ligado, restantes: inicial.restantes ?? 10 };
  rotear({
    "/auth/mfa/status": () =>
      resposta(200, { enabled: mfa.ligado, has_pending_secret: false, backup_codes_remaining: mfa.ligado ? mfa.restantes : 0 }),
    "/auth/mfa/setup": () => resposta(200, SETUP),
    "/auth/mfa/enable": () => {
      mfa.ligado = true;
      mfa.restantes = 10;
      return resposta(200, { ok: true, backup_codes: CODIGOS });
    },
    "/auth/mfa/disable": () => {
      mfa.ligado = false;
      return resposta(200, { ok: true });
    },
    ...extra,
  });
}

/** Resposta segurada até o teste soltar: a requisição fica "em voo". */
function emVoo(r: Response) {
  let soltar: () => void = () => undefined;
  const rota = () => new Promise<Response>((ok) => (soltar = () => ok(r)));
  return { rota, soltar: () => soltar() };
}

async function voltar() {
  await act(async () => {
    router.back();
    await drenar();
  });
}

/** Da Segurança até o campo do código no passo do QR. */
async function irAteOQr() {
  await tocar("Ativar");
  await waitFor(() => expect(screen).toHavePathname("/mfa-ativar"));
  fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
  await tocar("Continuar");
  await waitFor(() => expect(screen.getByText(SETUP.secret)).toBeTruthy());
}

async function digitarTotp() {
  await act(async () => {
    fireEvent.changeText(screen.getByLabelText("Código de 6 dígitos"), "123456");
    await drenar();
  });
}

/** Da Segurança (MFA ligado) até o envio do regenerate. */
async function enviarNovosCodigos() {
  await tocar("Gerar novos códigos");
  await waitFor(() => expect(screen).toHavePathname("/mfa-novos-codigos"));
  fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
  fireEvent.changeText(screen.getByLabelText("Código de 6 dígitos"), "123456");
  await tocar("Gerar novos códigos");
}

async function tocar(rotulo: string) {
  await act(async () => {
    fireEvent.press(screen.getByRole("button", { name: rotulo }));
    await drenar();
  });
}

beforeEach(() => {
  prepararCaso();
  jest.restoreAllMocks();
});

describe("Segurança — rotas", () => {
  it("sem sessão, /seguranca cai em /entrar", async () => {
    servidorMfa({ ligado: false });
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
  });

  it("com sessão, o botão Segurança do Início leva a /seguranca, que mostra Ativar", async () => {
    await guardarCredenciais(S);
    servidorMfa({ ligado: false });
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen.getByText("Olá, S")).toBeTruthy());

    await tocar("Segurança");

    await waitFor(() => expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy());
    expect(screen).toHavePathname("/seguranca");
  });

  it("deep link frio em /seguranca: voltar leva ao Início", async () => {
    await guardarCredenciais(S);
    servidorMfa({ ligado: true, restantes: 7 });
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(screen.getByText("Ativa · 7 de 10 códigos de backup")).toBeTruthy());

    await act(async () => {
      router.back();
      await drenar();
    });

    await waitFor(() => expect(screen).toHavePathname("/"));
  });

  it("status com erro mostra Tentar de novo, que recarrega", async () => {
    await guardarCredenciais(S);
    let vez = 0;
    servidorMfa({ ligado: false }, {
      "/auth/mfa/status": () =>
        ++vez === 1 ? resposta(500, {}) : resposta(200, { enabled: false, has_pending_secret: false, backup_codes_remaining: 0 }),
    });
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeTruthy());

    await tocar("Tentar de novo");

    await waitFor(() => expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy());
  });

  it("status com a sessão encerrada (401 + refresh 401) manda para /entrar com o aviso", async () => {
    await guardarCredenciais(S);
    servidorMfa({ ligado: false }, {
      "/auth/mfa/status": () => resposta(401, { detail: "expirado" }),
      "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
    });
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
  });

  it("com os códigos na tela, voltar NÃO fecha a sheet; \"Já guardei\" fecha e a Segurança mostra Ativa", async () => {
    await guardarCredenciais(S);
    servidorMfa({ ligado: false });
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy());

    await tocar("Ativar");
    await waitFor(() => expect(screen).toHavePathname("/mfa-ativar"));
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await tocar("Continuar");
    await waitFor(() => expect(screen.getByText(SETUP.secret)).toBeTruthy());
    await act(async () => {
      fireEvent.changeText(screen.getByLabelText("Código de 6 dígitos"), "123456");
      await drenar();
    });
    await waitFor(() => expect(screen.getByText(CODIGOS.join("\n"))).toBeTruthy());

    await act(async () => {
      router.back();
      await drenar();
    });
    expect(screen).toHavePathname("/mfa-ativar");
    expect(screen.getByText(CODIGOS.join("\n"))).toBeTruthy();

    await tocar("Já guardei meus códigos");
    await waitFor(() => expect(screen).toHavePathname("/seguranca"));
    await waitFor(() => expect(screen.getByText("Ativa · 10 de 10 códigos de backup")).toBeTruthy());
  });

  it("desativar com sucesso volta à Segurança, que recarrega e mostra Ativar de novo", async () => {
    await guardarCredenciais(S);
    servidorMfa({ ligado: true, restantes: 9 });
    jest.spyOn(Alert, "alert").mockImplementation((_t, _m, botoes?: AlertButton[]) => {
      botoes?.find((b) => b.text === "Desativar")?.onPress?.();
    });
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(screen.getByText("Ativa · 9 de 10 códigos de backup")).toBeTruthy());

    await tocar("Desativar");
    await waitFor(() => expect(screen).toHavePathname("/mfa-desativar"));
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    fireEvent.changeText(screen.getByLabelText("Código do app ou de backup"), "123456");
    await tocar("Desativar");

    await waitFor(() => expect(screen).toHavePathname("/seguranca"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy());
  });

  it("sheet fechada com o disable em voo: a resposta que chega depois NÃO tira da Segurança", async () => {
    await guardarCredenciais(S);
    let soltarResposta: () => void = () => undefined;
    servidorMfa({ ligado: true, restantes: 9 }, {
      "/auth/mfa/disable": () => new Promise((r) => (soltarResposta = () => r(resposta(200, { ok: true })))),
    });
    jest.spyOn(Alert, "alert").mockImplementation((_t, _m, botoes?: AlertButton[]) => {
      botoes?.find((b) => b.text === "Desativar")?.onPress?.();
    });
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen.getByText("Olá, S")).toBeTruthy());
    await tocar("Segurança");
    await waitFor(() => expect(screen.getByText("Ativa · 9 de 10 códigos de backup")).toBeTruthy());

    await tocar("Desativar");
    await waitFor(() => expect(screen).toHavePathname("/mfa-desativar"));
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    fireEvent.changeText(screen.getByLabelText("Código do app ou de backup"), "123456");
    await tocar("Desativar");

    await act(async () => {
      router.back();
      await drenar();
    });
    await waitFor(() => expect(screen).toHavePathname("/seguranca"));

    await act(async () => {
      soltarResposta();
      await drenar();
    });
    expect(screen).toHavePathname("/seguranca");
  });

  it("enable em voo: voltar NÃO fecha a sheet, e os códigos aparecem quando a resposta chega", async () => {
    await guardarCredenciais(S);
    const enable = emVoo(resposta(200, { ok: true, backup_codes: CODIGOS }));
    servidorMfa({ ligado: false }, { "/auth/mfa/enable": enable.rota });
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy());
    await irAteOQr();
    await digitarTotp();

    await voltar();
    expect(screen).toHavePathname("/mfa-ativar");

    await act(async () => {
      enable.soltar();
      await drenar();
    });
    await waitFor(() => expect(screen.getByText(CODIGOS.join("\n"))).toBeTruthy());
    expect(screen).toHavePathname("/mfa-ativar");
  });

  it("regenerate em voo: voltar NÃO fecha a sheet, e os códigos aparecem quando a resposta chega", async () => {
    await guardarCredenciais(S);
    const regenerar = emVoo(resposta(200, { backup_codes: CODIGOS }));
    servidorMfa({ ligado: true, restantes: 2 }, { "/auth/mfa/regenerate-backup-codes": regenerar.rota });
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(screen.getByText("Ativa · 2 de 10 códigos de backup")).toBeTruthy());
    await enviarNovosCodigos();

    await voltar();
    expect(screen).toHavePathname("/mfa-novos-codigos");

    await act(async () => {
      regenerar.soltar();
      await drenar();
    });
    await waitFor(() => expect(screen.getByText(CODIGOS.join("\n"))).toBeTruthy());
    expect(screen).toHavePathname("/mfa-novos-codigos");
  });

  it("enable recusado (400): a trava solta, o erro aparece e voltar fecha a sheet", async () => {
    await guardarCredenciais(S);
    servidorMfa({ ligado: false }, {
      "/auth/mfa/enable": () => resposta(400, { detail: "Código inválido. Tente novamente." }),
    });
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy());
    await irAteOQr();
    await digitarTotp();
    await waitFor(() => expect(screen.getByText("Código inválido. Tente novamente.")).toBeTruthy());

    await voltar();
    await waitFor(() => expect(screen).toHavePathname("/seguranca"));
  });

  it("regenerate recusado (400): a trava solta, o erro aparece e voltar fecha a sheet", async () => {
    await guardarCredenciais(S);
    servidorMfa({ ligado: true, restantes: 2 }, {
      "/auth/mfa/regenerate-backup-codes": () => resposta(400, { detail: "Código inválido." }),
    });
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(screen.getByText("Ativa · 2 de 10 códigos de backup")).toBeTruthy());
    await enviarNovosCodigos();
    await waitFor(() => expect(screen.getByText("Código inválido.")).toBeTruthy());

    await voltar();
    await waitFor(() => expect(screen).toHavePathname("/seguranca"));
  });

  it("sessão encerrada durante o regenerate (401 com a marca + refresh 401): vai para /entrar, não fica preso", async () => {
    await guardarCredenciais(S);
    const MARCA = { "WWW-Authenticate": 'Bearer realm="pigbank", error="invalid_token"' };
    servidorMfa({ ligado: true, restantes: 2 }, {
      "/auth/mfa/regenerate-backup-codes": () =>
        ({ ...resposta(401, { detail: "Token inválido ou expirado." }), headers: new Headers(MARCA) }) as Response,
      "/auth/refresh": () => resposta(401, { detail: "invalid_refresh_token" }),
    });
    renderRouter("./app", { initialUrl: "/seguranca" });
    await waitFor(() => expect(screen.getByText("Ativa · 2 de 10 códigos de backup")).toBeTruthy());
    await enviarNovosCodigos();

    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
  });
});
