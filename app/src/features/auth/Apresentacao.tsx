import { useEffect, useRef } from "react";
import { Animated, Image, useWindowDimensions, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import Svg, { Defs, LinearGradient, Rect, Stop } from "react-native-svg";
import { useTema } from "@/ui/tema";
import { Button } from "@/ui/componentes/Button";
import { Texto } from "@/ui/componentes/Texto";
import { useReduzirMovimento } from "@/ui/motion";
import { espaco } from "@/ui/tokens";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const PIGGY = require("../../../assets/brand/piggy-3d.png");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const SIMBOLO = require("../../../assets/brand/simbolo.png");

/** Imagem local: não depende da rede nem participa da sessão. */
export function Apresentacao({ comecar, entrar }: { comecar: () => void; entrar: () => void }) {
  const insets = useSafeAreaInsets();
  const { cores } = useTema();
  const { height } = useWindowDimensions();
  const reduzir = useReduzirMovimento();
  const entrada = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    if (reduzir) { entrada.setValue(1); return; }
    entrada.setValue(0);
    const animacao = Animated.timing(entrada, { toValue: 1, duration: 550, useNativeDriver: true });
    animacao.start();
    return () => animacao.stop();
  }, [entrada, reduzir]);
  return (
    <View style={{ flexGrow: 1, paddingTop: espaco.md, paddingBottom: espaco.xxl, gap: espaco.xl }}>
      <View pointerEvents="none" style={{ position: "absolute", top: -insets.top, left: -espaco.xl, right: -espaco.xl, height: height * 0.5 + insets.top }}>
        <Svg width="100%" height="100%" accessible={false}>
          <Defs><LinearGradient id="luz" x1="0%" y1="0%" x2="0%" y2="100%"><Stop offset="0" stopColor={cores.surfaceRaised} /><Stop offset="1" stopColor={cores.bg} /></LinearGradient></Defs>
          <Rect width="100%" height="100%" fill="url(#luz)" />
        </Svg>
      </View>
      <Animated.View style={{ opacity: entrada, transform: [{ translateY: entrada.interpolate({ inputRange: [0, 1], outputRange: [16, 0] }) }] }}>
        <Image source={PIGGY} accessible={false} resizeMode="contain" style={{ width: "100%", height: Math.max(220, Math.min(height * 0.44, 400)) }} />
      </Animated.View>
      <View style={{ gap: espaco.md, alignItems: "center" }}>
        <View style={{ flexDirection: "row", gap: espaco.sm, alignItems: "center" }}>
          <Image source={SIMBOLO} accessible={false} resizeMode="contain" style={{ width: 34, height: 37 }} />
          <Texto variante="display" accessibilityRole="header">PigBank</Texto>
        </View>
        <Texto tom="inkMuted" style={{ textAlign: "center" }}>Sua grana. Tudo mais claro.</Texto>
      </View>
      <View style={{ gap: espaco.sm, width: "100%", maxWidth: 300, alignSelf: "center", paddingTop: espaco.sm }}>
        <Button rotulo="Começar" tamanho="L" onPress={comecar} />
        <Button rotulo="Já tenho conta" variante="ghost" onPress={entrar} />
      </View>
      <Texto variante="legenda" tom="inkMuted" style={{ textAlign: "center" }}>Seu próximo passo começa aqui.</Texto>
    </View>
  );
}
