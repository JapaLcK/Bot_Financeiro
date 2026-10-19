import type { Lancamento } from "@/api/schemas/painel";
import { diasDoExtrato, rotuloDia } from "@/features/painel/extratoDias";
import { ATALHOS, atalhoDe } from "@/features/painel/extratoFiltros";

// Funções puras do Extrato agrupado por dia: o rótulo do cabeçalho (com `hoje` por parâmetro) e o total do grupo.
const HOJE = "2026-10-19"; // segunda
describe("rotuloDia", () => {
  it.each([
    [HOJE, HOJE, "Hoje · 19/10"],
    ["2026-10-18", HOJE, "Ontem · 18/10"],
    ["2026-10-17", HOJE, "Sáb · 17/10"],
    ["2026-10-16", HOJE, "Sex · 16/10"],
    ["2027-01-01", "2027-01-02", "Ontem · 01/01"],
    ["2026-12-31", "2027-01-01", "Ontem · 31/12"], // a virada de ano: Ontem nunca leva "/aa"
    ["2026-12-31", "2027-01-05", "Qui · 31/12/26"], // outro ano acrescenta "/aa"
    ["2026-02-29", HOJE, "29/02/2026"], // dia que não existe: sem dia da semana, cai no formatador de data atual
    ["2026-1-5", HOJE, "Data não informada"], // malformado cai no formatador de data atual
    ["", HOJE, "Data não informada"],
  ])("%s com hoje=%s -> %s", (dia, hoje, esperado) => { expect(rotuloDia(dia, hoje)).toBe(esperado); });
});

const l = (id: string, data: string, tipo: "entrada" | "saida", valor: number, extra: Partial<Lancamento> = {}) => ({ id, data, tipo, valor, moeda: "BRL", interno: false, ...extra }) as Lancamento;
describe("diasDoExtrato", () => {
  it("soma entrada e saída com sinal; a saída negativa entra pelo módulo", () => {
    const [d] = diasDoExtrato([l("a", "2026-10-19", "entrada", 12000), l("b", "2026-10-19", "saida", 3240), l("c", "2026-10-19", "saida", -1000)], true);
    expect(d).toMatchObject({ dia: "2026-10-19", total: 12000 - 3240 - 1000 });
  });
  it("interno e moeda diferente de BRL ficam fora do total", () => {
    const [d] = diasDoExtrato([l("a", "2026-10-19", "saida", 5000), l("b", "2026-10-19", "saida", 9999, { interno: true }), l("c", "2026-10-19", "saida", 7777, { moeda: "USD" })], true);
    expect(d!.total).toBe(-5000); expect(d!.itens).toHaveLength(3); // ficam na lista, só não somam
  });
  it("dia em que nada conta (só interno, só USD) tem total null, nunca zero", () => {
    const r = diasDoExtrato([l("a", "2026-10-19", "saida", 5000, { interno: true }), l("b", "2026-10-18", "entrada", 300, { moeda: "USD" })], true);
    expect(r.map((d) => d.total)).toEqual([null, null]);
  });
  it("agrupa pela 1ª aparição, mesmo com dias intercalados", () => {
    const r = diasDoExtrato([l("a", "2026-10-19", "saida", 100), l("b", "2026-10-18", "saida", 200), l("c", "2026-10-19", "saida", 300)], true);
    expect(r.map((d) => [d.dia, d.itens.map((i) => i.id), d.total])).toEqual([["2026-10-19", ["a", "c"], -400], ["2026-10-18", ["b"], -200]]);
  });
  it("lista incompleta: só o ÚLTIMO grupo perde o total (controle: completa mantém); lista vazia não quebra", () => {
    const itens = [l("a", "2026-10-19", "saida", 100), l("b", "2026-10-18", "saida", 200)];
    expect(diasDoExtrato(itens, false).map((d) => d.total)).toEqual([-100, null]);
    expect(diasDoExtrato(itens, true).map((d) => d.total)).toEqual([-100, -200]);
    expect(diasDoExtrato([], false)).toEqual([]);
  });
});

describe("atalhoDe", () => {
  it.each([["", "", "Tudo"], ["saida", "", "Saídas"], ["", "cartao", "Cartão"]])("(%j, %j) -> %s", (tipo, origem, nome) => { expect(atalhoDe({ tipo, origem })?.[2]).toBe(nome); });
  it("combinação (saída + banco) não é atalho nenhum; a lista tem 4", () => { expect(atalhoDe({ tipo: "saida", origem: "banco" })).toBeUndefined(); expect(ATALHOS).toHaveLength(4); });
});
