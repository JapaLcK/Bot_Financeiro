import { legendaReferencia, pontosAcumulados } from "@/features/painel/acumuladoMes";

type R = Parameters<typeof pontosAcumulados>[0];
type M = Parameters<typeof pontosAcumulados>[1];
const dia = (d: string, saiu: number) => ({ dia: d, entrou: 0, saiu });
// `saiu` do resumo e do detalhe = soma dos dias (o caso em que tudo fecha); `o` sobrescreve para quebrar uma regra de cada vez.
function caso(mes: string, dias: ReturnType<typeof dia>[], o: { resumo?: Record<string, unknown>; detalhe?: Record<string, unknown> } = {}) {
  const saiu = dias.reduce((s, x) => s + x.saiu, 0);
  const d = { mes, ate: `${mes}-28`, entrou: 0, saiu, anterior: { mes: "2026-09", entrou: 0, saiu: 411000 }, motivos: [], ...o.resumo } as unknown as R;
  const m = { mes, ate: `${mes}-28`, entrou: 0, saiu, dias, motivos: [], ...o.detalhe } as unknown as M;
  return { d, m };
}
const roda = (c: { d: R; m: M }, hoje: string) => pontosAcumulados(c.d, c.m, hoje);

describe("pontosAcumulados", () => {
  it("positivo: mês corrente com tudo fechando acumula por dia, vai até hoje e marca hoje", () => {
    const r = roda(caso("2026-10", [dia("2026-10-01", 90000), dia("2026-10-02", 41000), dia("2026-10-04", 30000), dia("2026-10-06", 28050)]), "2026-10-06")!;
    expect(r.pontos).toEqual([90000, 131000, 131000, 161000, 161000, 189050]); // dias sem lançamento repetem o acumulado
    expect(r).toMatchObject({ diasNoMes: 31, indiceHoje: 5, referencia: 411000 });
  });
  it("mês passado: vai até o último dia com lançamento e não marca hoje", () => {
    const r = roda(caso("2026-10", [dia("2026-10-01", 100), dia("2026-10-03", 250)]), "2026-11-15")!;
    expect(r.pontos).toEqual([100, 100, 350]);
    expect(r.indiceHoje).toBeNull();
  });
  it("dia ANTERIOR ao mês (compra de cartão na fatura do mês) fica FORA da curva, que começa em 0, e a soma geral ainda fecha", () => {
    const c = caso("2026-10", [dia("2026-09-28", 5000), dia("2026-10-01", 100), dia("2026-10-02", 200)]);
    const r = roda(c, "2026-10-03")!;
    expect(r.pontos).toEqual([100, 300, 300]);
    expect(r.pontos.at(-1)).toBe(100 + 200); // soma só dos dias do mês...
    expect(c.d.saiu).toBe(5300); // ...enquanto o Saiu do card inclui a fatura do mês anterior
  });
  it("esconde: dia anterior ao mês alterado em 1 centavo faz a soma geral não fechar (a coerência olha TODOS os dias); sem a alteração aparece", () => {
    const dias = [dia("2026-09-28", 5000), dia("2026-10-01", 100), dia("2026-10-02", 200)];
    const c = caso("2026-10", dias);
    expect(roda(c, "2026-10-03")).not.toBeNull();
    const torto = { d: c.d, m: { ...c.m, dias: [dia("2026-09-28", 5001), ...dias.slice(1)] } as unknown as M };
    expect(roda(torto, "2026-10-03")).toBeNull();
  });
  it.each([
    ["dia POSTERIOR ao fim do mês", "2026-10", [dia("2026-10-01", 100), dia("2026-11-01", 50)], "2026-10-20"],
    ["dia que não existe no mês (30/02)", "2026-02", [dia("2026-02-01", 100), dia("2026-02-30", 50)], "2026-03-10"],
    ["lançamento com valor depois de hoje no mês corrente", "2026-10", [dia("2026-10-01", 100), dia("2026-10-09", 50)], "2026-10-06"],
    ["menos de 2 pontos (hoje é dia 1)", "2026-10", [dia("2026-10-01", 100)], "2026-10-01"],
    ["mês futuro", "2026-10", [dia("2026-10-01", 100), dia("2026-10-03", 50)], "2026-09-20"],
    ["mês passado com lançamento só no dia 1", "2026-10", [dia("2026-10-01", 100)], "2026-11-15"],
    ["dia fora do formato", "2026-10", [dia("2026-10-01", 100), dia("1/10", 50)], "2026-10-20"],
  ])("esconde: %s", (_n, mes, dias, hoje) => {
    expect(roda(caso(mes, dias), hoje)).toBeNull();
  });
  it("esconde: d.mes ou hoje malformados, mesmo com o resto coerente (sem depender das outras guardas)", () => {
    expect(roda(caso("2026-13", [dia("2026-13-01", 100), dia("2026-13-02", 100)]), "2027-01-05")).toBeNull(); // mês 13: sem a guarda, "2026-13" < "2027-01" passaria
    expect(roda(caso("2026-10", [dia("2026-10-01", 100), dia("2026-10-02", 100)]), "x")).toBeNull(); // "2026-10" > "x" é falso: sem a guarda, viraria mês passado
  });
  it("esconde: detalhe de outro mês que o resumo (m.mes !== d.mes)", () => {
    expect(roda(caso("2026-10", [dia("2026-10-01", 100), dia("2026-10-02", 100)], { detalhe: { mes: "2026-09" } }), "2026-10-05")).toBeNull();
  });
  it("esconde: m.saiu diferente de d.saiu, nem que seja por 1 centavo", () => {
    expect(roda(caso("2026-10", [dia("2026-10-01", 100), dia("2026-10-02", 100)], { detalhe: { saiu: 201 } }), "2026-10-05")).toBeNull();
  });
  it("esconde: soma dos dias difere de d.saiu por 1 centavo (outra moeda no total legado); com a soma exata aparece", () => {
    const dias = [dia("2026-10-01", 100), dia("2026-10-02", 100)];
    const igual = caso("2026-10", dias);
    expect(roda(igual, "2026-10-05")).not.toBeNull();
    expect(roda({ d: { ...igual.d, saiu: 201 }, m: { ...igual.m, saiu: 201 } }, "2026-10-05")).toBeNull();
    expect(roda({ d: { ...igual.d, saiu: 199 }, m: { ...igual.m, saiu: 199 } }, "2026-10-05")).toBeNull();
  });
  it("esconde: histórico do plano cortado DENTRO do mês (m.motivos); o mesmo cenário sem o motivo aparece", () => {
    const dias = [dia("2026-10-04", 100), dia("2026-10-05", 200)];
    expect(roda(caso("2026-10", dias), "2026-10-06")).not.toBeNull();
    expect(roda(caso("2026-10", dias, { detalhe: { motivos: ["inicio_do_historico"] } }), "2026-10-06")).toBeNull();
  });
  it("corte só no mês ANTERIOR (motivo apenas em d.motivos): o mês está completo e a curva aparece", () => {
    const dias = [dia("2026-10-01", 100), dia("2026-10-02", 200)];
    expect(roda(caso("2026-10", dias, { resumo: { motivos: ["inicio_do_historico"] } }), "2026-10-06")).not.toBeNull();
  });
  it("sem anterior (null), ou anterior zerado, não há referência, mas a curva aparece", () => {
    const dias = [dia("2026-10-01", 100), dia("2026-10-02", 100)];
    expect(roda(caso("2026-10", dias, { resumo: { anterior: null } }), "2026-10-05")).toMatchObject({ referencia: null });
    expect(roda(caso("2026-10", dias, { resumo: { anterior: { mes: "2026-09", entrou: 0, saiu: 0 } } }), "2026-10-05")).toMatchObject({ referencia: null });
  });
  it.each([["2026-02", 28], ["2028-02", 29], ["2026-04", 30], ["2026-10", 31]])("diasNoMes de %s = %i", (mes, n) => {
    expect(roda(caso(mes, [dia(`${mes}-01`, 1), dia(`${mes}-${n}`, 1)]), "2030-01-01")!.diasNoMes).toBe(n);
    expect(roda(caso(mes, [dia(`${mes}-01`, 1), dia(`${mes}-${n}`, 1)]), "2030-01-01")!.pontos).toHaveLength(n);
  });
  it("centavos grandes (inteiros seguros) somam exatos; passou do seguro, esconde", () => {
    const grande = 4_000_000_000_000_000; // < 2^53 = 9,007e15
    expect(roda(caso("2026-10", [dia("2026-10-01", grande), dia("2026-10-02", 1)]), "2026-10-05")!.pontos).toEqual([grande, grande + 1, grande + 1, grande + 1, grande + 1]);
    expect(roda(caso("2026-10", [dia("2026-10-01", 2 ** 53), dia("2026-10-02", 1)]), "2026-10-05")).toBeNull();
  });
});

describe("legendaReferencia", () => {
  it("nome do mês anterior em maiúscula + valor em reais", () => {
    expect(legendaReferencia(411000, "2026-09")).toBe("Setembro fechou em R$ 4.110,00");
    expect(legendaReferencia(5, "2026-12")).toBe("Dezembro fechou em R$ 0,05");
  });
  it("sem referência, sem legenda", () => {
    expect(legendaReferencia(null, "2026-09")).toBeNull();
  });
});
