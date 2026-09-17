import type { ReactNode } from "react";
import { Animated, Pressable, View } from "react-native";

import { usePressao } from "@/ui/motion";
import { espaco } from "@/ui/tokens";

import { Icone, type NomeIcone } from "./Icone";
import { Texto } from "./Texto";

interface Props {
  icone?: NomeIcone;
  titulo: string;
  subtitulo?: string;
  trailing?: ReactNode;
  chevron?: boolean;
  onPress?: () => void;
}

/**
 * `role button` só existe com `onPress`: uma linha sem ação não é um
 * controle. `usePressao()` (listener de acessibilidade + 2 `Animated.Value`)
 * também só existe quando há `onPress` — extraído para este componente
 * interno em vez de chamado sempre: uma lista de 50 linhas sem ação nenhuma
 * não tem por que montar 100 `Animated.Value` e 50 assinaturas de
 * `reduceMotionChanged` à toa.
 */
function LinhaPressionavel({ onPress, children }: { onPress: () => void; children: ReactNode }) {
  const pressao = usePressao();
  return (
    <Pressable onPress={onPress} onPressIn={pressao.aoPressionar} onPressOut={pressao.aoSoltar} accessibilityRole="button">
      <Animated.View style={pressao.estilo}>{children}</Animated.View>
    </Pressable>
  );
}

export function ListRow({ icone, titulo, subtitulo, trailing, chevron = false, onPress }: Props) {
  const conteudo = (
    <View
      style={{
        flexDirection: "row",
        alignItems: "center",
        minHeight: 56,
        gap: espaco.md,
        paddingVertical: espaco.sm,
      }}
    >
      {icone ? <Icone nome={icone} tamanho={24} tom="inkMuted" /> : null}
      <View style={{ flex: 1 }}>
        <Texto variante="corpo" tom="ink">
          {titulo}
        </Texto>
        {subtitulo ? (
          <Texto variante="legenda" tom="inkMuted">
            {subtitulo}
          </Texto>
        ) : null}
      </View>
      {trailing}
      {chevron ? <Icone nome="CaretRight" tamanho={20} tom="inkFaint" /> : null}
    </View>
  );

  if (!onPress) return conteudo;

  return <LinhaPressionavel onPress={onPress}>{conteudo}</LinhaPressionavel>;
}
