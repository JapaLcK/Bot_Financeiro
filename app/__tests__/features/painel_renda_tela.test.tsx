// O painel herda o tema do provider da raiz; o card é lido no tema escuro.
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({ __esModule: true, default: () => "dark" }));
// "Hoje" fixo: o fixture de renda vai de 2026-05 a 2026-10 e o CI roda em qualquer data. `mesAtual` precisa ser trocado junto (usa o `hojeSP` interno, não o do mock).
let mockHoje = "2026-10-15";
jest.mock("@/features/painel/catalogo", () => ({ ...jest.requireActual("@/features/painel/catalogo"), hojeSP: () => mockHoje, mesAtual: () => mockHoje.slice(0, 7) }));

import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { StyleSheet } from "react-native";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";

jest.setTimeout(20_000); // monta o painel inteiro pelo router: no CI carregado o 1º teste passa dos 5 s padrão

const F = fixture as Record<string, any>, EVO = "/analytics/1/evolution";
const H = { includeHiddenElements: true }; // o gráfico é oculto ao VoiceOver de propósito
let evolucao: any;
beforeEach(async () => {
 mockHoje = "2026-10-15"; evolucao = F[EVO];
 prepararCaso(); desligarTrava(); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === "/api/app/perfil") return resposta(200, { perfil: "autonomo" }); // o preset do Autônomo tem o card Renda mês a mês
  if (path === EVO) return resposta(200, evolucao);
  return resposta(200, F[path] ?? {});
 });
});
async function abrir() {
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByText("Renda mês a mês")).toBeTruthy());
 await waitFor(() => expect(screen.getByText("maio de 2026")).toBeTruthy()); // a lista chegou
}
const alturas = () => screen.getAllByTestId("barra", H).map((b) => (StyleSheet.flatten(b.props.style) as { height: number }).height);

it("card Renda: barras dos 6 meses do fixture (mais antigo à esquerda), mês atual com valor compacto e '*', ACIMA da lista", async () => {
 await abrir();
 expect(alturas()).toEqual([103, 115, 107, 117, 113, 120]); // 6200, 6900, 6400, 7000, 6800, 7200 na escala do máximo (7200)
 expect(screen.getAllByTestId("barra-mes", H).map((t) => t.props.children)).toEqual(["mai", "jun", "jul", "ago", "set", "out*"]);
 expect(screen.getAllByTestId("barra-valor", H).map((t) => t.props.children)).toEqual(["R$ 7,2k"]);
 expect(screen.getByText("* mês em andamento", H)).toBeTruthy();
 const hosts = screen.UNSAFE_root.findAll((n) => typeof n.type === "string"); // pré-ordem = ordem de tela
 const ordem = (el: unknown) => hosts.indexOf(el as never);
 expect(ordem(screen.getByTestId("barras-mensais", H))).toBeGreaterThan(-1);
 expect(ordem(screen.getByTestId("barras-mensais", H))).toBeLessThan(ordem(screen.getByText("maio de 2026"))); // gráfico antes da primeira linha da lista
});
it("a lista continua igual: 6 meses, cada um com a renda e a linha 'Saídas' (versão acessível do gráfico)", async () => {
 await abrir();
 for (const m of ["maio", "junho", "julho", "agosto", "setembro", "outubro"]) expect(screen.getByText(`${m} de 2026`)).toBeTruthy();
 expect(screen.getAllByText("Saídas")).toHaveLength(6);
 expect(screen.getByLabelText("7200 reais")).toBeTruthy(); // renda de outubro
 expect(screen.getByLabelText("4500 reais")).toBeTruthy(); // saídas de maio
});
it("outro mês corrente: a marcação acompanha o mês de hoje (maio vira o atual e o último do gráfico)", async () => {
 mockHoje = "2026-05-20";
 await abrir();
 expect(screen.getAllByTestId("barra-mes", H).map((t) => t.props.children)).toEqual(["mai*"]); // meses depois do atual ficam fora do gráfico (a lista mantém os 6)
 expect(screen.getAllByTestId("barra-valor", H).map((t) => t.props.children)).toEqual(["R$ 6,2k"]);
});
it("valores ocultos: o gráfico e os valores somem ('Gráfico oculto') e a lista segue mascarada; voltam ao mostrar", async () => {
 await abrir();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); });
 expect(screen.queryByTestId("barras-mensais", H)).toBeNull();
 expect(screen.queryByText("R$ 7,2k", H)).toBeNull();
 expect(screen.getAllByText("Gráfico oculto")).toHaveLength(4); // Resumo do mês + Patrimônio (preset Autônomo) + este card + a Curva do "Saldo previsto" (sempre visível, 4 → 3 se o texto da Renda sumir)
 expect(screen.getAllByText("Saídas")).toHaveLength(6);
 expect(screen.queryByLabelText("7200 reais")).toBeNull();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Mostrar valores" })); });
 expect(screen.getByTestId("barras-mensais", H)).toBeTruthy();
});
it("todos os meses com renda 0: sem gráfico, só a lista", async () => {
 evolucao = { ...F[EVO], evolution: F[EVO].evolution.map((m: any) => ({ ...m, income: 0 })) };
 await abrir();
 expect(screen.queryByTestId("barras-mensais", H)).toBeNull();
 expect(screen.getAllByText("Saídas")).toHaveLength(6);
});
it("histórico vazio: o texto de antes e nenhum gráfico", async () => {
 evolucao = { ...F[EVO], evolution: [] };
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByText("Ainda não há histórico de renda disponível.")).toBeTruthy());
 expect(screen.queryByTestId("barras-mensais", H)).toBeNull();
});
it("hoje 31/10 (BRT) e o servidor já em novembro (UTC): o gráfico para no mês atual; a lista mantém os 7 meses", async () => {
 mockHoje = "2026-10-31";
 evolucao = { ...F[EVO], evolution: [...F[EVO].evolution, { month: "2026-11", income: 5000, expense: 100, net: 4900 }] };
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByText("novembro de 2026")).toBeTruthy());
 expect(screen.getAllByTestId("barra-mes", H).map((t) => t.props.children)).toEqual(["mai", "jun", "jul", "ago", "set", "out*"]);
 expect(screen.getAllByTestId("barra", H)).toHaveLength(6);
 expect(screen.getAllByText("Saídas")).toHaveLength(7);
});
