import { lerEvento, periodoSchema } from "@/api/schemas/painel";
import { TITULOS } from "@/features/painel/catalogo";
import fixture from "./painel.fixture.json";
import periodoJson from "../../../tests/fixtures/ai_chat_bloco_periodo.json";
import metasV2 from "../../../tests/frontend/api_v2_respostas.json";
import exemplos from "../../../tests/fixtures/ai_chat_eventos.json"; // lido também pelo pytest (tests/test_ai_chat_stream.py)

const ex = exemplos as Record<string, Record<string, unknown>>;
const sem = (tipo: string, chave: string) => { const { [chave]: _, ...resto } = ex[tipo]!; return resto; };
const bloco = (b: object) => ({ ...ex.bloco, bloco: { ...(ex.bloco!.bloco as object), ...b } });
const aba = (mes: string | null) => ({ mes, recursos: {} });

it.each(Object.keys(ex))("a fixture compartilhada: o exemplo de `%s` é aceito", (tipo) => { expect(lerEvento(ex[tipo])?.tipo).toBe(tipo); });
it.each([["passo", "texto"], ["bloco", "bloco"], ["delta", "texto"], ["fim", "reply"], ["fim", "usage"], ["fim", "message_id"]])("`%s` sem `%s` é descartado", (tipo, chave) => { expect(lerEvento(sem(tipo, chave))).toBeNull(); });
it("tipo desconhecido, sem tipo e lixo: null", () => { expect(lerEvento({ tipo: "novo" })).toBeNull(); expect(lerEvento({})).toBeNull(); expect(lerEvento(null)).toBeNull(); expect(lerEvento({ tipo: "toString" })).toBeNull(); });
it("bloco com card fora do catálogo, com 4 abas, sem aba ou com mês malformado: null", () => {
  expect(lerEvento(bloco({ card: "inventado" }))).toBeNull();
  expect(lerEvento(bloco({ abas: [aba(null), aba(null), aba(null), aba(null)] }))).toBeNull();
  expect(lerEvento(bloco({ abas: [] }))).toBeNull();
  for (const mes of ["2026-13", "2026-1", "outubro"]) expect(lerEvento(bloco({ abas: [aba(mes)] }))).toBeNull();
  expect(lerEvento(bloco({ abas: [aba("2026-10"), aba(null)] }))?.tipo).toBe("bloco");
});
it("fim com chips inválidos continua válido, com chips vazios; passo com prévia desconhecida vira null", () => {
  for (const chips of [["", "x"], ["a".repeat(41)], ["1", "2", "3", "4", "5"], "texto", [1]]) { const e = lerEvento({ ...ex.fim, chips }); expect(e).toMatchObject({ tipo: "fim", reply: "Oi!", chips: [] }); }
  expect(lerEvento({ ...ex.passo, previa: "inventado" })).toMatchObject({ tipo: "passo", previa: null });
  expect(lerEvento({ ...ex.fim, message_id: 0 })).toBeNull();
});

// O card `periodo` (só do chat): fixture compartilhada com o pytest (tests/test_ai_chat_periodo.py).
const periodo = periodoJson as Record<string, unknown>;
const blocoPeriodo = { tipo: "bloco", bloco: { card: "periodo", abas: [{ mes: null, recursos: { periodo } }] } };
it("bloco `periodo` com uma aba de `mes: null` é aceito; card inventado continua null; `periodo` não é widget", () => {
  expect(lerEvento(blocoPeriodo)?.tipo).toBe("bloco");
  expect(lerEvento({ ...blocoPeriodo, bloco: { ...blocoPeriodo.bloco, card: "inventado" } })).toBeNull();
  expect(Object.keys(TITULOS)).not.toContain("periodo");
});
it("`periodoSchema` aceita a fixture compartilhada e recusa a fixture sem `meses`", () => {
  expect(periodoSchema.safeParse(periodo).success).toBe(true);
  const { meses: _, ...semMeses } = periodo;
  expect(periodoSchema.safeParse(semMeses).success).toBe(false);
});

// Os recursos embutidos no `bloco` passam pelo schema de cada recurso (os mesmos do provider); fixture do painel.
const contas = (fixture as Record<string, Record<string, unknown>>)["/api/app/contas"]!;
const comRecursos = (recursos: Record<string, unknown>) => bloco({ abas: [{ mes: null, recursos }] });
type Lido = { bloco: { abas: { recursos: Record<string, any> }[] } };
it("bloco com `contas` válido é aceito, com o dinheiro em centavos", () => {
  const e = lerEvento(comRecursos({ contas })) as unknown as Lido;
  expect(e.bloco.abas[0]!.recursos.contas.total).toBe(940025); expect(e.bloco.abas[0]!.recursos.contas.carteira.saldo).toBe(45000);
});
it("`contas` inválido ao lado de `detalhes` (sincronização do card de gastos) sai do bloco sem invalidá-lo; sozinho, invalida", () => {
  const detalhes = fixture["/api/app/mes-detalhes" as keyof typeof fixture];
  const e = lerEvento(comRecursos({ detalhes, contas: {} })) as unknown as Lido;
  expect(Object.keys(e.bloco.abas[0]!.recursos)).toEqual(["detalhes"]);
  expect(lerEvento(comRecursos({ contas: {} }))).toBeNull(); expect(lerEvento(comRecursos({ detalhes: {}, contas }))).toBeNull(); // `detalhes` inválido segue invalidando
});
it("recurso inválido invalida o evento: `contas: {}`, e `periodo` sem `meses`", () => {
  expect(lerEvento(comRecursos({ contas: {} }))).toBeNull();
  const { meses: _, ...semMeses } = periodo; expect(lerEvento(comRecursos({ periodo: semMeses }))).toBeNull();
});
it("`detalhes` com `origens`: aceito, em centavos; origem inválida invalida o bloco; sem `origens` segue aceito", () => {
  const detalhes = (fixture as Record<string, Record<string, unknown>>)["/api/app/mes-detalhes"]!, origens = { cartao: "600.00", banco: "400.00", carteira: "0.00", registro_antigo: "0.00" };
  const e = lerEvento(comRecursos({ detalhes: { ...detalhes, origens } })) as unknown as Lido; expect(e.bloco.abas[0]!.recursos.detalhes.origens).toEqual({ cartao: 60000, banco: 40000, carteira: 0, registro_antigo: 0 });
  expect(lerEvento(comRecursos({ detalhes: { ...detalhes, origens: { ...origens, cartao: "abc" } } }))).toBeNull();
  expect(lerEvento(comRecursos({ detalhes }))?.tipo).toBe("bloco");
});
it("recurso desconhecido é descartado sem invalidar o bloco (e as chaves herdadas do Object não passam)", () => {
  const e = lerEvento(comRecursos({ contas, desconhecido: { x: 1 }, toString: "x" })) as unknown as Lido;
  expect(Object.keys(e.bloco.abas[0]!.recursos)).toEqual(["contas"]);
});

it("`periodo` com `inicio`/`fim` malformado invalida o bloco (o título do card não pode lançar); válido segue aceito", () => {
  for (const chave of ["inicio", "fim"]) for (const v of ["", "2026-13", "2026-8", "agosto"]) expect(lerEvento(comRecursos({ periodo: { ...periodo, [chave]: v } }))).toBeNull();
  expect(lerEvento(comRecursos({ periodo }))?.tipo).toBe("bloco");
});
it("`maiores` com mais de 5 lançamentos é aceito (se o servidor subir o teto, o evento não some calado)", () => {
  const cats = (periodo.categorias as { maiores: unknown[] }[]).map((c) => ({ ...c, maiores: Array(6).fill(c.maiores[0] ?? (periodo.categorias as { maiores: unknown[] }[])[0]!.maiores[0]) }));
  const e = lerEvento(comRecursos({ periodo: { ...periodo, categorias: cats } })) as unknown as Lido; expect(e.bloco.abas[0]!.recursos.periodo.categorias[0].maiores).toHaveLength(6);
});
const v2 = (metasV2 as unknown as { metas: { varios: unknown } }).metas.varios;
it("`caixinhas` (formato legado {ok, goals}) é aceito; `metas` no formato v2 também; o legado em `metas` invalida o evento", () => {
  const legado = (fixture as Record<string, unknown>)["/goals/1/status"];
  expect(lerEvento({ tipo: "bloco", bloco: { card: "caixinhas", abas: [{ mes: null, recursos: { caixinhas: legado } }] } })?.tipo).toBe("bloco");
  expect(lerEvento({ tipo: "bloco", bloco: { card: "metas", abas: [{ mes: null, recursos: { metas: v2 } }] } })?.tipo).toBe("bloco");
  expect(lerEvento({ tipo: "bloco", bloco: { card: "metas", abas: [{ mes: null, recursos: { metas: legado } }] } })).toBeNull(); // o legado `{ok, goals}` é do `caixinhas`
});
