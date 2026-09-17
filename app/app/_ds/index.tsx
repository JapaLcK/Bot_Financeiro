import { useState } from "react";
import { Pressable, ScrollView, useColorScheme, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { Texto } from "@/ui/componentes/Texto";
import { TemaProvider, useTema } from "@/ui/tema";
import { claro, espaco, raio, texto as escalas, type Paleta } from "@/ui/tokens";

type Esquema = "light" | "dark";

const NOMES_COR = Object.keys(claro) as (keyof Paleta)[];
const VARIANTES_TEXTO = Object.keys(escalas) as (keyof typeof escalas)[];

/**
 * Catálogo do design system, só em dev (as duas guardas ficam em `_layout.tsx`
 * — aqui e na raiz). Cobre o que a Fundação entrega: a tabela de cor completa
 * e as seis variantes tipográficas, nos dois temas.
 */
export default function DsIndex() {
  // Começa no tema do aparelho; o botão só troca para conferir o outro.
  const sistema = useColorScheme();
  const [esquema, setEsquema] = useState<Esquema>(sistema === "dark" ? "dark" : "light");
  return (
    <TemaProvider esquema={esquema}>
      <Catalogo esquema={esquema} onTrocar={() => setEsquema((e) => (e === "light" ? "dark" : "light"))} />
    </TemaProvider>
  );
}

function Catalogo(props: { esquema: Esquema; onTrocar: () => void }) {
  const { cores } = useTema();
  // A raiz não tem header: sem os insets, o topo do catálogo fica sob o relógio
  // e a Dynamic Island (visto no iPhone 17 Pro). O `Screen` do C1 assume isto.
  const insets = useSafeAreaInsets();
  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: cores.bg }}
      contentContainerStyle={{
        paddingTop: insets.top + espaco.lg,
        paddingBottom: insets.bottom + espaco.xxl,
        paddingHorizontal: espaco.xxl,
        gap: espaco.lg,
      }}
    >
      <Pressable
        accessibilityRole="button"
        onPress={props.onTrocar}
        style={{
          alignSelf: "flex-start",
          minHeight: 44,
          justifyContent: "center",
          paddingHorizontal: espaco.lg,
          borderRadius: raio.lg,
          backgroundColor: cores.brandSoft,
        }}
      >
        <Texto variante="rotulo" tom="brandInk">
          Ver em {props.esquema === "light" ? "escuro" : "claro"}
        </Texto>
      </Pressable>

      <Texto variante="secao">Cor</Texto>
      {NOMES_COR.map((nome) => (
        <View key={nome} style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
          <View
            style={{
              width: 32,
              height: 32,
              borderRadius: raio.sm,
              backgroundColor: cores[nome],
              borderWidth: 1,
              borderColor: cores.border,
            }}
          />
          <Texto variante="legenda" tom="inkMuted">
            {nome} · {cores[nome]}
          </Texto>
        </View>
      ))}

      <Texto variante="secao">Tipografia</Texto>
      {VARIANTES_TEXTO.map((variante) => (
        <Texto key={variante} variante={variante}>
          {variante}
        </Texto>
      ))}
    </ScrollView>
  );
}
