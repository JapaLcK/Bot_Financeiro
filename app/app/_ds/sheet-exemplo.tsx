import { router } from "expo-router";
import { View } from "react-native";

import { Button } from "@/ui/componentes/Button";
import { SheetConteudo } from "@/ui/componentes/Sheet";
import { Texto } from "@/ui/componentes/Texto";
import { espaco } from "@/ui/tokens";

/**
 * Rota que abre como Sheet nativa (as opções ficam em `_ds/_layout.tsx`,
 * junto do `Stack.Screen`) — a PROVA de que o preset do expo-router abre de
 * verdade, não só de que os tipos batem.
 */
export default function SheetExemplo() {
  return (
    <SheetConteudo>
      <View style={{ gap: espaco.lg }}>
        <Texto variante="secao">Sheet nativo</Texto>
        <Texto variante="corpo" tom="inkMuted">
          {'`presentation: "formSheet"` do expo-router — sem dependência nova.'}
        </Texto>
        <Button rotulo="Fechar" variante="secondary" onPress={() => router.back()} />
      </View>
    </SheetConteudo>
  );
}
