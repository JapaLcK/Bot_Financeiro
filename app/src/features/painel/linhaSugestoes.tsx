import { Pressable, ScrollView, View } from "react-native";
import { selecao } from "@/ui/haptics";
import { useTema } from "@/ui/tema";
import { raio } from "@/ui/tokens";
import { TextoPainel as Texto } from "./tipografia";
/** Sugestões do chat: pílulas com contorno numa ÚNICA linha que rola na horizontal. A pílula tem 36pt; o `Pressable` de 44pt em volta é o alvo de toque (mesma razão do `Chip`: `hitSlop` é recortado pelo pai). */
export function LinhaSugestoes({ itens, desativado }: { itens: { rotulo: string; aoEscolher: () => void }[]; desativado: boolean }) {
  const { cores } = useTema();
  return <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="handled" style={{ flexGrow: 0 }} contentContainerStyle={{ gap: 8 }}>
    {itens.map((x) => <Pressable key={x.rotulo} accessibilityRole="button" accessibilityState={{ disabled: desativado }} disabled={desativado} onPress={() => { selecao(); x.aoEscolher(); }} style={{ paddingVertical: 4, justifyContent: "center" }}>
      <View style={{ minHeight: 36, justifyContent: "center", paddingHorizontal: 14, borderWidth: 1, borderColor: cores.border, borderRadius: raio.pilula, opacity: desativado ? 0.5 : 1 }}><Texto variante="rotulo" numberOfLines={1}>{x.rotulo}</Texto></View>
    </Pressable>)}
  </ScrollView>;
}
