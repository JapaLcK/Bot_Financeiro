import { contraste } from "@/ui/contraste";
import { claro, coresBanco, escuro } from "@/ui/tokens";
import { corDoBanco, segmentosDeContas } from "@/features/painel/composicaoContas";

type Bloco = Parameters<typeof segmentosDeContas>[0];
const conta = (o: Record<string, unknown>) => ({ id: 1, instituicao: "Itaú", nome: "CC", saldo: 100, moeda: "BRL", no_total: true, conexao: "conectado", sincronizado_em: "", motivos: [], ...o });
// `total` padrão = o que o backend soma (Carteira + contas BRL no total com saldo); `o` sobrescreve.
const bloco = (contas: Record<string, unknown>[], o: Record<string, unknown> = {}) => {
  const carteira = (o.carteira as { saldo: number } | undefined) ?? { saldo: 0, motivos: [] };
  const total = carteira.saldo + contas.reduce((s, c) => s + (c.moeda === "BRL" && c.no_total ? (c.saldo as number | null) ?? 0 : 0), 0);
  return { total, motivos: [], fora_do_total: 0, carteira, contas, ...o } as unknown as Bloco;
};
const segs = (contas: Record<string, unknown>[], o: Record<string, unknown> = {}, paleta = escuro) => segmentosDeContas(bloco(contas, o), paleta);

// Matiz 0-360 do hex, só para provar que o mapa não tem verde nem rosa.
function matiz(hex: string): number {
  const n = Number.parseInt(hex.slice(1), 16), [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((x) => x / 255);
  const max = Math.max(r!, g!, b!), d = max - Math.min(r!, g!, b!);
  if (!d) return 0;
  const h = max === r ? ((g! - b!) / d) % 6 : max === g ? (b! - r!) / d + 2 : (r! - g!) / d + 4;
  return (h * 60 + 360) % 360;
}

describe("segmentosDeContas", () => {
  it("proporção: o valor do segmento é o saldo em centavos, na ordem das contas", () => {
    expect(segs([conta({ saldo: 625025 }), conta({ id: 2, instituicao: "Nubank", saldo: 270000 })]).map((s) => s.valor)).toEqual([625025, 270000]);
  });
  it.each([
    ["fora do total", { no_total: false }], ["sem saldo (null)", { saldo: null }], ["outra moeda", { moeda: "USD" }], ["saldo zero", { saldo: 0 }],
  ])("conta %s não entra na barra e, como também não entra no total, a soma continua fechando", (_n, o) => {
    const base = [conta({ saldo: 100 }), conta({ id: 2, saldo: 200 }), conta({ id: 3, ...o })];
    expect(segs(base)).toHaveLength(2);
    expect(segs([...base, conta({ id: 4, saldo: 300 })])).toHaveLength(3); // controle: as válidas continuam
  });
  it("menos de 2 segmentos não vira barra", () => {
    expect(segs([conta({ saldo: 100 })])).toEqual([]);
    expect(segs([conta({ saldo: 100 }), conta({ id: 2, saldo: null })])).toEqual([]);
    expect(segs([conta({ saldo: 100 }), conta({ id: 2, saldo: 5 })])).toHaveLength(2);
  });
  it("instituicao null ou vazia não quebra e cai na escala de tinta (o schema aceita null)", () => {
    for (const instituicao of [null, ""]) {
      const r = segs([conta({ instituicao, saldo: 100 }), conta({ id: 2, instituicao, saldo: 200 })]);
      expect(r.map((s) => s.cor)).toEqual([escuro.inkMuted, escuro.inkFaint]);
    }
  });

  describe("fecha com o total (centavos inteiros)", () => {
    const duas = [conta({ saldo: 300000 }), conta({ id: 2, instituicao: "Nubank", saldo: 300000 })];
    it("Carteira positiva vira o 1º segmento (tinta) e a soma fecha com o total", () => {
      const r = segs(duas, { carteira: { saldo: 50000, motivos: [] } });
      expect(r.map((s) => s.valor)).toEqual([50000, 300000, 300000]);
      expect(r[0]!.cor).toBe(escuro.ink);
      expect(r.reduce((s, x) => s + x.valor, 0)).toBe(650000);
    });
    it("Carteira zero ou negativa não vira segmento", () => {
      expect(segs(duas, { carteira: { saldo: 0, motivos: [] } })).toHaveLength(2);
    });
    it("o total declarado manda: soma diferente (em 1 centavo que seja) não desenha", () => {
      expect(segs(duas, { total: 600000 })).toHaveLength(2); // controle
      expect(segs(duas, { total: 600001 })).toEqual([]);
      expect(segs(duas, { total: 599999 })).toEqual([]);
    });
    it("saldo negativo entra no total e fica fora da barra: não desenha", () => {
      expect(segs([...duas, conta({ id: 3, saldo: -100000 })])).toEqual([]);
      expect(segs([...duas, conta({ id: 3, saldo: 0 })])).toHaveLength(2); // controle
    });
    it("Carteira negativa idem", () => {
      expect(segs(duas, { carteira: { saldo: -5000, motivos: [] } })).toEqual([]);
    });
    it("conta em outra moeda DENTRO do total (soma diverge) não desenha", () => {
      const usd = conta({ id: 3, moeda: "USD", saldo: 100000, no_total: true });
      expect(segs([...duas, usd], { total: 700000 })).toEqual([]);
      expect(segs([...duas, usd], { total: 600000 })).toHaveLength(2); // controle: total sem ela fecha
    });
    it("cenário do Tester (total 1000, Carteira -5000, 3000/3000/-1000): não desenha", () => {
      const cs = [conta({ saldo: 300000 }), conta({ id: 2, instituicao: "Nubank", saldo: 300000 }), conta({ id: 3, saldo: -100000 })];
      expect(segs(cs, { total: 100000, carteira: { saldo: -500000, motivos: [] }, motivos: ["banco_desatualizado"] })).toEqual([]);
      expect(segs(cs, { total: 100000, carteira: { saldo: -500000, motivos: [] } })).toEqual([]); // sem o motivo, só a soma já barra
    });
  });

  describe("saldo incerto esconde a barra (lista branca, tabela escrita à mão)", () => {
    const duas = [conta({ saldo: 300000 }), conta({ id: 2, instituicao: "Nubank", saldo: 300000 })]; // soma fecha com o total
    it.each([
      ["banco_desatualizado", false], ["saldo_ausente", false], ["conta_fora_do_ultimo_sync", false], ["moeda_presumida", false],
      ["conexao_pausada", false], ["codigo_novo_do_backend", false],
      ["outra_moeda", true], ["carteira_nao_confirmada", true], ["conciliacao_pendente", true], ["movimentos_pendentes", true], ["especie_incompleta", true],
    ])("%s no bloco: desenha = %s", (m, desenha) => {
      expect(segs(duas, { motivos: [m] })).toHaveLength(desenha ? 2 : 0);
    });
    it("um motivo que esconde basta, mesmo junto de motivos inofensivos", () => {
      expect(segs(duas, { motivos: ["outra_moeda", "saldo_ausente"] })).toEqual([]);
      expect(segs(duas, { motivos: ["outra_moeda", "especie_incompleta"] })).toHaveLength(2);
    });
  });
});

describe("corDoBanco", () => {
  it("banco conhecido usa a cor da marca quando passa 3:1 sobre o card, sem depender de acento ou caixa", () => {
    expect(corDoBanco("NUBANK", 0, claro)).toBe(coresBanco.nubank);
    expect(corDoBanco("Itaú Unibanco", 0, escuro)).toBe(coresBanco.itau);
    expect(corDoBanco("Banco Inter", 0, escuro)).toBe(coresBanco.inter);
    expect(corDoBanco("Mercado Pago", 0, escuro)).toBe(coresBanco["mercado pago"]);
  });
  it("sem contraste 3:1 sobre o card do tema, cai na tinta (a mesma cor passa num tema e reprova no outro)", () => {
    expect(contraste(coresBanco.nubank!, escuro.surface)).toBeLessThan(3);
    expect(corDoBanco("Nubank", 0, escuro)).toBe(escuro.ink);
    expect(contraste(coresBanco.itau!, claro.surface)).toBeLessThan(3);
    expect(corDoBanco("Itaú", 1, claro)).toBe(claro.inkMuted);
  });
  it("banco desconhecido ou verde (PicPay) usa a escala de tinta, ciclando", () => {
    const tinta = [escuro.ink, escuro.inkMuted, escuro.inkFaint];
    expect([0, 1, 2, 3].map((i) => corDoBanco("PicPay", i, escuro))).toEqual([...tinta, tinta[0]]);
    expect(corDoBanco("Banco Qualquer", 2, escuro)).toBe(escuro.inkFaint);
    expect(corDoBanco("", 0, escuro)).toBe(escuro.ink);
  });
  it("BTG (#001E62) cai na tinta no tema escuro e usa a marca no claro", () => {
    expect(contraste(coresBanco.btg!, escuro.surface)).toBeLessThan(3);
    expect(contraste(coresBanco.btg!, claro.surface)).toBeGreaterThanOrEqual(3);
    expect(corDoBanco("BTG Pactual", 0, escuro)).toBe(escuro.ink);
    expect(corDoBanco("BTG Pactual", 0, claro)).toBe(coresBanco.btg);
  });
  it("nome parecido não casa por engano ('xp' dentro de outra palavra)", () => {
    expect(corDoBanco("Expresso Bank", 0, escuro)).toBe(escuro.ink);
  });
  it("o mapa tem no máximo 10 bancos e nenhuma cor verde (ganho) nem rosa (marca)", () => {
    expect(Object.keys(coresBanco).length).toBeLessThanOrEqual(10);
    for (const cor of Object.values(coresBanco)) {
      const h = matiz(cor);
      expect(h >= 75 && h <= 165).toBe(false); // verde
      expect(h >= 300 && h <= 345).toBe(false); // rosa/magenta
    }
  });
});
