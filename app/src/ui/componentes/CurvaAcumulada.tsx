import { createContext, useContext, useEffect, useId, useRef, useState } from "react";
import { Animated, type GestureResponderEvent, StyleSheet, View } from "react-native";
import Svg, { Defs, Line, LinearGradient, Path, Stop } from "react-native-svg";

import { partes } from "@/ui/dinheiro";
import { selecao } from "@/ui/haptics";
import { facilitador, useReduzirMovimento } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { raio, texto as escala } from "@/ui/tokens";

import { Texto } from "./Texto";

export interface CurvaAcumuladaProps {
  /** Valor acumulado por dia (`pontos[i]` = dia `i + 1`); o eixo X vai de 1 a `diasNoMes`, o resto fica vazio (nunca desenha o futuro). Nunca negativo: o eixo Y começa em 0 e `pontosAcumulados` esconde a curva se algum ponto for. */
  pontos: number[];
  /** Mês passado no mesmo eixo (tracejada, cinza, só linha); `null` = sem a série. */
  anterior?: number[] | null;
  diasNoMes: number;
  /** Índice do ponto "hoje" (ponto com anel pulsante); `null` = sem ponto (mês passado). */
  indiceHoje: number | null;
  /** Cor da curva atual e do ponto: o sentido do ritmo (`positive` abaixo, `warning` acima) ou `ink` sem comparação. */
  tom?: "ink" | "positive" | "warning";
}

const ALTURA = 130, MARGEM_X = 12, TOPO = 8, BASE = 8, RAIO_PONTO = 4, COLUNA_Y = 44;
const SEGURAR_MS = 250, FOLGA_DEDO = 10, BOLHA_W = 124, BOLHA_W_COM_ANTERIOR = 148, ALTURA_LINHA = 16, FOLGA_BOLHA = 10;
const DESENHO_MS = 500, PULSO_MS = 2400; // duração própria do gráfico: `duracoes` do app é de feedback/transição (≤ 400)
/** Aviso do gráfico ao card que o contém: `current = true` quando a bolha abre (o toque que segue NÃO é um toque no card). Quem zera é o card. */
export const SegurouContext = createContext<{ current: boolean } | null>(null);
/**
 * A bolha vai ACIMA do ponto; se não cabe dentro do gráfico (ponto perto do topo, ela cobriria a linha "vs R$ ... mês anterior"), vai ABAIXO.
 * Usa `lineHeight` fixo de 16 (a `legenda`): com fonte ampliada a bolha cresce e a escolha pode errar; limite aceito.
 */
export function ladoDaBolha(cy: number, linhas: number) {
  return cy >= linhas * ALTURA_LINHA + 8 + FOLGA_BOLHA ? "acima" : "abaixo"; // 8 = padding 3+3 e borda 1+1
}
const AnimatedPath = Animated.createAnimatedComponent(Path);

/** Degraus "redondos" (1, 2, 3, 5 × 10^n reais) com no máximo 4 intervalos; em centavos, de 0 até o topo, que cobre `maximo`. */
export function ticksY(maximo: number) {
  for (let e = 0; e < 14; e++) for (const k of [1, 2, 3, 5]) {
    const passo = k * 10 ** e * 100, n = Math.max(1, Math.ceil(maximo / passo));
    if (n <= 4) return Array.from({ length: n + 1 }, (_, i) => i * passo);
  }
  return [0, maximo]; // ponytail: acima de R$ 10 trilhões não há degrau redondo; ninguém chega lá
}
/** Centavos em "R$ 950", "R$ 7,2k", "R$ 12k" (reais inteiros; no "k", uma casa e vírgula pt-BR); o valor exato fica na lista. */
// ponytail: sem "mi" — acima de R$ 999,9 mil sai "R$ 1000k"; trocar por Intl compact se alguém chegar lá
export function rotuloCompacto(centavos: number) {
  const reais = centavos / 100;
  return Math.round(reais) < 1000 ? `R$ ${Math.round(reais)}` : `R$ ${String(Math.round(reais / 100) / 10).replace(".", ",")}k`;
}

/** Índice do dia mais próximo de `x` (pt dentro do plot, mesma régua do desenho), preso a [0, `limite`]: nunca depois de "hoje". */
export function indiceDoDia(x: number, largura: number, diasNoMes: number, limite: number) {
  const util = largura - 2 * MARGEM_X;
  if (util <= 0 || diasNoMes < 2) return 0;
  return Math.min(Math.max(Math.round(((x - MARGEM_X) / util) * (diasNoMes - 1)), 0), limite);
}
export const reais = (c: number) => { const p = partes(c); return p ? `R$ ${p.inteiro},${p.centavos}` : "—"; };
/** Textos da bolha: o valor é o `pontos[i]` desenhado (nunca recalculado); a 2ª linha ("mês passado R$ ...") só com a série do mês passado, no mesmo `i` (a série já vem preenchida até o fim do mês). */
export function conteudoBolha(i: number, pontos: number[], anterior: number[] | null) {
  return { dia: `dia ${i + 1}`, valor: reais(pontos[i]!), anterior: anterior && `mês passado ${reais(anterior[Math.min(i, anterior.length - 1)]!)}` };
}

/**
 * Curva de acumulado (identidade do /painel): linha de 2pt na cor do tom, área em degradê vertical da mesma cor, e, quando há,
 * o mês passado só em linha tracejada cinza no MESMO eixo Y (o máximo das duas séries). Eixo Y em degraus redondos com grade
 * bem fraca, eixo X com os dias 1, 15, 25 e o último, legenda embaixo e, só em "hoje", ponto com anel pulsante (escala 1 a 3,2 em 2,4s).
 * A linha se desenha da esquerda para a direita em 500ms; com "reduzir movimento" a curva já nasce pronta e o anel nem existe.
 * Segurar o dedo (250ms) no gráfico abre a bolha do dia (valor deste mês e, se houver, do mês passado) e arrastar troca o dia; soltar fecha.
 * O gesto usa só os eventos brutos de toque (`onTouch*`), que chegam ao View onde o dedo pousou MESMO com o card (`Pressable`) sendo o responder.
 * Não dá para disputar o responder: com um ancestral já responder, o ResponderEventPlugin só negocia a troca com os ANTEPASSADOS dele,
 * nunca com um filho (`onMoveShouldSetResponderCapture`/`onResponderMove` do filho não rodam). Por isso o toque curto segue para o card
 * (navega) e, ao abrir a bolha, o gráfico marca `SegurouContext` para o card (ver `Bloco`) não navegar ao soltar.
 * Decorativa: tudo, inclusive os rótulos e a bolha, some do VoiceOver; quem usa o gráfico lê os números e o chip ao lado.
 */
export function CurvaAcumulada({ pontos, anterior = null, diasNoMes, indiceHoje, tom = "ink" }: CurvaAcumuladaProps) {
  const { cores } = useTema();
  const idArea = `area${useId().replace(/\W/g, "")}`; // um id por instância: várias curvas na tela não podem compartilhar o degradê
  const reduzir = useReduzirMovimento();
  const [largura, setLargura] = useState(0);
  const segurou = useContext(SegurouContext), [sel, setSel] = useState<number | null>(null);
  const ativo = useRef<number | null>(null), timer = useRef<ReturnType<typeof setTimeout>>(undefined), origem = useRef({ x: 0, y: 0 });
  const desenho = useRef(new Animated.Value(0)).current;
  const pulso = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reduzir) { desenho.setValue(1); return; }
    desenho.setValue(0);
    const a = Animated.timing(desenho, { toValue: 1, duration: DESENHO_MS, easing: facilitador, useNativeDriver: false });
    a.start();
    return () => a.stop();
  }, [reduzir, desenho]);

  useEffect(() => () => clearTimeout(timer.current), []);

  useEffect(() => {
    if (reduzir || indiceHoje === null) return;
    pulso.setValue(0);
    const loop = Animated.loop(Animated.timing(pulso, { toValue: 1, duration: PULSO_MS, easing: facilitador, useNativeDriver: true }));
    loop.start();
    return () => loop.stop();
  }, [reduzir, indiceHoje, pulso]);

  if (!pontos.length || diasNoMes < 2) return null;
  const serieAnterior = anterior?.slice(0, diasNoMes) ?? null; // mês passado mais longo que o corrente: o eixo X acaba no último dia do corrente
  const ticks = ticksY(Math.max(...pontos, ...(serieAnterior ?? []))), topo = ticks.at(-1)!;
  const x = (i: number) => MARGEM_X + (i / (diasNoMes - 1)) * (largura - 2 * MARGEM_X);
  const y = (v: number) => TOPO + (1 - v / topo) * (ALTURA - TOPO - BASE);
  const caminho = (serie: number[]) => serie.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const linha = caminho(pontos);
  const area = `${linha} L${x(pontos.length - 1).toFixed(1)},${y(0)} L${x(0).toFixed(1)},${y(0)} Z`;
  const comprimento = pontos.slice(1).reduce((s, v, i) => s + Math.hypot(x(i + 1) - x(i), y(v) - y(pontos[i]!)), 0) + 1;
  const hoje = indiceHoje === null ? null : { cx: x(indiceHoje), cy: y(pontos[indiceHoje]!) };
  const cor = cores[tom];
  const ponto = { position: "absolute", width: 2 * RAIO_PONTO, height: 2 * RAIO_PONTO, borderRadius: RAIO_PONTO, backgroundColor: cor } as const;
  const limite = indiceHoje ?? pontos.length - 1;
  const escolher = (px: number) => { if (segurou) segurou.current = true; const i = indiceDoDia(px, largura, diasNoMes, limite); if (i !== ativo.current) { ativo.current = i; selecao(); setSel(i); } };
  const fechar = () => { clearTimeout(timer.current); ativo.current = null; setSel(null); };
  const gesto = {
    onTouchStart: (e: GestureResponderEvent) => { const { locationX, pageX, pageY } = e.nativeEvent; origem.current = { x: pageX, y: pageY }; clearTimeout(timer.current); timer.current = setTimeout(() => escolher(locationX), SEGURAR_MS); },
    onTouchMove: (e: GestureResponderEvent) => {
      const { locationX, pageX, pageY } = e.nativeEvent; // `locationX` é do View onde o dedo pousou (este, sem filhos), também nas moves
      if (ativo.current !== null) escolher(locationX);
      else if (Math.hypot(pageX - origem.current.x, pageY - origem.current.y) > FOLGA_DEDO) clearTimeout(timer.current); // moveu antes da hora: é rolagem, não segurar
    },
    onTouchEnd: fechar, onTouchCancel: fechar,
  };
  const bolha = sel === null || sel >= pontos.length ? null : { cx: x(sel), cy: y(pontos[sel]!), w: serieAnterior ? BOLHA_W_COM_ANTERIOR : BOLHA_W, lado: ladoDaBolha(y(pontos[sel]!), serieAnterior ? 2 : 1), ...conteudoBolha(sel, pontos, serieAnterior) };
  const dias = [...new Set([1, 15, 25, diasNoMes])].filter((n) => n <= diasNoMes);
  const legenda = [{ rotulo: "Este mês", tracejado: false }, ...(serieAnterior ? [{ rotulo: "Mês passado", tracejado: true }] : [])];

  return (
    <View testID="curva-acumulada" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ gap: 8 }}>
      <View style={{ flexDirection: "row" }}>
        <View style={{ width: COLUNA_Y, height: ALTURA }}>
          {ticks.map((t) => <Texto key={t} testID="curva-rotulo-y" variante="legenda" tom="inkFaint" numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.7} style={{ position: "absolute", right: 6, top: y(t) - 8, height: 16, lineHeight: 16 }}>{rotuloCompacto(t)}</Texto>)}
        </View>
        <View style={{ flex: 1 }}>
          <View testID="curva-plot" onLayout={(e) => setLargura(e.nativeEvent.layout.width)} style={{ height: ALTURA }}>
            {largura > 0 && (
              <>
                <Svg width={largura} height={ALTURA}>
                  {ticks.map((t) => <Line key={t} testID="curva-grade" x1={0} x2={largura} y1={y(t)} y2={y(t)} stroke={cores.border} strokeWidth={1} />)}
                  <Defs><LinearGradient id={idArea} x1="0" y1="0" x2="0" y2="1"><Stop offset="0" stopColor={cor} stopOpacity={0.15} /><Stop offset="1" stopColor={cor} stopOpacity={0} /></LinearGradient></Defs>
                  {serieAnterior && <AnimatedPath testID="curva-anterior" d={caminho(serieAnterior)} fill="none" stroke={cores.inkFaint} strokeWidth={1.5} strokeDasharray="4 4" strokeLinecap="round" strokeLinejoin="round" strokeOpacity={desenho} />}
                  <AnimatedPath testID="curva-area" d={area} fill={`url(#${idArea})`} fillOpacity={desenho.interpolate({ inputRange: [0, 1], outputRange: [0, 1] })} />
                  <AnimatedPath testID="curva-linha" d={linha} fill="none" stroke={cor} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={`${comprimento} ${comprimento}`} strokeDashoffset={desenho.interpolate({ inputRange: [0, 1], outputRange: [comprimento, 0] })} />
                </Svg>
                {hoje && (
                  <Animated.View testID="ponto-hoje" style={{ position: "absolute", left: hoje.cx - RAIO_PONTO, top: hoje.cy - RAIO_PONTO, width: 2 * RAIO_PONTO, height: 2 * RAIO_PONTO, opacity: desenho.interpolate({ inputRange: [0.85, 1], outputRange: [0, 1], extrapolate: "clamp" }) }}>
                    {!reduzir && <Animated.View testID="anel-pulso" style={[ponto, { left: 0, top: 0, opacity: pulso.interpolate({ inputRange: [0, 1], outputRange: [0.5, 0] }), transform: [{ scale: pulso.interpolate({ inputRange: [0, 1], outputRange: [1, 3.2] }) }] }]} />}
                    <View testID="ponto-hoje-centro" style={[ponto, { left: 0, top: 0 }]} />
                  </Animated.View>
                )}
                {bolha && (
                  <>
                    <View pointerEvents="none" testID="scrub-linha" style={{ position: "absolute", left: bolha.cx - 0.5, top: TOPO, width: 1, height: ALTURA - TOPO - BASE, backgroundColor: cores.inkFaint }} />
                    <View pointerEvents="none" testID="scrub-ponto" style={[ponto, { left: bolha.cx - RAIO_PONTO, top: bolha.cy - RAIO_PONTO }]} />
                    <View pointerEvents="none" testID="curva-bolha" style={{ position: "absolute", width: bolha.w, left: Math.min(Math.max(bolha.cx - bolha.w / 2, 0), Math.max(0, largura - bolha.w)), ...(bolha.lado === "acima" ? { bottom: ALTURA - bolha.cy + FOLGA_BOLHA } : { top: bolha.cy + FOLGA_BOLHA }), paddingHorizontal: 6, paddingVertical: 3, borderRadius: raio.chip, borderWidth: 1, borderColor: cores.border, backgroundColor: cores.surfaceRaised, shadowColor: cores.shadow, shadowOpacity: 0.15, shadowRadius: 4, shadowOffset: { width: 0, height: 2 }, elevation: 3 }}>
                      <Texto variante="legenda" tom="inkMuted" numerico numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.7}>{bolha.dia} · <Texto variante="legenda" numerico style={{ fontFamily: escala.secao.fontFamily }}>{bolha.valor}</Texto></Texto>
                      {bolha.anterior && <Texto variante="legenda" tom="inkMuted" numerico numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.7}>{bolha.anterior}</Texto>}
                    </View>
                  </>
                )}
                <View testID="curva-scrub" {...gesto} style={StyleSheet.absoluteFill} />
              </>
            )}
          </View>
          <View style={{ height: 16 }}>
            {largura > 0 && dias.map((n) => <Texto key={n} testID="curva-rotulo-x" variante="legenda" tom="inkFaint" style={{ position: "absolute", left: x(n - 1) - 12, width: 24, textAlign: "center" }}>{n}</Texto>)}
          </View>
        </View>
      </View>
      <View style={{ flexDirection: "row", justifyContent: "center", gap: 16 }}>
        {legenda.map((l) => (
          <View key={l.rotulo} testID="curva-legenda" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <View style={{ flexDirection: "row", gap: 2 }}>{(l.tracejado ? [0, 1, 2] : [0]).map((i) => <View key={i} testID={l.tracejado ? "amostra-tracejada" : "amostra-solida"} style={{ width: l.tracejado ? 4 : 16, height: 2, backgroundColor: l.tracejado ? cores.inkFaint : cor }} />)}</View>
            <Texto variante="legenda" tom="inkMuted">{l.rotulo}</Texto>
          </View>
        ))}
      </View>
    </View>
  );
}
