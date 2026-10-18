// A conta do "Seu dia" no app: a média do calendário, o dia que se abre, o vizinho e o dia contra a média.
// Reimplementa `webapp/src/dashboard/lib/gastos-dia.ts` (o web é a AUTORIDADE; os projetos npm são separados, então não se importa) emulando a
// aritmética `double` dele EXPRESSÃO A EXPRESSÃO: `Number(bruto)` de cada total, a mesma ordem das operações, `Math.round` em double.
// Nada de centavos primeiro (muda o tom: "1.040"/"1.137") nem decimal exato (erra nos empates do meio: "0.191"/"0.209").
// Paridade com o web: `tests/fixtures/gastos_dia_casos.json` (gerada por `scripts/gerar_gastos_dia_casos.mjs`, lida pelos dois testes).
// Só para DECIDIR; o que se exibe em reais sai em centavos pelo `decimalParaCentavos`. Os tons ("gain"/"warn"/"") são os do web.
type Mes = { mes: string; hoje: string; historico_desde: string | null; dias: { dia: string; total: { bruto: string } }[] };

const pad = (n: number) => String(n).padStart(2, "0");

export function mediaDoPeriodo(d: Mes) {
  const [y, m] = d.mes.split("-").map(Number) as [number, number];
  const fim = `${d.mes}-${pad(new Date(y, m, 0).getDate())}`;
  const desde = d.historico_desde; // antes dele o dia é desconhecido, não zero
  const de = desde && desde > `${d.mes}-01` ? desde : `${d.mes}-01`;
  const ate = d.hoje < fim ? d.hoje : fim;
  const vividos = d.dias.filter((g) => g.dia >= de && g.dia <= ate && Number(g.total.bruto) > 0);
  const nDias = Number(ate.slice(8)) - Number(de.slice(8)) + 1;
  const media = nDias > 0 ? vividos.reduce((a, g) => a + Number(g.total.bruto), 0) / nDias : null;
  return { de, ate, fim, vividos, nDias, media };
}

/** Abre-se o dia dentro do histórico; o futuro só se tiver gasto agendado. Dia passado sem gasto se abre. */
export function diaAbrivel(k: string, d: Mes) {
  const { de, fim } = mediaDoPeriodo(d);
  return k >= de && k <= fim && !(k > d.hoje && !d.dias.some((g) => g.dia === k));
}

/** O vizinho de `dia` (±1) no mesmo mês, ou null se não se abre. */
export function vizinho(dia: string, passo: 1 | -1, d: Mes) {
  const k = `${d.mes}-${pad(Number(dia.slice(8)) + passo)}`;
  return diaAbrivel(k, d) ? k : null;
}

/** O pct/tom do `dia` contra a média do mês `d`: a conversão `Number(bruto)` mora AQUI (nunca centavos/100: muda o tom). QUANDO mostrar a pílula não é daqui: o web só a mostra com `dia < hoje` e `Number(bruto) > 0` (`GastosDiaTopo.tsx:28`), regra de quem chama (PR 3). */
export function contraMediaDoDia(dia: { total: { bruto: string } }, d: Mes) {
  return contraMedia(Number(dia.total.bruto), mediaDoPeriodo(d).media);
}

/** Pct inteiro (0 = "na média") e tom da pílula: ≤ −5 é "gain", ≥ +15 é "warn", no meio "". Sem média utilizável (≤ 0, nula) ou total ilegível, null. */
export function contraMedia(total: number, media: number | null) {
  if (media === null || !(media > 0) || !Number.isFinite(total)) return null;
  const pct = Math.round((total / media - 1) * 100) || 0; // `|| 0` troca o −0
  return { pct, tom: pct <= -5 ? "gain" : pct >= 15 ? "warn" : "" } as const;
}
