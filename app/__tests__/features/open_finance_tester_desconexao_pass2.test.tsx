import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { Alert } from "react-native";
import { guardarCredenciais } from "@/storage/secure";
import { chamadas, prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";
import { VIVO } from "./open_finance_volta_apoio";
import { appVai, desligarTrava, drenar, liberar, ligarTrava, prompts } from "./open_finance_volta_rota_apoio";

beforeEach(async () => { prepararCaso(); desligarTrava(); await guardarCredenciais(S); });
afterEach(() => jest.restoreAllMocks());

it.each([false, true])("remoção com resposta perdida=%p em background libera CTAs depois do FaceID", async (perdida) => {
  const pausa = segurar();
  let lista = [VIVO, { ...VIVO, id: 2, provider_item_id: "item_b", institution_name: "Itaú" }];
  rotear({ "/open-finance/1": () => resposta(200, { connections: lista }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 2, em_uso: lista.length, pode_adicionar: lista.length < 2, code: null, message: null }),
    "/open-finance/1/connections/1": async () => {
      await pausa.promessa;
      lista = lista.filter((c) => c.id !== 1);
      if (perdida) throw new TypeError("resposta perdida");
      return resposta(200, { ok: true, deleted: 1 });
    },
  });
  jest.spyOn(Alert, "alert").mockImplementation((_title, _message, botoes) => { botoes?.find((b) => b.style === "destructive")?.onPress?.(); });
  ligarTrava();
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(prompts()).toBe(1));
  await liberar();
  await waitFor(() => expect(screen.getByRole("button", { name: "Desconectar Nubank" })).toBeTruthy());
  await act(async () => {
    fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" }));
    fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" }));
    await drenar();
  });
  ligarTrava();
  await appVai("inactive");
  await appVai("background");
  await act(async () => { pausa.soltar(); await drenar(); });
  jest.setSystemTime(Date.now() + 61_000);
  await appVai("active");
  await waitFor(() => expect(prompts()).toBe(1));
  await liberar();
  await waitFor(() => expect(screen.queryByRole("button", { name: "Desconectar Nubank" })).toBeNull());
  expect(screen.getByRole("button", { name: "Reconectar Itaú" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Conectar outro banco" })).toBeEnabled();
  expect(chamadas().filter((c) => c.caminho.includes("/connections/"))).toHaveLength(1);
});

it("DELETE falho com item ainda presente mostra erro e libera retentativa", async () => {
  let tentativas = 0;
  rotear({ "/open-finance/1": () => resposta(200, { connections: [VIVO] }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 2, em_uso: 1, pode_adicionar: true, code: null, message: null }),
    "/open-finance/1/connections/1": () => { tentativas += 1; throw new TypeError("rede fora"); },
  });
  jest.spyOn(Alert, "alert").mockImplementation((_title, _message, botoes) => { botoes?.find((b) => b.style === "destructive")?.onPress?.(); });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Desconectar Nubank" })).toBeTruthy());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
  await waitFor(() => expect(screen.getByRole("button", { name: "Reconectar Nubank" })).toBeEnabled());
  expect(tentativas).toBe(1);
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
  expect(tentativas).toBe(2);
});
