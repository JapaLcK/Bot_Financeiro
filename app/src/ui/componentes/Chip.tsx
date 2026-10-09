import { Animated, Pressable } from "react-native";

import { selecao } from "@/ui/haptics";
import { usePressao } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { raio } from "@/ui/tokens";

import { Texto } from "./Texto";

interface Props {
  rotulo: string;
  selecionado: boolean;
  onPress: () => void;
  desativado?: boolean;
}

/**
 * Alvo de toque de 44pt na ALTURA e na LARGURA pelo tamanho real do
 * `Pressable`, não por `hitSlop`: o React Native recorta o `hitSlop` nos
 * limites do pai, e numa linha de chips o pai encolhe para a altura do chip
 * — a folga prometida não existiria (numa `ScrollView` horizontal, que corta
 * o conteúdo, menos ainda). Por isso a pílula visível tem 34pt e o
 * `Pressable` em volta, 44pt. O `minWidth` cobre o rótulo de 1-2
 * caracteres. Selecionado usa `brandSoft`/`brandInk` (par medido em `PARES`,
 * ≥4,5 nos dois temas). O contorno de 1px (`inkMuted`/`brandInk`, também em
 * `PARES`) existe porque o fundo `surface` do não selecionado some dentro de
 * um `Card`, que tem o mesmo fundo.
 */
export function Chip({ rotulo, selecionado, onPress, desativado }: Props) {
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
      disabled={desativado}
      accessibilityRole="button"
      accessibilityState={{ selected: selecionado }}
      style={{ minHeight: 44, justifyContent: "center" }}
    >
      <Animated.View
        style={[
          {
            paddingHorizontal: 14,
            borderRadius: raio.pilula,
            borderWidth: 1,
            borderColor: selecionado ? cores.brandInk : cores.inkMuted,
            alignItems: "center",
            justifyContent: "center",
            minHeight: 34,
            minWidth: 44,
            backgroundColor: selecionado ? cores.brandSoft : cores.surface,
          },
          desativado && { opacity: 0.5 },
          pressao.estilo,
        ]}
      >
        <Texto variante="rotulo" tom={selecionado ? "brandInk" : "inkMuted"} style={{ fontSize: 15, lineHeight: 20 }}>
          {rotulo}
        </Texto>
      </Animated.View>
    </Pressable>
  );
}
