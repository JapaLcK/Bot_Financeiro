// Bancos conectados: a LINHA (sem dinheiro) e dois detalhes do DETALHE que o ícone-stub do Jest esconde (aviso "a conferir" e o `ui.detail`). O Icone vira um Text com o nome;
// a Folha (que precisa do navegador) vira um invólucro: o que ela faz (abrir, fechar, `focado`) é das suítes de rota. O resto é o real.
import { fireEvent } from "@testing-library/react-native";
import { Image } from "react-native";
import { BancoCard } from "@/features/openFinance/conexoes/BancoCard";
import { BancoDetalhe } from "@/features/openFinance/conexoes/BancoDetalhe";
import type { Item } from "@/features/openFinance/conexoes/juntar";
import { renderInterativo } from "../ui/_render";

jest.mock("@/ui/componentes/Icone", () => ({ Icone: (p: { nome: string }) => jest.requireActual("react").createElement(jest.requireActual("react-native").Text, { testID: `icone-${p.nome}` }) }));
jest.mock("@/features/painel/base", () => ({ Folha: (p: { children: unknown }) => p.children }));

const conexao = (estado: string, detail: string | null) => ({ id: 11, status: "ACTIVE", status_reason: null, last_sync_at: null, reconnected_at: null, provider_item_id: "item_11", institution_name: "Nubank", logo_url: null, ui: { state: estado, label: estado, detail } });
const lido = (motivos: string[]) => ({ conexao_id: 11, saldo_conta: 100, investido: 200, total: 300, contas: 1, cartoes: 0, investimentos: 1, motivos });
const detalhe = (item: Item, oculto = false, agora?: number) => renderInterativo(<BancoDetalhe agora={agora} item={item} numeros oculto={oculto} indice={0} podeReconectar entrando={false} removendoId={null} falha={null}
  fechar={() => {}} aoAcompanhar={() => {}} aoReconectar={() => {}} aoDesconectar={() => {}} />);
const avisos = (t: ReturnType<typeof detalhe>) => t.queryAllByTestId("icone-WarningCircle").length;

describe("a linha do banco", () => {
  it("é um botão 'nome, estado' sem valor nenhum, e o toque chama aoAbrir", () => {
    const aoAbrir = jest.fn();
    const t = renderInterativo(<BancoCard item={{ conexao: conexao("updated", null), banco: lido(["conexao_pausada"]) }} indice={0} aoAbrir={aoAbrir} />);
    const linha = t.getByRole("button", { name: "Nubank, updated" });
    expect(t.queryAllByText(/R\$/)).toHaveLength(0);
    expect(t.queryAllByTestId("icone-WarningCircle")).toHaveLength(0); // o "a conferir" mora no detalhe
    fireEvent.press(linha);
    expect(aoAbrir).toHaveBeenCalledTimes(1);
  });
});

describe("logo do banco: o próprio vira <Image> do nosso host; a Pluggy segue; sem os dois, a inicial", () => {
  const PROPRIO = "/brand/bancos/60746948.png", PLUGGY = "https://cdn.pluggy.ai/a/201.png"; // .png de propósito: bitmap, sem o fetch de SVG
  const uris = (t: ReturnType<typeof detalhe>) => t.UNSAFE_queryAllByType(Image).map((i) => i.props.source?.uri);
  const item = (extra: Record<string, unknown>) => ({ conexao: { ...conexao("updated", null), ...extra }, banco: lido([]) });
  const linha = (extra: Record<string, unknown>) => renderInterativo(<BancoCard item={item(extra)} indice={0} aoAbrir={() => {}} />);
  it.each([["linha", (e: Record<string, unknown>) => linha(e)], ["detalhe", (e: Record<string, unknown>) => detalhe(item(e))]])("%s: com os dois campos, o Avatar recebe o host da API + caminho", (_n, desenhar) => {
    expect(uris(desenhar({ logo_proprio: PROPRIO, logo_url: PLUGGY }) as ReturnType<typeof detalhe>)).toEqual([`http://backend.teste${PROPRIO}`]);
  });
  it("só logo_url: o da Pluggy", () => {
    expect(uris(linha({ logo_url: PLUGGY }) as ReturnType<typeof detalhe>)).toEqual([PLUGGY]);
  });
  it("sem os dois: nenhuma imagem, fica a inicial", () => {
    const t = linha({});
    expect(uris(t as ReturnType<typeof detalhe>)).toEqual([]);
    expect(t.getByText("N", { includeHiddenElements: true })).toBeTruthy();
  });
  it("próprio fora da regra cai no logo da Pluggy (nunca no host de fora)", () => {
    expect(uris(linha({ logo_proprio: "//evil.example/x.png", logo_url: PLUGGY }) as ReturnType<typeof detalhe>)).toEqual([PLUGGY]);
  });
  it("o próprio que falha ao carregar cai na inicial, sem tentar a Pluggy no meio", () => {
    const t = linha({ logo_proprio: PROPRIO, logo_url: PLUGGY });
    fireEvent(t.UNSAFE_getByType(Image), "error");
    expect(t.UNSAFE_queryAllByType(Image)).toHaveLength(0);
    expect(t.getByText("N", { includeHiddenElements: true })).toBeTruthy();
  });
});

describe("aviso 'a conferir' no total do detalhe", () => {
  it("aparece quando há motivo a conferir (e é falado)", () => {
    const t = detalhe({ conexao: conexao("updated", null), banco: lido(["conexao_pausada"]) });
    expect(avisos(t)).toBe(1);
    expect(t.getByLabelText("a conferir")).toBeTruthy();
  });
  it.each([
    ["sem motivo", lido([])], ["só 'plano_sem_investimentos'", lido(["plano_sem_investimentos"])], ["banco que não veio em /bancos", undefined],
  ])("não aparece: %s", (_n, banco) => {
    expect(avisos(detalhe({ conexao: conexao("updated", null), banco }))).toBe(0);
  });
  it("não aparece com valores ocultos", () => {
    expect(avisos(detalhe({ conexao: conexao("updated", null), banco: lido(["conexao_pausada"]) }, true))).toBe(0);
  });
});

describe("há quanto tempo sincronizou", () => {
  it("usa o `agora` que a tela passa (não o relógio do render)", () => {
    const sync = "2026-10-05T12:00:00Z";
    const item = { conexao: { ...conexao("updated", null), last_sync_at: sync }, banco: lido([]) };
    expect(detalhe(item, false, Date.parse(sync) + 5 * 60_000).getByText("há 5 min")).toBeTruthy();
  });
  it("sem last_sync_at: 'Ainda não sincronizou'", () => {
    expect(detalhe({ conexao: conexao("updated", null), banco: lido([]) }).getByText("Ainda não sincronizou")).toBeTruthy();
  });
});

describe("ui.detail do servidor", () => {
  const DETALHE = "O Nubank pediu que você autorize de novo.";
  it("aparece quando o estado não é 'atualizado'", () => {
    expect(detalhe({ conexao: conexao("needs_user_action", DETALHE), banco: lido([]) }).getByText(DETALHE)).toBeTruthy();
  });
  it("some quando o estado é 'atualizado', mesmo com texto", () => {
    expect(detalhe({ conexao: conexao("updated", DETALHE), banco: lido([]) }).queryByText(DETALHE)).toBeNull();
  });
});
