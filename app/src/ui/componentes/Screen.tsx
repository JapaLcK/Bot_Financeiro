import { useEffect, useRef, type ReactNode, type RefObject } from "react";
// eslint-disable-next-line no-restricted-imports -- só o `TextInput.State` (quem tem o foco); nada de texto é renderizado aqui.
import { Keyboard, Platform, RefreshControl, ScrollView, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

/**
 * `onAtualizar`/`atualizando` só existem junto de rolagem: sem `ScrollView`
 * não há gesto de puxar para atualizar, então passá-los com `rolar={false}`
 * antes era aceito pelo tipo e descartado em silêncio — união discriminada
 * torna isso erro de compilação, não comportamento surpresa em runtime.
 */
type Props = {
  /**
   * Tela sob cabeçalho nativo (`headerShown: true`): o cabeçalho já ocupa a
   * área segura de cima, então somar `insets.top` de novo dobrava a margem.
   */
  sobCabecalho?: boolean;
} & (
  | {
      children: ReactNode;
      rolar?: true;
      onAtualizar?: () => void;
      atualizando?: boolean;
      /**
       * Teclado como inset inferior + rolagem até o campo focado. `false` em
       * tela que já usa `KeyboardAvoidingView` (contaria o teclado duas vezes).
       */
      ajustarTeclado?: boolean;
    }
  | {
      children: ReactNode;
      /** Quando falso, a tela não rola (formulário curto que já cabe). */
      rolar: false;
    }
);

/**
 * `automaticallyAdjustKeyboardInsets` rola só até o CURSOR
 * (`RCTTextInputComponentView.mm`), e o botão depois do campo ficava atrás do
 * teclado. No `keyboardDidShow` (inset já aplicado) pede rolagem até o rótulo
 * do campo focado: o `scrollTo` nativo corta no fim do conteúdo, então é "até o
 * fim, mas nunca passando do campo". Só no iOS: no Android o `resize` do
 * sistema já encolhe a tela, caminho nunca verificado num aparelho Android.
 */
export function useRolarAteCampoFocado(ativo: boolean, rolagem: RefObject<ScrollView | null>, conteudo: RefObject<View | null>) {
  useEffect(() => {
    if (!ativo || Platform.OS !== "ios") return;
    const sub = Keyboard.addListener("keyboardDidShow", () => {
      const campo = TextInput.State.currentlyFocusedInput();
      if (!conteudo.current || !campo) return;
      // `xxxl` acima do TextInput: o rótulo do `Input` fica ali.
      campo.measureLayout(conteudo.current, (_x, y) => rolagem.current?.scrollTo({ y: y - espaco.xxxl }), () => undefined);
    });
    return () => sub.remove();
  }, [ativo, rolagem, conteudo]);
}

/**
 * Casca de tela: fundo do tema + área segura nos QUATRO lados (top/bottom
 * viram padding — o top não, com `sobCabecalho`; left/right somam ao respiro
 * horizontal — paisagem com notch lateral tem os dois diferentes de zero, e ignorá-los cortava
 * conteúdo sob a área segura).
 */
export function Screen(props: Props) {
  const { cores, acesso } = useTema();
  const insets = useSafeAreaInsets();
  const rolagem = useRef<ScrollView>(null);
  const conteudo = useRef<View>(null);
  const ajustar = props.rolar === false ? false : (props.ajustarTeclado ?? true);
  useRolarAteCampoFocado(ajustar, rolagem, conteudo);
  const topo = props.sobCabecalho ? 0 : insets.top;
  const preenchimento = {
    paddingTop: topo,
    paddingBottom: insets.bottom,
    paddingLeft: (acesso ? espaco.xl : espaco.lg) + insets.left,
    paddingRight: (acesso ? espaco.xl : espaco.lg) + insets.right,
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
    <View style={{ flex: 1, backgroundColor: cores.bg }}>
      <ScrollView
        ref={rolagem}
        // O .d.ts do RN tipa sem o `| null` que o `useRef` do React 19 devolve.
        innerViewRef={conteudo as RefObject<View>}
        testID="tela"
        style={{ flex: 1, backgroundColor: cores.bg }}
        contentContainerStyle={preenchimento}
        automaticallyAdjustKeyboardInsets={ajustar}
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
              // `tintColor` é iOS; no Android o RN descarta essa prop antes de
              // repassar ao nativo (RefreshControl.js, ramo do
              // AndroidSwipeRefreshLayout) e quem pinta é `colors`. Sem as duas,
              // um dos sistemas cai no indicador padrão da plataforma.
              tintColor={cores.brand}
              colors={[cores.brand]}
              progressViewOffset={topo}
            />
          ) : undefined
        }
      >
        {children}
      </ScrollView>
      {/* Faixa fixa atrás da barra de status: sem ela o conteúdo rolado aparece sob o relógio. */}
      {topo > 0 ? (
        <View
          testID="tela-topo"
          pointerEvents="none"
          style={{ position: "absolute", top: 0, left: 0, right: 0, height: topo, backgroundColor: cores.bg }}
        />
      ) : null}
    </View>
  );
}
