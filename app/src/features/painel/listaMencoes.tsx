import { Image, Pressable, ScrollView, View } from "react-native";
import { selecao } from "@/ui/haptics";
import { TextoPainel as Texto } from "./tipografia";
import { GRUPO, type itensMencao } from "./agentes";
import { AVATAR } from "./avatares";
/** A lista do @, no lugar das sugestões: rola na vertical, e o toque no item não só fecha o teclado (`keyboardShouldPersistTaps`). Item de 44 pt. */
export function ListaMencoes({ itens, onEscolher }: { itens: ReturnType<typeof itensMencao>; onEscolher: (nome: string) => void }) {
  let ultimo = "";
  return <ScrollView keyboardShouldPersistTaps="handled" accessibilityLabel="Agentes" style={{ maxHeight: 260, flexGrow: 0 }}>
    {itens.map((a) => {
      const grupo = GRUPO[a.acesso], cabeca = grupo !== ultimo ? (ultimo = grupo) : null;
      return <View key={a.kind}>
        {cabeca && <Texto variante="legenda" tom="inkMuted" accessibilityRole="header" style={{ paddingTop: 8 }}>{cabeca}</Texto>}
        <Pressable accessibilityRole="button" accessibilityLabel={`${a.nome}, ${a.tema}`} onPress={() => { selecao(); onEscolher(a.nome); }} style={{ minHeight: 44, flexDirection: "row", alignItems: "center", gap: 12 }}>
          <Image source={AVATAR[a.kind]!} accessibilityIgnoresInvertColors style={{ width: 28, height: 28, borderRadius: 14 }} />
          <Texto variante="rotulo">{a.nome}</Texto>
          <Texto variante="legenda" tom="inkMuted" numberOfLines={1} style={{ flex: 1 }}>{a.tema}</Texto>
        </Pressable>
      </View>;
    })}
  </ScrollView>;
}
