import { useFonts } from "expo-font";
import { Stack } from "expo-router";
import { useEffect } from "react";
import { useColorScheme, View } from "react-native";

import { SessaoProvider, useSessao } from "@/features/auth/sessao";
import { rastrear } from "@/services/analytics";
import { Button } from "@/ui/componentes/Button";
import { Texto } from "@/ui/componentes/Texto";
import { TemaProvider, useTema } from "@/ui/tema";
import { claro, escuro, espaco } from "@/ui/tokens";

import "../global.css";

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
      <SessaoProvider>
        <Roteador />
      </SessaoProvider>
    </TemaProvider>
  );
}

/**
 * Lê a sessão e decide entre a mesma View de espera (enquanto ela verifica o
 * cofre), uma tela de erro plana (cofre ilegível) ou a pilha de rotas — as
 * duas primeiras não podem entrar como `Stack.Screen`: sem sessão decidida
 * ainda não há como saber se o guard de `(auth)` ou `(app)` vale.
 */
function Roteador() {
  const { estado, tentarDeNovo } = useSessao();
  const { cores } = useTema();

  if (estado.fase === "verificando") {
    return <View style={{ flex: 1, backgroundColor: cores.bg }} />;
  }

  if (estado.fase === "erro") {
    return (
      <View style={{ flex: 1, backgroundColor: cores.bg, alignItems: "center", justifyContent: "center", gap: espaco.lg, padding: espaco.xxl }}>
        <Texto variante="corpo" tom="danger" style={{ textAlign: "center" }}>
          {estado.mensagem}
        </Texto>
        <Button rotulo="Tentar de novo" onPress={tentarDeNovo} />
      </View>
    );
  }

  return (
    <Stack
      screenOptions={{
        headerShown: false,
        contentStyle: { backgroundColor: cores.bg },
      }}
    >
      {/*
        (auth)/(app) vêm ANTES de `_ds`, de propósito: quando mais de um
        `Stack.Protected` está com o guard verdadeiro ao mesmo tempo (em dev,
        `__DEV__` é sempre true, e ele não olha para a sessão), o
        `Stack.Protected` resolve a rota padrão pelo PRIMEIRO grupo
        verdadeiro na ordem em que aparecem aqui — medido com `renderRouter`.
        Com `_ds` primeiro, todo cold start em build de dev caía no catálogo
        interno em vez da tela de Entrar/da tela autenticada.
      */}
      <Stack.Protected guard={estado.fase === "anonimo"}>
        <Stack.Screen name="(auth)" />
      </Stack.Protected>
      <Stack.Protected guard={estado.fase === "autenticado"}>
        <Stack.Screen name="(app)" />
      </Stack.Protected>
      {/* Catálogo interno do design system: só existe em build de dev. */}
      <Stack.Protected guard={__DEV__}>
        <Stack.Screen name="_ds" />
      </Stack.Protected>
    </Stack>
  );
}
