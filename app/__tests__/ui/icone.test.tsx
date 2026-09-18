import IconeStub from "./__mocks__/iconeStub";
import { Icone } from "@/ui/componentes/Icone";
import { claro, escuro } from "@/ui/tokens";

import { renderNosDoisTemas } from "./_render";

describe("Icone", () => {
  it("tamanho e cor (tom) chegam como props ao ícone real (24/ink por padrão)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<Icone nome="CaretRight" />);
    expect(c.UNSAFE_getByType(IconeStub).props).toMatchObject({ size: 24, color: claro.ink, weight: "regular" });
    expect(e.UNSAFE_getByType(IconeStub).props).toMatchObject({ size: 24, color: escuro.ink, weight: "regular" });
  });

  it("tamanho 20 e tom customizado passam adiante", () => {
    const { claro: c } = renderNosDoisTemas(<Icone nome="Wallet" tamanho={20} tom="inkFaint" />);
    expect(c.UNSAFE_getByType(IconeStub).props).toMatchObject({ size: 20, color: claro.inkFaint });
  });

  it.each([
    "CaretRight",
    "Wallet",
    "Bell",
    "CheckCircle",
    "Info",
    "WarningCircle",
    "WarningOctagon",
    "LinkBreak",
    "PauseCircle",
    "Trash",
    "Question",
  ] as const)("resolve o ícone %s sem lançar", (nome) => {
    const { claro: c } = renderNosDoisTemas(<Icone nome={nome} />);
    expect(c.UNSAFE_getByType(IconeStub)).toBeTruthy();
  });

  it("snapshot (dois temas)", () => {
    const { claro: c, escuro: e } = renderNosDoisTemas(<Icone nome="Bell" tamanho={20} tom="danger" />);
    expect(c.toJSON()).toMatchSnapshot("claro");
    expect(e.toJSON()).toMatchSnapshot("escuro");
  });
});

declare const __dirname: string;

describe("Icone - exports reais do pacote (sem passar pelo stub)", () => {
  /**
   * O `moduleNameMapper` do Jest (jest.config.js) só casa o ESPECIFICADOR
   * `phosphor-react-native/src/icons/...` — um `require` por caminho
   * ABSOLUTO não bate na regex e carrega o arquivo de verdade. Sem isto, um
   * typo no nome exportado que `resolver()` (Icone.tsx) procura passaria
   * verde para sempre: o stub só tem `default`, e o fallback de `resolver()`
   * (`registro[nomeado] ?? registro.default`) mascara qualquer nome errado
   * em teste — em produção o mesmo typo faz `resolver()` lançar no load do
   * módulo (tela branca).
   */
  it.each([
    ["CaretRight", "CaretRightIcon"],
    ["Wallet", "WalletIcon"],
    ["Bell", "BellIcon"],
    ["CheckCircle", "CheckCircleIcon"],
    ["Info", "InfoIcon"],
    ["WarningCircle", "WarningCircleIcon"],
    ["WarningOctagon", "WarningOctagonIcon"],
    ["LinkBreak", "LinkBreakIcon"],
    ["PauseCircle", "PauseCircleIcon"],
    ["Trash", "TrashIcon"],
    ["Question", "QuestionIcon"],
  ] as const)("o módulo real de %s exporta %s (nomeado, existe de fato no pacote)", (arquivo, nomeado) => {
    const caminho = `${__dirname}/../../node_modules/phosphor-react-native/src/icons/${arquivo}`;
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- mesmo motivo do Icone.tsx: caminho estático, fora do grafo do tsc.
    const mod = require(caminho) as Record<string, unknown>;
    expect(typeof mod[nomeado]).toBe("function");
  });
});
