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
/**
 * Deep link frio para `/esqueci-senha` (sem sessão) montava a pilha só com a
 * sheet — `canGoBack()` falso, sem `entrar` embaixo dela. `initialRouteName`
 * manda o expo-router inserir `entrar` como base da pilha do grupo ANTES de
 * empurrar a rota pedida, mesmo quando ela não é a primeira da URL.
 */
export const unstable_settings = { initialRouteName: "entrar" };

export default function LayoutAuth() {
  return (
    <Stack screenOptions={{ headerShown: false }}>
      <Stack.Screen name="entrar" />
      <Stack.Screen name="criar-conta" />
      <Stack.Screen name="esqueci-senha" options={OPCOES_SHEET} />
    </Stack>
  );
}
