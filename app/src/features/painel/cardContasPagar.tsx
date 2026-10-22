import { View } from "react-native";
import type { z } from "zod";
import type { contasAPagarSchema } from "@/api/schemas/painel";
import { Linha, Valor, Vazio } from "./base";
import { TextoPainel as Texto } from "./tipografia";

type Contas = z.output<typeof contasAPagarSchema>;
const VISIVEIS = 3;
const plural = (n: number, um: string, varios: string) => `${n} ${n === 1 ? um : varios}`;
const quando = (n: number) => n === 0 ? "hoje" : n === 1 ? "amanhã" : n === -1 ? "ontem" : n > 0 ? `em ${n} dias` : `há ${-n} dias`;
// Conta variável sem estimativa é gravada como 0 (`db/recurring.py`): não é "grátis", é um valor a definir.
const aDefinir = (c: Contas["contas"][number]) => c.variavel && c.valor === 0;

/** As contas pendentes: o total estimado, quantas vencidas e as 3 primeiras (a ordem é a do servidor: a mais atrasada antes). Porta de `CardContas.tsx` do /painel. */
export function CardContasPagar({ d }: { d: Contas }) {
  const resto = d.contas.length - VISIVEIS, semValor = d.contas.filter(aDefinir).length;
  return <View style={{ gap: 12 }}>
    {d.pendentes === 0 ? <Vazio texto="Nenhuma conta a pagar pendente." /> : <View style={{ gap: 4 }}>
      {semValor > 0 && d.total_estimado === 0 ? <Texto variante="secao">Valor a definir</Texto> : <Valor valor={d.total_estimado} destaque />}
      <Texto variante="legenda" tom="inkMuted">{`${d.tem_variavel ? "estimado" : "no total"} · ${plural(d.pendentes, "conta", "contas")}${semValor > 0 && d.total_estimado !== 0 ? ` · fora ${plural(semValor, "conta", "contas")} a definir` : ""}`}</Texto>
    </View>}
    {d.vencidas > 0 && <Texto tom="warning">{plural(d.vencidas, "conta vencida", "contas vencidas")}</Texto>}
    {d.contas.slice(0, VISIVEIS).map((c) => <Linha key={c.id} titulo={c.nome ?? "Conta"} valor={aDefinir(c) ? undefined : c.valor}
      detalhe={[c.dias === null ? null : `${c.vencida ? "vencida" : "vence"} ${quando(c.dias)}`, aDefinir(c) ? "valor a definir" : null].filter(Boolean).join(" · ") || undefined} />)}
    {resto > 0 && <Texto variante="legenda" tom="inkMuted">{`e mais ${plural(resto, "conta", "contas")}`}</Texto>}
  </View>;
}
