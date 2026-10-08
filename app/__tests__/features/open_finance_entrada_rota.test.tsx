import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais, lerTentativaBancaria } from "@/storage/secure";
import { cofre, chamadas, prepararCaso, resposta, rotear, S } from "./auth_apoio";
import { guardarSessaoOf, VIVO } from "./open_finance_volta_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

type Props = { connectToken: string; updateItem?: string; onSuccess: (d: { item: { id: string } }) => void; onClose: () => void };
const mockWidgets: Props[] = [];
jest.mock("react-native-pluggy-connect", () => ({ PluggyConnect: (p: Props) => {
  mockWidgets.push(p);
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require("react").createElement(require("react-native").View, { testID: "widget-entrada" });
} }));
const bancos = [VIVO, { ...VIVO, id: 2, provider_item_id: "banco_b", institution_name: "Itaú" }];
function servidor(falharToken = false, atualizando = false) {
  let tokens = 0;
  rotear({
    "/auth/me": () => resposta(200, { user_id: 1, display_name: "Ana", app_access: true }),
    "/onboarding/open-finance": () => resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" }),
    "/open-finance/1": () => resposta(200, { connections: atualizando ? [{ ...VIVO, ui: { state: "updating", label: "Atualizando…", detail: null } }, bancos[1]] : bancos }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 3, em_uso: 2, pode_adicionar: true, code: null, message: null }),
    "/open-finance/1/connect-token": () => falharToken ? resposta(503, {}) : resposta(200, { ok: true, accessToken: `token_${++tokens}` }),
    "/open-finance/1/pluggy-item": () => resposta(200, { connections: [...bancos, { ...VIVO, id: 3, provider_item_id: "banco_novo" }] }),
  });
}
const tokens = () => chamadas().filter((c) => c.caminho.endsWith("connect-token"));
beforeEach(async () => { prepararCaso(); desligarTrava(); mockWidgets.length = 0; await guardarSessaoOf(S); cofre.delete("pb.of.tentativa"); servidor(); });
afterEach(() => jest.restoreAllMocks());
it.each([
  ["/conectar-banco", "Conectar meu banco", "Conectar meu banco"],
  ["/conexoes", "Conectar outro banco", "Conectar outro banco"],
  ["/conexoes", "Reconectar Nubank", "Reconectar Itaú"],
  ["/conexoes", "Reconectar Nubank", "Conectar outro banco"],
])("Expo Router real: %s, %s e %s no mesmo frame emitem um token", async (url, primeiro, segundo) => {
  renderRouter("./app", { initialUrl: url });
  await waitFor(() => expect(screen.getByRole("button", { name: primeiro })).toBeTruthy());
  const a = screen.getByRole("button", { name: primeiro });
  const b = screen.getByRole("button", { name: segundo });
  await act(async () => { fireEvent.press(a); fireEvent.press(b); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("widget-entrada")).toBeTruthy());
  await act(async () => { await drenar(); });
  expect(screen).toHavePathname("/autorizando");
  expect(tokens()).toHaveLength(1);
  expect(new Set(mockWidgets.map((w) => w.connectToken))).toEqual(new Set(["token_1"]));
  expect(await lerTentativaBancaria(1)).not.toBeNull();
});

it.each(["/conectar-banco", "/conexoes"])("%s libera entrada ao voltar e callback A não sobrescreve novo B", async (url) => {
  const rotulo = url === "/conectar-banco" ? "Conectar meu banco" : "Conectar outro banco";
  renderRouter("./app", { initialUrl: url });
  await waitFor(() => expect(screen.getByRole("button", { name: rotulo })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: rotulo })); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("widget-entrada")).toBeTruthy());
  const a = mockWidgets.find((w) => w.connectToken === "token_1")!;
  const tentativaA = (await lerTentativaBancaria(1))!;
  await act(async () => { router.back(); await drenar(); });
  await waitFor(() => expect(screen.getByRole("button", { name: rotulo })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: rotulo })); await drenar(); });
  await waitFor(() => expect(screen.getByRole("button", { name: "Iniciar nova tentativa" })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Iniciar nova tentativa" })); await drenar(); });
  await waitFor(() => expect(mockWidgets.some((w) => w.connectToken === "token_2")).toBe(true));
  const b = mockWidgets.find((w) => w.connectToken === "token_2")!;
  const tentativaB = (await lerTentativaBancaria(1))!;
  expect(tentativaB.tentativa_id).not.toBe(tentativaA.tentativa_id);
  await act(async () => { a.onSuccess({ item: { id: "item_antigo_a" } }); await drenar(); });
  expect(await lerTentativaBancaria(1)).toEqual(tentativaB);
  await act(async () => { b.onSuccess({ item: { id: "banco_novo" } }); await drenar(); });
  await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
  expect(chamadas().filter((c) => c.caminho.endsWith("pluggy-item")).map((c) => c.corpo)).toEqual([{ item: { id: "banco_novo" } }]);
});

it.each(["/conectar-banco", "/conexoes"])("%s volta de erro de token e permite nova tentativa", async (url) => {
  const rotulo = url === "/conectar-banco" ? "Conectar meu banco" : "Conectar outro banco";
  servidor(true);
  renderRouter("./app", { initialUrl: url });
  await waitFor(() => expect(screen.getByRole("button", { name: rotulo })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: rotulo })); await drenar(); });
  await waitFor(() => expect(screen.getByRole("button", { name: "Voltar e tentar de novo" })).toBeTruthy());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Voltar e tentar de novo" })); await drenar(); });
  await waitFor(() => expect(screen.getByRole("button", { name: rotulo })).toBeEnabled());
  servidor();
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: rotulo })); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("widget-entrada")).toBeTruthy());
  expect(tokens()).toHaveLength(2);
});

it("nova sessão após sair da autorização não herda reserva ou callback da sessão anterior", async () => {
  renderRouter("./app", { initialUrl: "/conectar-banco" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Conectar meu banco" })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Conectar meu banco" })); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("widget-entrada")).toBeTruthy());
  const a = mockWidgets.find((w) => w.connectToken === "token_1")!;
  await act(async () => {
    await guardarCredenciais({ access: "eyJhbGciOiJIUzI1NiJ9.eyJqdGkiOiJzZXNzYW8tYiJ9.assinatura", refresh: "r-b" });
    router.back(); await drenar();
  });
  await waitFor(() => expect(screen.getByRole("button", { name: "Conectar meu banco" })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Conectar meu banco" })); await drenar(); });
  await waitFor(() => expect(mockWidgets.some((w) => w.connectToken === "token_2")).toBe(true));
  const b = await lerTentativaBancaria(1);
  expect(b).toMatchObject({ sessao: "sessao-b" });
  await act(async () => { a.onSuccess({ item: { id: "item_antigo_a" } }); await drenar(); });
  expect(await lerTentativaBancaria(1)).toEqual(b);
});

// Aqui o push é retido só para inspecionar o visual ainda na origem. Os casos
// de dupla emissão acima usam Expo Router/Stack reais, sem spy de navegação.
it.each(["/conectar-banco", "/conexoes"])("%s desativa visualmente todos os CTAs de autorização da origem", async (url) => {
  servidor(false, true);
  const rotulos = url === "/conectar-banco" ? ["Conectar meu banco", "Ver meus bancos", "Configurações", "Continuar para o Início"] : ["Reconectar Nubank", "Reconectar Itaú", "Conectar outro banco", "Acompanhar sincronização", "Voltar ao Início"];
  renderRouter("./app", { initialUrl: url });
  await waitFor(() => expect(screen.getByRole("button", { name: rotulos[0] })).toBeEnabled());
  const push = jest.spyOn(router, "push").mockImplementation(() => {});
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: rotulos[0] })); await drenar(); });
  for (const name of rotulos) expect(screen.getByRole("button", { name })).toBeDisabled();
  expect(push).toHaveBeenCalledTimes(1);
});

it.each(["/conectar-banco", "/conexoes"])("%s libera reserva se a navegação falhar e permite push real seguinte", async (url) => {
  const rotulo = url === "/conectar-banco" ? "Conectar meu banco" : "Conectar outro banco";
  renderRouter("./app", { initialUrl: url });
  await waitFor(() => expect(screen.getByRole("button", { name: rotulo })).toBeEnabled());
  const push = jest.spyOn(router, "push").mockImplementationOnce(() => { throw new Error("navegação indisponível"); });
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: rotulo })); await drenar(); });
  expect(screen.getByText("Não conseguimos abrir a autorização. Tente de novo.")).toBeTruthy();
  expect(screen.getByRole("button", { name: rotulo })).toBeEnabled();
  push.mockRestore();
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: rotulo })); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("widget-entrada")).toBeTruthy());
  expect(tokens()).toHaveLength(1);
});

it("entrada reservada não permite salto para Bancos que abra segunda autorização oculta", async () => {
  renderRouter("./app", { initialUrl: "/conectar-banco" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Conectar meu banco" })).toBeEnabled());
  const conectar = screen.getByRole("button", { name: "Conectar meu banco" });
  const bancos = screen.getByRole("button", { name: "Ver meus bancos" });
  await act(async () => { fireEvent.press(conectar); fireEvent.press(bancos); await drenar(); });
  await waitFor(() => expect(screen.queryByRole("button", { name: "Conectar outro banco" }) || screen.queryByTestId("widget-entrada")).toBeTruthy());
  // Se a navegação irmã escapar, o usuário consegue abrir outro widget enquanto
  // a primeira Autorizando já montada continua preparando token em segundo plano.
  const outro = screen.queryByRole("button", { name: "Conectar outro banco" });
  if (outro) await act(async () => { fireEvent.press(outro); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("widget-entrada")).toBeTruthy());
  await act(async () => { await drenar(); });
  expect(tokens()).toHaveLength(1);
  expect(screen).toHavePathname("/autorizando");
});

const navegacoesIrmas = [
  ["/conectar-banco", "Conectar meu banco", "Ver meus bancos", "/conexoes"],
  ["/conectar-banco", "Conectar meu banco", "Configurações", "/configuracoes"],
  ["/conectar-banco", "Conectar meu banco", "Continuar para o Início", "/resumo"],
  ["/conexoes", "Conectar outro banco", "Voltar ao Início", "/resumo"],
  ["/conexoes", "Conectar outro banco", "Acompanhar sincronização", "/open-finance-volta"],
];
it.each(navegacoesIrmas)("reserva em %s por %s bloqueia %s antes do render", async (url, autorizar, navegar) => {
  servidor(false, true);
  renderRouter("./app", { initialUrl: url });
  await waitFor(() => expect(screen.getByRole("button", { name: autorizar })).toBeEnabled());
  const a = screen.getByRole("button", { name: autorizar });
  const n = screen.getByRole("button", { name: navegar });
  await act(async () => { fireEvent.press(a); fireEvent.press(n); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("widget-entrada")).toBeTruthy());
  expect(screen).toHavePathname("/autorizando");
  expect(tokens()).toHaveLength(1);
});
it.each(navegacoesIrmas)("origem livre %s (CTA %s) mantém %s para %s", async (url, _autorizar, navegar, destino) => {
  servidor(false, true);
  renderRouter("./app", { initialUrl: url });
  await waitFor(() => expect(screen.getByRole("button", { name: navegar })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: navegar })); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname(destino));
  expect(tokens()).toHaveLength(0);
});
