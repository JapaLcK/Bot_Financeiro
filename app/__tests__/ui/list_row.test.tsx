import { fireEvent } from "@testing-library/react-native";
import { AccessibilityInfo, ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { Icone } from "@/ui/componentes/Icone";
import { GrupoDeLinhas, ListRow } from "@/ui/componentes/ListRow";

import { claro } from "@/ui/tokens";

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

  it("carregando: desativa, marca busy, mostra spinner no lugar do chevron e não dispara onPress", () => {
    const onPress = jest.fn();
    const { getByRole, UNSAFE_queryAllByType } = renderInterativo(<ListRow titulo="Sair" chevron carregando onPress={onPress} />);
    expect(getByRole("button").props.accessibilityState).toMatchObject({ disabled: true, busy: true });
    fireEvent.press(getByRole("button"));
    expect(onPress).not.toHaveBeenCalled();
    expect(UNSAFE_queryAllByType(ActivityIndicator)).toHaveLength(1);
    expect(UNSAFE_queryAllByType(Icone)).toHaveLength(0);
  });

  it("desativado: disabled sem busy, esmaecido e sem disparar onPress; ativo não leva accessibilityState", () => {
    const onPress = jest.fn();
    const r = renderInterativo(<ListRow titulo="Bancos" chevron desativado onPress={onPress} />);
    expect(r.getByRole("button").props.accessibilityState).toMatchObject({ disabled: true, busy: false });
    fireEvent.press(r.getByRole("button"));
    expect(onPress).not.toHaveBeenCalled();
    r.unmount();
    const ativo = renderInterativo(<ListRow titulo="Bancos" onPress={onPress} />);
    expect(ativo.getByRole("button").props.accessibilityState?.disabled).toBeFalsy();
  });

  it('tom="danger": título em danger e sem chevron', () => {
    const { getByText, UNSAFE_queryAllByType } = renderInterativo(<ListRow titulo="Sair" tom="danger" chevron onPress={jest.fn()} />);
    expect(StyleSheet.flatten(getByText("Sair").props.style).color).toBe(claro.danger);
    expect(UNSAFE_queryAllByType(Icone)).toHaveLength(0);
  });

  it("GrupoDeLinhas: um separador entre cada par de linhas, nenhum com uma só", () => {
    const tres = renderInterativo(<GrupoDeLinhas><ListRow titulo="a" /><ListRow titulo="b" /><ListRow titulo="c" /></GrupoDeLinhas>);
    expect(tres.getAllByTestId("grupo-separador")).toHaveLength(2);
    tres.unmount();
    const uma = renderInterativo(<GrupoDeLinhas><ListRow titulo="a" />{false}</GrupoDeLinhas>);
    expect(uma.queryAllByTestId("grupo-separador")).toHaveLength(0);
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <ListRow titulo="Mercado" subtitulo="Hoje" icone="Wallet" chevron onPress={jest.fn()} />,
    );
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
