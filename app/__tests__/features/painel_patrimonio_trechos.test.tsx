import { render } from "@testing-library/react-native";
import { patrimonioSchema } from "@/api/schemas/painel";
import { BlocoContexto } from "@/features/painel/blocoContexto";
import { Distribuicao, Patrimonio } from "@/features/painel/patrimonio";
import { TemaProvider } from "@/ui/tema";
import v2 from "../../../tests/frontend/api_v2_respostas.json";
// Patrimônio com trechos e variação: o schema tolera servidor velho; a curva não liga trechos separados; a variação vem do servidor; `carteira_nao_confirmada` não aparece.
jest.mock("@/features/painel/provider", () => ({ usePainel: () => ({ oculto: false, dados: {} }) }));
const F = v2.patrimonio as Record<string, any>;
const ler = (o: object) => patrimonioSchema.parse(o);
const tela = (o: object, C: () => React.JSX.Element = Patrimonio) => render(<TemaProvider><BlocoContexto.Provider value={{ recursos: { patrimonio: ler(o) } as never }}><C /></BlocoContexto.Provider></TemaProvider>);
const caminhos = (t: ReturnType<typeof tela>) => [...new Set(t.UNSAFE_root.findAll((n) => typeof n.props?.d === "string" && n.props.d.startsWith("M")).map((n) => n.props.d as string))]; // o mesmo `d` aparece em cada camada do componente
const { trechos: _t, variacao: _v, ...velho } = F.continuo;

describe("schema", () => {
  it("sem trechos/variacao (servidor velho): [] e null, o resto parseia", () => { const r = ler(velho); expect(r.trechos).toEqual([]); expect(r.variacao).toBeNull(); expect(r.total).toBe(172000); });
  it("variação em centavos nas fixtures continuo e com_quebra", () => { expect(ler(F.continuo).variacao?.valor).toBe(2000); expect(ler(F.com_quebra).variacao?.valor).toBe(2000); });
  it("variação fora do contrato vira null sem derrubar", () => { const r = ler({ ...F.continuo, variacao: { valor: "abc", desde: "x", ate: "y" } }); expect(r.variacao).toBeNull(); expect(r.historico).toHaveLength(2); });
});
describe("tela", () => {
  it("contínuo: uma linha só (um M), +R$ 20,00 e desde 12/10", () => {
    const t = tela(F.continuo); expect(caminhos(t).map((d) => d.match(/M/g)!.length)).toEqual([1]);
    expect(t.getByText(/\+.*20,00/)).toBeTruthy(); expect(t.getByText("desde 12/10")).toBeTruthy(); expect(t.getByText("Histórico desde 12/10/2026")).toBeTruthy();
  });
  it("com quebra: 12/10 fica isolado e a linha de 13/10 a hoje não o liga", () => {
    const t = tela(F.com_quebra), [d] = caminhos(t); expect(d).toMatch(/^M[^ ]+ M[^ ]+ L[^ ]+$/); // ponto, M, uma só linha
    expect(t.UNSAFE_root.findAll((n) => n.props?.r === 3)).not.toHaveLength(0); // o ponto isolado de 12/10
  });
  it("foto de hoje no historico (job das 18h): o ponto ao vivo a substitui, sem duplicar o dia", () => {
    const h = F.continuo.historico, t = tela({ ...F.continuo, historico: [...h, { ...h[1], dia: "2026-10-14", total: "99999.00" }] });
    const d = caminhos(t)[0]!; expect(d.match(/M/g)).toHaveLength(1); expect(d.match(/L/g)).toHaveLength(2); expect(caminhos(tela(F.continuo))[0]).toBe(d);
  });
  it("variação negativa mostra o sinal de menos; zero real fica sem '+'", () => {
    const v = (valor: string) => tela({ ...F.continuo, variacao: { ...F.continuo.variacao, valor } });
    const n = v("-5.00"); expect(n.getByText(/−\s*R\$\s*5,00/)).toBeTruthy(); expect(n.queryByText(/\+/)).toBeNull();
    const z = v("0.00"); expect(z.getByText("desde 12/10")).toBeTruthy(); expect(z.getByText(/^R\$\s*0,00$/)).toBeTruthy(); expect(z.queryByText(/\+/)).toBeNull();
  });
  it("trechos inválidos + variação válida: sem variação nem 'desde'", () => { const t = tela({ ...F.continuo, trechos: "x" }); expect(t.queryByText(/^desde /)).toBeNull(); expect(t.queryByText(/\+/)).toBeNull(); });
  it("variacao null (trecho de um ponto): nada de 'desde' nem de variação", () => { const t = tela({ ...F.continuo, variacao: null }); expect(t.queryByText(/^desde /)).toBeNull(); expect(t.queryByText(/\+/)).toBeNull(); });
  it("vazio: sem variação e sem 'desde'", () => { const t = tela(F.vazio); expect(t.queryByText(/desde/)).toBeNull(); expect(t.queryByText(/R\$/)).toBeTruthy(); });
  it("servidor velho: uma linha com os totais do historico, sem variação", () => { const t = tela(velho); expect(caminhos(t).map((d) => d.match(/M/g)!.length)).toEqual([1]); expect(t.queryByText(/^desde /)).toBeNull(); });
  it("carteira_nao_confirmada some dos avisos; moeda_presumida segue", () => {
    for (const C of [Patrimonio, Distribuicao]) {
      expect(tela({ ...F.continuo, motivos: ["carteira_nao_confirmada"] }, C).queryByLabelText("Ver ressalvas")).toBeNull();
      expect(tela({ ...F.continuo, motivos: ["carteira_nao_confirmada", "moeda_presumida"] }, C).getByLabelText("Ver ressalvas")).toBeTruthy();
    }
  });
});
