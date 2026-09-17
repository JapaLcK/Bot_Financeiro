import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, Easing } from "react-native";

/** feedback = toque; transicao = crossfade/entrada de tela; sheet = abrir folha. */
export const duracoes = { feedback: 150, transicao: 250, sheet: 400 } as const;

/** Curva única do produto (emil-design-eng): nunca ease-in, sempre esta saída suave. */
export const facilitador = Easing.bezier(0.23, 1, 0.32, 1);

/** Lê e acompanha "Reduzir movimento" do sistema (iOS/Android). */
export function useReduzirMovimento(): boolean {
  const [reduzir, setReduzir] = useState(false);
  useEffect(() => {
    let montado = true;
    AccessibilityInfo.isReduceMotionEnabled().then((v) => {
      if (montado) setReduzir(v);
    });
    const assinatura = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduzir);
    return () => {
      montado = false;
      assinatura.remove();
    };
  }, []);
  return reduzir;
}

/**
 * Press scale 0.97/150ms, `Animated` nativo — sem reanimated: com o Sheet
 * nativo do expo-router (decisão do dono), nada nesta fase precisa dele, e
 * `Animated` com `useNativeDriver` cobre transform e opacidade. Nunca `scale(0)`, só
 * transform/opacity, sempre `useNativeDriver`.
 *
 * Com "reduzir movimento" a escala fica parada e só a opacidade desce a 0.85:
 * o feedback do toque continua existindo, sem o movimento que a preferência
 * pede para tirar.
 */
export function usePressao() {
  const reduzir = useReduzirMovimento();
  const escala = useRef(new Animated.Value(1)).current;
  const opacidade = useRef(new Animated.Value(1)).current;
  const valorAnimado = reduzir ? opacidade : escala;

  const anima = (paraBaixo: boolean) =>
    Animated.timing(valorAnimado, {
      toValue: paraBaixo ? (reduzir ? 0.85 : 0.97) : 1,
      duration: duracoes.feedback,
      easing: facilitador,
      useNativeDriver: true,
    }).start();

  return {
    aoPressionar: () => anima(true),
    aoSoltar: () => anima(false),
    estilo: reduzir ? { opacity: opacidade } : { transform: [{ scale: escala }] },
  };
}
