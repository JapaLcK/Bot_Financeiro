// Bancos conectados: logo do banco, valores ocultos e o "há N min" (o relógio da tela) — o detalhe aberto.
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, S } from "./auth_apoio";
import { banco, DINHEIRO, HIDDEN, INTER, NUBANK, servidor } from "./open_finance_bancos_apoio";
import { abrirBanco, appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

beforeEach(async () => { prepararCaso(); desligarTrava(); await guardarCredenciais(S); });
afterEach(() => jest.restoreAllMocks());

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
    await abrirBanco();
    expect(screen.queryAllByLabelText("Nubank")).toHaveLength(0);
    expect(screen.queryAllByText("N")).toHaveLength(0);
  });
  it("abrir o detalhe NÃO refaz o download dos logos da lista (só o avatar novo do detalhe baixa o do próprio banco)", async () => {
    servidor([banco(11, "Nubank", { logo_url: ok }), banco(12, "Inter", { logo_url: ok.replace("201", "202") })]);
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getAllByRole("button", { name: /^(Nubank|Inter),/ })).toHaveLength(2));
    await act(async () => { await drenar(); });
    expect(baixados()).toHaveLength(2);
    await abrirBanco();
    expect(baixados().filter((u) => u.endsWith("202.svg"))).toHaveLength(1);
    expect(baixados().filter((u) => u.endsWith("201.svg"))).toHaveLength(2); // a lista + o avatar de 64 do detalhe
  });
});

describe("ocultar valores (a escolha feita no Painel vale aqui)", () => {
  let barrasDoPainel = 0;
  async function ate(conexoes: string, ocultar: boolean) {
    servidor([NUBANK, INTER]);
    renderRouter("./app", { initialUrl: "/resumo" });
    await waitFor(() => expect(screen.getByRole("button", { name: "Ocultar valores" })).toBeTruthy());
    if (ocultar) await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); await drenar(); });
    barrasDoPainel = screen.queryAllByTestId("barra-composicao", HIDDEN).length; // a do Painel, ainda montada embaixo da pilha
    await act(async () => { router.push(conexoes as "/conexoes"); await drenar(); });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
    await act(drenar);
  }
  it("escondido no Painel: o detalhe não tem R$, valor falado, barra nem o aviso 'a conferir'", async () => {
    await ate("/conexoes", true);
    await abrirBanco();
    expect(screen.queryAllByTestId("barra-composicao", HIDDEN).length).toBe(barrasDoPainel);
    expect(screen.queryAllByText(DINHEIRO, HIDDEN)).toHaveLength(0);
    expect(screen.queryAllByLabelText(/reais/, HIDDEN)).toHaveLength(0);
    expect(screen.queryAllByLabelText("a conferir", HIDDEN)).toHaveLength(0);
    expect(screen.getAllByLabelText("Valor oculto").length).toBeGreaterThan(0);
  });
  it("controle positivo: sem esconder, os mesmos valores, a barra e o 'a conferir' (banco pausado) aparecem", async () => {
    await ate("/conexoes", false);
    await abrirBanco();
    expect(screen.queryAllByTestId("barra-composicao", HIDDEN).length).toBe(barrasDoPainel + 1);
    expect(screen.getByText("R$ 45.000,00")).toBeTruthy();
    expect(screen.getByLabelText("45000 reais")).toBeTruthy();
  });
});

it("a hora da última sincronização é tempo relativo, medido no relógio de agora", async () => {
  servidor([NUBANK]);
  jest.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-05T12:12:30Z"));
  renderRouter("./app", { initialUrl: "/conexoes" });
  await abrirBanco();
  await waitFor(() => expect(screen.getByText("há 12 min")).toBeTruthy());
});

describe("o 'há N min' acompanha o relógio com a tela aberta (um intervalo de 1 min para a tela toda)", () => {
  const SYNC = Date.parse("2026-10-05T12:00:00Z");
  const subir = async (aposRelogio: () => void = () => {}) => {
    jest.useFakeTimers({ now: SYNC + 30_000 });
    aposRelogio();
    servidor([banco(11, "Nubank", { last_sync_at: "2026-10-05T12:00:00Z" })]);
    renderRouter("./app", { initialUrl: "/conexoes" });
    await abrirBanco();
    await waitFor(() => expect(screen.getByText("agora")).toBeTruthy());
  };
  const passar = (ms: number) => act(async () => { jest.advanceTimersByTime(ms); await drenar(); });
  afterEach(() => jest.useRealTimers());

  it("30 s depois da sincronização é 'agora'; 5 min adiante, sem tocar em nada, 'há 5 min'", async () => {
    await subir();
    await passar(5 * 60_000);
    expect(screen.getByText("há 5 min")).toBeTruthy();
    expect(screen.queryByText("agora")).toBeNull();
  });
  it("em segundo plano o intervalo não corre; ao voltar o relógio acerta na hora", async () => {
    await subir();
    await appVai("background");
    jest.setSystemTime(SYNC + 10 * 60_000);
    await passar(5 * 60_000);
    expect(screen.getByText("agora")).toBeTruthy(); // parado: nenhum tique rodou
    await appVai("active");
    expect(screen.getByText("há 15 min")).toBeTruthy();
  });
  it("desmontar limpa o intervalo: 5 min depois ninguém mais lê o relógio", async () => {
    await subir();
    screen.unmount();
    const relogio = jest.spyOn(Date, "now");
    await passar(5 * 60_000);
    expect(relogio).not.toHaveBeenCalled();
  });
});

