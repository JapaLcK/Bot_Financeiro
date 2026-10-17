import { ActivityIndicator, View } from "react-native";
import { CurvaAcumulada } from "@/ui/componentes/CurvaAcumulada";
import { Icone } from "@/ui/componentes/Icone";
import { useTema } from "@/ui/tema";
import { raio, tingir } from "@/ui/tokens";
import { ritmoDoMes, textoPct, variacao } from "./acumuladoMes";
import { Avisos, Linha, RecursoDados, Valor } from "./base";
import { hojeSP } from "./catalogo";
import { useBloco } from "./blocoContexto";
import { usePainel } from "./provider";
import { TextoPainel as Texto } from "./tipografia";

const TOM = { abaixo: "positive", acima: "warning", igual: "ink" } as const;

/**
 * "Ritmo de gastos": quanto este mês gastou a mais ou a menos que o mês passado NO MESMO DIA, a variação em % e as duas curvas
 * acumuladas. Três estados (regra em `ritmoDoMes`): (a) as duas curvas; (b) só a deste mês, sem comparação; (c) nenhuma, só o `d.saiu`.
 * Sem `base` > 0 não há chip de %, mas a frase "vs R$ X mês anterior" fica (é o que dá sentido a "acima").
 */
export function ResumoMes() {
  const { oculto, dados } = usePainel(); const { cores, esquema } = useTema(); const bloco = useBloco();
  return <RecursoDados nome="resumo">{(d) => <RecursoDados nome="detalhes">{(m) => {
    const ant = dados.detalhesAnterior; // no chat o bloco não traz o mês anterior (P2): só a curva do mês, sem "vs mês anterior"
    if (!bloco && (!ant || ant.fase === "carregando")) return <ActivityIndicator accessibilityLabel="Carregando dados" />;
    const r = ritmoDoMes(d, m, !bloco && ant?.fase === "pronto" ? ant.dado : null, hojeSP());
    const v = r && r.base !== null ? variacao(r.gasto, r.base) : null, tom = v ? TOM[v.sentido] : "ink";
    return <View style={{ gap: 12 }}>
      {r ? <>
        <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "flex-end", gap: 8 }}>
          <View style={{ flexShrink: 1 }}><Valor valor={v ? v.diferenca : r.gasto} destaque /></View>
          <Texto variante="corpo" tom={tom} style={{ paddingBottom: 6 }}>{v ? v.sentido : r.atual.indiceHoje === null ? "gasto no mês" : "gasto até hoje"}</Texto>
        </View>
        {v && <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
          {v.pct !== null && <View testID="chip-pct" style={{ flexDirection: "row", alignItems: "center", gap: 4, paddingHorizontal: 8, paddingVertical: 4, borderRadius: raio.chip, backgroundColor: tingir(cores[tom], esquema) }}>
            {v.sentido !== "igual" && <Icone nome={v.sentido === "abaixo" ? "ArrowDown" : "ArrowUp"} tamanho={20} tom={tom} />}
            <Texto variante="rotulo" tom={tom}>{textoPct(v.pct)}</Texto>
          </View>}
          <Texto variante="rotulo" tom="inkMuted">vs</Texto><Valor valor={r.base} pequeno /><Texto variante="rotulo" tom="inkMuted">mês anterior</Texto>
        </View>}
        {oculto ? <Texto tom="inkMuted">Gráfico oculto</Texto> : <>
          <CurvaAcumulada pontos={r.atual.pontos} anterior={r.anterior?.pontos ?? null} diasNoMes={r.atual.diasNoMes} indiceHoje={r.atual.indiceHoje} tom={tom} />
          <Texto variante="legenda" tom="inkMuted" style={{ textAlign: "center" }}>Compras pela data em que foram feitas</Texto>
        </>}
      </> : <Linha titulo="Saiu no mês" valor={d.saiu} tipo="saida" />}
      <Avisos motivos={[...d.motivos, ...m.motivos]} />
    </View>;
  }}</RecursoDados>}</RecursoDados>;
}
