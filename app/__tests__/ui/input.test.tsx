import { fireEvent, render } from "@testing-library/react-native";
import * as Haptics from "expo-haptics";
import { AccessibilityInfo } from "react-native";

import { Icone } from "@/ui/componentes/Icone";
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

  it("M3 — erro anuncia para o leitor de tela (AccessibilityInfo), não só o accessibilityLabel", () => {
    const resultado = render(
      <TemaProvider esquema="light">
        <Input rotulo="Senha" />
      </TemaProvider>,
    );
    expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalled();
    resultado.rerender(
      <TemaProvider esquema="light">
        <Input rotulo="Senha" erro="E-mail ou senha incorretos." />
      </TemaProvider>,
    );
    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith("E-mail ou senha incorretos.");

    // Uma SEGUNDA falha (texto diferente) também precisa ser ouvida — não só
    // a transição de "sem erro" para "com erro".
    resultado.rerender(
      <TemaProvider esquema="light">
        <Input rotulo="Senha" erro="Muitas tentativas." />
      </TemaProvider>,
    );
    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith("Muitas tentativas.");
    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledTimes(2);
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

  describe("icone", () => {
    it("sem icone: o campo permanece com o MESMO estilo de antes (o snapshot acima não muda)", () => {
      const { claro: c } = renderNosDoisTemas(<Input rotulo="Nome" />);
      expect(c.UNSAFE_queryAllByType(Icone)).toHaveLength(0);
    });

    it("com icone: renderiza o ícone em tom inkMuted, dentro de um contorno próprio", () => {
      const { claro: c } = renderNosDoisTemas(<Input rotulo="E-mail" icone="Envelope" />);
      expect(c.UNSAFE_getByType(Icone).props).toMatchObject({ nome: "Envelope", tom: "inkMuted" });
    });

    it("com icone: o TextInput não tem mais o próprio contorno — quem borda é a linha ao redor do ícone", () => {
      const { claro: c } = renderNosDoisTemas(<Input rotulo="E-mail" icone="Envelope" />);
      const campo = c.getByLabelText("E-mail");
      expect(campo.props.style[1].borderWidth).toBeUndefined();
    });

    it("aceita digitação normalmente com icone", () => {
      const onChangeText = jest.fn();
      const { getByLabelText } = renderInterativo(<Input rotulo="E-mail" icone="Envelope" onChangeText={onChangeText} />);
      fireEvent.changeText(getByLabelText("E-mail"), "ana@x.com");
      expect(onChangeText).toHaveBeenCalledWith("ana@x.com");
    });
  });
});
