import { hojeSP, mesAbrev, mesAtual } from "@/features/painel/catalogo";

afterEach(() => { jest.useRealTimers(); });

// O fuso de Brasília é provado aqui no Node; o `Intl` do Hermes só se prova no aparelho.
describe("hojeSP / mesAtual", () => {
  it.each([
    ["2026-10-15T15:00:00Z", "2026-10-15"],
    ["2026-11-01T02:30:00Z", "2026-10-31"], // 23:30 de 31/10 em Brasília já é 01/11 em UTC: o mês ainda é outubro
    ["2026-11-01T03:30:00Z", "2026-11-01"], // 00:30 de 01/11 em Brasília
  ])("%s UTC -> %s em Brasília", (instante, esperado) => {
    jest.useFakeTimers().setSystemTime(new Date(instante));
    expect(hojeSP()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(hojeSP()).toBe(esperado);
    expect(mesAtual()).toMatch(/^\d{4}-(0[1-9]|1[0-2])$/);
    expect(mesAtual()).toBe(esperado.slice(0, 7));
  });
});

describe("mesAbrev", () => {
  it.each([["2026-05", "mai"], ["2026-10", "out"], ["2026-03", "mar"], ["2026-13", "2026-13"], ["lixo", "lixo"]])("mesAbrev(%s) = %s", (mes, esperado) => {
    expect(mesAbrev(mes)).toBe(esperado);
  });
});
