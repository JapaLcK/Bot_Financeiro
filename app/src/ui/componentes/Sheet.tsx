import type { NativeStackNavigationOptions } from "expo-router";
import type { ReactNode } from "react";
import { View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useTema } from "@/ui/tema";
import { espaco, raio } from "@/ui/tokens";

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
 * `sheetGrabberVisible: false`: a alça é do CONTEÚDO, não do sistema — nos
 * dois SOs o visual fica igual, em vez de um traço nativo só no iOS
 * (`sheetGrabberVisible` é `@platform ios`) e nenhum no Android.
 */
export const OPCOES_SHEET: NativeStackNavigationOptions = {
  presentation: "formSheet",
  sheetAllowedDetents: [0.5, 1],
  sheetCornerRadius: raio.lg,
  sheetGrabberVisible: false,
};

interface Props {
  children: ReactNode;
}

/**
 * Casca de CONTEÚDO da sheet: alça própria (decorativa — `cores.border`,
 * nunca contorno de controle, escondida do leitor de tela), área segura
 * inferior e respiro lateral. Usada DENTRO da rota aberta com `OPCOES_SHEET`.
 */
export function SheetConteudo({ children }: Props) {
  const { cores } = useTema();
  const insets = useSafeAreaInsets();

  return (
    <View
      testID="sheet-conteudo"
      style={{
        flex: 1,
        backgroundColor: cores.bg,
        paddingHorizontal: espaco.lg,
        paddingBottom: insets.bottom + espaco.lg,
      }}
    >
      <View
        importantForAccessibility="no-hide-descendants"
        accessibilityElementsHidden
        style={{
          alignSelf: "center",
          width: 36,
          height: 4,
          borderRadius: raio.sm,
          backgroundColor: cores.border,
          marginTop: espaco.sm,
          marginBottom: espaco.lg,
        }}
      />
      {children}
    </View>
  );
}
