import { View } from "react-native";
import Svg, { Path } from "react-native-svg";
import { Card } from "@/ui/componentes/Card";
import { falado, VALOR_INDISPONIVEL_FALADO } from "@/ui/dinheiro";
import { useTema } from "@/ui/tema";
import { entrouSaiuAcumulados } from "./acumuladoMes";
import { Avisos, RecursoDados, Valor } from "./base";
import { hojeSP } from "./catalogo";
import { usePainel, type Dados } from "./provider";
import { TextoPainel as Texto, useTipografiaPainel } from "./tipografia";

/** Entrou e Saiu lado a lado (coluna com fonte ampliada): o card `periodo` do chat e a legenda do gráfico de `NumerosMes` (`amostras`: o traço da cor da linha antes do nome). */
export function EntrouSaiu({ entrou, saiu, amostras }: { entrou: number; saiu: number; amostras?: { entrou: string; saiu: string } }) {
  const { ampliado } = useTipografiaPainel();
  const nome = (rotulo: string, cor?: string) => <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>{cor && <View testID={`amostra-${rotulo}`} style={{ width: 16, height: 3, borderRadius: 2, backgroundColor: cor }} />}<Texto variante="legenda" tom="inkMuted">{rotulo}</Texto></View>;
  return <View style={{ flexDirection: ampliado ? "column" : "row", gap: 20 }}>
    <View style={{ flex: 1, gap: 5 }}>{nome("Entrou", amostras?.entrou)}<Valor valor={entrou} tipo="entrada" /></View>
    <View style={{ flex: 1, gap: 5 }}>{nome("Saiu", amostras?.saiu)}<Valor valor={saiu} tipo="saida" /></View>
  </View>;
}

const LARGURA = 300, ALTURA = 100, FOLGA = 6; // viewBox: o `Svg` de 100% de largura escala, como a `Curva`
/** Duas linhas acumuladas dia a dia (dia 1 ao último do mês, no mesmo eixo): sem grade, eixo nem ponto; o nome e o total de cada uma estão na legenda. Valores ocultos: nem forma, nem número. */
function GraficoMes({ d, m }: { d: Dados["resumo"]; m: Dados["detalhes"] }) {
  const { oculto } = usePainel(); const { cores } = useTema();
  if (oculto) return <View style={{ height: 100, justifyContent: "center" }}><Texto tom="inkMuted">Gráfico oculto</Texto></View>;
  const s = entrouSaiuAcumulados(d, m, hojeSP());
  if (!s) return null;
  const todos = [...s.entrou, ...s.saiu], min = Math.min(0, ...todos), amplitude = Math.max(...todos) - min || 1;
  const caminho = (serie: number[]) => serie.map((v, i) => `${i ? "L" : "M"}${(i * LARGURA / (s.diasNoMes - 1)).toFixed(1)},${(ALTURA - FOLGA - (v - min) / amplitude * (ALTURA - 2 * FOLGA)).toFixed(1)}`).join(" ");
  const rotulo = `Entrou ${falado(s.entrou.at(-1)!) ?? VALOR_INDISPONIVEL_FALADO}, saiu ${falado(s.saiu.at(-1)!) ?? VALOR_INDISPONIVEL_FALADO}, acumulado no mês`;
  return <Svg testID="grafico-entrou-saiu" width="100%" height={112} viewBox={`0 0 ${LARGURA} ${ALTURA}`} accessible accessibilityLabel={rotulo}>
    <Path testID="linha-entrou" d={caminho(s.entrou)} fill="none" stroke={cores.positive} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />
    <Path testID="linha-saiu" d={caminho(s.saiu)} fill="none" stroke={cores.alert} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />
  </Svg>;
}

/** Entrou e Saiu do mês selecionado (os mesmos `resumo` e `detalhes` do provider) como duas linhas acumuladas, nomeadas na legenda com o total de cada uma. Topo da aba Gastos; sem toque: o Extrato é uma aba. */
export function NumerosMes() {
  const { cores } = useTema();
  return <Card><View testID="numeros-mes" style={{ gap: 8 }}>
    <Texto variante="rotulo" tom="inkMuted">Entrou e saiu no mês</Texto>
    <RecursoDados nome="resumo">{(d) => <View style={{ gap: 8 }}>
      <EntrouSaiu entrou={d.entrou} saiu={d.saiu} amostras={{ entrou: cores.positive, saiu: cores.alert }} />
      <RecursoDados nome="detalhes">{(m) => <><GraficoMes d={d} m={m} /><Avisos motivos={m.motivos.filter((x) => !d.motivos.includes(x))} /></>}</RecursoDados>
      <Avisos motivos={d.motivos} />
    </View>}</RecursoDados>
  </View></Card>;
}
