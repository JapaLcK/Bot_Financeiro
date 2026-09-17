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
 * Visualmente compacto (padding vertical menor que 44pt — o pill de filtro
 * do site) e `hitSlop` fecha a diferença até o toque de 44pt nos 4 LADOS —
 * um rótulo de 1-2 caracteres também fica estreito demais na horizontal, por
 * isso o `minWidth` no visual. Selecionado usa
 * `brandSoft`/`brandInk` (par medido em `PARES`, ≥4,5 nos dois temas).
 *
 * Hipótese não verificável neste ambiente: no iOS o `hitSlop` amplia a área
 * que RECEBE o toque, mas não o `accessibilityFrame` que o VoiceOver desenha
 * — o retângulo do leitor de tela continua do tamanho visual. Só o aparelho
 * com VoiceOver ligado confirma isso.
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
      hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
    >
      <Animated.View
        style={[
          {
            paddingVertical: espaco.sm,
            paddingHorizontal: espaco.lg,
            borderRadius: raio.md,
            alignItems: "center",
            justifyContent: "center",
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
