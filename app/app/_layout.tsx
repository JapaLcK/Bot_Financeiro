import { useFonts } from "expo-font";
import { Stack } from "expo-router";
import { useEffect } from "react";
import { useColorScheme, View } from "react-native";

import { rastrear } from "@/services/analytics";
import { TemaProvider } from "@/ui/tema";
import { claro, escuro } from "@/ui/tokens";

// O Sentry e o analytics NÃO sobem aqui: sobem em `index.ts`, antes do import
// do roteador. Aqui já seria tarde — os imports deste arquivo são avaliados
// antes do corpo dele, e uma exceção em qualquer um deles aconteceria com o
// Sentry ainda desinstalado.

export default function Layout() {
  // Segue o sistema. O site hoje força escuro por localStorage e ignora a
  // preferência do aparelho; o app não repete isso. Usado só para pintar o
  // fundo ANTES da fonte carregar — depois disso quem decide o tema é o
  // `TemaProvider`.
  const esquema = useColorScheme();
  const paleta = esquema === "dark" ? escuro : claro;

  // `require()`, não asset remoto: as quatro TTFs vêm do woff2 de
  // `frontend/fonts/` (tests/test_app_espelhos.py garante a paridade). Sem o
  // plugin de config do expo-font — este é o caminho assíncrono de propósito,
  // é ele que justifica o estado "carregando" abaixo.
  const [fontesCarregadas, erroFontes] = useFonts({
    "Inter-Regular": require("../assets/fonts/Inter-Regular.ttf"),
    "Inter-Medium": require("../assets/fonts/Inter-Medium.ttf"),
    "Inter-SemiBold": require("../assets/fonts/Inter-SemiBold.ttf"),
    "Inter-Bold": require("../assets/fonts/Inter-Bold.ttf"),
  });

  // O evento fica no efeito de propósito: ele marca que a tela apareceu, e
  // disparar antes do primeiro render contaria abertura que não aconteceu.
  useEffect(() => {
    rastrear("app.aberto");
  }, []);

  // Erro de fonte não trava o app: segue com a fonte do sistema. `null`
  // enquanto carrega piscaria branco puro no tema escuro — por isso a View na
  // cor do fundo.
  if (!fontesCarregadas && !erroFontes) {
    return <View style={{ flex: 1, backgroundColor: paleta.bg }} />;
  }

  return (
    <TemaProvider>
      <Stack
        screenOptions={{
          headerShown: false,
          contentStyle: { backgroundColor: paleta.bg },
        }}
      >
        {/* Catálogo interno do design system: só existe em build de dev. */}
        <Stack.Protected guard={__DEV__}>
          <Stack.Screen name="_ds" />
        </Stack.Protected>
      </Stack>
    </TemaProvider>
  );
}
