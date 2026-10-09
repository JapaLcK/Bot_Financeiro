import { Children, type ReactNode } from "react";
import { ActivityIndicator, Animated, Pressable, StyleSheet, View } from "react-native";

import { usePressao } from "@/ui/motion";
import { useTema } from "@/ui/tema";
import { espaco, raio } from "@/ui/tokens";

import { Icone, type NomeIcone } from "./Icone";
import { Texto } from "./Texto";

interface Props {
  icone?: NomeIcone;
  titulo: string;
  subtitulo?: string;
  trailing?: ReactNode;
  chevron?: boolean;
  onPress?: () => void;
  /** `danger`: título em `danger` e sem chevron (ação destrutiva, ex.: Sair). */
  tom?: "danger";
  desativado?: boolean;
  /** Spinner no lugar do chevron; também desativa o toque. */
  carregando?: boolean;
}

/**
 * `role button` só existe com `onPress`: uma linha sem ação não é um
 * controle. `usePressao()` (listener de acessibilidade + 2 `Animated.Value`)
 * também só existe quando há `onPress` — extraído para este componente
 * interno em vez de chamado sempre: uma lista de 50 linhas sem ação nenhuma
 * não tem por que montar 100 `Animated.Value` e 50 assinaturas de
 * `reduceMotionChanged` à toa.
 */
function LinhaPressionavel({ onPress, inativo, carregando, children }: { onPress: () => void; inativo: boolean; carregando: boolean; children: ReactNode }) {
  const pressao = usePressao();
  return (
    <Pressable
      onPress={onPress}
      onPressIn={pressao.aoPressionar}
      onPressOut={pressao.aoSoltar}
      accessibilityRole="button"
      disabled={inativo || undefined}
      accessibilityState={inativo ? { disabled: true, busy: carregando } : undefined}
    >
      <Animated.View style={pressao.estilo}>{children}</Animated.View>
    </Pressable>
  );
}

export function ListRow({ icone, titulo, subtitulo, trailing, chevron = false, onPress, tom, desativado = false, carregando = false }: Props) {
  const { cores } = useTema();
  const inativo = desativado || carregando;
  const conteudo = (
    <View
      style={{
        flexDirection: "row",
        alignItems: "center",
        minHeight: 56,
        gap: espaco.md,
        paddingVertical: espaco.sm,
        ...(desativado ? { opacity: 0.45 } : null),
      }}
    >
      {icone ? <Icone nome={icone} tamanho={24} tom="inkMuted" /> : null}
      <View style={{ flex: 1 }}>
        <Texto variante="corpo" tom={tom === "danger" ? "danger" : "ink"}>
          {titulo}
        </Texto>
        {subtitulo ? (
          <Texto variante="legenda" tom="inkMuted">
            {subtitulo}
          </Texto>
        ) : null}
      </View>
      {trailing}
      {carregando ? <ActivityIndicator color={tom === "danger" ? cores.danger : cores.inkMuted} /> : chevron && tom !== "danger" ? <Icone nome="CaretRight" tamanho={20} tom="inkFaint" /> : null}
    </View>
  );

  if (!onPress) return conteudo;

  return <LinhaPressionavel onPress={onPress} inativo={inativo} carregando={carregando}>{conteudo}</LinhaPressionavel>;
}

/** Cartão agrupado estilo Ajustes do iOS: `surface`, raio e separador hairline entre as linhas. */
export function GrupoDeLinhas({ children }: { children: ReactNode }) {
  const { cores } = useTema();
  const linhas = Children.toArray(children);
  return (
    <View style={{ backgroundColor: cores.surface, borderRadius: raio.md, paddingHorizontal: espaco.lg }}>
      {linhas.map((linha, i) => (
        <View key={i}>
          {i > 0 ? <View testID="grupo-separador" style={{ height: StyleSheet.hairlineWidth, backgroundColor: cores.border }} /> : null}
          {linha}
        </View>
      ))}
    </View>
  );
}
