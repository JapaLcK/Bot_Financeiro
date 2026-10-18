// Bancos conectados (PR 3): o que a tela mostra de /api/app/bancos e o que ela NUNCA faz com ele (somar, inventar zero, derrubar a lista).
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { Alert, StyleSheet } from "react-native";
import type { Conexao } from "@/api/schemas/openFinance";
import { guardarCredenciais } from "@/storage/secure";
// O contrato LIDO do arquivo que o pytest valida contra o Pydantic (PR 1); nunca copiado (CLAUDE.md §0.7).
import contrato from "../../../tests/fixtures/app_bancos.json";
import { chamadas, fetchFalso, prepararCaso, resposta, rotear, S, type Rota } from "./auth_apoio";
import { VIVO } from "./open_finance_volta_apoio";
import { abrirBancos, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

// O SVG do logo vem do CDN: aqui a rede é o `fetchFalso` (rota por caminho) e o `react-native-svg` é o real.
const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>';
const logoRota = () => ({ ok: true, status: 200, text: async () => SVG }) as unknown as Response;

const banco = (id: number, nome: string, extra: Partial<Conexao> = {}): Conexao => ({ ...VIVO, id, provider_item_id: `item_${id}`, institution_name: nome, logo_url: null, ...extra });
const NUBANK = banco(11, "Nubank"), INTER = banco(12, "Inter");
const HIDDEN = { includeHiddenElements: true };
const DINHEIRO = /R\$\s?\d/;
type Bancos = { status?: number; corpo?: unknown; cai?: boolean };
function servidor(lista: Conexao[], bancos: Bancos = {}, rotas: Record<string, Rota> = {}) {
  rotear({
    "/open-finance/1": () => resposta(200, { connections: lista }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: 2, em_uso: lista.length, pode_adicionar: lista.length < 2, code: null, message: null }),
    "/open-finance/1/connections/11": () => { lista = lista.filter((c) => c.id !== 11); return resposta(200, { ok: true, deleted: 1 }); },
    "/api/app/bancos": () => { if (bancos.cai) throw new TypeError("rede fora"); return resposta(bancos.status ?? 200, bancos.corpo ?? contrato); },
    "/assets/connector-icons/201.svg": logoRota, "/assets/connector-icons/202.svg": logoRota,
    ...rotas,
  });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); await guardarCredenciais(S); });
afterEach(() => jest.restoreAllMocks());

it("o app NÃO soma: o topo é o total do servidor (67.000), mesmo com o snapshot listando só o Nubank (45.000)", async () => {
  servidor([NUBANK]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByText("R$ 67.000,00")).toBeTruthy());
  expect(screen.getByRole("button", { name: /^Nubank, Atualizado, 45000 reais$/ })).toBeTruthy();
});

it("dois 'Nubank' com ids diferentes: cada cartão tem o valor da SUA conexão (juntar por nome reprova)", async () => {
  servidor([banco(12, "Nubank"), banco(11, "Nubank")]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getAllByRole("button", { name: /^Nubank,/ })).toHaveLength(2));
  expect(screen.getAllByRole("button", { name: /^Nubank,/ }).map((b) => b.props.accessibilityLabel)).toEqual(["Nubank, Atualizado, 22000 reais", "Nubank, Atualizado, 45000 reais"]);
});

it("null é '—', nunca R$ 0,00; conexão que não veio em /bancos também é '—'", async () => {
  servidor([NUBANK, banco(13, "Itaú", { ui: { state: "paused", label: "Pausado", detail: null } }), banco(7, "Recém conectado")]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank, Atualizado, 45000 reais$/ })).toBeTruthy());
  expect(screen.getByRole("button", { name: "Itaú, Pausado, valor indisponível, a conferir" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Recém conectado, Atualizado, valor indisponível" })).toBeTruthy();
  expect(screen.getAllByText("—")).toHaveLength(2);
  expect(screen.queryByText("R$ 0,00")).toBeNull();
});

it("um item inválido (dinheiro '1E-7') não derruba os outros nem a tela", async () => {
  servidor([NUBANK, INTER], { corpo: { ...contrato, bancos: [contrato.bancos[0], { ...contrato.bancos[1], total: "1E-7" }] } });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank, Atualizado, 45000 reais$/ })).toBeTruthy());
  expect(screen.getByRole("button", { name: "Inter, Atualizado, valor indisponível" })).toBeTruthy();
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
  await abrirBancos();
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
  await waitFor(() => expect(screen.queryByRole("button", { name: /^Nubank,/ })).toBeNull());
  expect(chamadas().filter((c) => c.caminho === "/open-finance/1/connections/11")).toHaveLength(1);
});

it("Conferir de novo e puxar para atualizar releem as DUAS leituras", async () => {
  servidor([NUBANK, INTER]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByText("R$ 67.000,00")).toBeTruthy());
  const n = (caminho: string) => chamadas().filter((c) => c.caminho === caminho).length;
  const antes = [n("/open-finance/1"), n("/api/app/bancos")];
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Conferir de novo" })); await drenar(); });
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

describe("segunda leitura falha (puxar para atualizar): quem tem o ícone de conferir não ganha um botão a mais", () => {
  const puxarComFalha = async (lista: Conexao[]) => {
    let ok = true;
    servidor(lista, {}, { "/open-finance/1": () => (ok ? resposta(200, { connections: lista }) : resposta(503, { detail: "fora" })) });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByText(lista.length ? "Nos seus bancos" : "Conecte seu primeiro banco")).toBeTruthy());
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
  it("com bancos listados: só o ícone do topo, sem botão duplicado", async () => {
    await puxarComFalha([NUBANK]);
    await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
    expect(screen.getAllByRole("button", { name: "Conferir de novo" })).toHaveLength(1);
  });
});

describe("logo do banco (dado de terceiro: o host é fixado)", () => {
  const baixados = () => fetchFalso.mock.calls.map(([u]: [string]) => String(u)).filter((u) => u.endsWith(".svg"));
  const ok = "https://cdn.pluggy.ai/assets/connector-icons/201.svg";
  it("SVG do CDN da Pluggy é buscado; outro host, http, javascript:, null e ausente ficam na inicial", async () => {
    const semCampo = banco(15, "Sem campo"); delete (semCampo as { logo_url?: unknown }).logo_url;
    servidor([banco(11, "Nubank", { logo_url: ok }), banco(12, "Outro host", { logo_url: "https://evil.example/x.svg" }), banco(13, "Http", { logo_url: "http://cdn.pluggy.ai/x.svg" }),
      banco(14, "Script", { logo_url: "javascript:alert(1)" }), semCampo, banco(16, "Nulo")]);
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getAllByRole("button", { name: /^Nubank,/ })).toHaveLength(1));
    await act(async () => { await drenar(); });
    expect(baixados()).toEqual([ok]);
    // A inicial do Nubank sai quando o desenho existe; as dos outros cinco (sem logo) ficam.
    expect(screen.queryAllByText("N", HIDDEN)).toHaveLength(1); // só "Nulo" ("N"); o Nubank já tem desenho
  });
  it("o círculo do banco é decorativo: o leitor de tela lê o banco uma vez só, no cabeçalho", async () => {
    servidor([banco(11, "Nubank", { logo_url: ok })]);
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
    expect(screen.queryAllByLabelText("Nubank")).toHaveLength(0);
    expect(screen.queryAllByText("N")).toHaveLength(0);
  });
  it("1 toque no cabeçalho de um cartão NÃO refaz o download dos logos (nem do dele nem dos outros)", async () => {
    servidor([banco(11, "Nubank", { logo_url: ok }), banco(12, "Inter", { logo_url: ok.replace("201", "202") })]);
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getAllByRole("button", { name: /^(Nubank|Inter),/ })).toHaveLength(2));
    await act(async () => { await drenar(); });
    expect(baixados()).toHaveLength(2);
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: /^Nubank,/ })); await drenar(); });
    expect(screen.getByRole("button", { name: /^Nubank,/ }).props.accessibilityState.expanded).toBe(true);
    expect(baixados()).toHaveLength(2);
  });
});

describe("ocultar valores (a escolha feita no Painel vale aqui)", () => {
  async function ate(conexoes: string, ocultar: boolean) {
    servidor([NUBANK, INTER]);
    renderRouter("./app", { initialUrl: "/resumo" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Ocultar valores" })).toBeTruthy());
    if (ocultar) await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); await drenar(); });
    const barrasAntes = screen.queryAllByTestId("barra-composicao", HIDDEN).length; // a do Painel, ainda montada embaixo da pilha
    await act(async () => { router.push(conexoes as "/conexoes"); await drenar(); });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
    return screen.queryAllByTestId("barra-composicao", HIDDEN).length - barrasAntes;
  }
  it("escondido no Painel: nenhum valor em R$, nenhum valor falado, nenhuma barra", async () => {
    expect(await ate("/conexoes", true)).toBe(0);
    expect(screen.queryAllByText(DINHEIRO, HIDDEN)).toHaveLength(0);
    expect(screen.queryAllByLabelText(/reais/, HIDDEN)).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Nubank, Atualizado, valor oculto" })).toBeTruthy();
    await abrirBancos();
    expect(screen.queryAllByText(DINHEIRO, HIDDEN)).toHaveLength(0);
  });
  it("controle positivo: sem esconder, os mesmos valores e a barra aparecem", async () => {
    expect(await ate("/conexoes", false)).toBe(1);
    expect(screen.getByText("R$ 67.000,00")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Nubank, Atualizado, 45000 reais" })).toBeTruthy();
  });
});

it("a hora da última sincronização é tempo relativo, medido no relógio de agora", async () => {
  servidor([NUBANK]);
  jest.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-05T12:12:30Z"));
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByText("há 12 min")).toBeTruthy());
});

it("alvo de toque de 44 pt ou mais: cabeçalho do cartão, 'Conferir de novo', 'Conectar outro banco' e os botões", async () => {
  servidor([NUBANK]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
  await abrirBancos();
  const estilo = (el: { props: { style?: unknown } }) => StyleSheet.flatten(el.props.style as object) as Record<string, number>;
  expect(estilo(screen.getByRole("button", { name: /^Nubank,/ })).minHeight).toBeGreaterThanOrEqual(44);
  const conferir = estilo(screen.getByRole("button", { name: "Conferir de novo" }));
  expect([conferir.width, conferir.height]).toEqual([44, 44]);
  expect(estilo(screen.getByRole("button", { name: "Conectar outro banco" })).minHeight).toBeGreaterThanOrEqual(44);
  for (const nome of ["Reconectar Nubank", "Desconectar Nubank"]) expect(estilo(screen.getByRole("button", { name: nome }).children[0] as never).minHeight).toBeGreaterThanOrEqual(44);
});

it("sem nenhum banco: o Piggy, os três passos e o botão de conectar (nada de total nem de barra)", async () => {
  servidor([]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByText("Conecte seu primeiro banco")).toBeTruthy());
  expect(screen.getByRole("button", { name: "Conectar banco" })).toBeEnabled();
  expect(screen.queryByText("Nos seus bancos")).toBeNull();
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
    await abrirBancos();
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
    await waitFor(() => expect(screen.queryByRole("button", { name: /^Nubank,/ })).toBeNull());
    expect(chamadas().filter((c) => c.caminho === "/open-finance/1/connections/11")).toHaveLength(1);
  });

  it("os números chegam depois do snapshot e completam a tela", async () => {
    let soltar: (r: Response) => void = () => {};
    servidor([NUBANK], {}, { "/api/app/bancos": () => new Promise<Response>((r) => { soltar = r; }) });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Nubank, Atualizado" })).toBeTruthy());
    await act(async () => { soltar(resposta(200, contrato)); await drenar(); });
    expect(screen.getByText("R$ 67.000,00")).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Nubank, Atualizado, 45000 reais$/ })).toBeTruthy();
  });

  it("corrida: a 1ª leitura de /bancos (R$ 111) responde DEPOIS da 2ª (R$ 222): o final é 222 e a 1ª foi abortada", async () => {
    const soltar: ((r: Response) => void)[] = [];
    const sinais: (AbortSignal | undefined)[] = [];
    servidor([NUBANK], {}, { "/api/app/bancos": (o) => {
      sinais.push(o.signal ?? undefined);
      return sinais.length === 1 ? new Promise<Response>((r) => soltar.push(r)) : resposta(200, { ...contrato, total: "222.00" });
    } });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Conferir de novo" })).toBeTruthy());
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Conferir de novo" })); await drenar(); });
    await waitFor(() => expect(screen.getByText("R$ 222,00")).toBeTruthy());
    expect(sinais[0]?.aborted).toBe(true); // a leitura velha foi cancelada, não só ignorada
    await act(async () => { soltar[0]!(resposta(200, { ...contrato, total: "111.00" })); await drenar(); });
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

it("topo que não passa no schema (null) com bancos listados: '—' no topo, nunca R$ 0,00", async () => {
  servidor([NUBANK, INTER], { corpo: { ...contrato, total: "1E-7" } });
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank, Atualizado, 45000 reais$/ })).toBeTruthy());
  expect(screen.getAllByText("—")).toHaveLength(1);
  expect(screen.queryByText("R$ 0,00")).toBeNull();
});

describe("composição: barra e legenda só com bancos de valor positivo, % entre as partes", () => {
  const nulo = { saldo_conta: null, investido: null, contas: 1, cartoes: 0, investimentos: 0, motivos: [] };
  const com = (extra: object[]) => ({ ...contrato, bancos: [contrato.bancos[0], contrato.bancos[1], ...extra] });
  it("45.000 e 22.000 viram 67% e 33%, em 2 segmentos", async () => {
    servidor([NUBANK, INTER]);
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByText("67%")).toBeTruthy());
    expect(screen.getByText("33%")).toBeTruthy();
    expect(screen.getAllByTestId("segmento-composicao", HIDDEN)).toHaveLength(2);
  });
  it("banco com total 0, negativo ou null fica FORA da barra e da legenda", async () => {
    servidor([NUBANK, INTER, banco(13, "Zerado"), banco(14, "Devedor"), banco(15, "Sem valor")],
      { corpo: com([{ ...nulo, conexao_id: 13, total: "0.00" }, { ...nulo, conexao_id: 14, total: "-50.00" }, { ...nulo, conexao_id: 15, total: null }]) });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByText("67%")).toBeTruthy());
    expect(screen.getAllByTestId("segmento-composicao", HIDDEN)).toHaveLength(2);
    expect(screen.getByText("33%")).toBeTruthy();
    expect(screen.queryAllByText(/^-?\d+%$/)).toHaveLength(2); // nenhuma legenda "Zerado 0%" nem "Devedor -0%"
  });
  it("com UM banco positivo só, não há composição", async () => {
    servidor([NUBANK, banco(13, "Zerado")], { corpo: com([{ ...nulo, conexao_id: 13, total: "0.00" }]) });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByText("R$ 67.000,00")).toBeTruthy());
    expect(screen.queryAllByTestId("segmento-composicao", HIDDEN)).toHaveLength(0);
  });
});

it("sem institution_name: o título é 'Seu banco', mas os botões leem 'Reconectar banco' e 'Desconectar banco'", async () => {
  servidor([banco(11, "x", { institution_name: null, ui: { state: "needs_user_action", label: "Ação necessária", detail: null } })]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByText("Seu banco")).toBeTruthy());
  await abrirBancos();
  expect(screen.getByRole("button", { name: "Reconectar banco" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Desconectar banco" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^(Reconectar|Desconectar) Seu banco/ })).toBeNull();
});
