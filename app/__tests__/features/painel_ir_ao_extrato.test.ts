import { readdirSync, readFileSync, statSync } from "fs";
import { join } from "path";
import { irAoExtrato } from "@/features/painel/navegacao";

const raiz = join(__dirname, "../../src");
const fontes = (dir: string): string[] => readdirSync(dir).flatMap((n) => (statSync(join(dir, n)).isDirectory() ? fontes(join(dir, n)) : /\.tsx?$/.test(n) ? [join(dir, n)] : []));

it("dois toques seguidos no mesmo dia levam `entrada` diferente (o Extrato só zera os filtros se ela mudar)", () => {
 const navegar = jest.fn(); irAoExtrato(navegar, { dia: "2026-10-02" }); irAoExtrato(navegar, { dia: "2026-10-02" });
 const [a, b] = navegar.mock.calls.map(([d]) => d.params);
 expect(a).toMatchObject({ dia: "2026-10-02" }); expect(b.dia).toBe(a.dia); expect(b.entrada).toBeGreaterThan(a.entrada);
 expect(navegar.mock.calls[0]![0].pathname).toBe("/extrato");
});
it("categoria também leva `entrada`", () => {
 const navegar = jest.fn(); irAoExtrato(navegar, { categoria: "" });
 expect(navegar.mock.calls[0]![0]).toEqual({ pathname: "/extrato", params: { categoria: "", entrada: expect.any(Number) } });
});
// Guarda contra a reintrodução: navegar a `/extrato` com `dia`/`categoria` direto, sem o helper, deixaria filtros de uma visita anterior combinando com o pedido.
it("nenhum arquivo de src navega a `/extrato` fora de `irAoExtrato`", () => {
 const fora = fontes(raiz).filter((f) => !f.endsWith("features/painel/navegacao.ts")).filter((f) => /pathname:\s*["']\/extrato["']|\/extrato\?/.test(readFileSync(f, "utf8")));
 expect(fora).toEqual([]);
});
