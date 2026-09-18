import type { ReactNode } from "react";
import { View } from "react-native";

import { useTema } from "@/ui/tema";
import { espaco, raio } from "@/ui/tokens";

type Elevacao = "surface" | "raised";

interface Props {
  children: ReactNode;
  /**
   * `surface`: plano, mesmo fundo do card comum. `raised`: se destaca do
   * fundo. Chamada de `elevacao`, não `tone`: em `Texto`/`Money` deste
   * design system `tom` já é a chave de COR (`tom="positive"`,
   * `tom="inkMuted"`); usar o mesmo nome aqui para "plano vs. destacado"
   * (que não muda cor nenhuma, só fundo/sombra) misturaria dois conceitos
   * diferentes sob um nome. Vale para quem compuser `Card` em
   * `TransactionRow`/`InsightCard`: não reintroduzir `tone` para isto.
   */
  elevacao?: Elevacao;
}

/**
 * Sombra declarada UMA vez (impeccable): só o `raised` no tema claro a usa —
 * no escuro, `surfaceRaised` já é mais claro que `bg`, então a elevação vem da
 * cor, não de sombra (nada de "brilho" artificial sobre fundo escuro).
 */
const SOMBRA_RAISED_CLARO = {
  shadowOpacity: 0.06,
  shadowOffset: { width: 0, height: 1 },
  shadowRadius: 2,
  elevation: 2,
} as const;

export function Card({ children, elevacao = "surface" }: Props) {
  const { esquema, cores } = useTema();
  const raised = elevacao === "raised";
  const comSombra = raised && esquema === "light";

  return (
    <View
      style={[
        {
          borderRadius: raio.md,
          padding: espaco.lg,
          backgroundColor: raised ? cores.surfaceRaised : cores.surface,
        },
        // `shadowColor` vem de `cores.shadow` (tokens.ts), não do literal
        // "black": o gate de hex só barra `#...` fora de `tokens.ts`, mas uma
        // cor de sombra é regra de produto igual a qualquer outra — uma
        // fonte só, mesmo sem precisar de par de contraste (sombra não é
        // texto nem contorno de controle).
        comSombra ? { borderWidth: 1, borderColor: cores.border, shadowColor: cores.shadow, ...SOMBRA_RAISED_CLARO } : null,
      ]}
    >
      {children}
    </View>
  );
}
