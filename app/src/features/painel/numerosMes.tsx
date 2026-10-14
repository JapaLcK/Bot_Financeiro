import { View } from "react-native";
import { Card } from "@/ui/componentes/Card";
import { Avisos, Linha, RecursoDados, Valor } from "./base";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";

/** Entrou e Saiu lado a lado (coluna com fonte ampliada): o topo de `NumerosMes` e do card `periodo` do chat. */
export function EntrouSaiu({ entrou, saiu }: { entrou: number; saiu: number }) {
  const { ampliado } = useTipografiaPainel();
  return <View style={{ flexDirection: ampliado ? "column" : "row", gap: 20 }}>
    <View style={{ flex: 1, gap: 5 }}><Texto variante="legenda" tom="inkMuted">Entrou</Texto><Valor valor={entrou} tipo="entrada" /></View>
    <View style={{ flex: 1, gap: 5 }}><Texto variante="legenda" tom="inkMuted">Saiu</Texto><Valor valor={saiu} tipo="saida" /></View>
  </View>;
}

/** Entrou, Saiu, Saldo e Guardado líquido do mês selecionado (os mesmos `resumo` e `detalhes` do provider). Topo da aba Gastos; sem toque: o Extrato é uma aba. */
export function NumerosMes() {
  return <Card><View testID="numeros-mes" style={{ gap: 8 }}>
    <Texto variante="rotulo" tom="inkMuted">Entrou e saiu no mês</Texto>
    <RecursoDados nome="resumo">{(d) => <View style={{ gap: 8 }}>
      <EntrouSaiu entrou={d.entrou} saiu={d.saiu} />
      <Linha titulo="Saldo do mês" valor={d.entrou - d.saiu} />
      <RecursoDados nome="detalhes">{(m) => <><Linha titulo="Guardado líquido" valor={m.guardado.liquido} /><Avisos motivos={m.motivos.filter((x) => !d.motivos.includes(x))} /></>}</RecursoDados>
      <Avisos motivos={d.motivos} />
    </View>}</RecursoDados>
  </View></Card>;
}
