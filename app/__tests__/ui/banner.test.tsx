import { fireEvent } from "@testing-library/react-native";
import { AccessibilityInfo, View } from "react-native";

import { Banner } from "@/ui/componentes/Banner";
import { Icone } from "@/ui/componentes/Icone";
import { claro, escuro } from "@/ui/tokens";

import { renderInterativo, renderNosDoisTemas } from "./_render";

describe("Banner", () => {
  afterEach(() => jest.clearAllMocks());

  it("M3 — danger anuncia para o leitor de tela; info/warning não interrompem a leitura", () => {
    renderNosDoisTemas(<Banner mensagem="Sua fatura fecha em 3 dias." />);
    expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalled();

    renderNosDoisTemas(<Banner tom="warning" mensagem="Alguns lançamentos podem estar desatualizados." />);
    expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalled();

    renderNosDoisTemas(<Banner tom="danger" mensagem="Conexão perdida." />);
    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith("Conexão perdida.");
  });

  it("info (padrão): sem accessibilityRole alert, ícone Info em tom ink", () => {
    const { claro: c } = renderNosDoisTemas(<Banner mensagem="Sua fatura fecha em 3 dias." />);
    expect(c.UNSAFE_getAllByType(View)[0]!.props.accessibilityRole).toBeUndefined();
    expect(c.UNSAFE_getByType(Icone).props).toMatchObject({ nome: "Info", tom: "ink" });
  });

  it("warning: ícone WarningCircle em tom warning", () => {
    const { claro: c } = renderNosDoisTemas(<Banner tom="warning" mensagem="Alguns lançamentos podem estar desatualizados." />);
    expect(c.UNSAFE_getByType(Icone).props).toMatchObject({ nome: "WarningCircle", tom: "warning" });
  });

  it("danger: accessibilityRole alert e ícone WarningOctagon em tom danger", () => {
    const { claro: c } = renderNosDoisTemas(<Banner tom="danger" mensagem="Conexão perdida." />);
    expect(c.UNSAFE_getAllByType(View)[0]!.props.accessibilityRole).toBe("alert");
    expect(c.UNSAFE_getByType(Icone).props).toMatchObject({ nome: "WarningOctagon", tom: "danger" });
  });

  it("titulo só aparece quando passado", () => {
    const { claro: semTitulo } = renderNosDoisTemas(<Banner mensagem="x" />);
    expect(semTitulo.queryByText("Título")).toBeNull();

    const { claro: comTitulo } = renderNosDoisTemas(<Banner titulo="Título" mensagem="x" />);
    expect(comTitulo.getByText("Título")).toBeTruthy();
  });

  it("acao renderiza um botão que chama o callback", () => {
    const onPress = jest.fn();
    const { getByRole } = renderInterativo(<Banner mensagem="x" acao={{ rotulo: "Reconectar", onPress }} />);
    fireEvent.press(getByRole("button"));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it("fundo é sempre `surface`, nos dois temas", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<Banner mensagem="x" />);
    expect(c.UNSAFE_getAllByType(View)[0]!.props.style.backgroundColor).toBe(claro.surface);
    expect(e.UNSAFE_getAllByType(View)[0]!.props.style.backgroundColor).toBe(escuro.surface);
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <Banner tom="danger" titulo="Conexão perdida" mensagem="Reconecte para continuar." acao={{ rotulo: "Reconectar", onPress: () => {} }} />,
    );
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
