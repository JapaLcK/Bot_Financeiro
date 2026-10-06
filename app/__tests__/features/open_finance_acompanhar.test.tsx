import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { definirWidgetAberto } from "@/features/openFinance/volta";
import type { Conexao } from "@/api/schemas/openFinance";
import { capturarItemBancario, lerTentativaBancaria } from "@/storage/secure";
import { redirectSystemPath } from "../../app/+native-intent";
import { chamadas, prepararCaso, resposta, rotear, S } from "./auth_apoio";
import { guardarSessaoOf, VIVO } from "./open_finance_volta_apoio";
import { desligarTrava, drenar, umIntervalo } from "./open_finance_volta_rota_apoio";

const B = "banco_b";
function servidor() {
  const e = { bancos: [{ ...VIVO, ui: { ...VIVO.ui, label: "Banco A atualizado" } }, { ...VIVO, id: 2, provider_item_id: B, institution_name: "Itaú", ui: { state: "updating", label: "Banco B atualizando", detail: null } }] as Conexao[], acesso: true };
  rotear({
    "/auth/me": () => resposta(200, { user_id: 1, display_name: "Ana", app_access: e.acesso }),
    "/onboarding/open-finance": () => resposta(200, { ok: true, completed: true, completed_at: VIVO.last_sync_at }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 3, em_uso: 2, pode_adicionar: true, code: null, message: null }),
    "/open-finance/1": () => resposta(200, { connections: e.bancos }),
    "/open-finance/1/pluggy-item": () => resposta(200, { connections: e.bancos }),
  });
  return e;
}
const posts = () => chamadas().filter((c) => c.caminho.endsWith("pluggy-item"));
async function acompanhar() {
  await waitFor(() => expect(screen.getByRole("button", { name: "Acompanhar sincronização" })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Acompanhar sincronização" })); await drenar(); });
}
// Exercita a implementação RN instalada, não a semântica diferente de Node.
const NativeSearchParams = jest.requireActual("react-native/Libraries/Blob/URLSearchParams").URLSearchParams;
beforeEach(async () => {
  jest.spyOn(globalThis, "URLSearchParams").mockImplementation((entrada) => new NativeSearchParams(entrada));
  prepararCaso(); desligarTrava(); await guardarSessaoOf(S);
});
afterEach(() => jest.restoreAllMocks());

it.each([false, true])("ação real acompanha B mantendo A conhecido=%s e callback A continua recuperável", async (conhecido) => {
  const e = servidor(); const origemA = (await lerTentativaBancaria(1))!.tentativa_id;
  if (conhecido) await capturarItemBancario(VIVO.provider_item_id, origemA);
  const marcadorA = await lerTentativaBancaria(1);
  const r = renderRouter("./app", { initialUrl: "/conexoes" });
  await acompanhar();
  await waitFor(() => expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy());
  expect(r.getSearchParams()).toEqual({ itemId: B, modo: "acompanhar" });
  expect(screen.queryByText("Banco A atualizado")).toBeNull();
  expect(posts()).toHaveLength(0);
  expect(await lerTentativaBancaria(1)).toEqual(marcadorA);
  e.bancos = e.bancos.map((c) => c.provider_item_id === B ? { ...c, ui: { ...c.ui, state: "updated", label: "Banco B atualizado" } } : c);
  await umIntervalo();
  await waitFor(() => expect(screen.getByText("Banco B atualizado")).toBeTruthy());
  expect(await lerTentativaBancaria(1)).toEqual(marcadorA);
  expect(posts()).toHaveLength(0);
  await act(async () => { router.back(); await drenar(); });
  const destinoA = redirectSystemPath({ path: `pigbank://open-finance-volta/${origemA}?itemId=${VIVO.provider_item_id}`, initial: false });
  await act(async () => { router.push(destinoA as "/open-finance-volta"); await drenar(); });
  await waitFor(() => expect(screen.getByText("Banco A atualizado")).toBeTruthy());
  expect(await lerTentativaBancaria(1)).toBeNull();
});

it("URI fria de observação valida B próprio sem capturar nonce A nem user_id da query", async () => {
  const e = servidor(); const a = (await lerTentativaBancaria(1))!;
  e.bancos = e.bancos.map((c) => c.provider_item_id === B ? { ...c, ui: { ...c.ui, state: "updated", label: "Banco B atualizado" } } : c);
  renderRouter("./app", { initialUrl: `/open-finance-volta/${a.tentativa_id}?itemId=${B}&modo=acompanhar&user_id=999` });
  await waitFor(() => expect(screen.getByText("Banco B atualizado")).toBeTruthy());
  expect(await lerTentativaBancaria(1)).toEqual(a);
  expect(posts()).toHaveLength(0);
  expect(chamadas().some((c) => c.caminho.includes("999"))).toBe(false);
});

it.each(["modo", "modo=trusted", "modo=", "modo=acompanhar&modo=acompanhar", "modo=acompanhar&modo=trusted"])("URI fria com %s é fechada sem fallback OAuth A", async (modo) => {
  servidor(); const a = (await lerTentativaBancaria(1))!;
  renderRouter("./app", { initialUrl: `/open-finance-volta/${a.tentativa_id}?itemId=${B}&${modo}` });
  await waitFor(() => expect(screen.getByText("Se você conectou um banco, ele aparece em instantes.")).toBeTruthy());
  expect(await lerTentativaBancaria(1)).toEqual(a);
  expect(chamadas().filter((c) => c.caminho.startsWith("/open-finance/"))).toHaveLength(0);
});

it("back de acompanhamento ainda updating não limpa tentativa A nem solicita token", async () => {
  servidor(); const a = await lerTentativaBancaria(1);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await acompanhar();
  await waitFor(() => expect(screen.getByRole("button", { name: "Sair" })).toBeTruthy());
  await act(async () => { router.back(); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname("/conexoes"));
  await umIntervalo();
  expect(await lerTentativaBancaria(1)).toEqual(a);
  expect(posts()).toHaveLength(0);
  expect(chamadas().filter((c) => c.caminho.endsWith("connect-token"))).toHaveLength(0);
});

it.each(["%6dodo", "mo%64o"])("key codificada %s conserva observação somente leitura pelo Router", async (key) => {
  const e = servidor(); const a = (await lerTentativaBancaria(1))!;
  e.bancos = e.bancos.map((c) => c.provider_item_id === B ? { ...c, ui: { ...c.ui, state: "updated", label: "Banco B atualizado" } } : c);
  renderRouter("./app", { initialUrl: `/open-finance-volta/${a.tentativa_id}?itemId=${B}&${key}=acompanhar` });
  await waitFor(() => expect(screen.getByText("Banco B atualizado")).toBeTruthy());
  expect(await lerTentativaBancaria(1)).toEqual(a);
  expect(posts()).toHaveLength(0);
});

it.each(["modo=acompanhar&%6dodo=acompanhar", "%6dodo=acompanhar&mo%64o=trusted"])("query codificada duplicada %s fecha sem capture/fallback", async (query) => {
  servidor(); const a = (await lerTentativaBancaria(1))!;
  renderRouter("./app", { initialUrl: `/open-finance-volta/${a.tentativa_id}?itemId=${B}&${query}` });
  await waitFor(() => expect(screen.getByText("Se você conectou um banco, ele aparece em instantes.")).toBeTruthy());
  expect(await lerTentativaBancaria(1)).toEqual(a);
  expect(chamadas().filter((c) => c.caminho.startsWith("/open-finance/"))).toHaveLength(0);
});


it.each([
  "modo=acompanhar=extra&itemId=banco_b",
  "modo=acompanhar&itemId=banco_b=extra",
  "%6dodo&itemId=banco_b",
  "mo%6do=acompanhar&itemId=%zz",
  "%6modo=acompanhar&itemId=banco_b",
  "itemId=banco_b&extra=%",
  "itemId=banco_b&%69temId=outro_banco",
])("query inválida %s fecha sem tratar item/mode truncado como válido", async (query) => {
  servidor(); const a = (await lerTentativaBancaria(1))!;
  renderRouter("./app", { initialUrl: `/open-finance-volta/${a.tentativa_id}?${query}` });
  await waitFor(() => expect(screen.getByText("Se você conectou um banco, ele aparece em instantes.")).toBeTruthy());
  expect(await lerTentativaBancaria(1)).toEqual(a);
  expect(chamadas().filter((c) => c.caminho.startsWith("/open-finance/"))).toHaveLength(0);
});

it.each([
  "&note=texto%26modo%3Dacompanhar",
  "#&modo=acompanhar&tentativaId=falsa",
])("%s não cria query fictícia nem remove OAuth UUID legítimo", async (extra) => {
  const e = servidor(); const a = (await lerTentativaBancaria(1))!;
  e.bancos = e.bancos.map((c) => c.provider_item_id === B ? { ...c, ui: { ...c.ui, state: "updated", label: "Banco B atualizado" } } : c);
  renderRouter("./app", { initialUrl: `/open-finance-volta/${a.tentativa_id}?itemId=${B}${extra}` });
  await waitFor(() => expect(screen.getByText("Banco B atualizado")).toBeTruthy());
  expect(await lerTentativaBancaria(1)).toBeNull();
  expect(posts()).toHaveLength(0);
});

it("query de origem codificada também exclui captura pelo UUID do path", async () => {
  servidor(); const a = (await lerTentativaBancaria(1))!;
  const r = renderRouter("./app", { initialUrl: `/open-finance-volta/${a.tentativa_id}?itemId=${B}&tentativa%49d=falsa` });
  await waitFor(() => expect(screen.getByText("Estamos conferindo com o banco.")).toBeTruthy());
  expect(r.getSearchParams()).toEqual({ itemId: B });
  expect(await lerTentativaBancaria(1)).toEqual(a); expect(posts()).toHaveLength(0);
});

it("escape inválido com widget focado não navega nem captura item", async () => {
  const a = (await lerTentativaBancaria(1))!;
  definirWidgetAberto(true);
  try {
    expect(redirectSystemPath({ path: `pigbank://open-finance-volta/${a.tentativa_id}?itemId=${B}&%6modo=acompanhar`, initial: false })).toBeNull();
    await drenar();
    expect(await lerTentativaBancaria(1)).toEqual(a); expect(chamadas()).toHaveLength(0);
  } finally { definirWidgetAberto(false); }
});
