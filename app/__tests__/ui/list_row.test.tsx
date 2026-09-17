import { fireEvent } from "@testing-library/react-native";
import { AccessibilityInfo, StyleSheet, Text, View } from "react-native";

import { Icone } from "@/ui/componentes/Icone";
import { ListRow } from "@/ui/componentes/ListRow";

import { renderInterativo, renderNosDoisTemas } from "./_render";

describe("ListRow", () => {
  it("sem onPress: sem accessibilityRole button (não é um controle)", () => {
    const { claro: c } = renderNosDoisTemas(<ListRow titulo="Mercado" />);
    expect(c.queryByRole("button")).toBeNull();
  });

  it("com onPress: accessibilityRole button e o toque chama o callback", () => {
    const onPress = jest.fn();
    const { getByRole } = renderInterativo(<ListRow titulo="Mercado" onPress={onPress} />);
    fireEvent.press(getByRole("button"));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it("subtitulo, trailing e chevron só aparecem quando passados", () => {
    const { claro: minimo } = renderNosDoisTemas(<ListRow titulo="Mercado" />);
    expect(minimo.queryByText("R$ 12,00")).toBeNull();

    const { claro: completo } = renderNosDoisTemas(
      <ListRow titulo="Mercado" subtitulo="Hoje" trailing={<Text>R$ 12,00</Text>} chevron />,
    );
    expect(completo.getByText("Hoje")).toBeTruthy();
    expect(completo.getByText("R$ 12,00")).toBeTruthy();
    expect(completo.UNSAFE_getAllByType(Icone)).toHaveLength(1); // só o chevron, sem `icone`
  });

  it("ícone leading aparece quando `icone` é passado", () => {
    const { claro: c } = renderNosDoisTemas(<ListRow titulo="Mercado" icone="Wallet" chevron />);
    expect(c.UNSAFE_getAllByType(Icone)).toHaveLength(2);
  });

  it("sem onPress, não chama usePressao (sem assinatura de reduceMotionChanged à toa)", () => {
    const addSpy = jest.spyOn(AccessibilityInfo, "addEventListener");
    renderNosDoisTemas(<ListRow titulo="Mercado" />);
    expect(addSpy).not.toHaveBeenCalled();
    addSpy.mockRestore();
  });

  it("com onPress, usePressao roda de verdade (assina reduceMotionChanged)", () => {
    const addSpy = jest.spyOn(AccessibilityInfo, "addEventListener");
    renderNosDoisTemas(<ListRow titulo="Mercado" onPress={jest.fn()} />);
    expect(addSpy).toHaveBeenCalled();
    addSpy.mockRestore();
  });

  it("minHeight >= 56", () => {
    // Sem `onPress`, a linha é um `View` puro (achado C1: `usePressao()` não
    // pode rodar sem um pressionável de verdade — ver `LinhaPressionavel`).
    const { claro: c } = renderNosDoisTemas(<ListRow titulo="Mercado" />);
    const estilo = StyleSheet.flatten(c.UNSAFE_getByType(View).props.style);
    expect(estilo.minHeight).toBeGreaterThanOrEqual(56);
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <ListRow titulo="Mercado" subtitulo="Hoje" icone="Wallet" chevron onPress={jest.fn()} />,
    );
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
