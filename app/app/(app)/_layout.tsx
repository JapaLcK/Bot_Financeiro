import { Stack, type NativeStackNavigationOptions } from "expo-router";

import { OPCOES_SHEET } from "@/ui/componentes/Sheet";
import { useTema } from "@/ui/tema";
import { texto } from "@/ui/tokens";

/**
 * `index` vem DECLARADO PRIMEIRO, e é a base da pilha: mesma armadilha
 * documentada em `app/(auth)/_layout.tsx` — só as rotas com opções próprias
 * declaradas fariam a rota padrão do grupo cair numa delas, e um deep link
 * frio para `/seguranca` montaria a pilha sem o Início embaixo.
 */
export const unstable_settings = { initialRouteName: "index" };

/** Os códigos de backup não cabem em meia tela: sheet só inteira. */
const SHEET_INTEIRA: NativeStackNavigationOptions = { ...OPCOES_SHEET, sheetAllowedDetents: [1] };

export default function LayoutApp() {
  const { cores } = useTema();
  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: cores.bg } }}>
      <Stack.Screen name="index" />
      <Stack.Screen
        name="seguranca"
        options={{
          headerShown: true,
          title: "Segurança",
          headerBackButtonDisplayMode: "minimal",
          headerShadowVisible: false,
          headerStyle: { backgroundColor: cores.bg },
          headerTintColor: cores.ink,
          headerTitleStyle: { fontFamily: texto.rotulo.fontFamily },
        }}
      />
      <Stack.Screen name="mfa-ativar" options={SHEET_INTEIRA} />
      <Stack.Screen name="mfa-novos-codigos" options={SHEET_INTEIRA} />
      <Stack.Screen name="mfa-desativar" options={SHEET_INTEIRA} />
    </Stack>
  );
}
