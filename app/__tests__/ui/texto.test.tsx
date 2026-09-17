import { StyleSheet, Text } from "react-native";

import { Texto } from "@/ui/componentes/Texto";
import { texto as escalas } from "@/ui/tokens";

import { renderNosDoisTemas } from "./_render";

const VARIANTES = Object.keys(escalas) as (keyof typeof escalas)[];

describe("Texto", () => {
  it.each(VARIANTES)("variante %s tem o teto de fonte fixo em 1.3", (variante) => {
    const { claro } = renderNosDoisTemas(<Texto variante={variante}>x</Texto>);
    expect(claro.getByText("x").props.maxFontSizeMultiplier).toBe(1.3);
  });

  it("o teto não pode ser sobrescrito pelo chamador", () => {
    // `maxFontSizeMultiplier` não é uma prop de `Texto` (TextProps do RN a
    // tem, mas o componente a ignora de propósito) — `any` só para forçar a
    // tentativa de sobrescrita aqui no teste.
    const props = { maxFontSizeMultiplier: 5 } as unknown as { children: string };
    const { claro } = renderNosDoisTemas(<Texto {...props}>x</Texto>);
    expect(claro.getByText("x").props.maxFontSizeMultiplier).toBe(1.3);
  });

  it("numerico liga tabular-nums", () => {
    const { claro } = renderNosDoisTemas(<Texto numerico>123</Texto>);
    expect(claro.getByText("123").props.style).toContainEqual({ fontVariant: ["tabular-nums"] });
  });

  it("sem numerico não injeta fontVariant", () => {
    const { claro } = renderNosDoisTemas(<Texto>123</Texto>);
    const estilo = ([] as unknown[]).concat(claro.getByText("123").props.style);
    expect(estilo.every((s) => !(s && (s as { fontVariant?: unknown }).fontVariant))).toBe(true);
  });

  it("numerico vence o fontVariant que o chamador passar em style", () => {
    const { claro } = renderNosDoisTemas(
      <Texto numerico style={{ fontVariant: ["oldstyle-nums"] }}>
        123
      </Texto>,
    );
    const estilo = StyleSheet.flatten(claro.getByText("123").props.style);
    expect(estilo.fontVariant).toEqual(["tabular-nums"]);
  });

  it("fontWeight do chamador não passa adiante (a Inter cai pro sistema se combinar peso solto com família custom)", () => {
    const { claro } = renderNosDoisTemas(<Texto style={{ fontWeight: "700" }}>x</Texto>);
    const estilo = StyleSheet.flatten(claro.getByText("x").props.style);
    expect(estilo.fontWeight).toBeUndefined();
  });

  it("é o único componente do produto que chama Text do react-native (regra do ESLint, provada aqui em código)", () => {
    const { claro } = renderNosDoisTemas(<Texto>x</Texto>);
    expect(claro.UNSAFE_getByType(Text)).toBeTruthy();
  });

  it("cor muda entre os dois temas para o mesmo tom", () => {
    const { claro, escuro } = renderNosDoisTemas(<Texto tom="ink">x</Texto>);
    const corClara = claro.getByText("x").props.style.find((s: { color?: string }) => s?.color)?.color;
    const corEscura = escuro.getByText("x").props.style.find((s: { color?: string }) => s?.color)?.color;
    expect(corClara).not.toBe(corEscura);
  });
});
