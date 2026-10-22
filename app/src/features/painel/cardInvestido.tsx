import { View } from "react-native";
import type { z } from "zod";
import type { investidoSchema } from "@/api/schemas/painel";
import { BarraComposicao } from "@/ui/componentes/BarraComposicao";
import { CORES_CATEGORIA } from "@/ui/tokens";
import { Avisos, Linha, Valor, Vazio } from "./base";
import { percentualDe, somarCentavos } from "./decimal";

type Investido = z.output<typeof investidoSchema>;
const RENDA_FIXA = new Set(["renda_fixa", "tesouro"]);
const FALTA_SALDO = "Ainda não sei: seu banco não informou o saldo dos seus investimentos.";
/** A mesma frase do /painel (`fraseSemTotal` em `InvestidoResposta.tsx`) para o total que o servidor não deu. */
const fraseSemTotal = (m: string[]) => m.includes("sem_banco_conectado") ? "Ainda não sei: conecte seu banco para eu ver seus investimentos."
  : m.includes("saldo_ausente") ? FALTA_SALDO : m.includes("nenhum_investimento") ? "Não encontrei investimentos nos seus bancos conectados." : "Ainda não sei: ainda não consegui ler seus investimentos no banco.";
/** Renda fixa + Tesouro em centavos exatos, a mesma soma do `total_renda_fixa` do servidor (`tests/frontend/total_renda_fixa.json`): sem total ou com uma parte sem saldo é `null`, nunca uma soma que parece completa. */
export const totalRendaFixa = (total: number | null, porTipo: { tipo: string; valor: number | null }[]) => {
  const partes = porTipo.filter((p) => RENDA_FIXA.has(p.tipo));
  return total === null || partes.some((p) => p.valor === null) ? null : somarCentavos(partes.map((p) => p.valor!));
};
const rotulo = (n: number) => n === 0 ? "<1%" : `${n}%`; // positivo que arredonda a 0 não vira "0%"

/** O total, a barra por tipo e a lista (`/api/app/investido`). `foco`: só renda fixa + Tesouro. Os avisos da renda fixa são os das partes mostradas e os do banco, que não têm tipo. */
export function CardInvestido({ d, foco = false }: { d: Investido; foco?: boolean }) {
  const partes = foco ? d.por_tipo.filter((p) => RENDA_FIXA.has(p.tipo)) : d.por_tipo, total = foco ? totalRendaFixa(d.total, d.por_tipo) : d.total;
  const soma = somarCentavos(partes.map((p) => Math.max(p.valor ?? 0, 0)));
  const pct = (v: number | null) => v !== null && v > 0 && soma > 0 ? percentualDe(v, soma) : null;
  const cor = (i: number) => CORES_CATEGORIA[i % CORES_CATEGORIA.length]!;
  const barra = partes.flatMap((p, i) => pct(p.valor) === null ? [] : [{ valor: p.valor!, cor: cor(i) }]);
  const avisos = foco ? [...new Set([...partes.flatMap((p) => p.motivos), ...d.motivos.filter((m) => m === "outra_moeda" || m === "conexao_pausada")])] : d.motivos.filter((m) => m !== "sem_banco_conectado");
  return <View style={{ gap: 12 }}>
    {d.total === null ? <Vazio texto={fraseSemTotal(d.motivos)} /> : <Valor valor={total} destaque ausente="legenda" />}
    {d.total !== null && !partes.length && <Vazio texto={foco ? "Nada em renda fixa nos bancos conectados." : fraseSemTotal(["nenhum_investimento"])} />}
    <BarraComposicao segmentos={barra} />
    {partes.map((p, i) => <Linha key={p.tipo} titulo={p.rotulo} valor={p.valor} detalhe={pct(p.valor) === null ? undefined : rotulo(pct(p.valor)!)}
      inicio={<View accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: cor(i) }} />} />)}
    <Avisos motivos={avisos} />
  </View>;
}
