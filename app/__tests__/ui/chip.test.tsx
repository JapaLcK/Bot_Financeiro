import { fireEvent } from "@testing-library/react-native";
import { Animated, StyleSheet } from "react-native";
import * as Haptics from "expo-haptics";

import { Chip } from "@/ui/componentes/Chip";
import { claro, escuro } from "@/ui/tokens";

import { renderInterativo, renderNosDoisTemas } from "./_render";

/** Estilo do `Animated.View` visual dentro do `Pressable` (único, sempre existe). */
function estiloVisual(resultado: ReturnType<typeof renderInterativo>) {
  return StyleSheet.flatten(resultado.UNSAFE_getByType(Animated.View).props.style);
}

describe("Chip", () => {
  afterEach(() => jest.clearAllMocks());

  it("onPress chama o callback", () => {
    const onPress = jest.fn();
    const { getByRole } = renderInterativo(<Chip rotulo="Mercado" selecionado={false} onPress={onPress} />);
    fireEvent.press(getByRole("button"));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it("todo toque dispara selecao() (mesmo virando não-selecionado)", () => {
    const { getByRole } = renderInterativo(<Chip rotulo="Mercado" selecionado onPress={jest.fn()} />);
    fireEvent.press(getByRole("button"));
    expect(Haptics.selectionAsync).toHaveBeenCalledTimes(1);
  });

  it("accessibilityState.selected reflete a prop", () => {
    const { claro: sel } = renderNosDoisTemas(<Chip rotulo="x" selecionado onPress={jest.fn()} />);
    const { claro: naoSel } = renderNosDoisTemas(<Chip rotulo="x" selecionado={false} onPress={jest.fn()} />);
    expect(sel.getByRole("button").props.accessibilityState).toMatchObject({ selected: true });
    expect(naoSel.getByRole("button").props.accessibilityState).toMatchObject({ selected: false });
  });

  it("selecionado usa brandSoft/brandInk; não selecionado usa surface/inkMuted", () => {
    const { claro: sel } = renderNosDoisTemas(<Chip rotulo="x" selecionado onPress={jest.fn()} />);
    expect(estiloVisual(sel).backgroundColor).toBe(claro.brandSoft);
    expect(sel.getByText("x").props.style.some((s: { color?: string }) => s?.color === claro.brandInk)).toBe(true);

    const { escuro: naoSel } = renderNosDoisTemas(<Chip rotulo="x" selecionado={false} onPress={jest.fn()} />);
    expect(estiloVisual(naoSel).backgroundColor).toBe(escuro.surface);
  });

  it("altura de toque de 44pt vem do tamanho real, não de hitSlop (que o pai recorta)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <Chip rotulo="x" selecionado={false} onPress={jest.fn()} />,
    );
    expect(estiloVisual(c).minHeight).toBeGreaterThanOrEqual(44);
    expect(estiloVisual(e).minHeight).toBeGreaterThanOrEqual(44);
    expect(c.getByRole("button").props.hitSlop).toBeUndefined();
  });

  it("minWidth 44 no visual, mesmo com rótulo de 1 caractere", () => {
    const { claro: c } = renderNosDoisTemas(<Chip rotulo="1" selecionado={false} onPress={jest.fn()} />);
    expect(estiloVisual(c).minWidth).toBeGreaterThanOrEqual(44);
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<Chip rotulo="Mercado" selecionado onPress={jest.fn()} />);
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
