import { act, fireEvent, screen, waitFor } from "expo-router/testing-library";
import { AccessibilityInfo, ScrollView, StyleSheet } from "react-native";
import { prepararCaso } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { abrir, enviar, fim, noResumo, reiniciar, respostaFluxo, srv, type Fluxo } from "./conversa_apoio";
// Apresentação do chat: status, passos, prévia (Skeleton), markdown mínimo, entradas animadas e "voltar ao fim".
jest.setTimeout(20_000);

let f: Fluxo | undefined;
const definirReduzir = (v: boolean) => (global as unknown as { __definirReduzirMovimento: (v: boolean) => void }).__definirReduzirMovimento(v);
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); f = undefined; srv.chat = () => (f = respostaFluxo()); definirReduzir(false); });
afterEach(() => { jest.restoreAllMocks(); jest.useRealTimers(); });
const empurrar = async (...evs: object[]) => { await waitFor(() => expect(f).toBeDefined()); await act(async () => { evs.forEach((e) => f!.empurrar(e)); await drenar(); }); };
const passo = (texto: string, previa: string | null = null) => ({ tipo: "passo", texto, previa });
const delta = (texto: string) => ({ tipo: "delta", texto });
const status = () => screen.queryByLabelText("Piggy está respondendo");
// Depois do useFakeTimers: espia o intervalo de 2 s da troca de frase e o clearInterval.
const espiarIntervalo = () => [jest.spyOn(global, "setInterval"), jest.spyOn(global, "clearInterval")] as const;
const idDoIntervalo = (set: jest.SpyInstance) => set.mock.results[set.mock.calls.findIndex((c) => c[1] === 2000)]!.value;
const fluxoPronto = (reply: string, extra: object = {}) => () => { const g = respostaFluxo(); g.empurrar(fim(reply, extra)); g.fechar(); return g; };

it("status: frase divertida antes do 1º passo (troca a cada 2 s, rótulo fixo), depois só o último passo, e some no 1º delta", async () => {
  jest.spyOn(Math, "random").mockReturnValue(0);
  await abrir(); jest.useFakeTimers(); const [intervalo, limpar] = espiarIntervalo(); await enviar("oi"); await waitFor(() => expect(status()).toBeTruthy());
  expect(screen.getByText("Contando as moedinhas…")).toBeTruthy();
  await act(async () => { jest.advanceTimersByTime(2000); });
  expect(screen.getByText("Abrindo o cofrinho…")).toBeTruthy(); expect(screen.queryByText("Contando as moedinhas…")).toBeNull();
  expect(status()!.props.accessibilityLabel).toBe("Piggy está respondendo");
  const anuncios = () => jest.mocked(AccessibilityInfo.announceForAccessibility).mock.calls.map((c) => c[0]);
  expect(anuncios()).toEqual([]); // a frase divertida nunca é anunciada
  await empurrar(passo("Resumindo o período")); expect(screen.getByText("Resumindo o período")).toBeTruthy(); expect(screen.queryByText("Abrindo o cofrinho…")).toBeNull();
  expect(screen.getByLabelText("Piggy está respondendo: Resumindo o período")).toBeTruthy(); expect(status()).toBeNull();
  expect(limpar).toHaveBeenCalledWith(idDoIntervalo(intervalo)); // a troca de frase parou
  await empurrar(passo("Montando o card")); expect(screen.getByText("Montando o card")).toBeTruthy(); expect(screen.queryByText("Resumindo o período")).toBeNull();
  expect(anuncios()).toEqual(["Resumindo o período", "Montando o card"]); // iOS: cada passo real, uma vez
  await empurrar(delta("Vou ver")); expect(status()).toBeNull();
});
it("passo que chega com outra aba à vista não é anunciado (o turno em voo segue montado)", async () => {
  jest.mocked(AccessibilityInfo.announceForAccessibility).mockClear(); // a espiã é global: o caso anterior anunciou o mesmo passo
  await abrir(); await enviar("oi"); await waitFor(() => expect(status()).toBeTruthy()); await noResumo();
  await empurrar(passo("Resumindo o período"));
  expect(jest.mocked(AccessibilityInfo.announceForAccessibility).mock.calls.map((c) => c[0])).not.toContain("Resumindo o período");
});
it("sair da tela com o status à mostra limpa o timer: nada dispara depois", async () => {
  const erro = jest.spyOn(console, "error").mockImplementation(() => undefined);
  await abrir(); jest.useFakeTimers(); const [intervalo, limpar] = espiarIntervalo(); await enviar("oi"); await waitFor(() => expect(status()).toBeTruthy());
  expect(limpar).not.toHaveBeenCalled(); screen.unmount(); expect(limpar).toHaveBeenCalledWith(idDoIntervalo(intervalo));
  await act(async () => { jest.advanceTimersByTime(10_000); }); expect(erro).not.toHaveBeenCalled();
});
it("passos ficam numa linha acima do texto enquanto a resposta chega, sem repetição e em ordem; com a resposta pronta, a linha some", async () => {
  const LEGENDA = "Montando o card · Comparando outubro com setembro";
  await abrir(); await enviar("oi"); await empurrar(passo("Montando o card"), passo("Comparando outubro com setembro"), passo("Montando o card"), delta("Texto parcial"));
  expect(screen.getByText(LEGENDA)).toBeTruthy(); expect(status()).toBeNull(); expect(screen.getByText("Texto parcial")).toBeTruthy();
  await empurrar(fim("Pronto")); expect(screen.getByText("Pronto")).toBeTruthy(); expect(screen.queryByText(LEGENDA)).toBeNull(); // controle: sem o `resposta === undefined` do TurnoChat a legenda fica grudada
});
it("em voo o passo aparece uma vez só; no erro a linha fica; turno sem passos não ganha linha", async () => {
  await abrir(); await enviar("oi"); await empurrar(passo("Vendo")); expect(screen.getAllByText("Vendo")).toHaveLength(1);
  await empurrar(delta("meio"), { tipo: "erro", mensagem: "x" }); expect(screen.getAllByText("Vendo")).toHaveLength(1); expect(status()).toBeNull();
  srv.chat = fluxoPronto("Sem passos"); await enviar("outra"); await waitFor(() => expect(screen.getByText("Sem passos")).toBeTruthy());
  expect(screen.getAllByText("Vendo")).toHaveLength(1); // só o turno antigo
});
it("prévia mostra o Skeleton até o bloco do mesmo card, mesmo com texto já chegando; sem prévia, sem Skeleton", async () => {
  await abrir(); await enviar("oi"); await empurrar(passo("Vendo", null)); expect(screen.queryByLabelText("Carregando")).toBeNull();
  await empurrar(passo("Montando o card", "categorias")); expect(screen.getByLabelText("Carregando")).toBeTruthy();
  expect(StyleSheet.flatten(screen.getByTestId("fala-piggy").props.style).width).toBe("100%"); // medido no simulador: sem largura cheia o Skeleton "100%" colapsa a 0
  await empurrar(delta("Olha só")); expect(screen.getByLabelText("Carregando")).toBeTruthy();
  await empurrar({ tipo: "bloco", bloco: { card: "categorias", abas: [{ mes: null, recursos: {} }] } }); expect(screen.queryByLabelText("Carregando")).toBeNull();
});
it.each([
  ["Oi\n\nFim", ["Oi", "Fim"], 0], ["- item *um*\n• dois", ["item ", "um", "dois"], 2], ["-5 reais", ["-5 reais"], 0],
])("markdown: %j", async (reply, textos, bullets) => {
  srv.chat = fluxoPronto(reply); await abrir(); await enviar("oi"); await waitFor(() => expect(screen.getAllByText(textos[0]!, { exact: false }).length).toBeGreaterThan(0));
  for (const t of textos) expect(screen.getAllByText(t, { exact: false }).length).toBeGreaterThan(0);
  expect(screen.getByTestId("fala-piggy").findAll((n) => (n.type as unknown) === "Text" && n.props.children === "•")).toHaveLength(bullets);
  expect(screen.queryByText(/^- /)).toBeNull();
  if (reply.includes("*um*")) expect(StyleSheet.flatten(screen.getByText("um").props.style).fontFamily).toBe("Inter-SemiBold");
  if (reply.includes("\n\n")) { expect(screen.getByText("Oi", { exact: true })).toBeTruthy(); expect(screen.getByText("Fim", { exact: true })).toBeTruthy(); }
});
it.each([[true, "undefined"], [false, "object"]])("reduzir movimento=%s: bolha, fala e chips com entering %s", async (reduzir, tipo) => {
  definirReduzir(reduzir); srv.chat = fluxoPronto("Oi", { chips: ["E o mês passado?"] });
  await abrir(); await enviar("oi"); await waitFor(() => expect(screen.getByTestId("chips-piggy")).toBeTruthy());
  for (const id of ["bolha-usuario", "fala-piggy", "chips-piggy"]) expect(typeof screen.getByTestId(id).props.entering).toBe(tipo);
});
const rolar = (y: number) => fireEvent.scroll(screen.UNSAFE_getAllByType(ScrollView).find((s) => s.props.onScroll)!, { nativeEvent: { contentOffset: { y }, layoutMeasurement: { height: 600 }, contentSize: { height: 2000 } } });
it("voltar ao fim: aparece com conteúdo abaixo, rola ao fim no toque e some no fim da lista", async () => {
  const fimDaLista = jest.spyOn(ScrollView.prototype, "scrollToEnd").mockImplementation(() => undefined);
  await abrir(); await enviar("oi"); await empurrar(fim("Resposta"));
  expect(screen.queryByLabelText("Voltar ao fim da conversa")).toBeNull();
  await act(async () => { rolar(0); }); fireEvent.press(screen.getByLabelText("Voltar ao fim da conversa")); expect(fimDaLista).toHaveBeenCalledWith({ animated: true });
  await act(async () => { rolar(1400); }); expect(screen.queryByLabelText("Voltar ao fim da conversa")).toBeNull();
});
it("stream não puxa a tela: 5 deltas depois do turno novo, a rolagem segue com 1 chamada", async () => {
  const rolagem = jest.spyOn(ScrollView.prototype, "scrollTo").mockImplementation(() => undefined);
  await abrir(); await enviar("oi");
  const layout = () => fireEvent(screen.getByTestId("bolha-usuario"), "layout", { nativeEvent: { layout: { x: 0, y: 100, width: 300, height: 40 } } });
  layout(); for (let i = 0; i < 5; i++) { await empurrar(delta(`p${i} `)); layout(); }
  expect(rolagem).toHaveBeenCalledTimes(1);
});
it.each([[true, false], [false, true]])("reduzir movimento=%s: a rolagem ao turno novo sai com animated %s", async (reduzir, animado) => {
  definirReduzir(reduzir); const rolagem = jest.spyOn(ScrollView.prototype, "scrollTo").mockImplementation(() => undefined);
  await abrir(); await enviar("oi");
  fireEvent(screen.getByTestId("bolha-usuario"), "layout", { nativeEvent: { layout: { x: 0, y: 100, width: 300, height: 40 } } });
  expect(rolagem).toHaveBeenCalledWith({ y: expect.any(Number), animated: animado });
});
it.each([
  ["- a\r\n- b\r\nfim", 2], ["- a\r- b", 2], ["- \n- b", 1], ["Oi\n- ", 0],
])("markdown: CRLF e bullet vazio %j", async (reply, bullets) => {
  srv.chat = fluxoPronto(reply); await abrir(); await enviar("oi");
  await waitFor(() => expect(screen.getByLabelText("Copiar resposta")).toBeTruthy()); // só existe depois do `fim`: o 0 não sai verde antes da resposta
  expect(screen.getByTestId("fala-piggy").findAll((n) => (n.type as unknown) === "Text" && n.props.children === "•")).toHaveLength(bullets);
});
it.each([[true, false], [false, true]])("reduzir movimento=%s: o voltar ao fim rola com animated %s", async (reduzir, animado) => {
  definirReduzir(reduzir); const fimDaLista = jest.spyOn(ScrollView.prototype, "scrollToEnd").mockImplementation(() => undefined);
  await abrir(); await enviar("oi"); await empurrar(fim("Resposta"));
  await act(async () => { rolar(0); }); fireEvent.press(screen.getByLabelText("Voltar ao fim da conversa")); expect(fimDaLista).toHaveBeenCalledWith({ animated: animado });
});
