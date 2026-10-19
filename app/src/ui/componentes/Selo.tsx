import { View } from "react-native";

import { useTema } from "@/ui/tema";
import { raio } from "@/ui/tokens";

import { Texto } from "./Texto";

/** Etiqueta curta (ex.: "Pro"): texto `ink` sobre `border`, o mesmo estilo do selo da meta. */
export function Selo({ texto }: { texto: string }) {
  const { cores } = useTema();
  return <View style={{ backgroundColor: cores.border, borderRadius: raio.chip, paddingHorizontal: 8, paddingVertical: 2 }}><Texto variante="legenda">{texto}</Texto></View>;
}
