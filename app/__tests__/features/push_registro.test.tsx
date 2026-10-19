/**
 * Registro do aparelho e pedido de permissão pelo roteador de verdade (`_layout`
 * real). Grupo com os dois controles. NEGATIVOS: tirar o efeito `[registravel]`
 * do `Push.tsx` deixa vermelhos os casos de abertura e de login; voltar o
 * callback da volta do fundo ao antigo (registrar ali, com o `registravel` do
 * render anterior) deixa vermelho o caso do Face ID (registra antes dele).
 * POSITIVO: permissão negada não gera token nem POST.
 */
import * as LA from "expo-local-authentication";
import * as Notifications from "expo-notifications";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { guardarCredenciais } from "@/storage/secure";

import { chamadas, prepararCaso, rotear, S } from "./auth_apoio";
import { appVai } from "./seguranca_sem_senha_apoio";

declare const global: typeof globalThis & {
  __definirPush: (v: Record<string, unknown>) => void;
  __dispararAppState: (v: string) => void;
  __definirAppState: (v: string) => void;
};

const registros = () => chamadas().filter((c) => c.caminho === "/api/push/register");
const pedidos = () => jest.mocked(Notifications.requestPermissionsAsync).mock.calls.length;
const tokens = () => jest.mocked(Notifications.getDevicePushTokenAsync).mock.calls.length;
const drenar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

beforeEach(() => {
  prepararCaso();
  rotear({ "/api/push/register": () => ({ ok: true, status: 200, json: async () => ({ ok: true }) }) as Response });
  global.__definirAppState("active");
  global.__definirPush({ permissao: "granted", promptResponde: "granted", token: "apns-teste", ambiente: "development", resposta: null });
  jest.mocked(Notifications.getDevicePushTokenAsync).mockClear();
  jest.mocked(Notifications.requestPermissionsAsync).mockClear();
});

describe("registro", () => {
  it("abertura com sessão salva registra com o Bearer guardado e o ambiente certo", async () => {
    await guardarCredenciais(S);
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(registros()).toHaveLength(1));
    expect(registros()[0]).toEqual({
      caminho: "/api/push/register",
      auth: "Bearer access-s",
      corpo: { token: "apns-teste", platform: "ios", environment: "sandbox" },
    });
  });

  it("login depois da abertura anônima registra com o Bearer da conta que entrou", async () => {
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(screen.getByLabelText("E-mail")).toBeTruthy());
    expect(registros()).toHaveLength(0);

    fireEvent.changeText(screen.getByLabelText("E-mail"), "ana@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await act(async () => fireEvent.press(screen.getByRole("button", { name: "Entrar" })));

    await waitFor(() => expect(registros()).toHaveLength(1));
    expect(registros()[0]).toMatchObject({ auth: "Bearer access-ana", corpo: { token: "apns-teste" } });
  });

  it("volta do fundo registra de novo (idempotente no servidor)", async () => {
    await guardarCredenciais(S);
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(registros()).toHaveLength(1));
    for (const v of ["inactive", "background", "active"]) {
      await act(async () => {
        global.__dispararAppState(v);
        await drenar();
      });
    }
    await waitFor(() => expect(registros()).toHaveLength(2));
  });

  it("com a trava na frente a abertura não faz rede; liberou, registra uma vez", async () => {
    const pendentes: ((r: LA.LocalAuthenticationResult) => void)[] = [];
    jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.BIOMETRIC);
    jest.mocked(LA.authenticateAsync).mockImplementation(() => new Promise((r) => pendentes.push(r)));
    await guardarCredenciais(S);
    try {
      renderRouter("./app", { initialUrl: "/" });
      await waitFor(() => expect(pendentes).toHaveLength(1));
      await act(drenar);
      expect(chamadas()).toEqual([]);
      expect(tokens()).toBe(0);

      await act(async () => {
        pendentes[0]!({ success: true } as LA.LocalAuthenticationResult);
        await drenar();
      });
      await waitFor(() => expect(registros()).toHaveLength(1));
    } finally {
      jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
      jest.mocked(LA.authenticateAsync).mockResolvedValue({ success: true });
    }
  });

  it("com Face ID: voltar do fundo DEPOIS da carência não registra antes do Face ID; liberou, registra 1 vez", async () => {
    const pendentes: ((r: LA.LocalAuthenticationResult) => void)[] = [];
    jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.BIOMETRIC);
    jest.mocked(LA.authenticateAsync).mockImplementation(() => new Promise((r) => pendentes.push(r)));
    const liberar = (i: number) =>
      act(async () => {
        pendentes[i]!({ success: true } as LA.LocalAuthenticationResult);
        await drenar();
      });
    await guardarCredenciais(S);
    try {
      renderRouter("./app", { initialUrl: "/" });
      await waitFor(() => expect(pendentes).toHaveLength(1));
      await liberar(0);
      await waitFor(() => expect(registros()).toHaveLength(1));

      // POSITIVO: dentro da carência não há trava, e a volta registra uma vez.
      await appVai("inactive");
      await appVai("background");
      jest.setSystemTime(Date.now() + 30_000);
      await appVai("active");
      await waitFor(() => expect(registros()).toHaveLength(2));
      expect(pendentes).toHaveLength(1);

      // Depois da carência a trava volta: zero registro (e nenhum /auth/refresh) até o Face ID.
      await appVai("inactive");
      await appVai("background");
      jest.setSystemTime(Date.now() + 61_000);
      await appVai("active");
      await waitFor(() => expect(pendentes).toHaveLength(2));
      await act(drenar);
      expect(registros()).toHaveLength(2);

      await liberar(1);
      await waitFor(() => expect(registros()).toHaveLength(3));
      await act(drenar);
      expect(registros()).toHaveLength(3);
    } finally {
      jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
      jest.mocked(LA.authenticateAsync).mockResolvedValue({ success: true });
    }
  });

  it("POSITIVO: permissão negada — nem token nem POST, na abertura e na volta do fundo", async () => {
    global.__definirPush({ permissao: "denied" });
    await guardarCredenciais(S);
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen.getByText("Bom dia, S")).toBeTruthy());
    for (const v of ["inactive", "background", "active"]) {
      await act(async () => {
        global.__dispararAppState(v);
        await drenar();
      });
    }
    expect(tokens()).toBe(0);
    expect(registros()).toEqual([]);
    expect(pedidos()).toBe(0);
  });
});

describe("permissão", () => {
  it("indeterminada e livre: um pedido só, com alerta e som e SEM badge", async () => {
    global.__definirPush({ permissao: "undetermined" });
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(pedidos()).toBe(1));
    expect(Notifications.requestPermissionsAsync).toHaveBeenCalledWith({
      ios: { allowAlert: true, allowSound: true, allowBadge: false },
    });
    expect(registros()).toEqual([]); // anônimo: nada no servidor
  });

  it("permitir com sessão registra na hora, sem esperar a volta do fundo", async () => {
    global.__definirPush({ permissao: "undetermined" });
    await guardarCredenciais(S);
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(registros()).toHaveLength(1));
    expect(pedidos()).toBe(1);
    expect(registros()[0]).toMatchObject({ auth: "Bearer access-s" });
  });

  it("recusar no prompt não registra", async () => {
    global.__definirPush({ permissao: "undetermined", promptResponde: "denied" });
    await guardarCredenciais(S);
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(pedidos()).toBe(1));
    await waitFor(() => expect(screen.getByText("Bom dia, S")).toBeTruthy());
    expect(tokens()).toBe(0);
    expect(registros()).toEqual([]);
  });

  it("já negada: nunca pede", async () => {
    global.__definirPush({ permissao: "denied" });
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
    expect(pedidos()).toBe(0);
  });

  it("com a trava na frente não pede (colidiria com o Face ID); liberou, pede", async () => {
    const pendentes: ((r: LA.LocalAuthenticationResult) => void)[] = [];
    jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.BIOMETRIC);
    jest.mocked(LA.authenticateAsync).mockImplementation(() => new Promise((r) => pendentes.push(r)));
    global.__definirPush({ permissao: "undetermined" });
    await guardarCredenciais(S);
    try {
      renderRouter("./app", { initialUrl: "/" });
      await waitFor(() => expect(pendentes).toHaveLength(1));
      await act(drenar);
      expect(pedidos()).toBe(0);

      await act(async () => {
        pendentes[0]!({ success: true } as LA.LocalAuthenticationResult);
        await drenar();
      });
      await waitFor(() => expect(pedidos()).toBe(1));
    } finally {
      jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
      jest.mocked(LA.authenticateAsync).mockResolvedValue({ success: true });
    }
  });

  it("a trava trava e destrava com a permissão ainda indeterminada: o prompt sai 1 vez só", async () => {
    const pendentes: ((r: LA.LocalAuthenticationResult) => void)[] = [];
    jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.BIOMETRIC);
    jest.mocked(LA.authenticateAsync).mockImplementation(() => new Promise((r) => pendentes.push(r)));
    global.__definirPush({ permissao: "undetermined" });
    // O pedido da abertura não resolve: a permissão segue indeterminada e o `Push` poderia pedir de novo.
    jest.mocked(Notifications.requestPermissionsAsync).mockResolvedValueOnce({ status: "undetermined", granted: false, canAskAgain: true, expires: "never" } as never);
    await guardarCredenciais(S);
    const liberar = (i: number) =>
      act(async () => {
        pendentes[i]!({ success: true } as LA.LocalAuthenticationResult);
        await drenar();
      });
    try {
      renderRouter("./app", { initialUrl: "/" });
      await waitFor(() => expect(pendentes).toHaveLength(1));
      await liberar(0);
      await waitFor(() => expect(pedidos()).toBe(1));

      await appVai("inactive");
      await appVai("background");
      jest.setSystemTime(Date.now() + 61_000); // passou da carência: a trava volta
      await appVai("active");
      await waitFor(() => expect(pendentes).toHaveLength(2));
      await liberar(1);
      await act(drenar);
      expect(pedidos()).toBe(1);
    } finally {
      jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
      jest.mocked(LA.authenticateAsync).mockResolvedValue({ success: true });
    }
  });
});
