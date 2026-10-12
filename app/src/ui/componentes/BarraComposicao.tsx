import { View } from "react-native";

export interface Segmento { valor: number; cor: string }

/**
 * Barra empilhada de 10pt, raio 5, vão de 2pt e segmento mínimo de 4pt (identidade do /painel, "Composição").
 * Largura proporcional ao `valor` (> 0). Decorativa: some do VoiceOver, o valor já está na lista ao lado.
 */
export function BarraComposicao({ segmentos }: { segmentos: Segmento[] }) {
  if (!segmentos.length) return null;
  return (
    <View testID="barra-composicao" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ flexDirection: "row", gap: 2, height: 10 }}>
      {segmentos.map((s, i) => <View key={i} testID="segmento-composicao" style={{ flexGrow: s.valor, flexBasis: 0, minWidth: 4, borderRadius: 5, backgroundColor: s.cor }} />)}
    </View>
  );
}
