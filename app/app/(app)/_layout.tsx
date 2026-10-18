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

/** Os códigos de backup não cabem em meia tela: sheet só inteira, e sem alça (não há o que arrastar). */
const SHEET_INTEIRA: NativeStackNavigationOptions = { ...OPCOES_SHEET, sheetAllowedDetents: [1], sheetGrabberVisible: false };

export default function LayoutApp() {
  const { cores } = useTema();
  const CABECALHO: NativeStackNavigationOptions = {
    headerShown: true,
    headerBackButtonDisplayMode: "minimal",
    headerShadowVisible: false,
    headerStyle: { backgroundColor: cores.bg },
    headerTintColor: cores.ink,
    headerTitleStyle: { fontFamily: texto.rotulo.fontFamily },
  };
  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: cores.bg } }}>
      <Stack.Screen name="index" />
      <Stack.Screen name="(painel)" />
      <Stack.Screen name="conectar-banco" />
      <Stack.Screen name="autorizando" />
      <Stack.Screen name="open-finance-volta" />
      <Stack.Screen name="configuracoes" options={{ headerShown: true, title: "Configurações", headerBackButtonDisplayMode: "minimal" }} />
      <Stack.Screen name="conexoes" options={{ headerShown: true, title: "Bancos conectados", headerBackButtonDisplayMode: "minimal" }} />
      <Stack.Screen name="teste-pluggy" options={{ headerShown: true, title: "Teste Open Finance", headerBackButtonDisplayMode: "minimal" }} />
      <Stack.Screen name="seguranca" options={{ ...CABECALHO, title: "Segurança" }} />
      <Stack.Screen name="perfil" options={{ ...CABECALHO, title: "Perfil" }} />
      {/* Folha de meta (criar, editar, apagar): a altura do conteúdo, só aqui; `OPCOES_SHEET` e `SHEET_INTEIRA` seguem como estão. */}
      <Stack.Screen name="meta" options={{ ...OPCOES_SHEET, sheetAllowedDetents: "fitToContents" }} />
      <Stack.Screen name="mfa-ativar" options={SHEET_INTEIRA} />
      <Stack.Screen name="mfa-novos-codigos" options={SHEET_INTEIRA} />
      <Stack.Screen name="mfa-desativar" options={SHEET_INTEIRA} />
      <Stack.Screen name="excluir-conta" options={SHEET_INTEIRA} />
      <Stack.Screen name="exportar-dados" options={SHEET_INTEIRA} />
    </Stack>
  );
}
