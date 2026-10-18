import type { Dados } from "./provider";

export interface Acumulado {
  /** Compras do mês acumuladas em centavos (dias anteriores ao dia 1 ficam de fora); `pontos[i]` é o dia `i + 1`. */
  pontos: number[];
  diasNoMes: number;
  /** Último índice de `pontos` quando o mês é o corrente (marca "hoje"); `null` em mês passado. */
  indiceHoje: number | null;
}

const MES = /^\d{4}-(0[1-9]|1[0-2])$/, DIA = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Curva das compras acumuladas do mês, a partir de `detalhes.dias`. Devolve `null` (o gráfico simplesmente não aparece)
 * sempre que os dados não se explicam com o número do card (todos os `dias`, inclusive os anteriores ao mês) — dinheiro em centavos inteiros:
 *  - `detalhes` de outro mês que o `resumo`, ou `m.saiu`/soma de `dias[].saiu` diferentes de `d.saiu` (outra moeda no
 *    total legado, mês parcial, corrida entre as duas chamadas);
 *  - dia depois do fim do mês, ou depois de HOJE no mês corrente com valor (não desenhamos o futuro);
 *  - algum ponto acumulado negativo (dia com `saiu` negativo): o eixo começa em 0 e a curva sairia do desenho;
 *  - menos de 2 pontos (dia 1 do mês corrente, mês futuro, mês sem lançamento);
 *  - `m.motivos` com "inicio_do_historico": o plano corta o histórico DENTRO do mês, o detalhe soma só a partir do
 *    corte e o resumo parcial ainda fecha com ele — a curva preencheria com zero os dias sem dado e afirmaria, falsamente,
 *    que não houve gasto ali. Esconder é o lado honesto. (O mesmo motivo só em `d.motivos` = corte no mês ANTERIOR: o mês
 *    está completo e a curva aparece.)
 *
 * DIAS ANTES DO DIA 1: compras de cartão entram pelo dia da compra mas pertencem à fatura que fecha no mês, então
 * `dias` pode trazer datas do mês anterior e `d.saiu` as inclui. Esses dias só entram na checagem de coerência (`soma`);
 * a curva NÃO os desenha: ela mostra as compras feitas dentro do mês, começa em 0 e por isso, quando há fatura de cartão
 * do mês anterior, pode terminar abaixo do `d.saiu` (decisão do dono).
 *
 * `hoje` ("AAAA-MM-DD", fuso de Brasília) vem de fora para o teste ser determinístico. Mês passado: vai até o último
 * dia com lançamento.
 */
export function pontosAcumulados(d: Dados["resumo"], m: Dados["detalhes"], hoje: string): Acumulado | null {
  if (!MES.test(d.mes) || !DIA.test(hoje) || m.mes !== d.mes || m.saiu !== d.saiu || m.motivos.includes("inicio_do_historico")) return null;
  const mesHoje = hoje.slice(0, 7);
  if (d.mes > mesHoje) return null;
  const corrente = d.mes === mesHoje, diasNoMes = new Date(Date.UTC(Number(d.mes.slice(0, 4)), Number(d.mes.slice(5, 7)), 0)).getUTCDate();
  const porDia = new Map<number, number>();
  let soma = 0, ultimo = corrente ? Number(hoje.slice(8, 10)) : 0;
  for (const x of m.dias) {
    if (!DIA.test(x.dia)) return null;
    soma += x.saiu;
    const mesDoDia = x.dia.slice(0, 7);
    if (mesDoDia < d.mes) continue;
    const n = Number(x.dia.slice(8, 10));
    if (mesDoDia > d.mes || n < 1 || n > diasNoMes || (corrente && n > ultimo && x.saiu !== 0)) return null;
    porDia.set(n, (porDia.get(n) ?? 0) + x.saiu);
    if (!corrente) ultimo = Math.max(ultimo, n);
  }
  if (!Number.isSafeInteger(soma) || soma !== d.saiu || ultimo < 2) return null;
  let acc = 0;
  const pontos = Array.from({ length: ultimo }, (_, i) => (acc += porDia.get(i + 1) ?? 0));
  if (pontos.some((v) => v < 0)) return null; // o eixo começa em 0; o backend não produz saída negativa (estorno de cartão fica fora)
  return { pontos, diasNoMes, indiceHoje: corrente ? ultimo - 1 : null };
}

export interface Ritmo {
  /** Este mês: vai até hoje no mês corrente; em mês fechado, até o último dia do mês (repete o último valor), como o `anterior`. */
  atual: Acumulado;
  /** Mês passado inteiro (acumulado completo, repetindo o último valor até o fim do mês); `null` sem curva válida do mês passado. */
  anterior: Acumulado | null;
  /** Acumulado deste mês até o dia de comparação (hoje, ou o último dia do mês quando ele já fechou). */
  gasto: number;
  /** Acumulado do mês passado nesse mesmo dia; `null` sem `anterior`. */
  base: number | null;
}

/**
 * Duas curvas do "ritmo de gastos". A deste mês usa `pontosAcumulados` tal e qual; a do mês passado usa a MESMA regra, com o
 * `anterior` do resumo (total do mês passado inteiro) no papel de resumo: `mAnt.mes` tem de ser `d.anterior.mes`, `mAnt.saiu`
 * e a soma dos dias têm de fechar com `d.anterior.saiu`, sem `inicio_do_historico` e com 2+ pontos. Sem `d.anterior`, sem `mAnt`
 * ou incoerente, só não há mês passado (`anterior: null`); sem a curva deste mês, não há ritmo (`null`).
 * Dia de comparação: o de hoje no mês corrente; em mês fechado, o último dia do mês SELECIONADO (o gráfico corta o mês passado aí). Mês passado mais curto cai no seu último dia.
 */
export function ritmoDoMes(d: Dados["resumo"], m: Dados["detalhes"], mAnt: Dados["detalhes"] | null, hoje: string): Ritmo | null {
  const a = pontosAcumulados(d, m, hoje);
  if (!a) return null;
  const ate = (p: number[], n: number) => Array.from({ length: n }, (_, i) => p[Math.min(i, p.length - 1)]!); // repete o último valor até o dia n
  const atual = a.indiceHoje === null ? { ...a, pontos: ate(a.pontos, a.diasNoMes) } : a;
  const ant = d.anterior && mAnt ? pontosAcumulados({ ...d, mes: d.anterior.mes, saiu: d.anterior.saiu, anterior: null }, mAnt, hoje) : null;
  const anterior = ant && { ...ant, pontos: ate(ant.pontos, ant.diasNoMes) };
  const base = anterior && anterior.pontos[Math.min(atual.indiceHoje ?? atual.diasNoMes - 1, anterior.pontos.length - 1)]!;
  return { atual, anterior, gasto: atual.pontos.at(-1)!, base };
}

/** `abaixo`: gastou menos que o mês passado no mesmo dia. `diferenca` em módulo; `pct` (com sinal, em %) só com base > 0. */
export function variacao(gasto: number, base: number) {
  const dif = gasto - base;
  return { sentido: dif < 0 ? "abaixo" : dif > 0 ? "acima" : "igual", diferenca: Math.abs(dif), pct: base > 0 ? (dif / base) * 100 : null } as const;
}

/** "-32,4%" / "+12,5%" / "0,0%": uma casa, vírgula, sinal ASCII (o zero arredondado não leva sinal). */
export function textoPct(pct: number) {
  const r = Math.round(pct * 10) / 10;
  return `${r < 0 ? "-" : r > 0 ? "+" : ""}${Math.abs(r).toFixed(1).replace(".", ",")}%`;
}

export interface EntrouSaiuMes {
  /** Acumulado por dia em centavos; `[i]` é o dia `i + 1`. A última posição é a ponta: bate com o Entrou/Saiu do resumo. */
  entrou: number[];
  saiu: number[];
  diasNoMes: number;
}

/**
 * As duas linhas do topo de Gastos. Acumula TODOS os dias do mês, inclusive os depois de hoje no mês corrente (lançamento agendado, fatura que fecha depois
 * de hoje): o Entrou/Saiu do resumo os inclui, então a ponta final bate com a legenda e o agendado aparece como degrau. Os dias ANTERIORES ao mês
 * (fatura de cartão do mês anterior) entram no dia 1. Mês passado vai até o último dia; o corrente, até o maior entre hoje e o último dia com lançamento
 * (sem rabo reto até o dia 31, que sugeriria "não vai gastar mais").
 * `null` (gráfico vazio, sem erro) quando os números não se explicam: detalhe de outro mês, mês futuro, `inicio_do_historico` dentro do mês (a linha afirmaria
 * zero onde não há dado), `m.entrou`/`m.saiu` ou a ponta calculada diferentes do `resumo` (mais de 1 centavo; outra moeda no total legado, corrida entre as duas chamadas),
 * menos de 2 dias ou tudo zero.
 */
export function entrouSaiuAcumulados(d: Dados["resumo"], m: Dados["detalhes"], hoje: string): EntrouSaiuMes | null {
  if (!MES.test(d.mes) || !DIA.test(hoje) || m.mes !== d.mes || m.motivos.includes("inicio_do_historico") || d.mes > hoje.slice(0, 7)) return null;
  const perto = (a: number, b: number) => Math.abs(a - b) <= 1, diasNoMes = new Date(Date.UTC(Number(d.mes.slice(0, 4)), Number(d.mes.slice(5, 7)), 0)).getUTCDate();
  if (!perto(m.entrou, d.entrou) || !perto(m.saiu, d.saiu)) return null;
  const entrou = new Array<number>(diasNoMes).fill(0), saiu = new Array<number>(diasNoMes).fill(0);
  let ultimo = d.mes === hoje.slice(0, 7) ? Number(hoje.slice(8, 10)) : diasNoMes;
  for (const x of m.dias) {
    if (!DIA.test(x.dia)) return null;
    const mesDoDia = x.dia.slice(0, 7), n = mesDoDia < d.mes ? 1 : Number(x.dia.slice(8, 10));
    if (mesDoDia > d.mes || n < 1 || n > diasNoMes) continue;
    entrou[n - 1]! += x.entrou; saiu[n - 1]! += x.saiu;
    if ((x.entrou !== 0 || x.saiu !== 0) && n > ultimo) ultimo = n;
  }
  for (let i = 1; i < diasNoMes; i++) { entrou[i]! += entrou[i - 1]!; saiu[i]! += saiu[i - 1]!; }
  if (ultimo < 2 || !perto(entrou[diasNoMes - 1]!, d.entrou) || !perto(saiu[diasNoMes - 1]!, d.saiu)) return null;
  const e = entrou.slice(0, ultimo), s = saiu.slice(0, ultimo);
  return e.some((v) => v !== 0) || s.some((v) => v !== 0) ? { entrou: e, saiu: s, diasNoMes } : null;
}
