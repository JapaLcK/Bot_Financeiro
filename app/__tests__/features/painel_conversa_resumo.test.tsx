// "Hoje" fixo: a fixture é de 2026-10 (dias 1 a 6) e o CI roda em qualquer data.
jest.mock("@/features/painel/catalogo", () => ({ ...jest.requireActual("@/features/painel/catalogo"), hojeSP: () => "2026-10-06" }));
import { screen, waitFor, within, act } from "expo-router/testing-library";
import { mesAtual, mesesAnteriores } from "@/features/painel/catalogo";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { abrir, caminho, noResumo, reiniciar } from "./conversa_apoio";
import { F, bloco, comBlocos, instalarCards, perguntar } from "./conversa_cards_apoio";
// O card Resumo do chat desenha SÓ o bloco: o bloco não traz o mês anterior (P2), então a curva sai sem "vs … mês anterior" mesmo que o provider
// tenha o mês anterior. No painel (controle positivo) o mesmo provider dá a comparação.
jest.setTimeout(30_000);
const DET = "/api/app/mes-detalhes", ANTERIOR = mesesAnteriores(mesAtual(), 2)[1]!;
const dia = (d: string, saiu: string) => ({ dia: d, entrou: "0.00", saiu });
const SETEMBRO = { ...F[DET], mes: "2026-09", ate: "2026-09-30", saiu: "4110.00", motivos: [], dias: [dia("2026-09-01", "1000.00"), dia("2026-09-02", "700.00"), dia("2026-09-03", "410.00"), dia("2026-09-05", "1000.00"), dia("2026-09-20", "1000.00")] };
beforeEach(async () => {
  prepararCaso(); desligarTrava(); await reiniciar(); instalarCards(true);
  const base = fetchFalso.getMockImplementation()!;
  fetchFalso.mockImplementation((u: string, r: RequestInit) => caminho(u) === DET && new URL(u).searchParams.get("mes") === ANTERIOR ? Promise.resolve(resposta(200, SETEMBRO)) : base(u, r));
});
afterEach(() => { jest.restoreAllMocks(); });

it("controle positivo: no painel o mesmo provider dá 'vs … mês anterior'", async () => {
  await abrir(); await noResumo(); const c = () => within(screen.getByTestId("bloco-resumo"));
  await waitFor(() => expect(c().getByText("mês anterior")).toBeTruthy()); expect(c().getByText("vs")).toBeTruthy();
});
it("no chat: só 'gasto até hoje' e a curva do mês, sem 'vs … mês anterior', mesmo com o provider carregado", async () => {
  comBlocos(bloco("resumo")); await perguntar(); await act(drenar);
  expect(fetchFalso.mock.calls.some(([u]) => caminho(u) === DET && new URL(String(u)).searchParams.get("mes") === ANTERIOR)).toBe(true); // o provider TEM o mês anterior
  const c = within(screen.getByTestId("bloco-resumo"));
  expect(c.getByText("gasto até hoje")).toBeTruthy(); expect(c.queryByText("vs")).toBeNull(); expect(c.queryByText("mês anterior")).toBeNull(); expect(c.queryByText("abaixo")).toBeNull();
});
