import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import type { Conexao } from "@/api/schemas/openFinance";
import { JANELA_MS } from "@/features/openFinance/volta";
import { iniciarTentativaBancaria, lerTentativaBancaria } from "@/storage/secure";
import { chamadas, cofre, prepararCaso, resposta, rotear, S } from "./auth_apoio";
import { guardarSessaoOf, SESSAO_OF, VIVO } from "./open_finance_volta_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

type Props = { onSuccess: (d: { item: { id: string } }) => void; onClose: () => void };
const mockWidget: { props: Props | null } = { props: null };
jest.mock("react-native-pluggy-connect", () => ({ PluggyConnect: (props: Props) => {
  mockWidget.props = props;
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require("react").createElement(require("react-native").View, { testID: "widget-pilha" });
} }));
function servidor(completo = true) {
  let sincronizacoes = 0;
  const estado = { completo, acesso: true, bancos: completo ? [VIVO] : [] as Conexao[], janela: false, atualizando: false };
  rotear({
    "/auth/me": () => resposta(200, { user_id: 1, display_name: "Ana", app_access: estado.acesso, of_banks_max: 3 }),
    "/onboarding/open-finance": () => resposta(200, { ok: true, completed: estado.completo, completed_at: estado.completo ? VIVO.last_sync_at : null }),
    "/open-finance/1": () => { if (estado.janela) jest.setSystemTime(Date.now() + JANELA_MS); return resposta(200, { connections: estado.bancos }); },
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 3, em_uso: estado.bancos.length, pode_adicionar: true, code: null, message: null }),
    "/open-finance/1/connect-token": () => resposta(200, { ok: true, accessToken: "token" }),
    "/open-finance/1/pluggy-item": () => {
      const marco = new Date(Date.now() + 1000 * ++sincronizacoes).toISOString();
      estado.bancos = [{ ...VIVO, reconnected_at: marco, last_sync_at: estado.atualizando ? null : marco, ui: estado.atualizando ? { state: "updating", label: "Atualizando…", detail: null } : VIVO.ui }];
      if (!estado.atualizando) estado.completo = true;
      return resposta(200, { connections: estado.bancos });
    },
  });
  return estado;
}
const tokens = () => chamadas().filter((c) => c.caminho.endsWith("connect-token"));
async function apertar(nome: string) {
  await waitFor(() => expect(screen.getByRole("button", { name: nome })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); });
}
async function autorizar(nome: string) {
  await apertar(nome);
  await waitFor(() => expect(screen.getByTestId("widget-pilha")).toBeTruthy());
  const tentativa = await lerTentativaBancaria(1);
  expect(tokens().at(-1)?.corpo).toEqual(expect.objectContaining({ attempt_id: tentativa?.tentativa_id }));
  await act(async () => { mockWidget.props!.onSuccess({ item: { id: VIVO.provider_item_id } }); await drenar(); });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); mockWidget.props = null; await guardarSessaoOf(S); cofre.delete("pb.of.tentativa"); });
afterEach(() => jest.restoreAllMocks());

it("primeira conexão volta à Home existente; back e repetição não reabrem autorização", async () => {
  servidor(false); const r = renderRouter("./app", { initialUrl: "/" });
  await apertar("Conectar meu banco");
  await autorizar("Conectar meu banco");
  await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
  await apertar("Continuar");
  await waitFor(() => expect(screen).toHavePathname("/"));
  expect(router.canGoBack()).toBe(false);
  expect(r.getRouterState()?.routes[0]?.state?.routes.find((route) => route.name === "(app)")?.state?.routes.map((route) => route.name)).toEqual(["index"]);
  await apertar("Bancos conectados");
  await autorizar("Reconectar Nubank");
  await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
  await apertar("Continuar");
  expect(router.canGoBack()).toBe(false);
  // Sem canGoBack, o navigator não oferece gesto Back na Home.
  expect(screen).toHavePathname("/");
  expect(tokens()).toHaveLength(2);
});

it.each(["conectar-banco", "conexoes"])("retorno explícito de %s não duplica Home nem deixa origem no histórico", async (destino) => {
  servidor(); renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Bancos conectados" })).toBeEnabled());
  await act(async () => { router.push(`/${destino}`); await drenar(); });
  await apertar(destino === "conexoes" ? "Voltar ao Início" : "Continuar para o Início");
  expect(screen).toHavePathname("/");
  expect(router.canGoBack()).toBe(false);
  expect(tokens()).toHaveLength(0);
});

it("timeout com Conexões existente retorna à mesma pilha e back vai à Home", async () => {
  const e = servidor(); renderRouter("./app", { initialUrl: "/" });
  await apertar("Bancos conectados"); await apertar("Conectar outro banco");
  await waitFor(() => expect(screen.getByTestId("widget-pilha")).toBeTruthy());
  e.janela = true;
  await act(async () => { mockWidget.props!.onClose(); await drenar(); });
  await apertar("Ver bancos conectados");
  expect(screen).toHavePathname("/conexoes");
  expect(router.canGoBack()).toBe(true);
  await act(async () => { router.back(); await drenar(); });
  expect(screen).toHavePathname("/");
  expect(router.canGoBack()).toBe(false);
  expect(tokens()).toHaveLength(1);
});

it("Sair enquanto coleta roda encerra pilha e mantém tentativa para Retomar", async () => {
  const e = servidor(false); renderRouter("./app", { initialUrl: "/" });
  await apertar("Conectar meu banco"); e.atualizando = true; await autorizar("Conectar meu banco");
  await apertar("Sair");
  expect(screen).toHavePathname("/");
  expect(router.canGoBack()).toBe(false);
  await waitFor(() => expect(screen.getByRole("button", { name: "Retomar conexão" })).toBeTruthy());
  expect(await lerTentativaBancaria(1)).not.toBeNull();
  expect(tokens()).toHaveLength(1);
});

it("retorno frio revalida entitlement sem conceder Início nem deixar Volta no back", async () => {
  const e = servidor(); renderRouter("./app", { initialUrl: "/open-finance-volta" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Continuar" })).toBeTruthy());
  e.acesso = false;
  await apertar("Continuar");
  await waitFor(() => expect(screen.getByText("Sua conta ainda não tem acesso ao app. A contratação pelo iPhone chegará em uma próxima atualização.")).toBeTruthy());
  expect(screen).toHavePathname("/");
  expect(router.canGoBack()).toBe(false);
  expect(screen.queryByRole("button", { name: "Bancos conectados" })).toBeNull();
  expect(tokens()).toHaveLength(0);
});

it("back de widget continua permitindo tentar de novo com nonce novo", async () => {
  servidor(); renderRouter("./app", { initialUrl: "/conexoes" });
  await apertar("Reconectar Nubank");
  await waitFor(() => expect(screen.getByTestId("widget-pilha")).toBeTruthy());
  const a = await lerTentativaBancaria(1);
  await act(async () => { router.back(); await drenar(); });
  await apertar("Reconectar Nubank");
  await autorizar("Iniciar nova tentativa");
  await waitFor(() => expect(screen.getByText("Atualizado")).toBeTruthy());
  expect(tokens()).toHaveLength(2);
  expect(tokens()[1]?.corpo).not.toEqual(expect.objectContaining({ attempt_id: a?.tentativa_id }));
});

it("retorno frio para bancos ausentes da pilha usa fallback sem ressuscitar Volta", async () => {
  const e = servidor(); e.janela = true;
  const tentativa = await iniciarTentativaBancaria(1, SESSAO_OF, []);
  renderRouter("./app", { initialUrl: "/open-finance-volta" });
  await apertar("Ver bancos conectados");
  expect(screen).toHavePathname("/conexoes");
  expect(router.canGoBack()).toBe(true);
  expect(await lerTentativaBancaria(1)).toEqual(tentativa);
  await act(async () => { router.back(); await drenar(); });
  expect(screen).toHavePathname("/");
  expect(router.canGoBack()).toBe(false);
  expect(tokens()).toHaveLength(0);
});

it("timeout da primeira autorização sem Conexões prévia não deixa Conectar sob Bancos", async () => {
  const e = servidor(false); renderRouter("./app", { initialUrl: "/" });
  await apertar("Conectar meu banco"); await apertar("Conectar meu banco");
  await waitFor(() => expect(screen.getByTestId("widget-pilha")).toBeTruthy());
  e.bancos = [VIVO]; e.janela = true;
  await act(async () => { mockWidget.props!.onClose(); await drenar(); });
  await apertar("Ver bancos conectados");
  expect(screen).toHavePathname("/conexoes");
  await act(async () => { router.back(); await drenar(); });
  expect(screen).toHavePathname("/");
  expect(router.canGoBack()).toBe(false);
  expect(tokens()).toHaveLength(1);
});
