import { View } from "react-native";
import type { z } from "zod";
import type * as S from "@/api/schemas/painel";
import { Curva, Valor } from "./base";
import { Marca } from "./simuladorResultado";
import { usePrivacidade } from "./privacidade";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";

type Res = z.output<typeof S.simuladorHabitosSchema>;

/** O resultado do corte (sobra por mês e as duas curvas), como o servidor devolveu: nenhuma conta aqui. Dos sliders (`SimuladorHabitos`) e do card do chat. */
export function ResultadoCorte({ res }: { res: Res }) {
  const { oculto } = usePrivacidade();
  return <>
    <Texto variante="legenda" tom="inkMuted">Sobra por mês</Texto>
    <Valor valor={res.economia_mensal} tipo="entrada" destaque />
    {res.estado === "indisponivel" ? null : <>
      <Curva valores={res.com_corte.trajetoria.map((x) => x.saldo)} outra={res.atual.trajetoria.map((x) => x.saldo)} />
      {oculto ? null : <View style={{ flexDirection: "row", gap: 16 }}><Marca nome="Com corte" /><Marca nome="Sem corte" tracejada /></View>}
    </>}
  </>;
}

/** Card do chat quando a pessoa PEDIU um corte ("simula cortar 30% do mercado"): o resultado e uma linha por corte. Sem slider e sem POST: o servidor já calculou. */
export function CardCorte({ r }: { r: Res }) {
  const { ampliado } = useTipografiaPainel();
  return <View style={{ gap: 8 }}>
    <ResultadoCorte res={r} />
    {r.cortes.map((c) => <View key={c.chave} style={{ flexDirection: "row", flexWrap: ampliado ? "wrap" : "nowrap", justifyContent: "space-between", alignItems: "center", columnGap: 8 }}>
      <Texto variante="rotulo" numberOfLines={ampliado ? undefined : 1} style={{ flexShrink: 1 }}>{c.nome}</Texto>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 4, flexShrink: 0 }}>
        <Texto variante="rotulo" tom="inkMuted">{c.valor_pedido === null ? `−${c.percentual}% ·` : "−"}</Texto>
        <Valor valor={c.valor_pedido ?? c.economia_mensal} pequeno />
        <Texto variante="rotulo" tom="inkMuted">/mês</Texto>
      </View>
    </View>)}
  </View>;
}
