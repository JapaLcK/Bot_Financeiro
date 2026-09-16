import { Stack } from "expo-router";
import { useEffect } from "react";
import { useColorScheme } from "react-native";

import { rastrear } from "@/services/analytics";
import { claro, escuro } from "@/ui/tokens";

// O Sentry e o analytics NÃO sobem aqui: sobem em `index.ts`, antes do import
// do roteador. Aqui já seria tarde — os imports deste arquivo são avaliados
// antes do corpo dele, e uma exceção em qualquer um deles aconteceria com o
// Sentry ainda desinstalado.

export default function Layout() {
  // Segue o sistema. O site hoje força escuro por localStorage e ignora a
  // preferência do aparelho; o app não repete isso.
  const esquema = useColorScheme();
  const paleta = esquema === "dark" ? escuro : claro;

  // O evento fica no efeito de propósito: ele marca que a tela apareceu, e
  // disparar antes do primeiro render contaria abertura que não aconteceu.
  useEffect(() => {
    rastrear("app.aberto");
  }, []);

  return (
    <Stack
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: paleta.bg },
      }}
    />
  );
}
