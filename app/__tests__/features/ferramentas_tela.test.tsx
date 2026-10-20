import { Alert } from "react-native";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
// Tela Ferramentas (chave no cabeçalho das abas do painel): `renderRouter` + servidor falso, plano variável em /api/app/me.
jest.setTimeout(30_000);
let plano: string | null; // null = /api/app/me responde 500
const posts = () => fetchFalso.mock.calls.filter(([u, o]) => (o as RequestInit)?.method === "POST" && new URL(String(u)).pathname === "/api/app/simulador");
const TITULOS = ["Vale a pena comprar?", "Progresso ao milhão", "Agentes", "Simulador", "Orçamento doméstico", "Orçamentos por categoria"];
const BREVE = TITULOS.slice(3);
beforeEach(async () => {
  prepararCaso(); desligarTrava(); plano = "pro"; await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
  fetchFalso.mockImplementation(async (url: string) => {
    const path = new URL(url).pathname;
    if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
    if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
    if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
    if (path === "/api/app/me") return plano ? resposta(200, { plan_tier: plano }) : resposta(500, undefined);
    if (path === "/auth/refresh") return resposta(401, { detail: "invalid_refresh_token" });
    return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
  });
});
const planoChegou = () => waitFor(() => expect(fetchFalso.mock.calls.some(([u]) => new URL(String(u)).pathname === "/api/app/me")).toBe(true));
async function abrir(aba = "gastos") {
  renderRouter("./app", { initialUrl: `/${aba}` });
  await waitFor(() => expect(screen.getByTestId("painel-ferramentas")).toBeTruthy()); await planoChegou(); await act(drenar);
}
const tocarChave = async () => { await act(async () => { fireEvent.press(screen.getByTestId("painel-ferramentas")); await drenar(); }); await waitFor(() => expect(screen.getByText(TITULOS[0]!)).toBeTruthy()); };
const linha = (t: string) => screen.getByRole("button", { name: new RegExp(t) });

describe("chave do cabeçalho", () => {
  it.each(["resumo", "gastos", "metas", "extrato", "piggy"])("%s: abre a lista com as 6 linhas, sem Painel antigo nem Guia do painel", async (aba) => {
    await abrir(aba); await tocarChave();
    expect(screen).toHavePathname("/ferramentas");
    for (const t of TITULOS) expect(screen.getByText(t)).toBeTruthy();
    expect(screen.queryByText("Painel antigo")).toBeNull(); expect(screen.queryByText("Guia do painel")).toBeNull();
  });
  it("dois toques rápidos abrem uma tela só", async () => {
    await abrir(); const chave = screen.getByTestId("painel-ferramentas");
    await act(async () => { fireEvent.press(chave); fireEvent.press(chave); await drenar(); });
    await waitFor(() => expect(screen.getByText(TITULOS[0]!)).toBeTruthy()); expect(screen.getAllByText(TITULOS[0]!)).toHaveLength(1);
  });
});

describe("Vale a pena comprar?", () => {
  it("positivo: Pro abre a folha do simulador e nenhum POST sai", async () => {
    await abrir(); await tocarChave();
    await act(async () => { fireEvent.press(linha(TITULOS[0]!)); await drenar(); });
    await waitFor(() => expect(screen.getByText("Simular compra")).toBeTruthy()); expect(screen).toHavePathname("/simulador-compra"); expect(posts()).toHaveLength(0);
  });
  it.each(["essencial", "plus"])("%s: selo Pro; o toque avisa, não abre a folha e não faz POST", async (p) => {
    plano = p; const aviso = jest.spyOn(Alert, "alert").mockImplementation(() => undefined);
    try {
      await abrir(); await tocarChave(); expect(screen.getByText("Pro")).toBeTruthy();
      await act(async () => { fireEvent.press(linha(TITULOS[0]!)); await drenar(); });
      expect(aviso).toHaveBeenCalledWith("Simulador indisponível no seu plano."); expect(screen).toHavePathname("/ferramentas"); expect(screen.queryByText("Simular compra")).toBeNull(); expect(posts()).toHaveLength(0);
    } finally { aviso.mockRestore(); }
  });
  it("plano com erro 500: linha desativada, sem selo, o toque não faz nada", async () => {
    plano = null; const aviso = jest.spyOn(Alert, "alert").mockImplementation(() => undefined);
    try {
      await abrir(); await tocarChave(); const l = linha(TITULOS[0]!); expect(l.props.accessibilityState).toMatchObject({ disabled: true }); expect(screen.queryByText("Pro")).toBeNull();
      await act(async () => { fireEvent.press(l); await drenar(); });
      expect(aviso).not.toHaveBeenCalled(); expect(screen).toHavePathname("/ferramentas"); expect(posts()).toHaveLength(0);
    } finally { aviso.mockRestore(); }
  });
});

it("as 3 linhas 'Em breve' não são botões", async () => {
  await abrir(); await tocarChave();
  expect(screen.getAllByText("Em breve")).toHaveLength(3);
  for (const t of BREVE) expect(screen.queryByRole("button", { name: new RegExp(t) })).toBeNull();
});

it.each(["pro", "essencial"])("Progresso ao milhão (plano %s): é botão e abre a tela", async (p) => {
  plano = p; await abrir(); await tocarChave();
  await act(async () => { fireEvent.press(linha(TITULOS[1]!)); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname("/progresso-milhao"));
});

it("Agentes: é botão e abre a tela", async () => {
  await abrir(); await tocarChave();
  await act(async () => { fireEvent.press(linha(TITULOS[2]!)); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname("/agentes"));
});
