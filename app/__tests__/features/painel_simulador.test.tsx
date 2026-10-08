import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { Dimensions, StyleSheet, View } from "react-native";
import { CORES_CATEGORIA } from "@/features/painel/base";
import { escuro } from "@/ui/tokens";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
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
const janela = Dimensions.get("window");
afterEach(() => { Dimensions.set({ window: janela }); jest.restoreAllMocks(); });
const mover = (rotulo: string, valor: number) => act(async () => { fireEvent(screen.getByLabelText(rotulo), "valueChange", valor); await drenar(); });
it("slider por categoria muda a economia; valor quebrado vai ao passo de 10; Zerar volta tudo a 0", async () => {
 renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getByLabelText("Corte em Alimentação")).toBeTruthy());
 // Alimentação gasta R$ 980,50 no fixture: 30% = R$ 294,15, na linha e no total.
 await mover("Corte em Alimentação", 30); expect(screen.getByText("30%")).toBeTruthy(); expect(screen.getAllByLabelText("294 reais e 15 centavos")).toHaveLength(2);
 // percentualEmCentavos lança com percentual não inteiro: 33,7 tem de virar 30 antes do estado. Moradia R$ 900,00 → R$ 270,00.
 await mover("Corte em Moradia", 33.7); expect(screen.getAllByText("30%")).toHaveLength(2); expect(screen.getByLabelText("270 reais")).toBeTruthy();
 expect(screen.getByLabelText("564 reais e 15 centavos")).toBeTruthy();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Zerar simulação" })); await drenar(); });
 expect(screen.queryByText("30%")).toBeNull(); expect(screen.getByLabelText("Corte em Moradia").props.value).toBe(0);
});
type No = ReturnType<typeof screen.getByText>;
const linha = (no: No) => { let p = no.parent; while (p && !(String(p.type) === "View" && StyleSheet.flatten(p.props.style)?.flexDirection === "row")) p = p.parent; return p!; };
it("Simulador: nome à esquerda e '30% · valor' à direita na mesma linha, sem quebrar o valor; slider embaixo", async () => {
 Dimensions.set({ window: { ...janela, width: 402, height: 874, fontScale: 1 } }); // fonte padrão: o modo ampliado deixa a linha quebrar de propósito
 renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getByLabelText("Corte em Alimentação")).toBeTruthy());
 await mover("Corte em Alimentação", 30);
 const valores = linha(screen.getByText("30%")), cabecalho = linha(valores);
 expect(within(valores).getByLabelText("294 reais e 15 centavos")).toBeTruthy(); expect(within(cabecalho).getByText("Alimentação")).toBeTruthy();
 expect(StyleSheet.flatten(valores.props.style).flexShrink).toBe(0); expect(StyleSheet.flatten(cabecalho.props.style).flexWrap).toBe("nowrap");
 expect(within(cabecalho).queryByLabelText("Corte em Alimentação")).toBeNull();
});
it("Para onde vai: a barra de cada categoria tem a cor da fatia dela no donut", async () => {
 renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getByLabelText("Corte em Moradia")).toBeTruthy());
 const cores = (nome: RegExp) => screen.getByRole("button", { name: nome }).findAll((n) => n.type === View).map((n) => StyleSheet.flatten(n.props.style)?.backgroundColor).filter(Boolean);
 expect(cores(/^Alimentação/)).toContain(escuro[CORES_CATEGORIA[0]]); expect(cores(/^Moradia/)).toContain(escuro[CORES_CATEGORIA[1]]);
});
