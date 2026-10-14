import { useState, type ReactNode } from "react";
import { ActivityIndicator, Pressable, View } from "react-native";
import Animated from "react-native-reanimated";
import { Icone } from "@/ui/componentes/Icone";
import { useEntrada } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { TextoPainel as Texto } from "./tipografia";
import { Acao, Vazio } from "./base";
import { LinhaLancamento, useLancamentos } from "./extrato";
/**
 * Linha de card do chat que, ao toque, abre os detalhes DENTRO do card (o chat nunca navega nem responde só texto). O botão é a `linha` toda (+ seta);
 * o conteúdo abre logo abaixo. `CaretUp` não está no subset de ícones: a mesma seta gira 180°. Quem usa guarda UMA linha aberta por card.
 */
export function LinhaExpansivel({ linha, aberta, alternar, children, testID }: { linha: ReactNode; aberta: boolean; alternar: () => void; children: ReactNode; testID?: string }) {
  const entrada = useEntrada(); const { cores } = useTema();
  return <View>
    <Pressable testID={testID} accessibilityRole="button" accessibilityState={{ expanded: aberta }} onPress={alternar} style={{ minHeight: 44, flexDirection: "row", alignItems: "center", gap: 12 }}>
      <View style={{ flex: 1, minWidth: 0 }}>{linha}</View>
      <View style={{ transform: [{ rotate: aberta ? "180deg" : "0deg" }] }}><Icone nome="CaretDown" tamanho={20} /></View>
    </Pressable>
    {aberta && <Animated.View testID="linha-expandida" entering={entrada} style={{ marginLeft: 4, paddingLeft: 12, paddingBottom: 8, borderLeftWidth: 2, borderLeftColor: cores.border }}>{children}</Animated.View>}
  </View>;
}
/** Lançamentos da `consulta` (paginados, só leitura), uma linha aberta por vez. Monta ao expandir e desmonta ao recolher: o cleanup do `useLancamentos` aborta a busca. */
export function LancamentosDe({ consulta }: { consulta: string }) {
  const l = useLancamentos(consulta); const [aberto, setAberto] = useState<string | null>(null);
  return <View style={{ gap: 4 }}>
    {l.itens.map((i) => <LinhaLancamento key={i.id} item={i} comParcela aberto={aberto === i.id} alternar={() => setAberto(aberto === i.id ? null : i.id)} />)}
    {l.carregando && <ActivityIndicator accessibilityLabel="Carregando lançamentos" />}
    {l.erro && <><Texto tom="warning">{l.erro}</Texto><Acao rotulo="Tentar de novo" onPress={l.carregarMais} /></>}
    {!l.carregando && !l.erro && !l.itens.length && <Vazio texto="Nenhum lançamento encontrado." />}
    {l.proximo && <Acao rotulo="Carregar mais lançamentos" desativado={l.carregando} onPress={l.carregarMais} />}
  </View>;
}
