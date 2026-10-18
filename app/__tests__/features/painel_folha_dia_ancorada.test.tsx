// A folha do "Seu dia" é um painel ANCORADO (altura do conteúdo, teto de 80% da janela), não o pageSheet de tela cheia. O resto da folha: painel_folha_dia.test.tsx.
import { Dimensions, Modal, ScrollView, StyleSheet } from "react-native";
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { dataFalada } from "@/features/painel/folhaDia";
import { faladoComSinal } from "@/ui/dinheiro";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import gastosBase from "../../../tests/fixtures/app_gastos.json";

jest.setTimeout(30_000); // monta o painel inteiro pelo router
// Os ícones viram `View testID="icone-<nome>"`: é assim que o teste sabe qual seta e qual tom a pílula usa (mesmo recurso de painel_conversa_metas).
// eslint-disable-next-line @typescript-eslint/no-require-imports -- a fábrica do jest.mock não importa módulos de fora (hoisting)
jest.mock("@/ui/componentes/Icone", () => ({ Icone: ({ nome, tom }: { nome: string; tom?: string }) => require("react").createElement(require("react-native").View, { testID: `icone-${nome}`, tom }) }));

const F = fixture as Record<string, any>, DET = "/api/app/mes-detalhes", GAS = "/api/app/gastos";
const dia = (d: string, saiu: string, entrou = "0.00") => ({ dia: d, entrou, saiu });
// hoje do /gastos = 2026-10-14. 10-04 só tem entrada (na grade, fora de /gastos.dias); 09-30 é parcela de outro mês; 10-20 é agendado (em /gastos.dias).
const DIAS = [dia("2026-10-01", "5.00"), dia("2026-10-03", "1.14"), dia("2026-10-04", "0.00", "100.00"), dia("2026-10-12", "250.00"), dia("2026-10-14", "10.00"), dia("2026-10-20", "30.00"), dia("2026-09-30", "70.00")];
const gastosCom = (extra: Record<string, unknown> = {}) => ({ ...gastosBase, dias: [...gastosBase.dias, { dia: "2026-10-14", total: "10.00", maiores: [], quantidade: 3, categorias: [] }, { dia: "2026-10-20", total: "30.00", maiores: [], quantidade: 1, categorias: [] }], ...extra });
let detalhes: any, gastos: () => Promise<Response> | Response, corte: string | null;
beforeEach(async () => {
 prepararCaso(); desligarTrava(); detalhes = { ...F[DET], dias: DIAS }; gastos = () => resposta(200, gastosCom()); corte = null;
 await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true, history_earliest_date: corte });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === DET) return resposta(200, detalhes);
  if (path === GAS) return gastos();
  return resposta(200, F[path] ?? {});
 });
});
const janelaOriginal = Dimensions.get("window");
afterEach(async () => { jest.restoreAllMocks(); await act(async () => { Dimensions.set({ window: janelaOriginal }); }); });
const linhas = (id: string) => screen.getAllByTestId(`calendario-dia-${id}`);
async function abrir(url: "/gastos" | "/resumo" = "/gastos", espera = "2026-10-12") {
 renderRouter("./app", { initialUrl: url });
 await waitFor(() => expect(linhas(espera).length).toBeGreaterThan(0));
}
// `origem`: 0 = a linha da tela; 1 = a do Calendario dentro da folha de detalhe do Resumo (a última na árvore).
const tocar = async (d: string, origem = 0) => { await act(async () => { fireEvent.press(linhas(d).at(origem === 0 ? 0 : -1)!); await drenar(); }); };
const apertar = async (nome: string) => { await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); };
// `oculta`: conta também a folha numa aba que perdeu o foco (o RNTL a trata como aria-hidden; o Modal em si continuaria sobre o Extrato).
const titulos = (oculta = false) => screen.queryAllByRole("header", { includeHiddenElements: oculta }).map((h) => h.props.accessibilityLabel ?? h.props.children);
const tituloDa = (d: string, oculta = false) => titulos(oculta).includes(dataFalada(d)) || null;
const folhaAberta = (d: string) => waitFor(() => expect(tituloDa(d)).toBeTruthy());
const folha = () => within(screen.getByTestId("folha-dia"));
// O total é o 1º valor da folha. Os maiores falam o valor SEM sinal, então num dia de uma compra só repetem o rótulo do total: pegue o primeiro.
const totalDe = (centavos: number) => folha().getAllByLabelText(faladoComSinal(centavos))[0]!;


// A folha NÃO cobre a tela: painel ancorado embaixo, altura do conteúdo até 80% da janela (o `pageSheet` do Modal sobe quase inteiro e não tem detents).
describe("folha ancorada (altura do conteúdo)", () => {
 const painel = () => StyleSheet.flatten(screen.getByTestId("folha-painel").props.style);
 const ancestral = (prop: string) => { let n = screen.getByTestId("folha-painel").parent; while (n && !(prop === "justifyContent" ? StyleSheet.flatten(n.props.style)?.justifyContent : n.props[prop])) n = n.parent; return n!; };
 const aberta = async () => { await abrir(); await tocar("2026-10-12"); await folhaAberta("2026-10-12"); await waitFor(() => expect(totalDe(25000)).toBeTruthy()); };
 it("Modal transparente (não pageSheet), painel colado embaixo SEM altura fixa e com teto de 80% da janela", async () => {
  await act(async () => { Dimensions.set({ window: { ...janelaOriginal, width: 402, height: 800 } }); await drenar(); });
  await aberta();
  const modal = screen.UNSAFE_getAllByType(Modal).find((m) => m.props.visible)!; expect(modal.props.transparent).toBe(true); expect(modal.props.presentationStyle).toBeUndefined();
  expect(painel().maxHeight).toBe(640); // 0,8 x 800
  expect(painel().height).toBeUndefined(); expect(painel().flex).toBeUndefined(); // altura = o conteúdo, não a tela
  expect(painel().borderTopLeftRadius).toBe(16); expect(painel().borderTopRightRadius).toBe(16);
  expect(StyleSheet.flatten(ancestral("justifyContent").props.style).justifyContent).toBe("flex-end"); // o contêiner do painel o cola embaixo
 });
 it("o teto acompanha a janela: 0,8 x 600 = 480", async () => {
  await act(async () => { Dimensions.set({ window: { ...janelaOriginal, width: 402, height: 600 } }); await drenar(); });
  await aberta(); expect(painel().maxHeight).toBe(480);
 });
 it("a lista rola dentro do teto: o conteúdo está num ScrollView que encolhe (flexGrow 0) e o Fechar fica FORA dele, sempre à mão", async () => {
  await aberta();
  const rolagem = within(screen.getByTestId("folha-painel")).UNSAFE_getByType(ScrollView);
  expect(StyleSheet.flatten(rolagem.props.style).flexGrow).toBe(0);
  expect(within(rolagem).getByRole("button", { name: "Ver extrato do dia" })).toBeTruthy(); expect(within(rolagem).queryByRole("button", { name: "Fechar" })).toBeNull();
  expect(within(screen.getByTestId("folha-painel")).getByRole("button", { name: "Fechar" })).toBeTruthy();
 });
 it("o fundo escurecido fecha ao toque; tocar no painel (positivo) não fecha e trocar de dia segue valendo", async () => {
  await aberta();
  await apertar("Dia anterior"); await waitFor(() => expect(tituloDa("2026-10-12")).toBeNull()); // trocar de dia mantém a folha aberta
  await act(async () => { fireEvent.press(screen.getByTestId("folha-painel")); await drenar(); }); expect(screen.getByTestId("folha-painel")).toBeTruthy();
  await act(async () => { fireEvent.press(screen.getByTestId("folha-fundo")); await drenar(); });
  await waitFor(() => expect(screen.queryByTestId("folha-painel")).toBeNull());
 });
 it("arrastar o grabber para baixo (> 60pt) fecha; um arrasto curto ou para cima não", async () => {
  await aberta(); const g = screen.getByTestId("folha-grabber");
  const arrasto = async (de: number, ate: number) => { await act(async () => { fireEvent(g, "touchStart", { nativeEvent: { pageY: de } }); fireEvent(g, "touchEnd", { nativeEvent: { pageY: ate } }); await drenar(); }); };
  await arrasto(300, 330); await arrasto(300, 200); expect(tituloDa("2026-10-12")).toBe(true);
  await arrasto(300, 400); await waitFor(() => expect(tituloDa("2026-10-12")).toBeNull());
 });
 it("escape do VoiceOver fecha e o painel é modal para o VoiceOver", async () => {
  await aberta(); expect(ancestral("accessibilityViewIsModal").props.accessibilityViewIsModal).toBe(true);
  await act(async () => { screen.getByTestId("folha-painel").props.onAccessibilityEscape(); await drenar(); });
  await waitFor(() => expect(tituloDa("2026-10-12")).toBeNull());
 });
});
