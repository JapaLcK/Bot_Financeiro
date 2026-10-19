import { StyleSheet } from "react-native";
import { act, fireEvent, screen, within } from "expo-router/testing-library";
import { dataCurta } from "@/features/painel/catalogo";
import { decimalParaCentavos, percentualDe, somarCentavos } from "@/features/painel/decimal";
import { falado } from "@/ui/dinheiro";
import { prepararCaso } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { reiniciar } from "./conversa_apoio";
import { F, comBlocos, consultaLanc, instalarCards, perguntar } from "./conversa_cards_apoio";
import tabela from "../../../tests/fixtures/percentual_categorias.json";
// O card `categorias` do chat (E2): UM card por bloco, abas por mês, colunas por categoria, "Outras", origem e sincronização. Desenha só o bloco.
jest.setTimeout(30_000);
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); instalarCards(); });
afterEach(() => { jest.restoreAllMocks(); });

const cat = (categoria: string | null, valor: number) => ({ categoria, valor: valor.toFixed(2), quantidade: 1 });
const aba = (mes: string, categorias: unknown[], extra: Record<string, unknown> = {}, contas?: unknown) => ({ mes, recursos: { detalhes: { ...F["/api/app/mes-detalhes"], mes, categorias, ...extra }, ...(contas ? { contas } : {}) } });
const mostrar = async (...abas: ReturnType<typeof aba>[]) => { comBlocos({ card: "categorias", abas }); await perguntar(); };
const card = () => within(screen.getByTestId("bloco-categorias"));
const colunas = () => card().getAllByRole("button").filter((b) => b.props.accessibilityState?.selected !== undefined); // a coluna tem `selected`; o título e o Avisos, não
const selecionadas = () => colunas().map((b) => b.props.accessibilityState.selected);
const tocar = async (nome: string | RegExp) => { await act(async () => { fireEvent.press(card().getByRole("button", { name: nome })); await drenar(); }); };
const aba3 = [aba("2026-10", [cat("Casa", 100)]), aba("2026-09", [cat("Lazer", 100)]), aba("2026-08", [cat("Viagem", 100)])];
const seis = ["Alfa", "Beta", "Gama", "Delta", "Eco", "Foxtrote"].map((n, i) => cat(n, 600 - i * 100)); // total 2.100: 29, 24, 19, 14, 10, 5

it("T1: 3 abas viram UM card com 3 tabs; trocar de aba troca o título e o conteúdo", async () => {
  await mostrar(...aba3);
  expect(screen.getAllByTestId("bloco-categorias")).toHaveLength(1); expect(screen.getAllByRole("tab")).toHaveLength(3);
  expect(screen.getAllByRole("tab").map((t) => t.props.accessibilityState.selected)).toEqual([true, false, false]);
  expect(screen.getByText("Para onde vai · outubro de 2026")).toBeTruthy(); expect(card().getByText("Casa")).toBeTruthy();
  fireEvent.press(screen.getByText("set"));
  expect(screen.getByText("Para onde vai · setembro de 2026")).toBeTruthy(); expect(card().getByText("Lazer")).toBeTruthy(); expect(card().queryByText("Casa")).toBeNull();
});
it("T2: o card é 100% do chat: tocar nele não abre folha nem modal; a coluna da aba selecionada mostra as transações no próprio card", async () => {
  await mostrar(...aba3); fireEvent.press(screen.getByText("ago"));
  fireEvent.press(screen.getByTestId("bloco-categorias")); fireEvent.press(card().getByText("Para onde vai · agosto de 2026"));
  expect(screen.queryByRole("button", { name: "Fechar" })).toBeNull(); expect(screen.queryByRole("header", { name: /Para onde vai/ })).toBeNull(); // só a Folha tem "Fechar" e o header
  expect(screen.getAllByText("Viagem")).toHaveLength(1); expect(screen.getAllByTestId("bloco-categorias")).toHaveLength(1); // o card segue no chat
  await tocar(/^Viagem/); expect(card().getByTestId("gastos-transacoes")).toBeTruthy(); expect(consultaLanc().get("mes")).toBe("2026-08");
});
it("T3: trocar de aba zera a coluna e as transações; tocar a coluna nova pede o mês novo; nada abre a Folha", async () => {
  const dois = (mes: string) => aba(mes, [cat("Casa", 100), cat("Lazer", 50)]);
  await mostrar(dois("2026-10"), dois("2026-09"));
  await tocar(/^Casa/); expect(selecionadas()).toEqual([true, false]); expect(screen.getByTestId("gastos-transacoes")).toBeTruthy();
  expect(consultaLanc().get("mes")).toBe("2026-10"); expect(screen.queryByRole("header", { name: /Para onde vai/ })).toBeNull(); // tocar na coluna não abre a Folha
  fireEvent.press(screen.getByText("set"));
  expect(selecionadas()).toEqual([false, false]); expect(screen.queryByTestId("gastos-transacoes")).toBeNull();
  await tocar(/^Casa/); expect(consultaLanc().get("mes")).toBe("2026-09");
});
it("T4: uma aba só não tem tablist nem tab", async () => {
  await mostrar(aba("2026-10", [cat("Casa", 100)]));
  expect(screen.queryAllByRole("tab")).toHaveLength(0); expect(screen.queryByRole("tablist")).toBeNull();
});
it("T5: rótulos das abas: 'out'/'set' no mesmo ano; 'out/2026'/'out/2025' em anos diferentes (e a 2ª aba funciona); VoiceOver lê o mês por extenso", async () => {
  await mostrar(aba("2026-10", [cat("Casa", 100)]), aba("2026-09", [cat("Lazer", 100)]));
  expect(screen.getAllByRole("tab").map((t) => within(t).getByText(/./).props.children)).toEqual(["out", "set"]);
  expect(screen.getByRole("tab", { name: "outubro de 2026" })).toBeTruthy();
});
it("T5b: o mesmo mês em anos diferentes tem rótulos únicos e a 2ª aba troca o título", async () => {
  await mostrar(aba("2026-10", [cat("Casa", 100)]), aba("2025-10", [cat("Lazer", 100)]));
  expect(screen.getAllByRole("tab").map((t) => within(t).getByText(/./).props.children)).toEqual(["out/2026", "out/2025"]);
  fireEvent.press(screen.getByText("out/2025")); expect(screen.getByText("Para onde vai · outubro de 2025")).toBeTruthy(); expect(card().getByText("Lazer")).toBeTruthy();
});
describe("T6: 'Outras'", () => {
  it("6 categorias: 4 + Outras (valor Σ da 5ª e da 6ª, % sobre o total)", async () => {
    await mostrar(aba("2026-10", seis));
    expect(colunas().map((b) => b.props.accessibilityLabel)).toEqual(["Alfa, 29%, " + falado(60000), "Beta, 24%, " + falado(50000), "Gama, 19%, " + falado(40000), "Delta, 14%, " + falado(30000), "Outras, 14%, " + falado(30000)]);
    expect(card().queryByText("Eco")).toBeNull();
  });
  it("5 categorias: as 5 pelo nome, sem 'Outras'; 4: as 4", async () => {
    await mostrar(aba("2026-10", seis.slice(0, 5))); expect(colunas()).toHaveLength(5); expect(card().queryByText("Outras")).toBeNull(); expect(card().getByText("Eco")).toBeTruthy();
  });
  it("4 categorias: as 4", async () => { await mostrar(aba("2026-10", seis.slice(0, 4))); expect(colunas()).toHaveLength(4); });
});
it("T7: paridade do % com o servidor (mesma tabela do pytest)", () => {
  for (const c of tabela) { const v = c.valores.map(decimalParaCentavos), total = somarCentavos(v); expect(v.map((x) => percentualDe(x, total))).toEqual(c.percentuais); }
});
it("T8: % que arredonda a 0 com valor > 0 diz '<1%'", async () => {
  await mostrar(aba("2026-10", [cat("Casa", 999), cat("Lazer", 0.5)])); expect(card().getByText("<1%")).toBeTruthy(); expect(card().getByText("100%")).toBeTruthy();
});
it("T9: a coluna é botão com nome, % e valor falado; 'selected' alterna; tocar de novo desmarca", async () => {
  await mostrar(aba("2026-10", F["/api/app/mes-detalhes"].categorias));
  const c = () => card().getByRole("button", { name: /^Alimentação/ });
  expect(c().props.accessibilityLabel).toBe(`Alimentação, 41%, ${falado(98050)}`); expect(selecionadas()).toEqual([false, false, false, false]);
  await tocar(/^Alimentação/); expect(selecionadas()).toEqual([true, false, false, false]);
  await tocar(/^Alimentação/); expect(selecionadas()).toEqual([false, false, false, false]); expect(screen.queryByTestId("gastos-transacoes")).toBeNull();
});
it("A12: a altura da barra é proporcional ao valor (maior = 96, metade = 48), pelo style", async () => {
  await mostrar(aba("2026-10", [cat("Casa", 400), cat("Lazer", 200), cat("Mercado", 100)]));
  expect(card().getAllByTestId("barra-coluna").map((b) => StyleSheet.flatten(b.props.style).height)).toEqual([96, 48, 24]);
});
it("A11: com uma coluna selecionada as outras ficam a 0.4 e a selecionada a 1; sem seleção, todas a 1", async () => {
  await mostrar(aba("2026-10", [cat("Casa", 100), cat("Lazer", 50)]));
  const op = () => colunas().map((b) => StyleSheet.flatten(b.props.style).opacity);
  expect(op()).toEqual([1, 1]); await tocar(/^Casa/); expect(op()).toEqual([1, 0.4]);
});
it("T9b: oculto: o card some do chat, sem % e sem origem (controle: antes, 41% e origem)", async () => {
  await mostrar(aba("2026-10", F["/api/app/mes-detalhes"].categorias, { origens: { cartao: "600.00", banco: "400.00", carteira: "0.00", registro_antigo: "0.00" } }));
  expect(screen.getAllByText("41%").length).toBeGreaterThan(0); expect(screen.getAllByLabelText(/Origem dos gastos/).length).toBeGreaterThan(0); // controle: visível
  fireEvent.press(screen.getByRole("button", { name: "Ocultar valores", hidden: true }));
  expect(screen.queryByTestId("bloco-categorias")).toBeNull();
  expect(screen.queryAllByText(/%/, { hidden: true })).toEqual([]); expect(screen.queryAllByLabelText(/Origem dos gastos|%/, { hidden: true })).toEqual([]);
});
it("T10: tocar 'Outras' lista a 5ª e a 6ª; tocar a 6ª pede as transações dela", async () => {
  await mostrar(aba("2026-10", seis));
  await tocar("Outras, 14%, " + falado(30000)); const regiao = within(screen.getByTestId("gastos-transacoes"));
  expect(regiao.getByText("Eco")).toBeTruthy(); expect(regiao.getByText("Foxtrote")).toBeTruthy(); expect(regiao.getByText("10%")).toBeTruthy(); expect(regiao.getByText("5%")).toBeTruthy();
  await act(async () => { fireEvent.press(regiao.getByRole("button", { name: /Foxtrote/ })); await drenar(); });
  expect([consultaLanc().get("categoria"), consultaLanc().get("mes"), consultaLanc().get("tipo")]).toEqual(["Foxtrote", "2026-10", "saida"]);
});
it("T10b: categoria real chamada '__outras' (nome livre) é coluna comum: pede as transações dela; com 6, ela e 'Outras' selecionam separadas", async () => {
  await mostrar(aba("2026-10", [cat("__outras", 300), cat("Casa", 100)]));
  await tocar(/^__outras,/);
  expect(consultaLanc().get("categoria")).toBe("__outras");
  await reiniciar(); instalarCards();
  await mostrar(aba("2026-10", [cat("__outras", 700), ...seis]));
  await tocar(/^__outras,/); expect(selecionadas()).toEqual([true, false, false, false, false]);
  await tocar(/^Outras,/); expect(selecionadas()).toEqual([false, false, false, false, true]);
  expect(within(screen.getByTestId("gastos-transacoes")).getByText("Foxtrote")).toBeTruthy();
});
describe("T11: origem", () => {
  const origens = { cartao: "600.00", banco: "400.00", carteira: "0.00", registro_antigo: "0.00" };
  it("2 itens (60%, 40%), sem a carteira, um elemento só para o VoiceOver e nenhum botão dentro", async () => {
    await mostrar(aba("2026-10", F["/api/app/mes-detalhes"].categorias, { origens }));
    const o = card().getByLabelText("Origem dos gastos: cartão 60%, banco 40%"); expect(within(o).getByText("60%")).toBeTruthy(); expect(within(o).getByText("40%")).toBeTruthy();
    expect(within(o).queryAllByText(/%/)).toHaveLength(2); expect(within(o).queryAllByRole("button")).toHaveLength(0); expect(card().getByTestId("barra-composicao", { hidden: true })).toBeTruthy();
  });
  it("controle: sem `origens` (ou todas zero), não há barra", async () => {
    await mostrar(aba("2026-10", F["/api/app/mes-detalhes"].categorias)); expect(card().queryByTestId("barra-composicao", { hidden: true })).toBeNull(); expect(card().queryByLabelText(/Origem dos gastos/)).toBeNull();
  });
  it("A7: `origens` todas zero: a Origem não aparece", async () => {
    await mostrar(aba("2026-10", F["/api/app/mes-detalhes"].categorias, { origens: { cartao: "0.00", banco: "0.00", carteira: "0.00", registro_antigo: "0.00" } }));
    expect(card().queryByTestId("barra-composicao", { hidden: true })).toBeNull(); expect(card().queryByLabelText(/Origem dos gastos/)).toBeNull();
  });
});
it("T12: o total é a soma das categorias (não `saiu`) e o intervalo é do dia 1 até `ate`", async () => {
  await mostrar(aba("2026-10", F["/api/app/mes-detalhes"].categorias, { saiu: "9999.00" }));
  expect(card().getByText("R$ 2.380,50")).toBeTruthy(); expect(card().queryByText("R$ 9.999,00")).toBeNull();
  expect(card().getByText("01/10–06/10")).toBeTruthy(); expect(card().getByLabelText("Período: 01/10 a 06/10")).toBeTruthy();
});
describe("T13: sincronização", () => {
  const com = (datas: (string | null)[]) => ({ ...F["/api/app/contas"], contas: datas.map((d, i) => ({ ...F["/api/app/contas"].contas[0], id: i + 1, sincronizado_em: d })) });
  const cats = F["/api/app/mes-detalhes"].categorias;
  it("mostra a MAIS ANTIGA entre as contas", async () => {
    await mostrar(aba("2026-10", cats, {}, com(["2026-10-06T12:00:00Z", "2026-10-04T12:00:00Z"])));
    const d = dataCurta("2026-10-04T12:00:00Z"); expect(card().getByLabelText(`Sincronizado em ${d}`)).toBeTruthy(); expect(card().getByText(d)).toBeTruthy(); expect(card().queryByLabelText(`Sincronizado em ${dataCurta("2026-10-06T12:00:00Z")}`)).toBeNull();
  });
  it("sem data em nenhuma conta, não há ícone", async () => {
    await mostrar(aba("2026-10", cats, {}, com([null, null]))); expect(card().queryByLabelText(/Sincronizado/)).toBeNull();
  });
  const pausada = (d: string | null) => ({ ...F["/api/app/contas"].contas[0], id: 9, sincronizado_em: d, motivos: ["conexao_pausada"] });
  it("conexão pausada ou removida não conta: 01/02 pausada + 06/10 viva mostra 06/10", async () => {
    await mostrar(aba("2026-10", cats, {}, { ...com(["2026-10-06T12:00:00Z"]), contas: [pausada("2026-02-01T12:00:00Z"), ...com(["2026-10-06T12:00:00Z"]).contas] }));
    const d = dataCurta("2026-10-06T12:00:00Z"); expect(card().getByLabelText(`Sincronizado em ${d}`)).toBeTruthy(); expect(card().queryByLabelText(`Sincronizado em ${dataCurta("2026-02-01T12:00:00Z")}`)).toBeNull();
  });
  it("só conexões pausadas: sem ícone", async () => {
    await mostrar(aba("2026-10", cats, {}, { ...com([]), contas: [pausada("2026-02-01T12:00:00Z")] })); expect(card().queryByLabelText(/Sincronizado/)).toBeNull();
  });
  it("`contas` inválido no bloco: o card de gastos aparece, sem o ícone", async () => {
    await mostrar(aba("2026-10", cats, {}, {})); expect(card().getByText("Alimentação")).toBeTruthy(); expect(card().queryByLabelText(/Sincronizado/)).toBeNull();
  });
  it("sem `contas` no bloco, não há ícone", async () => { await mostrar(aba("2026-10", cats)); expect(card().queryByLabelText(/Sincronizado/)).toBeNull(); });
});
