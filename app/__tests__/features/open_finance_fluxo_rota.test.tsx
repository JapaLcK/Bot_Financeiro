import { StyleSheet } from "react-native";
import { claro } from "@/ui/tokens";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { chamadas, prepararCaso, resposta, rotear, S } from "./auth_apoio";
import { desligarTrava, drenar, appVai } from "./open_finance_volta_rota_apoio";

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
it("conclusão servidor mantém Início e avatar abre Configurações", async () => {
  servidor(p, true); renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Bancos conectados" })).toBeTruthy());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Abrir minha conta" })); await drenar(); });
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Configurações" })); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname("/configuracoes"));
  expect(screen.queryByRole("button", { name: "Teste Open Finance" })).toBeNull();
  // Lista agrupada: sem o subtítulo antigo, e "Sair" vermelho (danger) dentro do próprio grupo.
  expect(screen.queryByText("Sua conta, do seu jeito")).toBeNull();
  expect(StyleSheet.flatten(screen.getByText("Sair").props.style).color).toBe(claro.danger);
});
it("falha transitória na rechecagem do fundo curto, com a MESMA credencial: a tela fica e a revalidação é silenciosa", async () => {
  servidor(p, true); renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Bancos conectados" })).toBeTruthy());
  rotear({ "/auth/me": () => resposta(503, {}) });
  const antes = chamadas().filter((c) => c.caminho === "/auth/me").length;
  await appVai("background"); await appVai("active");
  await waitFor(() => expect(chamadas().filter((c) => c.caminho === "/auth/me").length).toBeGreaterThan(antes));
  await act(drenar);
  expect(screen.getByTestId("painel-conta")).toBeTruthy(); expect(screen.queryByRole("button", { name: "Conferir acesso" })).toBeNull(); expect(screen.queryByText("Conferindo seu acesso…")).toBeNull();
});
it("falha transitória na rechecagem do fundo curto, com credencial de OUTRA sessão: portão com erro, sem dados", async () => {
  servidor(p, true); renderRouter("./app", { initialUrl: "/" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Bancos conectados" })).toBeTruthy());
  rotear({ "/auth/me": () => resposta(503, {}) });
  await appVai("background"); await guardarCredenciais({ access: "access-bela", refresh: "rt_bela" }); await appVai("active");
  await waitFor(() => expect(screen.getByRole("button", { name: "Conferir acesso" })).toBeTruthy());
  expect(screen.queryByTestId("painel-conta")).toBeNull();
});
