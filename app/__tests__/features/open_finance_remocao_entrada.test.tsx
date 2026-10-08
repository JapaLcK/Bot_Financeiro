import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { Alert } from "react-native";
import type { Conexao } from "@/api/schemas/openFinance";
import { lerTentativaBancaria } from "@/storage/secure";
import { cofre, chamadas, fetchFalso, prepararCaso, resposta, rotear, S, segurar } from "./auth_apoio";
import { guardarSessaoOf, VIVO } from "./open_finance_volta_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

type Props = { onSuccess: (d: { item: { id: string } }) => void };
const mockWidget: { props: Props | null } = { props: null };
jest.mock("react-native-pluggy-connect", () => ({ PluggyConnect: (props: Props) => {
  mockWidget.props = props;
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require("react").createElement(require("react-native").View, { testID: "widget-remocao-entrada" });
} }));
function servidor(esperarToken = false, pausaRemocao?: Promise<void>) {
  const emitiuToken = segurar();
  let bancos: Conexao[] = [VIVO, { ...VIVO, id: 2, provider_item_id: "banco_b", institution_name: "Itaú" }];
  rotear({
    "/auth/me": () => resposta(200, { user_id: 1, display_name: "Ana", app_access: true }),
    "/onboarding/open-finance": () => resposta(200, { ok: true, completed: true, completed_at: VIVO.last_sync_at }),
    "/open-finance/1": () => resposta(200, { connections: bancos }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 3, em_uso: bancos.length, pode_adicionar: true, code: null, message: null }),
    "/open-finance/1/connect-token": () => { emitiuToken.soltar(); return resposta(200, { ok: true, accessToken: "token" }); },
    "/open-finance/1/pluggy-item": () => {
      if (!bancos.some((c) => c.id === 1)) return resposta(409, { detail: { code: "OF_ITEM_REMOVED" } });
      const marco = "2026-10-05T13:00:00Z";
      bancos = bancos.map((c) => c.id === 1 ? { ...c, reconnected_at: marco, last_sync_at: marco } : c);
      return resposta(200, { connections: bancos });
    },
    "/open-finance/1/connections/1": async () => { if (pausaRemocao) await pausaRemocao; if (esperarToken) await emitiuToken.promessa; bancos = bancos.filter((c) => c.id !== 1); return resposta(200, { ok: true, deleted: 1 }); },
    "/open-finance/1/connections/2": async () => { if (pausaRemocao) await pausaRemocao; if (esperarToken) await emitiuToken.promessa; bancos = bancos.filter((c) => c.id !== 2); return resposta(200, { ok: true, deleted: 1 }); },
  });
}
const deletes = () => fetchFalso.mock.calls.filter(([u, o]: [string, RequestInit]) => String(u).includes("/connections/") && o.method === "DELETE").map(([u]: [string, RequestInit]) => ({ caminho: String(u).replace(/^https?:\/\/[^/]+/, "") }));
const tokens = () => chamadas().filter((c) => c.caminho.endsWith("connect-token"));
type Confirmacao = NonNullable<Parameters<typeof Alert.alert>[2]>[number]["onPress"];
let confirmar: Confirmacao;
function alerta(confirmarAgora = false) {
  return jest.spyOn(Alert, "alert").mockImplementation((_title, _message, botoes) => {
    confirmar = botoes?.find((b) => b.style === "destructive")?.onPress;
    if (confirmarAgora) confirmar?.();
  });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); mockWidget.props = null; confirmar = undefined; await guardarSessaoOf(S); cofre.delete("pb.of.tentativa"); servidor(); });
afterEach(() => jest.restoreAllMocks());

it.each(["Reconectar Nubank", "Conectar outro banco"])("%s antes de Desconectar no mesmo frame não abre Alert nem inicia DELETE", async (autorizar) => {
  servidor(true); const alert = alerta(true);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: autorizar })).toBeEnabled());
  const a = screen.getByRole("button", { name: autorizar });
  const r = screen.getByRole("button", { name: "Desconectar Nubank" });
  await act(async () => { fireEvent.press(a); fireEvent.press(r); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("widget-remocao-entrada")).toBeTruthy());
  await act(async () => { await drenar(); });
  expect(deletes()).toEqual([]);
  expect(alert).not.toHaveBeenCalled();
  expect(tokens()).toHaveLength(1);
  expect(await lerTentativaBancaria(1)).not.toBeNull();
});

it.each(["Nubank", "Itaú"])("Alert %s aberto antes da autorização não executa confirmação tardia enquanto entrada reservada", async (banco) => {
  servidor(true); alerta();
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Reconectar Nubank" })).toBeEnabled());
  await act(async () => {
    fireEvent.press(screen.getByRole("button", { name: `Desconectar ${banco}` }));
    fireEvent.press(screen.getByRole("button", { name: "Reconectar Nubank" }));
    await drenar();
  });
  await waitFor(() => expect(screen.getByTestId("widget-remocao-entrada")).toBeTruthy());
  const tentativa = await lerTentativaBancaria(1);
  expect(confirmar).toBeDefined();
  await act(async () => { confirmar?.(); await drenar(); });
  expect(deletes()).toEqual([]);
  expect(await lerTentativaBancaria(1)).toEqual(tentativa);
  await act(async () => { mockWidget.props!.onSuccess({ item: { id: VIVO.provider_item_id } }); await drenar(); });
  await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
});

it("remoção livre mantém DELETE individual e libera demais bancos", async () => {
  alerta(true); renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Desconectar Nubank" })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Desconectar Nubank" })).toBeNull());
  expect(deletes().map((c) => c.caminho)).toEqual(["/open-finance/1/connections/1"]);
  expect(screen.getByRole("button", { name: "Desconectar Itaú" })).toBeEnabled();
  expect(tokens()).toHaveLength(0);
});

it("voltar da autorização libera nova confirmação de remoção", async () => {
  alerta(true); renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Reconectar Nubank" })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Reconectar Nubank" })); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("widget-remocao-entrada")).toBeTruthy());
  await act(async () => { router.back(); await drenar(); });
  await waitFor(() => expect(screen.getByRole("button", { name: "Desconectar Nubank" })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Desconectar Nubank" })).toBeNull());
  expect(deletes()).toHaveLength(1);
  expect(tokens()).toHaveLength(1);
});

it("DELETE em voo continua bloqueando autorização e libera ações ao terminar", async () => {
  const pausa = segurar(); servidor(false, pausa.promessa); alerta(true);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Reconectar Nubank" })).toBeEnabled());
  const a = screen.getByRole("button", { name: "Reconectar Nubank" });
  const r = screen.getByRole("button", { name: "Desconectar Nubank" });
  await act(async () => { fireEvent.press(r); fireEvent.press(a); await drenar(); });
  expect(screen).toHavePathname("/conexoes");
  expect(tokens()).toHaveLength(0);
  expect(deletes()).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Conectar outro banco" })).toBeDisabled();
  await act(async () => { pausa.soltar(); await drenar(); });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Desconectar Nubank" })).toBeNull());
  expect(screen.getByRole("button", { name: "Reconectar Itaú" })).toBeEnabled();
});

it("confirmação de Alert da montagem substituída não inicia DELETE", async () => {
  alerta(); renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Desconectar Nubank" })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
  const antiga = confirmar;
  expect(antiga).toBeDefined();
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Voltar ao Início" })); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname("/resumo"));
  await act(async () => { antiga?.(); await drenar(); });
  expect(deletes()).toHaveLength(0);
});

// Retém só a navegação para inspecionar origem/refresh ainda focados. Os casos
// adversariais acima provam a execução pelo Router real, sem esse spy.
it("reserva desativa ambos Desconectar e refresh não libera a entrada", async () => {
  const alert = alerta(); renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Conectar outro banco" })).toBeEnabled());
  jest.spyOn(router, "push").mockImplementation(() => {});
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Conectar outro banco" })); await drenar(); });
  expect(screen.getByRole("button", { name: "Desconectar Nubank" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Desconectar Itaú" })).toBeDisabled();
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Conferir de novo" })); await drenar(); });
  expect(screen.getByRole("button", { name: "Desconectar Nubank" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Desconectar Itaú" })).toBeDisabled();
  expect(alert).not.toHaveBeenCalled();
});
