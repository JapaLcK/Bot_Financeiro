import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { router } from "expo-router";
import { StyleSheet } from "react-native";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import arredondamento from "./painel_arredondamento.fixture.json";
import { fluxoFim } from "./conversa_apoio";
const aviso = "Os valores de cada grupo são arredondados. A soma exibida pode diferir em centavos do total.";
let uso: number, limite: number, detalhes: unknown;
function servidor() {
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === "/api/app/perfil") return resposta(200, { perfil: "padrao" });
  if (path === "/api/app/mes-detalhes") return resposta(200, detalhes);
  if (path === "/ai/chat/stream") {
   uso++;
   return fluxoFim("Resposta exclusiva", uso, limite);
  }
  if (path === "/ai/messages") return resposta(200, { messages: [], usage: { used: uso, limit: limite } });
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
}
beforeEach(async () => {
 prepararCaso(); desligarTrava(); uso = 99; limite = 100;
 detalhes = fixture["/api/app/mes-detalhes"];
 await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor();
});
afterEach(() => jest.restoreAllMocks());
async function apertar(rotulo: string) { await act(async () => { fireEvent.press(screen.getByRole("button", { name: rotulo })); await drenar(); }); }
async function abrirRessalvas(n: number) {
 await waitFor(() => expect(screen.getAllByRole("button", { name: "Ver ressalvas" })).toHaveLength(n));
 for (const b of screen.getAllByRole("button", { name: "Ver ressalvas" })) await act(async () => { fireEvent.press(b); await drenar(); });
}
async function enviar() {
 await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy")).toBeTruthy());
 await act(async () => { fireEvent.changeText(screen.getByLabelText("Mensagem para o Piggy"), "Pergunta exclusiva"); await drenar(); });
 await apertar("Enviar mensagem"); await waitFor(() => expect(screen.getAllByText("Resposta exclusiva").length).toBeGreaterThan(0));
}
it("POST na última mensagem atualiza imediatamente a cota e bloqueia novo envio", async () => {
 renderRouter("./app", { initialUrl: "/piggy" }); await enviar();
 expect(screen.getByText("100 de 100 mensagens utilizadas")).toBeTruthy(); expect(screen.queryByText("99 de 100 mensagens utilizadas")).toBeNull();
 await act(async () => { fireEvent.changeText(screen.getByLabelText("Mensagem para o Piggy"), "Mais uma"); });
 expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled(); await apertar("Enviar mensagem");
 expect(fetchFalso.mock.calls.filter(([u]) => new URL(String(u)).pathname === "/ai/chat/stream")).toHaveLength(1);
});
it("drilldown Sem categoria preserva categoria= no GET e apresenta o filtro", async () => {
 detalhes = { ...fixture["/api/app/mes-detalhes"], categorias: [{ categoria: null, valor: "1.00", quantidade: 1 }] };
 renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getByRole("button", { name: "Sem categoria" })).toBeTruthy());
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Sem categoria" })); await drenar(); });
 await waitFor(() => expect(screen.getByLabelText("Buscar lançamento")).toBeTruthy());
 const url = fetchFalso.mock.calls.map(([u]) => new URL(String(u))).find((u) => u.pathname === "/api/app/lancamentos")!;
 expect(url.searchParams.has("categoria")).toBe(true); expect(url.searchParams.get("categoria")).toBe(""); expect(screen.getAllByText("Sem categoria").length).toBeGreaterThan(0);
});
it("extrato sem drilldown continua sem parâmetro categoria", async () => {
 renderRouter("./app", { initialUrl: "/extrato" }); await waitFor(() => expect(screen.getByLabelText("Buscar lançamento")).toBeTruthy());
 const url = fetchFalso.mock.calls.map(([u]) => new URL(String(u))).find((u) => u.pathname === "/api/app/lancamentos")!;
 expect(url.searchParams.has("categoria")).toBe(false); expect(screen.queryByText("Sem categoria")).toBeNull();
});
it("calendário distingue meses de compra e mantém a data civil no drilldown", async () => {
 detalhes = { ...fixture["/api/app/mes-detalhes"], dias: [{ dia: "2026-09-03", entrou: "0.00", saiu: "1.00" }, { dia: "2026-10-03", entrou: "0.00", saiu: "2.00" }] };
 renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getByTestId("calendario-dia-2026-09-03")).toBeTruthy());
 expect(within(screen.getByTestId("calendario-dia-2026-09-03")).getByText("03/09")).toBeTruthy(); expect(within(screen.getByTestId("calendario-dia-2026-10-03")).getByText("03/10")).toBeTruthy();
 await act(async () => { fireEvent.press(screen.getByTestId("calendario-dia-2026-09-03")); await drenar(); });
 await waitFor(() => expect(screen.getByLabelText("Buscar lançamento")).toBeTruthy()); expect(screen.getByText("03/09/2026")).toBeTruthy();
});
it("grupos arredondados apresentam aviso em Categorias, Calendário e nos números do mês", async () => {
 detalhes = arredondamento; renderRouter("./app", { initialUrl: "/gastos" }); await abrirRessalvas(4); // 3 blocos do `detalhes` + o "Entrou e saiu no mês" (o aviso do detalhe que o resumo não traz)
 await waitFor(() => expect(screen.getAllByText(aviso)).toHaveLength(4)); expect(screen.queryByText("arredondamento por grupo")).toBeNull();
});
it("patrimônio preserva total oficial e informa diferença de centavos entre grupos", async () => {
 const impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => new URL(url).pathname === "/api/app/patrimonio" ? resposta(200, { ...fixture["/api/app/patrimonio"], total: "2.01", partes: { carteira: "1.01", bancos: "1.01", investimentos_banco: "0.00", caixinhas: "0.00", investimentos_manuais: "0.00" }, motivos: ["arredondamento_por_grupo"], historico: [] }) : impl(url, req));
 renderRouter("./app", { initialUrl: "/resumo" }); await abrirRessalvas(1); await waitFor(() => expect(screen.getByText(aviso)).toBeTruthy());
 expect(screen.getByLabelText("2 reais e 1 centavo")).toBeTruthy(); expect(screen.queryByLabelText("2 reais e 2 centavos")).toBeNull();
});
it("foto histórica mantém seu aviso mesmo quando patrimônio atual não diverge", async () => {
 const impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => new URL(url).pathname === "/api/app/patrimonio" ? resposta(200, { ...fixture["/api/app/patrimonio"], motivos: [], historico: [{ ...fixture["/api/app/patrimonio"].historico[0], total: "2.01", motivos: ["arredondamento_por_grupo"] }] }) : impl(url, req));
 renderRouter("./app", { initialUrl: "/resumo" }); await abrirRessalvas(1); await waitFor(() => expect(screen.getByText(aviso)).toBeTruthy());
 expect(screen.getByLabelText("2 reais e 1 centavo")).toBeTruthy();
});
it("ação com ícone usa layout horizontal estático e alvo de pelo menos 44 pontos", async () => {
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: "Organizar" })).toBeTruthy());
 const botao = screen.getByRole("button", { name: "Organizar" });
 expect(typeof botao.props.style).not.toBe("function"); expect(StyleSheet.flatten(botao.props.style)).toMatchObject({ minHeight: 44, minWidth: 44 });
 expect(botao.findAll((n) => n.props.style && StyleSheet.flatten(n.props.style)?.flexDirection === "row").length).toBeGreaterThan(0);
 await apertar("Organizar"); expect(screen.getByText("Organizar painel")).toBeTruthy();
});
it("refresh do painel NÃO aborta o POST do chat: a resposta e a cota chegam depois", async () => {
 uso = 0; const atraso = segurar();
 const impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  if (new URL(url).pathname === "/ai/chat/stream") { await atraso.promessa; uso = 85; return fluxoFim("Resposta que sobrevive ao refresh", 85); }
  return impl(url, req);
 });
 renderRouter("./app", { initialUrl: "/piggy" }); await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy")).toBeTruthy());
 await waitFor(() => expect(screen.getByRole("button", { name: /Onde eu consigo economizar/ })).toBeEnabled());
 await act(async () => { fireEvent.changeText(screen.getByLabelText("Mensagem para o Piggy"), "Pergunta exclusiva"); }); await apertar("Enviar mensagem");
 const post = fetchFalso.mock.calls.find(([url]) => new URL(String(url)).pathname === "/ai/chat/stream")!;
 await act(async () => { router.navigate("/resumo"); await drenar(); }); await waitFor(() => expect(screen.getByTestId("tela").props.refreshControl).toBeTruthy());
 await act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); await drenar(); });
 expect(post[1].signal.aborted).toBe(false);
 atraso.soltar(); await act(async () => { await drenar(); });
 await act(async () => { router.navigate("/piggy"); await drenar(); });
 await waitFor(() => expect(screen.getByText("Resposta que sobrevive ao refresh")).toBeTruthy()); await waitFor(() => expect(screen.getByText("85 de 100 mensagens utilizadas")).toBeTruthy());
});
