import { AGENTES, acessoDe, itensMencao, mencaoDe, tokenEm, type Agentes } from "@/features/painel/agentes";
import { AVATAR } from "@/features/painel/avatares";
// Funções puras do @ (porta de webapp/src/dashboard/lib/agentes.ts): tabela de entrada e saída.
const cat = (over: Partial<Agentes> = {}, status: string | null = "active"): Agentes => ({
  catalog: AGENTES.map((a) => ({ kind: a.kind, nome: a.nome, disponivel: true, status, energy_cost: 2 })),
  can_chat: true, can_activate: true, energy_enabled: true, energy_budget: 10, energy_used: 2, ...over,
});
it.each([
  ["@Xerife quanto gastei?", "xerife", "quanto gastei?"],
  ["oi @Xerife tudo bem", "xerife", "oi tudo bem"],
  ["@reporter resumo", "reporter", "resumo"],
  ["@repórter resumo", "reporter", "resumo"],
  ["@Faria Limer e a carteira?", "faria_limer", "e a carteira?"],
  ["@Banqueiro metas", "cofre", "metas"],
  ["ana@detetive.com @Xerife oi", "xerife", "ana@detetive.com oi"],
  ["@Xerife", "xerife", ""],
  ["@Xerife ?", "xerife", ""],
  ["한국어 @Xerife oi", "xerife", "한국어 oi"], // Hangul vira jamos no NFD: os índices não podem andar
])("mencaoDe(%j)", (texto, kind, resto) => { expect(mencaoDe(texto, cat())).toEqual({ kind, texto: resto }); });
it.each(["x@gmail.com", "@Xerifex oi", "@Fulano oi", "sem arroba"])("mencaoDe(%j) é nula", (t) => { expect(mencaoDe(t, cat())).toBeNull(); });
it("agente indisponível não vira menção quando o catálogo é dado", () => {
  const d = cat(); d.catalog[0]!.disponivel = false;
  expect(mencaoDe("@Xerife oi", d)).toBeNull(); expect(mencaoDe("@Xerife oi")).not.toBeNull();
});
it("tokenEm: só o @ no começo ou depois de espaço, até o cursor", () => {
  expect(tokenEm("@rep", 4)).toEqual({ ini: 0, q: "rep" });
  expect(tokenEm("oi @Repó", 8)).toEqual({ ini: 3, q: "repo" });
  expect(tokenEm("x@gmail", 7)).toBeNull(); expect(tokenEm("@rep oi", 7)).toBeNull(); expect(tokenEm("@rep oi", 4)).toEqual({ ini: 0, q: "rep" });
});
it("acessoDe: upgrade, no_energy, ready, activate e indisponível", () => {
  expect(acessoDe(cat({ can_chat: false }), "xerife")).toBe("upgrade");
  expect(acessoDe(cat({ energy_used: 11 }), "xerife")).toBe("no_energy");
  expect(acessoDe(cat({ energy_used: 9 }, null), "xerife")).toBe("no_energy"); // 9 + 2 > 10 e não está ativo
  expect(acessoDe(cat(), "xerife")).toBe("ready");
  expect(acessoDe(cat({}, null), "xerife")).toBe("activate");
  expect(acessoDe(cat(), "nao_existe")).toBeNull();
});
it("itensMencao: ordem dos grupos e filtro por prefixo sem acento", () => {
  const d = cat(); d.catalog[1]!.status = null; d.catalog[2]!.status = null; // reporter e carteiro: ativar
  expect(itensMencao(d, "").map((i) => i.kind)).toEqual(["xerife", "detetive", "cofre", "barao", "faria_limer", "reporter", "carteiro"]);
  expect(itensMencao(d, "rep").map((i) => i.kind)).toEqual(["reporter"]); expect(itensMencao(d, "xyz")).toEqual([]);
});
it("todo agente do catálogo tem avatar", () => { for (const a of AGENTES) expect(AVATAR[a.kind]).toBeDefined(); });
