import { Pressable, View } from "react-native";

import { Icone } from "@/ui/componentes/Icone";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco, raio, texto } from "@/ui/tokens";

/** A linha tracejada do fim da lista. Plano cheio: apagada e desativada; o motivo vem do servidor (Banner logo acima), não é texto daqui. */
export function ConectarOutro({ desativado, aoConectar }: { desativado: boolean; aoConectar: () => void }) {
  const { cores } = useTema();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel="Conectar outro banco"
      accessibilityState={{ disabled: desativado }}
      disabled={desativado}
      onPress={aoConectar}
      style={{ flexDirection: "row", alignItems: "center", gap: espaco.md, minHeight: 72, paddingHorizontal: espaco.lg, borderRadius: raio.bloco, borderWidth: 1.5, borderStyle: "dashed", borderColor: cores.border, opacity: desativado ? 0.5 : 1 }}
    >
      <View style={{ width: 44, height: 44, borderRadius: 22, alignItems: "center", justifyContent: "center", backgroundColor: desativado ? cores.surfaceControl : cores.brandSoft }}>
        <Icone nome="Plus" tom={desativado ? "inkMuted" : "brandInk"} />
      </View>
      <Texto style={{ flex: 1, fontFamily: texto.secao.fontFamily }}>Conectar outro banco</Texto>
      <Icone nome="CaretRight" tamanho={20} tom="inkFaint" />
    </Pressable>
  );
}
