import { fireEvent } from "@testing-library/react-native";
import { ActivityIndicator, Animated, StyleSheet } from "react-native";

import { Button } from "@/ui/componentes/Button";
import { claro, escuro } from "@/ui/tokens";

import { renderInterativo, renderNosDoisTemas } from "./_render";

/** Estilo do `Animated.View` visual dentro do `Pressable` (único, sempre existe). */
function estiloVisual(resultado: ReturnType<typeof renderInterativo>) {
  return StyleSheet.flatten(resultado.UNSAFE_getByType(Animated.View).props.style);
}

describe("Button", () => {
  it("onPress chama o callback num toque normal", () => {
    const onPress = jest.fn();
    const { getByRole } = renderInterativo(<Button rotulo="Confirmar" onPress={onPress} />);
    fireEvent.press(getByRole("button"));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it("desativado bloqueia o onPress", () => {
    const onPress = jest.fn();
    const { getByRole } = renderInterativo(<Button rotulo="Confirmar" onPress={onPress} desativado />);
    fireEvent.press(getByRole("button"));
    expect(onPress).not.toHaveBeenCalled();
  });

  it("carregando bloqueia o onPress", () => {
    const onPress = jest.fn();
    const { getByRole } = renderInterativo(<Button rotulo="Confirmar" onPress={onPress} carregando />);
    fireEvent.press(getByRole("button"));
    expect(onPress).not.toHaveBeenCalled();
  });

  it("accessibilityState: carregando é busy (RN também marca disabled — bloqueado de fato)", () => {
    const { getByRole } = renderInterativo(<Button rotulo="x" onPress={jest.fn()} carregando />);
    expect(getByRole("button").props.accessibilityState).toMatchObject({ disabled: true, busy: true });
  });

  it("accessibilityState: sem desativado nem carregando, nem busy nem disabled", () => {
    const { getByRole } = renderInterativo(<Button rotulo="x" onPress={jest.fn()} />);
    expect(getByRole("button").props.accessibilityState).toMatchObject({ disabled: false, busy: false });
  });

  it("tamanho M tem minHeight 44, L tem 52", () => {
    const { claro: cM } = renderNosDoisTemas(<Button rotulo="x" onPress={jest.fn()} tamanho="M" />);
    const { claro: cL } = renderNosDoisTemas(<Button rotulo="x" onPress={jest.fn()} tamanho="L" />);
    expect(estiloVisual(cM).minHeight).toBe(44);
    expect(estiloVisual(cL).minHeight).toBe(52);
  });

  it("primary usa acao/onAcao, secondary usa contorno inkMuted, ghost usa brandInk, danger usa danger/onDanger", () => {
    const { claro: c } = renderNosDoisTemas(<Button rotulo="x" onPress={jest.fn()} variante="primary" />);
    expect(estiloVisual(c).backgroundColor).toBe(claro.acao);

    const { claro: cSec } = renderNosDoisTemas(<Button rotulo="x" onPress={jest.fn()} variante="secondary" />);
    expect(estiloVisual(cSec).borderColor).toBe(claro.inkMuted);

    const { claro: cGhost } = renderNosDoisTemas(<Button rotulo="x" onPress={jest.fn()} variante="ghost" />);
    expect(estiloVisual(cGhost).backgroundColor).toBe("transparent");

    const { escuro: eDanger } = renderNosDoisTemas(<Button rotulo="x" onPress={jest.fn()} variante="danger" />);
    expect(estiloVisual(eDanger).backgroundColor).toBe(escuro.danger);
  });

  it("carregando mostra ActivityIndicator e o texto fica invisível (largura não pula)", () => {
    const { claro: c } = renderNosDoisTemas(<Button rotulo="Confirmar" onPress={jest.fn()} carregando />);
    expect(c.UNSAFE_getByType(ActivityIndicator)).toBeTruthy();
    const texto = c.getByText("Confirmar");
    const estilo = StyleSheet.flatten(texto.props.style);
    expect(estilo.opacity).toBe(0);
  });

  it("nunca dispara haptic (Button não tem toque tátil)", () => {
    const Haptics = jest.requireMock("expo-haptics") as { selectionAsync: jest.Mock };
    Haptics.selectionAsync.mockClear();
    const { getByRole } = renderInterativo(<Button rotulo="x" onPress={jest.fn()} />);
    fireEvent.press(getByRole("button"));
    expect(Haptics.selectionAsync).not.toHaveBeenCalled();
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<Button rotulo="Confirmar" onPress={jest.fn()} variante="primary" />);
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
