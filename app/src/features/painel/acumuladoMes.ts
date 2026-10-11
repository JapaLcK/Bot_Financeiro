import { partes } from "@/ui/dinheiro";
import { nomeMes } from "./catalogo";
import type { Dados } from "./provider";

export interface Acumulado {
  /** Gasto acumulado em centavos; `pontos[i]` é o dia `i + 1`. */
  pontos: number[];
  diasNoMes: number;
  /** Último índice de `pontos` quando o mês é o corrente (marca "hoje"); `null` em mês passado. */
  indiceHoje: number | null;
  /** Total do mês anterior inteiro (centavos), para a linha de referência; `null` sem mês anterior. */
  referencia: number | null;
}

const MES = /^\d{4}-(0[1-9]|1[0-2])$/, DIA = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Curva do "Saiu" acumulado do mês, a partir de `detalhes.dias`. Devolve `null` (o gráfico simplesmente não aparece)
 * sempre que não puder garantir que a curva fecha com o número do card — dinheiro em centavos inteiros:
 *  - `detalhes` de outro mês que o `resumo`, ou `m.saiu`/soma de `dias[].saiu` diferentes de `d.saiu` (outra moeda no
 *    total legado, mês parcial, corrida entre as duas chamadas);
 *  - dia depois do fim do mês, ou depois de HOJE no mês corrente com valor (não desenhamos o futuro);
 *  - menos de 2 pontos (dia 1 do mês corrente, mês futuro, mês sem lançamento).
 *
 * DIAS ANTES DO DIA 1: compras de cartão entram pelo dia da compra mas pertencem à fatura que fecha no mês, então
 * `dias` pode trazer datas do mês anterior e `d.saiu` as inclui. Descartá-las faria a curva não fechar com `saiu`
 * para quem usa cartão; por isso elas NÃO são filtradas: somam na BASE e a curva já começa nesse acumulado no dia 1.
 *
 * `hoje` ("AAAA-MM-DD", fuso de Brasília) vem de fora para o teste ser determinístico. Mês passado: vai até o último
 * dia com lançamento.
 */
export function pontosAcumulados(d: Dados["resumo"], m: Dados["detalhes"], hoje: string): Acumulado | null {
  if (!MES.test(d.mes) || !DIA.test(hoje) || m.mes !== d.mes || m.saiu !== d.saiu) return null;
  const mesHoje = hoje.slice(0, 7);
  if (d.mes > mesHoje) return null;
  const corrente = d.mes === mesHoje, diasNoMes = new Date(Date.UTC(Number(d.mes.slice(0, 4)), Number(d.mes.slice(5, 7)), 0)).getUTCDate();
  const porDia = new Map<number, number>();
  let base = 0, soma = 0, ultimo = corrente ? Number(hoje.slice(8, 10)) : 0;
  for (const x of m.dias) {
    if (!DIA.test(x.dia)) return null;
    soma += x.saiu;
    const mesDoDia = x.dia.slice(0, 7);
    if (mesDoDia < d.mes) { base += x.saiu; continue; }
    const n = Number(x.dia.slice(8, 10));
    if (mesDoDia > d.mes || n < 1 || n > diasNoMes || (corrente && n > ultimo && x.saiu !== 0)) return null;
    porDia.set(n, (porDia.get(n) ?? 0) + x.saiu);
    if (!corrente) ultimo = Math.max(ultimo, n);
  }
  if (!Number.isSafeInteger(soma) || soma !== d.saiu || ultimo < 2) return null;
  let acc = base;
  const pontos = Array.from({ length: ultimo }, (_, i) => (acc += porDia.get(i + 1) ?? 0));
  const ref = d.anterior?.saiu ?? 0;
  return { pontos, diasNoMes, indiceHoje: corrente ? ultimo - 1 : null, referencia: ref > 0 ? ref : null };
}

/** "Setembro fechou em R$ 4.110,00": texto real da linha tracejada (a cor sozinha não diz o que ela é). */
export function legendaReferencia(referencia: number | null, mesAnterior: string): string | null {
  const p = referencia === null ? null : partes(referencia), nome = nomeMes(mesAnterior).split(" ")[0]!;
  return p && nome ? `${nome[0]!.toUpperCase()}${nome.slice(1)} fechou em R$ ${p.inteiro},${p.centavos}` : null;
}
