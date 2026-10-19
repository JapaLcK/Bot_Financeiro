/**
 * A chave "Banco precisa de atenção" (seção "No celular"). A preferência do servidor
 * (`push_bank_reconnect_enabled`) só vale com a permissão do sistema; cada estado dela faz uma coisa:
 * concedida escreve (PATCH só com esse campo), negada abre os Ajustes (zero PATCH), indeterminada pede.
 * Grupo com os dois controles: NEGATIVOS no relato (tirar o ramo `granted`, tirar o `openSettings`, tirar o
 * `pedirPermissaoPush`, `.default(true)` no schema); POSITIVOS são os casos "permitido escreve" e "pede e registra".
 */
import * as Notifications from "expo-notifications";
import { Linking } from "react-native";
import { screen, waitFor } from "expo-router/testing-library";

import { chamadas, resposta } from "./auth_apoio";
import { abrirNotificacoes, ligada, patches, servidor, trocar } from "./notificacoes_apoio";
import { appVai, preparar } from "./seguranca_sem_senha_apoio";

jest.mock("@react-native-community/datetimepicker", () => ({
  __esModule: true,
  default: () => null,
  DateTimePickerAndroid: { open: jest.fn() },
}));

declare const global: typeof globalThis & { __definirPush: (v: Record<string, unknown>) => void };

const ROTULO = "Banco precisa de atenção";
const abrir = async (permissao: string, inicial = {}, extra: Record<string, unknown> = {}) => {
  global.__definirPush({ permissao, token: "apns-teste", ambiente: "development", ...extra });
  const srv = servidor(inicial);
  await abrirNotificacoes(srv, { "/api/push/register": () => resposta(200, { ok: true }) });
  await waitFor(() => expect(screen.getByLabelText("Resumo mensal")).toBeTruthy());
  // A chave só destrava depois de a permissão ser lida.
  await waitFor(() => expect(screen.getByLabelText(ROTULO).props.disabled).toBe(false));
  return srv;
};
/** O `Push` do `_layout` real pede na 1ª abertura (D2); aqui esse pedido não resolve, e quem pede é o toque na chave. */
const semPedidoDaAbertura = () =>
  jest.mocked(Notifications.requestPermissionsAsync).mockResolvedValueOnce({ status: "undetermined", granted: false, canAskAgain: true, expires: "never" } as never);
const registros = () => chamadas().filter((c) => c.caminho === "/api/push/register");
const pedidos = () => jest.mocked(Notifications.requestPermissionsAsync).mock.calls.length;

beforeEach(() => {
  preparar();
  jest.mocked(Notifications.requestPermissionsAsync).mockClear();
  jest.spyOn(Linking, "openSettings").mockResolvedValue();
});
afterEach(() => jest.restoreAllMocks());

describe("permissão concedida: a chave é a preferência do servidor", () => {
  it("POSITIVO: ligada; desligar manda SÓ { push_bank_reconnect_enabled: false } e fica desligada; ligar de novo manda true", async () => {
    await abrir("granted");
    expect(ligada(ROTULO)).toBe(true);
    await trocar(ROTULO, false);
    await waitFor(() => expect(ligada(ROTULO)).toBe(false));
    expect(patches()).toEqual([{ push_bank_reconnect_enabled: false }]);
    await trocar(ROTULO, true);
    await waitFor(() => expect(ligada(ROTULO)).toBe(true));
    expect(patches()).toEqual([{ push_bank_reconnect_enabled: false }, { push_bank_reconnect_enabled: true }]);
    expect(Linking.openSettings).not.toHaveBeenCalled();
    expect(pedidos()).toBe(0);
  });

  it("preferência desligada no servidor: a chave abre desligada", async () => {
    await abrir("granted", { push_bank_reconnect_enabled: false });
    expect(ligada(ROTULO)).toBe(false);
  });
});

describe("permissão negada: a chave só leva aos Ajustes", () => {
  it("aparece desligada mesmo com a preferência ligada; o toque abre os Ajustes e NÃO manda PATCH nem pede de novo", async () => {
    await abrir("denied"); // o servidor diz true
    expect(ligada(ROTULO)).toBe(false);
    expect(screen.getByText("Nos Ajustes")).toBeTruthy();
    await trocar(ROTULO, true);
    expect(Linking.openSettings).toHaveBeenCalledTimes(1);
    expect(patches()).toEqual([]);
    expect(pedidos()).toBe(0);
    expect(ligada(ROTULO)).toBe(false);
  });

  it("voltou dos Ajustes com a permissão dada: a chave relê e liga, sem recarregar a tela", async () => {
    await abrir("denied");
    global.__definirPush({ permissao: "granted" });
    await appVai("background");
    await appVai("active");
    await waitFor(() => expect(ligada(ROTULO)).toBe(true));
    expect(screen.queryByText("Nos Ajustes")).toBeNull();
  });
});

describe("permissão indeterminada: o toque pede", () => {
  it("POSITIVO: concedeu — pede uma vez (alerta e som, sem badge), registra o aparelho e a chave liga; nenhum PATCH", async () => {
    semPedidoDaAbertura();
    await abrir("undetermined");
    expect(ligada(ROTULO)).toBe(false);
    expect(registros()).toEqual([]);
    const antes = pedidos();
    await trocar(ROTULO, true);
    await waitFor(() => expect(registros()).toHaveLength(1));
    expect(Notifications.requestPermissionsAsync).toHaveBeenLastCalledWith({ ios: { allowAlert: true, allowSound: true, allowBadge: false } });
    expect(pedidos()).toBe(antes + 1);
    await waitFor(() => expect(ligada(ROTULO)).toBe(true));
    expect(patches()).toEqual([]);
  });

  it("POSITIVO: tocou para ligar, concedeu e a preferência estava desligada no servidor: grava true e a chave liga", async () => {
    semPedidoDaAbertura();
    await abrir("undetermined", { push_bank_reconnect_enabled: false });
    await trocar(ROTULO, true);
    await waitFor(() => expect(patches()).toEqual([{ push_bank_reconnect_enabled: true }]));
    await waitFor(() => expect(ligada(ROTULO)).toBe(true));
    expect(registros()).toHaveLength(1);
  });

  it("recusou no prompt: nada é registrado, a chave segue desligada e passa a levar aos Ajustes", async () => {
    semPedidoDaAbertura();
    await abrir("undetermined", {}, { promptResponde: "denied" });
    await trocar(ROTULO, true);
    await waitFor(() => expect(screen.getByText("Nos Ajustes")).toBeTruthy());
    expect(registros()).toEqual([]);
    expect(patches()).toEqual([]);
    expect(ligada(ROTULO)).toBe(false);
  });

  it("recusou no prompt com a preferência desligada no servidor: nada é gravado", async () => {
    semPedidoDaAbertura();
    await abrir("undetermined", { push_bank_reconnect_enabled: false }, { promptResponde: "denied" });
    await trocar(ROTULO, true);
    await waitFor(() => expect(screen.getByText("Nos Ajustes")).toBeTruthy());
    expect(patches()).toEqual([]);
  });
});

describe("enquanto a permissão não foi lida", () => {
  it("a chave fica desabilitada e desligada", async () => {
    const real = jest.mocked(Notifications.getPermissionsAsync).getMockImplementation()!;
    jest.mocked(Notifications.getPermissionsAsync).mockImplementation(() => new Promise(() => undefined));
    try {
      global.__definirPush({ permissao: "granted" });
      await abrirNotificacoes(servidor(), { "/api/push/register": () => resposta(200, { ok: true }) });
      await waitFor(() => expect(screen.getByLabelText("Resumo mensal")).toBeTruthy());
      expect(screen.getByLabelText(ROTULO).props.disabled).toBe(true);
      expect(ligada(ROTULO)).toBe(false);
    } finally {
      jest.mocked(Notifications.getPermissionsAsync).mockImplementation(real);
    }
  });
});

it("Android: a seção de push não existe (o push é APNs)", async () => {
  const { Platform } = jest.requireActual("react-native");
  jest.replaceProperty(Platform, "OS", "android");
  global.__definirPush({ permissao: "granted" });
  await abrirNotificacoes(servidor());
  await waitFor(() => expect(screen.getByLabelText("Resumo mensal")).toBeTruthy());
  expect(screen.queryByLabelText(ROTULO)).toBeNull();
  expect(screen.queryByText("No celular")).toBeNull();
});
