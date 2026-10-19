// O detalhe do banco (uma Folha aberta por ESTADO de /conexoes): faixa, ações por estado, o banco que some, desconectar e a falha DENTRO da folha.
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { Alert, StyleSheet } from "react-native";
import type { Conexao } from "@/api/schemas/openFinance";
import * as servicosOF from "@/services/openFinance";
import { guardarCredenciais } from "@/storage/secure";
import { chamadas, prepararCaso, resposta, rotear, S } from "./auth_apoio";
import { banco, INTER, NUBANK } from "./open_finance_bancos_apoio";
import { abrirBanco, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

jest.mock("react-native-pluggy-connect", () => ({ PluggyConnect: () => null }));

const FAIXA = "Conexão regulamentada pelo Banco Central, oficial pelo Open Finance.";
const estado = (state: string, label = state): Partial<Conexao> => ({ ui: { state, label, detail: null } });
function servidor(lista: Conexao[], opcoes: { max?: number; excluir?: () => Response | Promise<Response> } = {}) {
  rotear({
    "/open-finance/1": () => resposta(200, { connections: lista }),
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: opcoes.max ?? 3, em_uso: lista.length, pode_adicionar: false, code: null, message: null }),
    "/open-finance/1/connections/11": () => { if (opcoes.excluir) return opcoes.excluir(); lista = lista.filter((c) => c.id !== 11); return resposta(200, { ok: true, deleted: 1 }); },
    "/api/app/bancos": () => resposta(404, {}),
    "/auth/refresh": () => resposta(401, {}),
  });
  return { tirar: (id: number) => { lista = lista.filter((c) => c.id !== id); }, pôr: (c: Conexao) => { lista = [...lista, c]; } };
}
const confirmar = () => jest.spyOn(Alert, "alert").mockImplementation((_t, _m, botoes) => { botoes?.find((b) => b.style === "destructive")?.onPress?.(); });
const fechar = () => act(async () => { fireEvent.press(screen.getByRole("button", { name: "Fechar" })); await drenar(); });
const puxar = () => act(async () => { screen.getAllByTestId("tela")[0]!.props.refreshControl.props.onRefresh(); await drenar(); });
beforeEach(async () => { prepararCaso(); desligarTrava(); await guardarCredenciais(S); });
afterEach(() => jest.restoreAllMocks());

it("a faixa do Banco Central só existe no detalhe, nunca na lista", async () => {
  servidor([NUBANK]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
  expect(screen.queryByText(FAIXA)).toBeNull();
  await abrirBanco();
  expect(screen.getByText(FAIXA)).toBeTruthy();
});

it("o banco some entre a lista e o detalhe: a folha fecha e a tela diz por quê (mede a tela; o efeito que zera `detalheId` é medido no caso seguinte)", async () => {
  const s = servidor([NUBANK, INTER]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await abrirBanco();
  s.tirar(11); // outra sessão o removeu
  await puxar();
  await waitFor(() => expect(screen.queryByTestId("folha-sheet")).toBeNull());
  expect(screen.getByText("Esse banco não está mais conectado.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^Nubank,/ })).toBeNull();
  expect(screen.getByRole("button", { name: /^Inter,/ })).toBeTruthy();
  await abrirBanco("Inter"); // abrir outro limpa o aviso
  expect(screen.queryByText("Esse banco não está mais conectado.")).toBeNull();
});

it("o banco some e REAPARECE: a folha não reabre sozinha (sem `setDetalheId(null)` no efeito, o id pendurado a reabriria)", async () => {
  const s = servidor([NUBANK, INTER]);
  renderRouter("./app", { initialUrl: "/conexoes" });
  await abrirBanco();
  s.tirar(11);
  await puxar();
  await waitFor(() => expect(screen.getByText("Esse banco não está mais conectado.")).toBeTruthy());
  s.pôr(NUBANK); // outra sessão o conectou de novo, com o mesmo id
  await puxar();
  await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
  expect(screen.queryByTestId("folha-sheet")).toBeNull();
  expect(screen.queryByText("Esse banco não está mais conectado.")).toBeNull(); // o banco voltou: o aviso de que ele sumiu não vale mais
});

it("desconectar pelo detalhe: DELETE, a folha fecha, 'Nubank desconectado.' e a lista sem o banco", async () => {
  servidor([NUBANK, INTER]); confirmar();
  renderRouter("./app", { initialUrl: "/conexoes" });
  await abrirBanco();
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
  await waitFor(() => expect(screen.queryByTestId("folha-sheet")).toBeNull());
  expect(screen.getByText("Nubank desconectado.")).toBeTruthy();
  expect(screen.queryByText("Esse banco não está mais conectado.")).toBeNull(); // foi a remoção desta tela, não outra sessão
  await waitFor(() => expect(screen.queryByRole("button", { name: /^Nubank,/ })).toBeNull());
  expect(screen.getByRole("button", { name: /^Inter,/ })).toBeTruthy();
  expect(screen.getByText("Nubank desconectado.")).toBeTruthy(); // sobrevive à releitura que tirou o banco da lista
  expect(chamadas().filter((c) => c.caminho === "/open-finance/1/connections/11")).toHaveLength(1);
});

it("a falha do DELETE aparece DENTRO da folha (que cobre a tela); sem a prop `falha` ficaria atrás", async () => {
  servidor([NUBANK], { excluir: () => resposta(500, { detail: "banco fora do ar" }) }); confirmar();
  renderRouter("./app", { initialUrl: "/conexoes" });
  await abrirBanco();
  const folha = within(screen.getByTestId("folha-sheet"));
  const FALHA = "Tivemos um problema aqui. Tente de novo em instantes.";
  expect(folha.queryByText(FALHA)).toBeNull();
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
  await waitFor(() => expect(folha.getByText(FALHA)).toBeTruthy());
  expect(screen.getByTestId("folha-sheet")).toBeTruthy(); // continua aberta para tentar de novo
  expect(folha.getByRole("button", { name: "Desconectar Nubank" })).toBeEnabled();
});

describe("a remoção é de UMA conexão: o resultado dela não mexe na folha de OUTRO banco", () => {
  const FALHA = "Tivemos um problema aqui. Tente de novo em instantes.";
  const desconectarNubank = async () => { await abrirBanco(); await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); }); };
  it("falha do Nubank, fecha e abre o Inter: a folha do Inter não herda o erro (e o do Nubank reaparece ao reabri-lo)", async () => {
    servidor([NUBANK, INTER], { excluir: () => resposta(500, { detail: "fora do ar" }) }); confirmar();
    renderRouter("./app", { initialUrl: "/conexoes" });
    await desconectarNubank();
    await waitFor(() => expect(screen.getByText(FALHA)).toBeTruthy());
    await fechar();
    await abrirBanco("Inter");
    expect(within(screen.getByTestId("folha-sheet")).queryByText(FALHA)).toBeNull();
    await fechar();
    await abrirBanco("Nubank"); // controle positivo: o erro é do Nubank e volta com ele
    expect(within(screen.getByTestId("folha-sheet")).getByText(FALHA)).toBeTruthy();
  });
  it("o DELETE do Nubank falha DEPOIS de abrir o Inter: a folha do Inter fica limpa; fechada, a tela mostra o erro", async () => {
    let soltar!: () => void; const pend = new Promise<void>((ok) => { soltar = ok; });
    servidor([NUBANK, INTER], { excluir: async () => { await pend; return resposta(500, { detail: "fora do ar" }); } }); confirmar();
    renderRouter("./app", { initialUrl: "/conexoes" });
    await desconectarNubank();
    await fechar();
    await abrirBanco("Inter");
    await act(async () => { soltar(); await drenar(); });
    await waitFor(() => expect(chamadas().filter((c) => c.caminho === "/open-finance/1").length).toBeGreaterThan(1)); // a conferência da remoção rodou
    expect(within(screen.getByTestId("folha-sheet")).queryByText(FALHA)).toBeNull();
    await fechar();
    await waitFor(() => expect(screen.getByText(FALHA)).toBeTruthy());
  });
  it("o DELETE do Nubank dá certo DEPOIS de abrir o Inter: a folha do Inter continua aberta e o aviso do Nubank aparece", async () => {
    let soltar!: () => void; const pend = new Promise<void>((ok) => { soltar = ok; });
    const s = servidor([NUBANK, INTER], { excluir: async () => { await pend; s.tirar(11); return resposta(200, { ok: true, deleted: 1 }); } }); confirmar();
    renderRouter("./app", { initialUrl: "/conexoes" });
    await desconectarNubank();
    await fechar();
    await abrirBanco("Inter");
    await act(async () => { soltar(); await drenar(); });
    await waitFor(() => expect(screen.getByText("Nubank desconectado.")).toBeTruthy());
    expect(within(screen.getByTestId("folha-sheet")).getByRole("button", { name: "Desconectar Inter" })).toBeTruthy();
  });
});

describe("a falha da folha é de UMA leitura de estado: sair da tela, tentar de novo, reler e trocar de conta a zeram", () => {
  const FALHA = "Tivemos um problema aqui. Tente de novo em instantes.";
  const desconectarNubank = async () => { await abrirBanco(); await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); }); await waitFor(() => expect(screen.getByText(FALHA)).toBeTruthy()); };
  const ESCONDIDO = { includeHiddenElements: true };
  it("sair da tela zera a falha NA HORA (a tela de baixo não guarda o banner) e voltar não a mostra", async () => {
    servidor([NUBANK], { excluir: () => resposta(500, { detail: "fora do ar" }) }); confirmar();
    renderRouter("./app", { initialUrl: "/conexoes" });
    await desconectarNubank();
    await act(async () => { router.push("/configuracoes"); await drenar(); });
    await waitFor(() => expect(screen).toHavePathname("/configuracoes"));
    expect(screen.queryByText(FALHA, ESCONDIDO)).toBeNull();
    await act(async () => { router.back(); await drenar(); });
    await waitFor(() => expect(screen).toHavePathname("/conexoes"));
    expect(screen.queryByText(FALHA)).toBeNull();
  });
  it("falha e tenta de novo: o banner velho some ao começar a nova tentativa (ainda em voo) e, no sucesso, só 'Nubank desconectado.'", async () => {
    let n = 0, soltar!: () => void; const pend = new Promise<void>((ok) => { soltar = ok; });
    const s = servidor([NUBANK], { excluir: async () => { if (++n === 1) return resposta(500, { detail: "fora do ar" }); await pend; s.tirar(11); return resposta(200, { ok: true, deleted: 1 }); } }); confirmar();
    renderRouter("./app", { initialUrl: "/conexoes" });
    await desconectarNubank();
    await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Desconectar Nubank" })); await drenar(); });
    expect(n).toBe(2);
    expect(screen.queryByText(FALHA)).toBeNull(); // a 2ª tentativa está pendurada: o erro da 1ª já não vale
    await act(async () => { soltar(); await drenar(); });
    await waitFor(() => expect(screen.getByText("Nubank desconectado.")).toBeTruthy());
    expect(screen.queryByText(FALHA)).toBeNull();
  });
  it("A: falha, fecha a folha e puxa para atualizar com leitura OK: o banner some", async () => {
    servidor([NUBANK], { excluir: () => resposta(500, { detail: "fora do ar" }) }); confirmar();
    renderRouter("./app", { initialUrl: "/conexoes" });
    await desconectarNubank();
    await fechar();
    expect(screen.getByText(FALHA)).toBeTruthy(); // controle: com a folha fechada a tela o mostra
    await puxar();
    await waitFor(() => expect(screen.queryByText(FALHA)).toBeNull());
    expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy();
  });
  it("B: falha e a conta troca (403 de uid alheio): o banner da conta A não aparece na conta B", async () => {
    const cofre = (globalThis as unknown as { __cofreDeTeste: Map<string, string> }).__cofreDeTeste;
    let conta = 1, trocar = false;
    const limite = { ok: true, of_banks_max: 2, em_uso: 1, pode_adicionar: false, code: null, message: null };
    const daB = (o: RequestInit) => /access-b/.test((o.headers as Record<string, string>)["Authorization"] ?? "");
    rotear({
      "/auth/me": () => resposta(200, { user_id: conta, email: "sofia@x.com", display_name: "Sofia", app_access: true }),
      "/open-finance/1": (o) => (daB(o) ? resposta(403, { detail: "Acesso negado" }) : resposta(200, { connections: [NUBANK] })),
      "/open-finance/1/limite": (o) => (daB(o) ? resposta(403, { detail: "Acesso negado" }) : resposta(200, limite)),
      "/open-finance/1/connections/11": () => resposta(500, { detail: "fora do ar" }),
      "/open-finance/2": () => resposta(200, { connections: [banco(21, "Itaú")] }),
      "/open-finance/2/limite": () => resposta(200, limite),
      "/api/app/bancos": () => resposta(404, {}),
    }); confirmar();
    renderRouter("./app", { initialUrl: "/conexoes" });
    await desconectarNubank();
    await fechar();
    const real = servicosOF.conexoes as (...a: never[]) => Promise<unknown>;
    jest.spyOn(servicosOF, "conexoes").mockImplementation(((...a: never[]) => {
      if (trocar) { trocar = false; cofre.set("pb.credenciais", JSON.stringify({ access: "access-b", refresh: "rt_b" })); conta = 2; }
      return real(...a);
    }) as never);
    trocar = true;
    await puxar();
    await waitFor(() => expect(screen.getByRole("button", { name: /^Itaú,/ })).toBeTruthy());
    expect(screen.queryByText(FALHA)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Nubank,/ })).toBeNull();
  });
  it("C: falha, outra sessão remove o banco e a leitura vem: só 'não está mais conectado', sem o banner do banco que não existe", async () => {
    const s = servidor([NUBANK, INTER], { excluir: () => resposta(500, { detail: "fora do ar" }) }); confirmar();
    renderRouter("./app", { initialUrl: "/conexoes" });
    await desconectarNubank();
    s.tirar(11);
    await puxar();
    await waitFor(() => expect(screen.getByText("Esse banco não está mais conectado.")).toBeTruthy());
    expect(screen.queryByText(FALHA)).toBeNull();
  });
});

it("Reconectar leva a /autorizando?itemId e a folha não reabre ao voltar; Acompanhar leva a /open-finance-volta modo acompanhar", async () => {
  servidor([banco(11, "Nubank", estado("updating", "Atualizando…"))]);
  const r = renderRouter("./app", { initialUrl: "/conexoes" });
  await abrirBanco();
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Reconectar Nubank" })); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname("/autorizando"));
  expect(r.getSearchParams()).toEqual({ itemId: "item_11" });
  await act(async () => { router.back(); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname("/conexoes"));
  expect(screen.queryByTestId("folha-sheet")).toBeNull();
  await abrirBanco();
  await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Acompanhar sincronização" })); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname("/open-finance-volta"));
  expect(r.getSearchParams()).toEqual({ itemId: "item_11", modo: "acompanhar" });
  await act(async () => { router.back(); await drenar(); });
  await waitFor(() => expect(screen).toHavePathname("/conexoes"));
  expect(screen.queryByTestId("folha-sheet")).toBeNull();
});

describe("ações por estado", () => {
  const botoes = (nome: string) => ({ rec: screen.queryByRole("button", { name: `Reconectar ${nome}` }), acomp: screen.queryByRole("button", { name: "Acompanhar sincronização" }), des: screen.queryByRole("button", { name: `Desconectar ${nome}` }) });
  const fundo = (el: { children: unknown[] }) => (StyleSheet.flatten((el.children[0] as { props: { style: unknown } }).props.style as object) as { backgroundColor?: string }).backgroundColor;
  it.each([["paused"], ["removed"], ["item_missing"]])("%s: sem Reconectar, só Desconectar", async (state) => {
    servidor([banco(11, "Nubank", estado(state))]);
    renderRouter("./app", { initialUrl: "/conexoes" });
    await abrirBanco();
    const b = botoes("Nubank");
    expect([!!b.rec, !!b.acomp, !!b.des]).toEqual([false, false, true]);
  });
  it("of_banks_max 0 (plano sem Open Finance): sem Reconectar, mesmo atualizado", async () => {
    servidor([NUBANK], { max: 0 });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await abrirBanco();
    expect([!!botoes("Nubank").rec, !!botoes("Nubank").des]).toEqual([false, true]);
  });
  it("of_banks_max 0 não escreve 'de 0 bancos' na lista; com vagas escreve (controle positivo)", async () => {
    servidor([NUBANK], { max: 0 });
    const t = renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByRole("button", { name: /^Nubank,/ })).toBeTruthy());
    await act(drenar);
    expect(screen.queryByLabelText(/de 0 bancos/)).toBeNull();
    t.unmount();
    servidor([NUBANK], { max: 3 });
    renderRouter("./app", { initialUrl: "/conexoes" });
    await waitFor(() => expect(screen.getByLabelText("1 de 3 bancos")).toBeTruthy());
  });
  it("sem provider_item_id: sem Reconectar nem Acompanhar", async () => {
    servidor([banco(11, "Nubank", { ...estado("updating"), provider_item_id: null })]);
    renderRouter("./app", { initialUrl: "/conexoes" });
    await abrirBanco();
    const b = botoes("Nubank");
    expect([!!b.rec, !!b.acomp, !!b.des]).toEqual([false, false, true]);
  });
  it("updating: Acompanhar e Reconectar; atualizado: só Reconectar (secundário); needs_user_action: Reconectar PRIMÁRIO (outro fundo)", async () => {
    const fundos: Record<string, unknown> = {};
    for (const [state, acomp] of [["updating", true], ["updated", false], ["needs_user_action", false]] as const) {
      servidor([banco(11, "Nubank", estado(state))]);
      const t = renderRouter("./app", { initialUrl: "/conexoes" });
      await abrirBanco();
      const b = botoes("Nubank");
      expect([!!b.rec, !!b.acomp]).toEqual([true, acomp]);
      fundos[state] = fundo(b.rec as never);
      t.unmount();
    }
    expect(fundos["needs_user_action"]).not.toEqual(fundos["updated"]);
    expect(fundos["updating"]).toEqual(fundos["updated"]);
  });
});
