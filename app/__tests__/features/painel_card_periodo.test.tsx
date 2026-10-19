import { StyleSheet } from "react-native";
import { fireEvent, screen, within } from "expo-router/testing-library";
import { tituloPeriodo } from "@/features/painel/cardPeriodo";
import { prepararCaso } from "./auth_apoio";
import { desligarTrava } from "./open_finance_volta_rota_apoio";
import { reiniciar } from "./conversa_apoio";
import { PERIODO, bloco, comBlocos, instalarCards, perguntar } from "./conversa_cards_apoio";
// O card `periodo` do chat (mais de um mês): título, números do servidor, barras por mês e categorias.
jest.setTimeout(30_000);
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); instalarCards(); });
afterEach(() => { jest.restoreAllMocks(); });
const card = () => within(screen.getByTestId("bloco-periodo"));
const mostrar = async (extra: Record<string, unknown>) => { comBlocos(bloco("periodo", { periodo: { ...PERIODO, ...extra } })); await perguntar(); };
const cat = (categoria: string | null, valor: string, percentual = 10) => ({ categoria, valor, quantidade: 1, percentual, maiores: [] });

it.each([["2026-03", "2026-08", "Março a agosto 2026"], ["2025-11", "2026-04", "Nov 2025 a abr 2026"], ["2026-08", "2026-08", "Agosto 2026"], ["2026-01", "2026-12", "Janeiro a dezembro 2026"]])("tituloPeriodo(%s, %s) = %s", (a, b, esperado) => { expect(tituloPeriodo(a, b)).toBe(esperado); });

it("a fixture compartilhada: título, Entrou/Saiu/Saldo do servidor e o percentual de cada categoria", async () => {
  await mostrar({});
  expect(screen.getByText("Agosto a setembro 2026")).toBeTruthy();
  expect(card().getByText("+R$ 3.000,00")).toBeTruthy(); expect(card().getByText("−R$ 1.300,50")).toBeTruthy(); expect(card().getByText("R$ 1.699,50")).toBeTruthy();
  expect(card().getByText("Mercado")).toBeTruthy(); expect(card().getByText("70%")).toBeTruthy(); expect(card().getByText("Transporte")).toBeTruthy(); expect(card().getByText("30%")).toBeTruthy();
});
it("categoria com percentual 0 e valor > 0 diz '<1%'", async () => { await mostrar({ categorias: [cat("mercado", "999.00", 99), cat("outros", "0.50", 0)] }); expect(card().getByText("<1%")).toBeTruthy(); expect(card().queryByText(/dos gastos/)).toBeNull(); });

describe("mais de 5 categorias", () => {
  const sete = ["a", "b", "c", "d", "e", "f", "g"].map((n, i) => cat(n, `${100 - i}.00`));
  it("mostra 5 + '+ 2 categorias'; tocar mostra as 7; 'Mostrar menos' volta a 5", async () => {
    await mostrar({ categorias: sete });
    const nomes = () => ["A", "B", "C", "D", "E", "F", "G"].filter((n) => card().queryByText(n));
    expect(nomes()).toEqual(["A", "B", "C", "D", "E"]);
    fireEvent.press(card().getByRole("button", { name: "+ 2 categorias" })); expect(nomes()).toHaveLength(7);
    fireEvent.press(card().getByRole("button", { name: "Mostrar menos" })); expect(nomes()).toEqual(["A", "B", "C", "D", "E"]);
  });
  it("com 6: o botão diz '+ 1 categoria' (singular)", async () => {
    await mostrar({ categorias: sete.slice(0, 6) }); expect(card().getByRole("button", { name: "+ 1 categoria" })).toBeTruthy(); expect(card().queryByRole("button", { name: "+ 1 categorias" })).toBeNull();
  });
  it("com 5 ou menos, não há botão de cortar", async () => { await mostrar({ categorias: sete.slice(0, 5) }); expect(card().queryByRole("button", { name: /categorias|Mostrar menos/ })).toBeNull(); });
  it("ordena pelo valor, do maior para o menor, qualquer que seja a ordem do servidor", async () => {
    await mostrar({ categorias: [cat("pequena", "10.00"), cat("grande", "500.00")] });
    const texto = card().getAllByText(/^(Pequena|Grande)$/).map((t) => t.props.children); expect(texto).toEqual(["Grande", "Pequena"]);
  });
});

describe("barras por mês", () => {
  const meses = [{ mes: "2026-08", entrou: "1500.00", saiu: "600.00" }, { mes: "2026-09", entrou: "1500.00", saiu: "700.50" }];
  const opacidades = () => card().getAllByTestId("barra-trilho", { hidden: true }).map((t) => StyleSheet.flatten(t.props.style).opacity);
  it("mês anterior ao `historico_desde` fica apagado; os outros não", async () => { await mostrar({ meses, historico_desde: "2026-09-10" }); expect(opacidades()).toEqual([0.4, 1]); });
  it("sem `historico_desde`, nenhum apagado", async () => { await mostrar({ meses, historico_desde: null }); expect(opacidades()).toEqual([1, 1]); });
  it("12 meses atravessando o ano: uma barra por mês, dentro de rolagem horizontal", async () => {
    const doze = Array.from({ length: 12 }, (_, i) => ({ mes: `${2025 + Math.floor((i + 8) / 12)}-${String((i + 8) % 12 + 1).padStart(2, "0")}`, entrou: "100.00", saiu: `${100 + i}.00` }));
    await mostrar({ inicio: doze[0]!.mes, fim: doze[11]!.mes, meses: doze });
    expect(card().getAllByTestId("barra-trilho", { hidden: true })).toHaveLength(12); expect(screen.getByText(/^Set 2025 a ago 2026$/)).toBeTruthy();
    expect(card().getByLabelText(/^Saídas por mês: setembro de 2025: .*; agosto de 2026: /)).toBeTruthy();
  });
  it("VoiceOver: o mês anterior ao `historico_desde` diz que é anterior ao histórico (não um valor)", async () => {
    await mostrar({ meses, historico_desde: "2026-09-10" });
    expect(card().getByLabelText("Saídas por mês: agosto de 2026: antes do histórico do seu plano; setembro de 2026: 700 reais e 50 centavos")).toBeTruthy();
  });
  it("VoiceOver: um rótulo só com o mês e o valor falado de cada barra", async () => {
    await mostrar({ meses }); expect(card().getByLabelText("Saídas por mês: agosto de 2026: 600 reais; setembro de 2026: 700 reais e 50 centavos")).toBeTruthy();
  });
});

it("sem categorias: 'Nenhum gasto em reais neste período.' e o topo e as barras ficam", async () => {
  await mostrar({ categorias: [] });
  expect(card().getByText("Nenhum gasto em reais neste período.")).toBeTruthy(); expect(card().getByText("Saldo")).toBeTruthy(); expect(card().getAllByTestId("barra-trilho", { hidden: true })).toHaveLength(2);
});

it("categoria com `maiores: []` NÃO é expansível; com `maiores`, é", async () => {
  await mostrar({ categorias: [{ ...cat("mercado", "100.00", 50), maiores: [PERIODO.categorias[0].maiores[0]] }, cat("transporte", "50.00", 50)] });
  expect(card().getByRole("button", { name: /Mercado/ })).toBeTruthy(); expect(card().getByText("Transporte")).toBeTruthy(); expect(card().queryByRole("button", { name: /Transporte/ })).toBeNull();
});
it("a linha das maiores de uma categoria MANTÉM a data (só o Extrato a omite)", async () => {
  await mostrar({}); fireEvent.press(card().getByRole("button", { name: /Mercado/ }));
  expect(card().getByText(/^03\/09\/2026 · Nubank · Mercado/)).toBeTruthy();
});
it("o Saldo é o do servidor, não Entrou − Saiu", async () => { await mostrar({ saldo: "1234.56" }); expect(card().getByText("R$ 1.234,56")).toBeTruthy(); expect(card().queryByText("R$ 1.699,50")).toBeNull(); });
