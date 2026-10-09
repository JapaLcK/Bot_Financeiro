import { origemDaTentativa } from "./open_finance_volta_apoio";
import { act, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { widgetAberto } from "@/features/openFinance/volta";
import { iniciarTentativaBancaria, lerTentativaBancaria } from "@/storage/secure";
import { redirectSystemPath } from "../../app/+native-intent";
import { cofre, chamadas, prepararCaso, resposta, rotear, S } from "./auth_apoio";
import { guardarSessaoOf, ITEM, SESSAO_OF, VIVO } from "./open_finance_volta_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

type Props = { forceOauthInBrowser: boolean; products?: string[]; onSuccess: (d: { item: { id: string } }) => void; onClose: () => void; onError: () => void };
const mockWidget: { props: Props | null } = { props: null };
jest.mock("react-native-pluggy-connect", () => ({
  PluggyConnect: (props: Props) => {
    mockWidget.props = props;
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("react").createElement(require("react-native").View, { testID: "widget-pluggy" });
  },
}));
beforeEach(async () => { prepararCaso(); desligarTrava(); mockWidget.props = null; await guardarSessaoOf(S); cofre.delete("pb.of.tentativa"); });
function servidor(acesso = true, products?: string[] | null) {
  let registrado = false;
  rotear({ "/auth/me": () => resposta(200, { user_id: 1, app_access: acesso, display_name: "Ana" }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 2, em_uso: 0, pode_adicionar: true, code: null, message: null }),
    "/open-finance/1": () => resposta(200, { connections: registrado ? [VIVO] : [] }),
    "/open-finance/1/connect-token": () => resposta(200, { ok: true, accessToken: "token", ...(products !== undefined && { products }) }),
    "/open-finance/1/pluggy-item": () => { registrado = true; return resposta(200, { connections: [VIVO] }); },
  });
}
it("widget oficial abre após marcador, usa OAuth navegador e onSuccess confirma no servidor", async () => {
  servidor(); renderRouter("./app", { initialUrl: "/autorizando" });
  await waitFor(() => expect(screen.getByTestId("widget-pluggy")).toBeTruthy());
  expect(mockWidget.props!.forceOauthInBrowser).toBe(true);
  expect(widgetAberto()).toBe(true);
  expect(await lerTentativaBancaria(1)).not.toBeNull();
  await act(async () => { mockWidget.props!.onSuccess({ item: { id: ITEM } }); await drenar(); });
  await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
  expect(chamadas().filter((v) => v.caminho.endsWith("pluggy-item"))).toHaveLength(1);
  expect(widgetAberto()).toBe(false);
});
it("products do servidor chegam ao widget", async () => {
  // fora do default de pluggy_products(): pega cópia/filtro local da lista no app
  const products = ["ACCOUNTS", "IDENTITY"];
  servidor(true, products); renderRouter("./app", { initialUrl: "/autorizando" });
  await waitFor(() => expect(screen.getByTestId("widget-pluggy")).toBeTruthy());
  expect(mockWidget.props!.products).toEqual(products);
});
it("products null do servidor: o widget abre e não recebe o campo (a lib poria products=null na URL)", async () => {
  servidor(true, null); renderRouter("./app", { initialUrl: "/autorizando" });
  await waitFor(() => expect(screen.getByTestId("widget-pluggy")).toBeTruthy());
  expect(mockWidget.props!.products).toBeUndefined();
});
it("callback capturado com widget aberto recupera onClose sem onSuccess; callbacks duplicados não repetem POST", async () => {
  servidor(); renderRouter("./app", { initialUrl: "/autorizando" });
  await waitFor(() => expect(screen.getByTestId("widget-pluggy")).toBeTruthy());
  expect(redirectSystemPath({ path: `pigbank://open-finance-volta/${origemDaTentativa()}?itemId=${ITEM}`, initial: false })).toBeNull();
  expect(await lerTentativaBancaria(1)).toMatchObject({ item_id: ITEM });
  await act(async () => { mockWidget.props!.onClose(); mockWidget.props!.onError(); await drenar(); });
  await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
  expect(chamadas().filter((v) => v.caminho.endsWith("pluggy-item"))).toHaveLength(1);
});
it("deep link direto para autorizando não abre widget sem app_access", async () => {
  servidor(false); renderRouter("./app", { initialUrl: "/autorizando" });
  await waitFor(() => expect(screen.getByText("Não conseguimos iniciar a conexão. Tente de novo.")).toBeTruthy());
  expect(screen.queryByTestId("widget-pluggy")).toBeNull();
  expect(chamadas().some((v) => v.caminho.endsWith("connect-token"))).toBe(false);
});
it("sucesso tardio de widget fechado não captura item na nova tentativa da mesma sessão", async () => {
  servidor(); renderRouter("./app", { initialUrl: "/autorizando" });
  await waitFor(() => expect(screen.getByTestId("widget-pluggy")).toBeTruthy());
  const antigo = mockWidget.props!;
  await act(async () => { antigo.onClose(); await drenar(); });
  const nova = await iniciarTentativaBancaria(1, SESSAO_OF, [], undefined, undefined, (await lerTentativaBancaria(1)) ?? undefined);
  await act(async () => { antigo.onSuccess({ item: { id: ITEM } }); await drenar(); });
  expect(await lerTentativaBancaria(1)).toEqual(nova);
  expect(chamadas().some((v) => v.caminho.endsWith("pluggy-item"))).toBe(false);
});
