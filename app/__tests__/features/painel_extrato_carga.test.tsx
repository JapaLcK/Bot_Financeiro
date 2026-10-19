import { router } from "expo-router";
import { StyleSheet } from "react-native";
import { Icone } from "@/ui/componentes/Icone";
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { irAoExtrato } from "@/features/painel/navegacao";
import { mesAtual, mesesAnteriores, nomeMes } from "@/features/painel/catalogo";
import fixture from "./painel.fixture.json";
import { ficha, filtrar } from "./extrato_filtros_apoio";
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
async function extratoComFiltros() {
 renderRouter("./app", { initialUrl: "/extrato" }); await lista(); await act(drenar);
 fireEvent.changeText(screen.getByLabelText("Buscar lançamento"), "Sal"); act(() => { jest.advanceTimersByTime(400); }); await lista(); await act(drenar);
 await filtrar({ origem: "Banco", tipo: "Entradas" }); await lista(); await act(drenar);
 fireEvent.press(screen.getByText("Salário")); await act(drenar);
}
const detalhe = () => screen.queryByText(/Nubank · 12:00/);
it("#897: inactive→active mantém busca, origem, tipo e lançamento aberto, sem revalidar", async () => {
 await extratoComFiltros(); const [me, lanc] = [authMe(), chamadasLanc().length];
 expect(screen.getByLabelText("Buscar lançamento").props.value).toBe("Sal"); expect(detalhe()).toBeTruthy();
 await appVai("inactive"); await appVai("active"); await act(drenar);
 expect(screen.getByLabelText("Buscar lançamento").props.value).toBe("Sal");
 expect(ficha("origem Banco")).toBeTruthy(); expect(ficha("Entradas")).toBeTruthy();
 expect(detalhe()).toBeTruthy(); expect(authMe()).toBe(me); expect(chamadasLanc().length).toBe(lanc);
});
// Decisão do dono (reverte o #897): o background (rede derrubada) NÃO fecha o portão; a tela e os filtros ficam (a tampa nativa cobre o app switcher) e o acesso é revalidado em silêncio.
it("background→active (volta curta) mantém a lista e a busca, e revalida o acesso em silêncio", async () => {
 await extratoComFiltros(); const me = authMe(); expect(detalhe()).toBeTruthy();
 await appVai("background"); expect(screen.getByText("Salário")).toBeTruthy(); expect(detalhe()).toBeTruthy();
 await appVai("active"); await lista(); await act(drenar);
 expect(authMe()).toBe(me + 1); expect(screen.queryByText("Conferindo seu acesso…")).toBeNull(); expect(screen.queryByText(VAZIO)).toBeNull();
 expect(screen.getByLabelText("Buscar lançamento").props.value).toBe("Sal"); // o detalhe aberto NÃO é afirmado: o Extrato fecha o detalhe e reinicia a lista a cada `versao` (extrato.tsx:30,46), igual ao refoco de rota empurrada
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
  await filtrar({ tipo: "Entradas" }); await lista(); await act(drenar); expect(detalhe()).toBeNull();
 });
 it("a linha recolhida NÃO diz 'parcela n/N' (só o detalhe aberto: 'Parcela n/N')", async () => {
  lancamentos = () => com([{ ...L[1], parcela: { n: 3, total: 10 } }]); renderRouter("./app", { initialUrl: "/extrato" });
  await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy()); expect(screen.queryByText(/parcela 3\/10/)).toBeNull();
  fireEvent.press(screen.getByText("Mercado")); await act(drenar); expect(screen.getByText(/Parcela 3\/10/)).toBeTruthy();
 });
});

// Filtros de uma visita anterior não valem para a seguinte: toda ida ao Extrato por `irAoExtrato` traz uma `entrada` nova (as abas ficam montadas).
describe("Extrato: filtros zeram a cada entrada", () => {
 const ativo = (nome: string) => !!ficha(nome);
 const ir = async (rota: "/gastos" | "/extrato") => { await act(async () => { router.navigate(rota); await drenar(); }); };
 const dia = "calendario-dia-2026-10-02", tocarDia = async () => { await waitFor(() => expect(screen.getByTestId(dia)).toBeTruthy()); await act(async () => { fireEvent.press(screen.getByTestId(dia)); await drenar(); }); await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ver extrato do dia" })); await drenar(); }); }; // o toque abre a folha do dia (PR 2b); o botão dela leva ao Extrato
 // Busca, origem e tipo de uma só vez; o debounce (300 ms) é avançado à mão porque o Router já usa timers falsos.
 const filtra = async () => { await filtrar({ origem: "Banco", tipo: "Entradas", min: "1000" }); fireEvent.changeText(screen.getByLabelText("Buscar lançamento"), "Sal"); await act(async () => { jest.advanceTimersByTime(300); await drenar(); }); expect(ativo("origem Banco")).toBe(true); expect(ativo("Entradas")).toBe(true); expect(ativo("valor")).toBe(true); expect(screen.getByTestId("extrato-escopo-busca")).toBeTruthy(); };
 // Chamado LOGO após a entrada, sem avançar o debounce: um reset que esquece `busca` ou `origem` ainda deixa `q`/`origem` na consulta aqui, e só converge sozinho 300 ms depois.
 const limpo = () => {
  expect(ativo("origem Banco")).toBe(false); expect(ativo("Entradas")).toBe(false); expect(ativo("valor")).toBe(false);
  expect(screen.getByLabelText("Buscar lançamento").props.value).toBe(""); expect(screen.queryByTestId("extrato-escopo-busca")).toBeNull();
  for (const k of ["q", "origem", "tipo", "valor_min", "valor_max"]) expect(consulta().has(k)).toBe(false);
 };
 const aguardar = () => waitFor(() => expect(screen.getByLabelText("Buscar lançamento")).toBeTruthy());
 beforeEach(() => { lancamentos = servidorReal(L); });
 it("'Entradas' escolhido antes, depois o toque do dia: mostra as saídas do dia; e de novo no MESMO dia (D → Entradas → Gastos → D)", async () => {
  renderRouter("./app", { initialUrl: "/extrato" }); await lista(); await filtra();
  await ir("/gastos"); await tocarDia(); limpo(); await aguardar();
  await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy()); expect(screen.queryByText(VAZIO)).toBeNull();
  await filtra(); await waitFor(() => expect(screen.getByText(VAZIO)).toBeTruthy());
  await ir("/gastos"); await tocarDia(); limpo(); // mesmo `dia`: só a `entrada` difere
  await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy()); expect(screen.queryByText(VAZIO)).toBeNull();
 });
 it("por Categorias: 'Entradas' e a busca escolhidos antes não combinam com a categoria (e repete na mesma categoria)", async () => {
  renderRouter("./app", { initialUrl: "/extrato" }); await lista(); await filtra();
  const linha = () => screen.getByRole("button", { name: /Alimentação/ });
  await ir("/gastos"); await waitFor(() => expect(linha()).toBeTruthy()); await act(async () => { fireEvent.press(linha()); await drenar(); }); await aguardar(); limpo();
  await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy());
  await filtra(); await waitFor(() => expect(screen.getByText(VAZIO)).toBeTruthy());
  await ir("/gastos"); await act(async () => { fireEvent.press(linha()); await drenar(); }); limpo(); await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy());
 });
 // Controle positivo: sem `entrada` nova (refresh, troca de mês) os filtros ficam, como na `main`; um reset em `versao`/`mes` apagaria a busca do usuário.
 it("refresh e troca de mês mantêm busca, origem e tipo", async () => {
  renderRouter("./app", { initialUrl: "/extrato" }); await lista(); await filtra();
  await refresh(); await act(drenar);
  const seguem = () => { expect(ativo("origem Banco")).toBe(true); expect(ativo("Entradas")).toBe(true); expect(ativo("valor")).toBe(true); expect(screen.getByLabelText("Buscar lançamento").props.value).toBe("Sal"); expect(screen.getByTestId("extrato-escopo-busca")).toBeTruthy(); };
  seguem();
  const outro = nomeMes(mesesAnteriores(mesAtual())[1]!);
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: nomeMes(mesAtual()).slice(0, 3) })); await drenar(); });
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: outro })); await drenar(); });
  expect(consulta().get("mes")).toBe(mesesAnteriores(mesAtual())[1]); seguem();
 });
});

// Botão que tira o filtro de `dia`/`categoria` (grudava depois de entrar pela folha do dia, pelo "Dia a dia" ou por "Para onde vai").
describe("Extrato: limpar o filtro de dia e de categoria", () => {
 const sem = { ...L[3], id: "l5", categoria: "", descricao: "Sem rótulo" };
 const entrar = async (filtro: { dia: string } | { categoria: string }) => { await act(async () => { irAoExtrato((d) => router.navigate(d), filtro); await drenar(); }); };
 const botao = (nome: string | RegExp) => screen.queryByRole("button", { name: nome });
 const limpar = async (nome: string) => { await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); };
 beforeEach(async () => { lancamentos = servidorReal([...L, sem]); renderRouter("./app", { initialUrl: "/extrato" }); await lista(); await act(drenar); });
 it("dia: o botão limpa a consulta (mês inteiro), o rótulo some, e sem filtro o botão não existe", async () => {
  expect(botao(/Limpar filtro/)).toBeNull();
  await entrar({ dia: "2026-10-02" }); await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy());
  expect(consulta().get("dia")).toBe("2026-10-02"); expect(screen.getByText("02/10/2026")).toBeTruthy();
  const antes = chamadasLanc().length; await limpar("Limpar filtro do dia 02/10/2026");
  await waitFor(() => expect(screen.getByText("Salário")).toBeTruthy()); expect(chamadasLanc().length).toBe(antes + 1);
  expect(consulta().has("dia")).toBe(false); expect(consulta().get("mes")).toMatch(/^\d{4}-\d{2}$/);
  expect(screen.queryByText("02/10/2026")).toBeNull(); expect(botao("Limpar filtro do dia 02/10/2026")).toBeNull();
 });
 it("categoria e `categoria: \"\"` (sem categoria, filtro válido): o botão tira o param da consulta", async () => {
  await entrar({ categoria: "alimentacao" }); await waitFor(() => expect(consulta().get("categoria")).toBe("alimentacao"));
  expect(screen.getByText("Alimentacao")).toBeTruthy(); await limpar("Limpar filtro de Alimentacao");
  await waitFor(() => expect(consulta().has("categoria")).toBe(false)); expect(screen.queryByText("Alimentacao")).toBeNull();
  await entrar({ categoria: "" }); await waitFor(() => expect(consulta().get("categoria")).toBe(""));
  await waitFor(() => expect(screen.getByText("Sem rótulo")).toBeTruthy()); expect(screen.queryByText("Mercado")).toBeNull();
  await limpar("Limpar filtro de Sem categoria");
  await waitFor(() => expect(consulta().has("categoria")).toBe(false)); await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy());
  expect(botao("Limpar filtro de Sem categoria")).toBeNull();
 });
 it("limpar um filtro não desfaz os outros: origem, tipo e busca escolhidos depois da entrada seguem na consulta", async () => {
  await entrar({ dia: "2026-10-02" }); await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy());
  await filtrar({ origem: "Banco", tipo: "Saídas" }); fireEvent.changeText(screen.getByLabelText("Buscar lançamento"), "Mer"); await act(async () => { jest.advanceTimersByTime(300); await drenar(); });
  await waitFor(() => expect(consulta().get("q")).toBe("Mer"));
  await limpar("Limpar filtro do dia 02/10/2026"); await waitFor(() => expect(consulta().has("dia")).toBe(false));
  expect([consulta().get("q"), consulta().get("origem"), consulta().get("tipo")]).toEqual(["Mer", "banco", "saida"]);
  expect(screen.getByLabelText("Buscar lançamento").props.value).toBe("Mer"); expect(ficha("Saídas")).toBeTruthy();
 });
 it("limpar a categoria também não desfaz os outros: origem, tipo e busca escolhidos depois da entrada seguem na consulta", async () => {
  await entrar({ categoria: "Alimentação" }); await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy());
  await filtrar({ origem: "Banco", tipo: "Saídas" }); fireEvent.changeText(screen.getByLabelText("Buscar lançamento"), "Mer"); await act(async () => { jest.advanceTimersByTime(300); await drenar(); });
  await waitFor(() => expect(consulta().get("q")).toBe("Mer"));
  await limpar("Limpar filtro de Alimentação"); await waitFor(() => expect(consulta().has("categoria")).toBe(false));
  expect([consulta().get("q"), consulta().get("origem"), consulta().get("tipo")]).toEqual(["Mer", "banco", "saida"]);
  expect(screen.getByLabelText("Buscar lançamento").props.value).toBe("Mer"); expect(ficha("Saídas")).toBeTruthy();
 });
 it("reentrar no MESMO dia depois de limpar volta a filtrar pelo dia", async () => {
  await entrar({ dia: "2026-10-02" }); await waitFor(() => expect(screen.getByText("Mercado")).toBeTruthy());
  await limpar("Limpar filtro do dia 02/10/2026"); await waitFor(() => expect(consulta().has("dia")).toBe(false));
  await entrar({ dia: "2026-10-02" }); await waitFor(() => expect(consulta().get("dia")).toBe("2026-10-02"));
  await waitFor(() => expect(screen.queryByText("Salário")).toBeNull()); expect(screen.getByText("Mercado")).toBeTruthy(); expect(screen.getByText("02/10/2026")).toBeTruthy();
 });
 it("a11y: role button, label do filtro e alvo de 44pt", async () => {
  await entrar({ dia: "2026-10-02" }); const b = screen.getByRole("button", { name: "Limpar filtro do dia 02/10/2026" });
  expect(b.props.accessibilityLabel).toBe("Limpar filtro do dia 02/10/2026"); expect(within(b).UNSAFE_getByType(Icone).props.nome).toBe("X"); expect(within(b).UNSAFE_getByType(Icone).props.tom).toBe("brandInk"); expect(StyleSheet.flatten(b.props.style)).toMatchObject({ minHeight: 44, minWidth: 44 });
 });
 // dia e categoria ao mesmo tempo (URL com os dois): cada botão limpa só o seu param
 const ambos = async () => { await act(async () => { router.navigate("/extrato?dia=2026-10-02&categoria=alimentacao"); await drenar(); }); await waitFor(() => expect(consulta().get("categoria")).toBe("alimentacao")); expect(consulta().get("dia")).toBe("2026-10-02"); };
 it("dia e categoria juntos: limpar o dia mantém a categoria (consulta e botão)", async () => {
  await ambos(); await limpar("Limpar filtro do dia 02/10/2026"); await waitFor(() => expect(consulta().has("dia")).toBe(false));
  expect(consulta().get("categoria")).toBe("alimentacao"); expect(botao("Limpar filtro de Alimentacao")).toBeTruthy(); expect(botao("Limpar filtro do dia 02/10/2026")).toBeNull();
 });
 it("dia e categoria juntos: limpar a categoria mantém o dia (consulta e botão)", async () => {
  await ambos(); await limpar("Limpar filtro de Alimentacao"); await waitFor(() => expect(consulta().has("categoria")).toBe(false));
  expect(consulta().get("dia")).toBe("2026-10-02"); expect(botao("Limpar filtro do dia 02/10/2026")).toBeTruthy(); expect(botao("Limpar filtro de Alimentacao")).toBeNull();
 });
});
