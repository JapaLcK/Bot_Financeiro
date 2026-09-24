import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { ActivityIndicator, ScrollView, View } from "react-native";

import type { MfaStatus } from "@/api/schemas/auth";
import { useFalha } from "@/features/seguranca/falha";
import { statusMfa } from "@/services/mfa";
import { Button } from "@/ui/componentes/Button";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

type Estado = { fase: "carregando" } | { fase: "erro"; mensagem: string } | { fase: "pronto"; status: MfaStatus };

/**
 * Segurança, aberta pelo Início até o Perfil (Fase 10) existir. Por ora só o
 * MFA; sessões e biometria entram como seções irmãs.
 *
 * O status recarrega a cada FOCO: é assim que a tela reflete o que as sheets
 * de ativar/gerar/desativar acabaram de fazer ao fechar. Sem voltar a
 * "carregando" nesse recarregamento — a tela já tem um status para mostrar.
 */
export default function Seguranca() {
  const { cores } = useTema();
  const falha = useFalha();
  const [estado, setEstado] = useState<Estado>({ fase: "carregando" });

  const carregar = async () => {
    try {
      setEstado({ fase: "pronto", status: await statusMfa() });
    } catch (e) {
      const texto = falha(e);
      if (texto !== null) setEstado({ fase: "erro", mensagem: texto });
    }
  };

  // `[]` de propósito, como o efeito do Início: `falha` muda de referência a
  // cada mudança de estado da sessão, e depender dela recarregaria a tela em
  // laço quando a sessão expira. O `carregar` do primeiro render basta — o
  // `expirou` do provider lê o estado pelo `setState` funcional.
  useFocusEffect(
    useCallback(() => {
      void carregar();
    }, []),
  );

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: cores.bg }}
      contentContainerStyle={{ padding: espaco.lg, gap: espaco.lg }}
      contentInsetAdjustmentBehavior="automatic"
    >
      <Texto variante="secao">Autenticação em 2 etapas</Texto>

      {estado.fase === "carregando" && <ActivityIndicator color={cores.brand} accessibilityLabel="Carregando" />}

      {estado.fase === "erro" && (
        <>
          <Texto variante="corpo" tom="danger">
            {estado.mensagem}
          </Texto>
          <Button
            rotulo="Tentar de novo"
            onPress={() => {
              setEstado({ fase: "carregando" });
              void carregar();
            }}
          />
        </>
      )}

      {estado.fase === "pronto" &&
        (estado.status.enabled ? (
          <View style={{ gap: espaco.md }}>
            <Texto variante="corpo">Ativa · {estado.status.backup_codes_remaining} de 10 códigos de backup</Texto>
            <Button rotulo="Gerar novos códigos" variante="secondary" onPress={() => router.push("/mfa-novos-codigos")} />
            <Button rotulo="Desativar" variante="ghost" onPress={() => router.push("/mfa-desativar")} />
          </View>
        ) : (
          <View style={{ gap: espaco.md }}>
            <Texto variante="corpo" tom="inkMuted">
              Adicione uma camada extra de proteção com Google Authenticator, Authy ou similar.
            </Texto>
            <Button rotulo="Ativar" onPress={() => router.push("/mfa-ativar")} />
          </View>
        ))}
    </ScrollView>
  );
}
