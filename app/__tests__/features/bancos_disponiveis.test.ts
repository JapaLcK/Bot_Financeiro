// "Disponíveis para conectar": a regra pura (quais populares ainda faltam, por ID) e o contrato tolerante de GET /open-finance/{uid}/connectors.
import { conectoresSchema, conexoesSchema, type Conector } from "@/api/schemas/openFinance";
import { VIVO } from "./open_finance_volta_apoio";
import { disponiveis, logoSeguro, POPULARES } from "@/features/openFinance/conexoes/juntar";

const c = (id: number, name: string): Conector => ({ id, name, logo: null });

describe("disponiveis", () => {
  it("a ordem é a dos populares (não a do catálogo), com no máximo 5", () => {
    const catalogo = ["C6 Bank", "Banco Inter", "Santander", "Banco do Brasil", "Bradesco", "Itaú", "Nubank", "Caixa"].map((n, i) => c(i + 1, n));
    expect(disponiveis(catalogo, []).map((x) => x.name)).toEqual(["Itaú", "Bradesco", "Banco do Brasil", "Santander", "Caixa"]);
  });
  it("o que não é popular nunca entra", () => {
    expect(disponiveis([c(1, "Banco Pan"), c(2, "Nubank")], []).map((x) => x.name)).toEqual(["Nubank"]);
  });
  it("já conectado é por ID: o gêmeo (mesmo nome, outro id) continua oferecido", () => {
    expect(disponiveis([c(1, "Nubank"), c(2, "Nubank")], ["1"]).map((x) => x.id)).toEqual([2]);
  });
  it("'Itaú' conectado com institution_id diferente do conector (conexão direta × Open Finance) continua oferecido", () => {
    expect(disponiveis([c(601, "Itaú")], ["201", null, undefined]).map((x) => x.id)).toEqual([601]);
  });
  it("'Itaú' vence 'Itaú Personnalité' nas duas ordens do catálogo, e sem acento ou caixa também", () => {
    expect(disponiveis([c(1, "Itaú Personnalité"), c(2, "Itaú")], []).map((x) => x.id)).toEqual([2]);
    expect(disponiveis([c(2, "ITAÚ"), c(1, "Itaú Personnalité")], []).map((x) => x.id)).toEqual([2]);
  });
  it("tudo conectado: nada a oferecer", () => {
    expect(disponiveis([c(1, "Nubank")], ["1"])).toEqual([]);
  });
  it("a lista de populares é fixa", () => {
    expect(POPULARES).toEqual(["itau", "bradesco", "banco do brasil", "santander", "caixa", "inter", "c6", "nubank", "btg", "mercado pago"]);
  });
});

describe("conectoresSchema: tolerante POR ITEM", () => {
  const lixo = [null, "x", { id: "5", name: "Id string", logo: null }, { id: 1.5, name: "Id float", logo: null }, { id: -3, name: "Id negativo", logo: null }, { id: 0, name: "Id zero", logo: null },
    { id: 9, name: "", logo: null }, { id: 10, name: "   ", logo: null }];
  it("item que não passa sai e os válidos ficam", () => {
    const r = conectoresSchema.parse({ connectors: [...lixo, { id: 201, name: "Nubank", logo: "https://cdn.pluggy.ai/assets/connector-icons/201.svg" }, { id: 202, name: "Inter", logo: null }] });
    expect(r.connectors.map((x) => x.id)).toEqual([201, 202]);
  });
  it.each([[{}], [{ connectors: null }], [{ connectors: "x" }], [{ connectors: {} }]])("`connectors` ausente ou de forma errada (%j) vira []", (corpo) => {
    expect(conectoresSchema.parse(corpo).connectors).toEqual([]);
  });
  it("logo fora do CDN, vazio ou http vira a inicial (sem logo) no render", () => {
    const r = conectoresSchema.parse({ connectors: ["https://evil.example/x.svg", "", "http://cdn.pluggy.ai/x.svg", 5, null].map((logo, i) => ({ id: i + 1, name: "B", logo })) });
    expect(r.connectors.map((x) => logoSeguro(x.logo))).toEqual([undefined, undefined, undefined, undefined, undefined]);
  });
});

describe("conexoesSchema: institution_id de forma ruim vira null e a lista de bancos NÃO some", () => {
  it.each([[201], [true], [{}], [["201"]]])("institution_id %j", (ruim) => {
    const r = conexoesSchema.parse({ connections: [{ ...VIVO, id: 1, institution_id: ruim }, { ...VIVO, id: 2, institution_id: "201" }, { ...VIVO, id: 3 }] });
    expect(r.connections.map((c) => [c.id, c.institution_id])).toEqual([[1, null], [2, "201"], [3, undefined]]);
  });
});
