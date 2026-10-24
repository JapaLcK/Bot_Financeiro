import { act, fireEvent, screen, waitFor, within } from "expo-router/testing-library";
import { metasSchema } from "@/api/schemas/painel";
import { faladoComSinal } from "@/ui/dinheiro";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { abrir, apertar, caminho, enviar, fim, ir, noResumo, reiniciar, respostaFluxo, srv } from "./conversa_apoio";
import v2 from "../../../tests/frontend/api_v2_respostas.json";
import metasBanco from "./painel_metas_banco.fixture.json";
import { CARDS, F, bloco, comBlocos, instalarCards, perguntar } from "./conversa_cards_apoio";
// Os 18 cards do chat desenham o bloco que o servidor embutiu, nunca o provider: o provider destes casos responde 500 (instalarCards).
jest.setTimeout(30_000);
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); instalarCards(); });
afterEach(() => { jest.restoreAllMocks(); });

const PROIBIDOS = ["Tentar novamente", "Carregando dados", "Conferindo disponibilidade do simulador…", "Simulador indisponível no seu plano.", "Dado indisponível neste card."];
// Um texto que SÓ existe se o conteúdo do bloco foi desenhado (positivo: o card não está vazio).
const CONTEUDO: Record<string, string> = { contas: "Nubank", hero: "30 dias · 05/11/2026", compromissos: "Fatura Nubank", resumo: "gasto até hoje", categorias: "Alimentação", calendario: "06/10", simulador: "Transporte", patrimonio: "Histórico desde 01/08/2026", wealth: "Investimentos do banco", rendimento: "Renda fixa", assinaturas: "Netflix", metas: "Viagem pro Japão", fatura: "Limite", parcelas: "Nubank · 2 de 3 pagas", renda: "Saídas", piggy: "Alimentação merece atenção", caixinhas: "Viagem", periodo: "Saldo" };
it("o mapa de conteúdo cobre os 18 cards", () => { expect(CARDS).toHaveLength(18); expect(Object.keys(CONTEUDO).sort()).toEqual([...CARDS].sort()); });
it.each(CARDS)("%s: o card do chat desenha o bloco, sem ler o provider", async (card) => {
  comBlocos(bloco(card)); await perguntar();
  const c = within(screen.getByTestId(`bloco-${card}`));
  for (const t of PROIBIDOS) expect(c.queryByText(t)).toBeNull();
  expect(c.queryByLabelText("Carregando dados")).toBeNull();
  expect(c.getAllByText(CONTEUDO[card]!).length).toBeGreaterThan(0);
});

const contasCom = (total: string) => ({ contas: { ...F["/api/app/contas"], total } });
it("o número do chat é o do bloco, não o do provider", async () => {
  instalarCards(true); comBlocos(bloco("contas", contasCom("4321.09"))); await perguntar();
  const bloco4321 = faladoComSinal(432109), provider9400 = faladoComSinal(940025);
  expect(screen.getAllByLabelText(bloco4321)).toHaveLength(1);
  expect(screen.queryByLabelText(provider9400)).toBeNull(); // o provider responde 9.400,25
});

it("o card é 100% do chat: sem botão de abrir, e tocar nele não abre folha nem modal", async () => {
  comBlocos(bloco("metas")); await perguntar();
  expect(screen.queryByRole("button", { name: /abrir detalhes/ })).toBeNull(); expect(within(screen.getByTestId("bloco-metas")).queryAllByRole("button").map((b) => b.props.accessibilityLabel)).toEqual(["+6, mostrar mais 6 metas"]); // o único botão é o "+N", que expande dentro do card
  fireEvent.press(screen.getByTestId("bloco-metas")); fireEvent.press(within(screen.getByTestId("bloco-metas")).getByText("Metas"));
  expect(screen.queryByRole("button", { name: "Fechar" })).toBeNull(); expect(screen.queryByRole("header", { name: "Metas" })).toBeNull(); // só a Folha tem "Fechar" e o header
  expect(screen.getByTestId("bloco-metas")).toBeTruthy(); expect(screen.getByText("Resposta do Piggy")).toBeTruthy(); // o card segue no chat
});

describe("só leitura no chat; as abas do painel seguem como eram", () => {
  const semPlano = (tier: string) => { const impl = fetchFalso.getMockImplementation()!; fetchFalso.mockImplementation((u: string, r: RequestInit) => caminho(u) === "/api/app/me" ? Promise.resolve(resposta(200, { plan_tier: tier })) : impl(u, r)); };
  it("hero sem os chips de horizonte; na aba, com", async () => {
    instalarCards(true); comBlocos(bloco("hero")); await perguntar();
    expect(within(screen.getByTestId("bloco-hero")).queryByRole("button", { name: "30 dias" })).toBeNull();
    await ir("/resumo"); await waitFor(() => expect(screen.getByRole("button", { name: "30 dias" })).toBeTruthy());
  });
  it("hero no chat mostra tudo inline (âncora, marcos, curva) e tocar nele não abre folha", async () => {
    instalarCards(true); comBlocos(bloco("hero")); await perguntar();
    const c = within(screen.getByTestId("bloco-hero"));
    expect(c.getByText("Hoje, com as premissas")).toBeTruthy(); expect(c.getByText(/^30 dias · /)).toBeTruthy(); expect(c.getByLabelText(/^Evolução dos valores no período/)).toBeTruthy();
    fireEvent.press(screen.getByTestId("bloco-hero")); expect(screen.queryByTestId("folha-sheet")).toBeNull(); expect(screen.queryByRole("button", { name: "Fechar" })).toBeNull();
    expect(c.queryByRole("button", { name: /ver detalhes/ })).toBeNull();
  });
  it("hero no chat não leva a seção 'Receitas na previsão', mesmo com respondidas no bloco; na aba (Resumo) ela é do card", async () => {
    const respondidas = [{ chave: "extrato#alu", nome: "Aluguel Carla", valor: "1200.00", dia: 10, status: "confirmada" }];
    instalarCards(true); comBlocos(bloco("hero", { previsao: { ...F["/api/app/previsao"], receitas_do_banco: respondidas } })); await perguntar();
    expect(within(screen.getByTestId("bloco-hero")).getByText("30 dias · 05/11/2026")).toBeTruthy(); // o card desenhou o bloco
    expect(screen.queryByText(/Receitas na previsão/)).toBeNull(); expect(screen.queryByTestId("receitas-respondidas")).toBeNull();
  });
  it("assinaturas sem Ignorar/É assinatura/Voltar a mostrar/Remover marca; na aba, com", async () => {
    const dado = { ...F["/api/app/assinaturas"] }, outra = { ...dado.servicos[0], chave: "o", nome: "Outra" };
    const todas = { ...dado, servicos: [{ ...dado.servicos[0], marcada: true }], outras: [outra], ignoradas: [{ ...outra, chave: "i", nome: "Ignorada" }] };
    instalarCards(true); comBlocos(bloco("assinaturas", { assinaturas: todas })); await perguntar();
    const c = within(screen.getByTestId("bloco-assinaturas"));
    expect(c.getByText("Netflix")).toBeTruthy(); for (const n of [/Ignorar/, /É assinatura|Marcar .* como assinatura/, /Voltar a mostrar/, /Remover marca|Não é assinatura/]) expect(c.queryByRole("button", { name: n })).toBeNull();
    await ir("/gastos"); await waitFor(() => expect(screen.getAllByRole("button", { name: /Ignorar/ }).length).toBeGreaterThan(0));
  });
  it("simulador no plano free: o chat mostra os sliders (o servidor já aplicou o plano); a aba, 'indisponível'", async () => {
    instalarCards(true); semPlano("free"); comBlocos(bloco("simulador")); await perguntar();
    expect(within(screen.getByTestId("bloco-simulador")).getByLabelText("Corte em Mercado")).toBeTruthy();
    expect(screen.queryByText("Simulador indisponível no seu plano.")).toBeNull();
    await ir("/gastos"); await waitFor(() => expect(screen.getByText("Simulador indisponível no seu plano.")).toBeTruthy());
  });
});

it("compromissos: o título usa o horizonte do bloco (60), não o do provider (30)", async () => {
  instalarCards(true); comBlocos(bloco("compromissos", { previsao: { ...F["/api/app/previsao"], dias: 60 } })); await perguntar();
  expect(within(screen.getByTestId("bloco-compromissos")).getByText("Próximos 60 dias")).toBeTruthy();
  expect(screen.queryByText("Próximos 30 dias")).toBeNull();
});

it("simulador: o título é 'Simulador', sem o mês do bloco (a base é a estimativa, não o mês)", async () => {
  instalarCards(true); comBlocos(bloco("simulador", undefined, "2026-10")); await perguntar();
  expect(within(screen.getByTestId("bloco-simulador")).getByText("Transporte")).toBeTruthy();
  expect(screen.queryByText(/Simulador · /)).toBeNull();
});

it("simulador: bloco só com `detalhes` (servidor antigo) mostra 'Dado indisponível', sem quebrar", async () => {
  instalarCards(true); comBlocos(bloco("simulador", { detalhes: F["/api/app/mes-detalhes"] })); await perguntar();
  expect(within(screen.getByTestId("bloco-simulador")).getByText("Dado indisponível neste card.")).toBeTruthy();
});

it("sem bloco, o chip com assunto não monta card (o card legado do assunto saiu)", async () => {
  await abrir(); await apertar("Quanto falta pra minha próxima meta e quando eu chego lá?");
  await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy());
  expect(screen.queryByTestId("bloco-metas")).toBeNull();
});
it("bloco de recursos vazios: 'Dado indisponível neste card.', sem tela de erro", async () => {
  comBlocos({ card: "categorias", abas: [{ mes: null, recursos: {} }] }); await perguntar();
  expect(within(screen.getByTestId("bloco-categorias")).getByText("Dado indisponível neste card.")).toBeTruthy();
});

describe("`caixinhas` e `metas` v2 no chat", () => {
  it("`caixinhas` (formato legado): a mesma lista de metas, com o título do card", async () => {
    comBlocos(bloco("caixinhas")); await perguntar();
    expect(within(screen.getByTestId("bloco-caixinhas")).getByText("Caixinhas")).toBeTruthy(); expect(within(screen.getByTestId("bloco-caixinhas")).getByText("Viagem")).toBeTruthy();
  });
  // Portados de `painel_regressoes`: a aba Metas já não mostra caixinhas, o card `caixinhas` (`ListaMetas`) é quem as desenha.
  it("`caixinhas` com saldo bancário 500.005 é aceito e apresentado", async () => {
    expect(metasSchema.parse(metasBanco).goals[0]!.balance).toBe(50001);
    comBlocos(bloco("caixinhas", { caixinhas: metasBanco })); await perguntar();
    const c = within(screen.getByTestId("bloco-caixinhas")); expect(c.getByText("Espelhada")).toBeTruthy(); expect(c.getByLabelText("500 reais e 1 centavo")).toBeTruthy();
  });
  it("`caixinhas`: indicador tight e prazo da meta têm cópia em português", async () => {
    comBlocos(bloco("caixinhas", { caixinhas: { ...metasBanco, goals: [{ ...metasBanco.goals[0], is_goal: true, indicator: "tight", target_date: "2026-11-03" }] } })); await perguntar();
    const c = within(screen.getByTestId("bloco-caixinhas")); expect(c.getByText("Prazo apertado")).toBeTruthy(); expect(c.getByText("03/11/2026")).toBeTruthy(); expect(c.queryByText("tight")).toBeNull();
  });
  it("`caixinhas` com valores ocultos: o chat troca o turno por um marcador (o card some inteiro, sem a barra nem o valor); volta ao mostrar", async () => {
    comBlocos(bloco("caixinhas")); await perguntar();
    const barras = () => screen.UNSAFE_root.findAll((n) => typeof n.type === "string" && n.props.accessibilityRole === "progressbar"); // a barra não é `accessible`: getByRole não a acha
    expect(barras()).toHaveLength(1); expect(within(screen.getByTestId("bloco-caixinhas")).getByLabelText("6000 reais")).toBeTruthy();
    await apertar("Ocultar valores"); expect(screen.queryByTestId("bloco-caixinhas")).toBeNull(); expect(barras()).toHaveLength(0); expect(screen.queryByLabelText("6000 reais")).toBeNull();
    await apertar("Mostrar valores"); expect(barras()).toHaveLength(1);
  });
  // O recurso `metas` do chat vem no formato v2 (`/api/v2/metas`); o legado `{ok, goals}` já não vale para o bloco `metas` (é do `caixinhas`).
  const metasV2 = (v2 as unknown as { metas: { varios: Record<string, any> } }).metas.varios;
  const turnoCom = async (previa: string, recursos: Record<string, unknown>) => {
    let f: ReturnType<typeof respostaFluxo> | undefined; srv.chat = () => (f = respostaFluxo()); await abrir(); await enviar("oi"); await waitFor(() => expect(f).toBeDefined());
    await act(async () => { f!.empurrar({ tipo: "passo", texto: "Montando as metas", previa }); f!.empurrar({ tipo: "bloco", bloco: bloco("metas", recursos) }); await drenar(); });
    return f!;
  };
  const fechar = async (f: ReturnType<typeof respostaFluxo>) => { await act(async () => { f.empurrar(fim("Sua reserva está em 25%.")); f.fechar(); await drenar(); }); await waitFor(() => expect(screen.getByText("Sua reserva está em 25%.")).toBeTruthy()); };
  it("bloco `metas` v2 é desenhado: o card aparece, a prévia some com ele e o texto do `fim` vem depois", async () => {
    const f = await turnoCom("metas", { metas: metasV2 });
    expect(screen.getByTestId("bloco-metas")).toBeTruthy(); expect(within(screen.getByTestId("bloco-metas")).getByText("Viagem pro Japão")).toBeTruthy();
    expect(screen.queryByLabelText("Carregando")).toBeNull();
    await fechar(f); expect(screen.getByTestId("bloco-metas")).toBeTruthy();
  });
  it("bloco `metas` LEGADO ({ok, goals}) é descartado sem derrubar o turno: nenhum card, o Skeleton vale só em voo e o texto aparece no `fim`", async () => {
    const f = await turnoCom("metas", { metas: F["/goals/1/status"] });
    expect(screen.getByLabelText("Carregando")).toBeTruthy(); // a prévia vale até o bloco chegar: o descartado não a satisfaz
    expect(screen.queryByTestId("bloco-metas")).toBeNull(); expect(screen.queryByText(/Viagem/)).toBeNull();
    await fechar(f); expect(screen.queryByLabelText("Carregando")).toBeNull(); expect(screen.queryByTestId("bloco-metas")).toBeNull();
  });
  // Cada mutação invalida o recurso inteiro (zod): o bloco some e o turno segue.
  const mutar = (f: (m: Record<string, any>) => void) => { const c = structuredClone(metasV2); f(c.metas[1]); return { metas: c }; };
  it.each([
    ["dinheiro em number", (m: Record<string, any>) => { m.atual = 12.5; }],
    ["percentual 150", (m: Record<string, any>) => { m.percentual = 150; }],
    ["chega_em mês 13", (m: Record<string, any>) => { m.chega_em = "2027-13"; }],
    ["ritmo ausente", (m: Record<string, any>) => { delete m.ritmo; }],
  ])("bloco `metas` v2 com %s é descartado e o turno segue", async (_nome, f) => {
    const t = await turnoCom("metas", mutar(f)); expect(screen.queryByTestId("bloco-metas")).toBeNull(); await fechar(t);
  });
  it("controle: o mesmo turno com `caixinhas` (legado) desenha o card e a prévia `caixinhas` some com ele", async () => {
    const f = await turnoCom("caixinhas", { metas: F["/goals/1/status"] });
    await act(async () => { f.empurrar({ tipo: "bloco", bloco: bloco("caixinhas") }); await drenar(); });
    expect(screen.getByTestId("bloco-caixinhas")).toBeTruthy(); expect(screen.queryByLabelText("Carregando")).toBeNull();
  });
});

describe("chat × painel: o que muda de um para o outro", () => {
  it("'Bancos conectados' some no card Contas do chat e aparece no painel", async () => {
    instalarCards(true); comBlocos(bloco("contas")); await perguntar();
    expect(within(screen.getByTestId("bloco-contas")).getByText("Nubank")).toBeTruthy(); expect(screen.queryByRole("button", { name: "Bancos conectados" })).toBeNull();
    await ir("/resumo"); await waitFor(() => expect(screen.getByRole("button", { name: "Bancos conectados" })).toBeTruthy());
  });
  it("compromissos no PAINEL: o título do card e o da Folha seguem o horizonte do provider", async () => {
    instalarCards(true); const impl = fetchFalso.getMockImplementation()!;
    fetchFalso.mockImplementation((u: string, r: RequestInit) => caminho(u) === "/api/app/previsao" ? Promise.resolve(resposta(200, { ...F["/api/app/previsao"], dias: 60 })) : impl(u, r));
    await abrir(); await noResumo(); await waitFor(() => expect(screen.getByText("Próximos 60 dias")).toBeTruthy()); expect(screen.queryByText("Próximos 30 dias")).toBeNull();
    fireEvent.press(screen.getByRole("button", { name: "Abrir Próximos 60 dias" })); expect(screen.getByRole("header", { name: "Próximos 60 dias" })).toBeTruthy();
  });
});
