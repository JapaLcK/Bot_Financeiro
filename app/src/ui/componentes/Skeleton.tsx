import { useEffect, useRef } from "react";
import { Animated } from "react-native";

import { facilitador, useReduzirMovimento } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { raio as raioTokens } from "@/ui/tokens";

interface Props {
  largura: number;
  altura: number;
  raio?: number;
}

const DURACAO_PULSO = 700;

/**
 * Forma REAL (impeccable): `largura`/`altura`/`raio` são do CHAMADOR — o
 * retângulo do skeleton tem a forma exata do conteúdo que ele substitui, não
 * um bloco genérico. Com "reduzir movimento" o pulso desliga e o bloco fica
 * parado em opacidade cheia (ainda indica "carregando", só sem o movimento).
 */
export function Skeleton({ largura, altura, raio = raioTokens.sm }: Props) {
  const { cores } = useTema();
  const reduzir = useReduzirMovimento();
  const opacidade = useRef(new Animated.Value(reduzir ? 1 : 0.5)).current;

  useEffect(() => {
    if (reduzir) {
      opacidade.setValue(1);
      return;
    }
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacidade, { toValue: 1, duration: DURACAO_PULSO, easing: facilitador, useNativeDriver: true }),
        Animated.timing(opacidade, { toValue: 0.5, duration: DURACAO_PULSO, easing: facilitador, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [reduzir, opacidade]);

  return (
    <Animated.View
      accessible
      accessibilityLabel="Carregando"
      style={{ width: largura, height: altura, borderRadius: raio, backgroundColor: cores.border, opacity: opacidade }}
    />
  );
}
