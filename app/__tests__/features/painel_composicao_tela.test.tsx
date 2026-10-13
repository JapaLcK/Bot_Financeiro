// O painel herda o tema do provider da raiz; a barra é lida no tema escuro.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({ __esModule: true, default: () => "dark" }));

import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";

beforeEach(async () => {
 prepararCaso(); desligarTrava(); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
});
const H = { includeHiddenElements: true }; // a barra é oculta ao VoiceOver de propósito
it("widget Contas mostra a barra de composição (Carteira + 2 contas BRL do fixture, 450,00 + 6.250,25 + 2.700,00 = total) e ela some com valores ocultos", async () => {
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByText("Disponível agora")).toBeTruthy());
 expect(screen.getAllByTestId("segmento-composicao", H)).toHaveLength(3);
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); });
 expect(screen.queryByTestId("barra-composicao", H)).toBeNull();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Mostrar valores" })); });
 expect(screen.getAllByTestId("segmento-composicao", H)).toHaveLength(3);
});
it("widget Contas não desenha a barra quando o bloco declara saldo incerto", async () => {
 const base = fetchFalso.getMockImplementation()!, c = (fixture as Record<string, any>)["/api/app/contas"];
 fetchFalso.mockImplementation(async (url: string) => new URL(url).pathname === "/api/app/contas" ? resposta(200, { ...c, motivos: ["banco_desatualizado"] }) : base(url));
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByText("Disponível agora")).toBeTruthy());
 expect(screen.queryByTestId("barra-composicao", H)).toBeNull();
});
