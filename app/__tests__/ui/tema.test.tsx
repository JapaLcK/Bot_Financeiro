import { act, render } from "@testing-library/react-native";
import { Stack, useTheme } from "expo-router";
import { renderRouter, screen } from "expo-router/testing-library";
import { Text } from "react-native";

import { TemaProvider, useTema } from "@/ui/tema";
import { claro, escuro } from "@/ui/tokens";

// Mock do submódulo, não de `"react-native"` inteiro: espalhar o namespace
// (`{...RN}`) força TODO getter do índice a avaliar, incluindo módulo nativo
// que não existe no Jest (`DevMenu`) — quebra a suíte antes do primeiro teste.
// A fábrica lê a espiã só na CHAMADA: o `tema.tsx` importa o expo-router, que
// carrega `useColorScheme` antes de esta `const` existir (ver `layout.test.tsx`).
const mockUseColorScheme = jest.fn(() => "light");
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: () => mockUseColorScheme(),
}));

// Dublê global do `jest.setup.js`.
const mockSetBg = jest.requireMock("expo-system-ui").setBackgroundColorAsync as jest.Mock;

function Sonda() {
  const { esquema, cores } = useTema();
  return <Text testID="sonda">{`${esquema}:${cores.bg}`}</Text>;
}

describe("TemaProvider / useTema", () => {
  it("sem esquema forçado, segue o sistema", () => {
    mockUseColorScheme.mockReturnValue("dark");
    const { getByTestId } = render(
      <TemaProvider>
        <Sonda />
      </TemaProvider>,
    );
    expect(getByTestId("sonda").props.children).toBe(`dark:${escuro.bg}`);
  });

  it("esquema forçado vence o sistema", () => {
    mockUseColorScheme.mockReturnValue("dark");
    const { getByTestId } = render(
      <TemaProvider esquema="light">
        <Sonda />
      </TemaProvider>,
    );
    expect(getByTestId("sonda").props.children).toBe(`light:${claro.bg}`);
  });

  it("useTema() fora de um TemaProvider lança, em vez de devolver cor errada em silêncio", () => {
    // Sem `console.error` do React sujando a saída do teste: o erro é esperado.
    const consoleErro = jest.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Sonda />)).toThrow(/TemaProvider/);
    consoleErro.mockRestore();
  });

  describe("fundo da janela nativa (cantos do teclado no iOS)", () => {
    beforeEach(() => mockSetBg.mockClear());

    it("pinta com o bg do tema do sistema e repinta quando o sistema troca", () => {
      mockUseColorScheme.mockReturnValue("dark");
      const { rerender } = render(<TemaProvider>{null}</TemaProvider>);
      expect(mockSetBg).toHaveBeenLastCalledWith(escuro.bg);

      mockUseColorScheme.mockReturnValue("light");
      rerender(<TemaProvider>{null}</TemaProvider>);
      // Positivo: o claro continua branco.
      expect(mockSetBg).toHaveBeenLastCalledWith("#FFFFFF");
      expect(claro.bg).toBe("#FFFFFF");
    });

    it("segue o esquema forçado e a troca dele", () => {
      mockUseColorScheme.mockReturnValue("light");
      const { rerender } = render(<TemaProvider esquema="dark">{null}</TemaProvider>);
      expect(mockSetBg).toHaveBeenLastCalledWith(escuro.bg);

      rerender(<TemaProvider esquema="light">{null}</TemaProvider>);
      expect(mockSetBg).toHaveBeenLastCalledWith(claro.bg);
    });

    it("provider aninhado devolve a cor do de fora ao desmontar", () => {
      mockUseColorScheme.mockReturnValue("light");
      const arvore = (comAninhado: boolean) => (
        <TemaProvider>{comAninhado ? <TemaProvider esquema="dark">{null}</TemaProvider> : null}</TemaProvider>
      );
      // Ordem real: a raiz monta primeiro, a tela com tema forçado depois.
      const { rerender } = render(arvore(false));
      rerender(arvore(true));
      expect(mockSetBg).toHaveBeenLastCalledWith(escuro.bg);

      rerender(arvore(false));
      expect(mockSetBg).toHaveBeenLastCalledWith(claro.bg);
    });

    it("o nativo recusando não derruba nada (cor é cosmética)", async () => {
      mockSetBg.mockRejectedValueOnce(new Error("sem janela"));
      const { getByTestId } = render(
        <TemaProvider>
          <Sonda />
        </TemaProvider>,
      );
      // Deixa a rejeição assentar: sem o `.catch`, ela estoura aqui.
      await act(async () => {});
      expect(mockSetBg).toHaveBeenCalledTimes(1);
      expect(getByTestId("sonda")).toBeTruthy();
    });
  });

  describe("tema de navegação (fundo do container nativo das pilhas)", () => {
    // O que o native-stack pinta atrás dos cartões nas transições do iOS 26.
    function Tela() {
      return <Text testID="navegacao">{String(useTheme().colors.background)}</Text>;
    }
    const Layout = () => (
      <TemaProvider>
        <Stack screenOptions={{ headerShown: false }} />
      </TemaProvider>
    );

    it.each([
      ["dark", escuro.bg],
      ["light", claro.bg],
    ])("sistema %s: colors.background dentro da pilha é o bg do tema", (sistema, bg) => {
      mockUseColorScheme.mockReturnValue(sistema);
      renderRouter({ _layout: Layout, index: Tela }, { initialUrl: "/" });
      expect(screen.getByTestId("navegacao").props.children).toBe(bg);
    });
  });
});
