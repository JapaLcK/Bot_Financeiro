import { entrouSaiuAcumulados, pontosAcumulados, ritmoDoMes, textoPct, variacao } from "@/features/painel/acumuladoMes";

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
    expect(r).toMatchObject({ diasNoMes: 31, indiceHoje: 5 });
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
  it("esconde: acumulado NEGATIVO (dia com saiu negativo) mesmo com a soma fechando com d.saiu; um dia negativo que não leva o acumulado abaixo de 0 aparece", () => {
    const negativo = caso("2026-10", [dia("2026-10-01", 500), dia("2026-10-02", -800), dia("2026-10-03", 100)]); // acumulado 500, -300, -200
    expect(negativo.d.saiu).toBe(-200); expect(negativo.m.dias.reduce((t, x) => t + x.saiu, 0)).toBe(-200);
    expect(roda(negativo, "2026-10-05")).toBeNull();
    expect(roda(caso("2026-10", [dia("2026-10-01", 500), dia("2026-10-02", -300), dia("2026-10-03", 100)]), "2026-10-05")!.pontos).toEqual([500, 200, 300, 300, 300]);
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

const soma = (ds: ReturnType<typeof dia>[]) => ds.reduce((t, x) => t + x.saiu, 0);
// Outubro (atual) × setembro (anterior); `o` quebra uma regra do mês passado de cada vez.
function ritmo(atual: ReturnType<typeof dia>[], ant: ReturnType<typeof dia>[] | null, hoje: string, o: { resumoAnt?: Record<string, unknown> | null; detalheAnt?: Record<string, unknown> | null; mes?: string } = {}) {
  const c = caso(o.mes ?? "2026-10", atual, { resumo: { anterior: o.resumoAnt !== undefined ? o.resumoAnt : ant && { mes: "2026-09", entrou: 0, saiu: soma(ant) } } });
  const mAnt = o.detalheAnt === null || !ant ? null : ({ ...caso("2026-09", ant).m, ...o.detalheAnt } as M);
  return ritmoDoMes(c.d, c.m, mAnt, hoje);
}
const ATUAL = [dia("2026-10-01", 100), dia("2026-10-02", 200), dia("2026-10-03", 300)];
const ANT = [dia("2026-09-01", 1000), dia("2026-09-02", 500), dia("2026-09-05", 2000)];

describe("ritmoDoMes", () => {
  it("mês corrente: o mês passado entra inteiro (repete o último valor até o fim do mês) e a base é o acumulado dele no MESMO dia de hoje", () => {
    const r = ritmo(ATUAL, ANT, "2026-10-03")!;
    expect(r.anterior!.pontos).toHaveLength(30);
    expect(r.anterior!.pontos.slice(0, 6)).toEqual([1000, 1500, 1500, 1500, 3500, 3500]);
    expect(r).toMatchObject({ gasto: 600, base: 1500 }); // dia 3 de setembro, não o total (3500)
  });
  it("mês passado MAIS CURTO que o dia de hoje: usa o último dia dele (sem o clamp a base viraria undefined)", () => {
    const r = ritmo([dia("2026-10-01", 10), dia("2026-10-31", 20)], ANT, "2026-10-31")!;
    expect(r.atual.pontos).toHaveLength(31);
    expect(r.base).toBe(3500);
  });
  it("mês selecionado já fechado: compara os totais finais, e não o acumulado do mês passado no dia 3", () => {
    const r = ritmo(ATUAL, ANT, "2026-11-15")!;
    expect(r.atual.indiceHoje).toBeNull();
    expect(r).toMatchObject({ gasto: 600, base: 3500 });
  });
  it("mês fechado sem lançamento perto do fim: a série deste mês vai até o último dia (repete o total), gasto e base não mudam", () => {
    const r = ritmo([dia("2026-10-01", 100), dia("2026-10-10", 200)], ANT, "2026-11-15")!;
    expect(r.atual.pontos).toHaveLength(31);
    expect(r.atual.pontos.slice(9)).toEqual(Array(22).fill(300)); // dias 10–31
    expect(r.atual.indiceHoje).toBeNull();
    expect(r).toMatchObject({ gasto: 300, base: 3500 });
  });
  it("mês fechado com lançamento no último dia: série igual à de antes", () => {
    const r = ritmo([dia("2026-10-01", 100), dia("2026-10-31", 200)], ANT, "2026-11-15")!;
    expect(r.atual.pontos).toHaveLength(31);
    expect(r.atual.pontos[29]).toBe(100);
    expect(r.atual.pontos[30]).toBe(300);
  });
  it("mês corrente: a série vai só até hoje (não estende ao futuro)", () => {
    const r = ritmo(ATUAL, ANT, "2026-10-10")!;
    expect(r.atual.pontos).toHaveLength(10);
    expect(r.atual.indiceHoje).toBe(9);
    expect(ritmo(ATUAL, ANT, "2026-10-03")!.atual.pontos).toHaveLength(3);
  });
  // Mês fechado: compara até o ÚLTIMO DIA DO MÊS SELECIONADO (o gráfico corta o mês passado aí); `fecha` = mês selecionado × anterior.
  const fecha = (mes: string, atual: ReturnType<typeof dia>[], mesAnt: string, ant: ReturnType<typeof dia>[]) => {
    const c = caso(mes, atual, { resumo: { anterior: { mes: mesAnt, entrou: 0, saiu: soma(ant) } } });
    return ritmoDoMes(c.d, c.m, caso(mesAnt, ant).m, "2027-06-15")!;
  };
  it("mês fechado CURTO × anterior longo (fev 28 × jan 31): exclui jan 29–31 e a base é o último ponto do mês passado que o gráfico mostra", () => {
    const r = fecha("2027-02", [dia("2027-02-01", 100), dia("2027-02-10", 200)], "2027-01", [dia("2027-01-02", 1000), dia("2027-01-28", 500), dia("2027-01-30", 7000)]);
    expect(r).toMatchObject({ gasto: 300, base: 1500 }); // sem os 7000 dos dias 29–31 (o total de jan seria 8500)
    expect(r.base).toBe(r.anterior!.pontos.slice(0, r.atual.diasNoMes).at(-1)); // o que o CurvaAcumulada desenha
    expect(variacao(r.gasto, r.base!).pct).toBeCloseTo(-80);
  });
  it("mês fechado de 31 dias × anterior de 30: usa o último dia do anterior (clamp)", () => {
    const r = fecha("2026-10", [dia("2026-10-01", 100), dia("2026-10-31", 200)], "2026-09", ANT);
    expect(r).toMatchObject({ gasto: 300, base: 3500 });
  });
  it("mês fechado do mesmo tamanho (31 × 31): compara o último dia dos dois", () => {
    const r = fecha("2026-08", [dia("2026-08-01", 100), dia("2026-08-15", 200)], "2026-07", [dia("2026-07-02", 1000), dia("2026-07-31", 500)]);
    expect(r).toMatchObject({ gasto: 300, base: 1500 });
  });
  it("dias anteriores ao dia 1 do mês passado (fatura) entram só na coerência: a curva dele começa em 0 e a soma ainda fecha", () => {
    const r = ritmo(ATUAL, [dia("2026-08-30", 700), ...ANT], "2026-10-03")!;
    expect(r.anterior!.pontos[0]).toBe(1000);
    expect(r.base).toBe(1500);
  });
  it.each([
    ["d.anterior null", { resumoAnt: null }],
    ["detalhe do mês passado indisponível (null)", { detalheAnt: null }],
    ["detalhe de outro mês (agosto no lugar de setembro)", { detalheAnt: { mes: "2026-08" } }],
    ["mAnt.saiu 1 centavo diferente da soma dos dias", { detalheAnt: { saiu: 3501 } }],
    ["d.anterior.saiu 1 centavo diferente de mAnt.saiu", { resumoAnt: { mes: "2026-09", entrou: 0, saiu: 3501 } }],
    ["histórico cortado dentro do mês passado (inicio_do_historico no detalhe dele)", { detalheAnt: { motivos: ["inicio_do_historico"] } }],
  ])("sem a curva do mês passado, mas com a deste mês: %s", (_n, o) => {
    const r = ritmo(ATUAL, ANT, "2026-10-03", o as never)!;
    expect(r.anterior).toBeNull();
    expect(r.base).toBeNull();
    expect(r.atual.pontos).toEqual([100, 300, 600]);
  });
  it("detalhe do mês passado que é do MESMO mês do resumo (outubro), com o saiu igual ao de d.anterior: não vale como setembro", () => {
    const outubro = ANT.map((x) => dia(x.dia.replace("2026-09", "2026-10"), x.saiu)); // mesma soma de d.anterior.saiu (3500), datas de outubro
    const r = ritmo(ATUAL, ANT, "2026-11-15", { detalheAnt: { mes: "2026-10", dias: outubro } })!;
    expect(r.anterior).toBeNull();
    expect(ritmo(ATUAL, ANT, "2026-11-15")!.anterior).not.toBeNull(); // controle: o setembro de verdade vale
  });
  it("mês passado com acumulado negativo: sem curva dele (mesma regra), com a deste mês", () => {
    const r = ritmo(ATUAL, [dia("2026-09-01", 500), dia("2026-09-02", -800), dia("2026-09-05", 100)], "2026-10-03")!;
    expect(r.anterior).toBeNull();
    expect(r.atual.pontos).toEqual([100, 300, 600]);
  });
  it("mês passado com lançamento só no dia 1 (menos de 2 pontos): sem curva dele", () => {
    expect(ritmo(ATUAL, [dia("2026-09-01", 100)], "2026-10-03")!.anterior).toBeNull();
  });
  it("sem a curva deste mês (hoje é dia 1), não há ritmo, mesmo com o mês passado válido", () => {
    expect(ritmo([dia("2026-10-01", 100)], ANT, "2026-10-01")).toBeNull();
  });
  it("positivo: com o mesmo cenário coerente as duas curvas existem (controle das guardas acima)", () => {
    const r = ritmo(ATUAL, ANT, "2026-10-03")!;
    expect(r.anterior).not.toBeNull();
    expect(r.atual.pontos).toEqual([100, 300, 600]);
  });
});

describe("variacao e textoPct", () => {
  it("a conta da referência: 4.235,39 no mês passado, 2.861,31 neste mês: R$ 1.374,08 abaixo, -32,4%", () => {
    const v = variacao(286131, 423539);
    expect(v).toMatchObject({ sentido: "abaixo", diferenca: 137408 });
    expect(textoPct(v.pct!)).toBe("-32,4%");
  });
  it("acima: gastou mais; diferença em módulo e % positivo com sinal", () => {
    const v = variacao(450000, 400000);
    expect(v).toMatchObject({ sentido: "acima", diferenca: 50000, pct: 12.5 });
    expect(textoPct(v.pct!)).toBe("+12,5%");
  });
  it("igual: diferença 0 e 0,0% sem sinal", () => {
    const v = variacao(1000, 1000);
    expect(v).toMatchObject({ sentido: "igual", diferenca: 0, pct: 0 });
    expect(textoPct(v.pct!)).toBe("0,0%");
  });
  it("base 0 (mês passado sem gasto até o dia): sem %, mas o sentido existe", () => {
    expect(variacao(500, 0)).toMatchObject({ sentido: "acima", diferenca: 500, pct: null });
  });
  it.each([[-0.04, "0,0%"], [-32.44, "-32,4%"], [-32.46, "-32,5%"], [100, "+100,0%"], [0.05, "+0,1%"]])("textoPct(%f) = %s", (n, t) => {
    expect(textoPct(n)).toBe(t);
  });
});

describe("entrouSaiuAcumulados (as duas linhas do topo de Gastos)", () => {
  const d2 = (dia: string, entrou: number, saiu: number) => ({ dia, entrou, saiu });
  const soma = (dias: ReturnType<typeof d2>[], k: "entrou" | "saiu") => dias.reduce((t, x) => t + x[k], 0);
  // `resumo` e `detalhes` fecham com a soma dos dias (o caso coerente); `o` quebra uma regra de cada vez.
  const roda2 = (mes: string, dias: ReturnType<typeof d2>[], hoje: string, o: { resumo?: Record<string, unknown>; detalhe?: Record<string, unknown> } = {}) => {
    const tot = { entrou: soma(dias, "entrou"), saiu: soma(dias, "saiu") };
    return entrouSaiuAcumulados({ mes, ...tot, motivos: [], ...o.resumo } as unknown as R, { mes, ...tot, dias, motivos: [], ...o.detalhe } as unknown as M, hoje);
  };
  it("mês corrente: acumula Entrou e Saiu por dia até o fim do mês e a ponta bate com o total, incluindo a saída AGENDADA depois de hoje (degrau)", () => {
    const r = roda2("2026-10", [d2("2026-10-01", 720000, 90000), d2("2026-10-03", 0, 24000), d2("2026-10-20", 5000, 7000)], "2026-10-05")!;
    expect(r.diasNoMes).toBe(31); expect(r.saiu).toHaveLength(20); // até o último dia com lançamento (20), não até hoje (5) nem o dia 31
    expect(r.saiu[4]).toBe(114000); expect(r.saiu[18]).toBe(114000); expect(r.saiu[19]).toBe(121000); // degrau no dia 20
    expect(r.saiu.at(-1)).toBe(121000); expect(r.entrou.at(-1)).toBe(725000);
  });
  it("sem lançamento depois de hoje, a linha para em hoje (não inventa rabo reto)", () => {
    const r = roda2("2026-10", [d2("2026-10-01", 100, 50), d2("2026-10-02", 0, 10)], "2026-10-04")!;
    expect(r.saiu).toEqual([50, 60, 60, 60]); expect(r.entrou).toEqual([100, 100, 100, 100]);
  });
  it("mês passado: vai até o último dia do mês e as pontas são o total", () => {
    const r = roda2("2026-09", [d2("2026-09-02", 100, 50), d2("2026-09-30", 200, 70)], "2026-10-15")!;
    expect(r.saiu).toHaveLength(30); expect(r.entrou.at(-1)).toBe(300); expect(r.saiu.at(-1)).toBe(120);
  });
  it("dia ANTES do mês (fatura de cartão do mês anterior) entra no dia 1: a ponta bate com o total do card", () => {
    const r = roda2("2026-10", [d2("2026-09-28", 0, 5000), d2("2026-10-02", 0, 200)], "2026-10-03")!;
    expect(r.saiu).toEqual([5000, 5200, 5200]);
  });
  it("tolera 1 centavo de diferença entre a ponta e o resumo; 2 centavos já escondem", () => {
    const dias = [d2("2026-10-01", 100, 50), d2("2026-10-03", 0, 10)];
    expect(roda2("2026-10", dias, "2026-10-04", { resumo: { saiu: 61 }, detalhe: { saiu: 61 } })).not.toBeNull();
    expect(roda2("2026-10", dias, "2026-10-04", { resumo: { saiu: 62 }, detalhe: { saiu: 62 } })).toBeNull();
  });
  it.each([
    ["resumo.saiu 99999 contra detalhes 15000 (corrida/outra moeda)", () => roda2("2026-10", [d2("2026-10-02", 0, 15000), d2("2026-10-03", 5, 0)], "2026-10-05", { resumo: { saiu: 99999 } })],
    ["resumo.entrou diferente do detalhes", () => roda2("2026-10", [d2("2026-10-02", 700, 15000), d2("2026-10-03", 5, 0)], "2026-10-05", { resumo: { entrou: 10 } })],
    ["a ponta calculada não fecha com o resumo (detalhes.saiu mentiu)", () => roda2("2026-10", [d2("2026-10-02", 0, 15000), d2("2026-10-03", 5, 0)], "2026-10-05", { resumo: { saiu: 20000 }, detalhe: { saiu: 20000 } })],
    ["detalhe de outro mês", () => entrouSaiuAcumulados({ mes: "2026-10", entrou: 2, saiu: 2, motivos: [] } as unknown as R, { mes: "2026-09", entrou: 2, saiu: 2, dias: [d2("2026-09-02", 1, 1), d2("2026-09-03", 1, 1)], motivos: [] } as unknown as M, "2026-10-05")],
    ["inicio_do_historico dentro do mês", () => roda2("2026-10", [d2("2026-10-02", 1, 1), d2("2026-10-03", 1, 1)], "2026-10-05", { detalhe: { motivos: ["inicio_do_historico"] } })],
    ["tudo zero", () => roda2("2026-10", [d2("2026-10-02", 0, 0)], "2026-10-05")],
    ["sem dias", () => roda2("2026-10", [], "2026-10-05")],
    ["dia 1 do mês corrente sem lançamento depois (um ponto só)", () => roda2("2026-10", [d2("2026-10-01", 1, 1)], "2026-10-01")],
    ["mês futuro", () => roda2("2026-12", [d2("2026-12-02", 1, 1)], "2026-10-05")],
    ["data inválida", () => roda2("2026-10", [d2("hoje", 1, 1)], "2026-10-05")],
  ])("vazio (null, sem erro): %s", (_n, f) => { expect(f()).toBeNull(); });
});
