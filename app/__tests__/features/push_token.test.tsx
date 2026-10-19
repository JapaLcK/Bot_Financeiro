/**
 * O token APNs que rola com o app aberto (`addPushTokenListener`). O evento nativo dispara a cada `didRegister`,
 * inclusive o do nosso próprio `getDevicePushTokenAsync`: o ouvinte nunca pede o token (laço), só registra o que o
 * evento traz e só se ainda não foi enviado. Grupo com os dois controles. NEGATIVOS: tirar a assinatura; tirar o
 * `tokenJaEnviado`; tirar o reset do desregistro; tirar o reset da falha do POST. POSITIVO: token novo registra.
 */
import * as LA from "expo-local-authentication";
import * as Notifications from "expo-notifications";
import { act, renderRouter, screen, waitFor } from "expo-router/testing-library";

import { guardarCredenciais, limparCredenciais } from "@/storage/secure";

import { chamadas, prepararCaso, resposta, rotear, S } from "./auth_apoio";
import { drenar } from "./seguranca_sem_senha_apoio";

declare const global: typeof globalThis & {
  __definirPush: (v: Record<string, unknown>) => void;
  __emitirToken: (data: string) => void;
};

const registros = () => chamadas().filter((c) => c.caminho === "/api/push/register");
const tokens = () => jest.mocked(Notifications.getDevicePushTokenAsync).mock.calls.length;
const emitir = (data: string) =>
  act(async () => {
    global.__emitirToken(data);
    await drenar();
  });
let falhar = false;

beforeEach(() => {
  prepararCaso();
  falhar = false;
  rotear({ "/api/push/register": () => (falhar ? resposta(500, { detail: "x" }) : resposta(200, { ok: true })) });
  global.__definirPush({ permissao: "granted", token: "apns-teste", ambiente: "development", resposta: null });
  jest.mocked(Notifications.getDevicePushTokenAsync).mockClear();
});

const abrirLogado = async () => {
  await guardarCredenciais(S);
  renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(registros()).toHaveLength(1));
};

it("POSITIVO: token novo com a sessão livre registra esse token, sem pedir o token ao nativo", async () => {
  await abrirLogado();
  const pedidos = tokens();
  await emitir("token-novo");
  await waitFor(() => expect(registros()).toHaveLength(2));
  expect(registros()[1]).toMatchObject({ auth: "Bearer access-s", corpo: { token: "token-novo", platform: "ios", environment: "sandbox" } });
  expect(tokens()).toBe(pedidos);
});

it("o mesmo token que acabou de ser registrado não repete o POST (o evento do nosso próprio pedido não faz laço)", async () => {
  await abrirLogado();
  const pedidos = tokens();
  await emitir("apns-teste");
  await emitir("apns-teste");
  expect(registros()).toHaveLength(1);
  expect(tokens()).toBe(pedidos);
});

it("token novo emitido duas vezes registra uma só", async () => {
  await abrirLogado();
  await emitir("token-novo");
  await emitir("token-novo");
  await waitFor(() => expect(registros()).toHaveLength(2));
  expect(registros()).toHaveLength(2);
});

it("a falha do POST esquece o token: o mesmo token emitido de novo tenta outra vez", async () => {
  await abrirLogado();
  falhar = true;
  await emitir("token-novo");
  await waitFor(() => expect(registros()).toHaveLength(2));
  falhar = false;
  await emitir("token-novo");
  await waitFor(() => expect(registros()).toHaveLength(3));
});

it("depois do Sair (cofre vazio) um token novo não vai ao servidor", async () => {
  await abrirLogado();
  await limparCredenciais();
  await emitir("token-novo");
  expect(registros()).toHaveLength(1);
});

it("o desregistro esquece o último token: quem entra de novo com o mesmo token do aparelho registra", async () => {
  await abrirLogado();
  await limparCredenciais();
  await guardarCredenciais(S); // outra sessão no cofre, com o `Push` ainda montado
  await emitir("apns-teste");
  await waitFor(() => expect(registros()).toHaveLength(2));
});

it("sem sessão (anônimo) um token novo não vai ao servidor", async () => {
  renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen).toHavePathname("/boas-vindas"));
  await emitir("token-novo");
  expect(registros()).toEqual([]);
});

it("permissão negada: um token novo não vai ao servidor", async () => {
  global.__definirPush({ permissao: "denied" });
  await guardarCredenciais(S);
  renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByText("Bom dia, S")).toBeTruthy());
  await emitir("token-novo");
  expect(registros()).toEqual([]);
});

it("com a trava na frente não há ouvinte: nada vai ao servidor; liberou, o token novo registra", async () => {
  const pendentes: ((r: LA.LocalAuthenticationResult) => void)[] = [];
  jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.BIOMETRIC);
  jest.mocked(LA.authenticateAsync).mockImplementation(() => new Promise((r) => pendentes.push(r)));
  await guardarCredenciais(S);
  try {
    renderRouter("./app", { initialUrl: "/" });
    await waitFor(() => expect(pendentes).toHaveLength(1));
    await emitir("token-novo");
    expect(chamadas()).toEqual([]);

    await act(async () => {
      pendentes[0]!({ success: true } as LA.LocalAuthenticationResult);
      await drenar();
    });
    await waitFor(() => expect(registros()).toHaveLength(1)); // o registro da liberação (token do aparelho)
    await emitir("token-novo");
    await waitFor(() => expect(registros()).toHaveLength(2));
  } finally {
    jest.mocked(LA.getEnrolledLevelAsync).mockResolvedValue(LA.SecurityLevel.NONE);
    jest.mocked(LA.authenticateAsync).mockResolvedValue({ success: true });
  }
});
