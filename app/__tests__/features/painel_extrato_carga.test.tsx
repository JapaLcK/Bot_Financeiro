import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
// #849: o efeito do Extrato roda antes do efeito de carga do provider; se a carga completa cancelasse as
// operações, a lista nasceria abortada. A guarda de resposta velha e a de troca de usuário seguem de pé.
const LANC = "/api/app/lancamentos", VAZIO = "Nenhum lançamento encontrado.";
const pagina = (descricao: string) => resposta(200, { ...fixture[LANC], proximo: null, itens: [{ ...fixture[LANC].itens[0]!, descricao }] });
let lancamentos: (req: RequestInit) => Response | Promise<Response>;
function servidor() {
 const impl = (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 };
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => new URL(url).pathname === LANC ? lancamentos(req) : impl(url));
}
const chamadasLanc = () => fetchFalso.mock.calls.filter(([url]) => new URL(String(url)).pathname === LANC);
const refresh = () => act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); await drenar(); });
beforeEach(async () => { prepararCaso(); desligarTrava(); lancamentos = () => pagina("Salário"); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor(); });
afterEach(() => jest.restoreAllMocks());
const lista = () => waitFor(() => expect(screen.getByText("Salário")).toBeTruthy());
it("deep link a frio em /extrato mostra a lista", async () => {
 renderRouter("./app", { initialUrl: "/extrato" }); await lista(); await act(drenar);
 expect(screen.getByText("Salário")).toBeTruthy(); expect(screen.queryByText(VAZIO)).toBeNull();
});
it("refresh no Extrato mantém a lista e não aborta a última leitura", async () => {
 renderRouter("./app", { initialUrl: "/extrato" }); await lista(); const antes = chamadasLanc().length;
 await refresh(); await waitFor(() => expect(chamadasLanc().length).toBeGreaterThan(antes)); await act(drenar);
 expect(screen.getByText("Salário")).toBeTruthy(); expect(screen.queryByText(VAZIO)).toBeNull();
 expect(chamadasLanc().at(-1)![1].signal.aborted).toBe(false);
});
const authMe = () => fetchFalso.mock.calls.filter(([url]) => new URL(String(url)).pathname === "/auth/me").length;
const marcar = (nome: string) => { fireEvent.press(screen.getByRole("button", { name: nome })); };
async function extratoComFiltros() {
 renderRouter("./app", { initialUrl: "/extrato" }); await lista(); await act(drenar);
 fireEvent.changeText(screen.getByLabelText("Buscar lançamento"), "Sal"); act(() => { jest.advanceTimersByTime(400); }); await lista(); await act(drenar);
 marcar("Banco"); marcar("Entradas"); await lista(); await act(drenar);
 fireEvent.press(screen.getByText("Salário")); await act(drenar);
}
const detalhe = () => screen.queryByText(/Nubank · 12:00/);
it("#897: inactive→active mantém busca, origem, tipo e lançamento aberto, sem revalidar", async () => {
 await extratoComFiltros(); const [me, lanc] = [authMe(), chamadasLanc().length];
 expect(screen.getByLabelText("Buscar lançamento").props.value).toBe("Sal"); expect(detalhe()).toBeTruthy();
 await appVai("inactive"); await appVai("active"); await act(drenar);
 expect(screen.getByLabelText("Buscar lançamento").props.value).toBe("Sal");
 expect(screen.getByRole("button", { name: "Banco" }).props.accessibilityState.selected).toBe(true);
 expect(screen.getByRole("button", { name: "Entradas" }).props.accessibilityState.selected).toBe(true);
 expect(detalhe()).toBeTruthy(); expect(authMe()).toBe(me); expect(chamadasLanc().length).toBe(lanc);
});
// Controle positivo do conserto do #897: o background (rede derrubada) continua fechando o portão e revalidando.
it("#897: background→active fecha o portão e revalida", async () => {
 await extratoComFiltros(); const me = authMe(); expect(detalhe()).toBeTruthy();
 await appVai("background"); expect(screen.queryByText("Salário")).toBeNull(); expect(detalhe()).toBeNull();
 await appVai("active"); await lista(); await act(drenar);
 expect(authMe()).toBe(me + 1); expect(screen.queryByText(VAZIO)).toBeNull();
});
it("Extrato, Resumo com refresh, Extrato: a lista está lá", async () => {
 renderRouter("./app", { initialUrl: "/extrato" }); await lista();
 await act(async () => { router.navigate("/resumo"); await drenar(); }); await refresh();
 await act(async () => { router.navigate("/extrato"); await drenar(); }); await lista(); await act(drenar);
 expect(screen.queryByText(VAZIO)).toBeNull();
});
it("resposta velha do Extrato (refresh duplo) não aparece: a leitura superada sai abortada", async () => {
 const presa = segurar(); let n = 0;
 lancamentos = async () => (++n === 2 ? (await presa.promessa, pagina("Página velha")) : pagina("Salário"));
 renderRouter("./app", { initialUrl: "/extrato" }); await lista();
 await refresh(); await waitFor(() => expect(chamadasLanc()).toHaveLength(2)); await refresh();
 await act(async () => { presa.soltar(); await drenar(); }); await lista(); await act(drenar);
 expect(screen.queryByText("Página velha")).toBeNull(); expect(chamadasLanc()[1]![1].signal.aborted).toBe(true);
});
const bela = (req: RequestInit) => (req.headers as Record<string, string>).Authorization === "Bearer access-bela";
function comoBela() {
 const impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  const u = new URL(url);
  if (u.pathname === "/auth/me" && bela(req)) return resposta(200, { user_id: 2, display_name: "Bela", app_access: true });
  if (u.pathname === "/open-finance/2/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (/^\/(goals|cards|installments|analytics|insights)\/2\//.test(u.pathname)) return resposta(200, (fixture as Record<string, unknown>)[u.pathname.replace("/2/", "/1/")]);
  return impl(url, req);
 });
}
const trocarParaBela = async () => { await appVai("background"); await guardarCredenciais({ access: "access-bela", refresh: "rt_bela" }); await appVai("active"); };
// Isolamento de usuário no PROVIDER (dono/uid em carregar.atual): a resposta presa da Ana é do provider, que não desmonta; a tela desmonta (o portão devolve <View />) e remonta lendo `dados`.
it("troca de usuário: a conta da Ana, que chega depois da leitura da Bela, não aparece", async () => {
 const presa = segurar(); const contas = (banco: string) => resposta(200, { ...fixture["/api/app/contas"], contas: [{ ...fixture["/api/app/contas"].contas[0]!, instituicao: banco }] });
 const impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => new URL(url).pathname === "/api/app/contas" ? (bela(req) ? contas("Banco da Bela") : (await presa.promessa, contas("Banco da Ana"))) : impl(url, req));
 comoBela();
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(fetchFalso.mock.calls.some(([u]) => new URL(String(u)).pathname === "/api/app/contas")).toBe(true));
 await trocarParaBela(); await waitFor(() => expect(screen.getByText("Banco da Bela")).toBeTruthy());
 await act(async () => { presa.soltar(); await drenar(); });
 expect(screen.queryByText("Banco da Ana")).toBeNull(); expect(screen.getByText("Banco da Bela")).toBeTruthy();
});
// Defesa em profundidade: mesma mecânica (a tela desmonta na troca e remonta); este caso só fica vermelho se TODAS as guardas do Extrato saírem juntas; o do provider acima é o que mede o isolamento. O `atual()` do provider e a guarda do client seguram cada um sozinho; o `cancelar()` do cleanup age através do `atual()`.
it("troca de usuário no Extrato: o item da Ana não aparece para a Bela", async () => {
 const presa = segurar();
 lancamentos = async (req) => bela(req) ? pagina("Item da Bela") : (await presa.promessa, pagina("Item da Ana"));
 comoBela();
 renderRouter("./app", { initialUrl: "/extrato" }); await waitFor(() => expect(chamadasLanc()).toHaveLength(1));
 await trocarParaBela(); await waitFor(() => expect(screen.getByText("Item da Bela")).toBeTruthy());
 await act(async () => { presa.soltar(); await drenar(); });
 expect(screen.queryByText("Item da Ana")).toBeNull(); expect(screen.getByText("Item da Bela")).toBeTruthy();
 expect((chamadasLanc().at(-1)![1].headers as Record<string, string>).Authorization).toBe("Bearer access-bela");
});
