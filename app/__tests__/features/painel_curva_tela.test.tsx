// O painel herda o tema do provider da raiz; a curva é lida no tema escuro.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({ __esModule: true, default: () => "dark" }));
// "Hoje" fixo: o fixture é de 2026-10 (dias 1 a 6) e o CI roda em qualquer data.
let mockHoje = "2026-10-06";
jest.mock("@/features/painel/catalogo", () => ({ ...jest.requireActual("@/features/painel/catalogo"), hojeSP: () => mockHoje }));

import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";

const F = fixture as Record<string, any>, DET = "/api/app/mes-detalhes", RES = "/api/app/resumo-do-mes";
const H = { includeHiddenElements: true }; // a curva é oculta ao VoiceOver de propósito
beforeEach(async () => {
 mockHoje = "2026-10-06";
 prepararCaso(); desligarTrava(); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  return resposta(200, F[path] ?? {});
 });
});
// Troca o corpo de um recurso do fixture (`fn` recebe o original).
function com(path: string, fn: (o: any) => any) {
 const base = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string) => new URL(url).pathname === path ? resposta(200, fn(F[path])) : base(url));
}
const dia = (d: string, saiu: string) => ({ dia: d, entrou: "0.00", saiu });
const medir = () => act(async () => { fireEvent(screen.getByTestId("curva-acumulada", H), "layout", { nativeEvent: { layout: { width: 300, height: 120 } } }); });
async function abrir() {
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByText("Guardado líquido")).toBeTruthy());
}
const ROTULO = "Gasto acumulado no mês";
const LEGENDA = "Setembro fechou em R$ 4.110,00";

it("Resumo do mês desenha a curva do fixture (dias 1 a 6 somando 2.380,50), com a legenda do mês passado, e ela some com valores ocultos", async () => {
 await abrir();
 expect(screen.getByTestId("curva-acumulada", H)).toBeTruthy();
 expect(screen.getByText(LEGENDA)).toBeTruthy();
 expect(screen.getByText(ROTULO)).toBeTruthy();
 await medir();
 expect(screen.getByTestId("ponto-hoje", H)).toBeTruthy(); // 06/10 é "hoje"
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); });
 expect(screen.queryByTestId("curva-acumulada", H)).toBeNull();
 expect(screen.queryByText(LEGENDA)).toBeNull(); // a legenda carrega um valor: some junto
 expect(screen.queryByText(ROTULO)).toBeNull(); // o rótulo é do gráfico: some junto
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Mostrar valores" })); });
 expect(screen.getByTestId("curva-acumulada", H)).toBeTruthy();
 expect(screen.getByText(ROTULO)).toBeTruthy();
});
it("mês passado: a curva aparece, sem o ponto de hoje", async () => {
 mockHoje = "2026-11-15";
 await abrir();
 await medir();
 expect(screen.getByTestId("curva-linha", H)).toBeTruthy();
 expect(screen.queryByTestId("ponto-hoje", H)).toBeNull();
});
it("cartão: compra de setembro (29/09) na fatura de outubro entra na base e a curva aparece quando a soma fecha com o Saiu do card (2.480,50)", async () => {
 com(RES, (o) => ({ ...o, saiu: "2480.50" }));
 com(DET, (o) => ({ ...o, saiu: "2480.50", dias: [dia("2026-09-29", "100.00"), ...o.dias] }));
 await abrir();
 expect(screen.getByTestId("curva-acumulada", H)).toBeTruthy();
});
it.each([
 ["a compra de setembro não está no Saiu do card", RES, (o: any) => o, DET, (o: any) => ({ ...o, dias: [dia("2026-09-29", "100.00"), ...o.dias] })],
 ["o detalhe fecha 1 centavo a menos que o resumo", RES, (o: any) => o, DET, (o: any) => ({ ...o, saiu: "2380.49", dias: o.dias.map((x: any, i: number) => i === 5 ? { ...x, saiu: "279.99" } : x) })],
 ["a soma dos dias não fecha com o Saiu (outra moeda no total legado)", RES, (o: any) => o, DET, (o: any) => ({ ...o, dias: o.dias.slice(0, 5) })],
 ["o detalhe é de outro mês", RES, (o: any) => o, DET, (o: any) => ({ ...o, mes: "2026-09" })],
 ["o plano cortou o histórico dentro do mês (inicio_do_historico)", RES, (o: any) => o, DET, (o: any) => ({ ...o, motivos: [...o.motivos, "inicio_do_historico"] })],
 ["tem lançamento depois de hoje", RES, (o: any) => o, DET, (o: any) => ({ ...o, dias: o.dias.slice(0, 5).concat(dia("2026-10-09", "280.00")) })],
])("sem curva e sem legenda, e o resto do card segue como estava: %s", async (_n, p1, f1, p2, f2) => {
 com(p1, f1); com(p2, f2);
 await abrir();
 expect(screen.queryByTestId("curva-acumulada", H)).toBeNull();
 expect(screen.queryByText(LEGENDA)).toBeNull();
 expect(screen.queryByText(ROTULO)).toBeNull();
 expect(screen.getByText("Saldo do mês")).toBeTruthy();
});
it("sem mês anterior no resumo: curva sem legenda", async () => {
 com(RES, (o) => ({ ...o, anterior: null }));
 await abrir();
 expect(screen.getByTestId("curva-acumulada", H)).toBeTruthy();
 expect(screen.getByText(ROTULO)).toBeTruthy(); // o rótulo não depende da legenda do mês passado
 expect(screen.queryByText(/fechou em/)).toBeNull();
});
