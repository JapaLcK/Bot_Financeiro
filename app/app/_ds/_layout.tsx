import { Redirect, Stack } from "expo-router";

import { OPCOES_SHEET } from "@/ui/componentes/Sheet";

/**
 * Segunda guarda, redundante com o `Stack.Protected` do `_layout.tsx` raiz de
 * propósito: um `_` no nome da pasta NÃO esconde a rota no expo-router (só
 * `_layout`/`+html`/`+native-intent`/`+api` são especiais — ver matchers.js).
 * Sem as duas, bastaria um `expo-router` sem o guard pai — ou alguém navegando
 * direto pelo deep link — para abrir o catálogo em produção.
 */
export default function LayoutDs() {
  if (!__DEV__) return <Redirect href="/" />;
  return (
    <Stack screenOptions={{ headerShown: false }}>
      {/* Única rota do catálogo com opções PRÓPRIAS: as outras (index,
          inicio, lista, formulario) seguem o default do Stack acima. */}
      <Stack.Screen name="sheet-exemplo" options={OPCOES_SHEET} />
    </Stack>
  );
}
