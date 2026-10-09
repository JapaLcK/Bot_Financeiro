import { fireEvent } from "@testing-library/react-native";
import { Modal, Platform, StyleSheet, Text } from "react-native";

import { Folha } from "@/features/painel/base";

import { METRICAS_DE_TESTE, renderComAreaSegura } from "./_render";

const montar = (fechar = jest.fn()) => ({ fechar, ...renderComAreaSegura(<Folha titulo="Minha conta" aberta fechar={fechar}><Text>x</Text></Folha>) });

describe("Folha (pageSheet do painel)", () => {
  it("Folha não soma o inset do topo (sobCabecalho)", () => {
    expect(METRICAS_DE_TESTE.insets.top).toBeGreaterThan(0);
    const { claro: c } = montar();
    const tela = c.getByTestId("tela");
    expect(StyleSheet.flatten([tela.props.style, tela.props.contentContainerStyle]).paddingTop).toBe(0);
  });

  it("no Android (sem pageSheet, Modal em tela cheia) mantém o inset do topo", () => {
    jest.replaceProperty(Platform, "OS", "android");
    const { claro: c } = montar();
    const tela = c.getByTestId("tela");
    expect(StyleSheet.flatten([tela.props.style, tela.props.contentContainerStyle]).paddingTop).toBe(METRICAS_DE_TESTE.insets.top);
    jest.restoreAllMocks();
  });

  it("aceita arrastar para fechar e o arrasto chama fechar", () => {
    const { claro: c, fechar } = montar();
    const modal = c.UNSAFE_getByType(Modal);
    expect(modal.props.presentationStyle).toBe("pageSheet");
    expect(modal.props.allowSwipeDismissal).toBe(true);
    modal.props.onRequestClose();
    expect(fechar).toHaveBeenCalledTimes(1);
  });

  it("Fechar é botão só de ícone, mantém o rótulo e chama fechar", () => {
    // `escuro`: a RNTL só despacha evento na ÚLTIMA árvore renderizada (ver `_render.tsx`).
    const { escuro: c, fechar } = montar();
    fireEvent.press(c.getByRole("button", { name: "Fechar" }));
    expect(fechar).toHaveBeenCalledTimes(1);
    expect(c.queryByText("Fechar")).toBeNull();
  });
});
