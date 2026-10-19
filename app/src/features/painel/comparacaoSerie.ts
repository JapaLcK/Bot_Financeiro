import type { RecursosBloco } from "@/api/schemas/painel";
import { dataPtBr, diaMes, mesAbrev, mesCurto } from "./catalogo";

type Comparacao = NonNullable<RecursosBloco["comparacao"]>;

const mesInteiro = (j: Comparacao["atual"]) => j.inicio.slice(0, 7) === j.fim.slice(0, 7) && j.inicio.endsWith("-01") && Number(j.fim.slice(8)) === new Date(Date.UTC(Number(j.fim.slice(0, 4)), Number(j.fim.slice(5, 7)), 0)).getUTCDate();

/** Os nomes dos dois períodos, sempre DISTINTOS (a legenda do gráfico usa o rótulo como chave): o mês curto ("out", "set") para mês inteiro, "dd/mm a dd/mm" para período livre; se ficarem iguais, "out/2025" e "dd/mm/aaaa". */
export function rotulosDaComparacao(d: Comparacao) {
  const nome = (j: Comparacao["atual"], curto: (m: string) => string, ano: boolean) => d.modo !== "periodos_informados" || mesInteiro(j) ? curto(j.inicio.slice(0, 7)) : ano ? `${dataPtBr(j.inicio)} a ${dataPtBr(j.fim)}` : `${diaMes(j.inicio, j.fim)} a ${diaMes(j.fim, j.fim)}`;
  for (const [curto, ano] of [[mesAbrev, false], [mesCurto, false], [mesCurto, true]] as const) {
    const atual = nome(d.atual, curto, ano), anterior = nome(d.anterior, curto, ano);
    if (atual !== anterior) return { atual, anterior };
  }
  return { atual: "período atual", anterior: "período anterior" }; // janelas idênticas: o servidor não emite, mas a chave da legenda nunca repete
}

/**
 * As duas linhas do `CurvaAcumulada`, em centavos, no mesmo eixo X (o maior dos dois períodos). `serie` ausente, vazia ou com ponto negativo
 * (o eixo Y começa em 0): `null`, o card sai sem gráfico e nunca com linha inventada. `indiceHoje` marca o dia de hoje só no `mes_aberto` com
 * `dias_corridos` válido (0 < n <= pontos): aí a atual é SEMPRE o mês corrente e a marca é `dias_corridos - 1`, mesmo com a série esticada por um lançamento
 * de data futura (o scrub para nela). Em qualquer outro caso (`mes_fechado`, `periodos_informados`, `dias_corridos` ausente/inválido) é `null`: o servidor não
 * diz qual é hoje, e adivinhar pelo tamanho da série marcaria o dia do último lançamento futuro; sem marca, o scrub cobre a série inteira.
 */
export function curvasDaComparacao(d: Comparacao) {
  const s = d.serie, dias = Math.max(d.atual.dias, d.anterior.dias);
  if (!s || dias < 2 || !s.atual.length || !s.anterior.length || [...s.atual, ...s.anterior].some((v) => v < 0)) return null;
  const hoje = d.dias_corridos ?? 0, pontos = s.atual.slice(0, d.atual.dias), anterior = s.anterior.slice(0, d.anterior.dias);
  return { pontos, anterior, diasNoMes: dias, indiceHoje: d.modo === "mes_aberto" && hoje > 0 && hoje <= pontos.length ? hoje - 1 : null };
}
