import { render, screen } from "@testing-library/react-native";
import { CorpoAvisoBanco } from "@/features/painel/corpoAvisoBanco";
import { TemaProvider } from "@/ui/tema";
import { useAtualizarBanco } from "@/features/painel/usarAtualizarBanco";

// O contrato do corpo do aviso: "O banco está atualizando…" só existe com a máquina em `acompanhando` (uma rodada lendo DE FATO). Classificação (a0) sozinha não basta.
jest.mock("@/features/painel/usarAtualizarBanco", () => ({ useAtualizarBanco: jest.fn() }));
jest.mock("@/features/painel/navegacao", () => ({ useNavegacaoPainel: () => ({ navegar: jest.fn(), entrando: false }) }));
const SOZINHO = "O banco está atualizando. Seus dados aparecem aqui sozinhos quando terminar.";
const com = (acao: unknown, maquina: unknown) => jest.mocked(useAtualizarBanco).mockReturnValue({ acao, maquina, atualizar: jest.fn() } as never);

it("classificação (a0) com a máquina ociosa: NÃO diz que está atualizando; cai no texto padrão e no 'Ver bancos conectados'", () => {
  com({ tipo: "acompanhar" }, { fase: "ocioso" }); render(<TemaProvider esquema="light"><CorpoAvisoBanco bancos={["Nubank"]} /></TemaProvider>);
  expect(screen.queryByText(SOZINHO)).toBeNull(); expect(screen.getByText(/Os dados de Nubank precisam ser atualizados/)).toBeTruthy(); expect(screen.getByRole("button", { name: "Ver bancos conectados" })).toBeTruthy();
});
it("controle: com a máquina acompanhando o texto aparece, sem botão", () => {
  com({ tipo: "acompanhar" }, { fase: "acompanhando" }); render(<TemaProvider esquema="light"><CorpoAvisoBanco bancos={["Nubank"]} /></TemaProvider>);
  expect(screen.getByText(SOZINHO)).toBeTruthy(); expect(screen.queryAllByRole("button")).toHaveLength(0);
});
