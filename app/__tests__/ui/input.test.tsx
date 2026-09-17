import { fireEvent, render } from "@testing-library/react-native";
import * as Haptics from "expo-haptics";

import { Input } from "@/ui/componentes/Input";
import { TemaProvider } from "@/ui/tema";
import { claro, escuro } from "@/ui/tokens";

import { renderInterativo, renderNosDoisTemas } from "./_render";

describe("Input", () => {
  afterEach(() => jest.clearAllMocks());

  it("rótulo visível e repassado no accessibilityLabel", () => {
    const { claro: c } = renderNosDoisTemas(<Input rotulo="Nome" />);
    expect(c.getByText("Nome")).toBeTruthy();
    expect(c.getByLabelText("Nome")).toBeTruthy();
  });

  it("teto de fonte 1.3 no campo", () => {
    const { claro: c } = renderNosDoisTemas(<Input rotulo="Nome" />);
    expect(c.getByLabelText("Nome").props.maxFontSizeMultiplier).toBe(1.3);
  });

  it("contorno inkMuted por padrão; danger quando há erro", () => {
    const { claro: semErro } = renderNosDoisTemas(<Input rotulo="Nome" />);
    const { claro: comErro } = renderNosDoisTemas(<Input rotulo="Nome" erro="Obrigatório" />);
    expect(semErro.getByLabelText("Nome").props.style[1].borderColor).toBe(claro.inkMuted);
    expect(comErro.getByLabelText("Nome, erro: Obrigatório").props.style[1].borderColor).toBe(claro.danger);
  });

  it("desativado: editable=false e accessibilityState.disabled", () => {
    const { claro: c } = renderNosDoisTemas(<Input rotulo="Nome" desativado />);
    const campo = c.getByLabelText("Nome");
    expect(campo.props.editable).toBe(false);
    expect(campo.props.accessibilityState).toEqual({ disabled: true });
  });

  it("desativado usa inkMuted no texto e inkFaint no contorno (distinção visual do habilitado, não só o rótulo)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<Input rotulo="Nome" desativado />);
    expect(c.getByLabelText("Nome").props.style[1].color).toBe(claro.inkMuted);
    expect(c.getByLabelText("Nome").props.style[1].borderColor).toBe(claro.inkFaint);
    expect(e.getByLabelText("Nome").props.style[1].color).toBe(escuro.inkMuted);
    expect(e.getByLabelText("Nome").props.style[1].borderColor).toBe(escuro.inkFaint);
  });

  it("erro visível some do texto e some do erro anterior quando limpo", () => {
    const { claro: c } = renderNosDoisTemas(<Input rotulo="Nome" erro="Obrigatório" />);
    expect(c.getByText("Obrigatório")).toBeTruthy();
  });

  it("aviso() dispara 1 vez só na transição pra erro (reusa useAvisoAoErrar)", () => {
    const resultado = render(
      <TemaProvider esquema="light">
        <Input rotulo="Nome" />
      </TemaProvider>,
    );
    expect(Haptics.notificationAsync).not.toHaveBeenCalled();
    resultado.rerender(
      <TemaProvider esquema="light">
        <Input rotulo="Nome" erro="Obrigatório" />
      </TemaProvider>,
    );
    expect(Haptics.notificationAsync).toHaveBeenCalledTimes(1);
    resultado.rerender(
      <TemaProvider esquema="light">
        <Input rotulo="Nome" erro="Outro erro" />
      </TemaProvider>,
    );
    expect(Haptics.notificationAsync).toHaveBeenCalledTimes(1);
  });

  it("aceita TextInputProps (ex.: onChangeText) e chama de verdade", () => {
    const onChangeText = jest.fn();
    const resultado = renderInterativo(<Input rotulo="Nome" onChangeText={onChangeText} />);
    fireEvent.changeText(resultado.getByLabelText("Nome"), "Ana");
    expect(onChangeText).toHaveBeenCalledWith("Ana");
  });

  it("cor do texto muda entre os dois temas", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<Input rotulo="Nome" />);
    expect(c.getByLabelText("Nome").props.style[1].color).toBe(claro.ink);
    expect(e.getByLabelText("Nome").props.style[1].color).toBe(escuro.ink);
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<Input rotulo="Nome" erro="Obrigatório" />);
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
