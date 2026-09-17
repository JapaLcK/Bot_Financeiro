import { fireEvent } from "@testing-library/react-native";
import { Animated, StyleSheet } from "react-native";
import * as Haptics from "expo-haptics";

import { SegmentedControl } from "@/ui/componentes/SegmentedControl";
import { duracoes } from "@/ui/motion";
import { claro, escuro } from "@/ui/tokens";

import { renderInterativo, renderNosDoisTemas } from "./_render";

describe("SegmentedControl", () => {
  afterEach(() => jest.clearAllMocks());

  it("tocar uma opção diferente chama onChange com ela", () => {
    const onChange = jest.fn();
    const { getAllByRole } = renderInterativo(
      <SegmentedControl opcoes={["Semana", "Mês"]} valor="Semana" onChange={onChange} />,
    );
    fireEvent.press(getAllByRole("tab")[1]!);
    expect(onChange).toHaveBeenCalledWith("Mês");
  });

  it("tocar a opção JÁ ativa não dispara onChange nem selecao()", () => {
    const onChange = jest.fn();
    const { getAllByRole } = renderInterativo(
      <SegmentedControl opcoes={["Semana", "Mês"]} valor="Semana" onChange={onChange} />,
    );
    fireEvent.press(getAllByRole("tab")[0]!);
    expect(onChange).not.toHaveBeenCalled();
    expect(Haptics.selectionAsync).not.toHaveBeenCalled();
  });

  it("trocar de opção dispara selecao() exatamente 1 vez", () => {
    const { getAllByRole } = renderInterativo(
      <SegmentedControl opcoes={["Semana", "Mês"]} valor="Semana" onChange={jest.fn()} />,
    );
    fireEvent.press(getAllByRole("tab")[1]!);
    expect(Haptics.selectionAsync).toHaveBeenCalledTimes(1);
  });

  it("accessibilityState.selected marca a opção ativa", () => {
    const { claro: c } = renderNosDoisTemas(
      <SegmentedControl opcoes={["Semana", "Mês"]} valor="Mês" onChange={jest.fn()} />,
    );
    const tabs = c.getAllByRole("tab");
    expect(tabs[0]!.props.accessibilityState).toMatchObject({ selected: false });
    expect(tabs[1]!.props.accessibilityState).toMatchObject({ selected: true });
  });

  it("dois rótulos iguais: só o primeiro marca selected, e tocar o segundo dispara onChange (sem segmento morto)", () => {
    const onChange = jest.fn();
    const erroConsole = jest.spyOn(console, "error").mockImplementation(() => {});
    const { getAllByRole } = renderInterativo(<SegmentedControl opcoes={["Mês", "Mês"]} valor="Mês" onChange={onChange} />);
    const tabs = getAllByRole("tab");
    expect(tabs[0]!.props.accessibilityState).toMatchObject({ selected: true });
    expect(tabs[1]!.props.accessibilityState).toMatchObject({ selected: false });

    fireEvent.press(tabs[1]!);
    expect(onChange).toHaveBeenCalledWith("Mês");
    expect(Haptics.selectionAsync).toHaveBeenCalledTimes(1);

    const chaveDuplicada = erroConsole.mock.calls.some((args) => String(args[0]).toLowerCase().includes("key"));
    expect(chaveDuplicada).toBe(false);
    erroConsole.mockRestore();
  });

  it("segmento selecionado ganha borda inkMuted (o fundo surfaceRaised sozinho não distingue: 1,08/1,10 de contraste); não selecionado fica sem borda", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <SegmentedControl opcoes={["Semana", "Mês"]} valor="Mês" onChange={jest.fn()} />,
    );
    const indicadoresClaro = c.UNSAFE_getAllByType(Animated.View).map((v) => StyleSheet.flatten(v.props.style));
    // Cada segmento renderiza 2 `Animated.View` (o wrapper de pressão + o
    // indicador); o indicador é o de índice ímpar (1, 3, ...).
    const indicadorNaoSelecionado = indicadoresClaro[1]!;
    const indicadorSelecionado = indicadoresClaro[3]!;
    expect(indicadorNaoSelecionado.borderWidth).toBeFalsy();
    expect(indicadorSelecionado.borderWidth).toBe(1);
    expect(indicadorSelecionado.borderColor).toBe(claro.inkMuted);

    const indicadoresEscuro = e.UNSAFE_getAllByType(Animated.View).map((v) => StyleSheet.flatten(v.props.style));
    expect(indicadoresEscuro[3]!.borderColor).toBe(escuro.inkMuted);
  });

  it("usa usePressao: onPressIn anima a escala para 0.97 (padrão dos quatro componentes interativos)", () => {
    const timing = jest.spyOn(Animated, "timing").mockReturnValue({ start: jest.fn() } as unknown as Animated.CompositeAnimation);
    const { getAllByRole } = renderInterativo(
      <SegmentedControl opcoes={["Semana", "Mês"]} valor="Semana" onChange={jest.fn()} />,
    );
    fireEvent(getAllByRole("tab")[0]!, "pressIn");
    expect(timing).toHaveBeenCalledWith(expect.any(Animated.Value), expect.objectContaining({ toValue: 0.97 }));
    timing.mockRestore();
  });

  it("crossfade do indicador anima com duracoes.feedback (150ms)", () => {
    const timing = jest.spyOn(Animated, "timing");
    renderInterativo(<SegmentedControl opcoes={["Semana", "Mês"]} valor="Semana" onChange={jest.fn()} />);
    expect(timing).toHaveBeenCalledWith(expect.any(Animated.Value), expect.objectContaining({ duration: duracoes.feedback }));
    timing.mockRestore();
  });

  it("toque >= 44pt em cada segmento", () => {
    const { claro: c } = renderNosDoisTemas(
      <SegmentedControl opcoes={["Semana", "Mês"]} valor="Semana" onChange={jest.fn()} />,
    );
    for (const tab of c.getAllByRole("tab")) {
      expect(tab.props.style.minHeight).toBeGreaterThanOrEqual(44);
    }
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <SegmentedControl opcoes={["Semana", "Mês", "Ano"]} valor="Mês" onChange={jest.fn()} />,
    );
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
