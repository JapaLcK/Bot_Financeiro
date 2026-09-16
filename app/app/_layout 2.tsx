import { Stack } from "expo-router";
import { useEffect } from "react";
import { useColorScheme } from "react-native";

import { iniciarAnalytics, rastrear } from "@/services/analytics";
import { iniciarLogging } from "@/services/logging";
import { claro, escuro } from "@/ui/tokens";

export default function Layout() {
  // Segue o sistema. O site hoje força escuro por localStorage e ignora a
  // preferência do aparelho; o app não repete isso.
  const esquema = useColorScheme();
  const paleta = esquema === "dark" ? escuro : claro;

  useEffect(() => {
    iniciarLogging();
    iniciarAnalytics();
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
