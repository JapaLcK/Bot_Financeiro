import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { chaveLayout, mesAtual, mesesAnteriores, nomeMes } from "@/features/painel/catalogo";
import { cofre, fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import v2 from "../../../tests/frontend/api_v2_respostas.json";
// A aba Metas (PR 1 do app v2): lê `/api/app/metas` (formato v2), desenha as MESMAS pílulas do chat, só leitura, sem caixinhas; o bloco `metas` não está mais na Home.
jest.setTimeout(30_000);
const METAS = (v2 as unknown as { metas: Record<string, unknown> }).metas;
let metas: () => Response | Promise<Response>;
function servidor() {
 fetchFalso.mockImplementation(async (url: string) => {
  const path = new URL(url).pathname;
  if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === "/api/app/perfil") return resposta(200, { perfil: "padrao" });
  if (path === "/api/app/metas") return metas();
  return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
 });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); metas = () => resposta(200, METAS.varios); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor(); });
afterEach(() => jest.restoreAllMocks());
const chamou = (caminho: string) => fetchFalso.mock.calls.filter(([u]) => new URL(String(u)).pathname === caminho).length;
const aba = () => within(screen.getByTestId("bloco-metas"));
const ir = async (rota: "/resumo" | "/gastos" | "/metas") => { await act(async () => { router.navigate(rota); await drenar(); }); };
const abrirMetas = async (esperar = "meta-1") => { renderRouter("./app", { initialUrl: "/metas" }); await waitFor(() => expect(screen.getByTestId(esperar)).toBeTruthy()); };
async function apertar(rotulo: string) { await act(async () => { fireEvent.press(screen.getByRole("button", { name: rotulo })); await drenar(); }); }

describe("a aba lê a v2", () => {
  it("pílulas do `varios` (reserva primeiro, com blocos); pede /api/app/metas e nunca /goals/1/status", async () => {
    await abrirMetas();
    expect(screen.getAllByTestId(/^meta-\d+$/)[0]!.props.testID).toBe("meta-1");
    expect(within(screen.getByTestId("meta-1")).getAllByTestId("bloco-cheio").length).toBeGreaterThan(0);
    expect(aba().getByText("Viagem pro Japão")).toBeTruthy();
    expect(chamou("/api/app/metas")).toBeGreaterThan(0); expect(chamou("/goals/1/status")).toBe(0);
    expect(screen.queryByText("Recebemos dados incompatíveis. Tente atualizar.")).toBeNull();
  });
  it("fixture do fallback é a mesma do contrato (uma fonte só)", () => { expect((fixture as Record<string, unknown>)["/api/app/metas"]).toEqual(METAS.sem_custo); });
});

describe("a aba lê e abre a folha de meta", () => {
  it("sem metas: 'Nenhuma meta ainda.' e os dois caminhos de criar ('Criar meta' no cabeçalho, 'Montar reserva' na lista); nada pede à IA", async () => {
    metas = () => resposta(200, METAS.vazio); renderRouter("./app", { initialUrl: "/metas" });
    await waitFor(() => expect(aba().getByText("Nenhuma meta ainda.")).toBeTruthy());
    expect(aba().getAllByRole("button").map((b) => b.props.accessibilityLabel)).toEqual(["Criar meta", "Montar reserva de emergência"]); expect(aba().getByText("Montar reserva")).toBeTruthy();
  });
  it("com 11 metas todas aparecem, sem '+N'", async () => {
    await abrirMetas(); expect(screen.getAllByTestId(/^meta-\d+$/)).toHaveLength(11); expect(screen.queryByTestId("meta-mais")).toBeNull(); expect(aba().getAllByRole("button")).toHaveLength(12); // 11 pílulas tocáveis + o '+' de criar; com reserva, não há 'Montar reserva'
    expect(aba().queryByText("Montar reserva")).toBeNull();
  });
  it("relê ao abrir a aba: a meta criada no chat aparece, com 1 chamada nova; Resumo <-> Gastos não relê", async () => {
    metas = () => resposta(200, METAS.vazio); renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByTestId("bloco-resumo")).toBeTruthy());
    await waitFor(() => expect(chamou("/api/app/metas")).toBe(1));
    await ir("/gastos"); await ir("/resumo"); expect(chamou("/api/app/metas")).toBe(1); // positivo: trocar de aba não relê
    metas = () => resposta(200, METAS.varios); await ir("/metas");
    await waitFor(() => expect(screen.getByTestId("meta-2")).toBeTruthy()); expect(chamou("/api/app/metas")).toBe(2);
  });
});

// ACEITO DE PROPÓSITO: `conexao_pausada`/`banco_desatualizado` nos `motivos` das metas acendem o aviso de banco do topo (o sinal é verdadeiro e o aviso tem "Ver bancos conectados"); `metas` não sai de `temMotivoBanco`.
describe("aviso de banco do topo", () => {
  const AVISO = "Dados do banco desatualizados";
  it("metas com conexão pausada (`varios`) acendem o aviso", async () => {
    renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText(AVISO)).toBeTruthy());
  });
  it("controle: sem motivo de banco (`sem_custo`) não acende", async () => {
    metas = () => resposta(200, METAS.sem_custo); renderRouter("./app", { initialUrl: "/resumo" });
    await waitFor(() => expect(screen.getByTestId("bloco-resumo")).toBeTruthy()); await waitFor(() => expect(chamou("/api/app/metas")).toBe(1)); await act(drenar);
    expect(screen.queryByText(AVISO)).toBeNull();
  });
});

describe("quantas leituras de /api/app/metas", () => {
  it("abrir direto em /metas lê 1 vez; voltar de outra aba lê 1 a mais", async () => {
    await abrirMetas(); await act(drenar); expect(chamou("/api/app/metas")).toBe(1); // o foco do mount não relê: a carga completa já traz as metas
    await ir("/gastos"); expect(chamou("/api/app/metas")).toBe(1); await ir("/metas"); await waitFor(() => expect(chamou("/api/app/metas")).toBe(2));
  });
  it("trocar o mês não relê as metas (a meta não depende de mês), mesmo depois do 1º foco", async () => {
    renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByTestId("bloco-resumo")).toBeTruthy());
    await ir("/metas"); await waitFor(() => expect(chamou("/api/app/metas")).toBe(2));
    const mes = mesesAnteriores(mesAtual(), 2)[1]!, resumos = () => chamou("/api/app/resumo-do-mes");
    await apertar(nomeMes(mesAtual()).slice(0, 3)); const antes = resumos(); await apertar(nomeMes(mes));
    await waitFor(() => expect(resumos()).toBeGreaterThan(antes)); await act(drenar); expect(chamou("/api/app/metas")).toBe(2); // positivo: o mês foi trocado e o resumo relido
  });
});

describe("a releitura ao abrir é silenciosa", () => {
  const SEM_2 = () => { const m = (METAS.varios as { metas: { id: number; nome: string }[] }).metas; return { ...(METAS.varios as object), metas: [...m.filter((x) => x.id !== 2), { ...m[2]!, id: 99, nome: "Meta nova" }] }; };
  const pilulas = () => screen.queryAllByTestId(/^meta-\d+$/).length;
  it("o dado bom fica na tela, sem spinner nem RefreshControl, enquanto relê; e se a releitura falha (500) ele continua", async () => {
    renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByTestId("bloco-resumo")).toBeTruthy()); await waitFor(() => expect(chamou("/api/app/metas")).toBe(1));
    const atraso = segurar(); metas = async () => { await atraso.promessa; return resposta(500, { detail: "x" }); };
    await ir("/metas"); await waitFor(() => expect(chamou("/api/app/metas")).toBe(2)); // a releitura está pendurada
    expect(pilulas()).toBe(11); expect(aba().queryByLabelText("Carregando dados")).toBeNull(); expect(screen.getByTestId("tela").props.refreshControl.props.refreshing).toBe(false);
    await act(async () => { atraso.soltar(); await drenar(); });
    expect(pilulas()).toBe(11); expect(aba().queryByText("Tentar novamente")).toBeNull(); expect(screen.queryByText("Não conseguimos carregar agora. Tente novamente.")).toBeNull();
  });
  it("positivo: a 1ª carga mostra 'Carregando dados'; resposta boa na releitura troca os dados (meta nova aparece, apagada some)", async () => {
    const atraso = segurar(); metas = async () => { await atraso.promessa; return resposta(200, METAS.varios); };
    renderRouter("./app", { initialUrl: "/metas" }); await waitFor(() => expect(aba().getByLabelText("Carregando dados")).toBeTruthy()); expect(pilulas()).toBe(0);
    await act(async () => { atraso.soltar(); await drenar(); }); await waitFor(() => expect(screen.getByTestId("meta-2")).toBeTruthy());
    metas = () => resposta(200, SEM_2()); await ir("/gastos"); await ir("/metas");
    await waitFor(() => expect(screen.getByTestId("meta-99")).toBeTruthy()); expect(screen.queryByTestId("meta-2")).toBeNull();
  });
});

describe("erro e negado", () => {
  it("500: 'Tentar novamente' dentro do bloco; tocar chama /api/app/metas de novo", async () => {
    metas = () => resposta(500, { detail: "x" }); renderRouter("./app", { initialUrl: "/metas" });
    await waitFor(() => expect(aba().getByText("Tentar novamente")).toBeTruthy()); const antes = chamou("/api/app/metas");
    await apertar("Tentar novamente"); expect(chamou("/api/app/metas")).toBeGreaterThan(antes);
  });
  it("403: mensagem, sem botão de nova tentativa", async () => {
    metas = () => resposta(403, { detail: "x" }); renderRouter("./app", { initialUrl: "/metas" });
    await waitFor(() => expect(aba().getByText("Este recurso não está disponível no seu acesso atual.")).toBeTruthy()); expect(aba().queryByText("Tentar novamente")).toBeNull();
  });
});

it("valores ocultos na aba: sem %, sem barra, sem bloco cheio e rótulo 'Valor oculto'; voltam ao mostrar", async () => {
  await abrirMetas(); const barras = () => aba().UNSAFE_queryAllByProps({ accessibilityRole: "progressbar" }).filter((b) => typeof b.type === "string");
  expect(aba().queryAllByText(/^\d+%$/).length).toBeGreaterThan(0); expect(barras().length).toBeGreaterThan(0); expect(aba().queryAllByTestId("bloco-cheio").length).toBeGreaterThan(0);
  await apertar("Ocultar valores");
  expect(aba().queryAllByText(/^\d+%$/)).toHaveLength(0); expect(barras()).toHaveLength(0); expect(aba().queryAllByTestId("bloco-cheio")).toHaveLength(0);
  expect(screen.getByTestId("meta-2").props.accessibilityLabel).toContain("Valor oculto");
  await apertar("Mostrar valores"); expect(aba().queryAllByText(/^\d+%$/).length).toBeGreaterThan(0); expect(barras().length).toBeGreaterThan(0);
});

describe("a Home não tem mais o bloco Metas", () => {
  it("ordem salva com `metas` é lida sem ele; os outros ids ficam; a aba /metas segue com o bloco", async () => {
    cofre.set(chaveLayout(1, "padrao"), JSON.stringify(["contas", "metas", "patrimonio"]));
    renderRouter("./app", { initialUrl: "/resumo" });
    await waitFor(() => expect(screen.getByTestId("bloco-contas")).toBeTruthy());
    await waitFor(() => expect(screen.queryByTestId("bloco-hero")).toBeNull()); // a ordem salva já valeu (o padrão tem `hero`)
    expect(screen.getByTestId("bloco-contas")).toBeTruthy(); expect(screen.getByTestId("bloco-patrimonio")).toBeTruthy(); expect(screen.queryByTestId("bloco-metas")).toBeNull();
    await ir("/metas"); await waitFor(() => expect(screen.getByTestId("bloco-metas")).toBeTruthy());
  });
  it("'Organizar' não oferece 'Adicionar Metas' (e oferece outro bloco ausente)", async () => {
    renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByRole("button", { name: "Organizar" })).toBeTruthy());
    await apertar("Organizar"); expect(screen.getByRole("button", { name: "Adicionar Parcelas futuras" })).toBeTruthy(); expect(screen.queryByRole("button", { name: "Adicionar Metas" })).toBeNull();
  });
});
