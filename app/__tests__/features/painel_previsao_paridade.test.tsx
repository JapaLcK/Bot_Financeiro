import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import { __resetAvisoBanco } from "@/features/painel/avisoBanco";
// O que o /painel mostra da previsão e o app agora também (docs/paridade-app-dashboard.md, linha "Saldo previsto"):
// âncora, bancos fora do cálculo, pior dia com desde e causas, "como foi feita" e compromissos agrupados.
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
const F = fixture["/api/app/previsao"], base = F.compromissos[0]!.ocorrencias[0]!;
const oc = (chave: string, ciclo: string, data: string | null, valor: string | null, extra: object = {}) => ({ ...base, chave, ciclo, data, valor, ...extra });
async function apertar(alvo: ReturnType<typeof screen.getByTestId>) { await act(async () => { fireEvent.press(alvo); await drenar(); }); }
const hero = () => within(screen.getByTestId("bloco-hero"));
async function abrirResumo() { renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("A partir de hoje, 06/10")).toBeTruthy()); }

it("mostra o saldo de hoje com as premissas (âncora)", async () => {
 previsao = { ...F, ancora: { data: "2026-10-06", saldo: "9123.45" } };
 await abrirResumo();
 expect(hero().getByText("Hoje, com as premissas")).toBeTruthy(); expect(hero().getByLabelText(/9123/)).toBeTruthy();
});
it("sem âncora, nada de 'Hoje, com as premissas'", async () => {
 previsao = { ...F, ancora: null };
 await abrirResumo(); expect(hero().queryByText("Hoje, com as premissas")).toBeNull();
});
it("bancos conectados fora do cálculo: avisa no card", async () => {
 previsao = { ...F, base: { ...F.base, bancos_excluidos: true } };
 await abrirResumo(); expect(hero().getByText("Bancos conectados fora do cálculo")).toBeTruthy();
});
it("sem bancos excluídos, nenhum aviso de banco fora do cálculo", async () => {
 await abrirResumo(); expect(hero().queryByText("Bancos conectados fora do cálculo")).toBeNull();
});
it("pior dia diz desde quando e, ao toque, as causas com data", async () => {
 previsao = { ...F, pior_dia: { data: "2026-10-25", saldo: "-120.00", desde: "2026-10-14", causas: [oc("alug", "2026-10", "2026-10-15", "1800.00", { nome: "Aluguel", tipo: "gasto_fixo" }), oc("fat", "2026-10", "2026-10-20", "640.00", { nome: "ultraviolet-black" })] } };
 await abrirResumo();
 expect(hero().getByText("Menor saldo previsto · 25/10/2026")).toBeTruthy(); expect(hero().getByText("desde 14/10")).toBeTruthy();
 expect(hero().queryByText("Aluguel")).toBeNull();
 await apertar(screen.getByTestId("pior-dia"));
 expect(hero().getByText("Aluguel")).toBeTruthy(); expect(hero().getByText("15/10")).toBeTruthy(); expect(hero().getByText("Ultraviolet Black")).toBeTruthy();
 expect(hero().getByLabelText(/menos 1800 reais/)).toBeTruthy();
});
it("pior dia sem causas é só a linha, sem nada para abrir", async () => {
 previsao = { ...F, pior_dia: { data: "2026-10-25", saldo: "120.00", desde: null, causas: [] } };
 await abrirResumo();
 expect(hero().getByText("Menor saldo previsto · 25/10/2026")).toBeTruthy(); expect(screen.queryByTestId("pior-dia")).toBeNull(); expect(hero().queryByText(/^desde/)).toBeNull();
});
it("'Como foi feita' fica fechado e, aberto, mostra a base, as premissas e a cobertura", async () => {
 const p = "Projeção condicional; receitas previstas não são garantidas."; const erro = jest.spyOn(console, "error");
 previsao = { ...F, premissas: [p, p], cobertura: { fontes_incompletas: ["gastos_variaveis_nao_estimados"], janela_conferencia_inicio: "2026-09-01", inclui_estimativa_variavel: false } };
 await abrirResumo();
 expect(hero().queryByText(/receitas previstas não são garantidas/)).toBeNull(); expect(hero().queryByText("Saldo de partida")).toBeNull();
 await apertar(screen.getByTestId("como-foi-feita"));
 expect(hero().getAllByText(`• ${p}`)).toHaveLength(2); expect(erro.mock.calls.flat().join(" ")).not.toMatch(/same key/); // premissa repetida não quebra a lista
 expect(hero().getByText("Saldo de partida")).toBeTruthy(); expect(hero().getByText("Carteira + 2 contas do Open Finance")).toBeTruthy();
 expect(hero().getByText("Conferência desde 01/09")).toBeTruthy(); expect(hero().queryByText(/Há informações incompletas/)).toBeNull();
});
it("compromissos: grupo de valores iguais mostra o valor e quantas vezes; uma ocorrência é uma linha só", async () => {
 previsao = { ...F, compromissos: [
  { chave: "net", fonte: "gasto_recorrente", nome: "Netflix", primeira_data: "2026-10-10", ultima_data: "2026-11-10", ocorrencias: [oc("n1", "2026-10", "2026-10-10", "55.90", { nome: "Netflix", tipo: "gasto_fixo" }), oc("n2", "2026-11", "2026-11-10", "55.90", { nome: "Netflix", tipo: "gasto_fixo" })] },
  F.compromissos[0],
 ] };
 await abrirResumo();
 const card = within(screen.getByTestId("bloco-compromissos"));
 expect(card.getByText("Netflix")).toBeTruthy(); expect(card.getByText("2 vezes · 10/10 a 10/11")).toBeTruthy(); expect(card.getByLabelText(/menos 55 reais e 90 centavos/)).toBeTruthy();
 expect(card.getByText("Fatura Nubank")).toBeTruthy(); expect(card.getByText("15/10/2026")).toBeTruthy();
 expect(card.queryByText("10/11/2026")).toBeNull();
 await apertar(screen.getByTestId("grupo-net"));
 expect(card.getByText("10/10/2026")).toBeTruthy(); expect(card.getByText("10/11/2026")).toBeTruthy();
});
// Sem data fica no fim do grupo: o intervalo vai até a última data conhecida, não some (o simulador mostrou "10/10" sozinho).
it("compromissos: grupo com ocorrência sem data mostra o intervalo até a última data conhecida", async () => {
 const n = (chave: string, data: string | null) => oc(chave, "x", data, "55.90", { nome: "Netflix", tipo: "gasto_fixo" });
 previsao = { ...F, compromissos: [{ chave: "net", fonte: "gasto_recorrente", nome: "Netflix", primeira_data: "2026-10-10", ultima_data: "2026-11-10", ocorrencias: [n("n1", "2026-10-10"), n("n3", null), n("n2", "2026-11-10")] }] };
 await abrirResumo();
 expect(within(screen.getByTestId("bloco-compromissos")).getByText("3 vezes · 10/10 a 10/11")).toBeTruthy();
});
it("compromissos: o grupo fechado já diz quantas ocorrências ficam fora do cálculo", async () => {
 const n = (chave: string, data: string, incluida: boolean) => oc(chave, "x", data, "55.90", { nome: "Netflix", tipo: "gasto_fixo", incluida_no_calculo: incluida });
 previsao = { ...F, compromissos: [{ chave: "net", fonte: "gasto_recorrente", nome: "Netflix", primeira_data: "2026-10-10", ultima_data: "2026-11-10", ocorrencias: [n("n1", "2026-10-10", true), n("n2", "2026-11-10", false)] }] };
 await abrirResumo();
 expect(within(screen.getByTestId("bloco-compromissos")).getByText("2 vezes · 10/10 a 10/11 · 1 fora do cálculo")).toBeTruthy();
});
it("pior dia sem causas não diz 'desde', como o /painel", async () => {
 previsao = { ...F, pior_dia: { data: "2026-10-25", saldo: "120.00", desde: "2026-10-14", causas: [] } };
 await abrirResumo(); expect(hero().getByText("Menor saldo previsto · 25/10/2026")).toBeTruthy(); expect(hero().queryByText(/^desde/)).toBeNull();
});
it("com valores ocultos, nenhum dígito das causas nem da base aparece", async () => {
 previsao = { ...F, pior_dia: { data: "2026-10-25", saldo: "-120.00", desde: "2026-10-14", causas: [oc("alug", "2026-10", "2026-10-15", "1800.00", { nome: "Aluguel", tipo: "gasto_fixo" })] } };
 await abrirResumo();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); await drenar(); });
 const semDigitos = () => { expect(hero().queryByLabelText(/1800|9400|reais/)).toBeNull(); expect(hero().queryByText(/1\.800|9\.400/)).toBeNull(); };
 await apertar(screen.getByTestId("pior-dia")); expect(hero().getByText("Aluguel")).toBeTruthy(); semDigitos();
 await apertar(screen.getByTestId("como-foi-feita")); expect(hero().getByText("Saldo de partida")).toBeTruthy(); semDigitos();
});
