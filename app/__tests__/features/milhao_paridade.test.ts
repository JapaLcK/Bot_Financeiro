import * as app from "@/features/painel/milhao";
// `milhao.ts` copia o web (§0.7); o `import` estático do web quebraria o tsc do app (noUncheckedIndexedAccess), então entra por `requireActual`.
const web = jest.requireActual<typeof import("@/features/painel/milhao")>("../../../webapp/src/dashboard/lib/milhao");

type Modulo = typeof app;
const P = (m: Modulo, patrimonio: number, aporte: number, taxaAnual: number, inicio = "2026-10") => m.projetar({ patrimonio, aporte, taxaAnual, inicio });

// Os casos de `tests/frontend/dashboard_v2_milhao_projecao.test.mjs`, com os esperados escritos à mão.
describe.each([["app", app], ["web", web]] as [string, Modulo][])("conta do milhão: %s", (_n, m) => {
  it("sem rendimento, R$ 2.000 por mês levam 500 meses", () => { expect(P(m, 0, 2000, 0).meses).toBe(500); });
  it("o prazo é de 600 meses: 600 vale, 601 é sem previsão", () => {
    expect(P(m, 400000, 1000, 0).meses).toBe(600);
    const fora = P(m, 399000, 1000, 0);
    expect([fora.meses, fora.chegada, fora.pontos.length]).toEqual([null, null, 601]);
  });
  it("12% ao ano: R$ 100 viram R$ 112 em 12 meses", () => { expect(Math.abs(P(m, 100, 0, 12).pontos[12]! - 112)).toBeLessThan(1e-9); });
  it("patrimônio negativo não rende e o aporte entra", () => { const r = P(m, -1000, 1000, 12); expect([r.pontos[1], r.meses]).toEqual([0, 250]); });
  it("R$ 100 mil a 10%: 290 meses, dezembro de 2050, 24 anos e 2 meses", () => {
    const r = P(m, 100000, 0, 10);
    expect([r.meses, r.chegada, r.anos, r.mesesResto]).toEqual([290, "2050-12", 24, 2]); expect(m.duracao(r.anos, r.mesesResto)).toBe("24 anos e 2 meses");
  });
  it("exatamente R$ 1 milhão já passou: meta 2 milhões", () => { const r = P(m, 1000000, 0, 10); expect([r.meta, r.faltaCentavos]).toEqual([2e6, 1e8]); });
  it("R$ 1.250.000 a 10%: meta 2 milhões em 60 meses", () => { const r = P(m, 1250000, 0, 10); expect([r.meta, r.meses, r.anos, r.mesesResto]).toEqual([2e6, 60, 5, 0]); });
  it("R$ 500 a 10%, sem aporte: sem previsão", () => { expect(P(m, 500, 0, 10).meses).toBeNull(); });
  it("falta em centavos e percentual para baixo", () => {
    expect([P(m, 48250.37, 0, 10).faltaCentavos, P(m, 48250.37, 0, 10).pct]).toEqual([95174963, 4]);
    expect(P(m, 999999.99, 0, 0).pct).toBe(99); expect(P(m, -5, 0, 0).pct).toBe(0);
  });
  it("o mês de chegada vira o ano", () => { expect(P(m, 999000, 1000, 0).chegada).toBe("2026-11"); expect(P(m, 997000, 1000, 0).chegada).toBe("2027-01"); });
  it("duração: singular, plural e a parte zero some", () => {
    expect([m.duracao(1, 0), m.duracao(0, 1), m.duracao(2, 1), m.duracao(0, 10)]).toEqual(["1 ano", "1 mês", "2 anos e 1 mês", "10 meses"]);
  });
});

/** O que os dois lados dizem de diferente numa grade; lista vazia = paridade. `projetar` inteiro, `pontos` incluídos. */
function divergencias(a: Modulo, b: Modulo): string[] {
  const d: string[] = [];
  for (const pat of [-1000, 0, 500, 48250.37, 399000, 999999.99, 1e6, 1250000, 5e6]) for (const ap of [0, 50, 1000, 10000]) for (const tx of [0, 0.5, 10, 15]) for (const ini of ["2026-10", "2026-12"]) {
    try { expect(P(a, pat, ap, tx, ini)).toEqual(P(b, pat, ap, tx, ini)); } catch { d.push(`projetar ${pat}/${ap}/${tx}/${ini}`); }
  }
  for (let anos = 0; anos <= 30; anos++) for (let meses = 0; meses <= 11; meses++) if (a.duracao(anos, meses) !== b.duracao(anos, meses)) d.push(`duracao ${anos}/${meses}`);
  return d;
}
describe("paridade app × web", () => {
  it("a grade tem 'sem previsão' e meta de 2 milhões (senão ela não mede esses ramos)", () => {
    const todos = [-1000, 0, 500, 48250.37, 399000, 999999.99, 1e6, 1250000, 5e6].flatMap((pat) => [0, 50, 1000, 10000].flatMap((ap) => [0, 0.5, 10, 15].map((tx) => P(app, pat, ap, tx))));
    expect(todos.some((r) => r.meses === null)).toBe(true); expect(todos.some((r) => r.meta === 2e6)).toBe(true);
  });
  it("positivo: o app e o web dão o mesmo em toda a grade", () => { expect(divergencias(app, web)).toEqual([]); });
  it("controle negativo: um prazo desviado e a taxa mensal simples (anual/12) são acusados", () => {
    expect(divergencias(app, { ...app, projetar: (x) => ({ ...app.projetar(x), meses: 1 }) }).length).toBeGreaterThan(0);
    const simples: Modulo = { ...app, projetar: (x) => app.projetar({ ...x, taxaAnual: ((1 + x.taxaAnual / 1200) ** 12 - 1) * 100 }) }; // taxa mensal = anual / 12
    expect(divergencias(app, simples).length).toBeGreaterThan(0);
  });
});
