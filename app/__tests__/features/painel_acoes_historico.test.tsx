import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { mesAtual, mesesAnteriores, mesesDoHistorico, nomeMes } from "@/features/painel/catalogo";
import { perfilSchema } from "@/api/schemas/auth";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar, appVai } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
let corte: string | null | undefined;
function servidor() {
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true, ...(corte !== undefined ? { history_earliest_date: corte } : {}) });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); corte = undefined; await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor(); });
afterEach(() => jest.restoreAllMocks());
async function apertar(nome: string) { await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); }
it("cancelar marca de assinatura por refresh libera botões e permite retry real", async () => {
 const impl = fetchFalso.getMockImplementation()!; let marcas = 0;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  if (new URL(url).pathname === "/api/app/assinaturas/marca") {
   if (++marcas === 1) await new Promise<void>((_, rejeitar) => { req.signal!.addEventListener("abort", () => rejeitar(new Error("abortada")), { once: true }); });
   return resposta(200, fixture["/api/app/assinaturas"]);
  }
  return impl(url, req);
 });
 renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getByRole("button", { name: "Ignorar Netflix" })).toBeTruthy()); await apertar("Ignorar Netflix");
 expect(screen.getByRole("button", { name: "Salvando…" })).toBeDisabled();
 const primeira = fetchFalso.mock.calls.find(([url]) => new URL(String(url)).pathname === "/api/app/assinaturas/marca")!;
 await act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); await drenar(); });
 expect(primeira[1].signal.aborted).toBe(true); await waitFor(() => expect(screen.getByRole("button", { name: "Ignorar Netflix" })).toBeEnabled());
 await apertar("Ignorar Netflix"); await waitFor(() => expect(screen.getByRole("button", { name: "Ignorar Netflix" })).toBeEnabled());
 const posts = fetchFalso.mock.calls.filter(([url]) => new URL(String(url)).pathname === "/api/app/assinaturas/marca");
 expect(posts).toHaveLength(2); expect(JSON.parse(posts[1]![1].body)).toEqual({ chave: "netflix", status: "ignorar" });
 expect(screen.queryByText("Não conseguimos carregar agora. Tente novamente.")).toBeNull();
});
it("Dia a dia: uma linha por dia, mais recente primeiro, 7 visíveis, com privacidade e drilldown", async () => {
 const impl = fetchFalso.getMockImplementation()!;
 const dias = [{ dia: "2025-10-03", entrou: "0.00", saiu: "5.00" }, { dia: "2026-09-01", entrou: "10.00", saiu: "4.00" }, ...[2, 3, 4, 5, 6, 7, 8].map((n) => ({ dia: `2026-09-0${n}`, entrou: "0.00", saiu: "1.00" })), { dia: "2026-10-01", entrou: "7200.00", saiu: "0.00" }];
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => new URL(url).pathname === "/api/app/mes-detalhes" ? resposta(200, { ...fixture["/api/app/mes-detalhes"], dias }) : impl(url, req));
 const r = renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getByTestId("calendario-dia-2026-10-01")).toBeTruthy());
 const ordem = () => screen.getAllByTestId(/^calendario-dia-/).map((n) => String(n.props.testID).slice("calendario-dia-".length));
 expect(ordem()).toEqual(["2026-10-01", "2026-09-08", "2026-09-07", "2026-09-06", "2026-09-05", "2026-09-04", "2026-09-03"]);
 const salario = within(screen.getByTestId("calendario-dia-2026-10-01"));
 expect(salario.getByText("01/10")).toBeTruthy(); expect(salario.getByLabelText("mais 7200 reais")).toBeTruthy();
 // Lado zerado não aparece ("R$ 0,00" era ruído); só saída fica só com o "menos".
 expect(salario.queryByLabelText("zero reais")).toBeNull(); expect(salario.queryByLabelText(/^menos/)).toBeNull();
 const soSaida = within(screen.getByTestId("calendario-dia-2026-09-08")); expect(soSaida.getByLabelText("menos 1 real")).toBeTruthy(); expect(soSaida.queryByLabelText(/^mais|zero reais/)).toBeNull();
 expect(salario.queryByText("Entrou")).toBeNull(); expect(salario.queryByText("Saiu")).toBeNull();
 await apertar("Mostrar todos os dias (10)"); expect(ordem().slice(7)).toEqual(["2026-09-02", "2026-09-01", "2025-10-03"]);
 // Dia de outro ano leva o ano: a compra parcelada conserva o dia original e "03/10" seria ambíguo.
 expect(within(screen.getByTestId("calendario-dia-2025-10-03")).getByText("03/10/25")).toBeTruthy();
 const antigo = within(screen.getByTestId("calendario-dia-2026-09-01")); expect(antigo.getByText("01/09")).toBeTruthy(); expect(antigo.getByLabelText("mais 10 reais")).toBeTruthy(); expect(antigo.getByLabelText("menos 4 reais")).toBeTruthy();
 await apertar("Ocultar valores"); expect(salario.queryByLabelText("mais 7200 reais")).toBeNull(); expect(antigo.queryByLabelText("menos 4 reais")).toBeNull();
 await apertar("Mostrar valores"); await apertar("Mostrar menos"); expect(ordem()).toHaveLength(7);
 await apertar("Mostrar todos os dias (10)"); await act(async () => { fireEvent.press(screen.getByTestId("calendario-dia-2026-09-01")); await drenar(); });
 expect(screen).toHavePathname("/extrato"); expect(r.getSearchParams().dia).toBe("2026-09-01");
});
it("Patrimônio: histórico mostra os 7 dias mais recentes e abre o resto pelo mesmo botão do Dia a dia", async () => {
 const impl = fetchFalso.getMockImplementation()!, pat = fixture["/api/app/patrimonio"];
 const historico = [1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => ({ ...pat.historico[0]!, dia: `2026-09-0${n}`, total: `${n}00.00` }));
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => new URL(url).pathname === "/api/app/patrimonio" ? resposta(200, { ...pat, historico }) : impl(url, req));
 renderRouter("./app", { initialUrl: "/metas" }); await waitFor(() => expect(screen.getByText("09/09/2026")).toBeTruthy());
 expect(screen.getByText("03/09/2026")).toBeTruthy(); expect(screen.queryByText("02/09/2026")).toBeNull(); expect(screen.queryByText("01/09/2026")).toBeNull();
 await apertar("Mostrar todos os dias (9)"); expect(screen.getByText("01/09/2026")).toBeTruthy();
 await apertar("Mostrar menos"); expect(screen.queryByText("01/09/2026")).toBeNull();
});
it.each([13, 25])("corte do servidor inclui o mês parcial mais antigo de %s meses e exclui o anterior", async (quantidade) => {
 const meses = mesesAnteriores(mesAtual(), quantidade + 1), antigo = meses[quantidade - 1]!; corte = `${antigo}-07`;
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: nomeMes(mesAtual()).slice(0, 3) })).toBeTruthy()); await apertar(nomeMes(mesAtual()).slice(0, 3));
 expect(screen.getByRole("button", { name: nomeMes(antigo) })).toBeTruthy(); expect(screen.queryByRole("button", { name: nomeMes(meses[quantidade]!) })).toBeNull();
 expect(screen.getByText(`Histórico desde 07/${antigo.slice(5)}/${antigo.slice(0, 4)}`)).toBeTruthy(); expect(screen.queryByTestId("painel-historico-expandir")).toBeNull();
 await apertar(nomeMes(antigo)); expect(fetchFalso.mock.calls.some(([url]) => new URL(String(url)).pathname === "/api/app/resumo-do-mes" && new URL(String(url)).searchParams.get("mes") === antigo)).toBe(true);
});
it.each([null, undefined])("histórico sem cutoff %s não mostra corte e permite ampliar além de 12 meses", async (valor) => {
 corte = valor; renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: nomeMes(mesAtual()).slice(0, 3) })).toBeTruthy()); await apertar(nomeMes(mesAtual()).slice(0, 3));
 expect(screen.queryByTestId("painel-historico-escopo")).toBeNull(); expect(screen.queryByRole("button", { name: nomeMes(mesesAnteriores(mesAtual(), 13)[12]!) })).toBeNull();
 await apertar("Mostrar meses anteriores"); await apertar("Mostrar meses anteriores"); const antigo = mesesAnteriores(mesAtual(), 25)[24]!;
 expect(screen.getByRole("button", { name: nomeMes(antigo) })).toBeTruthy(); expect(screen.getByRole("button", { name: "Mostrar meses anteriores" })).toBeEnabled(); await apertar(nomeMes(antigo));
 expect(fetchFalso.mock.calls.some(([url]) => new URL(String(url)).pathname === "/api/app/resumo-do-mes" && new URL(String(url)).searchParams.get("mes") === antigo)).toBe(true);
});
it("schema conserva cutoff ISO/null e rejeita data impossível sem transformar ausência em plano", () => {
 expect(perfilSchema.parse({ user_id: 1, history_earliest_date: "2024-10-07" })).toHaveProperty("history_earliest_date", "2024-10-07");
 expect(perfilSchema.parse({ user_id: 1, history_earliest_date: null })).toHaveProperty("history_earliest_date", null);
 expect(perfilSchema.parse({ user_id: 1 })).not.toHaveProperty("history_earliest_date"); expect(perfilSchema.safeParse({ user_id: 1, history_earliest_date: "2026-02-31" }).success).toBe(false);
});

it("expansão sem corte permanece utilizável para meses de 2016 sem teto artificial", () => {
 const meses = mesesDoHistorico("2026-10", null, 132); expect(meses).toHaveLength(132); expect(meses).toContain("2016-01");
 expect(mesesDoHistorico("2026-10", undefined, 144)).toContain("2015-01");
});

it("revalidação com corte mais curto ajusta o mês antes de qualquer leitura mensal", async () => {
 const meses = mesesAnteriores(mesAtual(), 25), antigo = meses[24]!, novo = meses[12]!; corte = `${antigo}-07`;
 const impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  const u = new URL(url), m = u.searchParams.get("mes");
  if (m && corte && m < corte.slice(0, 7)) return resposta(403, { error: { code: "feature_unavailable", message: "Mês fora do histórico permitido" } });
  return impl(url, req);
 });
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: nomeMes(mesAtual()).slice(0, 3) })).toBeTruthy());
 await apertar(nomeMes(mesAtual()).slice(0, 3)); await apertar(nomeMes(antigo)); const antes = fetchFalso.mock.calls.length; corte = `${novo}-07`;
 await appVai("background"); await appVai("active"); await waitFor(() => expect(screen.getByRole("button", { name: nomeMes(novo).slice(0, 3) })).toBeTruthy()); await act(drenar);
 const mensais = fetchFalso.mock.calls.slice(antes).map(([url]) => new URL(String(url))).filter((u) => u.pathname === "/api/app/resumo-do-mes" || u.pathname === "/api/app/mes-detalhes");
 expect(mensais).toHaveLength(2); expect(mensais.every((u) => u.searchParams.get("mes") === novo)).toBe(true);
 await apertar(nomeMes(novo).slice(0, 3)); expect(screen.getByRole("button", { name: nomeMes(novo), selected: true })).toBeTruthy(); expect(screen.queryByRole("button", { name: nomeMes(antigo) })).toBeNull();
});
it("nova UID começa no mês atual e não herda seleção inacessível da conta anterior", async () => {
 const meses = mesesAnteriores(mesAtual(), 25), antigo = meses[24]!; corte = `${antigo}-07`; const impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  const u = new URL(url), nova = req.headers && (req.headers as Record<string, string>).Authorization === "Bearer access-bela";
  if (u.pathname === "/auth/me" && nova) return resposta(200, { user_id: 2, display_name: "Bela", app_access: true, history_earliest_date: `${meses[12]}-07` });
  if (u.pathname === "/open-finance/2/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (/^\/(goals|cards|installments|analytics|insights)\/2\//.test(u.pathname)) return resposta(200, (fixture as Record<string, unknown>)[u.pathname.replace("/2/", "/1/")]);
  return impl(url, req);
 });
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: nomeMes(mesAtual()).slice(0, 3) })).toBeTruthy()); await apertar(nomeMes(mesAtual()).slice(0, 3)); await apertar(nomeMes(antigo));
 await appVai("background"); await guardarCredenciais({ access: "access-bela", refresh: "rt_bela" }); const antes = fetchFalso.mock.calls.length; await appVai("active"); await waitFor(() => expect(screen.getByText("Bom dia, Bela")).toBeTruthy()); await act(drenar);
 const mensais = fetchFalso.mock.calls.slice(antes).filter(([url]) => ["/api/app/resumo-do-mes", "/api/app/mes-detalhes"].includes(new URL(String(url)).pathname));
 expect(mensais).toHaveLength(2); for (const [url, req] of mensais) { expect(new URL(String(url)).searchParams.get("mes")).toBe(mesAtual()); expect(req.headers.Authorization).toBe("Bearer access-bela"); }
});
