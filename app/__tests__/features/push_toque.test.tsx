/**
 * O toque na notificação. `rotaDoToque` com a forma REAL do iOS (o servidor põe
 * `rota` na raiz do payload; `content.data` chega vazio, o userInfo inteiro fica
 * em `trigger.payload`) e a integração pelo `_layout` real. Grupo com os dois
 * controles. NEGATIVOS: ler `content.data`; trocar o `Map` por `ROTAS[rota]`;
 * tirar a espera da trava; tirar o descarte do anônimo; tirar a dedupe.
 * POSITIVO: autenticado e livre navega.
 */
import * as LA from "expo-local-authentication";
import * as Notifications from "expo-notifications";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import * as ExpoRouter from "expo-router";
import { router } from "expo-router";

import { rotaDoToque } from "@/features/push/Push";
import { guardarCredenciais } from "@/storage/secure";

import { prepararCaso, rotear, S } from "./auth_apoio";

declare const global: typeof globalThis & {
  __definirPush: (v: Record<string, unknown>) => void;
  __responderNotificacao: (r: unknown) => void;
};

const ACAO = "expo.modules.notifications.actions.DEFAULT";
const drenar = async () => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

/** A forma que o iOS entrega para um push remoto: `data` vazio, userInfo no `trigger.payload`. */
function toque(rota: unknown, extra: Record<string, unknown> = {}, id = "n1", date = 1000) {
  return {
    actionIdentifier: ACAO,
    notification: {
      date,
      request: {
        identifier: id,
        content: { data: null },
        trigger: { type: "push", payload: { aps: { alert: "x" }, ...(rota === undefined ? {} : { rota }) } },
      },
    },
    ...extra,
  } as unknown as Notifications.NotificationResponse;
}

describe("rotaDoToque", () => {
  it("conexoes -> /conexoes (forma real do iOS)", () => {
    expect(rotaDoToque(toque("conexoes"))).toBe("/conexoes");
  });

  it.each([["seguranca"], ["__proto__"], ["constructor"], ["toString"], ["../x"], [42], [null], [undefined], [["conexoes"]]])(
    "rota fora da tabela (%p) não navega",
    (rota) => {
      expect(rotaDoToque(toque(rota))).toBeNull();
    },
  );

  it("ação que não é o toque padrão não navega", () => {
    expect(rotaDoToque(toque("conexoes", { actionIdentifier: "outra" }))).toBeNull();
  });

  it("gatilho que não é push (local) não navega, mesmo com payload", () => {
    const r = toque("conexoes") as unknown as { notification: { request: { trigger: unknown } } };
    r.notification.request.trigger = { type: "timeInterval", payload: { rota: "conexoes" } };
    expect(rotaDoToque(r as unknown as Notifications.NotificationResponse)).toBeNull();
  });

  it("rota só em content.data (forma que o iOS NÃO entrega) não vale", () => {
    const r = toque(undefined) as unknown as { notification: { request: { content: { data: unknown } } } };
    r.notification.request.content.data = { rota: "conexoes" };
    expect(rotaDoToque(r as unknown as Notifications.NotificationResponse)).toBeNull();
  });
});

beforeEach(() => {
  prepararCaso();
  rotear();
  global.__definirPush({ permissao: "granted", resposta: null, ambiente: "development" });
  jest.mocked(Notifications.clearLastNotificationResponse).mockClear();
});

describe("toque pelo _layout", () => {
  it("POSITIVO: autenticado e livre abre Bancos conectados", async () => {
    await guardarCredenciais(S);
    global.__definirPush({ resposta: toque("conexoes") });
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen).toHavePathname("/conexoes"));
    expect(Notifications.clearLastNotificationResponse).toHaveBeenCalledTimes(1);
  });

  it("POSITIVO: o 2º push com o MESMO identificador (collapse id do servidor) e outra data navega de novo", async () => {
    await guardarCredenciais(S);
    global.__definirPush({ resposta: toque("conexoes", {}, "of-reconectar", 1000) });
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen).toHavePathname("/conexoes"));

    await act(async () => router.navigate("/configuracoes"));
    await waitFor(() => expect(screen).toHavePathname("/configuracoes"));
    await act(async () => {
      global.__responderNotificacao(toque("conexoes", {}, "of-reconectar", 2000));
      await drenar();
    });
    await waitFor(() => expect(screen).toHavePathname("/conexoes"));
    expect(Notifications.clearLastNotificationResponse).toHaveBeenCalledTimes(2);
  });

  it("a mesma resposta duas vezes (mesmo id e mesma data) navega uma vez só", async () => {
    await guardarCredenciais(S);
    global.__definirPush({ resposta: toque("conexoes") });
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen).toHavePathname("/conexoes"));

    await act(async () => router.navigate("/configuracoes"));
    await waitFor(() => expect(screen).toHavePathname("/configuracoes"));
    await act(async () => {
      global.__responderNotificacao(toque("conexoes")); // outro objeto, mesmo identificador
      await drenar();
    });
    expect(screen).toHavePathname("/configuracoes");
    expect(Notifications.clearLastNotificationResponse).toHaveBeenCalledTimes(1);
  });

  it("rota fora da tabela com sessão não navega", async () => {
    await guardarCredenciais(S);
    global.__definirPush({ resposta: toque("seguranca") });
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(screen.getByText("Bom dia, S")).toBeTruthy());
    expect(screen).toHavePathname("/resumo");
  });

  it("anônimo descarta: logar depois NÃO leva a Bancos conectados", async () => {
    global.__definirPush({ resposta: toque("conexoes") });
    renderRouter("./app", { initialUrl: "/entrar" });
    await waitFor(() => expect(Notifications.clearLastNotificationResponse).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByLabelText("E-mail")).toBeTruthy());

    fireEvent.changeText(screen.getByLabelText("E-mail"), "ana@x.com");
    fireEvent.changeText(screen.getByLabelText("Senha"), "s3nha");
    await act(async () => fireEvent.press(screen.getByRole("button", { name: "Entrar" })));
    await waitFor(() => expect(screen.getByText("Bom dia, Ana")).toBeTruthy());
    expect(screen).toHavePathname("/resumo");
  });

  it("com a trava na frente espera; liberou, navega uma vez", async () => {
    const pendentes: ((r: LA.LocalAuthenticationResult) => void)[] = [];
    jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.BIOMETRIC);
    jest.mocked(LA.authenticateAsync).mockImplementation(() => new Promise((r) => pendentes.push(r)));
    await guardarCredenciais(S);
    global.__definirPush({ resposta: toque("conexoes") });
    try {
      renderRouter("./app", { initialUrl: "/" });
      await waitFor(() => expect(pendentes).toHaveLength(1));
      await waitFor(() => expect(Notifications.clearLastNotificationResponse).toHaveBeenCalled());
      await act(drenar);
      expect(screen).not.toHavePathname("/conexoes");

      await act(async () => {
        pendentes[0]!({ success: true } as LA.LocalAuthenticationResult);
        await drenar();
      });
      await waitFor(() => expect(screen).toHavePathname("/conexoes"));
    } finally {
      jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
      jest.mocked(LA.authenticateAsync).mockResolvedValue({ success: true });
    }
  });

  it("toque antes de a navegação montar não navega (nem lança); navega quando monta", async () => {
    const estadoDaNavegacao = jest.spyOn(ExpoRouter, "useRootNavigationState").mockReturnValue({} as never); // sem `key`: ainda não montou
    const navegar = jest.spyOn(router, "navigate");
    try {
      await guardarCredenciais(S);
      global.__definirPush({ resposta: toque("conexoes") });
      renderRouter("./app", { initialUrl: "/" });
      await waitFor(() => expect(screen.getByText("Bom dia, S")).toBeTruthy());
      expect(navegar).not.toHaveBeenCalledWith("/conexoes");
      expect(screen).toHavePathname("/resumo");

      estadoDaNavegacao.mockReturnValue({ key: "montou" } as never);
      await act(async () => {
        global.__responderNotificacao(toque("conexoes")); // a mesma resposta já tratada: só faz o `Push` renderizar e ler o estado novo
        await drenar();
      });
      await waitFor(() => expect(screen).toHavePathname("/conexoes"));
    } finally {
      estadoDaNavegacao.mockRestore();
      navegar.mockRestore();
    }
  });
});
