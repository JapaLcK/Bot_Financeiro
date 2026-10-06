import { act, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { lerTentativaBancaria } from "@/storage/secure";
import { cofre, chamadas, prepararCaso, resposta, rotear, S } from "./auth_apoio";
import { guardarSessaoOf, ITEM, VIVO } from "./open_finance_volta_apoio";
import { desligarTrava, drenar, umIntervalo } from "./open_finance_volta_rota_apoio";

type Props = { onSuccess: (d: { item: { id: string } }) => void; onClose: () => void; onError: (d?: { message: string; data?: { item: { id: string } } }) => void };
const mockWidget: { props: Props | null } = { props: null };
jest.mock("react-native-pluggy-connect", () => ({ PluggyConnect: (props: Props) => {
  mockWidget.props = props;
  return jest.requireActual("react").createElement(jest.requireActual("react-native").View, { testID: "widget-pass2" });
} }));
beforeEach(async () => { prepararCaso(); desligarTrava(); mockWidget.props = null; await guardarSessaoOf(S); cofre.delete("pb.of.tentativa"); });
function servidor() {
  let registrado = false;
  rotear({ "/auth/me": () => resposta(200, { user_id: 1, app_access: true, display_name: "Ana" }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 2, em_uso: 0, pode_adicionar: true, code: null, message: null }),
    "/open-finance/1": () => resposta(200, { connections: registrado ? [VIVO] : [] }),
    "/open-finance/1/connect-token": () => resposta(200, { ok: true, accessToken: "token" }),
    "/open-finance/1/pluggy-item": () => { registrado = true; return resposta(200, { connections: [VIVO] }); },
  });
}
const posts = () => chamadas().filter((v) => v.caminho.endsWith("/pluggy-item"));

it("onSuccess depois da montagem do retorno sem item retoma o registro automaticamente", async () => {
  servidor(); renderRouter("./app", { initialUrl: "/autorizando" });
  await waitFor(() => expect(screen.getByTestId("widget-pass2")).toBeTruthy());
  const callbacks = mockWidget.props!;
  await act(async () => { callbacks.onClose(); await drenar(); });
  await waitFor(() => expect(screen.getByText("Estamos conferindo com o banco.")).toBeTruthy());
  expect(chamadas().filter((v) => v.caminho === "/open-finance/1").length).toBeGreaterThanOrEqual(2);
  await act(async () => { callbacks.onSuccess({ item: { id: ITEM } }); await drenar(); });
  expect(await lerTentativaBancaria(1)).toMatchObject({ item_id: ITEM });
  await umIntervalo();
  expect(posts()).toHaveLength(1);
  await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
});

it("controle positivo: erro com item seguido de callbacks duplicados confirma uma vez", async () => {
  servidor(); renderRouter("./app", { initialUrl: "/autorizando" });
  await waitFor(() => expect(screen.getByTestId("widget-pass2")).toBeTruthy());
  const callbacks = mockWidget.props!;
  await act(async () => {
    callbacks.onError({ message: "cancelado", data: { item: { id: ITEM } } });
    callbacks.onSuccess({ item: { id: ITEM } }); callbacks.onClose();
    await drenar();
  });
  await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
  expect(posts()).toHaveLength(1);
});

it.each([undefined, { message: "cancelado" }, { message: "cancelado", data: { item: { id: "💰" } } }])("erro sem id válido %p mantém tentativa recuperável sem POST", async (erro) => {
  servidor(); renderRouter("./app", { initialUrl: "/autorizando" });
  await waitFor(() => expect(screen.getByTestId("widget-pass2")).toBeTruthy());
  await act(async () => { mockWidget.props!.onError(erro); await drenar(); });
  await waitFor(() => expect(screen.getByText("Estamos conferindo com o banco.")).toBeTruthy());
  expect(posts()).toHaveLength(0);
  expect(await lerTentativaBancaria(1)).not.toBeNull();
});
