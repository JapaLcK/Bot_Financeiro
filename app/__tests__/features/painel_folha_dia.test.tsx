// A folha do "Seu dia" (PR 2b): toque no dia do Calendario abre a Folha; setas; estados; fechar antes do Extrato nas 3 origens. Molde: painel_numeros_gastos.
import { AccessibilityInfo, Dimensions, StyleSheet } from "react-native";
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { mesAtual, mesesAnteriores, nomeMes } from "@/features/painel/catalogo";
import { abreFolha, dataCurta, dataFalada } from "@/features/painel/folhaDia";
import { falado, faladoComSinal } from "@/ui/dinheiro";
import { CORES_CATEGORIA, claro, escuro } from "@/ui/tokens";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import gastosBase from "../../../tests/fixtures/app_gastos.json";
import tabela from "../../../tests/fixtures/gastos_dia_casos.json";

jest.setTimeout(30_000); // monta o painel inteiro pelo router
// Os ícones viram `View testID="icone-<nome>"`: é assim que o teste sabe qual seta e qual tom a pílula usa (mesmo recurso de painel_conversa_metas).
// eslint-disable-next-line @typescript-eslint/no-require-imports -- a fábrica do jest.mock não importa módulos de fora (hoisting)
jest.mock("@/ui/componentes/Icone", () => ({ Icone: ({ nome, tom }: { nome: string; tom?: string }) => require("react").createElement(require("react-native").View, { testID: `icone-${nome}`, tom }) }));

// O stub do Phosphor (iconeStub) descarta as props; aqui ele expõe a `color` que o IconeCategoria repassa, para provar que o ícone usa a cor da fatia.
// eslint-disable-next-line @typescript-eslint/no-require-imports -- a fábrica do jest.mock não importa módulos de fora (hoisting)
jest.mock("../ui/__mocks__/iconeStub", () => ({ __esModule: true, default: ({ color }: { color?: string }) => require("react").createElement(require("react-native").View, { testID: "cor-icone", color }) }));
const F = fixture as Record<string, any>, DET = "/api/app/mes-detalhes", GAS = "/api/app/gastos", LANC = "/api/app/lancamentos";
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
const chamadas = (path: string) => fetchFalso.mock.calls.filter(([u]) => new URL(String(u)).pathname === path);
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
const desativada = (nome: string) => screen.getByRole("button", { name: nome }).props.accessibilityState.disabled;
const folha = () => within(screen.getByTestId("folha-dia"));
// O total é o 1º valor da folha. Os maiores falam o valor SEM sinal, então num dia de uma compra só repetem o rótulo do total: pegue o primeiro.
const totalDe = (centavos: number) => folha().getAllByLabelText(faladoComSinal(centavos))[0]!;
// `announceForAccessibility` já é um jest.fn do preset do RN: `restoreAllMocks` não zera as chamadas de outro caso.
const anunciar = () => { const s = jest.spyOn(AccessibilityInfo, "announceForAccessibility").mockImplementation(() => {}); s.mockClear(); return s; };
const extratoMontado = () => waitFor(() => expect(screen.getByLabelText("Buscar lançamento")).toBeTruthy());

describe("regra síncrona de abrir (puro)", () => {
 it("abre no mês exibido e depois do corte; senão vai ao extrato", () => {
  expect(abreFolha("2026-10-12", "2026-10", null)).toBe(true);
  expect(abreFolha("2026-10-12", "2026-10", "2026-10-12")).toBe(true); // o próprio dia do corte abre
  expect(abreFolha("2026-10-03", "2026-10", "2026-10-05")).toBe(false); // antes do corte
  expect(abreFolha("2026-09-30", "2026-10", null)).toBe(false); // parcela de outro mês
  expect(abreFolha("2025-10-12", "2026-10", null)).toBe(false); // outro ano
 });
 it("data curta e falada", () => {
  expect(dataCurta("2026-10-16")).toBe("sex, 16 out"); expect(dataFalada("2026-10-16")).toBe("sexta-feira, 16 de outubro"); expect(dataCurta("2026-10-04")).toBe("dom, 4 out");
 });
});

describe("abrir pelo toque", () => {
 it("abre a Folha com a data como título, o total e o contador; a leitura de /gastos só começa ao tocar", async () => {
  await abrir(); expect(chamadas(GAS)).toHaveLength(0);
  await tocar("2026-10-12"); await folhaAberta("2026-10-12");
  expect(screen.getByText("seg, 12 out")).toBeTruthy(); expect(chamadas(GAS)).toHaveLength(1); expect(String(chamadas(GAS)[0]![0])).toContain("mes=2026-10");
  await waitFor(() => expect(totalDe(25000)).toBeTruthy());
  expect(folha().getByLabelText("1 compra")).toBeTruthy(); expect(screen.getByRole("button", { name: "Ver extrato do dia" })).toBeTruthy();
  expect(chamadas(LANC)).toHaveLength(0); // abrir a folha não vai ao extrato
 });
 it("dia de outro mês (parcela) NAVEGA ao extrato, sem folha e sem ler /gastos (controle negativo da regra síncrona: sem ela abriria com R$ 0,00)", async () => {
  await abrir(); await tocar("2026-09-30"); await extratoMontado();
  expect(chamadas(GAS)).toHaveLength(0); expect(screen.queryByRole("header", { name: /setembro|outubro/ })).toBeNull();
  await waitFor(() => expect(String(chamadas(LANC).at(-1)![0])).toContain("dia=2026-09-30"));
 });
 it("antes do corte do plano navega ao extrato; no corte e depois, abre (positivo)", async () => {
  corte = "2026-10-05"; await abrir();
  await tocar("2026-10-03"); await extratoMontado(); expect(chamadas(GAS)).toHaveLength(0);
 });
 it("no corte abre a folha (positivo do mesmo grupo)", async () => {
  corte = "2026-10-05"; await abrir(); await tocar("2026-10-12"); await folhaAberta("2026-10-12");
 });
});

describe("cache do mês", () => {
 it("abrir, fechar e abrir de novo no mesmo mês lê /gastos UMA vez (o hook persiste entre aberturas; sem isso são 2 leituras)", async () => {
  await abrir(); await tocar("2026-10-12"); await folhaAberta("2026-10-12"); await waitFor(() => expect(totalDe(25000)).toBeTruthy());
  await apertar("Fechar"); await waitFor(() => expect(tituloDa("2026-10-12")).toBeNull());
  await tocar("2026-10-14"); await folhaAberta("2026-10-14"); await waitFor(() => expect(totalDe(1000)).toBeTruthy());
  expect(chamadas(GAS)).toHaveLength(1);
 });
});

describe("setas", () => {
 it("trocar de dia não fecha a folha nem lê /gastos de novo; dia sem saída mostra R$ 0,00 sem contador", async () => {
  const anuncio = anunciar();
  await abrir(); await tocar("2026-10-12"); await folhaAberta("2026-10-12"); await waitFor(() => expect(totalDe(25000)).toBeTruthy());
  expect(anuncio).not.toHaveBeenCalled(); // a abertura cai no título, não anuncia
  await apertar("Dia anterior"); await folhaAberta("2026-10-11");
  expect(screen.getByText("dom, 11 out")).toBeTruthy(); expect(tituloDa("2026-10-12")).toBeNull();
  expect(totalDe(0)).toBeTruthy(); expect(folha().queryByLabelText(/compra/)).toBeNull();
  expect(chamadas(GAS)).toHaveLength(1);
  expect(anuncio).toHaveBeenCalledTimes(1); expect(anuncio).toHaveBeenCalledWith(`${dataFalada("2026-10-11")}, ${faladoComSinal(0)}`);
  await apertar("Dia seguinte"); await folhaAberta("2026-10-12"); expect(anuncio).toHaveBeenLastCalledWith(`${dataFalada("2026-10-12")}, ${faladoComSinal(25000)}`);
 });
 it("limite inferior: no dia 1 a seta anterior é desabilitada; a seguinte abre o dia 2 (sem saída, na grade) e volta", async () => {
  await abrir(); await tocar("2026-10-01"); await folhaAberta("2026-10-01"); await waitFor(() => expect(desativada("Dia anterior")).toBe(true)); expect(desativada("Dia seguinte")).toBe(false);
  await apertar("Dia seguinte"); await folhaAberta("2026-10-02"); await apertar("Dia anterior"); await folhaAberta("2026-10-01");
 });
 it("hoje: o seguinte (futuro sem gasto) está desabilitado e o total aparece sem ícone de agendado", async () => {
  await abrir(); await tocar("2026-10-14"); await folhaAberta("2026-10-14");
  await waitFor(() => expect(totalDe(1000)).toBeTruthy());
  expect(desativada("Dia seguinte")).toBe(true); expect(desativada("Dia anterior")).toBe(false); expect(folha().queryByLabelText("Agendado")).toBeNull(); expect(folha().getByLabelText("3 compras")).toBeTruthy();
 });
 it("agendado (dia futuro com saída em /gastos): ícone de calendário ao lado do total; sem seguinte (21 não tem gasto agendado)", async () => {
  await abrir(); await tocar("2026-10-20"); await folhaAberta("2026-10-20");
  await waitFor(() => expect(totalDe(3000)).toBeTruthy()); expect(folha().getByLabelText("Agendado")).toBeTruthy(); expect(desativada("Dia seguinte")).toBe(true);
 });
 it("dia futuro SÓ com entrada (ausente de /gastos.dias) abre em R$ 0,00 com o botão do extrato", async () => {
  detalhes = { ...F[DET], dias: [dia("2026-10-20", "0.00", "50.00")] }; gastos = () => resposta(200, gastosBase); await abrir("/gastos", "2026-10-20");
  await tocar("2026-10-20"); await folhaAberta("2026-10-20");
  await waitFor(() => expect(totalDe(0)).toBeTruthy()); expect(folha().queryByLabelText("Agendado")).toBeNull(); expect(screen.getByRole("button", { name: "Ver extrato do dia" })).toBeTruthy();
 });
 it("antes do corte do plano a seta anterior é desabilitada (nunca R$ 0,00 de um dia desconhecido)", async () => {
  gastos = () => resposta(200, gastosCom({ historico_desde: "2026-10-12" })); corte = "2026-10-12"; await abrir();
  await tocar("2026-10-12"); await folhaAberta("2026-10-12"); await waitFor(() => expect(desativada("Dia anterior")).toBe(true));
 });
});

describe("estados", () => {
 it("corte do /gastos depois do dia e /auth/me sem corte: só a data e o botão, nunca R$ 0,00 (a checagem do Corpo)", async () => {
  gastos = () => resposta(200, gastosCom({ historico_desde: "2026-10-13" })); await abrir(); await tocar("2026-10-12"); await folhaAberta("2026-10-12");
  await waitFor(() => expect(desativada("Dia anterior")).toBe(true)); await act(drenar); expect(chamadas(GAS)).toHaveLength(1);
  expect(folha().queryByLabelText(faladoComSinal(0))).toBeNull(); expect(folha().queryByLabelText(faladoComSinal(25000))).toBeNull(); expect(folha().queryByLabelText("Carregando dados")).toBeNull();
  expect(desativada("Dia seguinte")).toBe(true); expect(screen.getByRole("button", { name: "Ver extrato do dia" })).toBeTruthy();
 });
 it("carregando: data e Fechar na tela, setas desabilitadas, botão do extrato presente; depois o total", async () => {
  const s = segurar(); gastos = async () => { await s.promessa; return resposta(200, gastosCom()); };
  await abrir(); await tocar("2026-10-12"); await folhaAberta("2026-10-12");
  expect(screen.getByText("seg, 12 out")).toBeTruthy(); expect(screen.getByRole("button", { name: "Fechar" })).toBeTruthy();
  expect(folha().getAllByLabelText("Carregando dados").length).toBeGreaterThan(0); expect(desativada("Dia anterior")).toBe(true); expect(desativada("Dia seguinte")).toBe(true);
  expect(screen.getByRole("button", { name: "Ver extrato do dia" })).toBeTruthy();
  await act(async () => { s.soltar(); await drenar(); }); await waitFor(() => expect(totalDe(25000)).toBeTruthy()); expect(desativada("Dia anterior")).toBe(false);
 });
 it("erro: mensagem e 'Tentar novamente' que repete SÓ a leitura de /gastos", async () => {
  let falha = true; gastos = () => (falha ? resposta(500, {}) : resposta(200, gastosCom()));
  await abrir(); await tocar("2026-10-12"); await folhaAberta("2026-10-12");
  await waitFor(() => expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeTruthy());
  expect(desativada("Dia anterior")).toBe(true); expect(folha().queryByLabelText(/reais/)).toBeNull(); // nunca R$ 0,00 no erro
  const [det, res] = [chamadas(DET).length, chamadas("/api/app/resumo-do-mes").length]; falha = false;
  await apertar("Tentar novamente"); await waitFor(() => expect(totalDe(25000)).toBeTruthy());
  expect(chamadas(GAS)).toHaveLength(2); expect(chamadas(DET)).toHaveLength(det); expect(chamadas("/api/app/resumo-do-mes")).toHaveLength(res); // o do RecursoDados recarregaria os 15 recursos
 });
 it("disponivel:false: só a data e o botão do extrato, nunca R$ 0,00", async () => {
  gastos = () => resposta(200, gastosCom({ disponivel: false, dias: [] })); await abrir(); await tocar("2026-10-12"); await folhaAberta("2026-10-12");
  await waitFor(() => expect(desativada("Dia anterior")).toBe(true)); // o hook já respondeu
  await act(drenar); expect(chamadas(GAS)).toHaveLength(1); expect(folha().queryByLabelText(faladoComSinal(0))).toBeNull(); expect(folha().queryByLabelText("Carregando dados")).toBeNull();
  expect(desativada("Dia seguinte")).toBe(true); expect(screen.getByRole("button", { name: "Ver extrato do dia" })).toBeTruthy();
 });
 it("valores ocultos: total mascarado, label sem valor e o anúncio da troca só com a data", async () => {
  const anuncio = anunciar();
  await abrir(); await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); });
  await tocar("2026-10-12"); await folhaAberta("2026-10-12"); await waitFor(() => expect(desativada("Dia anterior")).toBe(false));
  expect(folha().queryByLabelText(faladoComSinal(25000))).toBeNull(); expect(folha().getAllByLabelText("Valor oculto").length).toBeGreaterThan(0);
  await apertar("Dia anterior"); await folhaAberta("2026-10-11"); expect(anuncio).toHaveBeenCalledWith(dataFalada("2026-10-11"));
  expect(JSON.stringify(anuncio.mock.calls)).not.toMatch(/reais|centavo|R\$/);
 });
 it.each([[1, "linha"], [2, "coluna"]])("fontScale %s: setas com 44pt; %s (as setas só dividem a linha com o total quando não é ampliado)", async (fontScale, _n) => {
  await act(async () => { Dimensions.set({ window: { ...janelaOriginal, width: 402, height: 874, fontScale } }); await drenar(); });
  await abrir(); await tocar("2026-10-12"); await folhaAberta("2026-10-12"); await waitFor(() => expect(totalDe(25000)).toBeTruthy());
  for (const nome of ["Dia anterior", "Dia seguinte"]) { const e = StyleSheet.flatten(screen.getByRole("button", { name: nome }).props.style); expect(e.minHeight).toBeGreaterThanOrEqual(44); }
  const linhaDe = (no: any) => { let p = no.parent; while (p && StyleSheet.flatten(p.props?.style)?.flexDirection !== "row") p = p.parent; return p; };
  const linha = linhaDe(screen.getByRole("button", { name: "Dia anterior" })), total = totalDe(25000);
  expect(within(linha).queryByLabelText(faladoComSinal(25000))).toBe(fontScale === 1 ? total : null);
 });
});

describe("Ver extrato do dia fecha a folha e o Extrato monta com o dia (as 3 origens)", () => {
 // Voltar à aba de origem: se `fechar()` não rodou antes do Extrato, a folha (estado ainda aberto) REAPARECE aqui.
 const vai = async (origem: number, url: string, aindaAbertas: () => void = () => {}) => {
  await tocar("2026-10-12", origem); await folhaAberta("2026-10-12");
  await apertar("Ver extrato do dia"); await extratoMontado();
  await waitFor(() => expect(String(chamadas(LANC).at(-1)![0])).toContain("dia=2026-10-12"));
  expect(tituloDa("2026-10-12", true)).toBeNull(); aindaAbertas();
  await act(async () => { router.navigate(url as any); await drenar(); });
  await waitFor(() => expect(linhas("2026-10-12").length).toBeGreaterThan(0)); await act(drenar);
  expect(tituloDa("2026-10-12")).toBeNull(); expect(tituloDa("2026-10-12", true)).toBeNull(); // controle negativo: sem o `fechar()` antes do `irAoExtrato`, a folha volta
 };
 it("aba Gastos", async () => { await abrir("/gastos"); await vai(0, "/gastos"); });
 it("Resumo em linha", async () => { await abrir("/resumo"); await vai(0, "/resumo"); });
 it("Resumo na folha de detalhe (folha dentro de folha): nenhuma folha sobra visível; sem `&& focado` a do Resumo continuaria (controle negativo)", async () => {
  await abrir("/resumo"); await apertar("Abrir Dia a dia"); await waitFor(() => expect(screen.getByRole("header", { name: "Dia a dia" })).toBeTruthy());
  await vai(1, "/resumo", () => expect(titulos(true)).not.toContain("Dia a dia")); // a folha de detalhe do Resumo (estado `detalhe` ainda aberto) some com a aba
 });
});

describe("mês do painel", () => {
 it("trocar o mês fecha a folha do dia", async () => {
  await abrir(); await tocar("2026-10-12"); await folhaAberta("2026-10-12");
  await apertar(nomeMes(mesAtual()).slice(0, 3)); await apertar(nomeMes(mesesAnteriores(mesAtual())[1]!));
  await waitFor(() => expect(tituloDa("2026-10-12")).toBeNull());
 });
});

// ---- PR 3: pílula contra a média, barra com ícones e 3 maiores ----
type Par = [string, string]; // [chave, valor bruto] / [descricao, valor]
const D = (d: string, total: string, categorias: Par[] = [], maiores: Par[] = []) => ({ dia: d, total, quantidade: 1, categorias: categorias.map(([chave, valor]) => ({ chave, valor })), maiores: maiores.map(([descricao, valor]) => ({ descricao, valor, categoria: "x" })) });
const pronta = (oculto = false) => waitFor(() => expect(folha().getAllByLabelText(oculto ? "Valor oculto" : /real|reais|centavo/).length).toBeGreaterThan(0));
async function abrirDia(d: string, dias: ReturnType<typeof D>[], extra: Record<string, unknown> = {}, oculto = false, fontScale = 1) { // o fontScale padrão do jest é 2 (ampliado): fixar 1 para a barra aparecer
 await act(async () => { Dimensions.set({ window: { ...janelaOriginal, width: 402, height: 874, fontScale } }); await drenar(); });
 gastos = () => resposta(200, { ...gastosBase, dias, ...extra }); detalhes = { ...F[DET], dias: dias.map((x) => dia(x.dia, x.total)) };
 await abrir("/gastos", d); if (oculto) await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); });
 await tocar(d); await folhaAberta(d); await pronta(oculto);
}
// Um caso da tabela dourada (a mesma que prende `gastosDia.ts`): o total de cada dia vira o do /gastos e o corte vira o do /auth/me.
const doCaso = (i: number, d: string, oculto = false) => { const e = tabela[i]!.entrada; corte = e.historico_desde; return abrirDia(d, e.dias.map((x) => D(x.dia, x.total)), { hoje: e.hoje, historico_desde: e.historico_desde }, oculto); };
// Caso 2 do rótulo: média 25.37875, o arredondamento a centavos (round, não floor) dá 25,38.
const pilula = () => folha().queryByTestId("pilula-dia");
// A barra é decorativa (escondida do leitor de tela): sem `includeHiddenElements` o RNTL não a acha e "sem barra" passaria vazio.
const barra = () => folha().queryByTestId("barra-composicao", { includeHiddenElements: true });
const segmentos = () => folha().queryAllByTestId("segmento-composicao", { includeHiddenElements: true }).map((s) => StyleSheet.flatten(s.props.style));
const cor = (no: any) => StyleSheet.flatten(no.props.style).color;

describe("pílula contra a média (PR 3)", () => {
 // 1: hoje 10-02, sem corte. 7: hoje 10-14, corte 10-06, 10-09 tem "0.000", 10-11 tem "0.004" (0 centavos), 10-20 é agendado.
 it.each([
  ["dia anterior ao hoje do /gastos", 1, "2026-10-01", true],
  ["hoje (o dia em curso não compara)", 1, "2026-10-02", false],
  ["agendado", 7, "2026-10-20", false],
  ["total bruto 0.000", 7, "2026-10-09", false],
  ["bruto 0.004 (0 centavos): decide o bruto", 7, "2026-10-11", true],
 ])("%s", async (_n, i, d, aparece) => { await doCaso(i, d); expect(!!pilula()).toBe(aparece); });
 it.each([
  [0, "-4 neutro", "inkMuted", "ArrowDown"], [1, "-5 positivo", "positive", "ArrowDown"], [6, "+15 aviso", "warning", "ArrowUp"], [11, "0% sem seta", "inkMuted", null],
 ] as const)("caso %s da tabela, %s: tom, seta e N%%", async (i, _n, tom, seta) => {
  await doCaso(i, "2026-10-01"); const e = tabela[i]!.saida.dias.find((x) => x.dia === "2026-10-01")!, N = `${Math.abs(e.pct!)}%`;
  expect(tom).toBe(({ gain: "positive", warn: "warning", "": "inkMuted" } as const)[e.tom as "gain"]); // o mapeamento web → app, contra a própria tabela
  const t = within(pilula()!).getByText(N); expect([escuro[tom], claro[tom]]).toContain(cor(t));
  for (const s of ["ArrowDown", "ArrowUp"]) { const ic = within(pilula()!).queryByTestId(`icone-${s}`); if (s === seta) expect(ic!.props.tom).toBe(tom); else expect(ic).toBeNull(); }
 });
 it.each([[1, `5% abaixo da média do mês, ${falado(20)} por dia`], [6, `15% acima da média do mês, ${falado(10000)} por dia`], [11, `na média do mês, ${falado(10000)} por dia`], [2, `259% acima da média do mês, ${falado(2538)} por dia`]])("caso %s: o rótulo fala o sentido e a média", async (i, rotulo) => {
  await doCaso(i, "2026-10-01"); expect(pilula()!.props.accessibilityLabel).toBe(rotulo);
 });
 it("oculto: sem pílula e nenhum rótulo da folha fala média", async () => {
  await doCaso(6, "2026-10-01", true); expect(pilula()).toBeNull(); expect(folha().queryByLabelText(/média/)).toBeNull();
 });
});

const PREFIXO_ICONE = "icone-categoria-"; // o `IconeCategoria` é decorativo (escondido do leitor de tela): o teste o enxerga com `includeHiddenElements`
const coresIcones = () => folha().queryAllByTestId(new RegExp(`^${PREFIXO_ICONE}`), { includeHiddenElements: true }).map((n) => within(n).getByTestId("cor-icone", { includeHiddenElements: true }).props.color);
const icones = () => folha().queryAllByTestId(new RegExp(`^${PREFIXO_ICONE}`), { includeHiddenElements: true }).map((n) => String(n.props.testID).slice(PREFIXO_ICONE.length));
describe("barra de categorias com ícones (PR 3)", () => {
 const DIA = "2026-10-05"; // catálogo do fixture: lazer game-controller, mercado shopping-cart, alimentacao hamburger, transporte bus (posições 0..3)
 const NOMES = ["airplane-tilt", "bank", "car", "pill", "house", "film-slate", "books", "gift", "cake"];
 const cat = (n: number) => Array.from({ length: n }, (_, i) => ({ chave: `c${i}`, nome: `Cat ${i}`, emoji: "🅰", icone: NOMES[i]! }));
 it("0.004 + 0.006 desenham 2 segmentos 40/60 pelo bruto; 0.004 sozinha não desenha barra nem ícones", async () => {
  await abrirDia(DIA, [D(DIA, "0.010", [["alimentacao", "0.006"], ["transporte", "0.004"]])]);
  expect(segmentos().map((x) => x.flexGrow)).toEqual([0.006, 0.004]); expect(icones()).toEqual(["hamburger", "bus"]);
 });
 it("0.004 sozinha: sem barra e sem ícones", async () => {
  await abrirDia(DIA, [D(DIA, "0.004", [["transporte", "0.004"]])]);
  expect(barra()).toBeNull(); expect(icones()).toEqual([]);
 });
 it("fatia <= 0 fica fora da barra, dos ícones e do rótulo; só uma fatia > 0 sobra: sem barra", async () => {
  await abrirDia(DIA, [D(DIA, "10.00", [["lazer", "10.00"], ["mercado", "0.00"], ["alimentacao", "-5.00"]])]);
  expect(barra()).toBeNull(); expect(icones()).toEqual([]);
 });
 it("duas > 0 e uma zero: 2 segmentos, a zero não entra; escalas 90.005 × 1.005 sem erro; cor pela posição no catálogo", async () => {
  await abrirDia(DIA, [D(DIA, "91.010", [["mercado", "90.005"], ["transporte", "0.00"], ["alimentacao", "1.005"]])]);
  const s = segmentos(); expect(s.map((x) => x.flexGrow)).toEqual([90.005, 1.005]);
  expect(s.map((x) => x.backgroundColor)).toEqual([CORES_CATEGORIA[1], CORES_CATEGORIA[2]]); // posição 1 e 2 no catálogo; no índice da fatia seriam 0 e 1
  expect(icones()).toEqual(["shopping-cart", "hamburger"]); expect(folha().queryByText(/\p{Extended_Pictographic}/u)).toBeNull(); // nunca o emoji
  expect(coresIcones()).toEqual(s.map((x) => x.backgroundColor)); // o ícone leva a cor da fatia na barra (sem repassar `cor`, cairia em inkMuted)
  expect(folha().getByLabelText(`Mercado, ${falado(9001)}; Alimentação, ${falado(101)}`)).toBeTruthy(); // nome e valor (centavos) só no rótulo
  expect(folha().queryByText(/Mercado|Alimentação/)).toBeNull();
 });
 it("chave fora do catálogo: cor neutra", async () => {
  await abrirDia(DIA, [D(DIA, "3.00", [["fantasma", "1.00"], ["lazer", "2.00"]])]);
  expect(segmentos().map((x) => x.backgroundColor)).toEqual([CORES_CATEGORIA[6], CORES_CATEGORIA[0]]);
  expect(icones()).toEqual(["tag", "game-controller"]); // ícone neutro da chave sem catálogo
 });
 it("a cor gira no vetor: a 9ª categoria (posição 8) pega CORES_CATEGORIA[8 % length], nunca undefined", async () => {
  await abrirDia(DIA, [D(DIA, "2.00", [["c8", "1.00"], ["c1", "1.00"]])], { categorias: cat(9) });
  expect(segmentos().map((x) => x.backgroundColor)).toEqual([CORES_CATEGORIA[8 % CORES_CATEGORIA.length], CORES_CATEGORIA[1]]);
 });
 it("até 4 ícones e '+N' para o resto; com exatamente 4, sem '+N'", async () => {
  const f = (n: number) => D(DIA, "6.00", Array.from({ length: n }, (_, i) => [`c${i}`, "1.00"] as Par));
  await abrirDia(DIA, [f(6)], { categorias: cat(6) });
  expect(icones()).toEqual(NOMES.slice(0, 4)); expect(folha().getByText("+2")).toBeTruthy();
  expect(folha().getByLabelText(/Cat 5, /)).toBeTruthy(); // o rótulo lista TODAS as fatias
 });
 it("4 fatias: 4 ícones e nenhum '+N'", async () => {
  await abrirDia(DIA, [D(DIA, "4.00", ["c0", "c1", "c2", "c3"].map((c) => [c, "1.00"] as Par))], { categorias: cat(4) });
  expect(icones()).toEqual(NOMES.slice(0, 4)); expect(folha().queryByText(/^\+/)).toBeNull();
 });
 it("oculto: sem barra, ícones nem rótulo de fatias", async () => {
  await abrirDia(DIA, [D(DIA, "91.010", [["mercado", "90.005"], ["alimentacao", "1.005"]])], {}, true);
  expect(barra()).toBeNull(); expect(icones()).toEqual([]); expect(folha().queryByLabelText(/Mercado/)).toBeNull();
 });
 it("fonte ampliada: sem barra, mas os ícones e o rótulo ficam", async () => {
  await abrirDia(DIA, [D(DIA, "91.010", [["mercado", "90.005"], ["alimentacao", "1.005"]])], {}, false, 2);
  expect(barra()).toBeNull(); expect(icones()).toEqual(["shopping-cart", "hamburger"]); expect(folha().getByLabelText(/^Mercado, /)).toBeTruthy();
 });
});

describe("3 maiores (PR 3)", () => {
 const DIA = "2026-10-05";
 it("descrição + valor em centavos (HALF_UP do bruto) na ordem do servidor; descrição só com espaços vira 'Lançamento'", async () => {
  await abrirDia(DIA, [D(DIA, "93.010", [], [["Mercado", "90.005"], ["Café", "1.005"], ["   ", "2.00"]])]);
  expect(folha().getAllByText(/^(Mercado|Café|Lançamento)$/).map((t) => t.props.children)).toEqual(["Mercado", "Café", "Lançamento"]);
  for (const c of [9001, 101]) { expect(folha().getByLabelText(faladoComSinal(c))).toBeTruthy(); expect(folha().queryByLabelText(faladoComSinal(c, "saida"))).toBeNull(); } // VoiceOver não fala "menos"
  expect(folha().getByText(/^R\$\s*90,01$/)).toBeTruthy(); expect(folha().getByText(/^R\$\s*1,01$/)).toBeTruthy(); // valor certo no texto visível
  expect(folha().queryByText(/−/)).toBeNull(); // sem o sinal de menos (U+2212) em nenhum valor da folha
 });
 it("oculto: 'Lançamento' no lugar das descrições e valores mascarados", async () => {
  await abrirDia(DIA, [D(DIA, "91.010", [], [["Mercado", "90.005"], ["Café", "1.005"]])], {}, true);
  expect(folha().queryByText("Mercado")).toBeNull(); expect(folha().getAllByText("Lançamento")).toHaveLength(2); expect(folha().queryByLabelText(faladoComSinal(9001))).toBeNull();
  expect(folha().getAllByLabelText("Valor oculto").length).toBeGreaterThanOrEqual(3); // total + 2 maiores
 });
 it("dia sem saída (estado do 2b) não ganha pílula, barra nem maiores", async () => {
  detalhes = { ...F[DET], dias: DIAS }; // 10-04 só tem entrada (na grade, ausente de /gastos.dias)
  await abrir(); await tocar("2026-10-04"); await folhaAberta("2026-10-04"); await pronta();
  expect(pilula()).toBeNull(); expect(barra()).toBeNull(); expect(folha().queryByText("Lançamento")).toBeNull();
 });
});

describe("troca de dia pela seta (PR 3)", () => {
 it("do dia 05 ao 09: pílula, barra, ícones e maiores são os do dia NOVO, e os do 05 somem", async () => {
  await abrirDia("2026-10-05", [D("2026-10-05", "91.010", [["mercado", "90.005"], ["alimentacao", "1.005"]], [["Mercado", "90.005"], ["Café", "1.005"]]), D("2026-10-09", "0.010", [["alimentacao", "0.006"], ["transporte", "0.004"]], [["Bala", "0.006"], ["Passe", "0.004"]])]);
  expect(within(pilula()!).getByText("1300%")).toBeTruthy(); expect(segmentos().map((x) => x.flexGrow)).toEqual([90.005, 1.005]); expect(folha().getByText("Mercado")).toBeTruthy();
  for (let i = 0; i < 4; i++) await apertar("Dia seguinte");
  await folhaAberta("2026-10-09");
  expect(within(pilula()!).getByText("100%")).toBeTruthy(); expect(within(pilula()!).getByTestId("icone-ArrowDown").props.tom).toBe("positive");
  expect(segmentos().map((x) => x.flexGrow)).toEqual([0.006, 0.004]);
  expect(icones()).toEqual(["hamburger", "bus"]);
  expect(folha().getAllByText(/^(Mercado|Café|Bala|Passe)$/).map((t) => t.props.children)).toEqual(["Bala", "Passe"]);
  expect(folha().getAllByLabelText(faladoComSinal(1))).toHaveLength(2); // total (0.010) + a maior "Bala" (0.006): 1 centavo (HALF_UP do bruto)
 });
});
