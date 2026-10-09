import { useEffect, useRef, useState } from "react";
import { Animated, View } from "react-native";

import { facilitador, useReduzirMovimento } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { raio, type Paleta } from "@/ui/tokens";

type Tom = "brand" | "positive" | "warning" | "danger";

interface Props {
  /** 0..1. Fora da faixa é clampado; não-finito (NaN) vira 0. */
  valor: number;
  tom?: Tom;
}

/**
 * Trilha em `border`: a barra mora dentro de `Card` (`surface`), e a trilha
 * `surface` sumia no fundo do card — não se via onde ficava o 100%. O
 * preenchimento continua medido contra o que o cerca por cima e por baixo, o
 * `surface` do card: `brand` (≥3, os dois temas) e `positive`/`warning`/
 * `danger` (≥4,5, TEXTO×FUNDOS em `PARES`). A trilha é referência visual, não
 * o único sinal: `brand`×`border` mede 2,80 no claro, abaixo de 3, e por isso
 * não entra em `PARES` (remeça se a paleta mudar). Preenchimento por `transform: scaleX` (não
 * `width`) por performance: o RN 0.86 tem `transformOrigin` para escalar a
 * partir da esquerda sem cálculo manual de translação.
 */
export function ProgressBar({ valor, tom = "brand" }: Props) {
  const { cores } = useTema();
  // NaN primeiro (não é finito nem clampável); Infinity/-Infinity passam pelo
  // clamp normal (Math.min/Math.max) e caem em 1/0 — a ordem antiga
  // (`Number.isFinite` como guarda) tratava ±Infinity como NaN e devolvia a
  // barra VAZIA (`scaleX: 0`) bem no momento em que o valor estourou.
  const clampado = Number.isNaN(valor) ? 0 : Math.min(1, Math.max(0, valor));
  const corPreenchimento: keyof Paleta = tom;
  // `accessibilityValue.now` da RN quer inteiro: 0..100 arredondado, não 0..1.
  const agora = Math.round(clampado * 100);

  return (
    <View
      accessibilityRole="progressbar"
      accessibilityValue={{ min: 0, max: 100, now: agora }}
      style={{ height: 8, borderRadius: raio.sm, backgroundColor: cores.border, overflow: "hidden" }}
    >
      <View
        style={{
          flex: 1,
          backgroundColor: cores[corPreenchimento],
          transform: [{ scaleX: clampado }],
          transformOrigin: "left",
        }}
      />
    </View>
  );
}

/**
 * Sem porcentagem: algo roda, sem saber quanto falta. Mesma trilha e mesmo
 * preenchimento da `ProgressBar`; um segmento de 30% corre da esquerda para a
 * direita sem parar (`translateX`, `useNativeDriver`). Com "reduzir movimento"
 * nada se desloca: a trilha fica cheia e PULSA (opacidade) — parada e cheia ela
 * leria como "terminou". Sem `accessibilityValue.now`: não há número a dizer.
 */
export function ProgressBarIndeterminada({ rotulo }: { rotulo: string }) {
  const { cores } = useTema();
  const reduzir = useReduzirMovimento();
  const [largura, setLargura] = useState(0);
  const anima = useRef(new Animated.Value(0)).current;
  const segmento = largura * 0.3;

  useEffect(() => {
    anima.setValue(0);
    const passo = (toValue: number) => Animated.timing(anima, { toValue, duration: 1400, easing: facilitador, useNativeDriver: true });
    const loop = Animated.loop(reduzir ? Animated.sequence([passo(1), passo(0)]) : passo(1));
    loop.start();
    return () => loop.stop();
  }, [reduzir, anima]);

  const estilo = reduzir
    ? { flex: 1, opacity: anima.interpolate({ inputRange: [0, 1], outputRange: [1, 0.4] }) }
    : { width: segmento, flex: 1, transform: [{ translateX: anima.interpolate({ inputRange: [0, 1], outputRange: [-segmento, largura] }) }] };

  return (
    <View
      accessible
      accessibilityRole="progressbar"
      accessibilityLabel={rotulo}
      accessibilityState={{ busy: true }}
      onLayout={(e) => setLargura(e.nativeEvent.layout.width)}
      style={{ height: 8, borderRadius: raio.sm, backgroundColor: cores.surface, overflow: "hidden" }}
    >
      <Animated.View style={[{ backgroundColor: cores.brand }, estilo]} />
    </View>
  );
}
