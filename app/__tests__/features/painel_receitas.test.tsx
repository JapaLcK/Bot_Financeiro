import * as Haptics from "expo-haptics";
import { AccessibilityInfo, Dimensions, StyleSheet } from "react-native";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { trocarParaBela } from "./conversa_apoio";
import fixture from "./painel.fixture.json";
// Caixa "Receitas do extrato" (PR 2b). O "servidor" guarda chave -> status; o POST muda o mapa e o GET devolve a lista dele: roda a conversa, não a função.
type Pedido = (path: string, req: RequestInit) => Promise<Response> | undefined;
const BASE = [
 { chave: "extrato#sal", nome: "Salário Acme", valor: "5000.00", dia: 5 }, { chave: "extrato#free", nome: "Freela Beta", valor: "800.00", dia: 20 },
 { chave: "extrato#alu", nome: "Aluguel Carla", valor: "1200.00", dia: 10 }, { chave: "extrato#tio", nome: "Pix Tio", valor: "100.00", dia: 15 },
];
const ST = { sim: "confirmada", nao: "negada", desfazer: "pendente" } as const;
const ERRO = "Não deu para salvar. Tente de novo.", PREVISAO = "/api/app/previsao", POST = "/api/app/previsao/receita";
let marcas: Record<string, string>; let sem: Pedido | undefined; let perfil: string;
const lista = () => BASE.filter((r) => r.chave in marcas).map((r) => ({ ...r, status: marcas[r.chave] }));
const previsao = (dias: number, campo = true) => ({ ...fixture[PREVISAO], dias, ...(campo ? { receitas_do_banco: lista() } : {}) });
const caminho = (u: unknown) => new URL(String(u)).pathname;
const corpo = (req: RequestInit) => JSON.parse(String(req.body));
const bela = (req: RequestInit) => (req.headers as Record<string, string>).Authorization === "Bearer access-bela";
function servidor() {
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  const u = new URL(url), path = u.pathname;
  const extra = sem?.(path, req); if (extra) return extra;
  if (path === "/auth/me") return resposta(200, { user_id: bela(req) ? 2 : 1, display_name: bela(req) ? "Bela" : "Ana", app_access: true });
  if (path === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (/^\/open-finance\/\d\/limite$/.test(path)) return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (path === "/api/app/perfil") return resposta(200, { perfil });
  if (path === POST) { const c = corpo(req); if (!(c.chave in marcas)) return resposta(404, { detail: { error: "not_found" } }); marcas[c.chave] = ST[c.resposta as keyof typeof ST]; return resposta(200, previsao(30)); }
  if (path === PREVISAO) return resposta(200, previsao(Number(u.searchParams.get("dias")) || 30));
  return resposta(200, (fixture as Record<string, unknown>)[path.replace("/2/", "/1/")] ?? {});
 });
}
beforeEach(async () => {
 prepararCaso(); desligarTrava(); perfil = "padrao"; sem = undefined; jest.mocked(AccessibilityInfo.announceForAccessibility).mockClear(); jest.mocked(Haptics.notificationAsync).mockClear();
 marcas = { "extrato#sal": "pendente", "extrato#free": "pendente", "extrato#alu": "confirmada", "extrato#tio": "negada" };
 await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor();
});
afterEach(() => jest.restoreAllMocks());
const abrir = async () => { renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.queryByText("Receitas do extrato") ?? screen.queryByText(/^Já respondidas/)).toBeTruthy()); await act(drenar); };
const botao = (nome: string) => screen.getByRole("button", { name: nome });
const apertar = async (nome: string) => { await act(async () => { fireEvent.press(botao(nome)); await drenar(); }); };
const chamadas = (path: string) => fetchFalso.mock.calls.filter(([u]) => caminho(u) === path);
// A pilha mostra UM aviso por vez: o total de pendentes são os pontos (sem pontos, o aviso único visível).
const pendentes = () => screen.queryAllByTestId(/^pilha-ponto-/).length || screen.queryAllByText(/É sua receita fixa\?/).length;
const ver = async (k: number) => { await act(async () => { fireEvent.press(screen.getByTestId(`pilha-ponto-${k}`)); await drenar(); }); };
const todosAtivos = () => screen.getAllByRole("button").filter((b) => /receita fixa|Desfazer resposta/.test(String(b.props.accessibilityLabel)));
const anunciou = (t: string) => expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith(t);

it("mostra as pendentes com pergunta e botões rotulados, sem a frase de abertura; as respondidas ficam recolhidas", async () => {
 await abrir();
 expect(screen.getByText("Salário Acme")).toBeTruthy(); expect(screen.getByText("todo dia 5. É sua receita fixa?")).toBeTruthy(); expect(pendentes()).toBe(2);
 expect(botao("Sim, é receita fixa: Salário Acme")).toBeTruthy(); expect(botao("Não é receita fixa: Salário Acme")).toBeTruthy();
 expect(screen.queryByText(/Entram na previsão/)).toBeNull();
 expect(screen.getByText("Já respondidas (2)")).toBeTruthy(); expect(screen.queryByText("Aluguel Carla")).toBeNull();
 await act(async () => { fireEvent.press(screen.getByText("Já respondidas (2)")); await drenar(); });
 expect(screen.getByText("Confirmada · todo dia 10")).toBeTruthy(); expect(screen.getByText("Recusada · todo dia 15")).toBeTruthy();
 expect(botao("Desfazer resposta: Aluguel Carla")).toBeTruthy(); expect(botao("Desfazer resposta: Pix Tio")).toBeTruthy();
 const ordem = () => screen.getAllByText(/^(Salário Acme|Freela Beta|Aluguel Carla|Pix Tio)$/).map((n) => n.props.children);
 expect(ordem()).toEqual(["Salário Acme", "Aluguel Carla", "Pix Tio"]); await ver(1); // a pilha mostra uma pendente por vez: a 2ª vem pelo ponto 2
 expect(ordem()).toEqual(["Freela Beta", "Aluguel Carla", "Pix Tio"]); // a ordem do servidor, sem inverter
});
it("Sim: POST com Bearer e JSON, depois UMA releitura só da previsão; a linha muda de grupo e o resultado é anunciado", async () => {
 await abrir(); const antes = fetchFalso.mock.calls.length;
 await apertar("Sim, é receita fixa: Salário Acme");
 const novas = fetchFalso.mock.calls.slice(antes).map(([u]) => caminho(u)); expect(novas).toEqual([POST, PREVISAO]);
 const [, req] = chamadas(POST)[0]!; expect(corpo(req)).toEqual({ chave: "extrato#sal", resposta: "sim" });
 expect(req.method).toBe("POST"); expect(req.headers.Authorization).toBe("Bearer access-ana"); expect(req.headers["Content-Type"]).toBe("application/json");
 expect(pendentes()).toBe(1); expect(screen.getByText("Já respondidas (3)")).toBeTruthy(); anunciou("Salário Acme: confirmada."); expect(Haptics.notificationAsync).toHaveBeenCalledWith("success");
 expect(screen.queryByText(ERRO)).toBeNull(); expect(todosAtivos().every((b) => !b.props.accessibilityState.disabled)).toBe(true);
});
it.each([
 ["Não é receita fixa: Freela Beta", "extrato#free", "negada", "Freela Beta: recusada."],
 ["Sim, é receita fixa: Freela Beta", "extrato#free", "confirmada", "Freela Beta: confirmada."],
 ["Desfazer resposta: Aluguel Carla", "extrato#alu", "pendente", "Aluguel Carla: resposta desfeita."],
 ["Desfazer resposta: Pix Tio", "extrato#tio", "pendente", "Pix Tio: resposta desfeita."],
])("%s manda a chave certa (a segunda da lista também) e o servidor guarda %s", async (nome, chave, status, fala) => {
 await abrir(); await act(async () => { fireEvent.press(screen.getByText("Já respondidas (2)")); await drenar(); });
 if (nome.includes("Freela")) await ver(1); // a pilha mostra uma pendente por vez
 await apertar(nome); expect(corpo(chamadas(POST)[0]![1]).chave).toBe(chave); expect(marcas[chave]).toBe(status); anunciou(fala);
 expect(pendentes()).toBe(status === "pendente" ? 3 : 1);
});
it("durante o POST e a releitura TODOS os botões ficam travados, e só destravam quando a releitura volta", async () => {
 await abrir(); const post = segurar(), get = segurar(); let soltarGet = false;
 sem = (path, req) => path === POST ? post.promessa.then(() => { marcas[corpo(req).chave] = "confirmada"; soltarGet = true; return resposta(200, previsao(30)); }) : path === PREVISAO && soltarGet ? get.promessa.then(() => resposta(200, previsao(30))) : undefined;
 await act(async () => { fireEvent.press(botao("Sim, é receita fixa: Salário Acme")); await drenar(); });
 const travados = () => todosAtivos().every((b) => b.props.accessibilityState.disabled === true);
 expect(travados()).toBe(true); expect(todosAtivos().length).toBeGreaterThan(1);
 await act(async () => { post.soltar(); await drenar(); }); expect(travados()).toBe(true); expect(todosAtivos().length).toBeGreaterThan(1); // POST voltou, releitura no ar: a lista de antes fica na tela (travada), a caixa não pisca
 expect(chamadas(PREVISAO).length).toBeGreaterThan(1);
 await act(async () => { get.soltar(); await drenar(); await drenar(); });
 expect(todosAtivos().every((b) => b.props.accessibilityState.disabled === false)).toBe(true); expect(pendentes()).toBe(1);
});
it.each([["Sim", "Sim, é receita fixa: Salário Acme", "Sim, é receita fixa: Salário Acme"], ["Sim e Não", "Sim, é receita fixa: Salário Acme", "Não é receita fixa: Salário Acme"]])("duplo toque no mesmo tique (%s) manda UM POST só", async (_, a, b) => {
 await abrir();
 // UM `act` com os dois toques: sem render entre eles, o `disabled` não existe ainda e só a trava por ref (`voo`) segura o 2º. (A pilha mostra um aviso por vez: não há "outra receita" para tocar no mesmo tique.)
 await act(async () => { fireEvent.press(botao(a)); fireEvent.press(botao(b)); await drenar(); });
 expect(chamadas(POST)).toHaveLength(1);
});
it.each([
 ["500", () => resposta(500, { detail: "x" })], ["403", () => resposta(403, { detail: { error: "pro_required" } })], ["404", () => resposta(404, { detail: { error: "not_found" } })], ["rede", () => Promise.reject(new TypeError("Network request failed"))],
])("POST com %s e a releitura mostra a chave SEM o status esperado: Banner anunciado, lista intacta, 2ª tentativa funciona", async (_, falha) => {
 await abrir(); let falhas = 1; const antes = chamadas(PREVISAO).length;
 sem = (path) => path === POST && falhas-- > 0 ? Promise.resolve(falha()) : undefined;
 await apertar("Sim, é receita fixa: Salário Acme");
 expect(screen.getByText(ERRO)).toBeTruthy(); anunciou(ERRO); expect(Haptics.notificationAsync).toHaveBeenCalledWith("warning"); expect(pendentes()).toBe(2); expect(marcas["extrato#sal"]).toBe("pendente");
 expect(chamadas(PREVISAO).length).toBe(antes + 1); expect(todosAtivos().every((b) => !b.props.accessibilityState.disabled)).toBe(true);
 await apertar("Sim, é receita fixa: Salário Acme");
 expect(screen.queryByText(ERRO)).toBeNull(); expect(pendentes()).toBe(1); anunciou("Salário Acme: confirmada.");
});
it.each([
 ["Sim", "Sim, é receita fixa: Salário Acme", "confirmada", "Salário Acme: confirmada."],
 ["Não", "Não é receita fixa: Freela Beta", "negada", "Freela Beta: recusada."],
 ["Desfazer", "Desfazer resposta: Aluguel Carla", "pendente", "Aluguel Carla: resposta desfeita."],
])("resposta perdida com %s (o servidor gravou e a rede caiu): a releitura mostra o status esperado e vale como sucesso", async (_, nome, status, fala) => {
 await abrir(); await act(async () => { fireEvent.press(screen.getByText("Já respondidas (2)")); await drenar(); });
 sem = (path, req) => { if (path !== POST) return undefined; marcas[corpo(req).chave] = status; return Promise.reject(new TypeError("Network request failed")); };
 if (nome.includes("Freela")) await ver(1);
 await apertar(nome);
 expect(screen.queryByText(ERRO)).toBeNull(); anunciou(fala);
});
it("controle do anterior: o POST caiu SEM gravar e a chave SUMIU da releitura NÃO conta como gravada (falso erro > falso sucesso)", async () => {
 await abrir();
 sem = (path, req) => { if (path !== POST) return undefined; delete marcas[corpo(req).chave]; return Promise.reject(new TypeError("Network request failed")); };
 await apertar("Sim, é receita fixa: Salário Acme");
 expect(screen.getByText(ERRO)).toBeTruthy(); expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalledWith("Salário Acme: confirmada.");
});
it("POST ok e releitura 500: sem Banner, sucesso anunciado, e quando a lista volta no Tentar novamente os botões estão ativos", async () => {
 await abrir(); let cai = true;
 sem = (path) => path === PREVISAO && cai && marcas["extrato#sal"] === "confirmada" ? Promise.resolve(resposta(500, { detail: "x" })) : undefined;
 await apertar("Sim, é receita fixa: Salário Acme");
 expect(screen.queryByText(ERRO)).toBeNull(); anunciou("Salário Acme: confirmada.");
 await waitFor(() => expect(screen.getAllByRole("button", { name: "Tentar novamente" }).length).toBeGreaterThan(0));
 cai = false; await act(async () => { fireEvent.press(screen.getAllByRole("button", { name: "Tentar novamente" })[0]!); await drenar(); });
 await waitFor(() => expect(screen.getByText("Já respondidas (3)")).toBeTruthy()); expect(todosAtivos().every((b) => !b.props.accessibilityState.disabled)).toBe(true);
});
it("com 60 dias escolhidos, responder relê ?dias=60 e o chip 60 continua selecionado", async () => {
 await abrir(); await apertar("60 dias"); await waitFor(() => expect(botao("60 dias")).toBeTruthy());
 const antes = chamadas(PREVISAO).length; await apertar("Não é receita fixa: Salário Acme");
 const novas = chamadas(PREVISAO).slice(antes); expect(novas).toHaveLength(1); expect(new URL(String(novas[0]![0])).searchParams.get("dias")).toBe("60");
 expect(screen.getByRole("button", { name: "60 dias", selected: true })).toBeTruthy();
});
it("trocar o horizonte no mesmo tique em que o POST volta pede a previsão UMA vez só (uma condição, não dois spreads)", async () => {
 await abrir(); const post = segurar(); sem = (path, req) => path === POST ? post.promessa.then(() => { marcas[corpo(req).chave] = "confirmada"; return resposta(200, previsao(30)); }) : undefined;
 await act(async () => { fireEvent.press(botao("Sim, é receita fixa: Salário Acme")); await drenar(); }); const antes = chamadas(PREVISAO).length;
 await act(async () => { fireEvent.press(botao("60 dias")); post.soltar(); await drenar(); await drenar(); });
 await waitFor(() => expect(pendentes()).toBe(1)); expect(chamadas(PREVISAO).length - antes).toBe(1); expect(chamadas(PREVISAO).at(-1)![0]).toContain("dias=60");
});
it("falha seguida de outra falha: o Banner é remontado e o leitor de tela ouve o erro as DUAS vezes", async () => {
 await abrir(); sem = (path) => path === POST ? Promise.resolve(resposta(500, {})) : undefined;
 const erros = () => (AccessibilityInfo.announceForAccessibility as jest.Mock).mock.calls.filter(([t]) => t === ERRO).length;
 await apertar("Sim, é receita fixa: Salário Acme"); expect(screen.getByText(ERRO)).toBeTruthy(); expect(erros()).toBe(1);
 await apertar("Sim, é receita fixa: Salário Acme"); expect(screen.getByText(ERRO)).toBeTruthy(); expect(erros()).toBe(2); // o Banner só anuncia ao montar: sem apagar o aviso ao começar, o 2º erro sairia mudo
});
it("o aviso da falha continua quando a releitura que a detectou volta, e some numa leitura boa POSTERIOR", async () => {
 await abrir(); let falhas = 1; sem = (path) => path === POST && falhas-- > 0 ? Promise.resolve(resposta(500, {})) : undefined;
 await apertar("Sim, é receita fixa: Salário Acme"); await act(drenar); expect(screen.getByText(ERRO)).toBeTruthy(); // a releitura já voltou boa e o aviso ficou
 const g = segurar(); sem = (path) => path === PREVISAO ? g.promessa.then(() => resposta(200, previsao(60))) : undefined;
 await apertar("60 dias"); expect(screen.getByText(ERRO)).toBeTruthy(); // leitura nova ainda no ar: não é "boa", o aviso fica
 await act(async () => { g.soltar(); await drenar(); await drenar(); });
 await waitFor(() => expect(screen.getByRole("button", { name: "60 dias", selected: true })).toBeTruthy());
 expect(screen.queryByText(ERRO)).toBeNull(); expect(pendentes()).toBe(2);
});
it.each([[1, "row"], [2, "column"]])("Sim e Não com fontScale %s ficam em %s com flexWrap: quebram em vez de estourar a largura (a medida real é só no aparelho)", async (fontScale, direcao) => {
 const janela = Dimensions.get("window");
 try {
  Dimensions.set({ window: { ...janela, fontScale } }); await abrir(); let no = botao("Sim, é receita fixa: Salário Acme").parent;
  while (no && !StyleSheet.flatten(no.props.style)?.flexWrap) no = no.parent;
  expect(StyleSheet.flatten(no!.props.style)).toMatchObject({ flexDirection: direcao, flexWrap: "wrap" });
 } finally { Dimensions.set({ window: janela }); }
});
it("rede fora no POST e na releitura, rede volta e o Tentar novamente do card relê: o aviso velho some", async () => {
 await abrir(); let cai = true;
 sem = (path) => cai && (path === POST || path === PREVISAO) ? Promise.reject(new TypeError("Network request failed")) : undefined;
 await apertar("Sim, é receita fixa: Salário Acme");
 expect(screen.getByText(ERRO)).toBeTruthy(); expect(screen.getAllByRole("button", { name: "Tentar novamente" }).length).toBeGreaterThan(0); // a caixa só tem o aviso: sem lista pronta
 cai = false; await act(async () => { fireEvent.press(screen.getAllByRole("button", { name: "Tentar novamente" })[0]!); await drenar(); await drenar(); });
 await waitFor(() => expect(screen.getByText("Salário Acme")).toBeTruthy()); expect(screen.queryByText(ERRO)).toBeNull(); expect(pendentes()).toBe(2);
});
it("depois de responder, trocar o MÊS relê o mês e NÃO a previsão (a releitura já foi entregue)", async () => {
 await abrir(); await apertar("Sim, é receita fixa: Salário Acme");
 const antes = chamadas(PREVISAO).length, resumo = chamadas("/api/app/resumo-do-mes").length;
 await apertar("out"); await act(async () => { fireEvent.press(screen.getByText("setembro de 2026")); await drenar(); await drenar(); });
 expect(chamadas("/api/app/resumo-do-mes").length - resumo).toBeGreaterThan(0); expect(chamadas(PREVISAO).length - antes).toBe(0);
});
it.each([
 ["401 com a renovação recusada", () => resposta(401, { detail: "expirado" })],
 ["402", () => resposta(402, { detail: { error: "payment_required" } })],
])("POST com %s: o provider cuida (a caixa sai com o painel), sem Banner da caixa e sem anúncio de sucesso", async (_, falha) => {
 await abrir();
 sem = (path) => path === POST ? Promise.resolve(falha()) : path === "/auth/refresh" ? Promise.resolve(resposta(401, { detail: "invalid_refresh_token" })) : undefined;
 const antes = chamadas(PREVISAO).length; await apertar("Sim, é receita fixa: Salário Acme"); await act(drenar);
 expect(screen.queryByText("Receitas do extrato")).toBeNull(); expect(screen.queryByText(ERRO)).toBeNull(); expect(chamadas(PREVISAO).length).toBe(antes);
 expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalledWith("Salário Acme: confirmada.");
});
it("com 'Já respondidas' aberta, o Desfazer também fica travado durante a espera", async () => {
 await abrir(); await act(async () => { fireEvent.press(screen.getByText("Já respondidas (2)")); await drenar(); });
 const post = segurar(); sem = (path) => path === POST ? post.promessa.then(() => resposta(500, {})) : undefined;
 await act(async () => { fireEvent.press(botao("Sim, é receita fixa: Salário Acme")); await drenar(); });
 for (const nome of ["Desfazer resposta: Aluguel Carla", "Desfazer resposta: Pix Tio"]) expect(botao(nome).props.accessibilityState.disabled).toBe(true);
 await ver(1); expect(botao("Não é receita fixa: Freela Beta").props.accessibilityState.disabled).toBe(true); // a outra pendente também, mesmo escolhida depois
 await act(async () => { post.soltar(); await drenar(); await drenar(); });
});
it("o título da caixa é cabeçalho para o leitor de tela", async () => { await abrir(); expect(screen.getByRole("header", { name: "Receitas do extrato" })).toBeTruthy(); });
it("puxar para atualizar com o POST no ar aborta o POST, sem Banner, e os botões voltam ativos depois da recarga", async () => {
 await abrir(); const post = segurar();
 sem = (path, req) => path === POST ? new Promise((_, rejeitar) => { req.signal!.addEventListener("abort", () => rejeitar(new Error("abortada")), { once: true }); post.promessa.then(() => rejeitar(new Error("solta"))); }) : undefined;
 await act(async () => { fireEvent.press(botao("Sim, é receita fixa: Salário Acme")); await drenar(); });
 const [, req] = chamadas(POST)[0]!; expect(botao("Sim, é receita fixa: Salário Acme").props.accessibilityState.disabled).toBe(true);
 sem = undefined; await act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); await drenar(); });
 expect(req.signal.aborted).toBe(true); await waitFor(() => expect(botao("Sim, é receita fixa: Salário Acme").props.accessibilityState.disabled).toBe(false));
 expect(screen.queryByText(ERRO)).toBeNull(); expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalledWith("Salário Acme: confirmada.");
});
it("troca de conta com o POST no ar: nada da Ana na Bela (sem Banner, sem anúncio, sem lista, sem releitura extra) e os botões dela ativos", async () => {
 await abrir(); const post = segurar(); const ana = marcas; const dela = { "extrato#bela": "pendente" };
 sem = (path, req) => path === POST && !bela(req) ? post.promessa.then(() => { throw new TypeError("Network request failed"); }) : undefined;
 await act(async () => { fireEvent.press(botao("Sim, é receita fixa: Salário Acme")); await drenar(); });
 BASE.push({ chave: "extrato#bela", nome: "Pagadora Bela", valor: "300.00", dia: 9 });
 try {
  marcas = dela; await trocarParaBela(); await waitFor(() => expect(screen.getByText("Pagadora Bela")).toBeTruthy());
  const gets = chamadas(PREVISAO).filter(([, r]) => bela(r)).length;
  await act(async () => { post.soltar(); await drenar(); await drenar(); });
  expect(screen.queryByText(ERRO)).toBeNull(); expect(screen.queryByText("Salário Acme")).toBeNull(); expect(AccessibilityInfo.announceForAccessibility).not.toHaveBeenCalledWith(expect.stringContaining("Salário"));
  expect(chamadas(PREVISAO).filter(([, r]) => bela(r)).length).toBe(gets); expect(botao("Sim, é receita fixa: Pagadora Bela").props.accessibilityState.disabled).toBe(false);
 } finally { BASE.pop(); marcas = ana; }
});
it("o servidor sem o campo: sem caixa, e a Previsão normal (sem 'dados incompatíveis')", async () => {
 renderRouter("./app", { initialUrl: "/resumo" });
 sem = (path) => path === PREVISAO ? Promise.resolve(resposta(200, previsao(30, false))) : undefined;
 await waitFor(() => expect(screen.getByText("Saldo previsto")).toBeTruthy()); await act(drenar); expect(screen.queryByText("Receitas do extrato")).toBeNull();
 expect(screen.queryByText("Recebemos dados incompatíveis. Tente atualizar.")).toBeNull(); // o título "Saldo previsto" aparece até no erro de contrato: este é o que prova a Previsão de pé
});
it.each([
 ["lista vazia", () => ({ receitas_do_banco: [] })],
 ["item malformado", () => ({ receitas_do_banco: [{ ...BASE[0], valor: "abc", status: "x" }] })],
])("%s: sem caixa, e a Previsão segue de pé", async (_, extra) => {
 sem = (path) => path === PREVISAO ? Promise.resolve(resposta(200, { ...fixture[PREVISAO], ...extra() })) : undefined;
 renderRouter("./app", { initialUrl: "/resumo" });
 await waitFor(() => expect(screen.getByText("Saldo previsto")).toBeTruthy()); await act(drenar);
 expect(screen.queryByText("Receitas do extrato")).toBeNull(); expect(screen.queryByText("Recebemos dados incompatíveis. Tente atualizar.")).toBeNull();
});
it("POST 403 (plano caiu) e a releitura 403: previsão negada, sem caixa e sem aviso, nem depois de puxar para atualizar", async () => {
 await abrir(); const nega = () => Promise.resolve(resposta(403, { detail: { error: "pro_required", feature: "forecast" } }));
 sem = (path) => path === POST || path === PREVISAO ? nega() : undefined;
 await apertar("Sim, é receita fixa: Salário Acme"); await act(drenar);
 expect(screen.queryByText("Receitas do extrato")).toBeNull(); expect(screen.queryByText(ERRO)).toBeNull();
 await act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); await drenar(); await drenar(); });
 expect(screen.queryByText("Receitas do extrato")).toBeNull(); expect(screen.queryByText(ERRO)).toBeNull();
}); // o controle positivo (POST 500 com releitura 200 mostra o aviso) são os casos "POST com 500..." acima
// Previsão `r` x evento (caixa / aviso / anúncio novo do erro):
//  undefined (recarga completa): sem caixa, aviso apagado, 0 anúncios | carregando (horizonte): lista e aviso ficam | pronto: lista; aviso só da falha da própria releitura (1 anúncio)
//  erro: só o aviso, até o "Tentar novamente" | negado: sem caixa e sem aviso, nem na recarga, nem no upgrade (a recarga passa por undefined e apaga o estado)
const puxar = () => act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); await drenar(); });
const erros = () => (AccessibilityInfo.announceForAccessibility as jest.Mock).mock.calls.filter(([t]) => t === ERRO).length;
const nega = () => Promise.resolve(resposta(403, { detail: { error: "pro_required", feature: "forecast" } }));
it("A: plano negado (POST 403 e releitura 403) e puxar para atualizar com a releitura SEGURADA: sem caixa, sem aviso e 0 anúncios, durante e depois", async () => {
 await abrir(); sem = (path) => path === POST || path === PREVISAO ? nega() : undefined;
 await apertar("Sim, é receita fixa: Salário Acme"); await act(drenar); expect(screen.queryByText("Receitas do extrato")).toBeNull();
 const g = segurar(); sem = (path) => path === PREVISAO ? g.promessa.then(nega) : undefined;
 await puxar(); expect(screen.queryByText("Receitas do extrato")).toBeNull(); expect(screen.queryByText(ERRO)).toBeNull(); expect(erros()).toBe(0);
 await act(async () => { g.soltar(); await drenar(); await drenar(); });
 expect(screen.queryByText("Receitas do extrato")).toBeNull(); expect(screen.queryByText(ERRO)).toBeNull(); expect(erros()).toBe(0);
});
it("B: o plano volta (upgrade) e o usuário puxa para atualizar: a lista volta certa, sem aviso e sem o anúncio velho do erro", async () => {
 await abrir(); sem = (path) => path === POST || path === PREVISAO ? nega() : undefined;
 await apertar("Sim, é receita fixa: Salário Acme"); await act(drenar);
 const g = segurar(); sem = (path) => path === PREVISAO ? g.promessa.then(() => resposta(200, previsao(30))) : undefined;
 await puxar(); expect(screen.queryByText("Receitas do extrato")).toBeNull(); expect(erros()).toBe(0);
 await act(async () => { g.soltar(); await drenar(); await drenar(); });
 await waitFor(() => expect(pendentes()).toBe(2)); expect(screen.queryByText(ERRO)).toBeNull(); expect(erros()).toBe(0);
});
it("C: aviso visível (POST 500, releitura 200) e puxar para atualizar com a releitura SEGURADA: o aviso velho some na recarga e não é anunciado de novo", async () => {
 await abrir(); sem = (path) => path === POST ? Promise.resolve(resposta(500, {})) : undefined;
 await apertar("Sim, é receita fixa: Salário Acme"); await act(drenar); expect(screen.getByText(ERRO)).toBeTruthy(); expect(erros()).toBe(1);
 const g = segurar(); sem = (path) => path === PREVISAO ? g.promessa.then(() => resposta(200, previsao(30))) : undefined;
 await puxar(); expect(screen.queryByText("Receitas do extrato")).toBeNull(); expect(screen.queryByText(ERRO)).toBeNull();
 await act(async () => { g.soltar(); await drenar(); await drenar(); });
 await waitFor(() => expect(pendentes()).toBe(2)); expect(screen.queryByText(ERRO)).toBeNull(); expect(erros()).toBe(1);
});
it("leitura de horizonte VELHA que cai no mesmo tique da resposta perdida não é julgada: vale a releitura que o POST pediu", async () => {
 await abrir(); const g60 = segurar(), post = segurar(); let seguraGet = false;
 sem = (path, req) => {
  if (path === PREVISAO && seguraGet) { seguraGet = false; const velha = previsao(60); return g60.promessa.then(() => resposta(200, velha)); } // dado de ANTES do POST
  if (path === POST) return post.promessa.then(() => { marcas[corpo(req).chave] = "confirmada"; throw new TypeError("Network request failed"); }); // o servidor gravou e a conexão caiu
  return undefined;
 };
 seguraGet = true; await apertar("60 dias"); await act(async () => { fireEvent.press(botao("Sim, é receita fixa: Salário Acme")); await drenar(); });
 await act(async () => { g60.soltar(); post.soltar(); await drenar(); await drenar(); await drenar(); });
 await waitFor(() => expect(pendentes()).toBe(1)); expect(screen.queryByText(ERRO)).toBeNull(); expect(erros()).toBe(0); anunciou("Salário Acme: confirmada.");
 expect(todosAtivos().every((b) => !b.props.accessibilityState.disabled)).toBe(true);
});
it("só respondidas: caixa com 'Já respondidas', sem pergunta e sem Sim/Não", async () => {
 marcas = { "extrato#alu": "confirmada", "extrato#tio": "negada" }; await abrir();
 expect(pendentes()).toBe(0); expect(screen.getByText("Já respondidas (2)")).toBeTruthy(); expect(screen.queryByRole("button", { name: /^Sim|^Não/ })).toBeNull();
});
it("previsão negada (plano sem forecast): sem caixa e sem erro da caixa", async () => {
 sem = (path) => path === PREVISAO ? Promise.resolve(resposta(403, { detail: { error: "pro_required", feature: "forecast" } })) : undefined;
 renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getAllByText("Este recurso não está disponível no seu acesso atual.").length).toBeGreaterThan(0)); await act(drenar);
 expect(screen.queryByText("Receitas do extrato")).toBeNull(); expect(screen.queryByText(ERRO)).toBeNull();
});
it("perfil sem o card Saldo previsto no layout (Economizar) ainda mostra a caixa, uma vez só, no topo", async () => {
 perfil = "economizar"; await abrir();
 expect(screen.queryByText("Saldo previsto")).toBeNull(); expect(screen.getAllByText("Receitas do extrato")).toHaveLength(1); expect(botao("Sim, é receita fixa: Salário Acme")).toBeTruthy();
 await apertar("Sim, é receita fixa: Salário Acme"); expect(marcas["extrato#sal"]).toBe("confirmada"); expect(pendentes()).toBe(1);
});
it("com o Saldo previsto no layout a caixa também é uma só (não repete na folha nem no card)", async () => { await abrir(); expect(screen.getAllByText("Receitas do extrato")).toHaveLength(1); });
it("Ocultar valores esconde o valor da caixa", async () => {
 await abrir(); const valor = () => screen.queryAllByLabelText(/5000 reais/); const antes = valor().length; expect(antes).toBeGreaterThan(0);
 await apertar("Ocultar valores"); expect(valor()).toHaveLength(0);
});
it.each([[1, 2], [2, undefined]])("nome longo com fontScale %s: título com numberOfLines %s (sem limite ampliado) e o rótulo do botão leva o nome inteiro", async (fontScale, linhas) => {
 const nome = "Pagadora ".repeat(25).trim(); BASE[0]!.nome = nome; const janela = Dimensions.get("window");
 try {
  Dimensions.set({ window: { ...janela, fontScale } }); await abrir();
  expect(screen.getByText(nome).props.numberOfLines).toBe(linhas); expect(botao(`Sim, é receita fixa: ${nome}`)).toBeTruthy();
 } finally { BASE[0]!.nome = "Salário Acme"; Dimensions.set({ window: janela }); }
});
