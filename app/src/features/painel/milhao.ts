// Cópia de `webapp/src/dashboard/lib/milhao.ts` (o app não importa o web, §0.7); só diferem os `!`/`as` do `noUncheckedIndexedAccess`. `__tests__/features/milhao_paridade.test.ts` roda a mesma grade nos dois.
// A conta do "Progresso ao milhão": juros compostos com taxa fixa e aporte mensal, sem I/O.
// O patrimônio de hoje vem do servidor (GET /api/v2/progresso-milhao); o resto é daqui.
// Teto de 600 meses (50 anos): depois disso, "sem previsão". Mês a mês, dinheiro negativo não rende.
export const MESES_MAX = 600;
const MILHAO = 1e6;

export type Projecao = {
  meta: number; // o próximo múltiplo de R$ 1 mi acima do patrimônio (exatamente 1 mi já é "passou": meta 2 mi)
  faltaCentavos: number;
  pct: number; // 0..100, inteiro para baixo
  meses: number | null; // null = não chega em 50 anos
  chegada: string | null; // "AAAA-MM"
  anos: number;
  mesesResto: number;
  pontos: number[]; // P0 até o mês em que chega (ou até o fim do prazo)
};

export function projetar({ patrimonio, aporte, taxaAnual, inicio }: { patrimonio: number; aporte: number; taxaAnual: number; inicio: string }): Projecao {
  const i = Math.pow(1 + taxaAnual / 100, 1 / 12) - 1;
  const meta = MILHAO * (Math.floor(Math.max(patrimonio, 0) / MILHAO) + 1);
  const pontos = [patrimonio];
  let meses: number | null = null;
  for (let k = 1; k <= MESES_MAX && meses === null; k++) {
    const p = pontos[k - 1]!;
    pontos.push(p + Math.max(p, 0) * i + aporte);
    if (pontos[k]! >= meta) meses = k;
  }
  const faltaCentavos = meta * 100 - Math.round(patrimonio * 100);
  const [a, m] = inicio.split("-").map(Number) as [number, number];
  const total = a * 12 + (m - 1) + (meses ?? 0);
  return {
    meta, faltaCentavos, meses, pontos,
    pct: Math.min(Math.max(Math.floor((patrimonio / meta) * 100), 0), 100),
    chegada: meses === null ? null : `${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`,
    anos: meses === null ? 0 : Math.floor(meses / 12),
    mesesResto: meses === null ? 0 : meses % 12,
  };
}

// "24 anos e 2 meses", "1 ano", "10 meses": a parte zero some.
export function duracao(anos: number, meses: number): string {
  const a = anos ? `${anos} ${anos === 1 ? "ano" : "anos"}` : "";
  const m = meses ? `${meses} ${meses === 1 ? "mês" : "meses"}` : "";
  return a && m ? `${a} e ${m}` : a || m;
}
