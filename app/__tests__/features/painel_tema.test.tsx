// O painel herda o tema do provider da raiz (segue o sistema), não força escuro.
const estadoDoEsquema: { valor: "light" | "dark" } = { valor: "light" };
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({ __esModule: true, default: () => estadoDoEsquema.valor }));

import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { StyleSheet } from "react-native";
import { guardarCredenciais } from "@/storage/secure";
import { contraste } from "@/ui/contraste";
import { claro, escuro } from "@/ui/tokens";
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
it.each([["light", claro], ["dark", escuro]] as const)("/resumo no sistema %s usa o fundo do tema %s", async (esquema, paleta) => {
 estadoDoEsquema.valor = esquema;
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getAllByTestId("tela").length).toBeGreaterThan(0));
 expect(StyleSheet.flatten(screen.getAllByTestId("tela")[0]!.props.style).backgroundColor).toBe(paleta.bg);
});
const corDe = (el: { props: { style?: unknown } }): string => (StyleSheet.flatten(el.props.style as object) as { color: string }).color;
// Texto pequeno (rótulo da aba 11pt, rótulos de filtro/chat) usa `brandInk`: `brand` no claro mede 3,49:1 sobre o fundo, abaixo dos 4,5 de texto.
it.each([["light", claro], ["dark", escuro]] as const)("rótulo da aba ativa no sistema %s é brandInk e passa 4,5:1", async (esquema, paleta) => {
 estadoDoEsquema.valor = esquema;
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByLabelText("Aba Resumo")).toBeTruthy());
 const cor = corDe(within(screen.getByLabelText("Aba Resumo")).getByText("Resumo"));
 expect(cor).toBe(paleta.brandInk);
 expect(contraste(cor, paleta.bg)).toBeGreaterThanOrEqual(4.5);
});
it.each([["light", claro], ["dark", escuro]] as const)("rótulo do filtro de dia no extrato (%s) é brandInk e passa 4,5:1", async (esquema, paleta) => {
 estadoDoEsquema.valor = esquema;
 renderRouter("./app", { initialUrl: "/extrato?dia=2026-10-01" });
 await waitFor(() => expect(screen.getByText("01/10/2026")).toBeTruthy());
 const cor = corDe(screen.getByText("01/10/2026"));
 expect(cor).toBe(paleta.brandInk);
 expect(contraste(cor, paleta.bg)).toBeGreaterThanOrEqual(4.5);
});
it.each([["light", claro], ["dark", escuro]] as const)("rótulo \"Você\" do chat (%s) é brandInk e passa 4,5:1", async (esquema, paleta) => {
 estadoDoEsquema.valor = esquema;
 const impl = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => new URL(url).pathname === "/ai/chat" ? resposta(200, { reply: "Oi", usage: { used: 1, limit: 100 } }) : impl(url, req));
 renderRouter("./app", { initialUrl: "/piggy" });
 await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy")).toBeTruthy());
 await act(async () => { fireEvent.changeText(screen.getByLabelText("Mensagem para o Piggy"), "Olá"); });
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Enviar mensagem" })); });
 await waitFor(() => expect(screen.getByText("Oi")).toBeTruthy());
 const cor = corDe(screen.getByText("Você"));
 expect(cor).toBe(paleta.brandInk);
 expect(contraste(cor, paleta.bg)).toBeGreaterThanOrEqual(4.5);
});
it.each([["light", claro], ["dark", escuro]] as const)("ícone da aba ativa no sistema %s é brandInk, igual ao rótulo", async (esquema, paleta) => {
 estadoDoEsquema.valor = esquema;
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByLabelText("Aba Resumo")).toBeTruthy());
 const cores = within(screen.getByLabelText("Aba Resumo")).UNSAFE_getAllByProps({ weight: "regular" }).map((n) => n.props.color);
 expect(cores).toContain(paleta.brandInk);
});
