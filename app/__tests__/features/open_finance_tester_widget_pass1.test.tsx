import { act, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { lerTentativaBancaria } from "@/storage/secure";
import { cofre, prepararCaso, resposta, rotear, S } from "./auth_apoio";
import { guardarSessaoOf, ITEM } from "./open_finance_volta_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

type Props = { onSuccess: (d: { item: { id: string } }) => void; onClose: () => void; onError: (d: { message: string; data?: { item: { id: string } } }) => void };
const mockWidget: { props: Props | null } = { props: null };
jest.mock("react-native-pluggy-connect", () => ({
  PluggyConnect: (props: Props) => {
    mockWidget.props = props;
    return jest.requireActual("react").createElement(jest.requireActual("react-native").View, { testID: "widget-tester" });
  },
}));
beforeEach(async () => { prepararCaso(); desligarTrava(); mockWidget.props = null; await guardarSessaoOf(S); cofre.delete("pb.of.tentativa"); });
function servidor() {
  rotear({ "/auth/me": () => resposta(200, { user_id: 1, app_access: true, display_name: "Ana" }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 2, em_uso: 0, pode_adicionar: true, code: null, message: null }),
    "/open-finance/1": () => resposta(200, { connections: [] }),
    "/open-finance/1/connect-token": () => resposta(200, { ok: true, accessToken: "token" }),
    "/open-finance/1/pluggy-item": () => resposta(200, { connections: [] }),
  });
}
it("onError com item preserva a única pista da autorização para recuperação", async () => {
  servidor(); renderRouter("./app", { initialUrl: "/autorizando" });
  await waitFor(() => expect(screen.getByTestId("widget-tester")).toBeTruthy());
  await act(async () => { mockWidget.props!.onError({ message: "login incomplete", data: { item: { id: ITEM } } }); await drenar(); });
  expect(await lerTentativaBancaria(1)).toMatchObject({ item_id: ITEM });
});
it("onClose seguido de onSuccess guarda o id mesmo sem reabrir a tela duas vezes", async () => {
  servidor(); renderRouter("./app", { initialUrl: "/autorizando" });
  await waitFor(() => expect(screen.getByTestId("widget-tester")).toBeTruthy());
  const callbacks = mockWidget.props!;
  await act(async () => { callbacks.onClose(); callbacks.onSuccess({ item: { id: ITEM } }); await drenar(); });
  expect(await lerTentativaBancaria(1)).toMatchObject({ item_id: ITEM });
});
