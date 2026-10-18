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
 * `Pressable` (8 de folga em cima e embaixo do chip de 28; não é `minHeight`, que
 * abriria um vão maior entre chips de uma linha do que entre os de duas), não por `hitSlop`: o React Native recorta o `hitSlop` nos
 * limites do pai, e numa linha de chips o pai encolhe para a altura do chip
 * — a folga prometida não existiria (numa `ScrollView` horizontal, que corta
 * o conteúdo, menos ainda). Por isso o chip visível tem 28pt (PDF) e o
 * `Pressable` em volta, 44pt. O `minWidth` cobre o rótulo de 1-2
 * caracteres. Selecionado usa `brandSoft`/`brandInk` (par medido em `PARES`,
 * ≥4,5 nos dois temas). Sem contorno (PDF): o fundo `surfaceControl` é um
 * plano acima do `surface` do Card. O `paddingVertical` deixa o rótulo quebrar
 * em 2 linhas sem encostar na borda.
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
      style={{ paddingVertical: 8, justifyContent: "center" }}
    >
      <Animated.View
        style={[
          {
            paddingHorizontal: 10,
            paddingVertical: 6,
            borderRadius: raio.pilula,
            alignItems: "center",
            justifyContent: "center",
            minHeight: 28,
            minWidth: 44,
            backgroundColor: selecionado ? cores.brandSoft : cores.surfaceControl,
          },
          desativado && { opacity: 0.5 },
          pressao.estilo,
        ]}
      >
        <Texto variante="legenda" tom={selecionado ? "brandInk" : "inkMuted"}>
          {rotulo}
        </Texto>
      </Animated.View>
    </Pressable>
  );
}
