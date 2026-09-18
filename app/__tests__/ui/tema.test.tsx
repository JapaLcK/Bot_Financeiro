import { render } from "@testing-library/react-native";
import { Text } from "react-native";

import { TemaProvider, useTema } from "@/ui/tema";
import { claro, escuro } from "@/ui/tokens";

// Mock do submódulo, não de `"react-native"` inteiro: espalhar o namespace
// (`{...RN}`) força TODO getter do índice a avaliar, incluindo módulo nativo
// que não existe no Jest (`DevMenu`) — quebra a suíte antes do primeiro teste.
const mockUseColorScheme = jest.fn(() => "light");
jest.mock("react-native/Libraries/Utilities/useColorScheme", () => ({
  __esModule: true,
  default: mockUseColorScheme,
}));

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
});
