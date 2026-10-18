import { Image, View } from "react-native";

import { Button } from "@/ui/componentes/Button";
import { Icone, type NomeIcone } from "@/ui/componentes/Icone";
import { Texto } from "@/ui/componentes/Texto";
import { STICKERS } from "@/ui/stickers";
import { useTema } from "@/ui/tema";
import { espaco } from "@/ui/tokens";

const PASSOS: { icone: NomeIcone; rotulo: string }[] = [{ icone: "Bank", rotulo: "Escolha" }, { icone: "Lock", rotulo: "Autorize" }, { icone: "CheckCircle", rotulo: "Pronto" }];

/** Nenhum banco: o Piggy, os três passos em ícone e o botão. O sticker é decorativo (a frase ao lado já diz tudo). */
export function SemBancos({ desativado, aoConectar }: { desativado: boolean; aoConectar: () => void }) {
  const { cores } = useTema();
  return (
    <View style={{ alignItems: "center", gap: espaco.lg, paddingVertical: espaco.xxl }}>
      <Image source={STICKERS.hello} accessibilityIgnoresInvertColors accessible={false} resizeMode="contain" style={{ width: 150, height: 150 }} />
      <Texto variante="titulo" style={{ textAlign: "center" }}>Conecte seu primeiro banco</Texto>
      <View accessible accessibilityLabel="Escolha, autorize, pronto" style={{ flexDirection: "row", alignItems: "center", gap: espaco.md }}>
        {PASSOS.map((p, i) => (
          <View key={p.rotulo} style={{ flexDirection: "row", alignItems: "center", gap: espaco.md }}>
            {i > 0 ? <View style={{ width: 22, height: 2, borderRadius: 2, backgroundColor: cores.border }} /> : null}
            <View style={{ alignItems: "center", gap: espaco.sm }}>
              <View style={{ padding: espaco.lg, borderRadius: 999, backgroundColor: i === PASSOS.length - 1 ? cores.brandSoft : cores.surface }}>
                <Icone nome={p.icone} tom={i === PASSOS.length - 1 ? "brandInk" : "ink"} />
              </View>
              <Texto variante="legenda" tom="inkMuted">{p.rotulo}</Texto>
            </View>
          </View>
        ))}
      </View>
      <View style={{ alignSelf: "stretch" }}><Button rotulo="Conectar banco" tamanho="L" desativado={desativado} onPress={aoConectar} /></View>
      <View style={{ flexDirection: "row", alignItems: "center", gap: espaco.xs }}>
        <Icone nome="ShieldCheck" tamanho={16} tom="inkFaint" />
        <Texto variante="legenda" tom="inkFaint">Só leitura. Você revoga quando quiser.</Texto>
      </View>
    </View>
  );
}
