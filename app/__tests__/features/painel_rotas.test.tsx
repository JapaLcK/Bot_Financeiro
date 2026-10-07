import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { chaveLayout } from "@/features/painel/catalogo";
import { chamadas, cofre, fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar, appVai } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
const credencial = { access: "access-ana", refresh: "rt_ana" };
function servidor(opcoes: { acesso?: boolean; semMarco?: boolean; semCampo?: boolean; perfilLento?: ReturnType<typeof segurar>; contasLentas?: ReturnType<typeof segurar> } = {}) {
 let perfil = "padrao";
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", ...(opcoes.semCampo ? {} : { app_access: opcoes.acesso ?? true }) });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: !opcoes.semMarco, completed_at: opcoes.semMarco ? null : "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === "/auth/logout") return resposta(200, {});
  if (path === "/api/app/perfil") { if (req.method === "PUT") { if (opcoes.perfilLento) await opcoes.perfilLento.promessa; perfil = JSON.parse(String(req.body)).perfil; } return resposta(200, { perfil }); }
  if (path === "/api/app/contas" && opcoes.contasLentas) await opcoes.contasLentas.promessa;
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); await guardarCredenciais(credencial); servidor(); });
afterEach(() => jest.restoreAllMocks());
async function apertar(label: string) { await act(async () => { fireEvent.press(screen.getByRole("button", { name: label })); await drenar(); }); }
it.each(["/resumo", "/gastos", "/piggy", "/metas", "/extrato"] as const)("deep link %s montado só depois de acesso e marco reais", async (url) => {
 renderRouter("./app", { initialUrl: url }); await waitFor(() => expect(screen.getByTestId("painel-conta")).toBeTruthy());
 expect(chamadas().findIndex((c) => c.caminho === "/onboarding/open-finance")).toBeLessThan(chamadas().findIndex((c) => c.caminho.startsWith("/api/app/")));
 const app = fetchFalso.mock.calls.filter(([u]) => String(u).includes("/api/app/")); expect(app.length).toBeGreaterThan(0);
 for (const [,req] of app) { expect(req.credentials).toBe("omit"); expect(req.headers.Authorization).toBe("Bearer access-ana"); }
});
it.each([{ acesso: false }, { semMarco: true }, { semCampo: true }])("deep link extrato recusa gate incompleto %p antes de dados", async (opcoes) => {
 servidor(opcoes); renderRouter("./app", { initialUrl: "/extrato" }); await act(async () => { await drenar(); await drenar(); });
 expect(screen.queryByTestId("painel-conta")).toBeNull(); expect(chamadas().filter((c) => c.caminho.startsWith("/api/app/"))).toHaveLength(0);
});
it("perfil e Organizar fazem PUT JSON e persistem somente o layout do dono", async () => {
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: "Padrão" })).toBeTruthy());
 await apertar("Padrão"); await apertar("Economizar"); await waitFor(() => expect(screen.getByRole("button", { name: "Economizar" })).toBeTruthy());
 expect(chamadas().find((c) => c.caminho === "/api/app/perfil" && c.corpo)).toMatchObject({ corpo: { perfil: "economizar" } });
 await apertar("Organizar"); await apertar("Esconder Contas"); expect(JSON.parse(cofre.get(chaveLayout(1,"economizar"))!)).not.toContain("contas"); expect(cofre.has(chaveLayout(2,"economizar"))).toBe(false);
 await apertar("Restaurar padrão deste perfil"); expect(JSON.parse(cofre.get(chaveLayout(1,"economizar"))!)[0]).toBe("contas");
});
it("PUT lento durante foreground não deixa seletor travado e reconcilia o perfil", async () => {
 const atraso = segurar(); servidor({ perfilLento: atraso }); renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: "Padrão" })).toBeTruthy());
 await apertar("Padrão"); await apertar("Investir"); await appVai("background"); atraso.soltar(); await appVai("active");
 await waitFor(() => expect(screen.getByRole("button", { name: /Padrão|Investir/ })).toBeEnabled());
});
it("privacidade remove montantes, gráfico e mensagem financeira da árvore acessível", async () => {
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("Saldo disponível agora")).toBeTruthy());
 await apertar("Ocultar valores"); expect(screen.queryByLabelText(/9[.]?400/)).toBeNull(); expect(screen.queryByLabelText("Evolução dos valores no período")).toBeNull();
 await act(async () => { router.navigate("/piggy"); await drenar(); }); await waitFor(() => expect(screen.getByText("Mensagem oculta enquanto os valores estão privados.")).toBeTruthy()); expect(screen.queryByText(/Oi! Posso ajudar/)).toBeNull();
});
it("contas A atrasadas após logout não são mostradas e não restauram sessão", async () => {
 const atraso = segurar(); servidor({ contasLentas: atraso }); renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByTestId("painel-conta")).toBeTruthy());
 await apertar("Abrir minha conta"); await apertar("Sair"); atraso.soltar(); await act(async () => { await drenar(); }); expect(screen.queryByText("Saldo disponível agora")).toBeNull(); expect(cofre.has("pb.credenciais")).toBe(false);
});
