import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { mesAtual, mesesAnteriores, nomeMes } from "@/features/painel/catalogo";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
const escopo = "Busca no histórico permitido pelo seu plano. O mês selecionado não limita os resultados.";
function servidor() {
 fetchFalso.mockImplementation(async (url: string) => {
  const u = new URL(url), path = u.pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === "/api/app/previsao") return resposta(200, { ...fixture[path], dias: Number(u.searchParams.get("dias") || 30) });
  if (path === "/api/app/lancamentos" && u.searchParams.has("q")) { const segunda = u.searchParams.has("cursor"); return resposta(200, { ...fixture[path], itens: [{ ...fixture[path].itens[0], id: segunda ? "busca-outra" : "busca-antiga", data: segunda ? "2026-08-01" : "2026-09-01", descricao: segunda ? "Loja de agosto" : "Loja de setembro" }], proximo: segunda ? null : "pagina-2" }); }
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor(); });
afterEach(() => jest.restoreAllMocks());
async function apertar(nome: string) { await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); }
async function escolherMes(mes: string, anterior = mesAtual()) { await apertar(nomeMes(anterior).slice(0, 3)); await apertar(nomeMes(mes)); }
function caminhos(desde: number) { return fetchFalso.mock.calls.slice(desde).map(([url]) => new URL(String(url)).pathname).sort(); }
it("busca global explicita o histórico permitido sem esconder registros fora do mês", async () => {
 renderRouter("./app", { initialUrl: "/extrato" }); await waitFor(() => expect(screen.getByLabelText("Buscar lançamento")).toBeTruthy());
 await act(async () => { fireEvent.changeText(screen.getByLabelText("Buscar lançamento"), "Loja"); });
 await waitFor(() => expect(screen.getByText("Loja de setembro")).toBeTruthy());
 expect(screen.getByText(escopo)).toBeTruthy(); expect(screen.getByText("01/09/2026 · banco")).toBeTruthy();
 expect(screen.getByText("Existem mais páginas desta busca no histórico permitido pelo seu plano.")).toBeTruthy();
 await apertar("Carregar mais lançamentos"); expect(screen.getByText("Loja de setembro")).toBeTruthy(); expect(screen.getByText("Loja de agosto")).toBeTruthy();
 expect(screen.getByText("Todos os resultados deste filtro foram carregados.")).toBeTruthy();
 const paginas = fetchFalso.mock.calls.map(([url]) => new URL(String(url))).filter((u) => u.pathname === "/api/app/lancamentos" && u.searchParams.has("q"));
 expect(paginas.map((u) => u.searchParams.get("cursor"))).toEqual([null, "pagina-2"]);
 await act(async () => { fireEvent.changeText(screen.getByLabelText("Buscar lançamento"), ""); });
 await waitFor(() => expect(screen.queryByText(escopo)).toBeNull());
});
it("seletores recarregam somente mês ou horizonte e preservam os recursos independentes", async () => {
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: "60 dias" })).toBeTruthy()); await act(drenar);
 const antesMes = fetchFalso.mock.calls.length;
 await escolherMes(mesesAnteriores(mesAtual())[1]!);
 expect(caminhos(antesMes)).toEqual(["/api/app/mes-detalhes", "/api/app/resumo-do-mes"]);
 const antesDias = fetchFalso.mock.calls.length;
 await apertar("60 dias");
 expect(caminhos(antesDias)).toEqual(["/api/app/previsao"]);
 expect(screen.getByRole("button", { name: "60 dias · selecionado" })).toBeTruthy();
});

it("mês não consome nova cota de insights nem substitui o saldo independente", async () => {
 const impl = fetchFalso.getMockImplementation()!; let insights = 0;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  if (new URL(url).pathname === "/insights/1/current" && ++insights > 1) return resposta(429, { detail: "limite" });
  return impl(url, req);
 });
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByLabelText("9400 reais e 25 centavos")).toBeTruthy()); await act(drenar);
 const saldo = screen.getByLabelText("9400 reais e 25 centavos");
 await escolherMes(mesesAnteriores(mesAtual())[1]!);
 expect(screen.getByLabelText("9400 reais e 25 centavos")).toBe(saldo); expect(insights).toBe(1);
 expect(screen.queryByText("Seu limite foi alcançado. Tente novamente mais tarde.")).toBeNull();
});
it("leitura independente em voo continua válida após seleção de mês", async () => {
 const atraso = segurar(), impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => { if (new URL(url).pathname === "/api/app/contas") await atraso.promessa; return impl(url, req); });
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: "60 dias" })).toBeTruthy());
 const chamada = fetchFalso.mock.calls.find(([url]) => new URL(String(url)).pathname === "/api/app/contas")!;
 await escolherMes(mesesAnteriores(mesAtual())[1]!); expect(chamada[1].signal.aborted).toBe(false);
 atraso.soltar(); await act(drenar); expect(screen.getByText("Saldo disponível agora")).toBeTruthy();
 expect(fetchFalso.mock.calls.filter(([url]) => new URL(String(url)).pathname === "/api/app/contas")).toHaveLength(1);
});
it("resumo e detalhes tardios do mês anterior não substituem o mês mais recente", async () => {
 const atraso = segurar(), impl = fetchFalso.getMockImplementation()!, meses = mesesAnteriores(mesAtual());
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  const u = new URL(url), m = u.searchParams.get("mes");
  if ((u.pathname === "/api/app/resumo-do-mes" || u.pathname === "/api/app/mes-detalhes") && m && m !== meses[0]) {
   if (m === meses[1]) await atraso.promessa;
   const valor = m === meses[1] ? "111.00" : "222.00";
   if (u.pathname === "/api/app/resumo-do-mes") return resposta(200, { ...fixture[u.pathname], mes: m, entrou: valor });
   return resposta(200, { ...fixture["/api/app/mes-detalhes"], mes: m, guardado: { ...fixture["/api/app/mes-detalhes"].guardado, liquido: valor } });
  }
  return impl(url, req);
 });
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: "60 dias" })).toBeTruthy());
 await escolherMes(meses[1]!); const antigas = fetchFalso.mock.calls.filter(([url]) => new URL(String(url)).searchParams.get("mes") === meses[1]);
 await escolherMes(meses[2]!, meses[1]!); expect(antigas).toHaveLength(2); for (const [, req] of antigas) expect(req.signal.aborted).toBe(true);
 expect(screen.getAllByLabelText(/^(mais )?222 reais$/)).toHaveLength(2);
 atraso.soltar(); await act(drenar); expect(screen.queryByLabelText(/^(mais )?111 reais$/)).toBeNull(); expect(screen.getAllByLabelText(/^(mais )?222 reais$/)).toHaveLength(2);
});
it("POST do Piggy em voo não é cancelado pela seleção de mês e conserva sua cota", async () => {
 const atraso = segurar(), impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => { if (new URL(url).pathname === "/ai/chat") { await atraso.promessa; return resposta(200, { reply: "Resposta preservada no mês", usage: { used: 100, limit: 100 } }); } return impl(url, req); });
 renderRouter("./app", { initialUrl: "/piggy" }); await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy")).toBeTruthy());
 await act(async () => { fireEvent.changeText(screen.getByLabelText("Mensagem para o Piggy"), "Pergunta em voo"); }); await apertar("Enviar mensagem");
 const post = fetchFalso.mock.calls.find(([url]) => new URL(String(url)).pathname === "/ai/chat")!;
 await escolherMes(mesesAnteriores(mesAtual())[1]!); expect(post[1].signal.aborted).toBe(false);
 expect(screen.getByLabelText("Mensagem para o Piggy").props.value).toBe("Pergunta em voo");
 atraso.soltar(); await act(drenar); expect(screen.getByText("Resposta preservada no mês")).toBeTruthy(); expect(screen.getByText("100 de 100 mensagens utilizadas")).toBeTruthy();
 expect(fetchFalso.mock.calls.filter(([url]) => new URL(String(url)).pathname === "/ai/messages")).toHaveLength(1);
});
it("PUT de perfil atravessa mês e horizonte sem cancelamento, rollback ou GET adicional", async () => {
 const atraso = segurar(), impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => { if (new URL(url).pathname === "/api/app/perfil" && req.method === "PUT") { await atraso.promessa; return resposta(200, { perfil: "autonomo" }); } return impl(url, req); });
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: "Padrão" })).toBeTruthy());
 await apertar("Padrão"); await apertar("Autônomo"); const put = fetchFalso.mock.calls.find(([, req]) => req.method === "PUT")!;
 await escolherMes(mesesAnteriores(mesAtual())[1]!); await apertar("60 dias"); expect(put[1].signal.aborted).toBe(false);
 expect(screen.getByRole("button", { name: "Autônomo" })).toBeDisabled();
 atraso.soltar(); await act(drenar); expect(screen.getByRole("button", { name: "Autônomo" })).toBeEnabled();
 expect(fetchFalso.mock.calls.filter(([url, req]) => new URL(String(url)).pathname === "/api/app/perfil" && req.method !== "PUT")).toHaveLength(1);
 await apertar("Autônomo"); expect(screen.getByRole("button", { name: "Autônomo · selecionado" })).toBeEnabled();
});
it("refresh completo supera previsão em voo e revalida todos os recursos", async () => {
 const atraso = segurar(), impl = fetchFalso.getMockImplementation()!; let chamadas60 = 0;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  const u = new URL(url);
  if (u.pathname === "/api/app/previsao" && u.searchParams.get("dias") === "60" && ++chamadas60 === 1) { await atraso.promessa; return resposta(200, { ...fixture["/api/app/previsao"], dias: 30 }); }
  return impl(url, req);
 });
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: "60 dias" })).toBeTruthy()); await apertar("60 dias");
 const antiga = fetchFalso.mock.calls.find(([url]) => new URL(String(url)).searchParams.get("dias") === "60")!;
 const antes = fetchFalso.mock.calls.length;
 await act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); await drenar(); });
 expect(caminhos(antes)).toHaveLength(15); expect(caminhos(antes)).toContain("/ai/messages"); expect(caminhos(antes)).toContain("/insights/1/current"); expect(antiga[1].signal.aborted).toBe(true);
 expect(screen.getByRole("button", { name: "60 dias · selecionado" })).toBeTruthy(); atraso.soltar(); await act(drenar);
 expect(screen.getByRole("button", { name: "60 dias · selecionado" })).toBeTruthy(); expect(screen.queryByRole("button", { name: "30 dias · selecionado" })).toBeNull();
});
it("busca com categoria vazia e dia civil mantém filtros e paginação global", async () => {
 renderRouter("./app", { initialUrl: "/extrato?categoria=&dia=2026-08-01" }); await waitFor(() => expect(screen.getByLabelText("Buscar lançamento")).toBeTruthy());
 await act(async () => { fireEvent.changeText(screen.getByLabelText("Buscar lançamento"), "Loja"); }); await waitFor(() => expect(screen.getByText(escopo)).toBeTruthy()); await act(drenar);
 expect(screen.queryByText("Loja de setembro")).toBeNull(); expect(screen.getByText("Sem categoria")).toBeTruthy(); expect(screen.getByText("01/08/2026")).toBeTruthy();
 await apertar("Carregar mais lançamentos"); expect(screen.getByText("Loja de agosto")).toBeTruthy();
 const paginas = fetchFalso.mock.calls.map(([url]) => new URL(String(url))).filter((u) => u.pathname === "/api/app/lancamentos" && u.searchParams.has("q"));
 expect(paginas).toHaveLength(2); for (const u of paginas) { expect(u.searchParams.get("categoria")).toBe(""); expect(u.searchParams.get("q")).toBe("Loja"); }
});

it("horizonte não repete leituras mensais, conversa ou insights", async () => {
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: "60 dias" })).toBeTruthy()); await act(drenar);
 const antes = fetchFalso.mock.calls.length, saldoMes = screen.getByLabelText("mais 7200 reais");
 await apertar("60 dias"); expect(caminhos(antes)).toEqual(["/api/app/previsao"]); expect(screen.getByLabelText("mais 7200 reais")).toBe(saldoMes);
 await apertar("90 dias"); expect(caminhos(antes)).toEqual(["/api/app/previsao", "/api/app/previsao"]);
 expect(screen.getByRole("button", { name: "90 dias · selecionado" })).toBeTruthy();
});
