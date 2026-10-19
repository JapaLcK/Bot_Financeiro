// Card "Saldo previsto" do Resumo: compacto (horizonte 30/60/90, data, saldo e a CURVA, sempre visível); o card INTEIRO abre a folha com o resto (âncora, marcos, pior dia, receitas).
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
const card = () => within(screen.getByTestId("bloco-hero")), folha = () => within(screen.getByTestId("folha-sheet"));
const titulo = () => card().getByRole("button", { name: "Saldo previsto, ver detalhes" });
const tocar = async (alvo: Parameters<typeof fireEvent.press>[0]) => { await act(async () => { fireEvent.press(alvo); await drenar(); }); };
const DETALHES = ["Hoje, com as premissas", /^30 dias · /, /^Menor saldo previsto/], CURVA = /^Saldo previsto ao longo do período/;
async function abrirResumo() { renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("A partir de hoje, 06/10")).toBeTruthy()); }
const semDetalhes = () => { for (const t of DETALHES) expect(card().queryByText(t)).toBeNull(); };
const comDetalhes = () => { expect(folha().getByText("Hoje, com as premissas")).toBeTruthy(); expect(folha().getByText(/^30 dias · /)).toBeTruthy(); expect(folha().getByLabelText(CURVA)).toBeTruthy(); };

it("compacto: horizonte, data, saldo e a curva sempre à vista; sem marcos, âncora, pior dia nem 'Como foi feita'", async () => {
  await abrirResumo();
  for (const dias of F[PREV].dias_permitidos) expect(card().getByRole("button", { name: `${dias} dias` })).toBeTruthy();
  expect(card().getByText("A partir de hoje, 06/10")).toBeTruthy(); expect(card().getAllByLabelText(/reais/).length).toBeGreaterThan(0);
  expect(card().getByLabelText(CURVA)).toBeTruthy(); // o gráfico não depende do toque
  semDetalhes(); expect(card().queryByText("Como foi feita")).toBeNull(); expect(card().queryByTestId("como-foi-feita")).toBeNull();
  expect(titulo().props.accessibilityState.expanded).toBeUndefined(); // abre outra tela: não é recolher/expandir
  expect(screen.queryByTestId("folha-sheet")).toBeNull(); expect(screen.queryByRole("button", { name: "Abrir Saldo previsto" })).toBeNull();
});
it("o card INTEIRO é o alvo: tocar no cartão ou no título abre a folha 'Saldo previsto' com os marcos; o card segue compacto", async () => {
  await abrirResumo();
  await tocar(screen.getByTestId("bloco-hero")); comDetalhes(); semDetalhes();
  expect(screen.getByRole("header", { name: "Saldo previsto" })).toBeTruthy(); expect(card().getByLabelText(CURVA)).toBeTruthy(); // a curva do card continua lá, atrás da folha
  await tocar(screen.getByRole("button", { name: "Fechar" })); await waitFor(() => expect(screen.queryByTestId("folha-sheet")).toBeNull());
  await tocar(titulo()); comDetalhes();
});
it("trocar o horizonte NÃO abre a folha (o chip toma o toque); dentro da folha, o chip muda o horizonte e a folha segue aberta", async () => {
  await abrirResumo();
  await tocar(card().getByRole("button", { name: "60 dias" }));
  const pediu = (n: number) => fetchFalso.mock.calls.some(([u]) => String(u).includes(`dias=${n}`)); // o mock devolve sempre o mesmo horizonte: a escolha prova-se pelo pedido
  await waitFor(() => expect(pediu(60)).toBe(true)); expect(screen.queryByTestId("folha-sheet")).toBeNull();
  await tocar(titulo()); await tocar(folha().getByRole("button", { name: "90 dias" })); await waitFor(() => expect(pediu(90)).toBe(true));
  expect(screen.getByTestId("folha-sheet")).toBeTruthy();
});
it("estados de erro ficam visíveis no card compacto (não são explicação)", async () => {
  previsao = { ...F[PREV], estado: "indisponivel", base: { ...F[PREV].base, bancos_excluidos: true } };
  await abrirResumo();
  expect(card().getByText("Previsão indisponível")).toBeTruthy(); expect(card().getByText("Bancos conectados fora do cálculo")).toBeTruthy(); semDetalhes();
});
it("respondidas moram na folha: o card compacto não as tem; na folha, a linha 'Receitas na previsão' e o Desfazer funcionam", async () => {
  previsao = { ...F[PREV], receitas_do_banco: [{ chave: "extrato#alu", nome: "Aluguel Carla", valor: "1200.00", dia: 10, status: "confirmada" }] };
  await abrirResumo(); await act(drenar);
  expect(screen.queryByText(/Receitas na previsão/)).toBeNull(); expect(screen.queryByRole("button", { name: /Desfazer resposta/ })).toBeNull();
  await tocar(titulo()); await tocar(folha().getByText("Receitas na previsão (1)"));
  expect(folha().getByRole("button", { name: "Desfazer resposta: Aluguel Carla" })).toBeTruthy(); expect(card().queryByText(/Receitas na previsão/)).toBeNull();
});
it("valores ocultos: card e folha não mostram número, só 'Gráfico oculto'", async () => {
  await abrirResumo(); await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Ocultar valores" })); await drenar(); });
  expect(card().queryByLabelText(/reais/)).toBeNull(); expect(card().getByText("Gráfico oculto")).toBeTruthy(); await tocar(titulo());
  expect(folha().queryByLabelText(/reais/)).toBeNull(); expect(folha().getByText("Gráfico oculto")).toBeTruthy();
});
it("VoiceOver: o título é o botão 'ver detalhes' (o card externo não é um segundo alvo anunciado)", async () => {
  await abrirResumo(); expect(screen.getByTestId("bloco-hero").props.accessible).toBe(false); expect(card().getAllByRole("button", { name: /^Saldo previsto/ })).toHaveLength(1);
  expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalled();
});

describe("sem a previsão na tela o alvo existe mas está desativado e o toque não abre nada", () => {
  const ocioso = () => { expect(titulo().props.accessibilityState).toMatchObject({ disabled: true }); };
  it("carregando: o card já está montado e desativado; tocar não abre folha, nem a deixa pendente para quando o dado chegar", async () => {
    const s = segurar(); previsaoResp = async () => { await s.promessa; return resposta(200, previsao); };
    renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByTestId("bloco-hero")).toBeTruthy()); await act(drenar);
    ocioso(); await tocar(screen.getByTestId("bloco-hero")); await tocar(titulo()); expect(screen.queryByTestId("folha-sheet")).toBeNull();
    s.soltar(); await waitFor(() => expect(screen.getByText("A partir de hoje, 06/10")).toBeTruthy());
    expect(screen.queryByTestId("folha-sheet")).toBeNull(); semDetalhes();
  });
  it.each([["erro", 500], ["negado", 403]])("%s: desativado e sem folha", async (_n, status) => {
    previsaoResp = async () => resposta(status, status === 403 ? { detail: { error: "pro_required", feature: "forecast" } } : {});
    renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByTestId("bloco-hero")).toBeTruthy()); for (let i = 0; i < 6; i++) await act(drenar);
    expect(screen.queryByText("A partir de hoje, 06/10")).toBeNull(); ocioso(); await tocar(screen.getByTestId("bloco-hero")); expect(screen.queryByTestId("folha-sheet")).toBeNull();
  });
  it("a troca de horizonte (o card volta a carregar) mantém o card montado e a folha e o pior dia abertos", async () => {
    const o = { ...F[PREV].compromissos[0].ocorrencias[0], chave: "alug", ciclo: "2026-10", data: "2026-10-15", valor: "1800.00", nome: "Aluguel", tipo: "gasto_fixo" };
    previsao = { ...F[PREV], pior_dia: { data: "2026-10-25", saldo: "-120.00", desde: "2026-10-14", causas: [o] } };
    await abrirResumo(); const raiz = screen.getByTestId("bloco-hero");
    await tocar(titulo()); await tocar(screen.getByTestId("pior-dia")); expect(folha().getByText("Aluguel")).toBeTruthy();
    const s = segurar(); previsaoResp = async () => { await s.promessa; return resposta(200, previsao); };
    await tocar(card().getByRole("button", { name: "60 dias" })); await act(drenar);
    expect(screen.getByTestId("bloco-hero")).toBe(raiz); // o mesmo nó: nada remontou
    s.soltar(); await waitFor(() => expect(folha().getByText("Aluguel")).toBeTruthy());
    expect(screen.getByTestId("folha-sheet")).toBeTruthy(); expect(screen.getByTestId("bloco-hero")).toBe(raiz);
  });
  it("pronto e depois a previsão recarrega com erro: o card volta a ficar desativado (o cleanup do `Pronto` desliga o alvo)", async () => {
    await abrirResumo(); expect(titulo().props.accessibilityState).toMatchObject({ disabled: false });
    previsaoResp = async () => resposta(500, { error: "falha" });
    await tocar(card().getByRole("button", { name: "60 dias" }));
    await waitFor(() => expect(titulo().props.accessibilityState).toMatchObject({ disabled: true }));
    await tocar(titulo()); expect(screen.queryByTestId("folha-sheet")).toBeNull();
  });
});
