import { act, fireEvent, screen, waitFor, within } from "expo-router/testing-library";
import { AGENTES } from "@/features/painel/agentes";
import { decimalParaCentavos } from "@/features/painel/decimal";
import { totalRendaFixa } from "@/features/painel/cardInvestido";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { abrir, apertar, caminho, enviar, ir, reiniciar, trocarParaBela } from "./conversa_apoio";
import v2Json from "../../../tests/frontend/api_v2_respostas.json";
import rendaFixaJson from "../../../tests/frontend/total_renda_fixa.json";
// O card abaixo da resposta do `@Agente` (#1197): o servidor diz QUAL card, o app lê a rota `/api/app` do tipo e só desenha se o `/auth/me` DEPOIS ainda é o dono do painel.
jest.setTimeout(30_000);
const v2 = v2Json as unknown as Record<string, Record<string, object>>;
type R = Response | Promise<Response>;
const S = { chat: (() => resposta(200, {})) as (n: number) => R, rotas: new Map<string, () => R>(), meUid: null as number | null, nChat: 0 };
const cat = (uid: number) => ({ catalog: AGENTES.map((a) => ({ kind: a.kind, nome: a.nome, disponivel: true, status: "active", energy_cost: 2 })), can_chat: true, can_activate: true, energy_enabled: true, energy_budget: 10, energy_used: uid === 1 ? 2 : 3 });
const abortavel = (r: R, sg?: AbortSignal | null): R => !sg ? r : new Promise((res, rej) => { const err = () => rej(Object.assign(new Error("Aborted"), { name: "AbortError" })); if (sg.aborted) err(); sg.addEventListener("abort", err); Promise.resolve(r).then(res, rej); });
const chave = (u: unknown) => caminho(u) + new URL(String(u)).search;
const gets = (rota: string) => fetchFalso.mock.calls.filter(([u]) => chave(u) === rota);
const comCard = (card: unknown, reply = "Resposta") => { S.chat = () => resposta(200, { reply, context: null, ...(card === undefined ? {} : { card }) }); };
const rota = (k: string, corpo: object | (() => R)) => S.rotas.set(k, typeof corpo === "function" ? (corpo as () => R) : () => resposta(200, corpo));
function ligar() {
  Object.assign(S, { chat: () => resposta(200, { reply: "Resposta", context: null }), meUid: null, nChat: 0 }); S.rotas.clear();
  const base = fetchFalso.getMockImplementation()!;
  fetchFalso.mockImplementation((url: string, req: RequestInit) => {
    const p = caminho(url), k = chave(url), m = /^\/agents\/(\d+)(?:\/(\w+)\/chat)?$/.exec(p);
    if (S.meUid !== null && p === "/auth/me") return abortavel(resposta(200, { user_id: S.meUid, display_name: "x", app_access: true }), req?.signal);
    const r = m ? (m[2] ? S.chat(++S.nChat) : resposta(200, cat(Number(m[1])))) : S.rotas.get(k)?.();
    return r ? abortavel(r, req?.signal) : base(url, req);
  });
}
beforeEach(async () => { prepararCaso(); desligarTrava(); await reiniciar(); ligar(); });
afterEach(() => { jest.restoreAllMocks(); });
const perguntar = async (texto = "@Xerife oi") => { await abrir(); await enviar(texto); await waitFor(() => expect(screen.getByText("Resposta")).toBeTruthy()); };
const RESUMO = (mes: string, entrou: string, saiu: string) => ({ ...v2.resumo_do_mes!.exato, mes, entrou, saiu });
const R_INV = "/api/app/investido", R_CONTAS = "/api/app/contas-a-pagar", R_ORC = "/api/app/orcamentos-categoria", R_CX = "/api/app/caixinhas", R_ASS = "/api/app/assinaturas";
const R_MAR = "/api/app/resumo-do-mes?mes=2019-03", R_FEV = "/api/app/resumo-do-mes?mes=2019-02";
const TODAS = [R_INV, R_CONTAS, R_ORC, R_CX, R_MAR, R_FEV];
const semGets = () => { for (const r of TODAS) expect(gets(r)).toHaveLength(0); };

// [nome, card do servidor, rotas lidas, o número-chave na tela, título]
const CASOS: [string, object, Record<string, object>, RegExp, string][] = [
  ["investido", { tipo: "investido" }, { [R_INV]: v2.investido!.com_banco! }, /57\.123,45/, "Investimentos"],
  ["renda_fixa (soma renda fixa + Tesouro, sem as ações)", { tipo: "renda_fixa" }, { [R_INV]: v2.investido!.com_banco! }, /40\.000,00/, "Renda fixa"],
  ["assinaturas (o widget do app, só leitura)", { tipo: "assinaturas" }, { [R_ASS]: v2.assinaturas!.cheia! }, /Smart Fit/, "Assinaturas"],
  ["caixinhas (v2)", { tipo: "caixinhas" }, { [R_CX]: v2.caixinhas!.cheia! }, /7\.350,50/, "Caixinhas"],
  ["contas", { tipo: "contas", hoje: "2026-10-20" }, { [R_CONTAS]: v2.contas_a_pagar!.cheia! }, /1\.245,60/, "Contas a pagar"],
  ["orcamentos", { tipo: "orcamentos", mes: "2026-10" }, { [R_ORC]: v2.orcamentos_categoria!.padrao! }, /1\.053,80/, "Orçamentos de outubro de 2026"],
  ["resumo_mes", { tipo: "resumo_mes", mes: "2019-03", motivos: [] }, { [R_MAR]: RESUMO("2019-03", "1040.00", "440.00") }, /600,00/, "Resumo de março de 2019"],
  ["comparacao_meses", { tipo: "comparacao_meses", meses: ["2019-02", "2019-03"], motivos: [] }, { [R_FEV]: RESUMO("2019-02", "1000.00", "500.00"), [R_MAR]: RESUMO("2019-03", "1040.00", "440.00") }, /\+R\$ 100,00/, "Fevereiro × março de 2019"],
];
it.each(CASOS)("positivo, %s: lê a rota do tipo, confere o /auth/me DEPOIS e desenha o número", async (_n, card, rotas, numero, titulo) => {
  for (const [k, c] of Object.entries(rotas)) rota(k, c);
  comCard(card); await perguntar();
  await waitFor(() => expect(screen.getAllByText(numero).length).toBeGreaterThan(0));
  const tipo = (card as { tipo: string }).tipo; expect(within(screen.getByTestId(`bloco-${tipo}`)).getAllByText(titulo).length).toBeGreaterThan(0);
  const chamadas = fetchFalso.mock.calls, post = chamadas.findIndex(([u]) => /^\/agents\/\d+\/\w+\/chat$/.test(caminho(u))); // o provider já leu `assinaturas` ANTES do chat: conta só o GET do card
  const i = chamadas.findIndex(([u], j) => j > post && chave(u) === Object.keys(rotas)[0]), ultimo = fetchFalso.mock.calls.map(([u]) => caminho(u)).lastIndexOf("/auth/me");
  expect(post).toBeGreaterThan(-1); expect(i).toBeGreaterThan(post); expect(ultimo).toBeGreaterThan(i); // o /auth/me depois da leitura
  expect(screen.queryByRole("button", { name: "Tentar de novo" })).toBeNull();
});
it("assinaturas com a mesma chave em cadeias diferentes: nenhuma key repetida no React", async () => {
  const erro = jest.spyOn(console, "error"); rota(R_ASS, v2.assinaturas!.cheia!); comCard({ tipo: "assinaturas" }); await perguntar();
  await waitFor(() => expect(screen.getAllByText(/Smart Fit/).length).toBeGreaterThan(0));
  const repetidas = erro.mock.calls.filter((c) => /same key/.test(String(c[0]))); erro.mockRestore();
  expect(repetidas).toEqual([]);
});
it("rode a conversa: '@Detetive quanto rendeu meus investimentos?' -> 'Perguntar ao Faria Limer' -> card Investimentos abaixo da resposta", async () => {
  rota(R_INV, v2.investido!.com_banco!);
  S.chat = (n) => resposta(200, n === 1 ? { reply: "Isso é com o Faria Limer.", context: null, redirects: [{ kind: "faria_limer", name: "Faria Limer", question: "Quanto rendeu meus investimentos?", access: "ready" }] } : { reply: "Você tem R$ 57 mil.", context: null, card: { tipo: "investido" } });
  await abrir(); await enviar("@Detetive quanto rendeu meus investimentos?"); await waitFor(() => screen.getByText("Isso é com o Faria Limer."));
  expect(gets(R_INV)).toHaveLength(0); await apertar("Perguntar ao Faria Limer");
  await waitFor(() => expect(screen.getByText(/57\.123,45/)).toBeTruthy()); expect(screen.getByText("Você tem R$ 57 mil.")).toBeTruthy();
});

// ADITIVO: o card nunca derruba a resposta. Controle negativo: sem o `.catch(null)` do `respostaAgenteSchema`, o schema falha e a resposta some (vermelho).
it.each([["ausente", undefined], ["tipo desconhecido", { tipo: "xyz" }], ["mês inválido", { tipo: "orcamentos", mes: "2026-13" }], ["comparação invertida", { tipo: "comparacao_meses", meses: ["2026-03", "2026-02"], motivos: [] }], ["contas sem dia", { tipo: "contas" }], ["null", null]])("card %s: a resposta fica, sem card e sem GET", async (_n, card) => {
  comCard(card); await perguntar();
  await act(drenar); semGets(); expect(screen.queryByTestId(/^bloco-/)).toBeNull();
});

it.each([403, 401])("%i: nenhum card e nenhum erro (o texto já disse o que importa)", async (status) => {
  rota(R_INV, () => resposta(status, { detail: "x" })); comCard({ tipo: "investido" });
  await abrir(); await enviar("@Xerife oi"); await waitFor(() => expect(gets(R_INV).length).toBeGreaterThan(0)); await act(drenar);
  expect(screen.queryByTestId("bloco-investido")).toBeNull(); expect(screen.queryByText("Não deu para carregar os dados agora.")).toBeNull();
  if (status === 401) await waitFor(() => expect(screen.queryByLabelText("Mensagem para o Piggy")).toBeNull()); // SessaoExpirada -> `falhou` -> a sessão expira e o painel sai
});
it("500: o card diz que não deu e 'Tentar de novo' desenha; corpo fora do contrato dá o mesmo erro, nunca meio número", async () => {
  let n = 0; rota(R_INV, () => ++n === 1 ? resposta(500, { detail: "x" }) : n === 2 ? resposta(200, { total: "abc", por_tipo: [], motivos: [] }) : resposta(200, v2.investido!.com_banco!)); comCard({ tipo: "investido" });
  await perguntar(); await waitFor(() => screen.getByText("Não deu para carregar os dados agora."));
  await apertar("Tentar de novo"); await waitFor(() => expect(n).toBe(2)); await waitFor(() => screen.getByText("Não deu para carregar os dados agora.")); expect(screen.queryByText(/abc/)).toBeNull();
  await apertar("Tentar de novo"); await waitFor(() => expect(screen.getByText(/57\.123,45/)).toBeTruthy());
});
it.each([["contas com outro dia", { tipo: "contas", hoje: "2026-10-19" }, R_CONTAS, () => v2.contas_a_pagar!.cheia!], ["orçamentos de outro mês", { tipo: "orcamentos", mes: "2026-09" }, R_ORC, () => v2.orcamentos_categoria!.padrao!], ["resumo de outro mês", { tipo: "resumo_mes", mes: "2019-03", motivos: [] }, R_MAR, () => RESUMO("2019-04", "1.00", "1.00")]])("%s: nenhum card (resumo de outro mês é erro de contrato: nunca o número do mês errado)", async (_n, card, r, corpo) => {
  rota(r, corpo()); comCard(card); await perguntar(); await waitFor(() => expect(gets(r).length).toBeGreaterThan(0)); await act(drenar);
  expect(screen.queryByText(/1\.245,60|1\.053,80|\b1,00/)).toBeNull(); const erro = screen.queryByText("Não deu para carregar os dados agora.") !== null, bloco = screen.queryByTestId(/^bloco-/);
  if (_n.startsWith("resumo")) expect(erro || bloco === null).toBe(true); else { expect(erro).toBe(false); expect(bloco).toBeNull(); } // contas e orçamentos de outro período: nenhum card, nem erro
});

// ISOLAMENTO: a rota lê a SESSÃO, não o uid do POST.
it("(a) o POST da Ana volta com card, o GET fica segurado e a Bela entra: nenhum número da Ana e o sinal do GET aborta", async () => {
  const g = segurar(); rota(R_INV, async () => { await g.promessa; return resposta(200, v2.investido!.com_banco!); }); comCard({ tipo: "investido" });
  await abrir(); await enviar("@Xerife oi"); await waitFor(() => expect(gets(R_INV)).toHaveLength(1));
  const sinal = gets(R_INV)[0]![1].signal as AbortSignal; await trocarParaBela(); await waitFor(() => expect(sinal.aborted).toBe(true));
  await act(async () => { g.soltar(); await drenar(); await drenar(); });
  expect(screen.queryByText(/57\.123,45/)).toBeNull(); expect(screen.queryByTestId("bloco-investido")).toBeNull();
});
it("sair da aba com o GET do card segurado: o sinal aborta (o cleanup do efeito) e nada aparece", async () => {
  const g = segurar(); rota(R_INV, async () => { await g.promessa; return resposta(200, v2.investido!.com_banco!); }); comCard({ tipo: "investido" });
  await abrir(); await enviar("@Xerife oi"); await waitFor(() => expect(gets(R_INV)).toHaveLength(1));
  const sinal = gets(R_INV)[0]![1].signal as AbortSignal; await ir("/resumo"); await waitFor(() => expect(sinal.aborted).toBe(true));
  await act(async () => { g.soltar(); await drenar(); });
});
// O painel é da Ana; o /auth/me DEPOIS da leitura responde `uid`. Só o dono do painel (1) vê o número.
it.each([[2, false], [1, true]])("(b) /auth/me depois da leitura devolve %i: número na tela = %s", async (uid, aparece) => {
  const g = segurar(); rota(R_INV, async () => { await g.promessa; return resposta(200, v2.investido!.com_banco!); }); comCard({ tipo: "investido" });
  await abrir(); await enviar("@Xerife oi"); await waitFor(() => expect(gets(R_INV).length).toBeGreaterThan(0));
  S.meUid = uid; await act(async () => { g.soltar(); await drenar(); await drenar(); });
  if (aparece) { await waitFor(() => expect(screen.getByText(/57\.123,45/)).toBeTruthy()); return; }
  expect(screen.queryByText(/57\.123,45/)).toBeNull(); expect(screen.queryByTestId("bloco-investido")).toBeNull(); expect(screen.queryByText("Não deu para carregar os dados agora.")).toBeNull();
});
// item 2: o `op.atual()` do catch. Sair do app com o GET segurado aborta a leitura; o abort não é erro do card.
it("segundo plano com o GET do card segurado: o abort não vira 'Não deu para carregar'", async () => {
  const g = segurar(); rota(R_INV, async () => { await g.promessa; return resposta(200, v2.investido!.com_banco!); }); comCard({ tipo: "investido" });
  await abrir(); await enviar("@Xerife oi"); await waitFor(() => expect(gets(R_INV)).toHaveLength(1));
  await appVai("background"); await act(async () => { g.soltar(); await drenar(); await drenar(); });
  expect(screen.queryByText("Não deu para carregar os dados agora.")).toBeNull();
  await appVai("active"); await act(drenar);
});
// Releitura (volta do segundo plano): o número bom fica se ela falha, troca se ela dá certo, some se o /auth/me diz que a conta mudou.
const comNumero = async (relido: () => R) => {
  let n = 0; rota(R_INV, () => ++n === 1 ? resposta(200, v2.investido!.com_banco!) : relido()); comCard({ tipo: "investido" });
  await perguntar(); await waitFor(() => expect(screen.getByText(/57\.123,45/)).toBeTruthy());
  await appVai("background"); await appVai("active"); await waitFor(() => expect(n).toBeGreaterThan(1)); await act(drenar); await act(drenar);
};
it("releitura que falha (500): o número continua e não vira erro", async () => {
  await comNumero(() => resposta(500, { detail: "x" }));
  expect(screen.getByText(/57\.123,45/)).toBeTruthy(); expect(screen.queryByText("Não deu para carregar os dados agora.")).toBeNull();
});
it("releitura com sucesso: troca para o número novo", async () => {
  await comNumero(() => resposta(200, { ...v2.investido!.com_banco!, total: "1234.56" }));
  await waitFor(() => expect(screen.getByText(/1\.234,56/)).toBeTruthy()); expect(screen.queryByText(/57\.123,45/)).toBeNull();
});
it("releitura cujo /auth/me é de outra conta: o card some", async () => {
  await comNumero(() => { S.meUid = 2; return resposta(200, v2.investido!.com_banco!); });
  expect(screen.queryByText(/57\.123,45/)).toBeNull(); expect(screen.queryByTestId("bloco-investido")).toBeNull();
});
it.each([401, 403])("releitura com %i: o número some (sessão ou plano, o número não pode ficar)", async (status) => {
  await comNumero(() => resposta(status, { detail: "x" }));
  expect(screen.queryByText(/57\.123,45/)).toBeNull(); expect(screen.queryByTestId("bloco-investido")).toBeNull();
  // o `semAcesso` é provado pelo 403; o 401 é protegido pela saída do painel (`falhou` expira a sessão)
  if (status === 401) await waitFor(() => expect(screen.queryByLabelText("Mensagem para o Piggy")).toBeNull());
});
it.each([["contas", { tipo: "contas", hoje: "2026-10-20" }, R_CONTAS, v2.contas_a_pagar!.cheia!, { hoje: "2026-10-21" }, /1\.245,60/], ["orcamentos", { tipo: "orcamentos", mes: "2026-10" }, R_ORC, v2.orcamentos_categoria!.padrao!, { mes: "2026-11" }, /1\.053,80/]])("releitura de %s em que o período virou: o card some, sem erro", async (_n, card, r, corpo, outro, numero) => {
  let n = 0; rota(r, () => resposta(200, ++n === 1 ? corpo : { ...corpo, ...outro })); comCard(card);
  await perguntar(); await waitFor(() => expect(screen.getAllByText(numero).length).toBeGreaterThan(0));
  await appVai("background"); await appVai("active"); await waitFor(() => expect(n).toBeGreaterThan(1)); await act(drenar); await act(drenar);
  expect(screen.queryByText(numero)).toBeNull(); expect(screen.queryByTestId(/^bloco-/)).toBeNull(); expect(screen.queryByText("Não deu para carregar os dados agora.")).toBeNull();
});
// item 5: no chat não há o aviso do topo (só as abas têm `TelaPainel`), então o estado do banco vindo do card fica nas ressalvas do próprio card.
it("conexao_pausada vinda do card aparece nas ressalvas do card do chat", async () => {
  rota(R_INV, { ...v2.investido!.com_banco!, motivos: ["conexao_pausada"] }); comCard({ tipo: "investido" }); await perguntar();
  await waitFor(() => expect(screen.getByText(/57\.123,45/)).toBeTruthy()); await apertar("Ver ressalvas");
  expect(screen.getByText("Há uma conexão bancária pausada.")).toBeTruthy();
});
it("oculto: o turno com card não monta o card e não faz GET", async () => {
  const g = segurar(); S.chat = async () => { await g.promessa; return resposta(200, { reply: "Resposta", context: null, card: { tipo: "investido" } }); }; rota(R_INV, v2.investido!.com_banco!);
  await abrir(); await enviar("@Xerife oi"); await act(drenar);
  await act(async () => { fireEvent.press(screen.getByLabelText(/valores/i)); await drenar(); }); await act(async () => { g.soltar(); await drenar(); await drenar(); });
  expect(gets(R_INV)).toHaveLength(0); expect(screen.getByText("Mensagem oculta enquanto os valores estão privados.")).toBeTruthy();
});

it.each(rendaFixaJson as { total: string | null; partes: [string, string | null][]; esperado: string | null }[])("renda fixa = a soma do servidor ($total, $esperado)", ({ total, partes, esperado }) => {
  const c = (v: string | null) => v === null ? null : decimalParaCentavos(v);
  expect(totalRendaFixa(c(total), partes.map(([tipo, valor]) => ({ tipo, valor: c(valor) })))).toBe(c(esperado));
});
