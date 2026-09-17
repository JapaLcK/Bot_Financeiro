import type { ReactNode } from "react";
import { RefreshControl, ScrollView, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

/**
 * `onAtualizar`/`atualizando` só existem junto de rolagem: sem `ScrollView`
 * não há gesto de puxar para atualizar, então passá-los com `rolar={false}`
 * antes era aceito pelo tipo e descartado em silêncio — união discriminada
 * torna isso erro de compilação, não comportamento surpresa em runtime.
 */
type Props =
  | {
      children: ReactNode;
      rolar?: true;
      onAtualizar?: () => void;
      atualizando?: boolean;
    }
  | {
      children: ReactNode;
      /** Quando falso, a tela não rola (formulário curto que já cabe). */
      rolar: false;
    };

/**
 * Casca de tela: fundo do tema + área segura nos QUATRO lados (top/bottom
 * viram padding sempre; left/right somam ao respiro horizontal — paisagem
 * com notch lateral tem os dois diferentes de zero, e ignorá-los cortava
 * conteúdo sob a área segura).
 */
export function Screen(props: Props) {
  const { cores } = useTema();
  const insets = useSafeAreaInsets();
  const preenchimento = {
    paddingTop: insets.top,
    paddingBottom: insets.bottom,
    paddingLeft: espaco.lg + insets.left,
    paddingRight: espaco.lg + insets.right,
    flexGrow: 1,
  };

  if (props.rolar === false) {
    return (
      <View testID="tela" style={[{ flex: 1, backgroundColor: cores.bg }, preenchimento]}>
        {props.children}
      </View>
    );
  }

  const { children, onAtualizar, atualizando = false } = props;
  return (
    <ScrollView
      testID="tela"
      style={{ flex: 1, backgroundColor: cores.bg }}
      contentContainerStyle={preenchimento}
      // Hipótese conhecida da RN, só o simulador/aparelho prova: sem isto, o
      // primeiro toque num botão da tela com o teclado aberto só fecha o
      // teclado (o toque é "engolido"), precisando de um segundo toque.
      keyboardShouldPersistTaps="handled"
      refreshControl={
        onAtualizar ? (
          // `progressViewOffset`: o padding da área segura está no
          // `contentContainerStyle`, mas o `RefreshControl` se posiciona pelo
          // ScrollView INTEIRO — sem o deslocamento, o indicador aparece sob a
          // barra de status e a ilha. Só o aparelho confirma a aparência.
          <RefreshControl
            refreshing={atualizando}
            onRefresh={onAtualizar}
            tintColor={cores.brand}
            progressViewOffset={insets.top}
          />
        ) : undefined
      }
    >
      {children}
    </ScrollView>
  );
}
