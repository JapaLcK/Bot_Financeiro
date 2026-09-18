import { useState } from "react";
import { Pressable, View } from "react-native";

import { AmountInput } from "@/ui/componentes/AmountInput";
import { Money } from "@/ui/componentes/Money";
import { Texto } from "@/ui/componentes/Texto";
import { TETO_CENTAVOS } from "@/ui/dinheiro";
import { useTema } from "@/ui/tema";
import { espaco, raio } from "@/ui/tokens";

const TIPOS = ["saldo", "entrada", "saida"] as const;
const VARIANTES = ["display", "corpo", "rotulo"] as const;

/**
 * Catálogo de `Money`/`AmountInput` (PR B), separado do `app/_ds/index.tsx`
 * porque o arquivo principal já ficaria perto do teto de 350 linhas com esta
 * seção junto — CLAUDE.md §0.5. Fora de `app/app/`, então o expo-router não
 * vira rota disto.
 */
export function SecaoDinheiro() {
  const { cores } = useTema();
  const [erro, setErro] = useState(false);
  const [centavos, setCentavos] = useState(4590);

  return (
    <View style={{ gap: espaco.lg }}>
      <Texto variante="secao">Dinheiro</Texto>

      {TIPOS.map((tipo) => (
        <View key={tipo} style={{ gap: espaco.xs }}>
          <Texto variante="rotulo" tom="inkMuted">
            {tipo}
          </Texto>
          {VARIANTES.map((variante) => (
            <Money key={variante} centavos={123456789} tipo={tipo} variante={variante} />
          ))}
        </View>
      ))}

      <View style={{ gap: espaco.xs }}>
        <Texto variante="rotulo" tom="inkMuted">
          casos especiais
        </Texto>
        <Money centavos={0} />
        <Money centavos={-123456789} tipo="saldo" />
        <Money centavos={-1230} tipo="saida" />
        <Money centavos={123456789} oculto />
        <Money centavos={NaN} />
        <Money centavos={TETO_CENTAVOS} variante="display" />
      </View>

      <View style={{ gap: espaco.sm }}>
        <Texto variante="rotulo" tom="inkMuted">
          AmountInput
        </Texto>
        <AmountInput centavos={centavos} onChange={setCentavos} rotulo="Valor" erro={erro ? "Saldo insuficiente" : undefined} />
        <Pressable
          accessibilityRole="button"
          onPress={() => setErro((v) => !v)}
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
            {erro ? "Tirar erro" : "Simular erro"}
          </Texto>
        </Pressable>
      </View>
    </View>
  );
}
