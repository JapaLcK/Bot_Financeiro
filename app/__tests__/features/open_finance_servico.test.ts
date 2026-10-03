import Constants from "expo-constants";

import { conexoes, pedirConnectToken, registrarItem } from "@/services/openFinance";
import { guardarCredenciais } from "@/storage/secure";

import { chamadas, prepararCaso, resposta, rotear, S } from "./auth_apoio";

/** O dublê de `expo-constants` (jest.setup.js) é um objeto: cada caso muda e o `afterEach` devolve. */
const constantes = Constants as unknown as { executionEnvironment: string; expoConfig: { scheme?: unknown } };
const ORIGINAL = { ambiente: constantes.executionEnvironment, scheme: constantes.expoConfig.scheme };

const LISTA = { ok: true, connections: [] };

beforeEach(async () => {
  prepararCaso();
  await guardarCredenciais(S);
  rotear({
    "/open-finance/7/connect-token": () => resposta(200, { ok: true, accessToken: "tok", includeSandbox: false, provider: "pluggy" }),
    "/open-finance/7": () => resposta(200, LISTA),
    "/open-finance/7/pluggy-item": () => resposta(200, { ...LISTA, connection: {} }),
  });
});

afterEach(() => {
  constantes.executionEnvironment = ORIGINAL.ambiente;
  constantes.expoConfig.scheme = ORIGINAL.scheme;
});

describe("services/openFinance — connect-token", () => {
  it("no binário manda o scheme dele como app_scheme", async () => {
    await expect(pedirConnectToken(7)).resolves.toMatchObject({ accessToken: "tok" });
    expect(chamadas()).toEqual([{ caminho: "/open-finance/7/connect-token", auth: "Bearer access-s", corpo: { app_scheme: "pigbank-dev" } }]);
  });

  it("no Expo Go não manda o campo", async () => {
    constantes.executionEnvironment = "storeClient";
    await pedirConnectToken(7);
    expect(chamadas()[0]!.corpo).toEqual({});
  });

  it.each([
    ["ausente", undefined],
    ["vazio", ""],
    ["em lista", ["pigbank-dev", "outro"]],
  ])("scheme %s: não manda o campo", async (_nome, scheme) => {
    constantes.expoConfig.scheme = scheme;
    await pedirConnectToken(7);
    expect(chamadas()[0]!.corpo).toEqual({});
  });
});

describe("services/openFinance — conexões e item", () => {
  it("GET da lista na rota do uid", async () => {
    await expect(conexoes(7)).resolves.toEqual({ connections: [] });
    expect(chamadas()).toEqual([{ caminho: "/open-finance/7", auth: "Bearer access-s", corpo: undefined }]);
  });

  it("POST do item manda só o id", async () => {
    await expect(registrarItem(7, "item_1")).resolves.toEqual({ connections: [] });
    expect(chamadas()).toEqual([{ caminho: "/open-finance/7/pluggy-item", auth: "Bearer access-s", corpo: { item: { id: "item_1" } } }]);
  });
});
