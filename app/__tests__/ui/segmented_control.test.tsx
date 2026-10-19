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

  it("`acessiveis` vira o accessibilityLabel de cada tab (o visível pode ser abreviado); sem ele, o rótulo é o texto", () => {
    const { getAllByRole } = renderInterativo(<SegmentedControl opcoes={["out", "set"]} acessiveis={["outubro de 2026", "setembro de 2026"]} valor="out" onChange={jest.fn()} />);
    expect(getAllByRole("tab").map((t) => t.props.accessibilityLabel)).toEqual(["outubro de 2026", "setembro de 2026"]);
    const { getAllByRole: sem } = renderInterativo(<SegmentedControl opcoes={["out", "set"]} valor="out" onChange={jest.fn()} />);
    expect(sem("tab").map((t) => t.props.accessibilityLabel)).toEqual([undefined, undefined]);
  });

  it("dois rótulos iguais: só o primeiro marca selected, e tocar o segundo chama onChange — mas NÃO fica selecionável (rótulo repetido não é suportado, ver JSDoc de `opcoes`)", () => {
    const onChange = jest.fn();
    const erroConsole = jest.spyOn(console, "error").mockImplementation(() => {});
    const { getAllByRole } = renderInterativo(<SegmentedControl opcoes={["Mês", "Mês"]} valor="Mês" onChange={onChange} />);
    const tabs = getAllByRole("tab");
    expect(tabs[0]!.props.accessibilityState).toMatchObject({ selected: true });
    expect(tabs[1]!.props.accessibilityState).toMatchObject({ selected: false });

    // Tocar no segundo chama onChange com o MESMO texto que já está em
    // `valor` — não é prova de que ele vira selecionável: um pai controlado
    // por `useState("Mês")` recebe esse mesmo valor de volta, não re-renderiza,
    // e o destaque continua preso no primeiro.
    fireEvent.press(tabs[1]!);
    expect(onChange).toHaveBeenCalledWith("Mês");
    expect(Haptics.selectionAsync).toHaveBeenCalledTimes(1);

    const chaveDuplicada = erroConsole.mock.calls.some((args) => String(args[0]).toLowerCase().includes("key"));
    expect(chaveDuplicada).toBe(false);
    erroConsole.mockRestore();
  });

  it("nenhum segmento tem borda (PDF); o selecionado é marcado pelo texto ink contra inkFaint", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <SegmentedControl opcoes={["Semana", "Mês"]} valor="Mês" onChange={jest.fn()} />,
    );
    const indicadoresClaro = c.UNSAFE_getAllByType(Animated.View).map((v) => StyleSheet.flatten(v.props.style));
    // Cada segmento renderiza 2 `Animated.View` (o wrapper de pressão + o
    // indicador); o indicador é o de índice ímpar (1, 3, ...).
    const indicadorNaoSelecionado = indicadoresClaro[1]!;
    const indicadorSelecionado = indicadoresClaro[3]!;
    expect(indicadorNaoSelecionado.borderWidth).toBeFalsy();
    expect(indicadorSelecionado.borderWidth).toBeFalsy();
    expect(indicadorSelecionado.backgroundColor).toBe(claro.surfaceRaised);
    expect(StyleSheet.flatten(c.getByText("Mês").props.style).color).toBe(claro.ink);
    expect(StyleSheet.flatten(c.getByText("Semana").props.style).color).toBe(claro.inkFaint);

    const indicadoresEscuro = e.UNSAFE_getAllByType(Animated.View).map((v) => StyleSheet.flatten(v.props.style));
    expect(indicadoresEscuro[3]!.borderWidth).toBeFalsy();
    expect(StyleSheet.flatten(e.getByText("Semana").props.style).color).toBe(escuro.inkFaint);
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
