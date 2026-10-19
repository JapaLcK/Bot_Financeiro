// "Disponíveis para conectar": o catálogo (/connectors) é OPCIONAL e só se pede com vaga; o "+" pré-seleciona o banco no widget, pelo ID do conector.
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { Image, StyleSheet } from "react-native";
import type { Conexao } from "@/api/schemas/openFinance";
import { chamadas, cofre, fetchFalso, prepararCaso, resposta, rotear, S, type Rota } from "./auth_apoio";
import { guardarSessaoOf, VIVO } from "./open_finance_volta_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

type Props = { connectToken: string; updateItem?: string; selectedConnectorId?: number };
const widget: { props: Props | null } = { props: null };
jest.mock("react-native-pluggy-connect", () => ({ PluggyConnect: (props: Props) => {
  widget.props = props;
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  return require("react").createElement(require("react-native").View, { testID: "widget-disponiveis" });
} }));

const conectado = (id: number, nome: string, institution: string): Conexao => ({ ...VIVO, id, provider_item_id: `item_${id}`, institution_name: nome, logo_url: null, institution_id: institution });
const NUBANK = conectado(11, "Nubank", "201");
const CATALOGO = [{ id: 201, name: "Nubank", logo: "" }, { id: 619, name: "Nubank", logo: null }, { id: 601, name: "Itaú", logo: "https://evil.example/x.svg" }, { id: 602, name: "Bradesco", logo: null },
  { id: 603, name: "Banco do Brasil", logo: null }, { id: 604, name: "Santander", logo: null }, { id: 605, name: "Caixa", logo: null }, { id: 606, name: "Banco Pan", logo: null }];
const conectores = () => chamadas().filter((c) => c.caminho === "/open-finance/1/connectors");
const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>';
function servidor(opcoes: { vaga?: boolean; catalogo?: Rota; rotas?: Record<string, Rota> } = {}) {
  rotear({
    "/open-finance/1": () => resposta(200, { connections: [NUBANK] }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 3, em_uso: 1, pode_adicionar: opcoes.vaga ?? true, code: null, message: null }),
    "/open-finance/1/connectors": opcoes.catalogo ?? (() => resposta(200, { connectors: CATALOGO })),
    "/open-finance/1/connect-token": () => resposta(200, { ok: true, accessToken: "token" }),
    "/onboarding/open-finance": () => resposta(200, { ok: true, completed: true, completed_at: VIVO.last_sync_at }),
    "/api/app/bancos": () => resposta(404, {}),
    "/auth/refresh": () => resposta(401, {}),
    ...opcoes.rotas,
  });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); widget.props = null; await guardarSessaoOf(S); cofre.delete("pb.of.tentativa"); });
afterEach(() => jest.restoreAllMocks());

it("lista só populares ainda não conectados, por ID, na ordem fixa: o gêmeo 619 do Nubank é oferecido, o 201 conectado não", async () => {
  servidor();
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByText("Disponíveis para conectar")).toBeTruthy());
  const nomes = screen.getAllByRole("button", { name: /^Conectar (?!outro)/ }).map((b) => b.props.accessibilityLabel);
  expect(nomes).toEqual(["Conectar Itaú", "Conectar Bradesco", "Conectar Banco do Brasil", "Conectar Santander", "Conectar Caixa"]); // 5 no máximo; "Banco Pan" nunca; Nubank cairia fora dos 5
  expect(screen.getByRole("button", { name: "Ver todos os bancos" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Conectar outro banco" })).toBeNull();
});

it("'+' abre o fluxo com o banco pré-selecionado (selectedConnectorId = o ID do conector); 'Ver todos' abre sem escolha", async () => {
  servidor();
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Conectar Bradesco" })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Conectar Bradesco" })); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("widget-disponiveis")).toBeTruthy());
  expect(widget.props).toMatchObject({ selectedConnectorId: 602, updateItem: undefined });
});

it("'Ver todos os bancos': widget sem banco escolhido", async () => {
  servidor();
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Ver todos os bancos" })).toBeEnabled());
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ver todos os bancos" })); await drenar(); });
  await waitFor(() => expect(screen.getByTestId("widget-disponiveis")).toBeTruthy());
  expect(widget.props?.selectedConnectorId).toBeUndefined();
});

describe("o parâmetro `conector` do link é dado de fora", () => {
  it.each([["abc"], ["-1"], ["1.5"], [""], ["1234567890"], ["0"], ["000000000"], ["00012"]])("conector=%j vira 'sem banco escolhido'", async (valor) => {
    servidor();
    renderRouter("./app", { initialUrl: `/autorizando?conector=${valor}` });
    await waitFor(() => expect(screen.getByTestId("widget-disponiveis")).toBeTruthy());
    expect(widget.props?.selectedConnectorId).toBeUndefined();
  });
  it("controle positivo: conector=12 chega ao widget (e sem zero à esquerda)", async () => {
    servidor();
    renderRouter("./app", { initialUrl: "/autorizando?conector=12" });
    await waitFor(() => expect(screen.getByTestId("widget-disponiveis")).toBeTruthy());
    expect(widget.props?.selectedConnectorId).toBe(12);
  });
  it("controle positivo: conector=601 chega ao widget", async () => {
    servidor();
    renderRouter("./app", { initialUrl: "/autorizando?conector=601" });
    await waitFor(() => expect(screen.getByTestId("widget-disponiveis")).toBeTruthy());
    expect(widget.props?.selectedConnectorId).toBe(601);
  });
  it("itemId (reconectar) + conector: o conector é ignorado", async () => {
    servidor();
    renderRouter("./app", { initialUrl: "/autorizando?itemId=item_11&conector=601" });
    await waitFor(() => expect(screen.getByTestId("widget-disponiveis")).toBeTruthy());
    expect(widget.props).toMatchObject({ updateItem: "item_11", selectedConnectorId: undefined });
  });
});

describe("logo do catálogo é dado de terceiro: só https no CDN da Pluggy é buscado ou desenhado", () => {
  const OK = "https://cdn.pluggy.ai/assets/connector-icons/602.svg";
  it.each([["http"], ["user@host"], ["porta"], ["query"], ["outro host"], ["javascript:"], ["data:"]])("%s: nem baixado nem vira <Image>; o logo bom da linha ao lado é baixado", async (caso) => {
    const ruim = { http: "http://cdn.pluggy.ai/a.png", "user@host": "https://user@cdn.pluggy.ai/a.svg", porta: "https://cdn.pluggy.ai:444/a.png", query: "https://cdn.pluggy.ai/a.svg?x=1",
      "outro host": "https://evil.example/a.png", "javascript:": "javascript:alert(1)", "data:": "data:image/png;base64,AAAA" }[caso]!;
    servidor({ catalogo: () => resposta(200, { connectors: [{ id: 601, name: "Itaú", logo: ruim }, { id: 602, name: "Bradesco", logo: OK }] }), rotas: { "/assets/connector-icons/602.svg": () => ({ ok: true, status: 200, text: async () => SVG }) as unknown as Response } });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Conectar Itaú" })).toBeTruthy());
    await act(drenar);
    const externas = fetchFalso.mock.calls.map(([u]: [string]) => String(u)).filter((u: string) => !/\/(open-finance|auth|api|onboarding)\//.test(u));
    expect(externas).toEqual([OK]); // o bom foi baixado (a medição funciona); o ruim, nunca
    expect(screen.UNSAFE_queryAllByType(Image).map((i) => i.props.source?.uri)).toEqual([]); // nem como bitmap
  });
});

describe("o catálogo é opcional: se falhar, a seção some e 'Conectar outro banco' aparece", () => {
  it.each([["500", () => resposta(500, { detail: "fora" })], ["rede fora", () => { throw new TypeError("rede fora"); }]] as const)("/connectors em %s", async (_nome, catalogo) => {
    servidor({ catalogo });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Conectar outro banco" })).toBeEnabled());
    expect(screen.queryByText("Disponíveis para conectar")).toBeNull();
    expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy(); // a lista segue
    expect(conectores().length).toBeGreaterThan(0);
  });
  it("/connectors em 401 terminal: a sessão expira (vai para /entrar) — o 401 só chega DEPOIS da lista, então só o ramo do catálogo pode expirar a sessão", async () => {
    let soltar!: () => void; const pend = new Promise<void>((ok) => { soltar = ok; });
    servidor({ catalogo: async () => { await pend; return resposta(401, { detail: "expirado" }); } });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
    await act(drenar);
    expect(screen).toHavePathname("/conexoes"); // controle: com a lista na tela e o catálogo pendente, nada expirou
    await act(async () => { soltar(); await drenar(); });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
  });
  it("lendo o catálogo (ainda sem resposta) não aparece nem 'Conectar outro banco' nem a seção", async () => {
    servidor({ catalogo: () => new Promise<Response>(() => {}) });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
    await act(drenar);
    expect(screen.queryByRole("button", { name: "Conectar outro banco" })).toBeNull();
    expect(screen.queryByText("Disponíveis para conectar")).toBeNull();
  });
  it("sair da tela aborta o pedido do catálogo", async () => {
    let sinal: AbortSignal | undefined;
    servidor({ catalogo: (o) => { sinal = o.signal ?? undefined; return new Promise<Response>(() => {}); } });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
    await waitFor(() => expect(sinal).toBeDefined());
    expect(sinal?.aborted).toBe(false);
    screen.unmount();
    expect(sinal?.aborted).toBe(true);
  });
});

it("sem vaga (pode_adicionar false): NENHUM pedido a /connectors, 'Conectar outro banco' desativado", async () => {
  servidor({ vaga: false });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Conectar outro banco" })).toBeDisabled());
  await act(drenar);
  expect(conectores()).toHaveLength(0);
  expect(screen.queryByText("Disponíveis para conectar")).toBeNull();
});

it("alvo de toque de 44 pt ou mais: cada banco oferecido e 'Ver todos os bancos'", async () => {
  servidor();
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Conectar Itaú" })).toBeTruthy());
  const piso = (el: { findAll: (f: (n: { props: { style?: unknown } }) => boolean) => unknown[] }) => el.findAll((n) => ((StyleSheet.flatten(n.props.style as object) as { minHeight?: number } | undefined)?.minHeight ?? 0) >= 44).length;
  for (const nome of ["Conectar Itaú", "Conectar Bradesco", "Ver todos os bancos"]) expect(piso(screen.getByRole("button", { name: nome }) as never)).toBeGreaterThan(0);
});
