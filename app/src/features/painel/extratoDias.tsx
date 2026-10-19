import { Children, Fragment, type ReactNode } from "react";
import type { Lancamento } from "@/api/schemas/painel";
import { Card } from "@/ui/componentes/Card";
import { useTema } from "@/ui/tema";
import { View } from "react-native";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";
import { Valor } from "./base";
import { dataPtBr, hojeSP, semana } from "./catalogo";
import { dataFalada } from "./folhaDia";
import { somarCentavos } from "./decimal";

/** ISO civil de verdade (rejeita "2026-13-45"): só então dá para pedir o dia da semana. */
const valido = (dia: string) => /^\d{4}-\d{2}-\d{2}$/.test(dia) && new Date(`${dia}T12:00:00Z`).toISOString().slice(0, 10) === dia;
/** O dia anterior de "AAAA-MM-DD", em UTC sobre a string (nunca o fuso do aparelho). */
const antes = (dia: string) => new Date(Date.parse(`${dia}T12:00:00Z`) - 864e5).toISOString().slice(0, 10);
/** "Hoje · 19/10", "Ontem · 18/10", "Sex · 17/10"; outro ano acrescenta "/aa" (Hoje/Ontem nunca: a virada de ano continua "Ontem · 31/12"). `hoje` entra por parâmetro: função pura. */
export function rotuloDia(dia: string, hoje: string) {
  if (!valido(dia)) return dataPtBr(dia);
  const ddmm = `${dia.slice(8)}/${dia.slice(5, 7)}`;
  if (dia === hoje) return `Hoje · ${ddmm}`;
  if (valido(hoje) && dia === antes(hoje)) return `Ontem · ${ddmm}`;
  const dds = semana(dia).slice(0, 3);
  return `${dds[0]!.toUpperCase()}${dds.slice(1)} · ${ddmm}${dia.slice(0, 4) !== hoje.slice(0, 4) ? `/${dia.slice(2, 4)}` : ""}`;
}
/**
 * Agrupa por dia (Map, ordem da 1ª aparição) e soma o que conta no total: fora `interno` e `moeda !== "BRL"`; saída soma pelo módulo, com sinal negativo.
 * `total` é `null` quando nada conta (nunca "R$ 0,00") e no ÚLTIMO grupo de uma lista incompleta: o servidor ordena por dia desc e só o último dia da página continua na próxima.
 */
export function diasDoExtrato(itens: Lancamento[], completo: boolean) {
  const grupos = new Map<string, Lancamento[]>();
  for (const i of itens) grupos.set(i.data, [...(grupos.get(i.data) ?? []), i]);
  const dias = [...grupos].map(([dia, lista]) => {
    const contam = lista.filter((i) => !i.interno && i.moeda === "BRL");
    return { dia, itens: lista, total: contam.length ? somarCentavos(contam.map((i) => (i.tipo === "entrada" ? 1 : -1) * Math.abs(i.valor))) : null };
  });
  return completo || !dias.length ? dias : dias.map((d, n) => (n === dias.length - 1 ? { ...d, total: null } : d));
}
/** Cabeçalho do dia FORA do card (rótulo à esquerda, total à direita) e as linhas dentro, separadas por hairline. */
export function CartaoDia({ dia, total, children }: { dia: string; total: number | null; children: ReactNode }) {
  const { cores } = useTema(); const { ampliado } = useTipografiaPainel();
  const rotulo = rotuloDia(dia, hojeSP()), falado = valido(dia) ? (rotulo.startsWith("Hoje") ? `Hoje, ${dataFalada(dia)}` : dataFalada(dia)) : rotulo;
  return <View style={{ gap: 8 }}>
    <View style={{ flexDirection: ampliado ? "column" : "row", justifyContent: "space-between", alignItems: ampliado ? "flex-start" : "center", gap: 8 }}>
      <Texto variante="rotulo" tom="inkMuted" accessibilityRole="header" accessibilityLabel={falado}>{rotulo}</Texto>
      {total !== null && <Valor pequeno valor={Math.abs(total)} tipo={total > 0 ? "entrada" : total < 0 ? "saida" : "saldo"} />}
    </View>
    <Card>{Children.toArray(children).map((c, n) => <Fragment key={n}>{n > 0 && <View style={{ height: 1, backgroundColor: cores.border }} />}{c}</Fragment>)}</Card>
  </View>;
}
