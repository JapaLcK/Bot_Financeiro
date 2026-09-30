import { useState } from "react";
import { Image, Platform, StyleSheet, View } from "react-native";
import { FullWindowOverlay } from "react-native-screens";

import { useSessao } from "@/features/auth/sessao";
import { Banner } from "@/ui/componentes/Banner";
import { Button } from "@/ui/componentes/Button";
import { Screen } from "@/ui/componentes/Screen";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

import { useBloqueio } from "./bloqueio";

// O mesmo símbolo da Boas-vindas (`app/(auth)/boas-vindas.tsx`), pelo mesmo `require()`.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const SIMBOLO = require("../../../assets/brand/simbolo.png");

const MENSAGEM_ERRO_SAIR = "Não conseguimos sair. Tente de novo.";

/**
 * A trava: o símbolo, "Desbloquear" (depois de uma falha) e "Sair". Quando o
 * app perde o foco, quem cobre é a tampa NATIVA (`modules/tampa`), com o
 * símbolo no MESMO ponto — o centro da área segura — para a troca não pular.
 */
export function TelaDeBloqueio() {
  const { estado, desbloquear } = useBloqueio();
  const sessao = useSessao();
  const [saindo, setSaindo] = useState(false);
  const [erroSaida, setErroSaida] = useState<string | null>(null);

  const sair = async () => {
    setErroSaida(null);
    setSaindo(true);
    const ok = await sessao.sair();
    if (!ok) {
      setSaindo(false);
      setErroSaida(MENSAGEM_ERRO_SAIR);
    }
  };

  return (
    <Screen rolar={false}>
      <View style={{ flex: 1 }}>
        {/* Só o símbolo no fluxo, centrado; título e botões FORA dele. Assim o
            símbolo fica exatamente onde a tampa nativa o desenha, com e sem
            "Desbloquear" na tela. */}
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <View>
            <Image source={SIMBOLO} accessible={false} resizeMode="contain" style={{ width: 60, height: 64 }} />
            <Texto
              variante="titulo"
              accessibilityRole="header"
              style={{ position: "absolute", top: 64 + espaco.md, left: -espaco.huge * 2, right: -espaco.huge * 2, textAlign: "center" }}
            >
              PigBank
            </Texto>
          </View>
        </View>
        <View style={{ position: "absolute", left: 0, right: 0, bottom: espaco.xxl, gap: espaco.sm }}>
          {erroSaida ? <Banner tom="danger" mensagem={erroSaida} /> : null}
          {estado.falhou && !estado.autenticando ? <Button rotulo="Desbloquear" tamanho="L" onPress={desbloquear} /> : null}
          <Button rotulo="Sair" variante="ghost" carregando={saindo} onPress={() => void sair()} />
        </View>
      </View>
    </Screen>
  );
}

/**
 * Por cima da pilha, sem desmontá-la (preserva a navegação e as sheets — os
 * códigos de backup aparecem uma vez só). No iOS, `FullWindowOverlay` fica
 * direto na window, acima das formSheet já apresentadas, e o modal de
 * acessibilidade impede o VoiceOver de ler o que está embaixo.
 */
export function CoberturaPorCima() {
  if (Platform.OS === "ios") {
    return (
      <FullWindowOverlay unstable_accessibilityContainerViewIsModal>
        <TelaDeBloqueio />
      </FullWindowOverlay>
    );
  }
  // ponytail: no Android só cobre a vista; TalkBack embaixo e FLAG_SECURE ficam para a issue do Android.
  return (
    <View style={StyleSheet.absoluteFill}>
      <TelaDeBloqueio />
    </View>
  );
}
