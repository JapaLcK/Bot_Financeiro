import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { DateTimePickerAndroid } from "@react-native-community/datetimepicker";
import { Alert, Platform } from "react-native";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import v2 from "../../../tests/frontend/api_v2_respostas.json";
// Folha de meta (PR 2 do app v2), continuação de painel_metas_escrita.test.tsx (o teto de 350 linhas): falha com a folha dispensada, resultado desconhecido, `?id=` inválido. O seletor de data é nativo: o dublê expõe o `onChange`.
jest.mock("@react-native-community/datetimepicker", () => ({ __esModule: true, default: (p: object) => jest.requireActual("react").createElement(jest.requireActual("react-native").View, { testID: "seletor-data", ...p }), DateTimePickerAndroid: { open: jest.fn() } }));
jest.setTimeout(30_000);
type Corpo = Record<string, unknown>;
const TODAS = (v2 as unknown as { metas: { varios: { metas: Record<string, unknown>[]; custo_mensal: unknown }; vazio: unknown; sem_custo: unknown } }).metas;
let lista: unknown, escrita: (rota: string, corpo: Corpo) => Response | Promise<Response>, investido: () => Response;
const posts = () => fetchFalso.mock.calls.filter(([, o]) => (o as RequestInit)?.method === "POST").map(([u, o]) => ({ rota: new URL(String(u)).pathname, corpo: JSON.parse(String((o as RequestInit).body)) as Corpo }));
const gets = () => fetchFalso.mock.calls.filter(([u, o]) => new URL(String(u)).pathname === "/api/app/metas" && (o as RequestInit).method === "GET").length;
function servidor() {
  fetchFalso.mockImplementation(async (url: string, o: RequestInit) => {
    const path = new URL(url).pathname;
    if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
    if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
    if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
    if (path === "/api/app/perfil") return resposta(200, { perfil: "padrao" });
    if (path === "/api/app/metas" && o.method === "GET") return resposta(200, lista);
    if (path === "/api/app/investido") return investido();
    if (path === "/api/app/contas") { const c = (fixture as unknown as Record<string, { contas: object[] }>)[path]!; return resposta(200, { ...c, contas: [...c.contas, { ...c.contas[0], id: 3, instituicao: "Wise", nome: "Dólar", moeda: "USD" }] }); } // uma conta em outra moeda: a meta é em reais, ela não entra na lista de fontes
    if (o.method === "POST" && path.startsWith("/api/app/metas")) return escrita(path, JSON.parse(String(o.body)));
    return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
  });
}
beforeEach(async () => {
  prepararCaso(); desligarTrava(); lista = TODAS.varios; escrita = () => resposta(200, { id: 99 }); investido = () => resposta(200, v2.investido.com_banco);
  await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor(); jest.spyOn(Alert, "alert").mockImplementation(() => {});
});
afterEach(() => jest.restoreAllMocks());
const botao = (nome: string | RegExp) => screen.getByRole("button", { name: nome });
const apertar = async (nome: string | RegExp) => { await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); };
const abrirAba = async () => { renderRouter("./app", { initialUrl: "/metas" }); await waitFor(() => expect(screen.getByTestId("bloco-metas")).toBeTruthy()); await waitFor(() => expect(gets()).toBeGreaterThan(0)); await act(drenar); };
const folha = async (titulo: string) => { await waitFor(() => expect(screen.getByText(titulo)).toBeTruthy()); await waitFor(() => expect(screen.getByLabelText(/^Nome/)).toBeTruthy()); };
const digitar = (rotulo: RegExp | string, valor: string) => fireEvent.changeText(screen.getByLabelText(rotulo), valor);
const digitarValor = (centavos: string) => fireEvent.changeText(screen.getByTestId("valor-input"), centavos);
const fechou = (titulo: string) => waitFor(() => expect(screen.queryByText(titulo)).toBeNull());
const salvar = () => apertar("Salvar");

describe("a gravação que FALHA com a folha já dispensada", () => {
  it.each([["500 (resultado desconhecido)", () => resposta(500, undefined)], ["400 (falha conhecida)", () => resposta(400, { error: { code: "valor_invalido", message: "x" } })]])("%s: a aba relê e mostra a lista nova (o resultado pode ter mudado a lista)", async (_nome, falha) => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "Viagem"); digitarValor("100000");
    const preso = segurar(); escrita = async () => { await preso.promessa; return falha(); };
    await act(async () => { fireEvent.press(botao("Salvar")); await drenar(); }); expect(posts()).toHaveLength(1);
    act(() => router.back()); await fechou("Nova meta"); await act(drenar); const antes = gets(); // deslizou
    lista = { ...TODAS.varios, metas: [{ ...TODAS.varios.metas[2]!, id: 99, nome: "Viagem" }] };
    preso.soltar(); await act(drenar); await act(drenar); await waitFor(() => expect(screen.getByTestId("meta-99")).toBeTruthy()); expect(gets()).toBeGreaterThan(antes);
  });
});

describe("resultado desconhecido: tudo inerte, só 'Fechar'", () => {
  const desligado = (nome: string | RegExp) => botao(nome).props.accessibilityState.disabled;
  const perder = async () => { escrita = () => resposta(500, undefined); await salvar(); await waitFor(() => expect(screen.getByText("Pode ter salvo. Confira a lista.")).toBeTruthy()); };
  it("meta nova sem prazo: Prazo e Fonte desligados (positivo: antes de falhar, ligados)", async () => {
    lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "X"); digitarValor("100000");
    expect(desligado(/Prazo/)).toBeFalsy(); expect(desligado(/Fonte/)).toBeFalsy();
    await perder(); expect(desligado(/Prazo/)).toBe(true); expect(desligado(/Fonte/)).toBe(true);
    await apertar(/Prazo/); expect(screen.queryByTestId("seletor-data")).toBeNull(); // o toque não muda nada
  });
  it("edição com prazo: Tirar prazo, Fonte e Apagar desligados; o toque em Apagar não abre o alerta", async () => {
    await abrirAba(); await apertar(/^Viagem pro Japão.*editar$/); await folha("Editar meta"); digitar(/^Nome/, "Japão 2");
    expect(desligado("Tirar prazo")).toBeFalsy(); expect(desligado(/Fonte/)).toBeFalsy(); expect(desligado("Apagar")).toBeFalsy();
    const seletor = () => screen.getByTestId("seletor-data").props.disabled; expect(seletor()).toBeFalsy();
    await perder(); expect(seletor()).toBe(true); expect(desligado("Tirar prazo")).toBe(true); expect(desligado(/Fonte/)).toBe(true); expect(desligado("Apagar")).toBe(true);
    await apertar("Apagar"); await apertar("Tirar prazo"); expect(Alert.alert).not.toHaveBeenCalled(); expect(screen.getByRole("button", { name: "Tirar prazo" })).toBeTruthy();
  });
  it("reserva sem custo: 'Definir o valor eu mesmo' desligado", async () => {
    lista = { ...TODAS.sem_custo as object, metas: [{ ...TODAS.varios.metas[0]!, alvo: null, atual: null, falta: null, percentual: null }] }; await abrirAba();
    await apertar(/^Reserva de emergência.*editar$/); await folha("Editar meta"); await apertar("Mais um mês");
    expect(desligado("Definir o valor eu mesmo")).toBeFalsy(); await perder(); expect(desligado("Definir o valor eu mesmo")).toBe(true);
    await apertar("Definir o valor eu mesmo"); expect(screen.getByRole("button", { name: "Definir o valor eu mesmo" })).toBeTruthy();
  });
});

describe("`?id=` inválido", () => {
  it.each(["", "0", "-1", "1.5"])("`?id=%j` (presente e inválido) é 'Essa meta não existe mais', nunca 'Nova meta'", async (id) => {
    lista = TODAS.vazio; await abrirAba(); act(() => router.push(`/meta?id=${id}` as never));
    await waitFor(() => expect(screen.getByText("Essa meta não existe mais.")).toBeTruthy()); expect(screen.queryByText("Nova meta")).toBeNull(); expect(screen.queryByLabelText(/^Nome/)).toBeNull();
  });
});

describe("o tipo não troca com o POST em voo", () => {
  const preparar = async () => { lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "Viagem"); digitarValor("100000"); };
  const tocarReserva = () => act(async () => { fireEvent.press(screen.getByRole("tab", { name: "Reserva" })); await drenar(); });
  it("tocar em 'Reserva' depois de Salvar não muda a tela, e o corpo enviado é o de meta (positivo: depois de uma falha 400, o toque troca)", async () => {
    await preparar(); const preso = segurar(); escrita = async () => { await preso.promessa; return resposta(400, { error: { code: "valor_invalido", message: "x" } }); };
    await act(async () => { fireEvent.press(botao("Salvar")); await drenar(); }); expect(posts()).toHaveLength(1);
    await tocarReserva(); expect(screen.getByText("Nova meta")).toBeTruthy(); expect(screen.queryByText("Montar reserva")).toBeNull();
    expect(posts()[0]!.corpo).toMatchObject({ tipo: "meta", nome: "Viagem" });
    preso.soltar(); await act(drenar); await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy()); // falha conhecida: a trava solta
    await tocarReserva(); expect(screen.getByText("Montar reserva")).toBeTruthy();
  });
});

describe("Android: o prazo abre o diálogo uma vez, no toque (não é o componente que reabre a cada render)", () => {
  const abrir = DateTimePickerAndroid.open as jest.Mock;
  const prepararAndroid = async () => { jest.replaceProperty(Platform, "OS", "android"); abrir.mockClear(); lista = TODAS.vazio; await abrirAba(); await apertar("Criar meta"); await folha("Nova meta"); digitar(/^Nome/, "Casa"); digitarValor("100000"); };
  it("tocar em Prazo chama `open` uma vez (mode date); 'dismissed' não muda nada; 'set' grava a data; o componente inline não é renderizado", async () => {
    await prepararAndroid(); expect(abrir).not.toHaveBeenCalled();
    await apertar(/Prazo/); expect(abrir).toHaveBeenCalledTimes(1); expect(abrir.mock.calls[0]![0]).toMatchObject({ mode: "date" });
    const { onChange } = abrir.mock.calls[0]![0] as { onChange: (e: { type: string }, d?: Date) => void };
    await act(async () => { onChange({ type: "dismissed" }); await drenar(); }); expect(screen.getByText("Sem prazo")).toBeTruthy();
    await act(async () => { onChange({ type: "set" }, new Date(2030, 0, 5, 9)); await drenar(); });
    expect(screen.getByText("05/01/2030")).toBeTruthy(); expect(screen.queryByTestId("seletor-data")).toBeNull(); expect(abrir).toHaveBeenCalledTimes(1); // nem ao aparecer a data nem a cada render
    await salvar(); await waitFor(() => expect(posts()[0]!.corpo).toMatchObject({ data_alvo: "2030-01-05" }));
  });
  it("com a data já posta, tocar na linha reabre o diálogo com ela; desativado (resultado desconhecido) não abre", async () => {
    await prepararAndroid(); await apertar(/Prazo/);
    await act(async () => { (abrir.mock.calls[0]![0] as { onChange: (e: { type: string }, d?: Date) => void }).onChange({ type: "set" }, new Date(2030, 0, 5, 9)); await drenar(); });
    await apertar(/Prazo/); expect(abrir).toHaveBeenCalledTimes(2); expect((abrir.mock.calls[1]![0] as { value: Date }).value.getFullYear()).toBe(2030);
    escrita = () => resposta(500, undefined); await salvar(); await waitFor(() => expect(screen.getByText("Pode ter salvo. Confira a lista.")).toBeTruthy());
    await apertar(/Prazo/); expect(abrir).toHaveBeenCalledTimes(2);
  });
});
