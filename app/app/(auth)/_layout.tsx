import { Stack } from "expo-router";

import { OPCOES_SHEET } from "@/ui/componentes/Sheet";
import { useTema } from "@/ui/tema";

/**
 * Existe por causa da sheet: "Esqueci a senha" precisa de
 * `presentation: "formSheet"` (decisão da Fase 2), e isso só se declara na
 * pilha que é PAI da rota — mesmo padrão de `app/_ds/_layout.tsx`.
 *
 * `boas-vindas` vem DECLARADA PRIMEIRO, e é a rota padrão sem sessão: um
 * `Stack.Screen` explícito muda a ordem de resolução do grupo, e deixar só
 * `esqueci-senha` explícito (como `_ds/_layout.tsx` faz com `sheet-exemplo`)
 * faz o `Stack.Protected` do `_layout.tsx` raiz cair na SHEET como rota
 * padrão do grupo quando `/(auth)` vira o único ramo disponível — medido com
 * `renderRouter`.
 */
/**
 * Deep link frio para `/esqueci-senha` (sem sessão) montava a pilha só com a
 * sheet — `canGoBack()` falso, sem base embaixo dela. `initialRouteName`
 * manda o expo-router inserir `boas-vindas` como base da pilha do grupo ANTES
 * de empurrar a rota pedida (`/entrar`, `/criar-conta`, a sheet), mesmo
 * quando ela não é a primeira da URL. Não `index`: colidiria com
 * `(app)/index` em `/`.
 */
export const unstable_settings = { initialRouteName: "boas-vindas" };

export default function LayoutAuth() {
  // Sem `contentStyle`, a tela fica com o fundo padrão do react-navigation
  // (cinza claro) — e ele aparece no espaço que o `KeyboardAvoidingView` abre
  // para o teclado, até no tema escuro.
  const { cores } = useTema();
  // Entrar e Criar conta: só a seta de voltar para a Boas-vindas, sem título
  // (mesmo cabeçalho do `(app)/_layout.tsx`).
  const soASeta = {
    headerShown: true,
    title: "",
    headerBackButtonDisplayMode: "minimal",
    headerShadowVisible: false,
    headerStyle: { backgroundColor: cores.bg },
    headerTintColor: cores.ink,
  } as const;
  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: cores.bg } }}>
      <Stack.Screen name="boas-vindas" />
      <Stack.Screen name="entrar" options={soASeta} />
      <Stack.Screen name="criar-conta" options={soASeta} />
      <Stack.Screen name="esqueci-senha" options={OPCOES_SHEET} />
    </Stack>
  );
}
