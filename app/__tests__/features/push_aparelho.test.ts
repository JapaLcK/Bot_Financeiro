/**
 * `features/push/aparelho.ts` (ambiente do APNs) e o isolamento entre contas do
 * registro (`services/push.ts` + `sair()`). O isolamento é grupo com os dois
 * controles: NEGATIVOS (tirar a fila de `aparelho.ts`; tirar a checagem de cofre
 * do `tokenSe`) e POSITIVO (a conta B registra com o token e o Bearer dela).
 */
import * as Application from "expo-application";
import * as Notifications from "expo-notifications";

import { ambienteApns, tokenSe } from "@/features/push/aparelho";
import { sair } from "@/services/auth";
import { registrarAparelho } from "@/services/push";
import { Platform } from "react-native";

import { guardarCredenciais, limparCredenciais } from "@/storage/secure";

import { chamadas, prepararCaso, respirar, rotear, segurar } from "./auth_apoio";

let mockIsDevice = true;
jest.mock("expo-device", () => ({
  get isDevice() {
    return mockIsDevice;
  },
  modelName: "iPhone",
  osVersion: "27.0",
}));

declare const global: typeof globalThis & { __definirPush: (v: Record<string, unknown>) => void };

const ACCESS_A = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIiwianRpIjoic2Vzc2FvLUEifQ.assinatura";
const ACCESS_B = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIyIiwianRpIjoic2Vzc2FvLUIifQ.assinatura";
const A = { access: ACCESS_A, refresh: "rt_A" };
const B = { access: ACCESS_B, refresh: "rt_B" };

beforeEach(() => {
  prepararCaso();
  rotear({ "/api/push/register": () => ({ ok: true, status: 200, json: async () => ({ ok: true }) }) as Response });
  global.__definirPush({ permissao: "granted", token: "token-A", ambiente: null, atrasoDesregistro: null });
  jest.mocked(Notifications.getDevicePushTokenAsync).mockClear();
  jest.mocked(Notifications.unregisterForNotificationsAsync).mockClear();
});

afterEach(() => {
  jest.restoreAllMocks();
  jest.useRealTimers();
});

describe("ambienteApns", () => {
  it.each([
    ["development", true, "sandbox"],
    ["development", false, "sandbox"],
    ["production", true, "production"],
    ["production", false, "production"],
    [null, true, "production"], // TestFlight/loja: sem perfil embutido
    [null, false, "sandbox"], // simulador
  ] as const)("aps-environment %s, aparelho real %s: %s", async (aps, aparelho, esperado) => {
    jest.mocked(Application.getIosPushNotificationServiceEnvironmentAsync).mockResolvedValueOnce(aps);
    mockIsDevice = aparelho;
    expect(await ambienteApns()).toBe(esperado);
  });

  it("falha ao ler o perfil cai na regra do aparelho", async () => {
    jest.mocked(Application.getIosPushNotificationServiceEnvironmentAsync).mockRejectedValueOnce(new Error("x"));
    mockIsDevice = true;
    expect(await ambienteApns()).toBe("production");
  });
});

const registros = () => chamadas().filter((c) => c.caminho === "/api/push/register");

describe("registro e troca de conta", () => {
  it("registra com o Bearer guardado, o token e o ambiente", async () => {
    global.__definirPush({ ambiente: "development" });
    await guardarCredenciais(A);
    await registrarAparelho();
    expect(registros()).toEqual([
      { caminho: "/api/push/register", auth: `Bearer ${ACCESS_A}`, corpo: { token: "token-A", platform: "ios", environment: "sandbox" } },
    ]);
  });

  it("sem sessão no cofre nunca pede o token ao APNs (ele re-registraria o aparelho)", async () => {
    await registrarAparelho();
    expect(Notifications.getDevicePushTokenAsync).not.toHaveBeenCalled();
    expect(registros()).toEqual([]);
  });

  it("A sai, B entra: o registro de B espera o desregistro de A e leva o token e o Bearer de B", async () => {
    await guardarCredenciais(A);
    await registrarAparelho();
    expect(registros()).toHaveLength(1);

    // O desregistro de A fica preso no APNs; B já está logada.
    const desregistro = segurar();
    global.__definirPush({ atrasoDesregistro: desregistro.promessa });
    await sair();
    await guardarCredenciais(B);
    jest.mocked(Notifications.getDevicePushTokenAsync).mockClear();
    global.__definirPush({ token: "token-B" });
    const emVoo = registrarAparelho();
    await respirar();
    expect(Notifications.getDevicePushTokenAsync).not.toHaveBeenCalled();

    desregistro.soltar();
    await emVoo;
    expect(Notifications.unregisterForNotificationsAsync).toHaveBeenCalledTimes(1);
    expect(Notifications.getDevicePushTokenAsync).toHaveBeenCalledTimes(1);
    expect(registros()).toHaveLength(2);
    expect(registros()[1]).toMatchObject({ auth: `Bearer ${ACCESS_B}`, corpo: { token: "token-B" } });
  });

  it("depois do Sair nenhum registro leva o token de A", async () => {
    await guardarCredenciais(A);
    await registrarAparelho();
    await sair();
    await registrarAparelho(); // volta do fundo no meio: o cofre está vazio
    await respirar();
    expect(registros()).toHaveLength(1);
    expect(Notifications.getDevicePushTokenAsync).toHaveBeenCalledTimes(1);
  });
});

describe("o token que nunca resolve (offline, simulador)", () => {
  it("depois do prazo a fila segue: o Sair desregistra 1 vez e o token tardio não gera POST", async () => {
    jest.useFakeTimers();
    let chegou!: (t: { type: string; data: string }) => void;
    jest.mocked(Notifications.getDevicePushTokenAsync).mockImplementationOnce(() => new Promise((r) => (chegou = r)) as never);
    await guardarCredenciais(A);
    const emVoo = registrarAparelho();
    await jest.advanceTimersByTimeAsync(0);
    expect(Notifications.getDevicePushTokenAsync).toHaveBeenCalledTimes(1);

    await limparCredenciais(); // o Sair: o cofre limpa e o desregistro entra na fila, atrás do token
    await jest.advanceTimersByTimeAsync(9_000);
    expect(Notifications.unregisterForNotificationsAsync).not.toHaveBeenCalled();

    await jest.advanceTimersByTimeAsync(2_000); // passou dos 10 s
    expect(Notifications.unregisterForNotificationsAsync).toHaveBeenCalledTimes(1);
    await emVoo;
    chegou({ type: "ios", data: "token-tardio" });
    await jest.advanceTimersByTimeAsync(0);
    expect(registros()).toEqual([]);
  });
});

it("token que chegou a tempo não deixa o cronômetro do prazo vivo", async () => {
  jest.useFakeTimers();
  expect(await tokenSe(async () => true)).toBe("token-A");
  expect(jest.getTimerCount()).toBe(0); // direto no `tokenSe`: o `chamar` tem o seu próprio prazo
});

describe("só iOS", () => {
  it("Android: a limpeza do cofre não desregistra", async () => {
    jest.replaceProperty(Platform, "OS", "android");
    await guardarCredenciais(A);
    await limparCredenciais();
    await respirar();
    expect(Notifications.unregisterForNotificationsAsync).not.toHaveBeenCalled();
  });

  it("Android: registrarAparelho não pede o token nem faz POST", async () => {
    jest.replaceProperty(Platform, "OS", "android");
    await guardarCredenciais(A);
    await registrarAparelho();
    expect(Notifications.getDevicePushTokenAsync).not.toHaveBeenCalled();
    expect(registros()).toEqual([]);
  });
});
