import { readFileSync } from "fs";
import { join } from "path";
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { AGENTES, PERGUNTA, SOBRE } from "@/features/painel/agentes";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import fixture from "./painel.fixture.json";
// Tela Agentes (Ferramentas > Agentes): `renderRouter` + servidor falso; /agents/{uid} variável por caso.
jest.setTimeout(30_000);
let agentes: { status: number; corpo: object }; let freio: { me?: boolean; auth?: boolean }; // `agents_ui_enabled` do /api/app/me e do /auth/me (ausente = servidor antigo)
const cat = (over: object = {}) => ({ catalog: AGENTES.map((a) => ({ kind: a.kind, nome: a.nome, disponivel: true, status: a.kind === "xerife" ? "active" : null, energy_cost: 2 })), can_chat: true, can_activate: true, energy_enabled: true, energy_budget: 10, energy_used: 2, ...over });
const getsDeAgentes = () => fetchFalso.mock.calls.map(([u]) => new URL(String(u)).pathname).filter((p) => p.startsWith("/agents"));
beforeEach(async () => {
  prepararCaso(); desligarTrava(); agentes = { status: 200, corpo: cat() }; freio = {}; await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
  fetchFalso.mockImplementation(async (url: string) => {
    const path = new URL(url).pathname;
    if (path === "/auth/me") return resposta(200, { user_id: 1, display_name: "Ana", app_access: true, ...(freio.auth !== undefined && { agents_ui_enabled: freio.auth }) });
    if (path === "/agents/1") return resposta(agentes.status, agentes.status === 200 ? agentes.corpo : { detail: "x" });
    if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
    if (path === "/open-finance/1/limite") return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
    if (path === "/api/app/me") return resposta(200, { plan_tier: "pro", ...(freio.me !== undefined && { agents_ui_enabled: freio.me }) });
    if (path === "/auth/refresh") return resposta(401, { detail: "invalid_refresh_token" });
    return resposta(200, (fixture as Record<string, unknown>)[path] ?? {});
  });
});
const tocar = async (alvo: Parameters<typeof fireEvent.press>[0]) => { await act(async () => { fireEvent.press(alvo); await drenar(); }); };
async function abrirAgentes() {
  renderRouter("./app", { initialUrl: "/gastos" });
  await waitFor(() => expect(screen.getByTestId("painel-ferramentas")).toBeTruthy()); await act(drenar);
  await tocar(screen.getByTestId("painel-ferramentas")); await waitFor(() => expect(screen.getByText("Agentes")).toBeTruthy());
  await tocar(screen.getByRole("button", { name: /Agentes/ })); await waitFor(() => expect(screen).toHavePathname("/agentes"));
  await act(drenar);
}

it("a copy é a mesma do /painel (webapp/src/dashboard/widgets/Agentes.tsx, §0.7)", () => {
  const web = readFileSync(join(__dirname, "../../../webapp/src/dashboard/widgets/Agentes.tsx"), "utf8");
  for (const { kind } of AGENTES) {
    const s = SOBRE[kind]!;
    expect(web).toContain(`${kind}: { frase: "${s.frase}", texto: "${s.texto}", ritmo: "${s.ritmo}", foco: "${Math.round(s.foco * 100)}%" }`);
    expect(web).toContain(`${kind}: "${PERGUNTA[kind]}"`);
  }
});

it("Ferramentas > Agentes: lê /agents do dono da sessão, mostra a energia e o Xerife aberto; tocar outra faixa troca a aberta", async () => {
  await abrirAgentes();
  await waitFor(() => expect(screen.getByText("8 livres")).toBeTruthy());
  expect(getsDeAgentes()).toContain("/agents/1");
  for (const a of AGENTES) expect(screen.getAllByText(a.nome).length).toBeGreaterThan(0);
  expect(screen.getByText("De olho nos excessos.")).toBeTruthy(); expect(screen.getByText("No seu time")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Conversar" })).toBeTruthy();
  await tocar(screen.getByRole("button", { name: /^Repórter, Resumo do mês/ }));
  expect(screen.getByText("Seu mês, em pauta.")).toBeTruthy(); expect(screen.queryByText("De olho nos excessos.")).toBeNull();
  expect(screen.getByRole("button", { name: "Ativar" })).toBeTruthy();
});

it.each([["Conversar", "xerife"], ["Ativar", "reporter"]])("%s volta ao chat do painel com '@Nome pergunta' no campo, sem enviar", async (rotulo, kind) => {
  await abrirAgentes(); await waitFor(() => expect(screen.getByText("8 livres")).toBeTruthy());
  if (kind === "reporter") await tocar(screen.getByRole("button", { name: /^Repórter, Resumo do mês/ }));
  const antes = fetchFalso.mock.calls.length;
  await tocar(screen.getByRole("button", { name: rotulo }));
  await waitFor(() => expect(screen).toHavePathname("/piggy"));
  const nome = AGENTES.find((a) => a.kind === kind)!.nome;
  await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy").props.value).toBe(`@${nome} ${PERGUNTA[kind]}`));
  expect(fetchFalso.mock.calls.slice(antes).some(([u]) => /\/(chat|activate)$/.test(new URL(String(u)).pathname))).toBe(false);
  expect(screen.queryByText(/^Sete Piggys/)).toBeNull(); expect(screen.queryByText("Vale a pena comprar?")).toBeNull(); // volta ao painel de baixo: Agentes e Ferramentas saem da pilha
});

it("fora do plano: sem botão, com o aviso do PigBank+", async () => {
  agentes.corpo = cat({ can_chat: false }); await abrirAgentes();
  await waitFor(() => expect(screen.getByText("Conversar com o Xerife faz parte do PigBank+.")).toBeTruthy());
  expect(screen.getByText("Fora do plano")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Conversar" })).toBeNull(); expect(screen.queryByRole("button", { name: "Ativar" })).toBeNull();
});

it("sem energia livre: o Xerife (ativo) segue com Conversar; o Repórter diz 'Sem energia' fechado e, aberto, não tem botão", async () => {
  agentes.corpo = cat({ energy_used: 10 }); await abrirAgentes();
  await waitFor(() => expect(screen.getByText("0 livres")).toBeTruthy());
  expect(screen.getByRole("button", { name: "Conversar" })).toBeTruthy();
  await tocar(screen.getByRole("button", { name: /^Repórter, Resumo do mês, sem energia$/ }));
  expect(screen.getByText("O Repórter usa ⚡2 de energia e você tem 0 livre.")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Ativar" })).toBeNull(); expect(screen.queryByRole("button", { name: "Conversar" })).toBeNull();
});

it("orçamento 0: sem barra de energia", async () => {
  agentes.corpo = cat({ energy_budget: 0, energy_used: 0 }); await abrirAgentes();
  await waitFor(() => expect(screen.getByRole("button", { name: "Conversar" })).toBeTruthy());
  expect(screen.queryByText(/livres$/)).toBeNull();
});

it("/agents com erro: avisa e mostra o catálogo sem selo nem botão", async () => {
  agentes.status = 500; await abrirAgentes();
  await waitFor(() => expect(screen.getByText(/Não deu para carregar o estado dos seus agentes/)).toBeTruthy());
  expect(screen.getByText("De olho nos excessos.")).toBeTruthy();
  expect(screen.queryByText("No seu time")).toBeNull(); expect(screen.queryByRole("button", { name: "Conversar" })).toBeNull();
});

it("freio dos agentes (AGENTS_UI_ENABLED=0): Ferramentas sem a linha Agentes", async () => {
  freio.me = false; renderRouter("./app", { initialUrl: "/gastos" });
  await waitFor(() => expect(screen.getByTestId("painel-ferramentas")).toBeTruthy()); await act(drenar);
  await tocar(screen.getByTestId("painel-ferramentas")); await waitFor(() => expect(screen.getByText("Progresso ao milhão")).toBeTruthy());
  expect(screen.queryByText("Agentes")).toBeNull();
});

it("freio dos agentes com a tela já aberta: só o aviso, sem pedir /agents", async () => {
  freio.auth = false; renderRouter("./app", { initialUrl: "/agentes" });
  await waitFor(() => expect(screen.getByText("Os agentes estão indisponíveis no momento.")).toBeTruthy()); await act(drenar);
  expect(getsDeAgentes()).toEqual([]); expect(screen.queryByText("De olho nos excessos.")).toBeNull();
});

it("/piggy já aberto: link com outro `agente` (sem `entrada` nova) troca o '@Nome pergunta' do campo", async () => {
  renderRouter("./app", { initialUrl: "/piggy?agente=xerife" });
  await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy").props.value).toBe(`@Xerife ${PERGUNTA.xerife}`));
  await act(async () => { router.setParams({ agente: "reporter" }); await drenar(); });
  await waitFor(() => expect(screen.getByLabelText("Mensagem para o Piggy").props.value).toBe(`@Repórter ${PERGUNTA.reporter}`));
});
