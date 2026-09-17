import { StyleSheet, Text, type TextProps } from "react-native";

import { useTema } from "@/ui/tema";
import { texto, type Paleta } from "@/ui/tokens";

type Variante = keyof typeof texto;
type Tom = keyof Paleta;

// Sem `maxFontSizeMultiplier` no tipo: o teto é fixo, e aceitar a prop para
// ignorá-la em silêncio faria o chamador achar que o ajuste valeu.
interface Props extends Omit<TextProps, "maxFontSizeMultiplier"> {
  variante?: Variante;
  tom?: Tom;
  /** Só para valor numérico: liga `tabular-nums`, para dígito não "dançar" ao trocar. */
  numerico?: boolean;
}

/**
 * ÚNICO lugar do produto que chama `Text` do react-native diretamente — o
 * ESLint barra o resto (regra em `src/ui/componentes/**`). É o que impede uma
 * tela de esquecer o teto de fonte ou o `tabular-nums`.
 *
 * `maxFontSizeMultiplier` fixo em 1.3 e NÃO pode ser sobrescrito pelo
 * chamador: acima disso o texto quebra layout em vez de só crescer, e não é
 * decisão de tela por tela.
 */
export function Texto({ variante = "corpo", tom = "ink", numerico = false, style, ...resto }: Props) {
  const { cores } = useTema();
  // `fontWeight` do chamador nunca passa adiante: peso + família custom cai
  // de volta no sistema no Android (mesmo motivo do comentário em
  // `tokens.ts`) — o peso vem da família de `texto[variante]`, não de um
  // `fontWeight` solto.
  const { fontWeight: _fontWeightDoChamador, ...estiloDoChamador } = StyleSheet.flatten(style) ?? {};
  return (
    <Text
      {...resto}
      maxFontSizeMultiplier={1.3}
      style={[
        texto[variante],
        { color: cores[tom] },
        estiloDoChamador,
        // Depois de `estiloDoChamador`, não antes: um `style={{ fontVariant:
        // [...] }}` do chamador não pode apagar o `tabular-nums` de um Texto
        // `numerico`.
        numerico ? { fontVariant: ["tabular-nums"] } : null,
      ]}
    />
  );
}
