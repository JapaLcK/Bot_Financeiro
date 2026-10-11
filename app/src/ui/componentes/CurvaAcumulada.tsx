import { useEffect, useId, useRef, useState } from "react";
import { Animated, View } from "react-native";
import Svg, { Defs, Line, LinearGradient, Path, Stop } from "react-native-svg";

import { facilitador, useReduzirMovimento } from "@/ui/motion";
import { useTema } from "@/ui/tema";

export interface CurvaAcumuladaProps {
  /** Valor acumulado por dia (`pontos[i]` = dia `i + 1`); o eixo X vai de 1 a `diasNoMes`, o resto fica vazio (nunca desenha o futuro). */
  pontos: number[];
  diasNoMes: number;
  /** Índice do ponto "hoje" (ponto rosa com pulso); `null` = sem ponto (mês passado). */
  indiceHoje: number | null;
  /** Linha tracejada horizontal no mesmo eixo Y da curva (ex.: total do mês passado); `null` = sem linha. */
  referencia: number | null;
}

const ALTURA = 120, MARGEM_X = 6, TOPO = 14, BASE = 6, RAIO_PONTO = 4;
const DESENHO_MS = 500, PULSO_MS = 2400; // duração própria do gráfico: `duracoes` do app é de feedback/transição (≤ 400)
const AnimatedPath = Animated.createAnimatedComponent(Path);

/**
 * Curva de acumulado (identidade do /painel): linha de 2pt em `ink`, sem eixos nem grade, área em degradê vertical da mesma cor (8% junto da linha até 0% embaixo),
 * referência tracejada fina e, só em "hoje", ponto rosa com anel pulsante (escala 1 a 3,2 em 2,4s). A linha se desenha
 * da esquerda para a direita em 500ms; com "reduzir movimento" a curva já nasce pronta e o anel nem existe.
 * Decorativa: some do VoiceOver, quem usa o gráfico lê os números e a legenda ao lado.
 */
export function CurvaAcumulada({ pontos, diasNoMes, indiceHoje, referencia }: CurvaAcumuladaProps) {
  const { cores } = useTema();
  const idArea = `area${useId().replace(/\W/g, "")}`; // um id por instância: várias curvas na tela não podem compartilhar o degradê
  const reduzir = useReduzirMovimento();
  const [largura, setLargura] = useState(0);
  const desenho = useRef(new Animated.Value(0)).current;
  const pulso = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reduzir) { desenho.setValue(1); return; }
    desenho.setValue(0);
    const a = Animated.timing(desenho, { toValue: 1, duration: DESENHO_MS, easing: facilitador, useNativeDriver: false });
    a.start();
    return () => a.stop();
  }, [reduzir, desenho]);

  useEffect(() => {
    if (reduzir || indiceHoje === null) return;
    pulso.setValue(0);
    const loop = Animated.loop(Animated.timing(pulso, { toValue: 1, duration: PULSO_MS, easing: facilitador, useNativeDriver: true }));
    loop.start();
    return () => loop.stop();
  }, [reduzir, indiceHoje, pulso]);

  if (!pontos.length || diasNoMes < 2) return null;
  const maximo = Math.max(...pontos, referencia ?? 0), minimo = Math.min(0, ...pontos), faixa = (maximo - minimo) * 1.1 || 1;
  const x = (i: number) => MARGEM_X + (i / (diasNoMes - 1)) * (largura - 2 * MARGEM_X);
  const y = (v: number) => TOPO + (1 - (v - minimo) / faixa) * (ALTURA - TOPO - BASE);
  const linha = pontos.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${linha} L${x(pontos.length - 1).toFixed(1)},${y(minimo)} L${x(0).toFixed(1)},${y(minimo)} Z`;
  const comprimento = pontos.slice(1).reduce((s, v, i) => s + Math.hypot(x(i + 1) - x(i), y(v) - y(pontos[i]!)), 0) + 1;
  const hoje = indiceHoje === null ? null : { cx: x(indiceHoje), cy: y(pontos[indiceHoje]!) };
  const ponto = { position: "absolute", width: 2 * RAIO_PONTO, height: 2 * RAIO_PONTO, borderRadius: RAIO_PONTO, backgroundColor: cores.brand } as const;

  return (
    <View testID="curva-acumulada" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" onLayout={(e) => setLargura(e.nativeEvent.layout.width)} style={{ height: ALTURA }}>
      {largura > 0 && (
        <>
          <Svg width={largura} height={ALTURA}>
            {referencia !== null && <Line testID="curva-referencia" x1={MARGEM_X} x2={largura - MARGEM_X} y1={y(referencia)} y2={y(referencia)} stroke={cores.inkFaint} strokeWidth={1} strokeDasharray="4 4" />}
            <Defs><LinearGradient id={idArea} x1="0" y1="0" x2="0" y2="1"><Stop offset="0" stopColor={cores.ink} stopOpacity={0.08} /><Stop offset="1" stopColor={cores.ink} stopOpacity={0} /></LinearGradient></Defs>
            <AnimatedPath testID="curva-area" d={area} fill={`url(#${idArea})`} fillOpacity={desenho.interpolate({ inputRange: [0, 1], outputRange: [0, 1] })} />
            <AnimatedPath testID="curva-linha" d={linha} fill="none" stroke={cores.ink} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={`${comprimento} ${comprimento}`} strokeDashoffset={desenho.interpolate({ inputRange: [0, 1], outputRange: [comprimento, 0] })} />
          </Svg>
          {hoje && (
            <Animated.View testID="ponto-hoje" style={{ position: "absolute", left: hoje.cx - RAIO_PONTO, top: hoje.cy - RAIO_PONTO, width: 2 * RAIO_PONTO, height: 2 * RAIO_PONTO, opacity: desenho.interpolate({ inputRange: [0.85, 1], outputRange: [0, 1], extrapolate: "clamp" }) }}>
              {!reduzir && <Animated.View testID="anel-pulso" style={[ponto, { left: 0, top: 0, opacity: pulso.interpolate({ inputRange: [0, 1], outputRange: [0.5, 0] }), transform: [{ scale: pulso.interpolate({ inputRange: [0, 1], outputRange: [1, 3.2] }) }] }]} />}
              <View testID="ponto-hoje-centro" style={[ponto, { left: 0, top: 0 }]} />
            </Animated.View>
          )}
        </>
      )}
    </View>
  );
}
