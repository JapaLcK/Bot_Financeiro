import { TextInput, View, type TextInputProps } from "react-native";

import { useAvisoAoErrar } from "@/ui/haptics";
import { useTema } from "@/ui/tema";
import { espaco, raio, texto as escalas } from "@/ui/tokens";

import { Texto } from "./Texto";

interface Props
  extends Omit<TextInputProps, "editable" | "style" | "maxFontSizeMultiplier" | "accessibilityLabel"> {
  rotulo: string;
  erro?: string;
  desativado?: boolean;
}

/**
 * Campo de texto GENÉRICO (contorno + rótulo + erro). Não compõe sobre
 * `AmountInput`, nem o contrário: `AmountInput` tem casca própria — prefixo
 * "R$", cursor preso no fim, sem contorno visível (decisão do dono, PR B) —
 * e as poucas linhas em comum (rótulo, `Texto` de erro, `useAvisoAoErrar`)
 * não justificam uma extração hoje (dois usos, nenhum ganho real ainda;
 * CLAUDE.md §0.1).
 *
 * `accessibilityLabel` sai do tipo (`Omit`) em vez de ser aceito e
 * sobrescrito em silêncio: o rótulo lido pelo leitor de tela é sempre
 * `rotulo` + erro, porque é o par que os dois têm que concordar (o texto
 * visível e o anunciado). Um `accessibilityLabel` custom do chamador
 * quebraria justamente essa concordância sem avisar ninguém.
 */
export function Input({ rotulo, erro, desativado = false, ...resto }: Props) {
  const { cores } = useTema();
  useAvisoAoErrar(!!erro);

  return (
    <View>
      <Texto variante="rotulo" tom={desativado ? "inkMuted" : "ink"}>
        {rotulo}
      </Texto>
      <TextInput
        {...resto}
        editable={!desativado}
        maxFontSizeMultiplier={1.3}
        accessibilityLabel={`${rotulo}${erro ? `, erro: ${erro}` : ""}`}
        accessibilityState={{ disabled: desativado }}
        style={[
          escalas.corpo,
          {
            // Desativado usa tokens diferentes (não opacity): texto e erro
            // continuam sendo tons semânticos distintos (`inkMuted`/`danger`)
            // em vez de uma opacidade que escureceria os dois por igual — e
            // `inkFaint` no contorno já é o token usado para decorativo/
            // desativado em `Icone`/`ListRow` (tokens.ts), então o campo
            // desativado passa a se distinguir do habilitado de verdade, não
            // só pelo rótulo acima dele.
            color: desativado ? cores.inkMuted : cores.ink,
            borderWidth: 1,
            borderColor: desativado ? cores.inkFaint : erro ? cores.danger : cores.inkMuted,
            borderRadius: raio.md,
            paddingHorizontal: espaco.lg,
            paddingVertical: espaco.md,
            minHeight: 44,
          },
        ]}
      />
      {erro ? (
        <Texto variante="legenda" tom="danger">
          {erro}
        </Texto>
      ) : null}
    </View>
  );
}
