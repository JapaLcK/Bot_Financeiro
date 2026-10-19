import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { claro, escuro } from "@/ui/tokens";
import IconeStub from "../ui/__mocks__/iconeStub";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { atalho, filtrar, nFichas } from "./extrato_filtros_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
// Extrato agrupado por dia (redesenho): total do dia só em grupo completo, Hoje/Ontem no relógio de São Paulo, chips de atalho, busca sem rótulo, linha com ícone e sem data.
const LANC = "/api/app/lancamentos", MENOS = "−";
const item = (id: string, data: string, tipo: "entrada" | "saida", valor: string, extra: object = {}) => ({ ...fixture[LANC].itens[0]!, id, data, tipo, valor, descricao: id, categoria: "mercado", origem: "banco", instituicao: "Nubank", ...extra });
// Pág. 1: dia A (hoje, completo) e dia B (o último; continua na pág. 2). Pág. 2: o resto de B e o dia C.
const P1 = [item("Salário A", "2026-10-19", "entrada", "120.00", { categoria: "renda" }), item("Compra A", "2026-10-19", "saida", "32.40"), item("Compra B1", "2026-10-18", "saida", "50.00"), item("Compra B2", "2026-10-18", "saida", "25.00")];
const P2 = [item("Compra B3", "2026-10-18", "saida", "11.00"), item("Compra C1", "2026-10-17", "saida", "7.00"), item("Compra C2", "2026-10-17", "saida", "3.00")];
let pagina1: object[], proximo: string | null, consultas: URL[];
beforeEach(async () => {
 prepararCaso(); desligarTrava(); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); consultas = []; pagina1 = P1; proximo = "p2";
 fetchFalso.mockImplementation(async (url: string) => {
  const u = new URL(url), path = u.pathname;
  if (path === LANC) { consultas.push(u); return u.searchParams.has("cursor") ? resposta(200, { ...fixture[LANC], itens: P2, proximo: null }) : resposta(200, { ...fixture[LANC], itens: pagina1, proximo }); }
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === "/api/app/categorias") return resposta(200, { categorias: [{ chave: "mercado", nome: "Mercado", icone: "basket" }, { chave: "renda", nome: "Renda", icone: "briefcase" }] }); // o fixture não traz `icone`
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
});
afterEach(() => { jest.restoreAllMocks(); });
const abrir = async (url = "/extrato") => { renderRouter("./app", { initialUrl: url }); jest.setSystemTime(new Date("2026-10-19T16:00:00Z")); await waitFor(() => expect(screen.getByText("Compra A")).toBeTruthy()); await act(drenar); };
const cabecalhos = () => screen.getAllByRole("header").map((h) => h.props.children as string).filter((t) => /·/.test(t));
const carregarMais = () => act(async () => { fireEvent.press(screen.getByRole("button", { name: "Carregar mais lançamentos" })); await drenar(); });
const total = (texto: string) => screen.queryByText(texto);

describe("total do dia só no grupo completo", () => {
 it("com 'Carregar mais' pendente, o ÚLTIMO dia não tem total (negativo); os de cima têm", async () => {
  await abrir();
  expect(total("+R$ 87,60")).toBeTruthy(); // A: +120,00 − 32,40, completo
  expect(total(`${MENOS}R$ 75,00`)).toBeNull(); // B: continua na próxima página
  expect(cabecalhos()).toEqual(["Hoje · 19/10", "Ontem · 18/10"]);
  expect(screen.getByLabelText("Hoje, segunda-feira, 19 de outubro")).toBeTruthy(); expect(screen.getByLabelText("domingo, 18 de outubro")).toBeTruthy(); // o rótulo falado do cabeçalho
 });
 it("controle positivo: sem próxima página o último dia mostra o total", async () => {
  proximo = null; await abrir();
  expect(total(`${MENOS}R$ 75,00`)).toBeTruthy();
 });
 it("depois de 'Carregar mais' o dia B fecha com o total das duas páginas (uma vez só) e C, agora o último de uma lista completa, também ganha", async () => {
  await abrir(); await carregarMais(); await waitFor(() => expect(screen.getByText("Compra C2")).toBeTruthy());
  expect(total(`${MENOS}R$ 86,00`)).toBeTruthy(); // B: 50 + 25 + 11
  expect(total(`${MENOS}R$ 10,00`)).toBeTruthy(); // C: 7 + 3
  expect(cabecalhos()).toEqual(["Hoje · 19/10", "Ontem · 18/10", "Sáb · 17/10"]);
 });
});

describe("Hoje/Ontem no relógio de São Paulo", () => {
 it("com o aparelho em Tóquio (já é dia 20 lá), 2026-10-19 às 13h em SP continua 'Hoje · 19/10'", async () => {
  // `process.env.TZ` não chega ao Node de dentro do Jest: o aparelho em Tóquio é simulado no `Intl` (quem não fixar o fuso, como o relógio do aparelho faria, cai em Tóquio).
  const Real = Intl.DateTimeFormat; jest.spyOn(Intl, "DateTimeFormat").mockImplementation(((l?: string, o?: object) => new Real(l, { timeZone: "Asia/Tokyo", ...o })) as never);
  await abrir();
  expect(cabecalhos()[0]).toBe("Hoje · 19/10");
 });
});

describe("atalhos (chips)", () => {
 const selecionado = (nome: string) => atalho(nome).props.accessibilityState.selected;
 const ultima = () => consultas.at(-1)!.searchParams;
 const tocar = async (nome: string) => { await act(async () => { fireEvent.press(atalho(nome)); await drenar(); }); };
 it("Tudo começa selecionado; Saídas troca só o tipo, fica selecionado e não gera ficha", async () => {
  await abrir(); expect(selecionado("Tudo")).toBe(true);
  await tocar("Saídas"); await waitFor(() => expect(ultima().get("tipo")).toBe("saida"));
  expect(ultima().has("origem")).toBe(false); expect(selecionado("Saídas")).toBe(true); expect(selecionado("Tudo")).toBe(false); expect(nFichas()).toBe(0);
 });
 it("Cartão troca só a origem (sem tipo)", async () => {
  await abrir(); await tocar("Cartão"); await waitFor(() => expect(ultima().get("origem")).toBe("cartao"));
  expect(ultima().has("tipo")).toBe(false); expect(selecionado("Cartão")).toBe(true); expect(nFichas()).toBe(0);
 });
 it("saída + banco pela folha é combinação: nenhum chip selecionado e as DUAS fichas", async () => {
  await abrir(); await filtrar({ tipo: "Saídas", origem: "Banco" }); await waitFor(() => expect(ultima().get("origem")).toBe("banco"));
  for (const n of ["Tudo", "Entradas", "Saídas", "Cartão"]) expect(selecionado(n)).toBe(false);
  expect(nFichas()).toBe(2);
 });
 it("Tudo zera tipo e origem e MANTÉM dia, categoria, valor e busca", async () => {
  await abrir("/extrato?dia=2026-10-02&categoria=mercado"); await filtrar({ tipo: "Saídas", min: "1000" });
  fireEvent.changeText(screen.getByLabelText("Buscar lançamento"), "Sal"); await act(async () => { jest.advanceTimersByTime(400); await drenar(); });
  await tocar("Tudo"); await waitFor(() => expect(ultima().has("tipo")).toBe(false));
  expect([ultima().get("dia"), ultima().get("categoria"), ultima().get("valor_min"), ultima().get("q")]).toEqual(["2026-10-02", "mercado", "10.00", "Sal"]);
  expect(selecionado("Tudo")).toBe(true);
 });
});

describe("Ocultar valores", () => {
 const alternar = (nome: string) => act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); });
 const verdes = () => screen.UNSAFE_getAllByType(IconeStub).filter((i) => i.props.color === claro.positive || i.props.color === escuro.positive);
 it("o total do dia some sem dígito; o ícone vira neutro (a seta verde revelaria o tipo); mostrar volta", async () => {
  await abrir(); expect(total("+R$ 87,60")).toBeTruthy(); expect(verdes()).toHaveLength(1); // controle positivo: a entrada tem a seta verde
  await alternar("Ocultar valores");
  expect(screen.queryByText(/87,60|120,00/)).toBeNull(); expect(verdes()).toHaveLength(0);
  expect(screen.queryByText(/Nubank|Mercado · |Renda/)).toBeNull(); // sem instituição nem categoria no subtítulo
  expect(screen.getAllByTestId(/^icone-categoria-/, { includeHiddenElements: true }).every((n) => n.props.testID === "icone-categoria-tag")).toBe(true);
  await alternar("Mostrar valores"); expect(total("+R$ 87,60")).toBeTruthy();
 });
});

describe("busca e linha", () => {
 it("campo de busca: rótulo só para o leitor de tela, placeholder 'Buscar'", async () => {
  await abrir(); expect(screen.getByLabelText("Buscar lançamento")).toBeTruthy(); expect(screen.queryByText("Buscar lançamento")).toBeNull(); expect(screen.getByPlaceholderText("Buscar")).toBeTruthy();
 });
 it("linha: entrada com seta verde, saída com o ícone da categoria, desconhecida em tag; sem data na linha, instituição e categoria no subtítulo", async () => {
  pagina1 = [P1[0]!, P1[1]!, item("Sem ícone", "2026-10-19", "saida", "1.00", { categoria: "inventada" })]; proximo = null; await abrir();
  expect(screen.UNSAFE_getAllByType(IconeStub).filter((i) => i.props.color === claro.positive || i.props.color === escuro.positive)).toHaveLength(1);
  const ids = screen.getAllByTestId(/^icone-categoria-/, { includeHiddenElements: true }).map((n) => n.props.testID);
  expect(ids).toEqual(expect.arrayContaining(["icone-categoria-basket", "icone-categoria-tag"]));
  expect(screen.queryByText(/\d{2}\/\d{2}\/\d{4}/)).toBeNull(); expect(screen.getByText("Nubank · Mercado")).toBeTruthy();
 });
});
