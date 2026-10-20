jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({ __esModule: true, default: () => "dark" }));
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { Dimensions, StyleSheet, View } from "react-native";
import { CORES_CATEGORIA } from "@/ui/tokens";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import v2 from "../../../tests/frontend/api_v2_respostas.json";
const V2 = v2 as unknown as { previsao: Record<string, unknown>; simulador: { habitos: Record<string, unknown> } };
// Incoerente com a base DE PROPÓSITO (a economia real de mercado a 50% seria R$ 90,00): a tela mostra o que o servidor mandou, não faz conta.
const RESP = { ...V2.simulador.habitos, economia_mensal: "12.34", cortes: [{ chave: "mercado", nome: "Mercado", despesa_mensal: "180.00", percentual: 50, economia_mensal: "9.99" }] };
let previsao: unknown, habitos: (o: RequestInit) => Response | Promise<Response>;
const posts = () => fetchFalso.mock.calls.filter(([u, o]) => (o as RequestInit)?.method === "POST" && new URL(String(u)).pathname === "/api/app/simulador/habitos").map(([, o]) => o as RequestInit);
const gets = () => fetchFalso.mock.calls.filter(([u, o]) => new URL(String(u)).pathname === "/api/app/previsao" && ((o as RequestInit)?.method ?? "GET") === "GET").length;
beforeEach(async () => {
 previsao = V2.previsao.pro90_com_estimativa; habitos = () => resposta(200, RESP);
 prepararCaso(); desligarTrava(); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
 fetchFalso.mockImplementation(async (url: string, o?: RequestInit) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === "/api/app/previsao") return resposta(200, previsao);
  if (path === "/api/app/simulador/habitos") return habitos(o ?? {});
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
});
const janela = Dimensions.get("window");
afterEach(() => { Dimensions.set({ window: janela }); jest.restoreAllMocks(); });
const abrir = async () => { renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getByLabelText("Corte em Mercado")).toBeTruthy()); };
const mover = (rotulo: string, valor: number) => act(async () => { fireEvent(screen.getByLabelText(rotulo), "valueChange", valor); await drenar(); });
const soltar = (rotulo: string, valor: number) => act(async () => { fireEvent(screen.getByLabelText(rotulo), "slidingComplete", valor); await drenar(); });
const corpo = (o: RequestInit) => JSON.parse(String(o.body));
it("a base é a estimativa da previsão; arrastar não envia; soltar envia UM POST com o corpo exato e a tela mostra o que o servidor devolveu", async () => {
 await abrir();
 for (const n of ["Mercado", "Transporte", "Lazer"]) expect(screen.getByLabelText(`Corte em ${n}`)).toBeTruthy();
 await mover("Corte em Mercado", 33.7); expect(screen.getByText("30%")).toBeTruthy(); // vai ao passo de 10 (o slider nativo entrega qualquer número)
 expect(posts()).toHaveLength(0);
 await soltar("Corte em Mercado", 49.6);
 expect(posts()).toHaveLength(1); expect(corpo(posts()[0]!)).toEqual({ cortes: [{ categoria: "mercado", percentual: 50 }] });
 expect(screen.getByText("Sobra por mês")).toBeTruthy(); expect(screen.getByLabelText("mais 12 reais e 34 centavos")).toBeTruthy();
 expect(screen.getByLabelText("mais 9 reais e 99 centavos")).toBeTruthy(); // economia da categoria, vinda da resposta
 expect(screen.getByText("Com corte")).toBeTruthy(); expect(screen.getByText("Sem corte")).toBeTruthy();
});
it("cortes de várias categorias vão juntos: o 2º POST leva Mercado e Transporte, na ordem da estimativa (não na do toque)", async () => {
 await abrir(); await soltar("Corte em Transporte", 50); await soltar("Corte em Mercado", 30);
 expect(corpo(posts()[1]!)).toEqual({ cortes: [{ categoria: "mercado", percentual: 30 }, { categoria: "transporte", percentual: 50 }] });
});
it("Zerar aborta o que está no ar, limpa o resultado e volta os sliders a 0", async () => {
 const lenta = segurar(); habitos = async () => { await lenta.promessa; return resposta(200, RESP); };
 await abrir(); await soltar("Corte em Mercado", 50);
 const o = posts()[0]!; expect(o.signal!.aborted).toBe(false);
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Zerar simulação" })); await drenar(); });
 expect(o.signal!.aborted).toBe(true); lenta.soltar(); await act(async () => { await drenar(); });
 expect(screen.queryByText("Sobra por mês")).toBeNull(); expect(screen.getByLabelText("Corte em Mercado").props.value).toBe(0);
});
it("o 2º toque aborta o 1º: a resposta do 1º não aparece", async () => {
 const lenta = segurar(); let n = 0;
 habitos = async () => { if (++n === 1) { await lenta.promessa; return resposta(200, { ...RESP, economia_mensal: "11.11" }); } return resposta(200, { ...RESP, economia_mensal: "22.22" }); };
 await abrir(); await soltar("Corte em Mercado", 30); await soltar("Corte em Mercado", 50);
 expect(posts()[0]!.signal!.aborted).toBe(true); expect(posts()[1]!.signal!.aborted).toBe(false);
 lenta.soltar(); await act(async () => { await drenar(); });
 expect(screen.getByLabelText("mais 22 reais e 22 centavos")).toBeTruthy(); expect(screen.queryByLabelText("mais 11 reais e 11 centavos")).toBeNull();
});
it("sem histórico (estimativa nula): texto curto e nenhum slider", async () => {
 previsao = V2.previsao.pro30; renderRouter("./app", { initialUrl: "/gastos" });
 await waitFor(() => expect(screen.getByText("Ainda sem histórico para estimar.")).toBeTruthy());
 expect(screen.queryByLabelText("Corte em Mercado")).toBeNull(); expect(posts()).toHaveLength(0);
});
it("422 categoria_fora_da_estimativa: relê a previsão (um GET a mais), avisa e não deixa número velho", async () => {
 await abrir(); await soltar("Corte em Mercado", 30); expect(screen.getByLabelText("mais 12 reais e 34 centavos")).toBeTruthy();
 habitos = () => resposta(422, { error: { code: "validation_error", message: "x", details: [{ loc: ["body", "cortes", 0, "categoria"], msg: "x", type: "categoria_fora_da_estimativa" }] } });
 const antes = gets(); await soltar("Corte em Mercado", 40);
 expect(screen.getByText("Seus gastos do dia a dia mudaram. Ajuste de novo.")).toBeTruthy(); expect(screen.queryByText("Sobra por mês")).toBeNull();
 await waitFor(() => expect(gets()).toBe(antes + 1));
});
it("422 + previsão nova: os cortes zeram, mas a mensagem 'mudaram' continua na tela", async () => {
 await abrir(); await soltar("Corte em Mercado", 30);
 habitos = () => resposta(422, { error: { code: "validation_error", message: "x", details: [{ loc: ["body", "cortes", 0, "categoria"], msg: "x", type: "categoria_fora_da_estimativa" }] } });
 previsao = { ...(V2.previsao.pro90_com_estimativa as object), calculado_em: "2026-10-06T15:05:00Z" };
 await soltar("Corte em Mercado", 40);
 await waitFor(() => expect(screen.getByLabelText("Corte em Mercado").props.value).toBe(0));
 expect(screen.getByText("Seus gastos do dia a dia mudaram. Ajuste de novo.")).toBeTruthy();
});
it("falha genérica: mensagem curta, sem número; o 200 seguinte mostra os números", async () => {
 habitos = () => resposta(500, {}); await abrir(); await soltar("Corte em Mercado", 30);
 expect(screen.getByText("Não deu para calcular. Tente de novo.")).toBeTruthy(); expect(screen.queryByText("Sobra por mês")).toBeNull();
 habitos = () => resposta(200, RESP); await soltar("Corte em Mercado", 40); expect(screen.getByLabelText("mais 12 reais e 34 centavos")).toBeTruthy();
});
it("valores ocultos: o resultado e a curva não mostram número", async () => {
 await abrir(); await soltar("Corte em Mercado", 50);
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); await drenar(); });
 expect(screen.queryByLabelText("mais 12 reais e 34 centavos")).toBeNull(); expect(within(screen.getByTestId("bloco-simulador")).getByText("Gráfico oculto")).toBeTruthy();
});
it("Para onde vai: a barra de cada categoria tem a cor da fatia dela no donut", async () => {
 renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getByRole("button", { name: /^Moradia/ })).toBeTruthy());
 const cores = (nome: RegExp) => screen.getByRole("button", { name: nome }).findAll((n) => n.type === View).map((n) => StyleSheet.flatten(n.props.style)?.backgroundColor).filter(Boolean);
 expect(cores(/^Alimentação/)).toContain(CORES_CATEGORIA[0]); expect(cores(/^Moradia/)).toContain(CORES_CATEGORIA[1]);
});
