import { DateTimePickerAndroid } from "@react-native-community/datetimepicker";
import { router } from "expo-router";
import { Dimensions, Platform, StyleSheet } from "react-native";
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { mesAtual } from "@/features/painel/catalogo";
import { isoDoSeletor } from "@/features/painel/metasEscrita";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { abrirFiltros, ficha, filtrar, nFichas } from "./extrato_filtros_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
// Botão "Filtros" do Extrato: a folha guarda um rascunho e a consulta só muda em "Aplicar filtros". O seletor de data é nativo: o dublê expõe as props (min/max do mês) e o `onChange`.
jest.mock("@react-native-community/datetimepicker", () => ({ __esModule: true, default: (p: object) => jest.requireActual("react").createElement(jest.requireActual("react-native").View, { testID: "seletor-data", ...p }), DateTimePickerAndroid: { open: jest.fn() } }));
const LANC = "/api/app/lancamentos";
const chamadas = () => fetchFalso.mock.calls.filter(([url]) => new URL(String(url)).pathname === LANC);
const consulta = () => new URL(String(chamadas().at(-1)![0])).searchParams;
beforeEach(async () => {
 prepararCaso(); desligarTrava(); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  return resposta(200, path === LANC ? { ...fixture[LANC], proximo: null } : (fixture as Record<string, unknown>)[path] ?? {});
 });
});
afterEach(() => jest.restoreAllMocks());
let r: ReturnType<typeof renderRouter>;
const abrir = async (url = "/extrato") => { r = renderRouter("./app", { initialUrl: url }); await waitFor(() => expect(screen.getByText("Salário")).toBeTruthy()); await act(drenar); };
const aplicado = (campos: Record<string, string | null>) => { for (const [k, v] of Object.entries(campos)) expect(consulta().get(k)).toBe(v); };

it("o botão Filtros abre a folha com tipo, origem, dia e valores; sem filtro não há ficha nem contagem", async () => {
 await abrir(); expect(screen.queryByText("Aplicar filtros")).toBeNull(); expect(nFichas()).toBe(0); expect(screen.getByRole("button", { name: "Filtros", selected: false })).toBeTruthy();
 expect(screen.queryByRole("button", { name: "Todas as origens" })).toBeNull(); // as duas fileiras de fichas saíram da tela
 await abrirFiltros();
 for (const t of ["Aplicar filtros", "Limpar tudo", "Tipo", "Origem", "Qualquer dia"]) expect(screen.getByText(t)).toBeTruthy();
 expect(screen.getByLabelText(/^Valor mínimo/)).toBeTruthy(); expect(screen.getByLabelText(/^Valor máximo/)).toBeTruthy(); expect(screen.getByRole("tab", { name: "Saídas" })).toBeTruthy();
});
it("tipo + valor mínimo montam a query (tipo=saida&valor_min=10.00) e só ao aplicar", async () => {
 await abrir(); const antes = chamadas().length;
 await filtrar({ tipo: "Saídas", min: "1000" }, false); await act(drenar);
 expect(chamadas().length).toBe(antes); // o rascunho não consulta
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Aplicar filtros" })); await drenar(); });
 await waitFor(() => expect(chamadas().length).toBeGreaterThan(antes)); aplicado({ tipo: "saida", valor_min: "10.00", valor_max: null });
});
it("faixa: valor_min e valor_max em N.NN exato (centavos, sem float)", async () => {
 await abrir(); await filtrar({ min: "100005", max: "2500000" }); await waitFor(() => expect(consulta().get("valor_max")).toBe("25000.00")); aplicado({ valor_min: "1000.05" });
 expect(screen.getByText("R$ 1.000,05 a R$ 25.000,00")).toBeTruthy();
});
it("com Ocultar valores a ficha de valor diz só 'Valor' (sem faixa); desligado, mostra a faixa", async () => {
 await abrir(); await filtrar({ min: "100005", max: "2500000" }); await waitFor(() => expect(screen.getByText("R$ 1.000,05 a R$ 25.000,00")).toBeTruthy()); // controle positivo
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); await drenar(); });
 expect(ficha("valor")).toBeTruthy(); expect(within(ficha("valor")!).getByText("Valor")).toBeTruthy(); expect(within(ficha("valor")!).queryByText(/R\$|\d/)).toBeNull(); expect(screen.queryByText(/1\.000,05|25\.000/)).toBeNull();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Mostrar valores" })); await drenar(); });
 expect(screen.getByText("R$ 1.000,05 a R$ 25.000,00")).toBeTruthy();
});
it.each([[{ min: "1234" }, "A partir de R$ 12,34", /12,34/], [{ max: "99999" }, "Até R$ 999,99", /999,99/]])("com Ocultar valores, só %j também diz só 'Valor' (sem número); desligado, mostra '%s'", async (escolha, texto, numero) => {
 await abrir(); await filtrar(escolha); await waitFor(() => expect(screen.getByText(texto)).toBeTruthy()); // controle positivo
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); await drenar(); });
 expect(within(ficha("valor")!).getByText("Valor")).toBeTruthy(); expect(within(ficha("valor")!).queryByText(/R\$|\d/)).toBeNull(); expect(screen.queryByText(numero)).toBeNull();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Mostrar valores" })); await drenar(); });
 expect(screen.getByText(texto)).toBeTruthy();
});
it("mínimo maior que o máximo: erro no campo e nenhuma consulta", async () => {
 await abrir(); const antes = chamadas().length; await filtrar({ min: "5000", max: "1000" }); await act(drenar);
 expect(screen.getByText("O máximo não pode ser menor que o mínimo.")).toBeTruthy(); expect(chamadas().length).toBe(antes);
});
it("a contagem de fichas soma tipo, origem, valor, dia e categoria (min e max são UM filtro)", async () => {
 await abrir("/extrato?dia=2026-10-02&categoria=alimentacao"); expect(nFichas()).toBe(2);
 await filtrar({ tipo: "Entradas", origem: "Banco", min: "1000", max: "9000" }); await waitFor(() => expect(nFichas()).toBe(5));
 expect(screen.getByRole("button", { name: "Filtros", selected: true })).toBeTruthy(); // o rótulo falado não muda com a contagem (o botão é só ícone); o VoiceOver ouve "selecionado"
});
it("min == max é válido (valor exato) e a ficha diz 'A partir de' só com mínimo, 'Até' só com máximo", async () => {
 await abrir(); await filtrar({ min: "5000", max: "5000" }); await waitFor(() => expect(consulta().get("valor_min")).toBe("50.00")); aplicado({ valor_max: "50.00" });
 expect(screen.queryByText("O máximo não pode ser menor que o mínimo.")).toBeNull(); expect(screen.getByText("R$ 50,00 a R$ 50,00")).toBeTruthy();
 await act(async () => { fireEvent.press(ficha("valor")!); await drenar(); }); await filtrar({ min: "1234" });
 await waitFor(() => expect(screen.getByText("A partir de R$ 12,34")).toBeTruthy()); aplicado({ valor_min: "12.34", valor_max: null });
 await act(async () => { fireEvent.press(ficha("valor")!); await drenar(); }); await filtrar({ max: "99999" });
 await waitFor(() => expect(screen.getByText("Até R$ 999,99")).toBeTruthy()); aplicado({ valor_min: null, valor_max: "999.99" });
});
it("tirar uma ficha refaz a consulta sem ela e mantém as outras", async () => {
 await abrir(); await filtrar({ tipo: "Saídas", min: "1000", max: "9000" }); await waitFor(() => expect(consulta().get("valor_min")).toBe("10.00")); const antes = chamadas().length;
 await act(async () => { fireEvent.press(ficha("valor")!); await drenar(); });
 await waitFor(() => expect(chamadas().length).toBeGreaterThan(antes)); aplicado({ tipo: "saida", valor_min: null, valor_max: null });
 expect(ficha("valor")).toBeNull(); expect(nFichas()).toBe(0); // sobrou só tipo=saida, que o atalho "Saídas" já mostra: sem ficha
});
it("Limpar tudo tira tipo, origem, valor, dia e categoria de uma vez e fecha a folha", async () => {
 await abrir("/extrato?dia=2026-10-02&categoria=alimentacao"); await filtrar({ tipo: "Saídas", origem: "Cartão", min: "1000" });
 await waitFor(() => expect(consulta().get("valor_min")).toBe("10.00")); await abrirFiltros();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Limpar tudo" })); await drenar(); });
 await waitFor(() => expect([...consulta().keys()].sort()).toEqual(["limite", "mes"]));
 expect(nFichas()).toBe(0); expect(screen.queryByText("Aplicar filtros")).toBeNull(); expect(screen.queryByRole("button", { name: /^Limpar filtro/ })).toBeNull();
});
it("fechar a folha sem aplicar descarta o rascunho", async () => {
 await abrir(); const antes = chamadas().length; await filtrar({ tipo: "Saídas", min: "1000" }, false);
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Fechar" })); await drenar(); }); await abrirFiltros();
 expect(screen.getByRole("tab", { name: "Tudo" }).props.accessibilityState.selected).toBe(true); expect(chamadas().length).toBe(antes);
});
describe("dia pelo seletor", () => {
 it("dia de OUTRO mês no param entra como 'Qualquer dia' e 'Aplicar' não o reenvia", async () => {
  await abrir("/extrato?dia=2025-01-05"); await waitFor(() => expect(consulta().get("dia")).toBe("2025-01-05")); expect(nFichas()).toBe(1);
  await abrirFiltros(); expect(screen.getByText("Qualquer dia")).toBeTruthy(); expect(screen.queryByTestId("seletor-data")).toBeNull();
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Aplicar filtros" })); await drenar(); });
  await waitFor(() => expect(consulta().has("dia")).toBe(false)); expect(r.getSearchParams().dia).toBeUndefined(); expect(nFichas()).toBe(0);
 });
 const [ano, mes] = mesAtual().split("-").map(Number) as [number, number];
 const dia15 = `${mesAtual()}-15`, cDia = `15/${String(mes).padStart(2, "0")}/${ano}`;
 it("iOS: o seletor fica limitado ao mês mostrado e o dia escolhido vai para o route param e para a consulta", async () => {
  await abrir(); await abrirFiltros();
  await act(async () => { fireEvent.press(screen.getByText("Qualquer dia")); await drenar(); });
  const seletor = screen.getByTestId("seletor-data");
  expect(isoDoSeletor(seletor.props.minimumDate)).toBe(`${mesAtual()}-01`); expect(isoDoSeletor(seletor.props.maximumDate)).toBe(isoDoSeletor(new Date(ano, mes, 0)));
  await act(async () => { seletor.props.onChange({}, new Date(ano, mes - 1, 15, 12)); await drenar(); });
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Aplicar filtros" })); await drenar(); });
  await waitFor(() => expect(consulta().get("dia")).toBe(dia15)); expect(r.getSearchParams().dia).toBe(dia15); expect(screen.getByRole("button", { name: `Limpar filtro do dia ${cDia}` })).toBeTruthy();
 });
 it("'Qualquer dia' limpa o dia escolhido antes: o param sai", async () => {
  await abrir(`/extrato?dia=${dia15}`); await waitFor(() => expect(consulta().get("dia")).toBe(dia15)); await abrirFiltros();
  await act(async () => { fireEvent.press(screen.getAllByText("Qualquer dia")[0]!); await drenar(); });
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Aplicar filtros" })); await drenar(); });
  await waitFor(() => expect(consulta().has("dia")).toBe(false)); expect(r.getSearchParams().dia).toBeUndefined(); expect(nFichas()).toBe(0);
 });
 it("Android: abre o diálogo nativo limitado ao mês, e o dia escolhido filtra", async () => {
  jest.replaceProperty(Platform, "OS", "android"); const abrirAndroid = DateTimePickerAndroid.open as jest.Mock;
  await abrir(); await abrirFiltros(); await act(async () => { fireEvent.press(screen.getByText("Qualquer dia")); await drenar(); });
  const args = abrirAndroid.mock.calls.at(-1)![0]; expect(isoDoSeletor(args.minimumDate)).toBe(`${mesAtual()}-01`); expect(isoDoSeletor(args.maximumDate)).toBe(isoDoSeletor(new Date(ano, mes, 0)));
  await act(async () => { args.onChange({ type: "set" }, new Date(ano, mes - 1, 15, 12)); await drenar(); });
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Aplicar filtros" })); await drenar(); });
  await waitFor(() => expect(consulta().get("dia")).toBe(dia15));
 });
 it("a rota navega com o dia do calendário e o botão já conta o dia", async () => {
  await abrir(); await act(async () => { router.setParams({ dia: dia15 }); await drenar(); }); await waitFor(() => expect(nFichas()).toBe(1)); expect(consulta().get("dia")).toBe(dia15);
 });
});

// Layout do botão: ao lado da busca; com fonte ampliada (fontScale > 1.1) a linha vira coluna. Mede o `flexDirection` do pai imediato do botão.
describe("Filtros e fonte ampliada", () => {
 const janela = Dimensions.get("window");
 const fonte = (fontScale: number) => act(async () => { Dimensions.set({ window: { ...janela, width: 402, height: 874, fontScale } }); await drenar(); });
 const direcao = () => { let p = screen.getByRole("button", { name: "Filtros" }).parent; while (p && StyleSheet.flatten(p.props.style)?.flexDirection === undefined) p = p.parent; return StyleSheet.flatten(p!.props.style).flexDirection; };
 afterEach(() => fonte(janela.fontScale));
 it.each([[1, "row"], [2, "column"]])("fontScale %s: busca e botão em %s", async (escala, esperado) => {
  await fonte(escala); await abrir(); expect(direcao()).toBe(esperado);
 });
});
