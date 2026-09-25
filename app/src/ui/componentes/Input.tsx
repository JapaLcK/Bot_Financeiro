import { useEffect, type Ref } from "react";
import { AccessibilityInfo, TextInput, View, type TextInputProps } from "react-native";

import { useAvisoAoErrar } from "@/ui/haptics";
import { useTema } from "@/ui/tema";
import { espaco, raio, texto as escalas } from "@/ui/tokens";

import { Icone, type NomeIcone } from "./Icone";
import { Texto } from "./Texto";

interface Props
  extends Omit<TextInputProps, "editable" | "style" | "maxFontSizeMultiplier" | "accessibilityLabel"> {
  rotulo: string;
  erro?: string;
  desativado?: boolean;
  /** Ícone à esquerda, dentro do contorno do campo (E-mail/Senha do login). Sem ele, o campo é IDÊNTICO ao de antes — snapshot preservado. */
  icone?: NomeIcone;
  /** Vai direto ao `TextInput` (React 19: `ref` é prop comum), para quem precisa devolver o foco. */
  ref?: Ref<TextInput>;
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
export function Input({ rotulo, erro, desativado = false, icone, ...resto }: Props) {
  const { cores } = useTema();
  useAvisoAoErrar(!!erro);
  // Quem não está com o dedo neste campo (ou usa leitor de tela sem foco
  // nele) não veria o erro sem isto — o texto some/aparece na árvore sem
  // nenhum aviso sonoro. Dispara a cada MUDANÇA de mensagem (não só na
  // transição de "sem erro" para "com erro"): um segundo erro diferente no
  // mesmo campo (ex.: 401 depois de outro 401) também precisa ser ouvido.
  useEffect(() => {
    if (erro) AccessibilityInfo.announceForAccessibility(erro);
  }, [erro]);

  // Desativado usa tokens diferentes (não opacity): texto e erro continuam
  // sendo tons semânticos distintos (`inkMuted`/`danger`) em vez de uma
  // opacidade que escureceria os dois por igual — e `inkFaint` no contorno já
  // é o token usado para decorativo/desativado em `Icone`/`ListRow`
  // (tokens.ts), então o campo desativado passa a se distinguir do habilitado
  // de verdade, não só pelo rótulo acima dele.
  const corBorda = desativado ? cores.inkFaint : erro ? cores.danger : cores.inkMuted;

  const campo = (
    <TextInput
      {...resto}
      editable={!desativado}
      maxFontSizeMultiplier={1.3}
      accessibilityLabel={`${rotulo}${erro ? `, erro: ${erro}` : ""}`}
      accessibilityState={{ disabled: desativado }}
      style={[
        escalas.corpo,
        icone
          ? { color: desativado ? cores.inkMuted : cores.ink, flex: 1, minHeight: 44 }
          : {
              color: desativado ? cores.inkMuted : cores.ink,
              borderWidth: 1,
              borderColor: corBorda,
              borderRadius: raio.md,
              paddingHorizontal: espaco.lg,
              paddingVertical: espaco.md,
              minHeight: 44,
            },
      ]}
    />
  );

  return (
    <View>
      <Texto variante="rotulo" tom={desativado ? "inkMuted" : "ink"}>
        {rotulo}
      </Texto>
      {icone ? (
        <View
          style={{
            flexDirection: "row",
            alignItems: "center",
            gap: espaco.sm,
            borderWidth: 1,
            borderColor: corBorda,
            borderRadius: raio.md,
            paddingHorizontal: espaco.lg,
          }}
        >
          <Icone nome={icone} tom="inkMuted" tamanho={20} />
          {campo}
        </View>
      ) : (
        campo
      )}
      {erro ? (
        <Texto variante="legenda" tom="danger">
          {erro}
        </Texto>
      ) : null}
    </View>
  );
}
