import { ActivityIndicator, Animated, Pressable, View } from "react-native";

import { usePressao } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { espaco, raio, type Paleta } from "@/ui/tokens";

import { Icone, type NomeIcone } from "./Icone";
import { Texto } from "./Texto";

type Variante = "primary" | "secondary" | "ghost" | "danger";
type Tamanho = "M" | "L";

interface Props {
  rotulo: string;
  variante?: Variante;
  tamanho?: Tamanho;
  carregando?: boolean;
  desativado?: boolean;
  onPress: () => void;
  /** Ícone à esquerda do rótulo (logo social do login, por exemplo). Sem ele, o botão é IDÊNTICO ao de antes. */
  icone?: NomeIcone;
}

const ALTURA: Record<Tamanho, number> = { M: 44, L: 52 };

/**
 * Rosa só onde há decisão (identidade pigbank-frontend): só `primary` usa
 * `acao`; `ghost` usa `brandInk` no TEXTO, nunca em fundo. `secondary` tem
 * contorno `inkMuted` (3:1 sobre `surface`/`bg`, já medido em `PARES`), nunca
 * `border` (decorativo, não é contorno de controle — CLAUDE.md/tokens.ts).
 */
const VISUAL: Record<Variante, { bg: keyof Paleta | "transparent"; tom: keyof Paleta; borda?: keyof Paleta }> = {
  primary: { bg: "acao", tom: "onAcao" },
  secondary: { bg: "surface", tom: "ink", borda: "inkMuted" },
  ghost: { bg: "transparent", tom: "brandInk" },
  danger: { bg: "danger", tom: "onDanger" },
};

export function Button({
  rotulo,
  variante = "primary",
  tamanho = "M",
  carregando = false,
  desativado = false,
  onPress,
  icone,
}: Props) {
  const { cores } = useTema();
  const pressao = usePressao();
  const bloqueado = desativado || carregando;
  const v = VISUAL[variante];

  return (
    <Pressable
      onPress={onPress}
      onPressIn={pressao.aoPressionar}
      onPressOut={pressao.aoSoltar}
      disabled={bloqueado}
      accessibilityRole="button"
      accessibilityState={{ disabled: desativado, busy: carregando }}
    >
      <Animated.View
        style={[
          {
            minHeight: ALTURA[tamanho],
            borderRadius: raio.md,
            paddingHorizontal: espaco.xl,
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: v.bg === "transparent" ? "transparent" : cores[v.bg],
            borderWidth: v.borda ? 1 : 0,
            borderColor: v.borda ? cores[v.borda] : undefined,
            opacity: desativado && !carregando ? 0.5 : 1,
          },
          pressao.estilo,
        ]}
      >
        {icone ? (
          // Só quando há ícone a árvore ganha uma `View` a mais — sem ele o
          // botão renderiza EXATAMENTE como antes (o teste de `carregando`
          // lê `opacity` direto no estilo do `Texto`, não de um invólucro).
          <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.sm, opacity: carregando ? 0 : 1 }}>
            <Icone nome={icone} tom={v.tom} tamanho={20} />
            <Texto variante="rotulo" tom={v.tom}>
              {rotulo}
            </Texto>
          </View>
        ) : (
          <Texto variante="rotulo" tom={v.tom} style={{ opacity: carregando ? 0 : 1 }}>
            {rotulo}
          </Texto>
        )}
        {/* Absoluto sobre o texto invisível: a largura do botão não pula ao trocar para o spinner. */}
        {carregando ? <ActivityIndicator color={cores[v.tom]} style={{ position: "absolute" }} /> : null}
      </Animated.View>
    </Pressable>
  );
}
