import { act, render } from "@testing-library/react-native";
import { SvgAst } from "react-native-svg";

import { Avatar } from "@/ui/componentes/Avatar";

import { TemaProvider } from "@/ui/tema";

import { renderInterativo } from "./_render";

// O `react-native-svg` é o REAL (nada de dublê do SvgUri): só a rede é falsa, e o teste conta quantos downloads o Avatar faz.
const URI = "https://cdn.pluggy.ai/assets/connector-icons/201.svg";
const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#820AD1"/></svg>';
const fetchSvg = jest.fn();
const texto = (corpo: string, status = 200) => ({ ok: status < 300, status, text: async () => corpo }) as Response;
const drenar = () => act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); });
const embrulha = (imagem: string) => <TemaProvider esquema="light"><Avatar nome="Nubank" imagem={imagem} /></TemaProvider>;
beforeEach(() => { fetchSvg.mockReset().mockResolvedValue(texto(SVG)); globalThis.fetch = fetchSvg as unknown as typeof fetch; });

describe("Avatar com SVG (logo de banco)", () => {
  it("a inicial fica até o desenho EXISTIR; aí o desenho entra e a inicial sai", async () => {
    const tela = renderInterativo(<Avatar nome="Nubank" imagem={URI} />);
    expect(tela.getByText("N")).toBeTruthy();
    expect(tela.UNSAFE_queryByType(SvgAst)).toBeNull();
    await drenar();
    expect(tela.UNSAFE_queryByType(SvgAst)).not.toBeNull();
    expect(tela.queryByText("N")).toBeNull();
  });

  it("logo com <style> (classes .cls-N, como o Nubank da Pluggy): o fill vira atributo do desenho, não sai preto", async () => {
    fetchSvg.mockResolvedValue(texto('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><defs><style>.cls-1{fill:#820AD1}</style></defs><circle class="cls-1" cx="5" cy="5" r="4"/></svg>'));
    const tela = renderInterativo(<Avatar nome="Nubank" imagem={URI} />);
    await drenar();
    expect(JSON.stringify(tela.UNSAFE_getByType(SvgAst).props.ast)).toContain('"style":{"fill":"#820AD1"}'); // o texto do <style> também tem a cor: só o `style` do círculo prova a aplicação
  });

  it.each([
    ["HTML com 200 (página do CDN)", texto("<!DOCTYPE html><html><body>Just a moment...</body></html>")],
    ["XML quebrado", texto("<svg><g></svg>")],
    ["corpo vazio", texto("")],
    ["404", texto("not found", 404)],
    ["404 com corpo SVG válido (sem `r.ok` o desenho entrava)", texto(SVG, 404)],
    ["500 com corpo SVG válido", texto(SVG, 500)],
  ])("%s: nada de círculo em branco, fica a inicial", async (_nome, corpo) => {
    fetchSvg.mockResolvedValue(corpo);
    const tela = renderInterativo(<Avatar nome="Nubank" imagem={URI} />);
    await drenar();
    expect(tela.UNSAFE_queryByType(SvgAst)).toBeNull();
    expect(tela.getByText("N")).toBeTruthy();
  });

  it("pede image/svg+xml e NÃO manda Authorization (host público do CDN)", async () => {
    renderInterativo(<Avatar nome="Nubank" imagem={URI} />);
    await drenar();
    const headers = (fetchSvg.mock.calls[0]![1] as RequestInit).headers as Record<string, string>;
    expect(headers.Accept).toBe("image/svg+xml");
    expect(Object.keys(headers).map((k) => k.toLowerCase())).not.toContain("authorization");
  });

  it("rede que falha cai na inicial", async () => {
    fetchSvg.mockRejectedValue(new TypeError("rede fora"));
    const tela = renderInterativo(<Avatar nome="Nubank" imagem={URI} />);
    await drenar();
    expect(tela.UNSAFE_queryByType(SvgAst)).toBeNull();
    expect(tela.getByText("N")).toBeTruthy();
  });

  it("re-render NÃO refaz o download (callback novo a cada render refazia o fetch de todos os logos)", async () => {
    const tela = renderInterativo(<Avatar nome="Nubank" imagem={URI} />);
    await drenar();
    for (let i = 0; i < 3; i++) tela.rerender(<TemaProvider esquema="light"><Avatar nome="Nubank" imagem={URI} tamanho={44 + i} /></TemaProvider>);
    await drenar();
    expect(fetchSvg).toHaveBeenCalledTimes(1);
    expect(tela.UNSAFE_queryByType(SvgAst)).not.toBeNull();
  });

  // Troca de URI: UM só TemaProvider (raiz de mesma profundidade, via `render` direto) para o React ATUALIZAR a prop em vez de remontar o Avatar.
  const B = URI.replace("201", "202");
  const SVG_B = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="1" height="1"/></svg>';
  const pendente = () => new Promise<Response>(() => {});

  it("troca de URI baixa a nova (e a anterior que falhou tenta de novo)", async () => {
    fetchSvg.mockRejectedValueOnce(new TypeError("rede fora"));
    const tela = render(embrulha(URI));
    await drenar();
    expect(tela.getByText("N")).toBeTruthy();
    tela.rerender(embrulha(B));
    await drenar();
    expect(fetchSvg).toHaveBeenCalledTimes(2);
    expect(String(fetchSvg.mock.calls[1]![0])).toContain("202.svg");
    expect(tela.UNSAFE_queryByType(SvgAst)).not.toBeNull();
  });

  it("A -> B -> A refaz o download de A (sem cache de propósito: o teto é um fetch por troca)", async () => {
    const tela = render(embrulha(URI));
    await drenar();
    tela.rerender(embrulha(B));
    await drenar();
    tela.rerender(embrulha(URI));
    await drenar();
    expect(fetchSvg.mock.calls.map((c) => String(c[0]))).toEqual([URI, B, URI]);
    expect(tela.UNSAFE_queryByType(SvgAst)).not.toBeNull();
  });

  it("desenho de A não aparece enquanto B baixa: volta a inicial", async () => {
    const tela = render(embrulha(URI));
    await drenar();
    expect(tela.UNSAFE_queryByType(SvgAst)).not.toBeNull();
    fetchSvg.mockImplementation(pendente);
    tela.rerender(embrulha(B));
    await drenar();
    expect(tela.UNSAFE_queryByType(SvgAst)).toBeNull();
    expect(tela.getByText("N")).toBeTruthy();
  });

  it("resposta VELHA de A (fetch que ignora o abort) chega depois de B e não derruba B", async () => {
    let respondeA!: (r: Response) => void;
    fetchSvg.mockImplementation((u: string) => (u === URI ? new Promise<Response>((r) => { respondeA = r; }) : Promise.resolve(texto(SVG_B))));
    const tela = render(embrulha(URI));
    tela.rerender(embrulha(B));
    await drenar();
    const astB = tela.UNSAFE_getByType(SvgAst).props.ast;
    await act(async () => { respondeA(texto(SVG)); });
    await drenar();
    expect(tela.UNSAFE_getByType(SvgAst).props.ast).toBe(astB);
    expect(tela.queryByText("N")).toBeNull();
  });

  it("FALHA velha de A (fetch que ignora o abort) chega depois de B e não marca B como falha", async () => {
    let rejeitaA!: (e: Error) => void;
    fetchSvg.mockImplementation((u: string) => (u === URI ? new Promise<Response>((_r, rej) => { rejeitaA = rej; }) : Promise.resolve(texto(SVG_B))));
    const tela = render(embrulha(URI));
    tela.rerender(embrulha(B));
    await drenar();
    await act(async () => { rejeitaA(new TypeError("rede fora")); });
    await drenar();
    expect(tela.UNSAFE_queryByType(SvgAst)).not.toBeNull();
    expect(tela.queryByText("N")).toBeNull();
  });

  it("desmontar durante o download aborta o pedido", async () => {
    let sinal: AbortSignal | undefined;
    fetchSvg.mockImplementation((_u: string, o: RequestInit) => { sinal = o.signal ?? undefined; return new Promise(() => {}); });
    const tela = renderInterativo(<Avatar nome="Nubank" imagem={URI} />);
    expect(sinal?.aborted).toBe(false);
    tela.unmount();
    expect(sinal?.aborted).toBe(true);
  });

  it("decorativo: fora do leitor de tela, sem rótulo; o padrão continua lendo o nome", () => {
    const deco = renderInterativo(<Avatar nome="Nubank" decorativo />);
    expect(deco.queryByLabelText("Nubank")).toBeNull();
    deco.unmount();
    const normal = renderInterativo(<Avatar nome="Nubank" />);
    expect(normal.getByLabelText("Nubank")).toBeTruthy();
  });

  it("fundo e letra pedidos chegam ao círculo e à inicial", () => {
    const tela = renderInterativo(<Avatar nome="Nubank" fundo="#123456" letra="#abcdef" />);
    expect(tela.getByLabelText("Nubank")).toHaveStyle({ backgroundColor: "#123456" });
    expect(tela.getByText("N")).toHaveStyle({ color: "#abcdef" });
  });
});
