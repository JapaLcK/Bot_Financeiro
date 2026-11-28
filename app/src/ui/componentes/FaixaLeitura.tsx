import { View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

import { Icone } from "./Icone";
import { Texto } from "./Texto";

/**
 * A faixa fixa da sessão de leitor (credencial de visualização): fica no topo,
 * fora da rolagem das telas, e leva a área segura de cima (o `LayoutApp` zera
 * o `top` do que vem abaixo, para não somar o inset duas vezes). Neutra, sem rosa:
 * é aviso de estado, não decisão.
 */
export function FaixaLeitura() {
  const { cores } = useTema();
  const { top } = useSafeAreaInsets();
  return (
    <View
      accessibilityRole="summary"
      testID="faixa-leitura"
      style={{
        paddingTop: top + espaco.xs,
        paddingBottom: espaco.xs,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: espaco.sm,
        backgroundColor: cores.surfaceRaised,
        borderBottomWidth: 1,
        borderBottomColor: cores.border,
      }}
    >
      <Icone nome="Eye" tamanho={16} tom="inkMuted" />
      <Texto variante="legenda" tom="inkMuted">
        Modo visualização · só leitura
      </Texto>
    </View>
  );
}
