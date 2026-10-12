import { useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, Easing } from "react-native";
import { Easing as EasingR, FadeInDown } from "react-native-reanimated";

/** feedback = toque; transicao = crossfade/entrada de tela; sheet = abrir folha. */
export const duracoes = { feedback: 160, transicao: 240, sheet: 400 } as const;

/** Curva única do produto (emil-design-eng): nunca ease-in, sempre esta saída suave. */
const CURVA = [0.23, 1, 0.32, 1] as const;
export const facilitador = Easing.bezier(...CURVA);

const ENTRADA = FadeInDown.duration(duracoes.transicao).easing(EasingR.bezier(...CURVA));
/** `entering` das bolhas e dos chips; `undefined` (sem animação) com "reduzir movimento". */
export function useEntrada() {
  return useReduzirMovimento() ? undefined : ENTRADA;
}

// Último valor lido: a leitura é assíncrona, então montagens novas começariam em `false` por 1 quadro.
let ultimoValor = false;

/** Lê e acompanha "Reduzir movimento" do sistema (iOS/Android). */
export function useReduzirMovimento(): boolean {
  const [reduzir, setReduzirEstado] = useState(ultimoValor);
  useEffect(() => {
    let montado = true;
    const setReduzir = (v: boolean) => {
      ultimoValor = v;
      if (montado) setReduzirEstado(v);
    };
    AccessibilityInfo.isReduceMotionEnabled().then(setReduzir);
    const assinatura = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduzir);
    return () => {
      montado = false;
      assinatura.remove();
    };
  }, []);
  return reduzir;
}

/**
 * Press scale 0.97/160ms, `Animated` nativo: `useNativeDriver` cobre transform
 * e opacidade; o Reanimated fica só para entrada/saída (`useEntrada`). Nunca `scale(0)`, só
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
