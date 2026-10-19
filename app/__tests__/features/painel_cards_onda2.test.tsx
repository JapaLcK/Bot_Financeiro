import { fireEvent, screen, within } from "expo-router/testing-library";
import { lerEvento } from "@/api/schemas/painel";
import { prepararCaso } from "./auth_apoio";
import { desligarTrava } from "./open_finance_volta_rota_apoio";
import { reiniciar } from "./conversa_apoio";
import { chamadasLanc, comBlocos, instalarCards, perguntar } from "./conversa_cards_apoio";
import ultimosJson from "../../../tests/fixtures/ai_chat_bloco_ultimos_lancamentos.json";
import semCategoriaJson from "../../../tests/fixtures/ai_chat_bloco_sem_categoria.json";
// Onda 2 dos cards (PR B): `ultimos_lancamentos` e `sem_categoria`. As fixtures são as do servidor (`tests/test_ai_chat_tool_card_listas.py` as confere contra o bloco real).
jest.setTimeout(30_000);
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); instalarCards(); });
afterEach(() => { jest.restoreAllMocks(); });

const ULT = ultimosJson, SEM = semCategoriaJson;
const lista = (id: "ultimos_lancamentos" | "sem_categoria", n: number) => {
  const base = (id === "ultimos_lancamentos" ? ULT : SEM).abas[0]!.recursos as Record<string, any>;
  return { card: id, abas: [{ mes: null, recursos: { [id]: { ...base[id], itens: Array.from({ length: n }, (_, i) => ({ ...base[id].itens[0], id: `l${i}`, descricao: `compra ${i}` })) } } }] };
};
const mostrar = async (b: unknown) => { comBlocos(b as never); await perguntar(); };
const card = (id: string) => within(screen.getByTestId(`bloco-${id}`));

describe.each([["ultimos_lancamentos", "Últimos lançamentos", ULT], ["sem_categoria", "Sem categoria", SEM]] as const)("%s", (id, titulo, fixture) => {
  it("a fixture do servidor: título e uma linha por lançamento, no formato do Extrato", async () => {
    await mostrar(fixture); const c = card(id);
    expect(screen.getByText(titulo)).toBeTruthy();
    const descricoes = (fixture.abas[0]!.recursos as Record<string, any>)[id].itens.map((i: any) => i.descricao as string);
    for (const d of descricoes) expect(c.getByText(d)).toBeTruthy();
    expect(c.getByText(id === "ultimos_lancamentos" ? "17/10/2026 · Carteira · Alimentacao" : "17/10/2026 · Carteira · Outros")).toBeTruthy(); // o card do chat MANTÉM a data, e ganha a categoria
    expect(c.queryByRole("button", { name: /^\+ / })).toBeNull();
  });
  it("10 itens: 5 à vista e '+ 5 lançamentos' que abre os outros 5 na própria lista; 'Mostrar menos' volta", async () => {
    await mostrar(lista(id, 10)); const c = within(screen.getByTestId(`bloco-${id}`)), vistas = () => Array.from({ length: 10 }, (_, i) => i).filter((i) => c.queryByText(`compra ${i}`));
    expect(vistas()).toHaveLength(5); fireEvent.press(c.getByRole("button", { name: "+ 5 lançamentos" })); expect(vistas()).toHaveLength(10);
    fireEvent.press(c.getByRole("button", { name: "Mostrar menos" })); expect(vistas()).toHaveLength(5);
  });
  it("1 item só (a confirmação de apagar manda a lista com 1): uma linha, nenhum '+ N'", async () => {
    await mostrar(lista(id, 1)); expect(card(id).getByText("compra 0")).toBeTruthy(); expect(card(id).queryByRole("button", { name: /^\+ |Mostrar menos/ })).toBeNull();
  });
  it("lista vazia: texto, nenhuma linha", async () => { await mostrar(lista(id, 0)); expect(card(id).getByText("Nenhum lançamento.")).toBeTruthy(); });
});

describe("ultimos_lancamentos: item da fatura do mês seguinte", () => {
  it("tocar abre o detalhe NA linha: não navega (o Extrato de mês futuro responde 422) e não busca nada", async () => {
    await mostrar(ULT); const c = card("ultimos_lancamentos");
    fireEvent.press(c.getByText("Uber")); // fatura 2026-11
    expect(c.getByText("Transporte · Nubank · Parcela 2/3")).toBeTruthy();
    expect(screen).toHavePathname("/piggy"); expect(chamadasLanc()).toHaveLength(0);
    fireEvent.press(c.getByText("Uber")); expect(c.queryByText("Transporte · Nubank · Parcela 2/3")).toBeNull(); // e fecha
  });
});

describe("cardLancamentos: o que o card tem além da linha do Extrato", () => {
  const comMotivos = (motivos: string[]) => { const b = lista("ultimos_lancamentos", 2); (b.abas[0]!.recursos.ultimos_lancamentos as any).motivos = motivos; return b; };
  it("o aviso do bloco aparece e abre com o texto do motivo; o motivo do banco fica só no aviso do topo", async () => {
    await mostrar(comMotivos(["movimentos_pendentes"])); const c = card("ultimos_lancamentos");
    expect(c.queryByText("Há movimentos que ainda precisam ser conferidos.")).toBeNull(); fireEvent.press(c.getByRole("button", { name: "Ver ressalvas" }));
    expect(c.getByText("Há movimentos que ainda precisam ser conferidos.")).toBeTruthy();
    await mostrar(comMotivos(["banco_desatualizado"])); expect(card("ultimos_lancamentos").queryByRole("button", { name: "Ver ressalvas" })).toBeNull(); // `Avisos` filtra `MOTIVOS_BANCO`
  });
  it.each([[6, "+ 1 lançamento"], [11, "+ 6 lançamentos"]])("%i itens: o botão diz '%s' (singular e plural)", async (n, rotulo) => {
    await mostrar(lista("ultimos_lancamentos", n)); expect(card("ultimos_lancamentos").getByRole("button", { name: rotulo })).toBeTruthy();
  });
  it("o item de cartão com parcela mostra 'parcela 2/3' já na linha recolhida", async () => {
    await mostrar(ULT); expect(card("ultimos_lancamentos").getByText(/ · parcela 2\/3$/)).toBeTruthy();
  });
});

describe("schema (lerEvento)", () => {
  const evento = (b: unknown) => lerEvento({ tipo: "bloco", bloco: b });
  it("aceita as duas fixtures do servidor e converte o dinheiro para centavos", () => {
    for (const f of [ULT, SEM]) expect(evento(f)).not.toBeNull();
    expect((evento(ULT) as any).bloco.abas[0].recursos.ultimos_lancamentos.itens[0].valor).toBe(5000);
  });
  it.each([
    ["lista sem itens", { ...ULT, abas: [{ mes: null, recursos: { ultimos_lancamentos: { motivos: [] } } }] }],
    ["lista com item sem id", { ...SEM, abas: [{ mes: null, recursos: { sem_categoria: { itens: [{ descricao: "x" }], motivos: [] } } }] }],
  ])("recusa a forma errada: %s", (_n, b) => { expect(evento(b)).toBeNull(); });
  it("bloco inválido não derruba o chat: o evento é descartado, o card não aparece e a resposta chega", async () => {
    await mostrar({ ...ULT, abas: [{ mes: null, recursos: { ultimos_lancamentos: { itens: "x", motivos: [] } } }] });
    expect(screen.queryByTestId("bloco-ultimos_lancamentos")).toBeNull(); expect(screen.getByText("Resposta do Piggy")).toBeTruthy();
  });
});
