import * as AppleAuthentication from "expo-apple-authentication";
import { ActivityIndicator, Platform, View } from "react-native";

import { continuarComApple } from "@/features/auth/apple";
import { tocar, type EstadoEntrar } from "@/features/auth/entrar";
import { continuarComGoogle } from "@/features/auth/google";
import { ALTURA, Button } from "@/ui/componentes/Button";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco, raio } from "@/ui/tokens";

interface Props {
  estado: EstadoEntrar;
  aplicar: (e: EstadoEntrar) => void;
  autenticar: () => void;
  /** O login por e-mail em voo (Entrar): os dois botões ficam apagados, como com o outro provedor em voo. */
  bloqueado?: boolean;
}

/** "Continuar com Google" e o botão da Apple, os mesmos no Entrar e na Boas-vindas. */
export function BotoesSociais({ estado, aplicar, autenticar, bloqueado = false }: Props) {
  const { cores, esquema } = useTema();
  const google = estado.fase === "google";
  const apple = estado.fase === "apple";
  const ocupado = bloqueado || google || apple;

  return (
    <View style={{ gap: espaco.sm }}>
      <Button
        rotulo="Continuar com Google"
        variante="secondary"
        icone="GoogleLogo"
        carregando={google}
        desativado={bloqueado || apple}
        onPress={() => {
          // G antes de `tocar()`, pelo mesmo motivo do Entrar (B1).
          aplicar({ fase: "google" });
          void tocar(() => continuarComGoogle(autenticar), aplicar);
        }}
      />
      {/* Só iOS: no Android não há Apple (nem botão, nem legenda). Botão do
          sistema (decisão do dono): não desenha carregando, então em A
          ele fica inerte e o indicador aparece ao lado; em E e G, apagado. */}
      {Platform.OS === "ios" ? (
        <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.sm }}>
          <View
            testID="apple-involucro"
            style={{ flex: 1, opacity: bloqueado || google ? 0.5 : 1 }}
            pointerEvents={ocupado ? "none" : "auto"}
          >
            <AppleAuthentication.AppleAuthenticationButton
              // O nativo não redesenha ao trocar o estilo: `key` pelo tema remonta.
              key={esquema}
              buttonType={AppleAuthentication.AppleAuthenticationButtonType.CONTINUE}
              buttonStyle={
                esquema === "dark"
                  ? AppleAuthentication.AppleAuthenticationButtonStyle.WHITE
                  : AppleAuthentication.AppleAuthenticationButtonStyle.WHITE_OUTLINE
              }
              cornerRadius={raio.md}
              style={{ height: ALTURA.M }}
              onPress={() => {
                // A antes de `tocar()`, pelo mesmo motivo do Entrar (B1).
                aplicar({ fase: "apple" });
                void tocar(() => continuarComApple(autenticar), aplicar);
              }}
            />
          </View>
          {apple ? <ActivityIndicator color={cores.ink} accessibilityLabel="Entrando com a Apple" /> : null}
        </View>
      ) : null}
    </View>
  );
}

/** O divisor "ou" entre os botões sociais e o outro caminho. */
export function Ou() {
  const { cores } = useTema();
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.md }}>
      <View style={{ flex: 1, height: 1, backgroundColor: cores.border }} />
      <Texto variante="legenda" tom="inkMuted">
        ou
      </Texto>
      <View style={{ flex: 1, height: 1, backgroundColor: cores.border }} />
    </View>
  );
}
