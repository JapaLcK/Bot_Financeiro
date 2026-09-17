import { StyleSheet, Text, View } from "react-native";

import { Card } from "@/ui/componentes/Card";
import { claro, escuro } from "@/ui/tokens";

import { renderNosDoisTemas } from "./_render";

function estiloDe(resultado: ReturnType<typeof renderNosDoisTemas>["claro"]) {
  return StyleSheet.flatten(resultado.UNSAFE_getByType(View).props.style) as {
    backgroundColor?: string;
    shadowOpacity?: number;
    shadowColor?: string;
    borderWidth?: number;
  };
}

describe("Card", () => {
  it("surface (padrão): fundo `surface`, sem sombra, nos dois temas", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <Card>
        <Text>x</Text>
      </Card>,
    );
    expect(estiloDe(c).backgroundColor).toBe(claro.surface);
    expect(estiloDe(c).shadowOpacity).toBeUndefined();
    expect(estiloDe(e).backgroundColor).toBe(escuro.surface);
    expect(estiloDe(e).shadowOpacity).toBeUndefined();
  });

  it("raised no claro: fundo `surfaceRaised` + sombra 6%", () => {
    const { claro: c } = renderNosDoisTemas(
      <Card elevacao="raised">
        <Text>x</Text>
      </Card>,
    );
    const estilo = estiloDe(c);
    expect(estilo.backgroundColor).toBe(claro.surfaceRaised);
    expect(estilo.shadowOpacity).toBe(0.06);
    expect(estilo.shadowColor).toBe(claro.shadow);
    expect(estilo.borderWidth).toBe(1);
  });

  it("raised no escuro: fundo `surfaceRaised`, SEM sombra", () => {
    const { escuro: e } = renderNosDoisTemas(
      <Card elevacao="raised">
        <Text>x</Text>
      </Card>,
    );
    const estilo = estiloDe(e);
    expect(estilo.backgroundColor).toBe(escuro.surfaceRaised);
    expect(estilo.shadowOpacity).toBeUndefined();
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <Card elevacao="raised">
        <Text>conteúdo</Text>
      </Card>,
    );
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
