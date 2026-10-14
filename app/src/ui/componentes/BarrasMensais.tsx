import { useEffect, useRef } from "react";
import { Animated, View } from "react-native";

import { facilitador, useReduzirMovimento } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { raio, texto as escala } from "@/ui/tokens";

import { Texto } from "./Texto";

/** `apagado`: mês antes do começo do histórico do usuário (rótulo e trilho esmaecidos; o valor é só contexto). */
export interface BarraMensal { chave: string; rotulo: string; valor: number; atual: boolean; apagado?: boolean }
export interface BarrasMensaisProps {
  /** Do mais antigo (esquerda) ao mais novo (direita); `atual` = mês em andamento (rosa cheio, valor escrito, rótulo com "*"). */
  meses: BarraMensal[];
  /** Texto compacto do valor, escrito só em cima da barra do mês atual. */
  rotuloValor: (valor: number) => string;
}

// Colunas flex:1 em pai que encolhe ao conteúdo (fala do Piggy no chat) ficariam espremidas: piso por coluna, cabe o "R$ 12,3k".
const ALTURA = 120, COLUNA_MIN = 40, LARGURA = 26, MINIMA = 4, CRESCER_MS = 450, OPACIDADE_OUTROS = 0.5, OPACIDADE_APAGADO = 0.4;

/** Altura em pt: proporcional ao MÁXIMO entre os meses, mínimo de 4 para valor > 0; valor <= 0 = só o trilho. */
export const alturaBarra = (valor: number, maximo: number, altura: number) => valor > 0 && maximo > 0 ? Math.max(MINIMA, Math.round(valor / maximo * altura)) : 0;

/**
 * Barras verticais de 26pt sobre um trilho neutro, topo arredondado (raio 4); o mês atual em rosa da marca cheio e os outros a 50%.
 * O rosa cheio da barra dá 3,04:1 (claro) e 5,17:1 (escuro) sobre o trilho; a 50% os outros meses ficam em ~2:1 e é de propósito: são contexto, a
 * barra é oculta ao VoiceOver e o único dado da tela (o mês atual) é o cheio. Opacidade não chega a 3:1 no claro nem a 100% (o teto é o 3,04).
 * O rótulo do mês atual usa `brandInk` (texto); `brand` é só para o desenho.
 * Crescem de baixo para cima em 450ms ao montar (transform nativo; com "reduzir movimento" o hook começa em false e resolve assíncrono: o 1º quadro pode animar e termina parado em 1, limite aceito).
 * O desenho some do VoiceOver (os números exatos estão na lista abaixo); a legenda do mês em andamento não, para o valor parcial ser anunciado.
 */
export function BarrasMensais({ meses, rotuloValor }: BarrasMensaisProps) {
  const { cores } = useTema();
  const reduzir = useReduzirMovimento();
  const crescer = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reduzir) { crescer.setValue(1); return; }
    crescer.setValue(0);
    const a = Animated.timing(crescer, { toValue: 1, duration: CRESCER_MS, easing: facilitador, useNativeDriver: true });
    a.start();
    return () => a.stop();
  }, [reduzir, crescer]);

  if (!meses.length) return null;
  const maximo = Math.max(...meses.map((m) => m.valor));
  return (
    <View testID="barras-mensais" style={{ gap: 8, minWidth: meses.length * COLUNA_MIN }}>
      <View testID="barras-mensais-desenho" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ flexDirection: "row" }}>
        {meses.map((m) => {
          const h = alturaBarra(m.valor, maximo, ALTURA);
          return (
            <View key={m.chave} style={{ flex: 1, alignItems: "center", gap: 4 }}>
              <View style={{ height: 16, alignSelf: "stretch", justifyContent: "center" }}>
                {m.atual && <Texto testID="barra-valor" variante="legenda" numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.7} style={{ alignSelf: "stretch", textAlign: "center" }}>{rotuloValor(m.valor)}</Texto>}
              </View>
              <View testID="barra-trilho" style={{ width: LARGURA, height: ALTURA, justifyContent: "flex-end", overflow: "hidden", borderRadius: raio.barra, backgroundColor: cores.surfaceControl, opacity: m.apagado ? OPACIDADE_APAGADO : 1 }}>
                {h > 0 && <Animated.View testID="barra" style={{ height: h, borderTopLeftRadius: raio.barra, borderTopRightRadius: raio.barra, backgroundColor: cores.brand, opacity: m.atual ? 1 : OPACIDADE_OUTROS, transform: [{ translateY: crescer.interpolate({ inputRange: [0, 1], outputRange: [h / 2, 0] }) }, { scaleY: crescer }] }} />}
              </View>
              <Texto testID="barra-mes" variante="legenda" tom={m.atual ? "brandInk" : m.apagado ? "inkFaint" : "inkMuted"} style={m.atual ? { alignSelf: "stretch", textAlign: "center", fontFamily: escala.secao.fontFamily } : { alignSelf: "stretch", textAlign: "center" }}>{`${m.rotulo}${m.atual ? "*" : ""}`}</Texto>
            </View>
          );
        })}
      </View>
      {meses.some((m) => m.atual) && <Texto variante="legenda" tom="inkMuted" accessibilityLabel="Mês atual em andamento: o valor ainda é parcial" style={{ textAlign: "center" }}>* mês em andamento</Texto>}
    </View>
  );
}
