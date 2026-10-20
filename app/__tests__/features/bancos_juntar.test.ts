// Bancos conectados (PR 3): a regra pura da tela: juntar por id, logo, "há N min" e o contrato de `/api/app/bancos`.
import type { Conexao } from "@/api/schemas/openFinance";
import { conectoresSchema, conexoesSchema } from "@/api/schemas/openFinance";
import { bancosSchema } from "@/api/schemas/painel";
import { juntar, logoDoBanco, logoSeguro } from "@/features/openFinance/conexoes/juntar";
import { tempoRelativo } from "@/features/openFinance/conexoes/tempoRelativo";
// O contrato LIDO do arquivo que o pytest valida contra o Pydantic (PR 1); nunca copiado (CLAUDE.md §0.7).
import contrato from "../../../tests/fixtures/app_bancos.json";

const conexao = (id: number, nome: string, state = "updated"): Conexao => ({ id, status: "ACTIVE", status_reason: null, last_sync_at: null, reconnected_at: null, provider_item_id: `item_${id}`, institution_name: nome, ui: { state, label: state, detail: null } });

describe("contrato de /api/app/bancos", () => {
  it("o fixture do servidor passa no schema do app: dinheiro em centavos, null continua null", () => {
    const b = bancosSchema.parse(contrato);
    expect(b.total).toBe(6_700_000);
    expect(b.bancos.map((x) => [x.conexao_id, x.total, x.saldo_conta, x.investido])).toEqual([[11, 4_500_000, 500_000, 4_000_000], [12, 2_200_000, 700_000, 1_500_000], [13, null, null, null]]);
  });

  it("tolerante POR ITEM: dinheiro em notação científica ('1E-7'), lixo e null saem; os bancos bons ficam", () => {
    const ruim = { ...contrato.bancos[0]!, conexao_id: 99, total: "1E-7" };
    const b = bancosSchema.parse({ ...contrato, bancos: [ruim, contrato.bancos[0], "lixo", null, { conexao_id: "x" }, contrato.bancos[1]] });
    expect(b.bancos.map((x) => x.conexao_id)).toEqual([11, 12]);
  });

  it("o topo que não passa vira null (a tela escreve '—') e os bancos seguem", () => {
    const b = bancosSchema.parse({ ...contrato, total: "1E-7", motivos: "x" });
    expect(b.total).toBeNull();
    expect(b.motivos).toEqual([]);
    expect(b.bancos).toHaveLength(3);
  });
});

describe("logo_url no snapshot", () => {
  it.each([[5], [{ a: 1 }], [["x"]], [true]])("tipo errado (%j) vira sem logo: a conexão e as outras ficam", (lixo) => {
    const ok = { ...conexao(2, "B"), logo_url: "https://cdn.pluggy.ai/a.svg" };
    const r = conexoesSchema.parse({ connections: [{ ...conexao(1, "A"), logo_url: lixo }, ok] });
    expect(r.connections.map((c) => [c.id, c.logo_url])).toEqual([[1, null], [2, "https://cdn.pluggy.ai/a.svg"]]);
  });
  it("ausente (servidor velho) e null passam", () => {
    const r = conexoesSchema.parse({ connections: [conexao(1, "A"), { ...conexao(2, "B"), logo_url: null }] });
    expect(r.connections).toHaveLength(2);
  });
});

describe("logo_proprio no schema: tolerante, uma conexão ruim não derruba a lista", () => {
  it.each([[5], [{ a: 1 }], [["x"]], [true]])("tipo errado (%j) vira null no snapshot e no catálogo; os outros itens ficam", (lixo) => {
    const ok = "/brand/bancos/60746948.png";
    const c = conexoesSchema.parse({ connections: [{ ...conexao(1, "A"), logo_proprio: lixo }, { ...conexao(2, "B"), logo_proprio: ok }, conexao(3, "C")] });
    expect(c.connections.map((x) => [x.id, x.logo_proprio ?? null])).toEqual([[1, null], [2, ok], [3, null]]);
    const k = conectoresSchema.parse({ connectors: [{ id: 1, name: "A", logo: "", logo_proprio: lixo }, { id: 2, name: "B", logo: null, logo_proprio: ok }, { id: 3, name: "C", logo: null }] });
    expect(k.connectors.map((x) => [x.id, x.logo_proprio ?? null])).toEqual([[1, null], [2, ok], [3, null]]);
  });
});

describe("logoDoBanco: próprio > Pluggy > inicial", () => {
  const PROPRIO = "/brand/bancos/60746948.png", PLUGGY = "https://cdn.pluggy.ai/assets/connector-icons/201.svg";
  it("com os dois, vale o próprio, na URL ABSOLUTA do servidor (o aparelho não tem origem relativa)", () => {
    expect(logoDoBanco({ logo_proprio: PROPRIO, logo_url: PLUGGY })).toBe(`http://backend.teste${PROPRIO}`);
  });
  it("sem próprio, segue o da Pluggy (positivo: o banco fora dos 10 principais não perde o logo)", () => {
    expect(logoDoBanco({ logo_proprio: null, logo_url: PLUGGY })).toBe(PLUGGY);
    expect(logoDoBanco({ logo_url: PLUGGY })).toBe(PLUGGY);
  });
  it("sem nenhum dos dois, inicial (undefined)", () => {
    expect(logoDoBanco({})).toBeUndefined();
    expect(logoDoBanco({ logo_proprio: null, logo_url: "" })).toBeUndefined();
  });
  it.each([
    "//evil.example/x.png", "/brand/bancos/../x.png", "https://evil.example/brand/bancos/60746948.png", "/brand/bancos/60746948.png?x=1", "/brand/bancos/6074694.png",
    "/brand/bancos/60746948.svg", "/brand/bancos/60746948.png\n", "brand/bancos/60746948.png", "",
  ])("próprio fora da regra (%j) cai no logo da Pluggy, nunca é concatenado ao host", (ruim) => {
    expect(logoDoBanco({ logo_proprio: ruim, logo_url: PLUGGY })).toBe(PLUGGY);
    expect(logoDoBanco({ logo_proprio: ruim, logo_url: null })).toBeUndefined();
  });
});

describe("juntar", () => {
  it("junta por id da conexão, nunca por nome: dois 'Nubank' são dois bancos com valores próprios", () => {
    const b = bancosSchema.parse({ total: "0", motivos: [], bancos: [{ ...contrato.bancos[0]!, conexao_id: 2 }, { ...contrato.bancos[1]!, conexao_id: 1 }] });
    const itens = juntar([conexao(1, "Nubank"), conexao(2, "Nubank")], b.bancos);
    expect(itens.map((i) => i.banco?.total)).toEqual([2_200_000, 4_500_000]);
  });

  it("conexão que não veio em /bancos fica sem banco ('—'), e a ordem é a do snapshot", () => {
    const b = bancosSchema.parse(contrato);
    const itens = juntar([conexao(13, "C"), conexao(7, "Recém conectado"), conexao(11, "A")], b.bancos);
    expect(itens.map((i) => i.banco?.conexao_id)).toEqual([13, undefined, 11]);
  });
});

describe("logoSeguro: o aparelho busca a URL, então o host é fixado", () => {
  it("aceita só https no CDN de ícones da Pluggy", () => {
    expect(logoSeguro("https://cdn.pluggy.ai/assets/connector-icons/201.svg")).toBe("https://cdn.pluggy.ai/assets/connector-icons/201.svg");
  });
  it.each([
    "http://cdn.pluggy.ai/assets/connector-icons/201.svg", "https://cdn.pluggy.ai.evil.com/x.svg", "https://evil.com/cdn.pluggy.ai/x.svg", "https://cdn.pluggy.ai@evil.com/x.svg",
    "https://user:pw@cdn.pluggy.ai/x.svg", "https://cdn.pluggy.ai:8443/x.svg", "https://cdn.pluggy.ai/x.svg?a=1", "https://cdn.pluggy.ai/a b.svg", "https:///cdn.pluggy.ai/x.svg",
    "https://CDN.pluggy.ai/x.svg", "https://cdn.pluggy.ai/", "javascript:alert(1)", "data:image/svg+xml;base64,AAAA", "", null, undefined,
  ])("recusa %s: sem logo, fica a inicial", (url) => {
    expect(logoSeguro(url)).toBeUndefined();
  });
});

describe("tempoRelativo (relógio congelado)", () => {
  const AGORA = Date.parse("2026-10-18T12:00:00Z");
  const antes = (ms: number) => new Date(AGORA - ms).toISOString();
  it.each([
    [0, "agora"], [59_000, "agora"], [60_000, "há 1 min"], [12 * 60_000, "há 12 min"], [59 * 60_000, "há 59 min"], [60 * 60_000, "há 1 h"], [23 * 3_600_000, "há 23 h"],
    [24 * 3_600_000, "há 1 dia"], [3 * 86_400_000, "há 3 dias"], [45 * 86_400_000, "há 45 dias"], [-5 * 60_000, "agora"],
  ])("%i ms atrás: %s", (ms, esperado) => {
    expect(tempoRelativo(antes(ms), AGORA)).toBe(esperado);
  });
});
