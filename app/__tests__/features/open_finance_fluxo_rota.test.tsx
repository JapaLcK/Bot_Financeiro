import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { chamadas, prepararCaso, resposta, rotear, S } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

beforeEach(async () => { prepararCaso(); desligarTrava(); await guardarCredenciais(S); });
const p = { user_id: 1, display_name: "Ana", app_access: true, of_banks_max: 2 };
function servidor(perfil = p, completed = false, teto = 2) {
  rotear({ "/auth/me": () => resposta(200, perfil), "/onboarding/open-finance": () => resposta(200, { ok: true, completed, completed_at: completed ? "2026-10-05T12:00:00Z" : null }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: teto, em_uso: 0, pode_adicionar: teto > 0, code: null, message: null }) });
}
it("Início sem primeira sync mostra conexão obrigatória e preserva Segurança", async () => {
  servidor(); renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Conectar meu banco" })).toBeTruthy());
  expect(screen.getByRole("button", { name: "Segurança" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Bancos conectados" })).toBeNull();
});
it("Free mostra falta de acesso sem consultar nem oferecer conexão", async () => {
  servidor({ ...p, app_access: false }); renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByText(/Sua conta ainda não tem acesso ao app/)).toBeTruthy());
  expect(screen.queryByRole("button", { name: "Conectar meu banco" })).toBeNull();
  expect(chamadas().some((c) => c.caminho === "/onboarding/open-finance")).toBe(false);
});
it("conclusão servidor mantém Início e avatar abre Configurações/Teste OF", async () => {
  servidor(p, true); renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Bancos conectados" })).toBeTruthy());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Configurações da conta" })); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname("/configuracoes"));
  expect(screen.getByRole("button", { name: "Teste Open Finance" })).toBeTruthy();
});
it("falha transitória na rechecagem mantém Home provisória e mostra aviso", async () => {
  servidor(p, true); renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Bancos conectados" })).toBeTruthy());
  rotear({ "/auth/me": () => resposta(503, {}) });
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Conferir acesso novamente" })); await drenar(); });
  await waitFor(() => expect(screen.getByText("Tivemos um problema aqui. Tente de novo em instantes.")).toBeTruthy());
  expect(screen.getByRole("button", { name: "Bancos conectados" })).toBeTruthy();
});
