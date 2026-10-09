import { router, useFocusEffect } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, ScrollView, View } from "react-native";

import type { MfaStatus, Perfil } from "@/api/schemas/auth";
import { EsqueciSenha } from "@/features/auth/EsqueciSenha";
import { useForeground } from "@/features/openFinance/useForeground";
import { Desbloqueio } from "@/features/seguranca/Desbloqueio";
import { useFalha } from "@/features/seguranca/falha";
import { perfil } from "@/services/auth";
import { statusMfa } from "@/services/mfa";
import { Button } from "@/ui/componentes/Button";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

type Estado = { fase: "carregando" } | { fase: "erro"; mensagem: string } | { fase: "pronto"; status: MfaStatus; conta: Perfil | null };

/**
 * Segurança, aberta pelo Início até o Perfil (Fase 10) existir: o MFA e a
 * trava do aparelho (`Desbloqueio`, independente do status do MFA); sessões
 * entram como seção irmã.
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
      // `conta` nula (falha do /auth/me) = lado permissivo: segue como se tivesse senha, e a
      // autoridade é o 409 do /auth/mfa/setup (mesma decisão do settings.html).
      const [status, conta] = await Promise.all([statusMfa(), perfil().catch(() => null)]);
      setEstado({ fase: "pronto", status, conta });
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

  // O link do e-mail abre o Safari: voltar ao app é AppState, não foco de navegação, e
  // o foco acima não dispara. Recarrega só na VOLTA do background (não no 1º render, que
  // o foco já cobre) e sem ir a "carregando": a tela segue como está até a resposta chegar. `[ativo]` de propósito,
  // como o `[]` acima: `carregar`/`estado` mudam a cada render.
  const ativo = useForeground();
  const antes = useRef(ativo);
  useEffect(() => {
    if (ativo && !antes.current && estado.fase !== "carregando") void carregar();
    antes.current = ativo;
  }, [ativo]);

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
        ) : estado.conta?.has_password === false ? (
          // Conta do Google/Apple: sem senha o setup responde 409, então "Ativar" nem aparece.
          // Só `false` conta; ausente ou `true` cai no ramo de baixo.
          estado.conta.email ? (
            <EsqueciSenha emailFixo={estado.conta.email} />
          ) : (
            <Texto variante="corpo">Sua conta ainda não tem senha. Defina uma senha pelo site para ativar a autenticação em 2 etapas.</Texto>
          )
        ) : (
          <View style={{ gap: espaco.md }}>
            <Button rotulo="Ativar" onPress={() => router.push("/mfa-ativar")} />
          </View>
        ))}

      <Desbloqueio />
    </ScrollView>
  );
}
