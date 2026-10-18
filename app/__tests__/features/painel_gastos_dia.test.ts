import { ContratoInvalido, chamar } from "@/api/client";
import { gastosSchema } from "@/api/schemas/painel";
import { decimalParaCentavos } from "@/features/painel/decimal";
import { contraMediaDoDia, diaAbrivel, mediaDoPeriodo, vizinho } from "@/features/painel/gastosDia";
import campos from "../../../tests/fixtures/app_gastos_campos_lidos.json";
import fixture from "../../../tests/fixtures/app_gastos.json";
import tabela from "../../../tests/fixtures/gastos_dia_casos.json";
// A conta do "Seu dia" (`gastosDia.ts`) contra a tabela dourada que o WEB gera (`scripts/gerar_gastos_dia_casos.mjs`, lida também pelo teste web), e o contrato de `/api/app/gastos`.

type Caso = (typeof tabela)[number];
const comBruto = (c: Caso) => ({ ...c.entrada, dias: c.entrada.dias.map((g) => ({ dia: g.dia, total: { bruto: g.total } })) });
/** A mesma saída que o gerador tira do web, agora do `gastosDia.ts`. */
function calcular(c: Caso) {
  const d = comBruto(c), { media, nDias, vividos } = mediaDoPeriodo(d);
  const dias = d.dias.map((g) => { const r = contraMediaDoDia(g, d); return { dia: g.dia, pct: r?.pct ?? null, tom: r?.tom ?? null }; });
  const grade = c.saida.grade.map(({ dia }) => ({ dia, abrivel: diaAbrivel(dia, d), anterior: vizinho(dia, -1, d), proximo: vizinho(dia, 1, d) }));
  return { media, nDias, vividos: vividos.length, grade, dias };
}

describe("paridade com a tabela dourada gerada pelo web", () => {
  it("a tabela cobre os casos que provam a emulação do double (e não é vazia)", () => {
    expect(tabela.length).toBeGreaterThanOrEqual(10);
    const nomes = tabela.map((c) => c.nome).join("|");
    for (const parte of ["1.040 e 1.137", "0.191 e 0.209", "91.010 × 1.005", "6 casas", "empate no meio"]) expect(nomes).toContain(parte);
  });
  it.each(tabela.map((c) => [c.nome, c] as const))("%s", (_nome, c) => { expect(calcular(c)).toEqual(c.saida); });
  // Controle negativo permanente da tabela: "centavos primeiro" (a alternativa descartada) DISCORDA dela no caso 1.040/1.137, então a tabela mede a emulação.
  it("centavos primeiro daria outro tom no caso 1.040/1.137 (−5% positivo, contra −4% neutro do web)", () => {
    const c = tabela[0]!, cent = c.entrada.dias.map((g) => decimalParaCentavos(g.total)), media = (cent[0]! + cent[1]!) / 2;
    expect(Math.round((cent[0]! / media - 1) * 100)).toBe(-5);
    expect(c.saida.dias[0]).toEqual({ dia: "2026-10-01", pct: -4, tom: "" });
  });
});

describe("gastosSchema (/api/app/gastos)", () => {
  const lido = gastosSchema.parse(fixture);
  it("lê só o que a folha usa: centavos para exibir e o texto bruto de total/categorias[].valor para decidir; maiores[].valor só em centavos; sem cor", () => {
    expect(lido.dias.map((d) => d.total)).toEqual([
      { centavos: 114, bruto: "1.137" }, { centavos: 9101, bruto: "91.010" }, { centavos: 1, bruto: "0.010" }, { centavos: 25000, bruto: "250.00" },
    ]);
    expect(lido.dias[2]!.categorias).toEqual([{ chave: "alimentacao", valor: { centavos: 1, bruto: "0.006" } }, { chave: "transporte", valor: { centavos: 0, bruto: "0.004" } }]); // sub-centavo: o bruto decide, os centavos só exibem
    expect(lido.dias[1]!.maiores[0]).toEqual({ descricao: "Mercado", valor: 9001, categoria: "mercado" }); // 90.005 → 90,01
    expect(lido.categorias[0]).toEqual({ chave: "lazer", nome: "Lazer", emoji: "🎮" });
    expect(lido).toMatchObject({ hoje: "2026-10-14", disponivel: true, historico_desde: null, motivos: ["banco_desatualizado", "arredondamento_por_grupo"] });
    expect(Object.keys(lido).sort()).toEqual(["categorias", "dias", "disponivel", "historico_desde", "hoje", "motivos"]); // nada de mes, total, cor...
  });
  it("campo a mais do servidor é ignorado (sem .strict())", () => { expect(gastosSchema.safeParse({ ...fixture, campo_novo: 1, dias: fixture.dias.map((d) => ({ ...d, novo: true })) }).success).toBe(true); });
  it("os campos lidos versionados (conferidos no servidor por tests/test_api_v2_gastos.py) são os do schema", () => {
    const dia = gastosSchema.shape.dias.element.shape;
    expect({ Gastos: Object.keys(gastosSchema.shape), GastoDia: Object.keys(dia), GastoMaior: Object.keys(dia.maiores.element.shape), GastoDiaCategoria: Object.keys(dia.categorias.element.shape), GastoCategoria: Object.keys(gastosSchema.shape.categorias.element.shape) })
      .toEqual(campos);
  });
  it.each([["abc"], ["1,5"], ["NaN"], ["Infinity"], [""], ["1e3"]])("dinheiro inválido %j em total, categoria ou maior compra: ContratoInvalido pelo `chamar`, nunca 0", async (ruim) => {
    const dia = fixture.dias[0]!, com = (d: object) => ({ ...fixture, dias: [{ ...dia, ...d }] });
    for (const quebrado of [com({ total: ruim }), com({ categorias: [{ chave: "x", valor: ruim }] }), com({ maiores: [{ ...dia.maiores[0]!, valor: ruim }] })]) {
      globalThis.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => quebrado }) as unknown as typeof fetch;
      await expect(chamar("/api/app/gastos?mes=2026-10", gastosSchema)).rejects.toBeInstanceOf(ContratoInvalido);
    }
  });
  it("controle positivo: a fixture válida passa pelo `chamar`", async () => {
    globalThis.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => fixture }) as unknown as typeof fetch;
    await expect(chamar("/api/app/gastos?mes=2026-10", gastosSchema)).resolves.toMatchObject({ hoje: "2026-10-14" });
  });
});
