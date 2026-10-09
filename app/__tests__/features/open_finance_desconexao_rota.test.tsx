import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { Alert } from "react-native";
import { guardarCredenciais } from "@/storage/secure";
import { chamadas, prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";
import { VIVO } from "./open_finance_volta_apoio";
import { appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

beforeEach(async () => { prepararCaso(); desligarTrava(); await guardarCredenciais(S); });
afterEach(() => jest.restoreAllMocks());
it("confirma remoção individual sem afetar a segunda conexão ou enviar DELETE todos", async () => {
  let lista = [VIVO, { ...VIVO, id: 2, provider_item_id: "item_b", institution_name: "Itaú" }];
  rotear({ "/open-finance/1": () => resposta(200, { connections: lista }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 2, em_uso: lista.length, pode_adicionar: lista.length < 2, code: null, message: null }),
    "/open-finance/1/connections/1": () => { lista = lista.filter((c) => c.id !== 1); return resposta(200, { ok: true, deleted: 1 }); },
  });
  const alert = jest.spyOn(Alert, "alert").mockImplementation((_title, _message, botoes) => { botoes?.find((b) => b.style === "destructive")?.onPress?.(); });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Desconectar Nubank" })).toBeTruthy());
  // Sem segundos; o ano só fora do ano corrente.
  expect(screen.getAllByText(/^Última sincronização: \d{2}\/\d{2}(\/\d{4})?, \d{2}:\d{2}$/)).toHaveLength(2);
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Desconectar Nubank" })).toBeNull());
  expect(screen.getByRole("button", { name: "Desconectar Itaú" })).toBeTruthy();
  expect(alert.mock.calls[0]?.[1]).toContain("dados importados deste banco serão removidos");
  expect(chamadas().filter((c) => c.caminho.includes("/connections/"))).toHaveLength(1);
});

it("DELETE concluído em background libera ações ao voltar ao foreground", async () => {
  const pausa = segurar();
  let lista = [VIVO, { ...VIVO, id: 2, provider_item_id: "item_b", institution_name: "Itaú" }];
  rotear({ "/open-finance/1": () => resposta(200, { connections: lista }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 2, em_uso: lista.length, pode_adicionar: lista.length < 2, code: null, message: null }),
    "/open-finance/1/connections/1": async () => { await pausa.promessa; lista = lista.filter((c) => c.id !== 1); return resposta(200, { ok: true, deleted: 1 }); },
  });
  jest.spyOn(Alert, "alert").mockImplementation((_title, _message, botoes) => { botoes?.find((b) => b.style === "destructive")?.onPress?.(); });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Desconectar Nubank" })).toBeTruthy());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
  await appVai("background");
  await act(async () => { pausa.soltar(); await drenar(); });
  await appVai("active");
  await waitFor(() => expect(screen.queryByRole("button", { name: "Desconectar Nubank" })).toBeNull());
  expect(screen.getByRole("button", { name: "Reconectar Itaú" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Conectar outro banco" })).toBeEnabled();
});
