// Ataques do Tester à tela Agentes: deep link com kind inválido, param velho, respostas estranhas do servidor, abertura a frio.
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { router } from "expo-router";
import { AGENTES } from "@/features/painel/agentes";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
jest.setTimeout(30_000);
let agentes: { status: number; corpo: object };
const cat = (over: object = {}) => ({ catalog: AGENTES.map((a) => ({ kind: a.kind, nome: a.nome, disponivel: true, status: a.kind === "xerife" ? "active" : null, energy_cost: 2 })), can_chat: true, can_activate: true, energy_enabled: true, energy_budget: 10, energy_used: 2, ...over });
beforeEach(async () => {
  prepararCaso(); desligarTrava(); agentes = { status: 200, corpo: cat() }; await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
  fetchFalso.mockImplementation(async (url: string) => {
    const path = new URL(url).pathname;
    if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
    if (path === "/agents/1") return resposta(agentes.status, agentes.corpo);
    if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
    if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
    if (path === "/api/app/me") return resposta(200, { plan_tier: "pro" });
    if (path === "/auth/refresh") return resposta(401, { detail: "invalid_refresh_token" });
    return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
  });
});
const campo = () => screen.getByLabelText("Mensagem para o Piggy");
const ir = async (url: string) => { await act(async () => { router.navigate(url as never); await drenar(); }); };
const abrirPiggy = async (url: string) => { renderRouter("./app", { initialUrl: url }); await waitFor(() => expect(campo()).toBeTruthy()); await act(drenar); };

it.each(["lixo", "<script>", "__proto__", "constructor", "toString", "xerife%00", ""])("deep link ?agente=%s nunca poe texto fora do catalogo", async (k) => {
  await abrirPiggy(`/piggy?agente=${encodeURIComponent(k)}&entrada=1`);
  expect(campo().props.value).toBe("");
});
it("deep link ?agente=xerife (kind valido, sem toque) pre-preenche so texto do catalogo", async () => {
  await abrirPiggy("/piggy?agente=xerife&entrada=1");
  expect(campo().props.value).toBe("@Xerife Algum gasto fugiu do meu padrão?");
});
it("agente repetido (?agente=a&agente=xerife) nao quebra", async () => {
  await abrirPiggy("/piggy?agente=a&agente=xerife&entrada=1");
  expect(typeof campo().props.value).toBe("string");
});
it("param velho NAO deve reescrever o campo ao voltar para a aba (rascunho do usuario)", async () => {
  await abrirPiggy("/piggy?agente=xerife&entrada=1");
  await waitFor(() => expect(campo().props.value).toContain("@Xerife"));
  await act(async () => { fireEvent.changeText(campo(), "meu rascunho"); await drenar(); });
  await ir("/gastos"); await waitFor(() => expect(screen.queryByLabelText("Mensagem para o Piggy")).toBeNull());
  await ir("/piggy"); await waitFor(() => expect(campo()).toBeTruthy()); await act(drenar);
  expect(campo().props.value).not.toContain("@Xerife");
});
it("energia acima de 30: 5 de 100 usados enchem ~5% da barra (proporção), não 5 de 30 traços", async () => {
  agentes.corpo = cat({ energy_budget: 100, energy_used: 5 });
  renderRouter("./app", { initialUrl: "/agentes" });
  await waitFor(() => expect(screen.getByText("95 livres")).toBeTruthy());
  const segs: string[] = []; const varre = (n: any) => { if (!n || typeof n !== "object") return; if (n.props?.style?.height === 5) segs.push(n.props.style.backgroundColor); (n.children || []).forEach(varre); };
  varre(screen.toJSON());
  const cheios = segs.filter((c) => c === segs[0]).length;
  expect(segs.length).toBe(30); // so 30 segmentos
  expect(cheios / segs.length).toBeLessThan(0.08); // esperado ~5%; real 5/30
});
it("energia: usada > orcamento nao mostra 'livres' negativo nem quebra", async () => {
  agentes.corpo = cat({ energy_budget: 10, energy_used: 14 });
  renderRouter("./app", { initialUrl: "/agentes" });
  await waitFor(() => expect(screen.getByText("0 livres")).toBeTruthy());
});
it("energy_enabled false e catalogo vazio/kind desconhecido nao quebram", async () => {
  agentes.corpo = { catalog: [{ kind: "novo", nome: "Novo", disponivel: true, status: null, energy_cost: 1 }], energy_enabled: false, can_chat: true };
  renderRouter("./app", { initialUrl: "/agentes" });
  await waitFor(() => expect(screen.getByText("De olho nos excessos.")).toBeTruthy());
  expect(screen.queryByText(/livres$/)).toBeNull();
});
it("corpo 200 malformado ({}), sem catalog, nao derruba a tela", async () => {
  agentes.corpo = {};
  renderRouter("./app", { initialUrl: "/agentes" });
  await waitFor(() => expect(screen.getByText("De olho nos excessos.")).toBeTruthy());
});
it("abertura a frio em /agentes (sem painel na pilha): Conversar leva ao chat com o texto; duplo toque nao empilha", async () => {
  renderRouter("./app", { initialUrl: "/agentes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Conversar" })).toBeTruthy());
  const b = screen.getByRole("button", { name: "Conversar" });
  await act(async () => { fireEvent.press(b); fireEvent.press(b); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname("/piggy"));
  await act(drenar);
  expect(campo().props.value).toBe("@Xerife Algum gasto fugiu do meu padrão?");
});
