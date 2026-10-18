// Bancos conectados: dois detalhes do cartão que o ícone-stub do Jest esconde (aviso "conferir" e o `ui.detail`). O Icone vira um Text com o nome; o resto é o real.
import { BancoCard } from "@/features/openFinance/conexoes/BancoCard";
import type { Item } from "@/features/openFinance/conexoes/juntar";
import { renderInterativo } from "../ui/_render";

jest.mock("@/ui/componentes/Icone", () => ({ Icone: (p: { nome: string }) => jest.requireActual("react").createElement(jest.requireActual("react-native").Text, { testID: `icone-${p.nome}` }) }));

const conexao = (estado: string, detail: string | null) => ({ id: 11, status: "ACTIVE", status_reason: null, last_sync_at: null, reconnected_at: null, provider_item_id: "item_11", institution_name: "Nubank", logo_url: null, ui: { state: estado, label: estado, detail } });
const lido = (motivos: string[]) => ({ conexao_id: 11, saldo_conta: 100, investido: 200, total: 300, contas: 1, cartoes: 0, investimentos: 1, motivos });
const desenhar = (item: Item, oculto = false) => renderInterativo(<BancoCard item={item} numeros oculto={oculto} aberto={false} alternar={() => {}} indice={0} podeReconectar entrando={false} removendoId={null} aoAcompanhar={() => {}} aoReconectar={() => {}} aoDesconectar={() => {}} />);
const avisos = (t: ReturnType<typeof desenhar>) => t.queryAllByTestId("icone-WarningCircle").length;

describe("aviso 'conferir' no total do banco", () => {
  it("aparece quando há motivo a conferir", () => {
    expect(avisos(desenhar({ conexao: conexao("updated", null), banco: lido(["conexao_pausada"]) }))).toBe(1);
  });
  it.each([
    ["sem motivo", lido([])], ["só 'plano_sem_investimentos'", lido(["plano_sem_investimentos"])], ["banco que não veio em /bancos", undefined],
  ])("não aparece: %s", (_n, banco) => {
    expect(avisos(desenhar({ conexao: conexao("updated", null), banco }))).toBe(0);
  });
  it("não aparece com valores ocultos", () => {
    expect(avisos(desenhar({ conexao: conexao("updated", null), banco: lido(["conexao_pausada"]) }, true))).toBe(0);
  });
});

describe("nome acessível do cartão fala o aviso que o ícone mostra", () => {
  const nome = (t: ReturnType<typeof desenhar>) => t.getByRole("button", { name: /^Nubank,/ }).props.accessibilityLabel;
  it("com motivo a conferir, termina em 'a conferir'", () => {
    expect(nome(desenhar({ conexao: conexao("updated", null), banco: lido(["conexao_pausada"]) }))).toBe("Nubank, updated, 3 reais, a conferir");
  });
  it.each([["sem motivo", lido([])], ["só 'plano_sem_investimentos'", lido(["plano_sem_investimentos"])]])("não fala: %s", (_n, banco) => {
    expect(nome(desenhar({ conexao: conexao("updated", null), banco }))).toBe("Nubank, updated, 3 reais");
  });
  it("com valores ocultos o rótulo continua sem número (e sem aviso, que o ícone também esconde)", () => {
    expect(nome(desenhar({ conexao: conexao("updated", null), banco: lido(["conexao_pausada"]) }, true))).not.toMatch(/\d|conferir/);
  });
});

describe("ui.detail do servidor", () => {
  const DETALHE = "O Nubank pediu que você autorize de novo.";
  it("aparece (com o ícone Info) quando o estado não é 'atualizado'", () => {
    const t = desenhar({ conexao: conexao("needs_user_action", DETALHE), banco: lido([]) });
    expect(t.getByText(DETALHE)).toBeTruthy();
    expect(t.queryAllByTestId("icone-Info")).toHaveLength(1);
  });
  it("some quando o estado é 'atualizado', mesmo com texto", () => {
    expect(desenhar({ conexao: conexao("updated", DETALHE), banco: lido([]) }).queryByText(DETALHE)).toBeNull();
  });
  it("não aparece sem texto", () => {
    expect(desenhar({ conexao: conexao("needs_user_action", null), banco: lido([]) }).queryAllByTestId("icone-Info")).toHaveLength(0);
  });
});
