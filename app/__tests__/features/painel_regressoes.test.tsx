import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { StyleSheet } from "react-native";
import { guardarCredenciais } from "@/storage/secure";
import { metasSchema } from "@/api/schemas/painel";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import metasBanco from "./painel_metas_banco.fixture.json";
import arredondamento from "./painel_arredondamento.fixture.json";
import janela from "./painel_chat_janela.fixture.json";
type Mensagem = { role: "user" | "assistant"; content: string; created_at: null };
const aviso = "Os valores de cada grupo são arredondados. A soma exibida pode diferir em centavos do total.";
let historico: Mensagem[], uso: number, limite: number, persistir: boolean, detalhes: unknown;
function servidor() {
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === "/api/app/perfil") return resposta(200, { perfil: "padrao" });
  if (path === "/api/app/mes-detalhes") return resposta(200, detalhes);
  if (path === "/goals/1/status") return resposta(200, metasBanco);
  if (path === "/ai/chat") {
   uso++;
   if (persistir) historico.push({ role: "user", content: "Pergunta exclusiva", created_at: null }, { role: "assistant", content: "Resposta exclusiva", created_at: null });
   return resposta(200, { reply: "Resposta exclusiva", usage: { used: uso, limit: limite } });
  }
  if (path === "/ai/messages") return resposta(200, { messages: [...historico], usage: { used: uso, limit: limite } });
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
}
beforeEach(async () => {
 prepararCaso(); desligarTrava(); historico = []; uso = 99; limite = 100; persistir = true;
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
async function atualizar() {
 await act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); await drenar(); });
 await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy")).toBeTruthy());
}
it("POST na última mensagem atualiza imediatamente a cota e bloqueia novo envio", async () => {
 renderRouter("./app", { initialUrl: "/piggy" }); await enviar();
 expect(screen.getByText("100 de 100 mensagens utilizadas")).toBeTruthy(); expect(screen.queryByText("99 de 100 mensagens utilizadas")).toBeNull();
 await act(async () => { fireEvent.changeText(screen.getByLabelText("Mensagem para o Piggy"), "Mais uma"); });
 expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeDisabled(); await apertar("Enviar mensagem");
 expect(fetchFalso.mock.calls.filter(([u]) => new URL(String(u)).pathname === "/ai/chat")).toHaveLength(1);
});
it("histórico confirmado após refresh substitui o par local sem duplicação", async () => {
 renderRouter("./app", { initialUrl: "/piggy" }); await enviar(); await atualizar(); await atualizar();
 expect(screen.getAllByText("Pergunta exclusiva")).toHaveLength(1); expect(screen.getAllByText("Resposta exclusiva")).toHaveLength(1);
});
it("duas perguntas iguais legítimas permanecem duas após confirmação", async () => {
 uso = 0; renderRouter("./app", { initialUrl: "/piggy" }); await enviar(); await enviar(); await atualizar();
 expect(screen.getAllByText("Pergunta exclusiva")).toHaveLength(2); expect(screen.getAllByText("Resposta exclusiva")).toHaveLength(2);
});
it("snapshot GET bem-sucedido substitui o eco mesmo sem o turno no histórico", async () => {
 uso = 0; persistir = false; renderRouter("./app", { initialUrl: "/piggy" }); await enviar(); await atualizar();
 expect(screen.queryByText("Resposta exclusiva")).toBeNull();
 historico.push({ role: "user", content: "Pergunta exclusiva", created_at: null }, { role: "assistant", content: "Resposta exclusiva", created_at: null });
 await atualizar(); expect(screen.getAllByText("Resposta exclusiva")).toHaveLength(1);
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
it("payload bancário com 500.005 é aceito e apresentado na rota Metas", async () => {
 expect(metasSchema.parse(metasBanco).goals[0]!.balance).toBe(50001);
 renderRouter("./app", { initialUrl: "/metas" }); await waitFor(() => expect(screen.getByText("Espelhada")).toBeTruthy());
 expect(screen.queryByText("Recebemos dados incompatíveis. Tente atualizar.")).toBeNull(); expect(screen.getByLabelText("500 reais e 1 centavo")).toBeTruthy();
});
it("indicador tight e prazo da meta têm cópia em português", async () => {
 const impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => new URL(url).pathname === "/goals/1/status" ? resposta(200, { ...metasBanco, goals: [{ ...metasBanco.goals[0], is_goal: true, indicator: "tight", target_date: "2026-11-03" }] }) : impl(url, req));
 renderRouter("./app", { initialUrl: "/metas" }); await waitFor(() => expect(screen.getByText("Prazo apertado")).toBeTruthy());
 expect(screen.getByText("03/11/2026")).toBeTruthy(); expect(screen.queryByText("tight")).toBeNull();
});
it("calendário distingue meses de compra e mantém a data civil no drilldown", async () => {
 detalhes = { ...fixture["/api/app/mes-detalhes"], dias: [{ dia: "2026-09-03", entrou: "0.00", saiu: "1.00" }, { dia: "2026-10-03", entrou: "0.00", saiu: "2.00" }] };
 renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getByTestId("calendario-dia-2026-09-03")).toBeTruthy());
 expect(within(screen.getByTestId("calendario-dia-2026-09-03")).getByText("03/09")).toBeTruthy(); expect(within(screen.getByTestId("calendario-dia-2026-10-03")).getByText("03/10")).toBeTruthy();
 await act(async () => { fireEvent.press(screen.getByTestId("calendario-dia-2026-09-03")); await drenar(); });
 await waitFor(() => expect(screen.getByLabelText("Buscar lançamento")).toBeTruthy()); expect(screen.getByText("03/09/2026")).toBeTruthy();
});
it("grupos arredondados apresentam aviso em Categorias e Calendário", async () => {
 detalhes = arredondamento; renderRouter("./app", { initialUrl: "/gastos" }); await abrirRessalvas(3);
 await waitFor(() => expect(screen.getAllByText(aviso)).toHaveLength(3)); expect(screen.queryByText("arredondamento por grupo")).toBeNull();
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
it("janela HTTP real de 30 mensagens substitui par antigo idêntico sem duplicar o novo", async () => {
 let enviado = false;
 const impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  const path = new URL(url).pathname;
  if (path === "/ai/messages") return resposta(200, { ...(enviado ? janela.after : janela.before), usage: { used: enviado ? 32 : 31, limit: 100 } });
  if (path === "/ai/chat") { enviado = true; return resposta(200, { reply: "Resposta exclusiva enviada", usage: { used: 32, limit: 100 } }); }
  return impl(url, req);
 });
 renderRouter("./app", { initialUrl: "/piggy" }); await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy")).toBeTruthy());
 await act(async () => { fireEvent.changeText(screen.getByLabelText("Mensagem para o Piggy"), "Pergunta exclusiva enviada"); });
 await apertar("Enviar mensagem"); expect(screen.getAllByText("Resposta exclusiva enviada")).toHaveLength(2);
 await atualizar(); expect(screen.getAllByText("Resposta exclusiva enviada")).toHaveLength(1);
 expect(screen.getAllByText("Pergunta exclusiva enviada")).toHaveLength(1);
 expect(screen.getByText("32 de 100 mensagens utilizadas")).toBeTruthy();
});
it("GET falho conserva histórico e eco do POST; próximo snapshot confirmado substitui", async () => {
 const impl = fetchFalso.getMockImplementation()!;
 let falhar = false;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => new URL(url).pathname === "/ai/messages" && falhar ? resposta(500, { detail: "indisponível" }) : impl(url, req));
 renderRouter("./app", { initialUrl: "/piggy" }); await enviar(); falhar = true; await atualizar();
 await waitFor(() => expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeTruthy());
 expect(screen.getAllByText("Resposta exclusiva")).toHaveLength(1); expect(screen.getByText("100 de 100 mensagens utilizadas")).toBeTruthy();
 falhar = false; await apertar("Tentar novamente"); await waitFor(() => expect(screen.queryByRole("button", { name: "Tentar novamente" })).toBeNull());
 expect(screen.getAllByText("Resposta exclusiva")).toHaveLength(1);
});
it("POST iniciado antes de refresh não restaura resposta nem cota quando chega tarde", async () => {
 uso = 0; const atraso = segurar();
 const impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  if (new URL(url).pathname === "/ai/chat") { await atraso.promessa; return resposta(200, { reply: "Resposta tardia descartada", usage: { used: 100, limit: 100 } }); }
  return impl(url, req);
 });
 renderRouter("./app", { initialUrl: "/piggy" }); await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy")).toBeTruthy());
 await act(async () => { fireEvent.changeText(screen.getByLabelText("Mensagem para o Piggy"), "Pergunta exclusiva"); }); await apertar("Enviar mensagem");
 const post = fetchFalso.mock.calls.find(([url]) => new URL(String(url)).pathname === "/ai/chat")!;
 uso = 7; await atualizar();
 expect(post[1].signal.aborted).toBe(true);
 atraso.soltar(); await act(async () => { await drenar(); });
 expect(screen.queryByText("Resposta tardia descartada")).toBeNull(); expect(screen.queryByText("100 de 100 mensagens utilizadas")).toBeNull();
 expect(screen.getByText("7 de 100 mensagens utilizadas")).toBeTruthy(); expect(screen.getByRole("button", { name: "Enviar mensagem" })).toBeEnabled();
});
