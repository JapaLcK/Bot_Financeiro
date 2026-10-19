// Bloco "Entrou e saiu no mês" no topo da aba Gastos: duas linhas acumuladas (Entrou e Saiu) com a legenda nomeada e o total de cada uma; o Resumo não o tem (ver painel_curva_tela).
// "Hoje" fixo: o fixture é de 2026-10 (dias 1 a 6) e o CI roda em qualquer data.
let mockHoje = "2026-10-06";
jest.mock("@/features/painel/catalogo", () => ({ ...jest.requireActual("@/features/painel/catalogo"), hojeSP: () => mockHoje }));
import { Dimensions, StyleSheet } from "react-native";
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";

jest.setTimeout(20_000); // monta o painel inteiro pelo router: no CI carregado o 1º teste passa dos 5 s padrão (igual a painel_seletores)

const F = fixture as Record<string, any>, DET = "/api/app/mes-detalhes", RES = "/api/app/resumo-do-mes";
let detalhes: () => Promise<Response> | Response, resumo: any;
beforeEach(async () => {
 mockHoje = "2026-10-06"; prepararCaso(); desligarTrava(); resumo = F[RES]; detalhes = () => resposta(200, F[DET]);
 await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === RES) return resposta(200, resumo);
  if (path === DET) return detalhes();
  return resposta(200, F[path] ?? {});
 });
});
afterEach(async () => { await act(async () => { Dimensions.set({ window: janelaOriginal }); }); });
const janelaOriginal = Dimensions.get("window");
const bloco = () => within(screen.getByTestId("numeros-mes"));
async function abrir(url: "/gastos" | "/resumo" = "/gastos") {
 renderRouter("./app", { initialUrl: url });
 await waitFor(() => expect(screen.getByTestId(url === "/gastos" ? "numeros-mes" : "bloco-resumo")).toBeTruthy());
}

const grafico = () => bloco().queryByTestId("grafico-entrou-saiu");
// Pontos "x,y" do `d` do caminho (M/L): o gráfico é desenhado no viewBox 300x100, dia 1 em x=0 e o último do mês em x=300.
const pontos = (id: string) => String(bloco().getByTestId(id).props.d).split(" ").map((p) => p.slice(1).split(",").map(Number) as [number, number]);
const ROTULO = "Entrou 7200 reais, saiu 2380 reais e 50 centavos, acumulado no mês";
it("topo da aba Gastos, antes de 'Para onde vai': as duas linhas nomeadas com o total de cada uma, e mais nada (sem Saldo do mês nem Guardado líquido)", async () => {
 await abrir(); await waitFor(() => expect(grafico()).toBeTruthy());
 for (const t of ["Entrou e saiu no mês", "Entrou", "Saiu"]) expect(bloco().getByText(t)).toBeTruthy();
 expect(bloco().getByLabelText("mais 7200 reais")).toBeTruthy(); expect(bloco().getByLabelText("menos 2380 reais e 50 centavos")).toBeTruthy();
 expect(bloco().getByTestId("amostra-Entrou")).toBeTruthy(); expect(bloco().getByTestId("amostra-Saiu")).toBeTruthy();
 expect(bloco().queryByText("Saldo do mês")).toBeNull(); expect(bloco().queryByText("Guardado líquido")).toBeNull(); expect(bloco().queryByLabelText("4819 reais e 50 centavos")).toBeNull(); expect(bloco().queryByLabelText("1200 reais")).toBeNull();
 expect(grafico()!.props.accessibilityLabel).toBe(ROTULO);
 await waitFor(() => expect(screen.getByText("Para onde vai")).toBeTruthy());
 expect(screen.getAllByText(/^(Entrou e saiu no mês|Para onde vai)$/).map((t) => t.props.children)).toEqual(["Entrou e saiu no mês", "Para onde vai"]); // ordem do documento
 expect(screen.queryByRole("button", { name: "Ver lançamentos" })).toBeNull();
});
it("mês corrente: as linhas vão do dia 1 até HOJE (6 pontos, x de 0 a 50 de 300) e as pontas batem com o Entrou/Saiu; mesmo eixo Y (Entrou = o teto)", async () => {
 await abrir(); await waitFor(() => expect(grafico()).toBeTruthy());
 const saiu = pontos("linha-saiu"), entrou = pontos("linha-entrou");
 expect(saiu).toHaveLength(6); expect(entrou).toHaveLength(6); expect(saiu.at(-1)![0]).toBe(50); expect(saiu[0]).toEqual([0, 83]); // dia 1: 900 de 7.200 = 12,5% de 88
 expect(entrou.every(([, y]) => y === 6)).toBe(true); // 7.200 acumulado desde o dia 1 e o teto do eixo (R$ 7.200 = 100%)
 expect(saiu.at(-1)![1]).toBe(64.9); // 2.380,50 de 7.200 = 33,06% da altura útil de 88
 expect(bloco().getByTestId("linha-entrou").props.stroke).not.toBe(bloco().getByTestId("linha-saiu").props.stroke);
});
it("mês passado: as linhas vão até o fim do mês (31 pontos, x até 300), com o mesmo total nas pontas", async () => {
 mockHoje = "2026-11-15"; await abrir(); await waitFor(() => expect(grafico()).toBeTruthy());
 const saiu = pontos("linha-saiu"); expect(saiu).toHaveLength(31); expect(saiu.at(-1)).toEqual([300, 64.9]);
});
it("saída AGENDADA depois de hoje (20/10, hoje 06/10): a linha vai até o dia 20 com degrau e a ponta bate com o Saiu da legenda", async () => {
 resumo = { ...F[RES], saiu: "2480.50" }; detalhes = () => resposta(200, { ...F[DET], saiu: "2480.50", dias: [...F[DET].dias, { dia: "2026-10-20", entrou: "0.00", saiu: "100.00" }] });
 await abrir(); await waitFor(() => expect(grafico()).toBeTruthy());
 const saiu = pontos("linha-saiu"); expect(saiu).toHaveLength(20); expect(saiu.at(-1)![0]).toBe(190); expect(saiu.at(-1)![1]).toBe(63.7); // 2.480,50 de 7.200: a ponta é o total
 expect(saiu[5]![1]).toBe(64.9); expect(saiu[18]![1]).toBe(64.9); // plano até o dia 19 e o degrau no 20
 expect(bloco().getByLabelText("menos 2480 reais e 50 centavos")).toBeTruthy();
});
it("resumo e detalhes que não fecham (saiu 99999 contra 2380,50): sem gráfico e sem erro, a legenda segue", async () => {
 resumo = { ...F[RES], saiu: "999.99" };
 await abrir(); await waitFor(() => expect(bloco().getByLabelText("menos 999 reais e 99 centavos")).toBeTruthy());
 for (let i = 0; i < 10; i++) await act(drenar);
 expect(grafico()).toBeNull(); expect(bloco().queryByRole("button", { name: "Tentar novamente" })).toBeNull(); expect(bloco().getByText("Saiu")).toBeTruthy();
});
it("valores ocultos: sem números nem gráfico (a forma revelaria o valor), só os nomes e a máscara", async () => {
 await abrir(); await waitFor(() => expect(grafico()).toBeTruthy());
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); });
 expect(grafico()).toBeNull(); expect(bloco().getByText("Gráfico oculto")).toBeTruthy();
 expect(bloco().getAllByLabelText("Valor oculto")).toHaveLength(2); expect(bloco().queryByLabelText(/reais/)).toBeNull(); expect(bloco().getByText("Entrou")).toBeTruthy(); expect(bloco().getByText("Saiu")).toBeTruthy();
});
it("série toda zero: gráfico vazio, sem erro, e a legenda segue", async () => {
 detalhes = () => resposta(200, { ...F[DET], dias: F[DET].dias.map((x: any) => ({ ...x, entrou: "0.00", saiu: "0.00" })) });
 await abrir(); await waitFor(() => expect(bloco().getByLabelText("mais 7200 reais")).toBeTruthy());
 for (let i = 0; i < 10; i++) await act(drenar);
 expect(grafico()).toBeNull(); expect(bloco().queryByText("Gráfico oculto")).toBeNull(); expect(bloco().queryByRole("button", { name: "Tentar novamente" })).toBeNull(); expect(bloco().getByText("Saiu")).toBeTruthy();
});
it("motivos do resumo e do detalhe aparecem em Avisos dentro do bloco (o do detalhe só se o resumo não o trouxe)", async () => {
 resumo = { ...F[RES], motivos: ["arredondamento_por_grupo"] }; detalhes = () => resposta(200, { ...F[DET], motivos: ["arredondamento_por_grupo", "moeda_presumida"] });
 await abrir(); await waitFor(() => expect(bloco().getAllByRole("button", { name: "Ver ressalvas" })).toHaveLength(2));
 for (const b of bloco().getAllByRole("button", { name: "Ver ressalvas" })) await act(async () => { fireEvent.press(b); });
 expect(bloco().getByText(/arredondados/)).toBeTruthy(); expect(bloco().getByText(/moeda de uma conta/)).toBeTruthy();
});
it.each([
 ["carregando", () => { const s = segurar(); detalhes = async () => { await s.promessa; return resposta(200, F[DET]); }; }, () => bloco().getByLabelText("Carregando dados")],
 ["com erro", () => { detalhes = () => resposta(500, {}); }, () => bloco().getByRole("button", { name: "Tentar novamente" })],
])("detalhes %s: Entrou e Saiu (com o total) seguem na tela; só o gráfico espera/falha", async (_n, preparar, sinal) => {
 preparar(); await abrir();
 await waitFor(() => expect(bloco().getByLabelText("mais 7200 reais")).toBeTruthy());
 for (let i = 0; i < 10; i++) await act(drenar);
 expect(bloco().getByLabelText("menos 2380 reais e 50 centavos")).toBeTruthy(); expect(grafico()).toBeNull();
 expect(sinal()).toBeTruthy();
});
it.each([
 ["/resumo", "bloco-resumo"], ["/metas", "bloco-metas"], ["/extrato", "Buscar lançamento"], ["/piggy", "Mensagem para o Piggy"],
] as const)("a aba %s continua sem o bloco", async (url, sinal) => {
 renderRouter("./app", { initialUrl: url });
 await waitFor(() => expect(screen.queryByTestId(sinal) ?? screen.queryByText(sinal) ?? screen.queryByLabelText(sinal)).toBeTruthy());
 expect(screen.queryByTestId("numeros-mes")).toBeNull(); expect(screen.queryByText("Entrou e saiu no mês")).toBeNull();
});
it.each([[1, "row"], [2, "column"]])("fontScale %s: Entrou e Saiu ficam em %s", async (fontScale, direcao) => {
 await act(async () => { Dimensions.set({ window: { ...janelaOriginal, width: 402, height: 874, fontScale } }); await drenar(); });
 await abrir(); await waitFor(() => expect(grafico()).toBeTruthy());
 const pai = (no: any) => { let p = no.parent; while (p && String(p.type) !== "View") p = p.parent; return p; };
 const linha = pai(pai(pai(bloco().getByText("Entrou")))); // texto > linha do nome (com o traço) > coluna > contêiner
 expect(pai(pai(pai(bloco().getByText("Saiu"))))).toBe(linha); expect(StyleSheet.flatten(linha.props.style).flexDirection).toBe(direcao);
});
it("abrir /gastos pede o mesmo número de vezes de antes do bloco (resumo 1, detalhes 2, medido sem o bloco): ele não faz fetch próprio", async () => {
 await abrir(); await waitFor(() => expect(grafico()).toBeTruthy());
 for (let i = 0; i < 10; i++) await act(drenar);
 const n = (rota: string) => fetchFalso.mock.calls.filter(([u]) => new URL(String(u)).pathname === rota).length;
 expect([n(RES), n(DET)]).toEqual([1, 2]);
});
