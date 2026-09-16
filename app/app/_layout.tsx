import { Stack } from "expo-router";
import { useEffect } from "react";
import { useColorScheme } from "react-native";

import { iniciarAnalytics, rastrear } from "@/services/analytics";
import { iniciarLogging } from "@/services/logging";
import { claro, escuro } from "@/ui/tokens";

// No escopo do MÓDULO, não num efeito: efeito só roda depois que a árvore
// inicial renderizou e foi commitada, e as falhas que esta camada mais precisa
// relatar — erro na avaliação de um módulo, erro no primeiro render — acontecem
// antes disso. Instalar tarde é não instalar para o caso que importa.
iniciarLogging();
iniciarAnalytics();

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
