import { fireEvent, screen, within } from "expo-router/testing-library";
import { falado, faladoComSinal } from "@/ui/dinheiro";
import { prepararCaso } from "./auth_apoio";
import { desligarTrava } from "./open_finance_volta_rota_apoio";
import { reiniciar } from "./conversa_apoio";
import { V2, bloco, comBlocos, instalarCards, perguntar } from "./conversa_cards_apoio";
// O card `metas` do chat (formato v2 de `/api/v2/metas`, fixtures reais), só leitura: o app mostra o que o servidor mandou.
jest.setTimeout(30_000);
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); instalarCards(); });
afterEach(() => { jest.restoreAllMocks(); });
const mostrar = async (caso: string) => { comBlocos(bloco("metas", { metas: V2.metas[caso] })); await perguntar(); return within(screen.getByTestId("bloco-metas")); };
/** Uma meta/custo do `varios` com campos trocados (strings de dinheiro como o servidor manda) para um caso que a fixture não tem. */
type Cru = Record<string, any>;
const base = (id: number): Cru => V2.metas.varios.metas.find((m: Cru) => m.id === id);
const mostrarMeta = async (m: Cru, custo: Cru = {}) => { comBlocos(bloco("metas", { metas: { ...V2.metas.varios, metas: [m], custo_mensal: { ...V2.metas.varios.custo_mensal, ...custo } } })); await perguntar(); return within(screen.getByTestId(`meta-${m.id}`)); };
const meta = (id: number) => within(screen.getByTestId(`meta-${id}`));
/** O `now` das barras do trecho. O `ProgressBar` é `View` com `accessibilityRole` mas sem `accessible`: o `getByRole` não o acha, então busca-se o host pela prop. */
type Consulta = { UNSAFE_queryAllByProps: (p: { [x: string]: unknown }) => { type: unknown; props: { [x: string]: any } }[] };
const barras = (c: Consulta): number[] => c.UNSAFE_queryAllByProps({ accessibilityRole: "progressbar" }).filter((b) => typeof b.type === "string").map((b) => b.props.accessibilityValue.now);
const R = (centavos: number, tipo?: "entrada") => faladoComSinal(centavos, tipo);

describe("varios", () => {
  it("reserva primeiro, depois as outras na ordem do servidor; título 'Metas'", async () => {
    const c = await mostrar("varios");
    expect(c.getAllByRole("header").map((h) => h.props.children)).toEqual(V2.metas.varios.metas.map((m: { nome: string }) => m.nome));
  });
  it("a reserva sobe ao topo mesmo se o servidor a mandar por último; o resto fica na ordem do servidor", async () => {
    const [reserva, ...resto] = V2.metas.varios.metas; comBlocos(bloco("metas", { metas: { ...V2.metas.varios, metas: [...resto, reserva] } })); await perguntar();
    expect(within(screen.getByTestId("bloco-metas")).getAllByRole("header").map((h) => h.props.children)).toEqual(V2.metas.varios.metas.map((m: { nome: string }) => m.nome));
  });
  it("reserva: subtítulo, N × custo, falta, barra e chegada sem prazo", async () => {
    await mostrar("varios"); const m = meta(1);
    expect(m.getByText("Reserva de emergência · 6 meses das suas contas fixas (6 × R$ 3.000,00) · Tudo que tenho investido")).toBeTruthy(); expect(m.queryByText("6 ×")).toBeNull();
    expect(m.getByLabelText(R(450000))).toBeTruthy(); expect(m.getByLabelText(R(1800000))).toBeTruthy();
    expect(m.getByText("Falta")).toBeTruthy(); expect(m.getByLabelText(R(1350000))).toBeTruthy();
    expect(barras(m)).toEqual([25]);
    expect(m.getByText("Nesse ritmo, chega em abril de 2029")).toBeTruthy();
  });
  it("Viagem: precisa por mês até o prazo, ritmo positivo, selo de medição parcial e chegada depois do prazo", async () => {
    await mostrar("varios"); const m = meta(2);
    expect(m.getByText("Precisa por mês")).toBeTruthy(); expect(m.getByLabelText(R(88889))).toBeTruthy(); expect(m.getByText("até junho de 2027")).toBeTruthy();
    expect(m.getByText("Seu ritmo")).toBeTruthy(); expect(m.getByLabelText(R(60000, "entrada"))).toBeTruthy(); expect(m.getByText("inclui rendimento e variação")).toBeTruthy();
    expect(m.getAllByLabelText("por mês")).toHaveLength(2); expect(m.getByText("medido em 45 dias")).toBeTruthy();
    expect(m.getByText("Nesse ritmo, chega em dezembro de 2027, depois do prazo")).toBeTruthy(); expect(m.getByText("Investido no Nubank")).toBeTruthy();
  });
  it("Notebook: ritmo negativo com sinal e 'a meta não anda'; sem prazo", async () => {
    await mostrar("varios"); const m = meta(3);
    expect(m.getByLabelText(R(-25000))).toBeTruthy(); expect(m.getByText("Nesse ritmo, a meta não anda.")).toBeTruthy();
    expect(m.getByText("Sem prazo. Defina uma data no painel e eu digo quanto guardar por mês.")).toBeTruthy();
  });
  it("sem ritmo: a frase do motivo, uma vez só (Entrada do apê: 4 de 7 dias; Moto: histórico incompleto)", async () => {
    await mostrar("varios");
    expect(meta(4).getAllByText("Ainda estou juntando histórico (4 de 7 dias). Volto a calcular seu ritmo a partir de 7 dias.")).toHaveLength(1);
    expect(meta(5).getAllByText("Ainda estou juntando histórico dos seus bancos. Em alguns dias eu calculo seu ritmo.")).toHaveLength(1);
    expect(meta(6).getAllByText(/um dos seus bancos não confirma a leitura/)).toHaveLength(1);
    expect(meta(7).getAllByText(/Pra saldo de conta eu ainda não meço ritmo/)).toHaveLength(1);
  });
  it("meta batida: check e 'Meta batida', barra verde cheia, sem Precisa nem Seu ritmo", async () => {
    await mostrar("varios"); const m = meta(9);
    expect(m.getByText("Meta batida")).toBeTruthy(); expect(m.queryByText("Precisa por mês")).toBeNull(); expect(m.queryByText("Seu ritmo")).toBeNull(); expect(m.queryByText("Falta")).toBeNull();
    expect(barras(m)).toEqual([100]);
  });
  it("prazo que passou: 'O prazo era <mês>'", async () => {
    await mostrar("varios"); expect(meta(8).getByText("O prazo era agosto de 2026")).toBeTruthy();
  });
  it("fonte que sumiu: '—' sem número inventado, a frase aponta o painel e não há barra nem 'Falta'", async () => {
    await mostrar("varios"); const m = meta(10);
    expect(m.getAllByText("—").length).toBeGreaterThan(0); expect(m.getAllByText(/no painel você edita a meta e escolhe outra/)).toHaveLength(1);
    expect(m.getByLabelText(R(500000))).toBeTruthy(); expect(barras(m)).toEqual([]); expect(m.queryByText("Falta")).toBeNull();
  });
  it("conta pausada com saldo zero: selo 'a conferir'; as outras não o têm", async () => {
    await mostrar("varios");
    expect(meta(11).getByText("a conferir")).toBeTruthy(); expect(meta(11).getAllByText("A conexão com o banco está pausada. Reconecte pra eu voltar a ler.")).toHaveLength(1);
    expect(screen.getAllByText("a conferir")).toHaveLength(1);
  });
  it("nenhum botão dentro do card, e o toque nele não abre folha", async () => {
    const c = await mostrar("varios");
    expect(c.queryAllByRole("button")).toHaveLength(0);
    fireEvent.press(screen.getByTestId("bloco-metas")); expect(screen.queryByRole("header", { name: "Metas" })).toBeNull(); expect(screen.queryByRole("button", { name: "Fechar" })).toBeNull();
    expect(screen.getAllByText("Viagem pro Japão")).toHaveLength(1);
  });
});
describe("avisos, ressalvas e fallbacks", () => {
  it("o card desenha os avisos da meta: banco desatualizado com ritmo presente", async () => {
    const m = await mostrarMeta({ ...base(2), motivos: ["banco_desatualizado"] });
    expect(m.getAllByText("Seu banco não atualiza há um tempo, então o número pode estar velho.")).toHaveLength(1);
  });
  it("motivos só do ritmo (historico_curto) não viram aviso: a frase da conexão pausada fica sozinha, sem 'juntando histórico'", async () => {
    const m = await mostrarMeta({ ...base(11), atual: "500.00", ritmo: { ...base(11).ritmo, dias_de_historico: 3, motivos: ["conexao_pausada", "historico_curto"] } });
    expect(m.getAllByText("A conexão com o banco está pausada. Reconecte pra eu voltar a ler.")).toHaveLength(1);
    expect(m.queryByText(/Ainda estou juntando/)).toBeNull();
  });
  it("meta batida com conexão pausada: 'Meta batida' e a ressalva (o bloco do ritmo não é desenhado, então a frase não foi mostrada)", async () => {
    const m = await mostrarMeta({ ...base(9), motivos: ["conexao_pausada"], ritmo: { ...base(9).ritmo, valor: null, chega_em: null, motivos: ["conexao_pausada"] } });
    expect(m.getByText("Meta batida")).toBeTruthy(); expect(m.queryByText("Seu ritmo")).toBeNull();
    expect(m.getAllByText("A conexão com o banco está pausada. Reconecte pra eu voltar a ler.")).toHaveLength(1);
  });
  it("reserva com custo e cobranças do banco não lidas: 'O valor pode estar velho'", async () => {
    const m = await mostrarMeta(base(1), { motivos: ["recorrencias_banco_nao_lidas"] });
    expect(m.getAllByText("Não consegui ler as cobranças do banco agora. O valor pode estar velho.")).toHaveLength(1);
  });
  it("'a conferir' só com motivo do saldo: motivo do alvo (custo_desconhecido) com saldo zero não o mostra", async () => {
    const m = await mostrarMeta({ ...base(11), motivos: ["custo_desconhecido"], ritmo: { ...base(11).ritmo, motivos: [] } });
    expect(m.queryByText("a conferir")).toBeNull();
  });
  it("prazo sem 'precisa por mês' (por_mes nulo): 'Prazo: <mês>'", async () => {
    const m = await mostrarMeta({ ...base(3), data_alvo: "2027-06-30", por_mes: null });
    expect(m.getByText("Prazo: junho de 2027")).toBeTruthy();
  });
});
describe("outros casos da fixture", () => {
  it("vazio: texto sem metas e o convite da reserva com o custo", async () => {
    const c = await mostrar("vazio");
    expect(c.getByText("Você ainda não tem metas. Crie a primeira no painel: pigbankai.com/painel.")).toBeTruthy();
    expect(c.getByText("Reserva de emergência: 6 meses das suas contas fixas. Monte no painel.")).toBeTruthy(); expect(c.getByLabelText(R(300000))).toBeTruthy(); expect(c.getByLabelText("por mês")).toBeTruthy();
  });
  it("sem_custo: 'definido no painel' (reserva sem alvo, uma vez) e 'plano sem investimentos' (meta sem saldo)", async () => {
    const c = await mostrar("sem_custo");
    expect(c.getAllByText(/precisa ser definido no painel/)).toHaveLength(1); expect(meta(1).getByText("guardado")).toBeTruthy(); expect(meta(1).getByLabelText(R(80000))).toBeTruthy();
    expect(meta(2).getAllByText("Seu plano atual não inclui investimentos.")).toHaveLength(1); expect(c.queryByText(/Monte no painel/)).toBeNull(); // já tem reserva
  });
  it("estresse: aceito e desenhado sem lançar; o alvo de R$ 24 bi cabe em centavos seguros e sai por inteiro, nunca '—'", async () => {
    const c = await mostrar("estresse");
    expect(c.getAllByRole("header")).toHaveLength(3); expect(meta(3).getByLabelText(R(2399999999976))).toBeTruthy(); expect(meta(3).queryByText("—")).toBeNull();
  });
});
describe("valores ocultos", () => {
  it("a linha da reserva no card: ao ocultar, o card some do chat e nenhum dígito de dinheiro fica (controle: antes, R$ 3.000,00)", async () => {
    const c = await mostrar("varios"); const linha = "Reserva de emergência · 6 meses das suas contas fixas";
    expect(c.getByText(`${linha} (6 × R$ 3.000,00) · Tudo que tenho investido`)).toBeTruthy();
    expect(c.getByLabelText(`${linha} (6 × ${falado(300000)}) · Tudo que tenho investido`)).toBeTruthy(); // o VoiceOver lê o custo falado, não "R$ 3.000,00"
    fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" }));
    expect(screen.queryByTestId("bloco-metas")).toBeNull(); expect(screen.queryAllByText(/\(6 × R\$ 3/, { hidden: true })).toEqual([]); expect(screen.queryAllByText(/3\.000/, { hidden: true })).toEqual([]);
    expect(screen.queryAllByLabelText(/3000 reais/, { hidden: true })).toEqual([]);
  });
  it("ocultar valores no chat: o card some, sem barra nem dígito de dinheiro; antes, a barra com 60 (controle)", async () => {
    await mostrar("varios");
    expect(barras(screen)).toContain(60); expect(screen.queryAllByLabelText(R(1200000), { hidden: true }).length).toBeGreaterThan(0);
    fireEvent.press(screen.getByRole("button", { name: "Ocultar valores", hidden: true }));
    expect(barras(screen)).toEqual([]); expect(screen.queryAllByText(/R\$ \d/, { hidden: true })).toEqual([]); expect(screen.queryAllByLabelText(/ reais?\b| centavos?\b/, { hidden: true })).toEqual([]);
    expect(screen.queryByTestId("bloco-metas")).toBeNull();
  });
});
