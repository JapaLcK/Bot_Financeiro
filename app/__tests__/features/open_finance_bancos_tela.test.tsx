// Bancos conectados: a LISTA não mostra dinheiro; o DETALHE (uma Folha) mostra o que /api/app/bancos trouxe, e a tela NUNCA soma, inventa zero nem derruba a lista.
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { Alert, StyleSheet } from "react-native";
import { guardarCredenciais } from "@/storage/secure";
import contrato from "../../../tests/fixtures/app_bancos.json";
import type { Conexao } from "@/api/schemas/openFinance";
import { chamadas, fetchFalso, prepararCaso, resposta, S, segurar } from "./auth_apoio";
import { banco, DINHEIRO, HIDDEN, INTER, NUBANK, servidor } from "./open_finance_bancos_apoio";
import { abrirBanco, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

beforeEach(async () => { prepararCaso(); desligarTrava(); await guardarCredenciais(S); });
afterEach(() => jest.restoreAllMocks());

const fechar = () => act(async () => { fireEvent.press(screen.getByRole("button", { name: "Fechar" })); await drenar(); });
const comTotal = (total: string) => ({ ...contrato, bancos: [{ ...contrato.bancos[0], total }, ...contrato.bancos.slice(1)] });

it("a lista fechada não tem NENHUM valor: nem R$ nem valor falado (Money na linha reprova)", async () => {
  servidor([NUBANK, INTER]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Nubank, Atualizado" })).toBeTruthy());
  expect(screen.getByRole("button", { name: "Inter, Atualizado" })).toBeTruthy();
  await act(drenar);
  expect(screen.queryAllByText(DINHEIRO, HIDDEN)).toHaveLength(0);
  expect(screen.queryAllByLabelText(/reais/, HIDDEN)).toHaveLength(0);
  expect(screen.queryAllByText("—", HIDDEN)).toHaveLength(0);
  await abrirBanco(); // controle positivo: o mesmo valor existe, só que no detalhe
  expect(screen.getByText("R$ 45.000,00")).toBeTruthy();
});

it("o app NÃO soma: o detalhe mostra o total do servidor (99.999), não saldo + investido (45.000)", async () => {
  servidor([NUBANK], { corpo: comTotal("99999.00") });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await abrirBanco();
  await waitFor(() => expect(screen.getByText("R$ 99.999,00")).toBeTruthy());
  expect(screen.queryByText("R$ 45.000,00")).toBeNull();
  expect(screen.getByText("R$ 5.000,00")).toBeTruthy(); // Em conta
  expect(screen.getByText("R$ 40.000,00")).toBeTruthy(); // Investido
});

it("dois 'Nubank' com ids diferentes: cada detalhe tem o valor da SUA conexão (juntar por nome reprova)", async () => {
  servidor([banco(12, "Nubank"), banco(11, "Nubank")]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await abrirBanco("Nubank", 0);
  await waitFor(() => expect(screen.getByText("R$ 22.000,00")).toBeTruthy());
  expect(screen.queryByText("R$ 45.000,00")).toBeNull();
  await fechar();
  await abrirBanco("Nubank", 1);
  await waitFor(() => expect(screen.getByText("R$ 45.000,00")).toBeTruthy());
  expect(screen.queryByText("R$ 22.000,00")).toBeNull();
});

it("null é '—', nunca R$ 0,00; conexão que não veio em /bancos também é '—' (total, Em conta e Investido)", async () => {
  servidor([NUBANK, banco(13, "Itaú", { ui: { state: "paused", label: "Pausado", detail: null } }), banco(7, "Recém conectado")]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  for (const nome of ["Itaú", "Recém conectado"]) {
    await abrirBanco(nome);
    await waitFor(() => expect(screen.getAllByText("—")).toHaveLength(3));
    expect(screen.queryByText("R$ 0,00")).toBeNull();
    expect(screen.queryAllByText(DINHEIRO)).toHaveLength(0);
    await fechar();
  }
  await abrirBanco("Itaú"); // motivo do servidor: o aviso "a conferir" fica ao lado do total
  expect(screen.getByLabelText("a conferir")).toBeTruthy();
});

it("um item inválido (dinheiro '1E-7') não derruba os outros nem a tela", async () => {
  servidor([NUBANK, INTER], { corpo: { ...contrato, bancos: [contrato.bancos[0], { ...contrato.bancos[1], total: "1E-7" }] } });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await abrirBanco("Inter");
  // O item que não passa no schema sai da lista de /bancos: para o detalhe é "banco ausente" (total, Em conta e Investido em "—").
  await waitFor(() => expect(screen.getAllByText("—")).toHaveLength(3));
  expect(screen.queryByText("R$ 22.000,00")).toBeNull();
  await fechar();
  await abrirBanco();
  expect(screen.getByText("R$ 45.000,00")).toBeTruthy();
});

it.each([
  ["500", { status: 500, corpo: {} }], ["404 (servidor velho)", { status: 404, corpo: { detail: "Not Found" } }],
  ["403 (onboarding)", { status: 403, corpo: { detail: { code: "open_finance_onboarding_required" } } }],
  ["rede fora", { cai: true }], ["schema inválido", { corpo: { bancos: 5 } }],
] as const)("/bancos em %s: a lista aparece, sem número nenhum, e Desconectar funciona", async (_nome, bancos) => {
  servidor([NUBANK, INTER], bancos);
  jest.spyOn(Alert, "alert").mockImplementation((_t, _m, botoes) => { botoes?.find((b) => b.style === "destructive")?.onPress?.(); });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Nubank, Atualizado" })).toBeTruthy());
  expect(screen.getByRole("button", { name: "Inter, Atualizado" })).toBeTruthy();
  expect(screen.queryAllByText(DINHEIRO, HIDDEN)).toHaveLength(0);
  expect(screen.queryAllByText("—", HIDDEN)).toHaveLength(0);
  await act(drenar);
  expect(screen).toHavePathname("/conexoes"); // falha comum de /bancos não é fim de sessão
  await abrirBanco();
  expect(screen.queryAllByText(DINHEIRO, HIDDEN)).toHaveLength(0); // o detalhe também não tem linha de valor, total, barra nem contagem
  for (const rotulo of ["Em conta", "Investido"]) expect(screen.queryByText(rotulo)).toBeNull();
  expect(screen.getByText("Última atualização")).toBeTruthy();
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
  await waitFor(() => expect(screen.queryByRole("button", { name: /^Nubank,/ })).toBeNull());
  expect(chamadas().filter((c) => c.caminho === "/open-finance/1/connections/11")).toHaveLength(1);
});

describe("401 terminal em /bancos (o cliente já limpou o cofre): a sessão precisa saber", () => {
  const expirado = () => resposta(401, { detail: "expirado" });
  it("com perfil, limite e snapshot ok, vai para /entrar com o aviso", async () => {
    servidor([NUBANK], {}, { "/api/app/bancos": expirado, "/auth/refresh": expirado });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen).toHavePathname("/entrar"));
    expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
  });
  it("leitura cancelada por uma nova (puxar para atualizar) com 401 tardio: NÃO expira a sessão", async () => {
    const velha = segurar();
    let vez = 0;
    servidor([NUBANK], {}, { "/auth/refresh": expirado,
      "/api/app/bancos": async () => (++vez === 1 ? (await velha.promessa, expirado()) : resposta(200, contrato)) });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
    await act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); await drenar(); });
    await abrirBanco();
    await waitFor(() => expect(screen.getByText("R$ 45.000,00")).toBeTruthy());
    velha.soltar();
    await act(drenar);
    expect(screen).toHavePathname("/conexoes");
    expect(screen.getByText("R$ 45.000,00")).toBeTruthy();
  });
});

it("puxar para atualizar relê as DUAS leituras", async () => {
  servidor([NUBANK, INTER]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
  await act(drenar);
  const n = (caminho: string) => chamadas().filter((c) => c.caminho === caminho).length;
  const antes = [n("/open-finance/1"), n("/api/app/bancos")];
  await act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); await drenar(); });
  await waitFor(() => expect([n("/open-finance/1"), n("/api/app/bancos")]).toEqual([antes[0]! + 1, antes[1]! + 1]));
});

it.each([
  ["5xx", () => resposta(503, { detail: "fora" })], ["rede fora", () => { throw new TypeError("rede fora"); }],
] as const)("primeira leitura falha (%s): sem lista, 'Conferir de novo' aparece, o toque relê e a lista surge", async (_nome, falha) => {
  let ok = false;
  servidor([NUBANK], {}, { "/open-finance/1": () => (ok ? resposta(200, { connections: [NUBANK] }) : falha()) });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Conferir de novo" })).toBeTruthy());
  expect(screen.queryByRole("button", { name: /^Nubank,/ })).toBeNull();
  ok = true;
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Conferir de novo" })); await drenar(); });
  await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
});

describe("segunda leitura falha (puxar para atualizar): o botão de reler aparece, uma vez só", () => {
  const puxarComFalha = async (lista: Conexao[]) => {
    let ok = true;
    servidor(lista, {}, { "/open-finance/1": () => (ok ? resposta(200, { connections: lista }) : resposta(503, { detail: "fora" })) });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(lista.length ? screen.getByRole("button", { name: /^Nubank,/ }) : screen.getByText("Conecte seu primeiro banco")).toBeTruthy());
    ok = false;
    await act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); await drenar(); });
    return () => { ok = true; };
  };
  it("zero bancos: o botão aparece e o toque relê", async () => {
    const consertar = await puxarComFalha([]);
    await waitFor(() => expect(screen.getByRole("button", { name: "Conferir de novo" })).toBeTruthy());
    consertar();
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Conferir de novo" })); await drenar(); });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Conferir de novo" })).toBeNull());
    expect(screen.getByText("Conecte seu primeiro banco")).toBeTruthy();
  });
  it("com bancos listados: o botão também aparece (um só) e o toque relê", async () => {
    const consertar = await puxarComFalha([NUBANK]);
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Conferir de novo" })).toHaveLength(1));
    expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy();
    consertar();
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Conferir de novo" })); await drenar(); });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Conferir de novo" })).toBeNull());
  });
});

it("alvo de toque de 44 pt ou mais: a linha do banco, 'Conectar outro banco', 'Fechar' e os botões do detalhe", async () => {
  servidor([NUBANK]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
  const estilo = (el: { props: { style?: unknown } }) => StyleSheet.flatten(el.props.style as object) as Record<string, number>;
  expect(estilo(screen.getByRole("button", { name: /^Nubank,/ }).children[0] as never).minHeight).toBeGreaterThanOrEqual(44);
  expect(estilo(screen.getByRole("button", { name: "Conectar outro banco" })).minHeight).toBeGreaterThanOrEqual(44);
  await abrirBanco();
  const fecha = estilo(screen.getByRole("button", { name: "Fechar" }));
  expect([fecha.minWidth, fecha.minHeight]).toEqual([44, 44]);
  for (const nome of ["Reconectar Nubank", "Desconectar Nubank"]) expect(estilo(screen.getByRole("button", { name: nome }).children[0] as never).minHeight).toBeGreaterThanOrEqual(44);
});

it("'Conferir de novo' (a leitura falhou) tem alvo de 44 x 44", async () => {
  servidor([NUBANK], {}, { "/open-finance/1": () => resposta(503, { detail: "fora" }) });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Conferir de novo" })).toBeTruthy());
  const conferir = StyleSheet.flatten((screen.getByRole("button", { name: "Conferir de novo" }).children[0] as never as { props: { style: object } }).props.style) as Record<string, number>;
  expect(conferir.minHeight).toBeGreaterThanOrEqual(44);
});

it("sem nenhum banco: o Piggy, os três passos e o botão de conectar (nada de total nem de barra)", async () => {
  servidor([]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByText("Conecte seu primeiro banco")).toBeTruthy());
  expect(screen.getByRole("button", { name: "Conectar banco" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: /^Nubank,/ })).toBeNull();
  expect(fetchFalso).toHaveBeenCalled();
});

describe("/api/app/bancos é opcional: lento ou preso, nunca segura a tela", () => {
  const preso = () => new Promise<Response>(() => {});
  it("pendurado para sempre: a lista, 'Conectar outro banco' e Desconectar funcionam SEM números (com o await de volta, reprova)", async () => {
    servidor([NUBANK, INTER], {}, { "/api/app/bancos": preso });
    jest.spyOn(Alert, "alert").mockImplementation((_t, _m, botoes) => { botoes?.find((b) => b.style === "destructive")?.onPress?.(); });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Nubank, Atualizado" })).toBeTruthy());
    expect(screen.queryByText("Conferindo seus bancos…")).toBeNull();
    expect(screen.queryAllByText(DINHEIRO, HIDDEN)).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Conectar outro banco" })).toBeTruthy();
    await abrirBanco();
    expect(screen.queryByText("Em conta")).toBeNull(); // sem números: nada de linha de valor
    expect(screen.getByRole("button", { name: "Reconectar Nubank" })).toBeEnabled();
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
    await waitFor(() => expect(screen.queryByRole("button", { name: /^Nubank,/ })).toBeNull());
    expect(chamadas().filter((c) => c.caminho === "/open-finance/1/connections/11")).toHaveLength(1);
  });

  it("os números chegam depois do snapshot e completam o detalhe JÁ aberto", async () => {
    let soltar: (r: Response) => void = () => {};
    servidor([NUBANK], {}, { "/api/app/bancos": () => new Promise<Response>((r) => { soltar = r; }) });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await abrirBanco();
    expect(screen.queryByText("Em conta")).toBeNull();
    await act(async () => { soltar(resposta(200, contrato)); await drenar(); });
    expect(screen.getByText("Em conta")).toBeTruthy();
    expect(screen.getByText("R$ 45.000,00")).toBeTruthy();
  });

  it("corrida: a 1ª leitura de /bancos (R$ 111) responde DEPOIS da 2ª (R$ 222): o final é 222 e a 1ª foi abortada", async () => {
    const soltar: ((r: Response) => void)[] = [];
    const sinais: (AbortSignal | undefined)[] = [];
    servidor([NUBANK], {}, { "/api/app/bancos": (o) => {
      sinais.push(o.signal ?? undefined);
      return sinais.length === 1 ? new Promise<Response>((r) => soltar.push(r)) : resposta(200, comTotal("222.00"));
    } });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Nubank, Atualizado" })).toBeTruthy());
    await act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); await drenar(); });
    await abrirBanco();
    await waitFor(() => expect(screen.getByText("R$ 222,00")).toBeTruthy());
    expect(sinais[0]?.aborted).toBe(true); // a leitura velha foi cancelada, não só ignorada
    await act(async () => { soltar[0]!(resposta(200, comTotal("111.00"))); await drenar(); });
    expect(screen.getByText("R$ 222,00")).toBeTruthy();
    expect(screen.queryByText("R$ 111,00")).toBeNull();
  });

  it("sair da tela durante o fetch de /bancos aborta o pedido", async () => {
    let sinal: AbortSignal | undefined;
    servidor([NUBANK], {}, { "/api/app/bancos": (o) => { sinal = o.signal ?? undefined; return new Promise<Response>(() => {}); } });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Nubank, Atualizado" })).toBeTruthy());
    expect(sinal?.aborted).toBe(false);
    screen.unmount();
    expect(sinal?.aborted).toBe(true);
  });
});

describe("composição: a barra do detalhe só com Em conta e Investido, os dois positivos", () => {
  const sem = { motivos: [], contas: 1, cartoes: 0, investimentos: 0 };
  const so = (b: object) => ({ ...contrato, bancos: [{ conexao_id: 11, total: "1.00", ...sem, ...b }] });
  it.each([
    ["Em conta e Investido positivos: 2 segmentos", {}, 2],
    ["Investido null: sem barra", { saldo_conta: "5.00", investido: null }, 0],
    ["Em conta zero: sem barra", { saldo_conta: "0.00", investido: "5.00" }, 0],
    ["Em conta negativo: sem barra", { saldo_conta: "-5.00", investido: "5.00" }, 0],
  ] as const)("%s", async (_nome, b, segmentos) => {
    servidor([NUBANK], { corpo: Object.keys(b).length ? so(b) : contrato });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await abrirBanco();
    await waitFor(() => expect(screen.getByText("Em conta")).toBeTruthy());
    expect(screen.queryAllByTestId("segmento-composicao", HIDDEN)).toHaveLength(segmentos);
  });
});

describe("contagens do detalhe: UM elemento acessível com o rótulo do web, plural correto (o número solto o VoiceOver lê sem contexto)", () => {
  const com = (contas: number, cartoes: number, investimentos: number) => ({ ...contrato, bancos: [{ conexao_id: 11, saldo_conta: "5.00", investido: "5.00", total: "10.00", motivos: [], contas, cartoes, investimentos }] });
  it.each([
    ["plural", [2, 3, 4], ["2 contas", "3 cartões", "4 investimentos"]],
    ["singular", [1, 1, 1], ["1 conta", "1 cartão", "1 investimento"]],
  ] as const)("%s", async (_nome, [c, k, i], rotulos) => {
    servidor([NUBANK], { corpo: com(c, k, i) });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await abrirBanco();
    await waitFor(() => expect(screen.getByText("Em conta")).toBeTruthy());
    for (const r of rotulos) expect(screen.getByLabelText(r)).toBeTruthy();
  });
  it("zero some (não há '0 cartões')", async () => {
    servidor([NUBANK], { corpo: com(1, 0, 1) });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await abrirBanco();
    await waitFor(() => expect(screen.getByLabelText("1 conta")).toBeTruthy());
    expect(screen.queryByLabelText(/cart/)).toBeNull();
  });
});

it("sem institution_name: o título é 'Seu banco', mas os botões leem 'Reconectar banco' e 'Desconectar banco'", async () => {
  servidor([banco(11, "x", { institution_name: null, ui: { state: "needs_user_action", label: "Ação necessária", detail: null } })]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByText("Seu banco")).toBeTruthy());
  await abrirBanco("Seu banco");
  expect(screen.getByRole("button", { name: "Reconectar banco" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Desconectar banco" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^(Reconectar|Desconectar) Seu banco/ })).toBeNull();
});
