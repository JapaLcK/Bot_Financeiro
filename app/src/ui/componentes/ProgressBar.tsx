import { View } from "react-native";

import { useTema } from "@/ui/tema";
import { raio, type Paleta } from "@/ui/tokens";

type Tom = "brand" | "positive" | "warning" | "danger";

interface Props {
  /** 0..1. Fora da faixa é clampado; não-finito (NaN) vira 0. */
  valor: number;
  tom?: Tom;
}

/**
 * Trilha em `surface`, nunca `border`: `brand` sobre `border` mede 2,80 no
 * claro (reprova até o teto de 3:1 de não-texto). Sobre `surface`, todo
 * preenchimento usado aqui já está coberto em `PARES`: `brand` (≥3, os dois
 * temas) e `positive`/`warning`/`danger` (≥4,5, já cobertos como TEXTO×FUNDOS
 * — `tom` aceita os quatro). Preenchimento por `transform: scaleX` (não
 * `width`) por performance: o RN 0.86 tem `transformOrigin` para escalar a
 * partir da esquerda sem cálculo manual de translação.
 */
export function ProgressBar({ valor, tom = "brand" }: Props) {
  const { cores } = useTema();
  // NaN primeiro (não é finito nem clampável); Infinity/-Infinity passam pelo
  // clamp normal (Math.min/Math.max) e caem em 1/0 — a ordem antiga
  // (`Number.isFinite` como guarda) tratava ±Infinity como NaN e devolvia a
  // barra VAZIA (`scaleX: 0`) bem no momento em que o valor estourou.
  const clampado = Number.isNaN(valor) ? 0 : Math.min(1, Math.max(0, valor));
  const corPreenchimento: keyof Paleta = tom;
  // `accessibilityValue.now` da RN quer inteiro: 0..100 arredondado, não 0..1.
  const agora = Math.round(clampado * 100);

  return (
    <View
      accessibilityRole="progressbar"
      accessibilityValue={{ min: 0, max: 100, now: agora }}
      style={{ height: 8, borderRadius: raio.sm, backgroundColor: cores.surface, overflow: "hidden" }}
    >
      <View
        style={{
          flex: 1,
          backgroundColor: cores[corPreenchimento],
          transform: [{ scaleX: clampado }],
          transformOrigin: "left",
        }}
      />
    </View>
  );
}
