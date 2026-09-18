import { fireEvent } from "@testing-library/react-native";
import { Image } from "react-native";

import { EmptyState } from "@/ui/componentes/EmptyState";
import { STICKERS } from "@/ui/stickers";
import { escuro } from "@/ui/tokens";

import { renderInterativo, renderNosDoisTemas } from "./_render";

describe("EmptyState", () => {
  it("mostra o sticker certo e a frase", () => {
    const { claro: c } = renderNosDoisTemas(<EmptyState sticker="thinking" frase="Nada por aqui." />);
    expect(c.getByText("Nada por aqui.")).toBeTruthy();
    expect(c.UNSAFE_getByType(Image).props.source).toBe(STICKERS.thinking);
  });

  it("sem halo no claro; halo (View com fundo `surface`) no escuro", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<EmptyState sticker="hello" frase="x" />);
    // No claro, a View que envolve a Image não tem estilo (undefined) — sem halo.
    const wrapperClaro = c.UNSAFE_getByType(Image).parent!;
    expect(wrapperClaro.props.style).toBeUndefined();

    const wrapperEscuro = e.UNSAFE_getByType(Image).parent!;
    expect(wrapperEscuro.props.style).toMatchObject({ backgroundColor: escuro.surface });
  });

  it("sem acao, sem botão", () => {
    const { claro: c } = renderNosDoisTemas(<EmptyState sticker="chill" frase="x" />);
    expect(c.queryByRole("button")).toBeNull();
  });

  it("com acao, o botão chama o callback", () => {
    const onPress = jest.fn();
    const resultado = renderInterativo(<EmptyState sticker="chill" frase="x" acao={{ rotulo: "Limpar filtro", onPress }} />);
    fireEvent.press(resultado.getByRole("button"));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <EmptyState sticker="thinking" frase="Nada por aqui com esse filtro." acao={{ rotulo: "Limpar filtro", onPress: () => {} }} />,
    );
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
