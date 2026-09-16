import { useEffect, useState } from "react";
import { ActivityIndicator, StyleSheet, Text, View, useColorScheme } from "react-native";

import { SessaoExpirada } from "@/api/client";
import { perfil } from "@/services/auth";
import { claro, escuro, espaco, texto } from "@/ui/tokens";

type Estado =
  | { fase: "carregando" }
  | { fase: "pronto"; nome: string }
  | { fase: "sem-sessao" }
  | { fase: "erro"; mensagem: string };

/**
 * A prova de ponta a ponta da Fase 1: token guardado → `Authorization: Bearer`
 * → `/auth/me` → nome na tela. Não é produto; a primeira tela de verdade é a
 * Fase 3. O que ela demonstra é a fundação inteira funcionando junta.
 */
export default function Inicio() {
  const paleta = useColorScheme() === "dark" ? escuro : claro;
  const [estado, setEstado] = useState<Estado>({ fase: "carregando" });

  useEffect(() => {
    let vivo = true;
    perfil()
      .then((p) => {
        if (!vivo) return;
        const nome = p.display_name?.trim() || p.email?.split("@")[0] || "por aí";
        setEstado({ fase: "pronto", nome });
      })
      .catch((e: unknown) => {
        if (!vivo) return;
        if (e instanceof SessaoExpirada) return setEstado({ fase: "sem-sessao" });
        setEstado({
          fase: "erro",
          mensagem: e instanceof Error ? e.message : "Algo deu errado.",
        });
      });
    return () => {
      vivo = false;
    };
  }, []);

  return (
    <View style={[estilos.tela, { backgroundColor: paleta.bg }]}>
      {estado.fase === "carregando" && <ActivityIndicator color={paleta.brand} />}

      {estado.fase === "pronto" && (
        <Text style={[texto.titulo, { color: paleta.ink }]}>
          Olá, {estado.nome}
        </Text>
      )}

      {estado.fase === "sem-sessao" && (
        <Text style={[texto.corpo, { color: paleta.inkMuted }]}>
          Entre para continuar.
        </Text>
      )}

      {estado.fase === "erro" && (
        <Text style={[texto.corpo, { color: paleta.negative }]}>
          {estado.mensagem}
        </Text>
      )}
    </View>
  );
}

const estilos = StyleSheet.create({
  tela: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: espaco.lg,
  },
});
