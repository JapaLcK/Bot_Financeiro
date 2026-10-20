import { useLocalSearchParams } from "expo-router";
import { Alert, View } from "react-native";
import { SEM_PLANO } from "@/features/painel/simuladorCompra";
import { useNavegarPainel } from "@/features/painel/navegacao";
import { GrupoDeLinhas, ListRow } from "@/ui/componentes/ListRow";
import { Screen } from "@/ui/componentes/Screen";
import { Selo } from "@/ui/componentes/Selo";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

const breve = <Texto variante="legenda" tom="inkMuted">Em breve</Texto>;

/** `pro` vem do botão do cabeçalho ("1" | "0"); ausente = o plano não chegou. O POST do simulador recusa `pro_required` de qualquer forma. */
export default function Ferramentas() {
  const { pro } = useLocalSearchParams<{ pro?: string }>();
  const { navegar } = useNavegarPainel();
  return <Screen sobCabecalho><View style={{ gap: espaco.lg, paddingVertical: espaco.xl }}>
    <GrupoDeLinhas>
      <ListRow icone="ShoppingCart" titulo="Vale a pena comprar?" desativado={pro === undefined} chevron={pro === "1"} trailing={pro === "0" ? <Selo texto="Pro" /> : undefined} onPress={() => { if (pro === "1") navegar("/simulador-compra"); else if (pro === "0") Alert.alert(SEM_PLANO); }} />
      <ListRow icone="Trophy" titulo="Progresso ao milhão" chevron onPress={() => navegar("/progresso-milhao")} />
      <ListRow icone="Robot" titulo="Agentes" chevron onPress={() => navegar("/agentes")} />
    </GrupoDeLinhas>
    <GrupoDeLinhas>
      <ListRow icone="Lightning" titulo="Simulador" trailing={breve} />
      <ListRow icone="House" titulo="Orçamento doméstico" trailing={breve} />
      <ListRow icone="ChartPie" titulo="Orçamentos por categoria" trailing={breve} />
    </GrupoDeLinhas>
  </View></Screen>;
}
