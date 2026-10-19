import { StyleSheet } from "react-native";
import { render } from "@testing-library/react-native";
import { fireEvent, screen, within } from "expo-router/testing-library";
import { CARDS_DA_TOOL, lerEvento } from "@/api/schemas/painel";
import { BlocoContexto } from "@/features/painel/blocoContexto";
import { CardFimDoMes, CardMaiores } from "@/features/painel/cardMaiores";
import * as privacidade from "@/features/painel/privacidade";
import * as provider from "@/features/painel/provider";
import { faladoComSinal } from "@/ui/dinheiro";
import { TemaProvider } from "@/ui/tema";
import { claro, escuro } from "@/ui/tokens";
import { prepararCaso } from "./auth_apoio";
import { desligarTrava } from "./open_finance_volta_rota_apoio";
import { posts, reiniciar } from "./conversa_apoio";
import { comBlocos, instalarCards, perguntar } from "./conversa_cards_apoio";
import maioresJson from "../../../tests/fixtures/ai_chat_bloco_maiores_gastos.json";
import fimJson from "../../../tests/fixtures/ai_chat_bloco_fim_do_mes.json";
// Os cards que a própria tool emite (PR 3A): `maiores_gastos` e `fim_do_mes`. A fixture é a do servidor (`tests/test_ai_chat_tool_card.py` a confere contra o bloco real).
jest.setTimeout(30_000);
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); instalarCards(); });
afterEach(() => { jest.restoreAllMocks(); });

type Bloco = typeof maioresJson;
const MAIORES = maioresJson, FIM = fimJson;
const gasto = (i: number, valor = 100 - i) => ({ valor, categoria: "mercado", descricao: `compra ${i}`, data: "2026-10-17", fonte: "launches", instituicao: null, parcelamento: null });
const maiores = (n: number, extra: Record<string, unknown> = {}) => ({ ...MAIORES, abas: [{ mes: null, recursos: { maiores_gastos: { ...MAIORES.abas[0]!.recursos.maiores_gastos, expenses: Array.from({ length: n }, (_, i) => gasto(i)), count: n, ...extra } } }] }) as Bloco;
const fim = (extra: Record<string, unknown> = {}) => ({ ...FIM, abas: [{ mes: null, recursos: { fim_do_mes: { ...FIM.abas[0]!.recursos.fim_do_mes, ...extra } } }] });
const mostrar = async (b: unknown) => { comBlocos(b as never); await perguntar(); };
const card = (id: string) => within(screen.getByTestId(`bloco-${id}`));
const larguras = () => screen.root.findAll((n) => typeof StyleSheet.flatten(n.props.style)?.width === "string").map((n) => StyleSheet.flatten(n.props.style).width);
const nomes = (n: number) => Array.from({ length: n }, (_, i) => `compra ${i}`).filter((t) => card("maiores_gastos").queryByText(t));

describe("maiores_gastos", () => {
  it("a fixture do servidor: título do mês, uma linha por gasto com data, categoria, banco e parcelamento", async () => {
    await mostrar(MAIORES);
    expect(screen.getByText("Maiores gastos · outubro de 2026")).toBeTruthy();
    const c = card("maiores_gastos");
    expect(c.getByText("aluguel")).toBeTruthy(); expect(c.getByText("17/10 · Moradia · Nubank · compra de R$ 600,00 em 3x")).toBeTruthy();
    expect(c.getByText("mercado")).toBeTruthy(); expect(c.getByText("17/10 · Alimentação")).toBeTruthy(); // sem banco nem parcelamento
    expect(c.getByLabelText(faladoComSinal(30000))).toBeTruthy(); expect(c.getByLabelText(faladoComSinal(12000))).toBeTruthy();
  });
  it("a barra é relativa ao maior (300 = 100%, 120 = 40%)", async () => {
    await mostrar(MAIORES);
    expect(larguras()).toContain("100%"); expect(larguras()).toContain("40%");
  });
  it("período que não é o mês inteiro: título com dd/mm a dd/mm", async () => {
    await mostrar(maiores(2, { start_date: "2026-10-10", end_date: "2026-10-17" }));
    expect(screen.getByText("Maiores gastos · 10/10 a 17/10")).toBeTruthy();
  });
  it.each([
    ["mês corrente parcial", "2026-10-01", "2026-10-17", "Maiores gastos · 01/10 a 17/10"],
    ["fevereiro bissexto inteiro", "2028-02-01", "2028-02-29", "Maiores gastos · fevereiro de 2028"],
    ["fevereiro comum inteiro", "2026-02-01", "2026-02-28", "Maiores gastos · fevereiro de 2026"],
    ["fevereiro bissexto sem o 29", "2028-02-01", "2028-02-28", "Maiores gastos · 01/02 a 28/02"],
    ["começa no dia 1 e acaba no 30 de um mês de 31", "2026-10-01", "2026-10-30", "Maiores gastos · 01/10 a 30/10"],
    ["cruza o ano: o dia de outro ano que o fim leva o ano", "2025-11-01", "2026-10-31", "Maiores gastos · 01/11/25 a 31/10"],
  ])("título: %s", async (_n, start_date, end_date, titulo) => { await mostrar(maiores(2, { start_date, end_date })); expect(screen.getByText(titulo)).toBeTruthy(); });
  it("12 meses: o mesmo dia em anos diferentes não se confunde (o ano só no que não é o do fim do período)", async () => {
    const dias = ["2025-10-17", "2026-10-17"];
    await mostrar(maiores(2, { start_date: "2025-10-01", end_date: "2026-10-31", expenses: dias.map((data, i) => ({ ...gasto(i), data, categoria: "mercado" })) }));
    const c = card("maiores_gastos");
    expect(c.getByText("17/10/25 · Mercado")).toBeTruthy(); expect(c.getByText("17/10 · Mercado")).toBeTruthy();
  });
  it("mais de 5: mostra 5 e '+ N gastos'; tocar mostra todos e 'Mostrar menos' volta a 5", async () => {
    await mostrar(maiores(8)); const c = card("maiores_gastos");
    expect(nomes(8)).toHaveLength(5); expect(c.getByRole("button", { name: "+ 3 gastos" })).toBeTruthy();
    fireEvent.press(c.getByRole("button", { name: "+ 3 gastos" })); expect(nomes(8)).toHaveLength(8);
    fireEvent.press(c.getByRole("button", { name: "Mostrar menos" })); expect(nomes(8)).toHaveLength(5);
  });
  it("6 itens: '+ 1 gasto' (singular)", async () => { await mostrar(maiores(6)); expect(card("maiores_gastos").getByRole("button", { name: "+ 1 gasto" })).toBeTruthy(); });
  it("5 ou menos: nenhum '+ N'", async () => {
    await mostrar(maiores(5)); expect(nomes(5)).toHaveLength(5); expect(card("maiores_gastos").queryByRole("button", { name: /^\+ |Mostrar menos/ })).toBeNull();
  });
  it("com motivos, o card dá acesso às ressalvas; `inicio_do_historico` diz que é o histórico do plano (não o aviso genérico); sem motivos, nada", async () => {
    await mostrar(maiores(2, { motivos: ["inicio_do_historico"] }));
    fireEvent.press(card("maiores_gastos").getByRole("button", { name: "Ver ressalvas" }));
    expect(card("maiores_gastos").getByText(/histórico do seu plano tem uma data de início/)).toBeTruthy();
    expect(card("maiores_gastos").queryByText(/informações incompletas/)).toBeNull();
  });
  it("sem motivos: nenhum acesso às ressalvas", async () => { await mostrar(maiores(2, { motivos: [] })); expect(card("maiores_gastos").queryByRole("button", { name: "Ver ressalvas" })).toBeNull(); });
  it("nenhuma linha expande: tocar numa linha não abre nada", async () => {
    await mostrar(MAIORES); expect(card("maiores_gastos").queryAllByRole("button")).toEqual([]);
  });
});

describe("fim_do_mes", () => {
  const traco = () => card("fim_do_mes").getByTestId("traco-receita", { includeHiddenElements: true });
  const segmentos = () => card("fim_do_mes").getAllByTestId("segmento-composicao", { includeHiddenElements: true }).map((s) => StyleSheet.flatten(s.props.style));
  it("a fixture: saldo previsto, 'dia 17 de 31', Entrou/Saiu e a barra com o traço da receita", async () => {
    await mostrar(FIM); const c = card("fim_do_mes");
    expect(screen.getByText("Fim do mês · outubro de 2026")).toBeTruthy(); expect(c.getByText("dia 17 de 31")).toBeTruthy();
    expect(c.getByLabelText(faladoComSinal(202000))).toBeTruthy(); expect(c.queryByText("Fecha no negativo")).toBeNull();
    expect(c.getByText("+R$ 5.200,00")).toBeTruthy(); expect(c.getByText("−R$ 2.100,50")).toBeTruthy();
    // gasto até hoje 2.100,50 | falta 1.079,50 | folga até a receita 2.020,00 (escala = receita)
    expect(segmentos().map((s) => s.flexGrow)).toEqual([210050, 107950, 202000]); expect(StyleSheet.flatten(traco().props.style).left).toBe("100%");
    expect(c.getByLabelText("Gasto até hoje 2100 reais e 50 centavos, previsto até o fim do mês 3180 reais, entrou 5200 reais")).toBeTruthy();
  });
  it("receita menor que o gasto previsto: o traço fica no meio da barra, sem folga", async () => {
    await mostrar(fim({ receita: 1590, despesa_projetada: 3180, saldo_projetado: -1590, vai_fechar_negativo: true }));
    expect(segmentos()).toHaveLength(2); expect(StyleSheet.flatten(traco().props.style).left).toBe("50%");
  });
  it("saldo negativo: '−R$', aviso 'Fecha no negativo' e a parte que falta na cor de alerta", async () => {
    await mostrar(fim({ saldo_projetado: -150.5, despesa_projetada: 5350.5, vai_fechar_negativo: true })); const c = card("fim_do_mes");
    expect(c.getByLabelText(faladoComSinal(-15050))).toBeTruthy(); expect(c.getByText("Fecha no negativo")).toBeTruthy();
    expect(segmentos()[1]!.backgroundColor).toBe(claro.alert);
  });
  it("receita 0: sem traço e sem a legenda 'Entrou' da barra (sobra só o 'Entrou' do EntrouSaiu); com receita, os dois (controle)", async () => {
    const entrou = () => card("fim_do_mes").getAllByText("Entrou", { includeHiddenElements: true });
    await mostrar(fim({ receita: 0, saldo_projetado: -3180, vai_fechar_negativo: true }));
    expect(card("fim_do_mes").queryByTestId("traco-receita", { includeHiddenElements: true })).toBeNull(); expect(entrou()).toHaveLength(1);
    await mostrar(fim()); expect(traco()).toBeTruthy(); expect(entrou()).toHaveLength(2);
  });
  it("com motivos, o card dá acesso às ressalvas e `inicio_do_historico` mostra o texto do histórico do plano; sem motivos, nada", async () => {
    await mostrar(fim({ motivos: ["inicio_do_historico"] }));
    fireEvent.press(card("fim_do_mes").getByRole("button", { name: "Ver ressalvas" }));
    expect(card("fim_do_mes").getByText(/histórico do seu plano tem uma data de início/)).toBeTruthy();
    await mostrar(fim({ motivos: [] })); expect(card("fim_do_mes").queryByRole("button", { name: "Ver ressalvas" })).toBeNull();
  });
  it.each([[-150.5, true], [2020, false]])("saldo %s: o número nunca é verde (verde é só para valor positivo, e o saldo previsto não é entrada)", async (saldo, negativo) => {
    await mostrar(fim({ saldo_projetado: saldo, vai_fechar_negativo: negativo }));
    const t = card("fim_do_mes").getByLabelText(faladoComSinal(Math.round(saldo * 100)));
    expect([claro.positive, escuro.positive]).not.toContain(StyleSheet.flatten(t.props.style).color);
  });
});

describe("valores ocultos", () => {
  it("no chat: o turno inteiro some (controle: antes, o card e o valor aparecem)", async () => {
    await mostrar(FIM); expect(screen.getByTestId("bloco-fim_do_mes")).toBeTruthy();
    fireEvent.press(screen.getByRole("button", { name: "Ocultar valores", hidden: true }));
    expect(screen.queryByTestId("bloco-fim_do_mes")).toBeNull(); expect(screen.queryAllByText(/R\$ \d/, { hidden: true })).toEqual([]);
  });
  const direto = (recursos: unknown, ui: React.ReactElement, oculto: boolean) => {
    jest.spyOn(provider, "usePainel").mockImplementation(() => ({ oculto, dados: {} }) as never);
    jest.spyOn(privacidade, "usePrivacidade").mockImplementation(() => ({ oculto, alternar: () => undefined, mostrar: () => undefined })); // `Valor`/`Curva` leem a privacidade, não o `usePainel`
    return render(<TemaProvider esquema="light"><BlocoContexto.Provider value={{ recursos: recursos as never }}>{ui}</BlocoContexto.Provider></TemaProvider>);
  };
  const recursosDe = (b: unknown) => (lerEvento({ tipo: "bloco", bloco: b }) as unknown as { bloco: { abas: { recursos: unknown }[] } }).bloco.abas[0]!.recursos; // o recurso JÁ no formato do app (centavos)
  it("CardFimDoMes direto: oculto não deixa dígito, nem barra, nem aviso; visível mostra (controle)", () => {
    const r = recursosDe(fim({ saldo_projetado: -150.5, vai_fechar_negativo: true }));
    const v = direto(r, <CardFimDoMes />, false);
    expect(screen.getAllByText(/R\$ \d/).length).toBeGreaterThan(0); expect(screen.getByTestId("barra-composicao", { includeHiddenElements: true })).toBeTruthy(); expect(screen.getByText("Fecha no negativo")).toBeTruthy();
    v.unmount(); direto(r, <CardFimDoMes />, true);
    expect(screen.queryAllByText(/R\$ \d/)).toEqual([]); expect(screen.queryAllByLabelText(/reais?\b|centavos?\b/)).toEqual([]);
    expect(screen.queryByTestId("barra-composicao", { includeHiddenElements: true })).toBeNull(); expect(screen.queryByText("Fecha no negativo")).toBeNull();
  });
  it("CardMaiores direto: oculto tira valor, barra e o parcelamento (que traz R$); data, categoria e banco ficam", () => {
    const r = recursosDe(MAIORES);
    const v = direto(r, <CardMaiores />, false);
    expect(screen.getByText("17/10 · Moradia · Nubank · compra de R$ 600,00 em 3x")).toBeTruthy(); expect(screen.getAllByText(/^R\$ \d/).length).toBeGreaterThan(0); expect(larguras()).toContain("40%");
    v.unmount(); direto(r, <CardMaiores />, true);
    expect(screen.getByText("17/10 · Moradia · Nubank")).toBeTruthy(); expect(screen.queryAllByText(/R\$ \d/)).toEqual([]); expect(screen.queryAllByLabelText(/reais?\b|centavos?\b/)).toEqual([]);
    expect(larguras()).not.toContain("40%");
  });
});

describe("o POST do chat", () => {
  it("manda `cards` com os cinco ids, os mesmos da lista do schema (a ordem do `cards.DA_TOOL` do Python)", async () => {
    const ids = ["maiores_gastos", "fim_do_mes", "comparacao", "ultimos_lancamentos", "sem_categoria"];
    await mostrar(MAIORES);
    expect(JSON.parse(String(posts().at(-1)![1].body))).toEqual({ message: "oi", cards: ids });
    expect(CARDS_DA_TOOL).toEqual(ids);
  });
});

describe("schema (lerEvento)", () => {
  const evento = (b: unknown) => lerEvento({ tipo: "bloco", bloco: b });
  const comMaiores = (extra: Record<string, unknown>) => ({ ...MAIORES, abas: [{ mes: null, recursos: { maiores_gastos: { ...MAIORES.abas[0]!.recursos.maiores_gastos, ...extra } } }] });
  it("aceita as duas fixtures do servidor", () => { expect(evento(MAIORES)).not.toBeNull(); expect(evento(FIM)).not.toBeNull(); });
  it("o dinheiro vira centavos", () => {
    const m = evento(MAIORES) as unknown as { bloco: { abas: { recursos: { maiores_gastos: { expenses: { valor: number }[] } } }[] } };
    expect(m.bloco.abas[0]!.recursos.maiores_gastos.expenses.map((x) => x.valor)).toEqual([30000, 12000]);
  });
  it("aceita as chaves opcionais do maiores_gastos (e o cartao_por data_da_compra)", () => {
    const opcionais = { motivos: ["inicio_do_historico"], historico_desde: "2026-08-10", limitado_a_12_meses: true, pedido: { start_date: "2024-01-01", end_date: "2026-10-31" }, aviso_limite: "x", fim_ajustado: true, aviso_fim: "y", cartao_por: "data_da_compra" };
    expect(evento(comMaiores(opcionais))).not.toBeNull(); expect(evento(comMaiores({ ...opcionais, historico_desde: null }))).not.toBeNull();
  });
  it.each([
    ["valor em texto", comMaiores({ expenses: [{ ...gasto(0), valor: "300.00" }] })],
    ["expenses que não é lista", comMaiores({ expenses: {} })],
    ["motivos que não é lista", comMaiores({ motivos: "x" })],
    ["valor nulo", comMaiores({ expenses: [{ ...gasto(0), valor: null }] })],
    ["fim_do_mes sem saldo_projetado", fim({ saldo_projetado: undefined })],
    ["fim_do_mes com vai_fechar_negativo em texto", fim({ vai_fechar_negativo: "sim" })],
    ["fim_do_mes com mês malformado", fim({ mes: "outubro" })],
    ["fim_do_mes com saldo em texto", fim({ saldo_projetado: "2020.00" })],
  ])("recusa a forma errada: %s", (_n, b) => { expect(evento(b)).toBeNull(); });
});
