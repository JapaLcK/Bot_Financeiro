import { Image } from "react-native";

import { Avatar } from "@/ui/componentes/Avatar";

import { renderNosDoisTemas } from "./_render";

describe("Avatar", () => {
  it("duas ou mais palavras: primeira letra do primeiro nome + do último", () => {
    const { claro: c } = renderNosDoisTemas(<Avatar nome="Ana Clara Souza" />);
    expect(c.getByText("AS")).toBeTruthy();
  });

  it("uma palavra só, e com acento: uma letra, maiúscula corretamente", () => {
    const { claro: c } = renderNosDoisTemas(<Avatar nome="Élida" />);
    expect(c.getByText("É")).toBeTruthy();
  });

  it("nome vazio: fallback \"?\", sem lançar", () => {
    const { claro: c } = renderNosDoisTemas(<Avatar nome="" />);
    expect(c.getByText("?")).toBeTruthy();
  });

  it("nome só com espaços: mesmo fallback do vazio", () => {
    const { claro: c } = renderNosDoisTemas(<Avatar nome="   " />);
    expect(c.getByText("?")).toBeTruthy();
  });

  it("emoji no nome não quebra em surrogate solto (indexação por ponto de código, não por [0])", () => {
    const { claro: c } = renderNosDoisTemas(<Avatar nome="😀 Silva" />);
    expect(c.getByText("😀S")).toBeTruthy();
  });

  it("com imagem, mostra Image em vez das iniciais", () => {
    const { claro: c } = renderNosDoisTemas(<Avatar nome="Ana Souza" imagem="https://x/y.png" />);
    expect(c.UNSAFE_getByType(Image).props.source).toEqual({ uri: "https://x/y.png" });
    expect(c.queryByText("AS")).toBeNull();
  });

  it("tamanho custom vira largura/altura/borderRadius do círculo", () => {
    const { claro: c } = renderNosDoisTemas(<Avatar nome="Ana Souza" tamanho={64} />);
    const container = c.getByLabelText("Ana Souza");
    expect(container.props.style).toMatchObject({ width: 64, height: 64, borderRadius: 32 });
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<Avatar nome="Ana Clara Souza" />);
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});
