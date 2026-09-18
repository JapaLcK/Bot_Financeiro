import { Stack } from "expo-router";

import { OPCOES_SHEET } from "@/ui/componentes/Sheet";

/**
 * Existe por causa da sheet: "Esqueci a senha" precisa de
 * `presentation: "formSheet"` (decisão da Fase 2), e isso só se declara na
 * pilha que é PAI da rota — mesmo padrão de `app/_ds/_layout.tsx`.
 *
 * `entrar` vem DECLARADO PRIMEIRO, mesmo sem opções próprias: um
 * `Stack.Screen` explícito muda a ordem de resolução do grupo, e deixar só
 * `esqueci-senha` explícito (como `_ds/_layout.tsx` faz com `sheet-exemplo`)
 * faz o `Stack.Protected` do `_layout.tsx` raiz cair na SHEET como rota
 * padrão do grupo quando `/(auth)` vira o único ramo disponível — medido com
 * `renderRouter`.
 */
export default function LayoutAuth() {
  return (
    <Stack screenOptions={{ headerShown: false }}>
      <Stack.Screen name="entrar" />
      <Stack.Screen name="esqueci-senha" options={OPCOES_SHEET} />
    </Stack>
  );
}
