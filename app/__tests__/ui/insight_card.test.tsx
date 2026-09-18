import { fireEvent } from "@testing-library/react-native";
import { Image } from "react-native";

import { InsightCard } from "@/ui/componentes/InsightCard";
import { STICKERS } from "@/ui/stickers";

import { renderInterativo, renderNosDoisTemas } from "./_render";

describe("InsightCard", () => {
  it("mostra titulo e mensagem", () => {
    const { claro: c } = renderNosDoisTemas(<InsightCard titulo="Meta" mensagem="Faltam R$ 120,00." />);
    expect(c.getByText("Meta")).toBeTruthy();
    expect(c.getByText("Faltam R$ 120,00.")).toBeTruthy();
  });

  it("sem sticker, sem Image", () => {
    const { claro: c } = renderNosDoisTemas(<InsightCard titulo="x" mensagem="y" />);
    expect(c.UNSAFE_queryByType(Image)).toBeNull();
  });

  it("com sticker, mostra a Image certa", () => {
    const { claro: c } = renderNosDoisTemas(<InsightCard sticker="goal" titulo="x" mensagem="y" />);
    expect(c.UNSAFE_getByType(Image).props.source).toBe(STICKERS.goal);
  });

  it("acao chama o callback", () => {
    const onPress = jest.fn();
    const resultado = renderInterativo(<InsightCard titulo="x" mensagem="y" acao={{ rotulo: "Ver detalhes", onPress }} />);
    fireEvent.press(resultado.getByRole("button"));
    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(
      <InsightCard sticker="goal" titulo="Meta" mensagem="Faltam R$ 120,00." acao={{ rotulo: "Ver detalhes", onPress: () => {} }} />,
    );
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
