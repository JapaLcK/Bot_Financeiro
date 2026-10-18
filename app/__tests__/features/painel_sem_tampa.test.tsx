import { act, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
// Sem tampa nativa (Android, Expo Go) o fundo desmonta o painel: é a tela que cobre o app switcher. Com tampa (iOS) o saldo fica no fundo: é o F1 de painel_refoco.
jest.mock("@/features/bloqueio/tampa", () => ({ temTampa: false, descobrir: jest.fn(), pular: jest.fn(() => Promise.resolve()) }));
jest.setTimeout(20_000);
const saldo = () => screen.queryAllByLabelText(/9[.]?400/).length > 0;
beforeEach(async () => {
  prepararCaso(); desligarTrava(); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
  fetchFalso.mockImplementation(async (url: string) => {
    const path = new URL(url).pathname;
    if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
    if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
    if (/^\/open-finance\/\d\/limite$/.test(path)) return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
    return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
  });
});
afterEach(() => jest.restoreAllMocks());
it("S1: sem tampa, o saldo sai da árvore no fundo e volta sem o portão", async () => {
  renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(saldo()).toBe(true));
  await appVai("background"); expect(saldo()).toBe(false); expect(screen.queryByTestId("painel-conta")).toBeNull();
  await appVai("active"); for (let i = 0; i < 10; i++) await act(drenar);
  await waitFor(() => expect(saldo()).toBe(true)); expect(screen.queryByText("Conferindo seu acesso…")).toBeNull();
});
