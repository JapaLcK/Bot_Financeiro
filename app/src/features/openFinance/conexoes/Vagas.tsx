import { View } from "react-native";

import { Icone } from "@/ui/componentes/Icone";
import { Texto } from "@/ui/componentes/Texto";
import { useTema } from "@/ui/tema";
import { espaco, raio } from "@/ui/tokens";

type Vagas = { max: number; emUso: number };
/** Até aqui desenho um quadradinho por vaga; acima, só o "2/9" (uma fileira de 9 não cabe na linha). */
const MAX_QUADRADOS = 6;

/** As vagas do plano ("2/3"), acima da lista. Sem total, barra nem legenda: a lista não mostra dinheiro. */
export function Vaga({ vagas }: { vagas: Vagas }) {
  const { cores } = useTema();
  const quadrados = vagas.max <= MAX_QUADRADOS ? vagas.max : 0;
  return (
    <View accessible accessibilityLabel={`${vagas.emUso} de ${vagas.max} bancos`} style={{ flexDirection: "row", alignItems: "center", gap: espaco.xs }}>
      {Array.from({ length: quadrados }, (_, i) => {
        const ocupada = i < vagas.emUso;
        return (
          <View key={i} style={{ width: 28, height: 28, borderRadius: raio.controle, alignItems: "center", justifyContent: "center", backgroundColor: ocupada ? cores.brandSoft : cores.surfaceControl }}>
            <Icone nome="Bank" tamanho={16} tom={ocupada ? "brandInk" : "inkFaint"} />
          </View>
        );
      })}
      <Texto variante="rotulo" tom="inkMuted" numerico>{vagas.emUso}/{vagas.max}</Texto>
    </View>
  );
}
