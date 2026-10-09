import type { NativeStackNavigationOptions } from "expo-router";
import { useRef, type ReactNode, type RefObject } from "react";
import { ScrollView, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useRolarAteCampoFocado } from "@/ui/componentes/Screen";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

/**
 * Decisão 1 do dono: Sheet NATIVO do expo-router (via `react-native-screens`
 * 4.26, já instalado) — sem `@gorhom/bottom-sheet`, reanimated,
 * gesture-handler nem worklets. Nomes conferidos em
 * `node_modules/react-native-screens/src/types.tsx` (não confiar em nomes
 * de memória — esta lista já mudou de versão para versão da lib).
 *
 * Uso: na rota PAI que declara a pilha (aqui, `app/_ds/_layout.tsx`),
 * `<Stack.Screen name="minha-rota" options={OPCOES_SHEET} />`. A rota em si
 * usa `SheetConteudo` (abaixo) como casca.
 *
 * Alça e raio são do SISTEMA (`sheetGrabberVisible`, `@platform ios`): a alça
 * desenhada à mão nunca bateu com a nativa e ficava dupla. Sheet inteira
 * (`SHEET_INTEIRA`) a desliga.
 */
export const OPCOES_SHEET: NativeStackNavigationOptions = {
  presentation: "formSheet",
  sheetAllowedDetents: [0.5, 1],
  sheetGrabberVisible: true,
};

interface Props {
  children: ReactNode;
  /**
   * Conteúdo que pode passar da altura da sheet. O `ScrollView` tem de ser a
   * RAIZ da casca: no `formSheet` do iOS o `react-native-screens` acha o
   * primeiro `ScrollView` descendente e impõe a ele o frame da tela inteira
   * (`RNSScreen.mm`, `applyFrameCorrectionForDescendantScrollView`) — aninhado
   * dentro do padding, ele ia para (0,0) e o conteúdo colava na borda e cobria
   * a alça. Na raiz, o padding fica no `contentContainerStyle` e sobrevive.
   *
   * Teclado (iOS): `automaticallyAdjustKeyboardInsets` põe o teclado como
   * inset inferior e rola até o campo focado (`RCTScrollViewComponentView.mm`,
   * `_keyboardWillChangeFrame`). Sem ela, na sheet inteira o teclado cobria o
   * campo do código do MFA. No Android a prop não existe; lá é o
   * `windowSoftInputMode` (padrão `resize` do Expo).
   *
   * Ela rola só até o CURSOR (`RCTTextInputComponentView.mm`,
   * `reactUpdateResponderOffsetForScrollView`), e o botão que vem depois do
   * campo ficava atrás do teclado. Por isso, no `keyboardDidShow` (o inset do
   * teclado já aplicado), a casca pede rolagem até o rótulo do campo focado: o
   * `scrollTo` nativo corta no fim do conteúdo (`RCTScrollViewComponentView.mm`,
   * `scrollTo:y:animated:`), então o resultado é "até o fim, mas nunca
   * passando do campo" — botão visível quando cabe, campo visível sempre.
   * Só no iOS: no Android o `resize` já encolhe a tela para o teclado, e esse
   * caminho nunca foi verificado num aparelho Android.
   */
  rolar?: boolean;
}

/**
 * Casca de CONTEÚDO da sheet: respiro de topo (a alça é do sistema), área
 * segura inferior e respiro lateral. Usada DENTRO da rota aberta com `OPCOES_SHEET`.
 */
export function SheetConteudo({ children, rolar = false }: Props) {
  const { cores, acesso } = useTema();
  const insets = useSafeAreaInsets();
  const rolagem = useRef<ScrollView>(null);
  const conteudo = useRef<View>(null);

  useRolarAteCampoFocado(rolar, rolagem, conteudo);

  const preenchimento = {
    paddingHorizontal: acesso ? espaco.xl : espaco.lg,
    paddingTop: espaco.xl,
    paddingBottom: insets.bottom + espaco.lg,
  };
  if (rolar) {
    return (
      <ScrollView
        ref={rolagem}
        // O .d.ts do RN tipa sem o `| null` que o `useRef` do React 19 devolve.
        innerViewRef={conteudo as RefObject<View>}
        testID="sheet-conteudo"
        style={{ flex: 1, backgroundColor: cores.bg }}
        contentContainerStyle={preenchimento}
        keyboardShouldPersistTaps="handled"
        automaticallyAdjustKeyboardInsets
      >
        {children}
      </ScrollView>
    );
  }

  return (
    <View testID="sheet-conteudo" style={{ flex: 1, backgroundColor: cores.bg, ...preenchimento }}>
      {children}
    </View>
  );
}
