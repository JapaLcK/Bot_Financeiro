import { router, useFocusEffect } from "expo-router";
import { useCallback, useRef, useState } from "react";
import { ActivityIndicator, ScrollView, View } from "react-native";

import { SessaoExpirada } from "@/api/client";
import type { MfaStatus, Perfil } from "@/api/schemas/auth";
import { EsqueciSenha } from "@/features/auth/EsqueciSenha";
import { Desbloqueio } from "@/features/seguranca/Desbloqueio";
import { useFalha } from "@/features/seguranca/falha";
import { useVoltaDoFundo } from "@/features/seguranca/voltaDoFundo";
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

  // Só a ÚLTIMA carga aplica estado: foco, volta do navegador e "Tentar de novo" podem
  // sobrepor, e uma resposta velha (ex.: `has_password: false`) não pode pintar por cima da nova.
  const geracao = useRef(0);

  const carregar = async () => {
    const minha = ++geracao.current;
    // A expiração desta carga já foi notificada à sessão? (ver invariante 3)
    let avisada = false;
    try {
      // statusMfa × perfil -> ação. A ordem de chegada NÃO conta (allSettled espera as duas), e a
      // sessão expirada de QUALQUER uma vence (o reset de senha revoga sessões, e uma chamada pode
      // descobrir isso depois da outra ter falhado por rede):
      //
      //   status \ perfil   ok                    sessão expirada   recuperável (rede/5xx)
      //   ok                pronto(conta)         login             pronto(conta null: permissivo,
      //                                                              o 409 do setup é a autoridade)
      //   sessão expirada   login                 login             login
      //   recuperável       erro + Tentar de      login             erro + Tentar de novo
      //                     novo; em recarga com                    (em recarga com dados na tela,
      //                     dados na tela, mantém                   mantém a tela)
      //
      // login = `falha(e)` na hora, pelo `avisando`; erro/mantém = o ramo A2 do catch.
      // Cada chamada avisa a sessão NA HORA em que rejeita com `SessaoExpirada`; o allSettled só
      // espera a irmã para decidir a ESCRITA de estado (invariante 4 abaixo).
      const avisando = <T,>(p: Promise<T>) =>
        p.catch((e: unknown) => {
          if (e instanceof SessaoExpirada) {
            avisada = true;
            falha(e);
          }
          throw e;
        });
      const [status, conta] = await Promise.allSettled([avisando(statusMfa()), avisando(perfil())]);
      const expirada = [status, conta].find((r) => r.status === "rejected" && r.reason instanceof SessaoExpirada);
      if (expirada?.status === "rejected") throw expirada.reason;
      if (status.status === "rejected") throw status.reason;
      if (minha === geracao.current) {
        setEstado({ fase: "pronto", status: status.value, conta: conta.status === "fulfilled" ? conta.value : null });
      }
    } catch (e) {
      // Invariantes de `carregar` (cinco rodadas de revisão neste ponto):
      //  1. A geração governa só ESCRITAS DE ESTADO da tela (o `setEstado` de pronto/erro).
      //  2. Sessão expirada é fato global: de QUALQUER geração, chega a `falha(e)`. Uma carga antiga
      //     que descobre a expiração depois de uma nova ter começado não pode ser descartada, ou a
      //     tela fica autenticada com controles velhos.
      //  3. Cada expiração é notificada UMA ÚNICA VEZ por chamada, no instante em que ocorre (`avisando`),
      //     e nunca se repete: `sessao.expirou` só é idempotente enquanto o provider está anônimo. Se a
      //     pessoa reentrar antes de a chamada irmã terminar, um segundo `falha(e)` tardio encontraria
      //     o provider `autenticado` de novo e derrubaria a sessão NOVA. Por isso o catch só notifica
      //     uma expiração que não passou pelo `avisando` (`avisada` falso).
      //  4. Evento terminal de sessão propaga NA HORA, sem esperar a chamada irmã (que pode ficar
      //     pendurada até o limite de 15 s do cliente); só a ESCRITA de estado espera as duas.
      if (e instanceof SessaoExpirada) {
        if (!avisada) falha(e);
        return;
      }
      if (minha !== geracao.current) return;
      const texto = falha(e);
      // Recarga que falha com dados na tela (foco, volta do navegador) mantém a tela: trocar por
      // "erro" desmontaria o `EsqueciSenha` e reabriria o envio (teto de 3/h). Erro só sem dados.
      if (texto !== null) setEstado((atual) => (atual.fase === "pronto" ? atual : { fase: "erro", mensagem: texto }));
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

  // O link do e-mail abre o Safari: a volta é AppState, não foco (ver `useVoltaDoFundo`).
  useVoltaDoFundo(() => void carregar());

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
          estado.conta.email?.trim() ? (
            <EsqueciSenha emailFixo={estado.conta.email.trim()} />
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
