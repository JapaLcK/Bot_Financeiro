// O painel herda o tema do provider da raiz; o card é lido no tema escuro (o chip também no claro, onde o teste troca `mockEsquema`).
let mockEsquema = "dark";
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({ __esModule: true, default: () => mockEsquema }));
// O ícone é um stub sem identidade no Jest; aqui ele expõe o nome (`icone-ArrowDown`) para provar a seta do chip.
// eslint-disable-next-line @typescript-eslint/no-require-imports -- a fábrica do jest.mock não pode importar módulos de fora (hoisting).
jest.mock("@/ui/componentes/Icone", () => ({ Icone: ({ nome }: { nome: string }) => require("react").createElement(require("react-native").View, { testID: `icone-${nome}` }) }));
// "Hoje" fixo: o fixture é de 2026-10 (dias 1 a 6) e o CI roda em qualquer data.
let mockHoje = "2026-10-06";
jest.mock("@/features/painel/catalogo", () => ({ ...jest.requireActual("@/features/painel/catalogo"), hojeSP: () => mockHoje }));

import { render } from "@testing-library/react-native";
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { Curva } from "@/features/painel/base";
import { TemaProvider } from "@/ui/tema";
import { StyleSheet } from "react-native";
import { mesAtual, mesesAnteriores, nomeMes } from "@/features/painel/catalogo";
import { guardarCredenciais } from "@/storage/secure";
import { claro, escuro } from "@/ui/tokens";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";

jest.setTimeout(20_000); // monta o painel inteiro pelo router: no CI carregado o 1º teste passa dos 5 s padrão (igual a painel_seletores/painel_conversa)

const F = fixture as Record<string, any>, DET = "/api/app/mes-detalhes", RES = "/api/app/resumo-do-mes";
const H = { includeHiddenElements: true }; // a curva é oculta ao VoiceOver de propósito
// O provider pede o mês anterior ao selecionado numa 3ª chamada de mes-detalhes; o servidor falso casa só o caminho, então o mês passado é escolhido pela query.
const ANTERIOR = mesesAnteriores(mesAtual(), 2)[1]!;
const dia = (d: string, saiu: string) => ({ dia: d, entrou: "0.00", saiu });
// Setembro: acumulado 1000, 1700, 2110, 2110, 3110, 3110 (dia 6) e 4110,00 no mês inteiro (= `anterior.saiu` do resumo).
const SETEMBRO = { ...F[DET], mes: "2026-09", ate: "2026-09-30", saiu: "4110.00", motivos: [], dias: [dia("2026-09-01", "1000.00"), dia("2026-09-02", "700.00"), dia("2026-09-03", "410.00"), dia("2026-09-05", "1000.00"), dia("2026-09-20", "1000.00")] };
let setembro: any;
beforeEach(async () => {
 mockHoje = "2026-10-06"; mockEsquema = "dark"; setembro = SETEMBRO;
 prepararCaso(); desligarTrava(); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
 fetchFalso.mockImplementation(async (url: string) => {
  const u = new URL(url), path = u.pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === DET && u.searchParams.get("mes") === ANTERIOR) { if (setembro instanceof Error) throw setembro; return typeof setembro === "number" ? resposta(setembro, {}) : resposta(200, setembro); }
  return resposta(200, F[path] ?? {});
 });
});
// Troca o corpo de um recurso do fixture (`fn` recebe o original); a chamada do mês passado não é afetada.
function com(path: string, fn: (o: any) => any) {
 const base = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string) => { const u = new URL(url); return u.pathname === path && !(path === DET && u.searchParams.get("mes") === ANTERIOR) ? resposta(200, fn(F[path])) : base(url); });
}
const anterior = (saiu: string, dias: any[]) => { com(RES, (o) => ({ ...o, anterior: { ...o.anterior, saiu } })); setembro = { ...SETEMBRO, saiu, dias }; };
const medir = () => act(async () => { fireEvent(screen.getByTestId("curva-plot", H), "layout", { nativeEvent: { layout: { width: 300, height: 130 } } }); });
const carta = () => within(screen.getByTestId("bloco-resumo"));
async function abrir() {
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByTestId("bloco-resumo")).toBeTruthy());
 await waitFor(() => expect(carta().queryByLabelText("Carregando dados")).toBeNull());
}
const LEGENDA_COMPRAS = "Compras pela data em que foram feitas"; // a curva filtra pela data da compra; o Saiu do resumo inclui fatura de cartão
const SUMIDOS = ["Guardado líquido", "Entrou", "Saiu", "Saldo do mês", "Ver lançamentos", "Compras feitas no mês"];

it("(a) as duas curvas: número grande = diferença no MESMO dia, 'abaixo', chip com %, 'vs R$ ... mês anterior', eixos e legenda; nada do card antigo", async () => {
 await abrir(); await medir();
 expect(carta().getByLabelText("729 reais e 50 centavos")).toBeTruthy(); // 3.110,00 (setembro até o dia 6) - 2.380,50
 expect(StyleSheet.flatten(carta().getByText("abaixo").props.style).color).toBe(escuro.positive);
 expect(carta().getByText("-23,5%")).toBeTruthy();
 expect(StyleSheet.flatten(carta().getByText("-23,5%").props.style).color).toBe(escuro.positive);
 expect(carta().getByText("vs")).toBeTruthy(); expect(carta().getByLabelText("3110 reais")).toBeTruthy(); expect(carta().getByText("mês anterior")).toBeTruthy();
 expect(carta().getByTestId("curva-anterior", H)).toBeTruthy(); expect(carta().getByTestId("ponto-hoje", H)).toBeTruthy(); // 06/10 é "hoje"
 expect(StyleSheet.flatten(carta().getByTestId("ponto-hoje-centro", H).props.style).backgroundColor).toBe(escuro.positive);
 expect(carta().getAllByTestId("curva-rotulo-y", H).map((t) => t.props.children)).toEqual(["R$ 0", "R$ 2k", "R$ 4k", "R$ 6k"]); // 4.110 (mês passado inteiro) manda no eixo
 expect(carta().getAllByTestId("curva-rotulo-x", H).map((t) => t.props.children)).toEqual([1, 15, 25, 31]);
 expect(carta().getAllByTestId("curva-legenda", H)).toHaveLength(2); expect(carta().getByText("Este mês", H)).toBeTruthy(); expect(carta().getByText("Mês passado", H)).toBeTruthy();
 expect(carta().getByText(LEGENDA_COMPRAS)).toBeTruthy();
 for (const t of SUMIDOS) expect(carta().queryByText(t)).toBeNull();
 expect(screen.queryByRole("button", { name: "Ver lançamentos" })).toBeNull();
 expect(screen.queryByRole("button", { name: "Abrir Resumo do mês" })).toBeNull();
 expect(screen.getByRole("button", { name: "Abrir Contas" })).toBeTruthy(); // os outros widgets seguem como estavam
 expect(fetchFalso.mock.calls.map(([u]) => String(u)).some((u) => u.includes(`${DET}?mes=${ANTERIOR}`))).toBe(true);
});
it("(a) mês fechado: compara os totais finais (4.110,00 × 2.380,50), não o acumulado do mês passado no dia de hoje", async () => {
 mockHoje = "2026-11-15";
 await abrir(); await medir();
 expect(carta().getByLabelText("1729 reais e 50 centavos")).toBeTruthy(); expect(carta().getByText("-42,1%")).toBeTruthy(); expect(carta().getByLabelText("4110 reais")).toBeTruthy();
 expect(carta().queryByTestId("ponto-hoje", H)).toBeNull();
});
it("acima: gastou mais que o mês passado no mesmo dia => 'acima', âmbar, + no chip", async () => {
 anterior("1000.00", [dia("2026-09-01", "500.00"), dia("2026-09-03", "500.00")]);
 await abrir();
 expect(carta().getByLabelText("1380 reais e 50 centavos")).toBeTruthy(); // 2.380,50 - 1.000,00
 expect(StyleSheet.flatten(carta().getByText("acima").props.style).color).toBe(escuro.warning);
 expect(StyleSheet.flatten(carta().getByText("+138,1%").props.style).color).toBe(escuro.warning);
});
it("igual: mesmo acumulado => 'igual', 0,0% e sem seta", async () => {
 anterior("2380.50", [dia("2026-09-01", "900.00"), dia("2026-09-02", "410.00"), dia("2026-09-03", "240.00"), dia("2026-09-04", "300.00"), dia("2026-09-05", "250.50"), dia("2026-09-06", "280.00")]);
 await abrir();
 expect(carta().getByLabelText("zero reais")).toBeTruthy(); expect(carta().getByText("igual")).toBeTruthy(); expect(carta().getByText("0,0%")).toBeTruthy();
});
it("mês passado sem gasto até o dia (base 0): 'acima' sem chip de %, mas com 'vs R$ 0,00 mês anterior'", async () => {
 anterior("500.00", [dia("2026-09-10", "250.00"), dia("2026-09-11", "250.00")]);
 await abrir();
 expect(carta().getByText("acima")).toBeTruthy(); expect(carta().queryByText(/%$/)).toBeNull(); expect(carta().getByText("vs")).toBeTruthy(); expect(carta().getByLabelText("zero reais")).toBeTruthy();
});
it.each([
 ["o resumo não traz mês anterior", () => com(RES, (o) => ({ ...o, anterior: null }))],
 ["o detalhe do mês passado falhou (500)", () => { setembro = 500; }],
 ["o detalhe do mês passado é de outro mês", () => { setembro = { ...SETEMBRO, mes: "2026-08" }; }],
 ["o detalhe do mês passado fecha 1 centavo a menos que o resumo", () => { setembro = { ...SETEMBRO, saiu: "4109.99" }; }],
 ["o plano cortou o histórico dentro do mês passado (inicio_do_historico)", () => { setembro = { ...SETEMBRO, motivos: ["inicio_do_historico"] }; }],
 ["o detalhe do mês passado deu 429", () => { setembro = 429; }],
 ["o detalhe do mês passado deu erro de rede", () => { setembro = new TypeError("Network request failed"); }],
 ["o detalhe do mês passado veio com schema inválido", () => { setembro = { mes: "2026-09" }; }],
])("(b) só a curva deste mês, sem chip, sem tracejada e sem legenda do mês passado, sem travar e sem mostrar erro: %s", async (_n, quebrar) => {
 quebrar();
 await abrir(); await medir();
 expect(carta().getByLabelText("2380 reais e 50 centavos")).toBeTruthy(); expect(carta().getByText("gasto até hoje")).toBeTruthy();
 expect(carta().getByTestId("curva-linha", H)).toBeTruthy();
 expect(carta().queryByTestId("curva-anterior", H)).toBeNull(); expect(carta().queryByText("vs")).toBeNull(); expect(carta().queryByText(/%$/)).toBeNull();
 expect(carta().queryByText("Mês passado", H)).toBeNull(); expect(carta().getByText("Este mês", H)).toBeTruthy();
 expect(carta().queryByText("abaixo")).toBeNull(); expect(carta().queryByText("acima")).toBeNull();
 expect(carta().getByText(LEGENDA_COMPRAS)).toBeTruthy();
 expect(screen.queryByText(/limite foi alcançado|Não conseguimos|incompatíveis/)).toBeNull(); // o recurso do mês passado é só enfeite do card: falha dele não vira erro na tela
});
it("(b) mês FECHADO sem o mês passado: o rótulo é 'gasto no mês' (não 'até hoje') e o ponto de hoje não existe", async () => {
 mockHoje = "2026-11-15"; setembro = 500;
 await abrir();
 expect(carta().getByText("gasto no mês")).toBeTruthy(); expect(carta().queryByText("gasto até hoje")).toBeNull(); expect(carta().queryByTestId("ponto-hoje", H)).toBeNull();
});
it("402 no detalhe do mês passado derruba o portão como os outros recursos (comportamento atual): o painel sai da tela", async () => {
 setembro = 402;
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(fetchFalso.mock.calls.some(([u]) => String(u).includes(`${DET}?mes=${ANTERIOR}`))).toBe(true));
 for (let i = 0; i < 20; i++) await act(drenar);
 expect(screen.queryByTestId("bloco-resumo")).toBeNull();
});
it("enquanto o detalhe do mês passado carrega o card só gira o spinner (não pisca no estado (b) antes do (a)); chegando, vira (a)", async () => {
 const atraso = segurar(), base = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => { const u = new URL(url); if (u.pathname === DET && u.searchParams.get("mes") === ANTERIOR) await atraso.promessa; return base(url, req); });
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByTestId("bloco-resumo")).toBeTruthy());
 await waitFor(() => expect(screen.getAllByText("Alimentação").length).toBeGreaterThan(0)); // o card "Para onde vai" usa o MESMO `detalhes`: ele já chegou, só o do mês passado está preso
 for (let i = 0; i < 20; i++) await act(drenar);
 expect(carta().getByLabelText("Carregando dados")).toBeTruthy(); expect(carta().queryByText("gasto até hoje")).toBeNull(); expect(carta().queryByTestId("curva-linha", H)).toBeNull(); expect(carta().queryByText("Saiu no mês")).toBeNull();
 atraso.soltar(); for (let i = 0; i < 20; i++) await act(drenar);
 await waitFor(() => expect(carta().getByText("abaixo")).toBeTruthy()); expect(carta().queryByLabelText("Carregando dados")).toBeNull();
});
it("ao trocar de mês, com o detalhe do mês passado novo em voo (fase 'carregando'), o card gira o spinner em vez de cair em (b)/(c); chegando, o spinner sai", async () => {
 await abrir();
 const meses = mesesAnteriores(mesAtual()), atraso = segurar(), base = fetchFalso.getMockImplementation()!;
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => { const u = new URL(url); if (u.pathname === DET && u.searchParams.get("mes") === mesesAnteriores(meses[1]!, 2)[1]) await atraso.promessa; return base(url, req); });
 for (const nome of [nomeMes(meses[0]!).slice(0, 3), nomeMes(meses[1]!)]) await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); // escolhe o mês anterior no seletor
 await waitFor(() => expect(screen.getAllByText("Alimentação").length).toBeGreaterThan(0)); // "Para onde vai" usa o mesmo `detalhes` do mês novo: já chegou, só o do mês passado está preso
 for (let i = 0; i < 20; i++) await act(drenar);
 expect(carta().getByLabelText("Carregando dados")).toBeTruthy(); expect(carta().queryByText("Saiu no mês")).toBeNull(); expect(carta().queryByText("gasto até hoje")).toBeNull();
 atraso.soltar(); for (let i = 0; i < 20; i++) await act(drenar);
 await waitFor(() => expect(carta().queryByLabelText("Carregando dados")).toBeNull());
});
const IGUAL = [dia("2026-09-01", "900.00"), dia("2026-09-02", "410.00"), dia("2026-09-03", "240.00"), dia("2026-09-04", "300.00"), dia("2026-09-05", "250.50"), dia("2026-09-06", "280.00")];
// abaixo = fixture; acima e igual trocam o mês passado (mesmos dados dos testes acima).
const SENTIDOS = [
 ["positive", "abaixo", "ArrowDown", () => {}],
 ["warning", "acima", "ArrowUp", () => anterior("1000.00", [dia("2026-09-01", "500.00"), dia("2026-09-03", "500.00")])],
 ["ink", "igual", null, () => anterior("2380.50", IGUAL)],
] as const;
it.each(SENTIDOS.flatMap(([tom, sentido, seta, preparar]) => [["escuro", "24", tom, sentido, seta, preparar, escuro], ["claro", "0f", tom, sentido, seta, preparar, claro]] as const))("chip no tema %s (alfa %s, hex de 8 dígitos, não sólido), tom %s, '%s': seta %s", async (_t, alfa, tom, sentido, seta, preparar, paleta) => {
 mockEsquema = paleta === claro ? "light" : "dark"; preparar();
 await abrir();
 expect(carta().getByText(sentido)).toBeTruthy();
 expect(StyleSheet.flatten(carta().getByTestId("chip-pct").props.style).backgroundColor).toBe(`${paleta[tom]}${alfa}`);
 expect(carta().queryAllByTestId(/^icone-Arrow/).map((i) => i.props.testID)).toEqual(seta ? [`icone-${seta}`] : []); // igual: sem seta
});
it("valores ocultos HOJE: o % do chip continua visível; só o número grande e o valor do 'vs' são mascarados", async () => {
 await abrir();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); });
 expect(carta().getByText("-23,5%")).toBeTruthy(); expect(carta().getByText("abaixo")).toBeTruthy(); expect(carta().getByText("vs")).toBeTruthy();
 expect(carta().getAllByLabelText("Valor oculto")).toHaveLength(2); // grande + vs
});
it.each([
 ["a compra de setembro não está no Saiu do card", RES, (o: any) => o, DET, (o: any) => ({ ...o, dias: [dia("2026-09-29", "100.00"), ...o.dias] })],
 ["o detalhe fecha 1 centavo a menos que o resumo", RES, (o: any) => o, DET, (o: any) => ({ ...o, saiu: "2380.49", dias: o.dias.map((x: any, i: number) => i === 5 ? { ...x, saiu: "279.99" } : x) })],
 ["a soma dos dias não fecha com o Saiu (outra moeda no total legado)", RES, (o: any) => o, DET, (o: any) => ({ ...o, dias: o.dias.slice(0, 5) })],
 ["o detalhe é de outro mês", RES, (o: any) => o, DET, (o: any) => ({ ...o, mes: "2026-09" })],
 ["o plano cortou o histórico dentro do mês (inicio_do_historico)", RES, (o: any) => o, DET, (o: any) => ({ ...o, motivos: [...o.motivos, "inicio_do_historico"] })],
 ["tem lançamento depois de hoje", RES, (o: any) => o, DET, (o: any) => ({ ...o, dias: o.dias.slice(0, 5).concat(dia("2026-10-09", "280.00")) })],
])("(c) nenhuma curva válida: só 'Saiu no mês' com o valor do resumo (fallback honesto), sem gráfico: %s", async (_n, p1, f1, p2, f2) => {
 com(p1, f1); com(p2, f2);
 await abrir();
 expect(carta().getByText("Saiu no mês")).toBeTruthy(); expect(carta().getByLabelText("menos 2380 reais e 50 centavos")).toBeTruthy();
 expect(screen.queryByTestId("curva-acumulada", H)).toBeNull(); expect(carta().queryByText("vs")).toBeNull(); expect(carta().queryByText("Este mês", H)).toBeNull();
 expect(carta().queryByText(LEGENDA_COMPRAS)).toBeNull();
});
it("(c) o aviso de histórico cortado explica a ausência do gráfico (Avisos dentro do card)", async () => {
 com(DET, (o) => ({ ...o, motivos: ["inicio_do_historico"] }));
 await abrir();
 expect(carta().getByRole("button", { name: "Ver ressalvas" })).toBeTruthy();
});
it("valores ocultos: número grande, valor do chip, curva, eixos e legenda somem; 'Gráfico oculto' no lugar", async () => {
 await abrir(); await medir();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); });
 expect(carta().queryByLabelText(/reais/)).toBeNull(); expect(carta().getAllByLabelText("Valor oculto").length).toBeGreaterThanOrEqual(2); // grande + vs
 expect(screen.queryByTestId("curva-acumulada", H)).toBeNull(); expect(carta().queryByText("Mês passado", H)).toBeNull(); expect(carta().queryByTestId("curva-rotulo-y", H)).toBeNull();
 expect(carta().getByText("Gráfico oculto")).toBeTruthy(); expect(carta().queryByText(LEGENDA_COMPRAS)).toBeNull();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Mostrar valores" })); });
 expect(screen.getByTestId("curva-acumulada", H)).toBeTruthy(); expect(carta().getByLabelText("729 reais e 50 centavos")).toBeTruthy();
});
it("o card inteiro leva à aba Gastos: tocar no número (fora do título) navega; o título é o botão do VoiceOver", async () => {
 await abrir();
 expect(screen.getByRole("button", { name: "Resumo do mês, abrir Gastos" })).toBeTruthy();
 expect(screen.getByTestId("bloco-resumo").props.accessible).toBe(false); // o toque do card inteiro não vira um botão único que engole os textos e o Avisos para o VoiceOver
 await act(async () => { fireEvent.press(carta().getByLabelText("729 reais e 50 centavos")); });
 expect(screen).toHavePathname("/gastos");
});
// Pressability de verdade (sem mock): o card é o responder desde o início do toque.
const dedo = { persist() {}, currentTarget: 1, nativeEvent: { pageX: 100, pageY: 100, locationX: 100, locationY: 20, timestamp: 0, touches: [{ pageX: 100, pageY: 100, identifier: 1 }], changedTouches: [{ pageX: 100, pageY: 100, identifier: 1 }] } };
const espera = (ms: number) => act(async () => { jest.advanceTimersByTime(ms); }); // o `renderRouter` liga o relógio falso
it("toque curto no card (sem nenhum evento no gráfico) navega ao soltar", async () => {
 await abrir(); await medir();
 await act(async () => { fireEvent(screen.getByTestId("bloco-resumo"), "responderGrant", dedo); });
 await act(async () => { fireEvent(screen.getByTestId("bloco-resumo"), "responderRelease", dedo); });
 expect(screen).toHavePathname("/gastos");
});
const card = () => screen.getByTestId("bloco-resumo");
const tocar = async (alvo: () => any) => { await act(async () => { fireEvent(alvo(), "responderGrant", dedo); }); await act(async () => { fireEvent(alvo(), "responderRelease", dedo); }); };
it("toque curto QUE PASSA PELO GRÁFICO (touchStart/touchEnd em < 250ms) navega ao soltar: o aviso do gráfico só vale depois que a bolha abre", async () => {
 await abrir(); await medir();
 const gesto = screen.getByTestId("curva-scrub", H);
 await act(async () => { fireEvent(card(), "responderGrant", dedo); fireEvent(gesto, "touchStart", dedo); });
 await espera(100);
 expect(carta().queryByTestId("curva-bolha", H)).toBeNull();
 await act(async () => { fireEvent(gesto, "touchEnd", dedo); fireEvent(card(), "responderRelease", dedo); });
 expect(screen).toHavePathname("/gastos");
});
it("segurar o card 400ms SEM nada no gráfico e soltar NAVEGA (toque lento não é scrub)", async () => {
 await abrir(); await medir();
 await act(async () => { fireEvent(card(), "responderGrant", dedo); });
 await espera(400);
 await act(async () => { fireEvent(card(), "responderRelease", dedo); });
 expect(screen).toHavePathname("/gastos");
});
it("segurar no gráfico 255ms (bolha aberta) e soltar NÃO navega, e fecha a bolha", async () => {
 await abrir(); await medir();
 const gesto = screen.getByTestId("curva-scrub", H);
 await act(async () => { fireEvent(card(), "responderGrant", dedo); fireEvent(gesto, "touchStart", dedo); });
 await espera(255);
 expect(carta().getByTestId("curva-bolha", H)).toBeTruthy();
 await act(async () => { fireEvent(card(), "responderRelease", dedo); fireEvent(gesto, "touchEnd", dedo); });
 expect(screen).toHavePathname("/resumo");
 expect(carta().queryByTestId("curva-bolha", H)).toBeNull();
});
it.each([["o card", () => screen.getByTestId("bloco-resumo")], ["o título", () => screen.getByRole("button", { name: "Resumo do mês, abrir Gastos" })]] as const)("scrub cancelado (rolagem: touchCancel e o card nem recebe o soltar) não deixa o aviso preso: o toque seguinte em %s navega", async (_n, alvo) => {
 await abrir(); await medir();
 const gesto = screen.getByTestId("curva-scrub", H);
 await act(async () => { fireEvent(card(), "responderGrant", dedo); fireEvent(gesto, "touchStart", dedo); });
 await espera(255);
 expect(carta().getByTestId("curva-bolha", H)).toBeTruthy();
 await act(async () => { fireEvent(gesto, "touchCancel", dedo); fireEvent(card(), "responderTerminate", dedo); });
 expect(screen).toHavePathname("/resumo");
 await tocar(alvo);
 expect(screen).toHavePathname("/gastos");
});
it("o gráfico do card mostra o dia segurado com o valor da MESMA série desenhada (dia 6 = hoje: R$ 2.380,50 este mês, R$ 3.110,00 o mês passado)", async () => {
 await abrir(); await medir();
 const gesto = screen.getByTestId("curva-scrub", H);
 await act(async () => { fireEvent(gesto, "touchStart", { nativeEvent: { locationX: 290, pageX: 290, pageY: 20 } }); });
 await espera(300);
 expect(carta().getByText("dia 6 · R$ 2.380,50", H)).toBeTruthy(); expect(carta().getByText("mês passado R$ 3.110,00", H)).toBeTruthy();
 // O card segue sendo o responder durante o arrasto (o gráfico não o disputa): só os eventos de toque do gráfico trocam o dia (x=12 = dia 1, R$ 900,00).
 await act(async () => { fireEvent(gesto, "touchMove", { nativeEvent: { locationX: 12, pageX: 12, pageY: 20 } }); });
 expect(carta().getByText("dia 1 · R$ 900,00", H)).toBeTruthy();
});
it("tocar no título também abre Gastos", async () => {
 await abrir();
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Resumo do mês, abrir Gastos" })); });
 expect(screen).toHavePathname("/gastos");
});
it("o ícone de ressalvas (Avisos) abre a explicação e NÃO navega", async () => {
 com(RES, (o) => ({ ...o, motivos: ["arredondamento_por_grupo"] }));
 await abrir();
 await act(async () => { fireEvent.press(carta().getByRole("button", { name: "Ver ressalvas" })); });
 expect(carta().getByText(/arredondados/)).toBeTruthy();
 expect(screen).toHavePathname("/resumo");
});

describe("Curva com `outra` (o simulador de compra: sem × com a compra, na MESMA escala)", () => {
 const paths = (r: ReturnType<typeof render>) => r.UNSAFE_root.findAll((n) => String(n.type) === "RNSVGPath");
 const d = (r: ReturnType<typeof render>, i: number) => paths(r)[i]!.props.d as string;
 const ver = (valores: (number | null)[], outra?: (number | null)[]) => render(<TemaProvider><Curva valores={valores} outra={outra} /></TemaProvider>);
 it("sem `outra`: um traço só, como sempre", () => { const r = ver([1, 2, 3]); expect(paths(r)).toHaveLength(1); expect(d(r, 0)).toBe("M0,100 L150,57.5 L300,15"); });
 it("com `outra`: dois traços (o tracejado primeiro), escala comum: o mesmo valor cai na mesma altura nos dois", () => {
  const r = ver([0, 10], [0, 20]); expect(paths(r)).toHaveLength(2);
  expect(d(r, 0)).toBe("M0,100 L300,15"); expect(d(r, 1)).toBe("M0,100 L300,57.5"); // 20 é o teto dos dois: 10 fica na metade
  expect(paths(r)[0]!.props.strokeDasharray).toEqual(["6", "4"]); expect(paths(r)[1]!.props.strokeDasharray).toBeUndefined();
 });
 it.each([["null na outra", [1, 2], [1, null]], ["null na principal", [1, null], [1, 2]], ["tamanhos diferentes", [1, 2], [1, 2, 3]]] as const)("%s: 'Trajetória indisponível.' e nenhum traço", (_, a, b) => {
  const r = ver([...a], [...b]); expect(paths(r)).toHaveLength(0); expect(r.getByText("Trajetória indisponível.")).toBeTruthy();
 });
});
