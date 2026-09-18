import { Animated, Pressable } from "react-native";

import { selecao } from "@/ui/haptics";
import { usePressao } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { espaco, raio } from "@/ui/tokens";

import { Texto } from "./Texto";

interface Props {
  rotulo: string;
  selecionado: boolean;
  onPress: () => void;
}

/**
 * Alvo de toque de 44pt na ALTURA e na LARGURA pelo tamanho real do
 * `Pressable`, não por `hitSlop`: o React Native recorta o `hitSlop` nos
 * limites do pai, e numa linha de chips o pai encolhe para a altura do chip
 * — a folga prometida não existiria. O `minWidth` cobre o rótulo de 1-2
 * caracteres. Selecionado usa `brandSoft`/`brandInk` (par medido em `PARES`,
 * ≥4,5 nos dois temas).
 */
export function Chip({ rotulo, selecionado, onPress }: Props) {
  const { cores } = useTema();
  const pressao = usePressao();

  function aoPressionar() {
    selecao();
    onPress();
  }

  return (
    <Pressable
      onPress={aoPressionar}
      onPressIn={pressao.aoPressionar}
      onPressOut={pressao.aoSoltar}
      accessibilityRole="button"
      accessibilityState={{ selected: selecionado }}
    >
      <Animated.View
        style={[
          {
            paddingHorizontal: espaco.lg,
            borderRadius: raio.md,
            alignItems: "center",
            justifyContent: "center",
            minHeight: 44,
            minWidth: 44,
            backgroundColor: selecionado ? cores.brandSoft : cores.surface,
          },
          pressao.estilo,
        ]}
      >
        <Texto variante="rotulo" tom={selecionado ? "brandInk" : "inkMuted"}>
          {rotulo}
        </Texto>
      </Animated.View>
    </Pressable>
  );
}
