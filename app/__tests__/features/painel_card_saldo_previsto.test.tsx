// Card "Saldo previsto" do Resumo: recolhido só o horizonte (30/60/90), a data e o saldo (mais os estados de erro); o card INTEIRO abre o resto inline, sem folha.
import { AccessibilityInfo } from "react-native";
import { act, fireEvent, renderRouter, screen, waitFor, within } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
import { __resetAvisoBanco } from "@/features/painel/avisoBanco";

jest.setTimeout(20_000); // monta o painel inteiro pelo router
const F = fixture as Record<string, any>, PREV = "/api/app/previsao";
let previsao: any, previsaoResp: (() => Promise<Response>) | null;
beforeEach(async () => {
  previsaoResp = null; __resetAvisoBanco(); prepararCaso(); desligarTrava(); previsao = F[PREV];
  await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
  fetchFalso.mockImplementation(async (url: string) => {
    const path = new URL(url).pathname;
    if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true });
    if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
    if (path === PREV) return previsaoResp ? previsaoResp() : resposta(200, previsao);
    return resposta(200, F[path] ?? {});
  });
});
afterEach(() => jest.restoreAllMocks());
const card = () => within(screen.getByTestId("bloco-hero"));
const titulo = () => card().getByRole("button", { name: "Saldo previsto" });
const tocar = async (alvo: Parameters<typeof fireEvent.press>[0]) => { await act(async () => { fireEvent.press(alvo); await drenar(); }); };
const DETALHES = ["Hoje, com as premissas", /^30 dias · /, /^Menor saldo previsto/];
async function abrirResumo() { renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("A partir de hoje, 06/10")).toBeTruthy()); }
const semDetalhes = () => { for (const t of DETALHES) expect(card().queryByText(t)).toBeNull(); expect(card().queryByLabelText("Evolução dos valores no período")).toBeNull(); };
const comDetalhes = () => { expect(card().getByText("Hoje, com as premissas")).toBeTruthy(); expect(card().getByText(/^30 dias · /)).toBeTruthy(); expect(card().getByLabelText("Evolução dos valores no período")).toBeTruthy(); };

it("recolhido: só o horizonte, a data e o saldo; nada de curva, marcos, âncora, pior dia nem 'Como foi feita'", async () => {
  await abrirResumo();
  for (const dias of F[PREV].dias_permitidos) expect(card().getByRole("button", { name: `${dias} dias` })).toBeTruthy();
  expect(card().getByText("A partir de hoje, 06/10")).toBeTruthy(); expect(card().getAllByLabelText(/reais/).length).toBeGreaterThan(0);
  semDetalhes(); expect(card().queryByText("Como foi feita")).toBeNull(); expect(card().queryByTestId("como-foi-feita")).toBeNull();
  expect(titulo().props.accessibilityState).toMatchObject({ expanded: false });
  expect(screen.queryByRole("button", { name: "Abrir Saldo previsto" })).toBeNull(); // nada de folha: o detalhe abre dentro do card
});
it("o card INTEIRO é o alvo: tocar no cartão (fora do título) e no título abre e fecha os detalhes inline, anunciando expandido", async () => {
  await abrirResumo();
  await tocar(screen.getByTestId("bloco-hero")); comDetalhes(); expect(titulo().props.accessibilityState).toMatchObject({ expanded: true });
  await tocar(titulo()); semDetalhes(); expect(titulo().props.accessibilityState).toMatchObject({ expanded: false });
  await tocar(titulo()); comDetalhes(); expect(screen.queryByRole("header", { name: "Saldo previsto" })).toBeNull(); expect(screen.queryByRole("button", { name: "Fechar" })).toBeNull();
});
it("trocar o horizonte NÃO abre nem fecha o card (o chip toma o toque) e a escolha vale", async () => {
  await abrirResumo();
  await tocar(card().getByRole("button", { name: "60 dias" }));
  const pediu = (n: number) => fetchFalso.mock.calls.some(([u]) => String(u).includes(`dias=${n}`)); // o mock devolve sempre o mesmo horizonte: a escolha prova-se pelo pedido
  await waitFor(() => expect(pediu(60)).toBe(true)); semDetalhes();
  await tocar(titulo()); await tocar(card().getByRole("button", { name: "90 dias" })); await waitFor(() => expect(pediu(90)).toBe(true));
  expect(titulo().props.accessibilityState).toMatchObject({ expanded: true }); // aberto continua aberto
});
it("estados de erro ficam visíveis com o card recolhido (não são explicação)", async () => {
  previsao = { ...F[PREV], estado: "indisponivel", base: { ...F[PREV].base, bancos_excluidos: true } };
  await abrirResumo();
  expect(card().getByText("Previsão indisponível")).toBeTruthy(); expect(card().getByText("Bancos conectados fora do cálculo")).toBeTruthy(); semDetalhes();
});
it("respondidas moram dentro do card: escondidas recolhido; aberto, a linha 'Receitas na previsão' e o Desfazer seguem funcionando", async () => {
  previsao = { ...F[PREV], receitas_do_banco: [{ chave: "extrato#alu", nome: "Aluguel Carla", valor: "1200.00", dia: 10, status: "confirmada" }] };
  await abrirResumo(); await act(drenar);
  expect(screen.queryByText(/Receitas na previsão/)).toBeNull(); expect(screen.queryByRole("button", { name: /Desfazer resposta/ })).toBeNull();
  await tocar(titulo()); await tocar(card().getByText("Receitas na previsão (1)"));
  expect(card().getByRole("button", { name: "Desfazer resposta: Aluguel Carla" })).toBeTruthy();
});
it("valores ocultos: recolhido ou aberto, o card não mostra número nem curva", async () => {
  await abrirResumo(); await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); await drenar(); });
  expect(card().queryByLabelText(/reais/)).toBeNull(); await tocar(titulo());
  expect(card().queryByLabelText(/reais/)).toBeNull(); expect(card().getByText("Gráfico oculto")).toBeTruthy();
});
it("VoiceOver: o título é o botão com estado expandido (o card externo não é um segundo alvo anunciado)", async () => {
  await abrirResumo(); expect(screen.getByTestId("bloco-hero").props.accessible).toBe(false); expect(card().getAllByRole("button", { name: "Saldo previsto" })).toHaveLength(1);
  expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalled();
});

describe("sem a previsão na tela o alvo existe mas está desativado (sem seta, sem estado invisível)", () => {
  const ocioso = () => { expect(titulo().props.accessibilityState).toMatchObject({ disabled: true }); expect(titulo().props.accessibilityState.expanded).toBeUndefined(); };
  it("carregando: o card já está montado e desativado; tocar não deixa nada aberto para quando o dado chegar", async () => {
    const s = segurar(); previsaoResp = async () => { await s.promessa; return resposta(200, previsao); };
    renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByTestId("bloco-hero")).toBeTruthy()); await act(drenar);
    ocioso(); await tocar(screen.getByTestId("bloco-hero")); await tocar(titulo());
    s.soltar(); await waitFor(() => expect(screen.getByText("A partir de hoje, 06/10")).toBeTruthy());
    expect(titulo().props.accessibilityState).toMatchObject({ expanded: false }); semDetalhes();
  });
  it.each([["erro", 500], ["negado", 403]])("%s: desativado", async (_n, status) => {
    previsaoResp = async () => resposta(status, status === 403 ? { detail: { error: "pro_required", feature: "forecast" } } : {});
    renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByTestId("bloco-hero")).toBeTruthy()); for (let i = 0; i < 6; i++) await act(drenar);
    expect(screen.queryByText("A partir de hoje, 06/10")).toBeNull(); ocioso();
  });
  it("a troca de horizonte (o card volta a carregar) mantém o card, o aberto e o pior dia aberto", async () => {
    const o = { ...F[PREV].compromissos[0].ocorrencias[0], chave: "alug", ciclo: "2026-10", data: "2026-10-15", valor: "1800.00", nome: "Aluguel", tipo: "gasto_fixo" };
    previsao = { ...F[PREV], pior_dia: { data: "2026-10-25", saldo: "-120.00", desde: "2026-10-14", causas: [o] } };
    await abrirResumo(); const raiz = screen.getByTestId("bloco-hero");
    await tocar(titulo()); await tocar(screen.getByTestId("pior-dia")); expect(card().getByText("Aluguel")).toBeTruthy();
    const s = segurar(); previsaoResp = async () => { await s.promessa; return resposta(200, previsao); };
    await tocar(card().getByRole("button", { name: "60 dias" })); await act(drenar);
    expect(screen.getByTestId("bloco-hero")).toBe(raiz); // o mesmo nó: nada remontou
    s.soltar(); await waitFor(() => expect(card().getByText("Aluguel")).toBeTruthy());
    expect(titulo().props.accessibilityState).toMatchObject({ expanded: true }); expect(screen.getByTestId("bloco-hero")).toBe(raiz);
  });
});
