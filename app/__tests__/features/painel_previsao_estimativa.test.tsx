import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { faladoComSinal } from "@/ui/dinheiro";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import { __resetAvisoBanco } from "@/features/painel/avisoBanco";
import { F as FX, bloco, comBlocos, instalarCards, perguntar } from "./conversa_cards_apoio";
import { reiniciar } from "./conversa_apoio";
import v2 from "../../../tests/frontend/api_v2_respostas.json";
// "Gastos do dia a dia" (docs/paridade-app-dashboard.md): a linha mensal e as categorias na folha e no card do chat, e a parte no pior dia.
// Sem estimativa (null) a folha é a de sempre. Mesmas fixtures do Playwright do /painel (tests/frontend/api_v2_respostas.json).
jest.setTimeout(30_000);
const V = (v2 as any).previsao as Record<string, any>;
const PRO90 = V.pro90_com_estimativa, CURTA = V.pro30_estimativa_curta;
const saida = (c: number) => faladoComSinal(c, "saida");
let previsao: unknown;
beforeEach(async () => {
 __resetAvisoBanco(); prepararCaso(); desligarTrava(); previsao = fixture["/api/app/previsao"];
 await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/api/app/previsao") return resposta(200, previsao);
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
});
afterEach(() => jest.restoreAllMocks());
async function apertar(alvo: ReturnType<typeof screen.getByTestId>) { await act(async () => { fireEvent.press(alvo); await drenar(); }); }
const folha = () => within(screen.getByTestId("folha-sheet"));
async function abrirResumo() { renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("A partir de hoje, 06/10")).toBeTruthy()); await apertar(screen.getByTestId("bloco-hero")); }

it("folha: a linha mensal em saída, e as categorias só ao toque, com o valor de cada uma", async () => {
 previsao = PRO90; await abrirResumo();
 expect(folha().getByText("Gastos do dia a dia")).toBeTruthy(); expect(folha().getByText("por mês")).toBeTruthy(); expect(folha().getByLabelText(saida(33000))).toBeTruthy();
 expect(folha().queryByText("Mercado")).toBeNull();
 await apertar(screen.getByTestId("estimativa-variavel"));
 for (const [n, c] of [["Mercado", 18000], ["Transporte", 9000], ["Lazer", 6000]] as const) { expect(folha().getByText(n)).toBeTruthy(); expect(folha().getByLabelText(saida(c))).toBeTruthy(); }
});
it("as categorias saem na ordem do servidor", async () => {
 previsao = PRO90; await abrirResumo(); await apertar(screen.getByTestId("estimativa-variavel"));
 const nomes: string[] = PRO90.estimativa_variavel.categorias.map((c: { nome: string }) => c.nome);
 const lidos = folha().getAllByText(new RegExp(`^(${nomes.join("|")})$`)).map((n) => n.props.children);
 expect(lidos).toEqual(nomes);
});
it("privacidade: valores e barras das categorias somem; sem ela as barras aparecem", async () => {
 previsao = PRO90; await abrirResumo(); await apertar(screen.getByTestId("estimativa-variavel"));
 expect(folha().getAllByTestId("categoria-barra", { hidden: true })).toHaveLength(3); expect(folha().getByLabelText(saida(18000))).toBeTruthy();
 await apertar(screen.getByRole("button", { name: "Ocultar valores" }));
 expect(folha().queryAllByTestId("categoria-barra", { hidden: true })).toHaveLength(0); expect(folha().queryByLabelText(saida(18000))).toBeNull(); expect(folha().queryByLabelText(saida(33000))).toBeNull();
});
it("sem estimativa (null ou campo ausente): nenhuma linha, a folha é a de sempre", async () => {
 for (const p of [{ ...(fixture["/api/app/previsao"] as object), estimativa_variavel: null }, fixture["/api/app/previsao"]]) {
  previsao = p; const r = renderRouter("./app", { initialUrl: "/resumo" });
  await waitFor(() => expect(screen.getByText("A partir de hoje, 06/10")).toBeTruthy()); await apertar(screen.getByTestId("bloco-hero"));
  expect(screen.queryByTestId("estimativa-variavel")).toBeNull(); expect(screen.queryByText("Gastos do dia a dia")).toBeNull(); expect(folha().getByText("Hoje, com as premissas")).toBeTruthy();
  r.unmount();
 }
});
it("valor malformado na estimativa: sem a linha, e o resto da previsão aparece", async () => {
 previsao = { ...PRO90, estimativa_variavel: { ...PRO90.estimativa_variavel, despesa_mensal: "1E-7" } }; await abrirResumo();
 expect(screen.queryByTestId("estimativa-variavel")).toBeNull(); expect(folha().getByText("Hoje, com as premissas")).toBeTruthy();
});
it("pior dia com causa e estimativa: a lista abre com a causa e a parte do dia a dia", async () => {
 previsao = PRO90; await abrirResumo();
 expect(folha().getByText("desde 14/10")).toBeTruthy();
 await apertar(screen.getByTestId("pior-dia"));
 expect(folha().getByText("Aluguel")).toBeTruthy(); expect(folha().getAllByText("Gastos do dia a dia")).toHaveLength(2); expect(folha().getByLabelText(saida(6600))).toBeTruthy();
});
it("pior dia só pela estimativa (causas vazias, histórico curto): 'desde' e a lista abrem mesmo assim; detalhe segue 'por mês'", async () => {
 previsao = CURTA; await abrirResumo();
 expect(folha().getByText("desde 25/10")).toBeTruthy(); expect(folha().getByText("por mês")).toBeTruthy();
 await apertar(screen.getByTestId("pior-dia"));
 expect(folha().getAllByText("Gastos do dia a dia")).toHaveLength(2); expect(folha().getByLabelText(saida(13200))).toBeTruthy();
});
it("pior dia sem causa e sem parte da estimativa continua só a linha", async () => {
 previsao = { ...PRO90, pior_dia: { ...PRO90.pior_dia, causas: [], despesa_variavel: null } }; await abrirResumo();
 expect(screen.queryByTestId("pior-dia")).toBeNull(); expect(folha().queryByText(/^desde/)).toBeNull();
});

describe("card do chat (tudo inline, sem folha)", () => {
 beforeEach(async () => { await reiniciar(); instalarCards(); });
 it("a linha e as categorias vêm do bloco, e a parte no pior dia também", async () => {
  comBlocos(bloco("hero", { previsao: PRO90 })); await perguntar();
  const c = within(screen.getByTestId("bloco-hero"));
  expect(c.getByText("Gastos do dia a dia")).toBeTruthy(); expect(c.getByLabelText(saida(33000))).toBeTruthy();
  await apertar(screen.getByTestId("estimativa-variavel")); expect(c.getByText("Transporte")).toBeTruthy();
  await apertar(screen.getByTestId("pior-dia")); expect(c.getAllByText("Gastos do dia a dia")).toHaveLength(2);
 });
 it("estimativa malformada no bloco não invalida o card: a previsão desenha, sem a linha", async () => {
  comBlocos(bloco("hero", { previsao: { ...FX["/api/app/previsao"], estimativa_variavel: { despesa_mensal: "1E-7", categorias: [] } } })); await perguntar();
  const c = within(screen.getByTestId("bloco-hero"));
  expect(c.getByText("30 dias · 05/11/2026")).toBeTruthy(); expect(c.queryByTestId("estimativa-variavel")).toBeNull();
 });
});
