import { Platform, ScrollView } from "react-native";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import v2 from "../../../tests/frontend/api_v2_respostas.json";
// A folha do simulador de compra (aba Gastos, só Pro): `renderRouter` + servidor falso. O seletor de data é nativo: o dublê basta.
const mockAbrirData = jest.fn();
jest.mock("@react-native-community/datetimepicker", () => ({ __esModule: true, DateTimePickerAndroid: { open: (o: unknown) => mockAbrirData(o) }, default: (p: object) => jest.requireActual("react").createElement(jest.requireActual("react-native").View, { testID: "seletor-data", ...p }) }));
jest.setTimeout(30_000);
type Sim = { cenarios: { nome: string; resumo: { saldo_final: string | null; delta_vs_atual: string | null; pior_dia: unknown; trajetoria: { data: string; saldo: string | null }[] } & Record<string, unknown>; contrato: Record<string, unknown> }[]; atual: { trajetoria: { data: string; saldo: string | null }[] }; premissas: string[] } & Record<string, unknown>;
const SIM = v2.simulador.compra as unknown as Sim, clone = (f?: (s: Sim) => void) => { const s = structuredClone(SIM); f?.(s); return s; };
let plano: string, simular: (corpo: Record<string, unknown>) => Response | Promise<Response>;
const posts = () => fetchFalso.mock.calls.filter(([u, o]) => (o as RequestInit)?.method === "POST" && new URL(String(u)).pathname === "/api/app/simulador").map(([, o]) => JSON.parse(String((o as RequestInit).body)) as Record<string, unknown>);
function servidor() {
  fetchFalso.mockImplementation(async (url: string, o: RequestInit) => {
    const path = new URL(url).pathname;
    if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
    if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
    if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
    if (path === "/api/app/me") return resposta(200, { plan_tier: plano });
    if (path === "/api/app/simulador" && o.method === "POST") return simular(JSON.parse(String(o.body)));
    if (path === "/auth/refresh") return resposta(401, { detail: "invalid_refresh_token" });
    return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
  });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); plano = "pro"; simular = () => resposta(200, SIM); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor(); });
const apertar = async (nome: string | RegExp) => { await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); };
const digitar = (rotulo: RegExp, valor: string, i = 0) => fireEvent.changeText(screen.getAllByLabelText(rotulo)[i]!, valor);
let base = 0; // a aba Gastos segue montada atrás da folha e tem os seus traços (donut): só se conta o que a folha acrescenta
const nPaths = () => screen.UNSAFE_root.findAll((n) => String(n.type) === "RNSVGPath").length - base;
const erro = (status: number, code: string, details?: unknown) => resposta(status, { error: { code, message: "texto cru em inglês", ...(details ? { details } : {}) } });
async function abrirGastos() { renderRouter("./app", { initialUrl: "/gastos" }); await waitFor(() => expect(screen.getByTestId("bloco-simulador")).toBeTruthy()); await waitFor(() => expect(fetchFalso.mock.calls.some(([u]) => new URL(String(u)).pathname === "/api/app/me")).toBe(true)); await act(drenar); }
async function abrirFolha() { await abrirGastos(); await waitFor(() => expect(screen.getByRole("button", { name: "Simular uma compra" })).toBeTruthy()); await apertar("Simular uma compra"); await waitFor(() => expect(screen.getByText("Simular compra")).toBeTruthy()); await waitFor(() => expect(screen.getAllByLabelText(/^Nome/).length).toBeGreaterThan(0)); base = 0; base = nPaths(); }
const preencher = () => { digitar(/^Nome/, "Celular"); digitar(/^Preço/, "100000"); };
const calcular = () => apertar("Calcular");
const resultado = async () => { await abrirFolha(); preencher(); await calcular(); await waitFor(() => expect(screen.getByRole("button", { name: "Editar" })).toBeTruthy()); };

describe("gate Pro", () => {
  it("essencial: sem o botão 'Simular uma compra' e nenhum POST; Pro: o botão existe e abre a folha", async () => {
    plano = "essencial"; await abrirGastos();
    await waitFor(() => expect(screen.getByText("Simulador indisponível no seu plano.")).toBeTruthy()); // o plano chegou
    expect(screen.queryByRole("button", { name: "Simular uma compra" })).toBeNull(); expect(posts()).toHaveLength(0);
  });
  it("positivo: com Pro o botão abre a folha (a sheet rola)", async () => {
    await abrirFolha(); expect(screen.getByTestId("sheet-conteudo").type).toBe("RCTScrollView"); expect(posts()).toHaveLength(0);
  });
});

describe("envio", () => {
  it("digitar em todos os campos não envia; 'Calcular' envia UM POST com o corpo exato (dinheiro em texto, parcelas número, juros com ponto, sem null)", async () => {
    await abrirFolha(); preencher(); digitar(/^Parcelas/, "12"); digitar(/^Reserva mínima/, "5000");
    await apertar(/Mais opções/); digitar(/^Entrada/, "20000"); digitar(/^Juros/, "1,99"); await act(drenar);
    expect(posts()).toHaveLength(0);
    await calcular();
    await waitFor(() => expect(posts()).toEqual([{ reserva_minima: "50.00", cenarios: [{ nome: "Celular", preco: "1000.00", entrada: "200.00", juros_mensal_pct: "1.99", parcelas: 12 }] }]));
  });
  it("dois toques no MESMO tick, com a resposta presa, enviam UM POST", async () => {
    await abrirFolha(); preencher(); const preso = segurar(); simular = async () => { await preso.promessa; return resposta(200, SIM); };
    const botao = screen.getByRole("button", { name: "Calcular" });
    await act(async () => { fireEvent.press(botao); fireEvent.press(botao); await drenar(); });
    expect(posts()).toHaveLength(1); preso.soltar(); await act(drenar); expect(posts()).toHaveLength(1);
  });
  it("com o POST em voo a edição é ignorada: o que está na tela é o que foi calculado", async () => {
    await abrirFolha(); preencher(); const preso = segurar(); simular = async () => { await preso.promessa; return resposta(200, SIM); };
    await calcular(); digitar(/^Nome/, "Outro"); expect(screen.getAllByLabelText(/^Nome/)[0]!.props.value).toBe("Celular"); preso.soltar(); await act(drenar);
  });
  it("android: o diálogo de data aberto ANTES do Calcular não altera o rascunho quando dispara com o POST em voo", async () => {
    const so = Platform.OS; Platform.OS = "android"; mockAbrirData.mockClear();
    try {
      await abrirFolha(); preencher(); await apertar("Data da compra"); const { onChange } = mockAbrirData.mock.calls[0]![0] as { onChange: (e: object, d: Date) => void };
      const preso = segurar(); simular = async () => { await preso.promessa; return resposta(200, SIM); }; await calcular();
      act(() => onChange({ type: "set" }, new Date(2031, 0, 15))); preso.soltar(); await act(drenar);
      await waitFor(() => expect(screen.getByRole("button", { name: "Editar" })).toBeTruthy()); await apertar("Editar");
      expect(screen.queryByText("15/01/2031")).toBeNull();
      act(() => onChange({ type: "set" }, new Date(2031, 0, 15))); expect(screen.getByText("15/01/2031")).toBeTruthy(); // controle: sem POST em voo o mesmo evento vale
    } finally { Platform.OS = so; }
  });
  it("editar um campo limpa o erro da tela", async () => {
    await abrirFolha(); preencher(); simular = () => resposta(500, undefined); await calcular();
    await waitFor(() => expect(screen.getByText("Não deu para calcular. Tente de novo.")).toBeTruthy());
    digitar(/^Nome/, "Outro"); expect(screen.queryByText("Não deu para calcular. Tente de novo.")).toBeNull();
  });
});

describe("resultado", () => {
  it("aparece no lugar do formulário: nome, saldo final, as duas curvas e a legenda; 'Editar' devolve o rascunho", async () => {
    await resultado();
    expect(screen.getByText("À vista")).toBeTruthy(); expect(screen.getAllByLabelText("600 reais").length).toBeGreaterThan(0); expect(screen.getByText(/^Menor saldo · 08\/10/)).toBeTruthy();
    expect(nPaths()).toBe(2); expect(screen.getByText("Com")).toBeTruthy(); expect(screen.getByText("Sem")).toBeTruthy();
    expect(screen.queryAllByLabelText(/^Preço/)).toHaveLength(0); expect(screen.queryByRole("button", { name: "Calcular" })).toBeNull();
    await apertar("Editar"); expect(screen.getAllByLabelText(/^Nome/)[0]!.props.value).toBe("Celular"); expect(screen.getAllByLabelText(/^Preço/)[0]!.props.value).toBe("1.000,00"); expect(nPaths()).toBe(0); expect(screen.queryByText("Com")).toBeNull();
  });
  it("saldo null aparece como 'Não informado' e a curva some: nunca R$ 0,00", async () => {
    simular = () => resposta(200, clone((s) => { const c = s.cenarios[0]!; c.resumo.saldo_final = null; c.resumo.delta_vs_atual = null; c.resumo.pior_dia = null; c.resumo.trajetoria = c.resumo.trajetoria.map((p) => ({ ...p, saldo: null })); c.contrato.total_pago = "100.00"; c.contrato.juros_totais = "0.00"; }));
    await resultado();
    expect(screen.getAllByText("Não informado").length).toBeGreaterThanOrEqual(3); expect(screen.getByText("Trajetória indisponível.")).toBeTruthy(); expect(nPaths()).toBe(0);
    expect(screen.queryAllByLabelText(/^zero reais/)).toHaveLength(0); expect(screen.queryByText(/R\$\s*0,00/)).toBeNull();
  });
  it.each([["90 pontos contra 91", (s: Sim) => { s.cenarios[0]!.resumo.trajetoria = s.cenarios[0]!.resumo.trajetoria.slice(0, 90); }], ["mesmo tamanho, datas diferentes", (s: Sim) => { s.cenarios[0]!.resumo.trajetoria[5]!.data = "2030-01-01"; }]])("trajetórias desalinhadas (%s): sem curva", async (_, f) => {
    simular = () => resposta(200, clone(f)); await resultado(); expect(nPaths()).toBe(0); expect(screen.getByText("Trajetória indisponível.")).toBeTruthy();
  });
  it("parcelado: 'N× parcela', juros e parcelas depois do horizonte; estado indisponível: aviso e sem curva", async () => {
    simular = () => resposta(200, clone((s) => { const k = s.cenarios[0]!.contrato; Object.assign(k, { a_vista: false, parcelas: 12, parcela: "50.00", juros_totais: "12.00", parcelas_fora_do_horizonte: { quantidade: 3, valor: "150.00" } }); s.estado = "indisponivel"; }));
    await resultado(); expect(screen.getByText("12× parcela")).toBeTruthy(); expect(screen.getByText("Juros")).toBeTruthy(); expect(screen.getByText("3 parcelas depois de 90 dias")).toBeTruthy();
    expect(screen.getByText("Previsão indisponível")).toBeTruthy(); expect(nPaths()).toBe(0);
  });
  it("reserva > 0: condicional mostra '0 dias abaixo da reserva' (controle); indisponível (saldo null) esconde a linha: dado ausente não vira zero", async () => {
    await resultado(); expect(screen.getByText("0 dias abaixo da reserva")).toBeTruthy();
    simular = () => resposta(200, clone((s) => { s.estado = "indisponivel"; const c = s.cenarios[0]!; c.resumo.saldo_final = null; c.resumo.delta_vs_atual = null; c.resumo.pior_dia = null; c.resumo.trajetoria = c.resumo.trajetoria.map((p) => ({ ...p, saldo: null })); }));
    await apertar("Editar"); await calcular(); await waitFor(() => expect(screen.getByText("Previsão indisponível")).toBeTruthy());
    expect(screen.queryByText(/abaixo da reserva/)).toBeNull();
  });
  it("reserva > 0, estado condicional mas saldo_final null (sem saldo de partida): a linha 'abaixo da reserva' some", async () => {
    simular = () => resposta(200, clone((s) => { const c = s.cenarios[0]!; c.resumo.saldo_final = null; c.resumo.trajetoria = c.resumo.trajetoria.map((p, i) => (i === 0 ? { ...p, saldo: null } : p)); }));
    await resultado(); expect(screen.getByRole("button", { name: "Editar" })).toBeTruthy(); expect(screen.queryByText(/abaixo da reserva/)).toBeNull();
  });
  it("ocultar valores (a escolha da aba vale na folha): 'Gráfico oculto' e nenhum valor à mostra", async () => {
    await abrirGastos(); await apertar("Ocultar valores"); await apertar("Simular uma compra"); await waitFor(() => expect(screen.getByText("Simular compra")).toBeTruthy());
    preencher(); await calcular(); await waitFor(() => expect(screen.getByRole("button", { name: "Editar" })).toBeTruthy());
    expect(screen.getByText("Gráfico oculto")).toBeTruthy(); expect(screen.queryAllByLabelText("600 reais")).toHaveLength(0);
  });
});

describe("resultado: premissas e legendas", () => {
  it("premissas (recolhível): estado, motivo com direção e a premissa da resposta; fechadas, nada disso aparece", async () => {
    simular = () => resposta(200, clone((s) => { s.motivos = [{ codigo: "banco_desatualizado", direcao_do_erro: "so_piora" }]; }));
    await resultado(); expect(screen.queryByText("Previsão condicional")).toBeNull();
    await apertar("Premissas");
    expect(screen.getByText("Previsão condicional")).toBeTruthy(); expect(screen.getByText("banco desatualizado")).toBeTruthy(); expect(screen.getByText("o real pode ser melhor")).toBeTruthy(); expect(screen.getByText(SIM.premissas[0]!)).toBeTruthy();
  });
  it.each([["bancos_excluidos", "Bancos conectados fora do cálculo", (s: Sim, v: boolean) => { (s.base as { bancos_excluidos: boolean }).bancos_excluidos = v; }], ["compra_fora_da_janela", "Compra depois de 90 dias: o saldo não muda", (s: Sim, v: boolean) => { s.cenarios[0]!.resumo.compra_fora_da_janela = v; }]] as const)("legenda '%s': ligada aparece, desligada não", async (_, texto, ajusta) => {
    simular = () => resposta(200, clone((s) => ajusta(s, true))); await resultado(); expect(screen.getByText(texto)).toBeTruthy();
    simular = () => resposta(200, clone((s) => ajusta(s, false))); await apertar("Editar"); await calcular(); await waitFor(() => expect(screen.queryByText(texto)).toBeNull()); expect(screen.getByRole("button", { name: "Editar" })).toBeTruthy();
  });
  it("datas: o dia de outro ano que o 'hoje' leva o ano (hoje em novembro: 90 dias caem em 2027); do mesmo ano, sem ano", async () => {
    simular = () => resposta(200, clone((s) => { s.hoje = "2026-11-20"; })); await resultado();
    expect(screen.getByText("90 dias · 04/01/27")).toBeTruthy(); expect(screen.getByText("A partir de hoje, 20/11")).toBeTruthy(); expect(screen.getByText(/^Menor saldo · 08\/10$/)).toBeTruthy();
  });
});

describe("rolagem da folha", () => {
  it("resultado e 'Editar' levam a rolagem ao topo; um 422 e a digitação não rolam (o erro fica perto do campo)", async () => {
    const rolagem = jest.spyOn(ScrollView.prototype, "scrollTo").mockImplementation(() => undefined);
    try {
      await abrirFolha(); preencher(); rolagem.mockClear(); digitar(/^Nome/, "Celular 2");
      simular = () => erro(422, "validation_error", [{ loc: ["body", "cenarios", 0, "preco"], msg: "x", type: "preco_invalido" }]); await calcular();
      await waitFor(() => expect(screen.getByLabelText(/^Preço.*erro/)).toBeTruthy()); expect(rolagem).not.toHaveBeenCalled();
      simular = () => resposta(200, SIM); await calcular(); await waitFor(() => expect(screen.getByRole("button", { name: "Editar" })).toBeTruthy());
      expect(rolagem.mock.calls).toEqual([[{ y: 0, animated: false }]]);
      await apertar("Editar"); expect(rolagem.mock.calls).toEqual([[{ y: 0, animated: false }], [{ y: 0, animated: false }]]);
      rolagem.mockClear(); simular = () => erro(403, "pro_required"); await calcular();
      await waitFor(() => expect(screen.queryByRole("button", { name: "Calcular" })).toBeNull()); expect(rolagem.mock.calls).toEqual([[{ y: 0, animated: false }]]);
    } finally { rolagem.mockRestore(); }
  });
});

describe("falhas", () => {
  it("422 por campo (2 cenários): o erro cai na Entrada do cenário 2 (Mais opções abre sozinha); o cenário 1 fica limpo; os valores seguem nos campos", async () => {
    await abrirFolha(); preencher(); await apertar("Comparar com outro cenário"); digitar(/^Nome/, "Moto", 1); digitar(/^Preço/, "50000", 1);
    simular = () => erro(422, "validation_error", [{ loc: ["body", "cenarios", 1, "entrada"], msg: "Value error, x", type: "entrada_maior_que_preco" }]); await calcular();
    await waitFor(() => expect(screen.getByLabelText(/^Entrada.*erro: Maior que o preço/)).toBeTruthy());
    expect(screen.getAllByLabelText(/^Entrada/)).toHaveLength(1); expect(screen.queryByText("Confira os campos.")).toBeNull(); expect(screen.queryByText(/texto cru|Value error/)).toBeNull();
    expect(screen.getAllByLabelText(/^Nome/).map((n) => n.props.value)).toEqual(["Celular", "Moto"]); expect(posts()[0]!.cenarios).toHaveLength(2);
  });
  it.each([
    ["500", () => resposta(500, undefined), "Não deu para calcular. Tente de novo."], ["rede", () => { throw new TypeError("Network request failed"); }, "Não deu para calcular. Tente de novo."],
    ["429", () => erro(429, "rate_limited"), "Muitas simulações. Espere um pouco."], ["200 fora do contrato", () => resposta(200, { hoje: "2026-10-06" }), "Não deu para calcular. Tente de novo."],
    ["onboarding", () => erro(403, "open_finance_onboarding_required"), "Conecte seu banco para simular."],
  ] as const)("%s: formulário com erro geral e o rascunho intacto, sem resultado e sem curva; o botão fica livre", async (_, falha, texto) => {
    await resultado(); await apertar("Editar"); digitar(/^Nome/, "Outro"); simular = falha; await calcular();
    await waitFor(() => expect(screen.getByText(texto)).toBeTruthy());
    expect(screen.getAllByLabelText(/^Nome/)[0]!.props.value).toBe("Outro"); expect(nPaths()).toBe(0); expect(screen.queryByRole("button", { name: "Editar" })).toBeNull(); expect(screen.queryByText("Menor saldo", { exact: false })).toBeNull();
    simular = () => resposta(200, SIM); await calcular(); await waitFor(() => expect(posts()).toHaveLength(3)); // tentar de novo funciona
  });
  it("403 pro_required: o convite no lugar do formulário e nenhum outro POST", async () => {
    await abrirFolha(); preencher(); simular = () => erro(403, "pro_required"); await calcular();
    await waitFor(() => expect(screen.getAllByText("Simulador indisponível no seu plano.").length).toBeGreaterThan(0)); expect(screen.queryByRole("button", { name: "Calcular" })).toBeNull(); expect(posts()).toHaveLength(1);
  });
  it("RequisicaoSuperada (outra conta assume o cofre com o POST no ar) fecha a folha: nada da conta anterior fica na tela", async () => {
    await abrirFolha(); preencher(); simular = async () => { await guardarCredenciais({ access: "access-b", refresh: "rt_b" }); return resposta(200, SIM); };
    await calcular(); await waitFor(() => expect(screen.queryByText("Simular compra")).toBeNull()); expect(screen.queryByText("Não deu para calcular. Tente de novo.")).toBeNull(); expect(screen.queryByRole("button", { name: "Editar" })).toBeNull();
  });
  it("402 fecha a folha (a aba cai no portão)", async () => {
    await abrirFolha(); preencher(); simular = () => erro(402, "subscription_inactive"); await calcular(); await waitFor(() => expect(screen.queryByText("Simular compra")).toBeNull());
  });
  it("fechar com o POST em voo e o 401 chegando depois: a folha já se foi e não derruba a sessão", async () => {
    await abrirFolha(); preencher(); const preso = segurar(); simular = async () => { await preso.promessa; return resposta(401, { detail: "expirado" }); }; await calcular();
    await apertar("Fechar"); await waitFor(() => expect(screen.queryByText("Simular compra")).toBeNull());
    preso.soltar(); await act(drenar); expect(screen).toHavePathname("/gastos"); expect(screen.queryByText("Sua sessão expirou. Entre de novo.")).toBeNull();
  });
  it("401 (com o refresh também 401): sessão expirada, volta a /entrar com o aviso", async () => {
    await abrirFolha(); preencher(); simular = () => resposta(401, { detail: "expirado" }); await calcular();
    await waitFor(() => expect(screen).toHavePathname("/entrar")); expect(screen.getByText("Sua sessão expirou. Entre de novo.")).toBeTruthy();
  });
  it("fechar com o POST em voo: o sinal é abortado, o rascunho se perde e nada lança quando a resposta chega", async () => {
    await abrirFolha(); preencher(); const preso = segurar(); simular = async () => { await preso.promessa; return resposta(200, SIM); }; await calcular();
    const chamada = fetchFalso.mock.calls.filter(([u]) => new URL(String(u)).pathname === "/api/app/simulador").at(-1)![1] as RequestInit; expect(chamada.signal!.aborted).toBe(false);
    await apertar("Fechar"); await waitFor(() => expect(screen.queryByText("Simular compra")).toBeNull()); expect(chamada.signal!.aborted).toBe(true);
    preso.soltar(); await act(drenar); await apertar("Simular uma compra"); await waitFor(() => expect(screen.getAllByLabelText(/^Nome/)[0]!.props.value).toBe("")); // reabrir começa do zero
  });
});
