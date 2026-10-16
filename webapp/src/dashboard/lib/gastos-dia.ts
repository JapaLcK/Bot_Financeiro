// A conta do "Dia a dia" que mais de um bloco de /gastos usa: a média do período e quais dias do mês se abrem.
// GastosCalendario (a grade) e GastosCompras (o card do dia, com a pílula e as setas) leem daqui, para os dois
// dizerem a mesma média e o mesmo dia desabilitado.
import type { GastoDia } from "./api-v2.gen";

type Mes = { mes: string; hoje: string; historico_desde: string | null; dias: GastoDia[] };

const pad = (n: number) => String(n).padStart(2, "0");

// Média, "dias sem gasto" e escala só contam de `de` até `ate`: dia agendado e dia fora do histórico não entram, nem como zero.
export function mediaDoPeriodo(d: Mes) {
  const [y, m] = d.mes.split("-").map(Number);
  const fim = `${d.mes}-${pad(new Date(y, m, 0).getDate())}`;
  const desde = d.historico_desde; // antes dele o dia é desconhecido, não zero
  const de = desde && desde > `${d.mes}-01` ? desde : `${d.mes}-01`;
  const ate = d.hoje < fim ? d.hoje : fim;
  const vividos = d.dias.filter((g) => g.dia >= de && g.dia <= ate && Number(g.total) > 0);
  const nDias = Number(ate.slice(8)) - Number(de.slice(8)) + 1;
  const media = nDias > 0 ? vividos.reduce((a, g) => a + Number(g.total), 0) / nDias : null;
  return { de, ate, fim, vividos, nDias, media };
}

// Abre-se o dia dentro do histórico; o futuro só se tiver gasto agendado. Dia passado sem gasto se abre.
export function diaAbrivel(k: string, d: Mes) {
  const { de, fim } = mediaDoPeriodo(d);
  return k >= de && k <= fim && !(k > d.hoje && !d.dias.some((g) => g.dia === k));
}

// O vizinho de `dia` (±1) no mesmo mês, ou null se não se abre.
export function vizinho(dia: string, passo: 1 | -1, d: Mes) {
  const k = `${d.mes}-${pad(Number(dia.slice(8)) + passo)}`;
  return diaAbrivel(k, d) ? k : null;
}

// O dia contra a média: pct inteiro (0 = "na média") e o tom da pílula. Abaixo de −5% é bom (gain), de +15% para cima é
// atenção (warn); no meio fica neutro. Sem média utilizável (≤ 0, nula) ou total ilegível, não há comparação.
export function contraMedia(total: number, media: number | null) {
  if (media === null || !(media > 0) || !Number.isFinite(total)) return null;
  const pct = Math.round((total / media - 1) * 100) || 0; // `|| 0` troca o −0
  return { pct, tom: pct <= -5 ? "gain" : pct >= 15 ? "warn" : "" } as const;
}
