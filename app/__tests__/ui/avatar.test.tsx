import { fireEvent } from "@testing-library/react-native";
import { Image } from "react-native";

import { Avatar } from "@/ui/componentes/Avatar";
import { TemaProvider } from "@/ui/tema";

import { renderInterativo, renderNosDoisTemas } from "./_render";

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

  it("acento DECOMPOSTO (E + marca combinante separada) mantém a marca, não só a base", () => {
    const { claro: c } = renderNosDoisTemas(<Avatar nome={"Élida"} />);
    expect(c.getByText("É")).toBeTruthy();
  });

  it("bandeira (par de indicadores regionais) sai inteira, não pela metade", () => {
    const { claro: c } = renderNosDoisTemas(<Avatar nome="🇧🇷 Silva" />);
    expect(c.getByText("🇧🇷S")).toBeTruthy();
  });

  it("com imagem, mostra Image em vez das iniciais", () => {
    const { claro: c } = renderNosDoisTemas(<Avatar nome="Ana Souza" imagem="https://x/y.png" />);
    expect(c.UNSAFE_getByType(Image).props.source).toEqual({ uri: "https://x/y.png" });
    expect(c.queryByText("AS")).toBeNull();
  });

  it("imagem que falha cai nas iniciais: círculo vazio é indistinguível de erro", () => {
    // `renderInterativo`: o `fireEvent` só alcança a ÚLTIMA árvore montada, e
    // `renderNosDoisTemas` monta duas.
    const tela = renderInterativo(<Avatar nome="Ana Souza" imagem="https://x/indisponivel.png" />);
    expect(tela.queryByText("AS")).toBeNull();
    fireEvent(tela.UNSAFE_getByType(Image), "error");
    expect(tela.getByText("AS")).toBeTruthy();
    expect(tela.UNSAFE_queryByType(Image)).toBeNull();
  });

  it("foto nova depois de uma falha é tentada de novo", () => {
    const tela = renderInterativo(<Avatar nome="Ana Souza" imagem="https://x/ruim.png" />);
    fireEvent(tela.UNSAFE_getByType(Image), "error");
    expect(tela.getByText("AS")).toBeTruthy();
    tela.update(
      <TemaProvider esquema="light">
        <Avatar nome="Ana Souza" imagem="https://x/nova.png" />
      </TemaProvider>,
    );
    expect(tela.UNSAFE_getByType(Image).props.source).toEqual({ uri: "https://x/nova.png" });
  });

  it("voltar para a URI que falhou tenta de novo (linha reaproveitada em lista)", () => {
    const comFoto = (uri: string) => (
      <TemaProvider esquema="light">
        <Avatar nome="Ana Souza" imagem={uri} />
      </TemaProvider>
    );
    const tela = renderInterativo(<Avatar nome="Ana Souza" imagem="https://x/a.png" />);
    fireEvent(tela.UNSAFE_getByType(Image), "error");
    expect(tela.getByText("AS")).toBeTruthy();
    tela.update(comFoto("https://x/b.png"));
    expect(tela.UNSAFE_getByType(Image).props.source).toEqual({ uri: "https://x/b.png" });
    tela.update(comFoto("https://x/a.png"));
    expect(tela.UNSAFE_getByType(Image).props.source).toEqual({ uri: "https://x/a.png" });
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
