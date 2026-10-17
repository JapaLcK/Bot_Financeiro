import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { mesAtual, mesesAnteriores, nomeMes } from "@/features/painel/catalogo";
import fixture from "./painel.fixture.json";
// #849: o efeito do Extrato roda antes do efeito de carga do provider; se a carga completa cancelasse as
// operações, a lista nasceria abortada. A guarda de resposta velha e a de troca de usuário seguem de pé.
const LANC = "/api/app/lancamentos", VAZIO = "Nenhum lançamento encontrado.";
const pagina = (descricao: string) => resposta(200, { ...fixture[LANC], proximo: null, itens: [{ ...fixture[LANC].itens[0]!, descricao }] });
let lancamentos: (req: RequestInit, url: URL) => Response | Promise<Response>;
function servidor() {
 const impl = (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 };
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => new URL(url).pathname === LANC ? lancamentos(req, new URL(url)) : impl(url));
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

// Extrato sobre o `useLancamentos` compartilhado com os cards do chat: o que é do Extrato (limite, filtro e reinício por dia, detalhe, parcela) fica fixado aqui.
const L = (fixture as Record<string, any>)[LANC].itens;
const com = (itens: unknown[], proximo: string | null = null) => resposta(200, { ...(fixture as Record<string, any>)[LANC], itens, proximo });
// O servidor de verdade: filtra por `dia`, `tipo` e `categoria` ANTES de paginar (limite 50, cursor opaco só para dizer que há mais).
const servidorReal = (todos: any[]) => (_req: RequestInit, url: URL) => {
 const f = todos.filter((i) => (!url.searchParams.get("dia") || i.data === url.searchParams.get("dia")) && (!url.searchParams.get("tipo") || i.tipo === url.searchParams.get("tipo")) && (!url.searchParams.has("categoria") || i.categoria === url.searchParams.get("categoria")));
 return com(f.slice(0, 50), f.length > 50 ? "p2" : null);
};
const consulta = (n = -1) => new URL(String(chamadasLanc().at(n)![0])).searchParams;
describe("Extrato: consulta, dia e detalhe", () => {
 it("pede o mês do painel com limite 50 (o chat pede 10 e 100: o Extrato não)", async () => {
  renderRouter("./app", { initialUrl: "/extrato" }); await lista();
  expect(consulta().get("limite")).toBe("50"); expect(consulta().get("mes")).toMatch(/^\d{4}-\d{2}$/);
 });
 it("`?dia=` mostra só os lançamentos do dia, e o dia vai na consulta junto do mês", async () => {
  lancamentos = servidorReal([L[0], L[1], L[2]]); renderRouter("./app", { initialUrl: "/extrato?dia=2026-10-02" });
  await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy()); expect(screen.queryByText("Salário")).toBeNull(); expect(screen.queryByText("Café e almoço")).toBeNull();
  expect(consulta().get("dia")).toBe("2026-10-02"); expect(consulta().get("mes")).toMatch(/^\d{4}-\d{2}$/);
 });
 it("mês com mais de 50 lançamentos: o dia que cai na 2ª página mostra as compras do dia", async () => {
  const outros = Array.from({ length: 55 }, (_, n) => ({ ...L[2], id: `x${n}`, data: "2026-10-10", descricao: `Outro ${n}` }));
  lancamentos = servidorReal([...outros, { ...L[1], id: "d1", descricao: "Compra do dia A" }, { ...L[1], id: "d2", descricao: "Compra do dia B" }]);
  renderRouter("./app", { initialUrl: "/extrato?dia=2026-10-02" }); await waitFor(() => expect(screen.getByText("Compra do dia A")).toBeTruthy());
  expect(screen.getByText("Compra do dia B")).toBeTruthy(); expect(screen.queryByText("Outro 0")).toBeNull(); expect(screen.queryByText("Nenhum resultado nesta página.")).toBeNull();
  expect([consulta().get("mes"), consulta().get("dia"), consulta().get("limite")]).toEqual([expect.stringMatching(/^\d{4}-\d{2}$/), "2026-10-02", "50"]);
 });
 it("controle positivo: sem `dia` a consulta não leva `dia` e a lista segue a 1ª página", async () => {
  const outros = Array.from({ length: 55 }, (_, n) => ({ ...L[2], id: `x${n}`, data: "2026-10-10", descricao: `Outro ${n}` }));
  lancamentos = servidorReal([...outros, { ...L[1], id: "d1", descricao: "Compra do dia A" }]); renderRouter("./app", { initialUrl: "/extrato" });
  await waitFor(() => expect(screen.getByText("Outro 0")).toBeTruthy()); expect(consulta().has("dia")).toBe(false); expect(screen.queryByText("Compra do dia A")).toBeNull();
  expect(screen.getByRole("button", { name: "Carregar mais lançamentos" })).toBeTruthy();
 });
 it("trocar só o `dia` refaz a leitura (1 → 2 requisições) e mostra o dia novo", async () => {
  lancamentos = servidorReal([L[0], L[1]]); renderRouter("./app", { initialUrl: "/extrato?dia=2026-10-01" }); await lista(); await act(drenar);
  expect(chamadasLanc()).toHaveLength(1); expect(screen.queryByText("Mercado")).toBeNull();
  await act(async () => { router.setParams({ dia: "2026-10-02" }); await drenar(); });
  expect(chamadasLanc()).toHaveLength(2); await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy()); expect(screen.queryByText("Salário")).toBeNull();
 });
 it("trocar o `dia` recupera de erro na 1ª página", async () => {
  lancamentos = () => resposta(500, { detail: "x" }); renderRouter("./app", { initialUrl: "/extrato?dia=2026-10-01" });
  await waitFor(() => expect(screen.getByText("Não conseguimos carregar agora. Tente novamente.")).toBeTruthy());
  lancamentos = () => com([L[0]]); await act(async () => { router.setParams({ dia: "2026-10-02" }); await drenar(); });
  await waitFor(() => expect(screen.queryByText("Não conseguimos carregar agora. Tente novamente.")).toBeNull()); expect(chamadasLanc()).toHaveLength(2);
 });
 it("mudar um filtro fecha o detalhe aberto", async () => {
  renderRouter("./app", { initialUrl: "/extrato" }); await lista(); fireEvent.press(screen.getByText("Salário")); await act(drenar); expect(detalhe()).toBeTruthy();
  marcar("Entradas"); await lista(); await act(drenar); expect(detalhe()).toBeNull();
 });
 it("a linha recolhida NÃO diz 'parcela n/N' (só o detalhe aberto: 'Parcela n/N')", async () => {
  lancamentos = () => com([{ ...L[1], parcela: { n: 3, total: 10 } }]); renderRouter("./app", { initialUrl: "/extrato" });
  await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy()); expect(screen.queryByText(/parcela 3\/10/)).toBeNull();
  fireEvent.press(screen.getByText("Mercado")); await act(drenar); expect(screen.getByText(/Parcela 3\/10/)).toBeTruthy();
 });
});

// Filtros de uma visita anterior não valem para a seguinte: toda ida ao Extrato por `irAoExtrato` traz uma `entrada` nova (as abas ficam montadas).
describe("Extrato: filtros zeram a cada entrada", () => {
 const selecionado = (nome: string) => screen.getByRole("button", { name: nome }).props.accessibilityState.selected;
 const ir = async (rota: "/gastos" | "/extrato") => { await act(async () => { router.navigate(rota); await drenar(); }); };
 const dia = "calendario-dia-2026-10-02", tocarDia = async () => { await waitFor(() => expect(screen.getByTestId(dia)).toBeTruthy()); await act(async () => { fireEvent.press(screen.getByTestId(dia)); await drenar(); }); await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ver extrato do dia" })); await drenar(); }); }; // o toque abre a folha do dia (PR 2b); o botão dela leva ao Extrato
 // Busca, origem e tipo de uma só vez; o debounce (300 ms) é avançado à mão porque o Router já usa timers falsos.
 const filtrar = async () => { marcar("Banco"); marcar("Entradas"); fireEvent.changeText(screen.getByLabelText("Buscar lançamento"), "Sal"); await act(async () => { jest.advanceTimersByTime(300); await drenar(); }); expect(selecionado("Banco")).toBe(true); expect(selecionado("Entradas")).toBe(true); expect(screen.getByTestId("extrato-escopo-busca")).toBeTruthy(); };
 // Chamado LOGO após a entrada, sem avançar o debounce: um reset que esquece `busca` ou `origem` ainda deixa `q`/`origem` na consulta aqui, e só converge sozinho 300 ms depois.
 const limpo = () => {
  expect(selecionado("Todas as origens")).toBe(true); expect(selecionado("Banco")).toBe(false); expect(selecionado("Tudo")).toBe(true); expect(selecionado("Entradas")).toBe(false);
  expect(screen.getByLabelText("Buscar lançamento").props.value).toBe(""); expect(screen.queryByTestId("extrato-escopo-busca")).toBeNull();
  for (const k of ["q", "origem", "tipo"]) expect(consulta().has(k)).toBe(false);
 };
 const aguardar = () => waitFor(() => expect(screen.getByLabelText("Buscar lançamento")).toBeTruthy());
 beforeEach(() => { lancamentos = servidorReal(L); });
 it("'Entradas' escolhido antes, depois o toque do dia: mostra as saídas do dia; e de novo no MESMO dia (D → Entradas → Gastos → D)", async () => {
  renderRouter("./app", { initialUrl: "/extrato" }); await lista(); await filtrar();
  await ir("/gastos"); await tocarDia(); limpo(); await aguardar();
  await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy()); expect(screen.queryByText(VAZIO)).toBeNull();
  await filtrar(); await waitFor(() => expect(screen.getByText(VAZIO)).toBeTruthy());
  await ir("/gastos"); await tocarDia(); limpo(); // mesmo `dia`: só a `entrada` difere
  await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy()); expect(screen.queryByText(VAZIO)).toBeNull();
 });
 it("por Categorias: 'Entradas' e a busca escolhidos antes não combinam com a categoria (e repete na mesma categoria)", async () => {
  renderRouter("./app", { initialUrl: "/extrato" }); await lista(); await filtrar();
  const linha = () => screen.getByRole("button", { name: /Alimentação/ });
  await ir("/gastos"); await waitFor(() => expect(linha()).toBeTruthy()); await act(async () => { fireEvent.press(linha()); await drenar(); }); await aguardar(); limpo();
  await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy());
  await filtrar(); await waitFor(() => expect(screen.getByText(VAZIO)).toBeTruthy());
  await ir("/gastos"); await act(async () => { fireEvent.press(linha()); await drenar(); }); limpo(); await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy());
 });
 // Controle positivo: sem `entrada` nova (refresh, troca de mês) os filtros ficam, como na `main`; um reset em `versao`/`mes` apagaria a busca do usuário.
 it("refresh e troca de mês mantêm busca, origem e tipo", async () => {
  renderRouter("./app", { initialUrl: "/extrato" }); await lista(); await filtrar();
  await refresh(); await act(drenar);
  const seguem = () => { expect(selecionado("Banco")).toBe(true); expect(selecionado("Entradas")).toBe(true); expect(screen.getByLabelText("Buscar lançamento").props.value).toBe("Sal"); expect(screen.getByTestId("extrato-escopo-busca")).toBeTruthy(); };
  seguem();
  const outro = nomeMes(mesesAnteriores(mesAtual())[1]!);
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(mesAtual()).slice(0, 3) })); await drenar(); });
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: outro })); await drenar(); });
  expect(consulta().get("mes")).toBe(mesesAnteriores(mesAtual())[1]); seguem();
 });
});
