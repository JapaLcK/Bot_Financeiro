// Bloco "Entrou e saiu no mês" no topo da aba Gastos (Entrou, Saiu, Saldo do mês, Guardado líquido); o Resumo não os tem (ver painel_curva_tela).
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
 prepararCaso(); desligarTrava(); resumo = F[RES]; detalhes = () => resposta(200, F[DET]);
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

it("topo da aba Gastos, antes de 'Para onde vai', com os 4 números e sem 'Ver lançamentos'", async () => {
 await abrir(); await waitFor(() => expect(bloco().getByLabelText("1200 reais")).toBeTruthy());
 for (const t of ["Entrou e saiu no mês", "Entrou", "Saiu", "Saldo do mês", "Guardado líquido"]) expect(bloco().getByText(t)).toBeTruthy();
 expect(bloco().getByLabelText("mais 7200 reais")).toBeTruthy(); expect(bloco().getByLabelText("menos 2380 reais e 50 centavos")).toBeTruthy();
 expect(bloco().getByLabelText("4819 reais e 50 centavos")).toBeTruthy(); // 7.200,00 - 2.380,50: a diferença, não a soma
 await waitFor(() => expect(screen.getByText("Para onde vai")).toBeTruthy());
 expect(screen.getAllByText(/^(Entrou e saiu no mês|Para onde vai)$/).map((t) => t.props.children)).toEqual(["Entrou e saiu no mês", "Para onde vai"]); // ordem do documento
 expect(screen.queryByRole("button", { name: "Ver lançamentos" })).toBeNull();
});
it("valores ocultos: os 4 números viram máscara", async () => {
 await abrir(); await waitFor(() => expect(bloco().getByLabelText("1200 reais")).toBeTruthy());
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); });
 expect(bloco().getAllByLabelText("Valor oculto")).toHaveLength(4); expect(bloco().queryByLabelText(/reais/)).toBeNull();
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
])("detalhes %s: Entrou, Saiu e Saldo seguem na tela; só o pedaço do Guardado líquido espera/falha", async (_n, preparar, sinal) => {
 preparar(); await abrir();
 await waitFor(() => expect(bloco().getByLabelText("mais 7200 reais")).toBeTruthy());
 for (let i = 0; i < 10; i++) await act(drenar);
 expect(bloco().getByLabelText("4819 reais e 50 centavos")).toBeTruthy(); expect(bloco().queryByText("Guardado líquido")).toBeNull();
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
 await abrir(); await waitFor(() => expect(bloco().getByLabelText("1200 reais")).toBeTruthy());
 const pai = (no: any) => { let p = no.parent; while (p && String(p.type) !== "View") p = p.parent; return p; };
 const linha = pai(pai(bloco().getByText("Entrou")));
 expect(pai(pai(bloco().getByText("Saiu")))).toBe(linha); expect(StyleSheet.flatten(linha.props.style).flexDirection).toBe(direcao);
});
it("abrir /gastos pede o mesmo número de vezes de antes do bloco (resumo 1, detalhes 2, medido sem o bloco): ele não faz fetch próprio", async () => {
 await abrir(); await waitFor(() => expect(bloco().getByLabelText("1200 reais")).toBeTruthy());
 for (let i = 0; i < 10; i++) await act(drenar);
 const n = (rota: string) => fetchFalso.mock.calls.filter(([u]) => new URL(String(u)).pathname === rota).length;
 expect([n(RES), n(DET)]).toEqual([1, 2]);
});
