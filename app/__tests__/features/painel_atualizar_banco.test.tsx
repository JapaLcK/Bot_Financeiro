import * as Haptics from "expo-haptics";
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { AccessibilityInfo } from "react-native";
import { __resetAtualizarBanco } from "@/features/painel/usarAtualizarBanco";
import { __resetAvisoBanco } from "@/features/painel/avisoBanco";
import { guardarCredenciais } from "@/storage/secure";
import { chamadas, fetchFalso, prepararCaso, resposta, rotear, segurar, type Rota } from "./auth_apoio";
import { trocarParaBela } from "./conversa_apoio";
import fixture from "./painel.fixture.json";
import { appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";

// O "Atualizar agora" do aviso de banco, pela rota: o servidor falso guarda o snapshot de conexões e o que o POST /refresh devolve.
// A máquina pura (cadência, teto, cancelamento) tem o relógio falso em `painel_atualizar_banco_maquina.test.ts`; aqui a conversa inteira.
jest.setTimeout(30_000); // `renderRouter` monta a árvore inteira: no CI o 1º teste paga o carregamento a frio e passou dos 5 s (igual a painel_receitas)
const AINDA = "O banco está demorando. Seus dados aparecem aqui quando terminar.", PARCIAL_TEXTO = "Erro em Nubank.";
const BANCO = ["banco_desatualizado"], FALHA = "Não conseguimos atualizar agora. Tente de novo.", SOZINHO = "O banco está atualizando. Seus dados aparecem aqui sozinhos quando terminar.";
const VELHO = "2026-10-10T12:00:00Z", NOVO = "2026-10-10T13:00:00Z";
type Srv = { estado: string; detalhe: string | null; sync: string; atualizado: boolean; limite: number; snapshot: Rota | null; post: Rota; leituras: number[]; t0: number };
let srv: Srv, extra: Record<string, Rota>;
const con = (uid = 1) => ({ id: 1, status: "ACTIVE", status_reason: null, last_sync_at: srv.sync, reconnected_at: null, provider_item_id: "it_1", institution_name: "Nubank", ui: { state: srv.estado, label: srv.estado, detail: srv.detalhe }, uid });
const item = (state: string, still = false, detail: string | null = null) => ({ item_id: "it_1", institution: "Nubank", state, label: state, detail, still_updating: still });
const postOk = (items = [item("updated")]): Rota => () => { srv.atualizado = true; return resposta(200, { ok: true, sync: { ok: true, items }, connections: [con()] }); };
/** Responde depois de `ms` de relógio falso; o abort do cliente (prazo, puxar para atualizar) rejeita como o fetch real. */
const lento = (ms: number, fn: Rota): Rota => (o) => new Promise((ok, no) => {
  const sinal = o.signal as AbortSignal; const t = setTimeout(() => ok(fn(o)), ms);
  sinal.addEventListener("abort", () => { clearTimeout(t); no(Object.assign(new Error("Aborted"), { name: "AbortError" })); });
});
const dado = (corpo: unknown): Rota => () => resposta(200, corpo);
function servidor() {
  const c = fixture["/api/app/contas"], pat = fixture["/api/app/patrimonio"];
  const velho = (o: Rota, comMotivo: unknown): Rota => (r) => srv.atualizado ? o(r) : dado(comMotivo)(r);
  extra["/api/app/contas"] = velho(dado(c), { ...c, motivos: BANCO, contas: [{ ...c.contas[0], instituicao: "Nu Pagamentos S.A.", conexao: "partial", motivos: BANCO }, c.contas[1]] });
  extra["/api/app/patrimonio"] = velho(dado(pat), { ...pat, motivos: BANCO });
  extra["/api/app/resumo-do-mes"] = velho(dado(fixture["/api/app/resumo-do-mes"]), { ...fixture["/api/app/resumo-do-mes"], motivos: BANCO });
  extra["/api/app/mes-detalhes"] = velho(dado(fixture["/api/app/mes-detalhes"]), { ...fixture["/api/app/mes-detalhes"], motivos: BANCO });
  rotear({
    "/open-finance/1": (o) => { srv.leituras.push(Date.now() - srv.t0); return srv.snapshot ? srv.snapshot(o) : resposta(200, { ok: true, connections: [con()] }); },
    "/open-finance/1/limite": () => resposta(200, { ok: true, of_banks_max: srv.limite, em_uso: 1, pode_adicionar: true, code: null, message: null }),
    "/open-finance/1/refresh": (o) => srv.post(o), ...extra,
  });
}
beforeEach(async () => {
  __resetAvisoBanco(); __resetAtualizarBanco(); prepararCaso(); desligarTrava(); jest.mocked(Haptics.notificationAsync).mockClear(); jest.mocked(AccessibilityInfo.announceForAccessibility).mockClear();
  srv = { estado: "partial", detalhe: null, sync: VELHO, atualizado: false, limite: 3, snapshot: null, post: postOk(), leituras: [], t0: 0 }; extra = {};
  await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
});
afterEach(() => jest.restoreAllMocks());
const abrir = async (url = "/resumo", aguardar = "Disponível agora") => { servidor(); renderRouter("./app", { initialUrl: url }); await waitFor(() => expect(screen.getByText(aguardar)).toBeTruthy()); await act(drenar); await act(drenar); };
const botao = (nome: string) => screen.getByRole("button", { name: nome });
const tem = (nome: string) => screen.queryByRole("button", { name: nome }) !== null;
const apertar = async (nome: string) => { await act(async () => { fireEvent.press(botao(nome)); await drenar(); }); };
// Em passos de 1 s por padrão: um salto único faria o relógio pular o prazo inteiro antes de a máquina armar a espera seguinte, e a cadência não seria medida.
const avancar = async (ms: number, passo = 1_000) => { for (let r = ms; r > 0; r -= passo) await act(async () => { jest.advanceTimersByTime(Math.min(passo, r)); for (let k = 0; k < 4; k++) await drenar(); }); };
const aviso = () => screen.queryByText("Dados do banco desatualizados");
const posts = () => fetchFalso.mock.calls.filter(([u]) => String(u).endsWith("/open-finance/1/refresh"));
const haptico = (tipo: string) => jest.mocked(Haptics.notificationAsync).mock.calls.filter(([t]) => t === tipo).length;
const AVISO_E_BOTOES = ["Atualizar agora", "Atualizando…", "Ver bancos conectados", "Reconectar Nubank"];
const botoes = () => AVISO_E_BOTOES.filter(tem);

it("(a) partial: UM POST com Bearer e JSON, o botão vira 'Atualizando…' desativado, e o aviso some sozinho quando o servidor para de mandar o motivo", async () => {
  const g = segurar(); srv.post = (o) => g.promessa.then(() => postOk()(o)); await abrir();
  expect(botoes()).toEqual(["Atualizar agora"]); expect(screen.getByText(/Os dados de Nu Pagamentos S.A. precisam ser atualizados/)).toBeTruthy();
  await apertar("Atualizar agora");
  expect(posts()).toHaveLength(1); const [, init] = posts()[0]!; expect(init.method).toBe("POST");
  expect(init.headers).toMatchObject({ Authorization: "Bearer access-ana", "Content-Type": "application/json" });
  expect(botoes()).toEqual(["Atualizando…"]); expect(botao("Atualizando…").props.accessibilityState.disabled).toBe(true);
  expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith("Atualizando os dados do banco");
  expect(aviso()).toBeTruthy(); // nada de otimismo: o aviso só sai com a releitura
  await act(async () => { g.soltar(); await drenar(); await drenar(); await drenar(); });
  await waitFor(() => expect(aviso()).toBeNull()); // SEM puxar para atualizar
  expect(posts()).toHaveLength(1); expect(haptico("success")).toBe(1);
});
it("(a) dois toques rápidos e a troca de aba no meio fazem UM POST só (estado de módulo: a outra aba também mostra 'Atualizando…')", async () => {
  const g = segurar(); srv.post = (o) => g.promessa.then(() => postOk()(o)); await abrir();
  await act(async () => { fireEvent.press(botao("Atualizar agora")); fireEvent.press(botao("Atualizar agora")); await drenar(); });
  await act(async () => { router.navigate("/gastos"); await drenar(); }); await waitFor(() => expect(screen.getByText("Para onde vai")).toBeTruthy()); await act(drenar);
  expect(botoes()).toEqual(["Atualizando…"]); await act(async () => { fireEvent.press(botao("Atualizando…")); await drenar(); });
  expect(posts()).toHaveLength(1);
  await act(async () => { g.soltar(); await drenar(); await drenar(); await drenar(); }); await waitFor(() => expect(aviso()).toBeNull()); expect(posts()).toHaveLength(1);
});
it("(a) o servidor demora 20 s (mais que os 15 s padrão): o POST espera e o aviso some; com o prazo curto ele seria cortado e viraria 'resultado'", async () => {
  srv.post = lento(20_000, postOk()); await abrir(); await apertar("Atualizar agora");
  await avancar(16_000, 16_000); expect(botoes()).toEqual(["Atualizando…"]); expect(aviso()).toBeTruthy(); // passou dos 15 s e segue esperando
  await avancar(4_000, 4_000); await waitFor(() => expect(aviso()).toBeNull()); expect(haptico("success")).toBe(1); expect(posts()).toHaveLength(1);
});
it("(a) still_updating: o aviso passa ao texto 'atualizando' (sem botão), lê em 5, 15, 35 e 75 s, e some quando o last_sync_at muda", async () => {
  srv.post = () => { srv.t0 = Date.now(); return postOk([item("updated", true)])({} as RequestInit); };
  srv.snapshot = () => { const n = srv.leituras.length; if (n >= 5) { srv.sync = NOVO; srv.estado = "updated"; srv.atualizado = true; } return resposta(200, { ok: true, connections: [con()] }); };
  await abrir(); const antes = srv.leituras.length; expect(antes).toBe(1); // a leitura que classificou o botão
  srv.atualizado = false; await apertar("Atualizar agora"); await act(async () => { await drenar(); });
  await waitFor(() => expect(screen.getByText(SOZINHO)).toBeTruthy()); expect(botoes()).toEqual([]);
  for (const ms of [5_000, 10_000, 20_000]) { await avancar(ms); expect(aviso()).toBeTruthy(); }
  expect(srv.leituras.slice(antes)).toEqual([5_000, 15_000, 35_000]);
  await avancar(40_000); await waitFor(() => expect(aviso()).toBeNull());
  expect(srv.leituras.slice(antes, antes + 4)).toEqual([5_000, 15_000, 35_000, 75_000]); expect(posts()).toHaveLength(1); expect(haptico("success")).toBe(1);
  const fim = srv.leituras.length; expect(fim - antes).toBeLessThanOrEqual(5); // a 5ª, se houver, é a leitura de classificação que a recarga (versão nova) faz com o corpo ainda montado
  await avancar(300_000, 60_000); expect(srv.leituras).toHaveLength(fim); // acabou: ninguém lê de novo
});
it("(a) teto de 5 min: para de ler (última leitura em 300 s), o texto 'demorando' fica com 'Ver bancos conectados' e o botão de atualizar não volta", async () => {
  srv.post = () => { srv.estado = "updating"; return resposta(200, { ok: true, sync: { ok: true, items: [item("updated", true)] }, connections: [con()] }); };
  await abrir(); await apertar("Atualizar agora"); await act(drenar); await waitFor(() => expect(screen.getByText(SOZINHO)).toBeTruthy());
  await avancar(300_000, 5_000);
  await waitFor(() => expect(screen.getByText(AINDA)).toBeTruthy()); expect(botoes()).toEqual(["Ver bancos conectados"]);
  const lidas = srv.leituras.length; expect(srv.leituras.at(-1)! - srv.leituras.at(-2)!).toBeLessThanOrEqual(60_000); await avancar(600_000, 60_000); expect(srv.leituras).toHaveLength(lidas); expect(posts()).toHaveLength(1);
});
it("(a) POST 500: a mensagem de falha, haptic de aviso e o botão volta; a segunda tentativa funciona", async () => {
  srv.post = () => resposta(500, {}); await abrir(); await apertar("Atualizar agora"); await act(drenar);
  expect(screen.getByText(FALHA)).toBeTruthy(); expect(botoes()).toEqual(["Atualizar agora"]); expect(haptico("warning")).toBe(1); expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith(FALHA);
  srv.post = postOk(); await apertar("Atualizar agora"); await act(drenar); await waitFor(() => expect(aviso()).toBeNull()); expect(posts()).toHaveLength(2);
});
it("(a) POST 402: cai no portão, sem a mensagem de falha", async () => {
  srv.post = () => resposta(402, { detail: "sem plano" }); await abrir(); await apertar("Atualizar agora"); await act(drenar); await act(drenar);
  expect(screen.queryByText(FALHA)).toBeNull(); expect(aviso()).toBeNull(); expect(haptico("warning")).toBe(0);
});
it("(a) puxar para atualizar com o POST em voo: o POST é abortado, sem mensagem, e o botão volta depois da recarga (uma recarga só)", async () => {
  let abortado = false; srv.post = (o) => new Promise((_, no) => { (o.signal as AbortSignal).addEventListener("abort", () => { abortado = true; no(Object.assign(new Error("Aborted"), { name: "AbortError" })); }); });
  await abrir(); await apertar("Atualizar agora"); const contas = () => chamadas().filter((c) => c.caminho === "/api/app/contas").length; const antes = contas();
  await act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); for (let k = 0; k < 3; k++) await drenar(); });
  expect(abortado).toBe(true); await waitFor(() => expect(botoes()).toEqual(["Atualizar agora"]));
  expect(screen.queryByText(FALHA)).toBeNull(); expect(haptico("warning") + haptico("success")).toBe(0); expect(contas() - antes).toBe(1); expect(posts()).toHaveLength(1);
});
it("trocar para a Bela com o POST da Ana em voo: nada da Ana aparece para a Bela, e ela vê 'Atualizar agora' ativo", async () => {
  const g = segurar(); srv.post = (o) => g.promessa.then(() => postOk()(o));
  servidor(); const base = (fetchFalso.getMockImplementation() as (u: string, o: RequestInit) => Promise<Response>);
  const doUid2 = (u: string) => String(u).replace(/\/(open-finance|cards|installments|analytics|insights|goals)\/2\b/, "/$1/1");
  fetchFalso.mockImplementation(async (u: string, o: RequestInit) => {
    const auth = (o.headers as Record<string, string>).Authorization;
    if (String(u).endsWith("/auth/me") && auth === "Bearer access-bela") return resposta(200, { user_id: 2, email: "bela@x.com", display_name: "Bela", app_access: true, of_banks_max: 2, cobranca_em_atraso: false });
    return base(doUid2(u), o);
  });
  renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("Disponível agora")).toBeTruthy()); await act(drenar); await act(drenar);
  await apertar("Atualizar agora"); expect(botoes()).toEqual(["Atualizando…"]);
  await trocarParaBela(); await act(drenar); await act(drenar);
  await waitFor(() => expect(botoes()).toEqual(["Atualizar agora"])); expect(botao("Atualizar agora").props.accessibilityState.disabled).toBeFalsy();
  const antes = haptico("success"); await act(async () => { g.soltar(); await drenar(); await drenar(); await drenar(); });
  expect(botoes()).toEqual(["Atualizar agora"]); expect(haptico("success")).toBe(antes); expect(screen.queryByText(FALHA)).toBeNull();
});
it("(b) needs_user_action 'Reautorize o banco': 'Reconectar Nubank' abre a autorização do item, SEM POST /refresh", async () => {
  srv.estado = "needs_user_action"; srv.detalhe = "Reautorize o banco"; await abrir(); expect(botoes()).toEqual(["Reconectar Nubank"]);
  const push = jest.spyOn(router, "push").mockImplementation(() => undefined);
  await apertar("Reconectar Nubank"); expect(push).toHaveBeenCalledWith({ pathname: "/autorizando", params: { itemId: "it_1" } }); expect(posts()).toHaveLength(0);
});
it("(c) needs_user_action de DISPOSITIVO: 'Ver bancos conectados' com o nome do banco, nada de 'Reconectar'", async () => {
  srv.estado = "needs_user_action"; srv.detalhe = "Autorize o acesso no app do banco"; await abrir();
  expect(botoes()).toEqual(["Ver bancos conectados"]); expect(screen.getByText("Erro em Nubank.")).toBeTruthy();
  const push = jest.spyOn(router, "push").mockImplementation(() => undefined); await apertar("Ver bancos conectados"); expect(push).toHaveBeenCalledWith("/conexoes");
});
it.each([
  ["item_missing", () => { srv.estado = "item_missing"; srv.detalhe = "Esse banco não existe mais"; }, "Erro em Nubank."],
  ["paused", () => { srv.estado = "paused"; srv.detalhe = "Plano pausado"; }, "Erro em Nubank."],
  ["(b) com of_banks_max 0", () => { srv.estado = "needs_user_action"; srv.detalhe = "Reautorize o banco"; srv.limite = 0; }, "Erro em Nubank."],
  ["snapshot 500", () => { srv.snapshot = () => resposta(500, {}); }, null],
])("(c) %s: só 'Ver bancos conectados' (nunca bloqueia o aviso)", async (_, montar, frase) => {
  montar(); await abrir(); expect(botoes()).toEqual(["Ver bancos conectados"]); expect(aviso()).toBeTruthy();
  if (frase) expect(screen.getByText(frase)).toBeTruthy(); else expect(screen.getByText(/precisam ser atualizados/)).toBeTruthy();
});
it("sem laço: a rodada que termina partial deixa o aviso com o nome do banco e SEM 'Atualizar agora'", async () => {
  srv.post = () => resposta(200, { ok: true, sync: { ok: false, items: [item("partial", false, "Parte dos dados ainda não veio")] }, connections: [con()] });
  await abrir(); await apertar("Atualizar agora"); await act(drenar); await act(drenar);
  await waitFor(() => expect(screen.getByText("Erro em Nubank.")).toBeTruthy());
  expect(botoes()).toEqual(["Ver bancos conectados"]); expect(aviso()).toBeTruthy(); expect(haptico("warning")).toBe(1); expect(haptico("success")).toBe(0);
  expect(posts()).toHaveLength(1);
});
it("(a0) updating sem detalhe: sem botão, sem POST; lê na cadência e some quando o last_sync_at muda", async () => {
  srv.estado = "updating"; srv.snapshot = () => { if (srv.leituras.length >= 2) { srv.estado = "updated"; srv.sync = NOVO; srv.atualizado = true; } return resposta(200, { ok: true, connections: [con()] }); };
  await abrir(); srv.t0 = Date.now(); srv.leituras = []; expect(screen.getByText(SOZINHO)).toBeTruthy(); expect(botoes()).toEqual([]);
  await avancar(5_000); expect(aviso()).toBeTruthy(); await avancar(10_000); await waitFor(() => expect(aviso()).toBeNull()); expect(srv.leituras.slice(0, 2)).toEqual([5_000, 15_000]); expect(posts()).toHaveLength(0); expect(haptico("success")).toBe(0); expect(haptico("warning")).toBe(0); expect(anuncios()).toEqual([]); // a rodada que nasce SOZINHA não faz barulho
});
it("controle positivo: sem aviso de banco nada é lido nem oferecido", async () => {
  srv.atualizado = true; extra = {}; await abrir(); expect(aviso()).toBeNull(); expect(botoes()).toEqual([]); expect(chamadas().filter((c) => c.caminho === "/open-finance/1" || c.caminho.endsWith("/refresh"))).toHaveLength(0);
});

// ---- A regra do "resultado" (ver usarAtualizarBanco.ts): pertence à rodada e à vista; recarga ou X o apagam ----
const PARCIAL = () => resposta(200, { ok: true, sync: { ok: false, items: [item("partial", false, "Parte dos dados ainda não veio")] }, connections: [con()] });
const refrescar = async () => { await act(async () => { screen.getByTestId("tela").props.refreshControl.props.onRefresh(); for (let k = 0; k < 4; k++) await drenar(); }); await act(drenar); await act(drenar); };
const ate = async (texto: string) => { await act(drenar); await act(drenar); await waitFor(() => expect(screen.getByText(texto)).toBeTruthy()); };
const anuncios = () => jest.mocked(AccessibilityInfo.announceForAccessibility).mock.calls.map((c) => c[0]);
it("resultado grudado (B1): depois de um 'partial' o banco passa a pedir reautorização e, ao recarregar, aparece 'Reconectar Nubank' (e não o resultado velho)", async () => {
  srv.post = PARCIAL; await abrir(); await apertar("Atualizar agora"); await ate(PARCIAL_TEXTO); expect(botoes()).toEqual(["Ver bancos conectados"]);
  srv.estado = "needs_user_action"; srv.detalhe = "Reautorize o banco"; await refrescar();
  await waitFor(() => expect(botoes()).toEqual(["Reconectar Nubank"])); expect(screen.queryByText(PARCIAL_TEXTO)).toBeNull();
});
it("depois de uma recarga o 'Atualizar agora' volta (o 'sem laço' vale só dentro da mesma vista) e o toque seguinte faz outro POST", async () => {
  srv.post = PARCIAL; await abrir(); await apertar("Atualizar agora"); await ate(PARCIAL_TEXTO); expect(botoes()).toEqual(["Ver bancos conectados"]);
  await act(async () => { router.navigate("/gastos"); await drenar(); }); await waitFor(() => expect(screen.getByText("Para onde vai")).toBeTruthy()); await act(drenar);
  expect(screen.getByText(PARCIAL_TEXTO)).toBeTruthy(); expect(botoes()).toEqual(["Ver bancos conectados"]); // outra aba, MESMA vista: o resultado segue
  await refrescar(); await waitFor(() => expect(botoes()).toEqual(["Atualizar agora"])); expect(screen.queryByText(PARCIAL_TEXTO)).toBeNull();
  srv.post = postOk(); await apertar("Atualizar agora"); await act(drenar); await waitFor(() => expect(aviso()).toBeNull()); expect(posts()).toHaveLength(2);
});
it("a falha do POST também pertence à rodada: a recarga a apaga", async () => {
  srv.post = () => resposta(500, {}); await abrir(); await apertar("Atualizar agora"); await act(drenar); expect(screen.getByText(FALHA)).toBeTruthy();
  await refrescar(); await waitFor(() => expect(botoes()).toEqual(["Atualizar agora"])); expect(screen.queryByText(FALHA)).toBeNull();
});
it("troca de conta (B2): o resultado da Ana não fica nem para a Bela nem quando a Ana volta (o botão volta)", async () => {
  srv.post = PARCIAL; servidor(); await bela(); renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("Disponível agora")).toBeTruthy()); await act(drenar); await act(drenar);
  await apertar("Atualizar agora"); await ate(PARCIAL_TEXTO);
  await trocarParaBela(); await act(drenar); await act(drenar); await waitFor(() => expect(botoes()).toEqual(["Atualizar agora"])); expect(screen.queryByText(PARCIAL_TEXTO)).toBeNull();
  await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); await appVai("background"); await appVai("active"); await act(drenar); await act(drenar);
  await waitFor(() => expect(botoes()).toEqual(["Atualizar agora"])); expect(screen.queryByText(PARCIAL_TEXTO)).toBeNull();
});
it("X do aviso no meio do POST: some tudo e, quando o servidor responde, nada de aviso(), sucesso(), anúncio nem recarga", async () => {
  const g = segurar(); srv.post = () => g.promessa.then(() => PARCIAL()); await abrir(); await apertar("Atualizar agora");
  const contas = () => chamadas().filter((c) => c.caminho === "/api/app/contas").length, antes = contas(); const falas = anuncios().length;
  await apertar("Fechar aviso"); expect(aviso()).toBeNull();
  await act(async () => { g.soltar(); await drenar(); await drenar(); await drenar(); });
  expect(haptico("warning") + haptico("success")).toBe(0); expect(anuncios().length).toBe(falas); expect(contas()).toBe(antes); expect(posts()).toHaveLength(1);
});
it("X do aviso no meio do acompanhamento: para de ler e o 'sucesso' não vem depois", async () => {
  srv.post = () => { srv.t0 = Date.now(); return postOk([item("updated", true)])({} as RequestInit); }; await abrir(); await apertar("Atualizar agora"); await ate(SOZINHO);
  await avancar(5_000); const lidas = srv.leituras.length; await apertar("Fechar aviso");
  srv.sync = NOVO; await avancar(120_000, 5_000); expect(srv.leituras).toHaveLength(lidas); expect(haptico("success") + haptico("warning")).toBe(0);
});
const bela = async () => {
  const base = (fetchFalso.getMockImplementation() as (u: string, o: RequestInit) => Promise<Response>);
  const doUid2 = (u: string) => String(u).replace(/\/(open-finance|cards|installments|analytics|insights|goals)\/2\b/, "/$1/1");
  fetchFalso.mockImplementation(async (u: string, o: RequestInit) => {
    const auth = (o.headers as Record<string, string>).Authorization;
    if (String(u).endsWith("/auth/me") && auth === "Bearer access-bela") return resposta(200, { user_id: 2, email: "bela@x.com", display_name: "Bela", app_access: true, of_banks_max: 2, cobranca_em_atraso: false });
    return base(doUid2(u), o);
  });
};
const doUid = (n: 1 | 2) => chamadas().filter((c) => c.caminho.startsWith("/open-finance") && c.caminho.split("/")[2] === String(n));
it("a Bela (uid 2) aperta 'Atualizar agora': a leitura do botão e o POST vão para o uid DELA, com o token DELA", async () => {
  srv.post = postOk(); servidor(); await bela(); await guardarCredenciais({ access: "access-bela", refresh: "rt_bela" });
  renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("Disponível agora")).toBeTruthy()); await act(drenar); await act(drenar);
  expect(doUid(2).some((c) => c.caminho === "/open-finance/2")).toBe(true); // a leitura que classifica o botão
  await apertar("Atualizar agora"); await act(drenar); await act(drenar);
  expect(doUid(1)).toEqual([]); expect(doUid(2).filter((c) => c.caminho === "/open-finance/2/refresh")).toEqual([expect.objectContaining({ auth: "Bearer access-bela" })]);
});
it("a Bela acompanha: os GETs do acompanhamento vão para o uid DELA", async () => {
  srv.estado = "updated"; srv.post = () => resposta(200, { ok: true, sync: { ok: true, items: [item("updated", true)] }, connections: [con()] });
  servidor(); await bela(); await guardarCredenciais({ access: "access-bela", refresh: "rt_bela" });
  renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("Disponível agora")).toBeTruthy()); await act(drenar); await act(drenar);
  await apertar("Atualizar agora"); await ate(SOZINHO); const antes = doUid(2).length; await avancar(16_000);
  expect(doUid(1)).toEqual([]); expect(doUid(2).length - antes).toBe(2); expect(doUid(2).slice(antes).every((c) => c.caminho === "/open-finance/2" && c.auth === "Bearer access-bela")).toBe(true);
});
it("puxar para atualizar no meio do acompanhamento: o 'atualizando' some e o botão volta NA HORA e funciona (o abort acorda a espera)", async () => {
  srv.estado = "updated"; srv.post = () => resposta(200, { ok: true, sync: { ok: true, items: [item("updated", true)] }, connections: [con()] });
  await abrir(); await apertar("Atualizar agora"); await ate(SOZINHO);
  await refrescar(); await waitFor(() => expect(botoes()).toEqual(["Atualizar agora"])); expect(screen.queryByText(SOZINHO)).toBeNull();
  await apertar("Atualizar agora"); await act(drenar); expect(posts()).toHaveLength(2); // sem o abort acordando a espera, a rodada velha ainda estaria "ocupada" e o toque seria ignorado
});

// ---- "versão local × versão do provider": a `versao` do provider sobe também no `finally` do `mudarPerfil` (PUT estourado) SEM cancelar a rodada ----
const gets = () => chamadas().filter((c) => c.caminho === "/open-finance/1").length;
const perfilQueEstoura = () => { extra["/api/app/perfil"] = (o) => (o.method === "PUT" ? new Promise((_, no) => (o.signal as AbortSignal).addEventListener("abort", () => no(Object.assign(new Error("Aborted"), { name: "AbortError" })))) : resposta(200, { perfil: "padrao" })); };
const fimComProblema = async (comBump: boolean) => {
  if (comBump) perfilQueEstoura();
  srv.estado = "updated"; let fim = false; srv.post = () => resposta(200, { ok: true, sync: { ok: true, items: [item("updated", true)] }, connections: [con()] });
  srv.snapshot = () => { if (fim) { srv.estado = "partial"; srv.detalhe = "Parte dos dados ainda não veio"; srv.sync = NOVO; } return resposta(200, { ok: true, connections: [con()] }); };
  await abrir(); await apertar("Atualizar agora"); await ate(SOZINHO);
  if (comBump) { await apertar("Padrão"); await apertar("Economizar"); await avancar(16_000, 16_000); } // o PUT estoura os 15 s: o provider sobe a `versao` sem cancelar
  const meio = { botoes: botoes(), sozinho: !!screen.queryByText(SOZINHO) }; const p0 = posts().length;
  fim = true; await avancar(120_000, 5_000); await act(drenar); await act(drenar);
  return { meio, postsNovos: posts().length - p0 };
};
it("o provider sobe a versão SEM cancelar a rodada (PUT de perfil estourado): a rodada segue visível, sem botão morto, e o problema do fim aparece (controle: sem o bump)", async () => {
  const controle = await fimComProblema(false); expect(controle.meio).toEqual({ botoes: [], sozinho: true }); expect(screen.getByText(PARCIAL_TEXTO)).toBeTruthy();
});
it("...e COM o bump: o mesmo (a visibilidade e o toque usam a mesma regra, a operação do provider)", async () => {
  const r = await fimComProblema(true);
  expect(r.meio).toEqual({ botoes: [], sozinho: true }); expect(r.postsNovos).toBe(0);
  expect(screen.getByText(PARCIAL_TEXTO)).toBeTruthy(); expect(botoes()).toEqual(["Ver bancos conectados"]); expect(haptico("warning")).toBe(1); expect(anuncios().filter((t) => t === PARCIAL_TEXTO)).toHaveLength(1);
});
it("a `versao` sobe SEM cancelar (PUT de perfil estourado) com o snapshot velho em voo: a resposta velha, que chega depois da nova, não toma o lugar dela", async () => {
  perfilQueEstoura(); const g = segurar(); let primeira = true;
  srv.snapshot = async () => { if (!primeira) return resposta(200, { ok: true, connections: [con()] }); primeira = false; const velha = resposta(200, { ok: true, connections: [con()] }); await g.promessa; return velha; };
  await abrir(); srv.estado = "needs_user_action"; srv.detalhe = "Reautorize o banco";
  await apertar("Padrão"); await apertar("Economizar"); await avancar(16_000, 16_000);
  await waitFor(() => expect(botoes()).toEqual(["Reconectar Nubank"]));
  await act(async () => { g.soltar(); for (let k = 0; k < 6; k++) await drenar(); });
  expect(botoes()).toEqual(["Reconectar Nubank"]); expect(gets()).toBe(2);
});
it("provider remontado para a MESMA conta (sair e entrar): a `versao` recomeça e repete a chave, mas a leitura velha não vale e o botão segue o snapshot novo", async () => {
  await abrir(); expect(botoes()).toEqual(["Atualizar agora"]); const g0 = gets();
  screen.unmount(); srv.estado = "needs_user_action"; srv.detalhe = "Reautorize o banco";
  await abrir(); await waitFor(() => expect(botoes()).toEqual(["Reconectar Nubank"])); expect(gets() - g0).toBe(1);
});
it("quatro abas montadas: UM GET do snapshot por recarga (dedupe por uid:versao)", async () => {
  await abrir();
  for (const r of ["/gastos", "/metas", "/extrato", "/resumo"] as const) { await act(async () => { router.navigate(r); await drenar(); }); await act(drenar); await act(drenar); }
  const g0 = gets(); await refrescar(); expect(gets() - g0).toBe(1);
});
it("X enquanto o snapshot (a0) ainda é lido: nenhuma rodada nasce depois do X", async () => {
  srv.estado = "updating"; const g = segurar(); let primeira = true;
  srv.snapshot = async () => { if (primeira) { primeira = false; await g.promessa; } return resposta(200, { ok: true, connections: [con()] }); };
  servidor(); renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("Disponível agora")).toBeTruthy()); await act(drenar); await act(drenar);
  await apertar("Fechar aviso"); const g0 = gets();
  await act(async () => { g.soltar(); for (let k = 0; k < 6; k++) await drenar(); }); await avancar(400_000, 20_000);
  expect(gets() - g0).toBe(0); expect(haptico("warning") + haptico("success")).toBe(0);
});
it("X enquanto o limite do plano é lido (candidato (b) + outro banco coletando): nenhuma rodada nasce depois do X", async () => {
  srv.snapshot = () => resposta(200, { ok: true, connections: [{ ...con(), ui: { state: "needs_user_action", label: "x", detail: "Reautorize o banco" } }, { ...con(), id: 2, provider_item_id: "it_2", institution_name: "Itaú", ui: { state: "updating", label: "u", detail: null } }] });
  const g = segurar(); servidor(); const base = fetchFalso.getMockImplementation() as (u: string, o: RequestInit) => Promise<Response>;
  fetchFalso.mockImplementation(async (u: string, o: RequestInit) => { if (String(u).endsWith("/open-finance/1/limite")) { await g.promessa; return resposta(200, { ok: true, of_banks_max: 0, em_uso: 1, pode_adicionar: true, code: null, message: null }); } return base(u, o); });
  renderRouter("./app", { initialUrl: "/resumo" }); await waitFor(() => expect(screen.getByText("Disponível agora")).toBeTruthy()); await act(drenar); await act(drenar);
  await apertar("Fechar aviso"); const g0 = gets();
  await act(async () => { g.soltar(); for (let k = 0; k < 6; k++) await drenar(); }); await avancar(400_000, 20_000);
  expect(gets() - g0).toBe(0); expect(haptico("warning") + haptico("success")).toBe(0);
});

it("(a0) que termina com problema também é calada: o aviso curto do banco aparece, sem aviso() nem anúncio", async () => {
  srv.estado = "updating"; srv.snapshot = () => { if (srv.leituras.length >= 2) { srv.estado = "partial"; srv.detalhe = "Parte dos dados ainda não veio"; srv.sync = NOVO; } return resposta(200, { ok: true, connections: [con()] }); };
  await abrir(); expect(screen.getByText(SOZINHO)).toBeTruthy(); await avancar(10_000);
  await waitFor(() => expect(screen.getByText(PARCIAL_TEXTO)).toBeTruthy()); expect(haptico("warning") + haptico("success")).toBe(0); expect(anuncios()).toEqual([]);
});
it("só a rodada do TOQUE faz barulho: a anunciar e a vibrar (controle da (a0) calada)", async () => {
  srv.post = PARCIAL; await abrir(); await apertar("Atualizar agora"); await ate(PARCIAL_TEXTO); expect(haptico("warning")).toBe(1); expect(anuncios()).toContain(PARCIAL_TEXTO);
});

// ---- Manager M1: bump sem cancelar DURANTE o POST + o PATCH pôs o item em coleta + o POST falha ----
const cenarioM1 = async (comBump: boolean) => {
  if (comBump) perfilQueEstoura();
  const g = segurar(); srv.post = () => g.promessa.then(() => resposta(500, {}));
  await abrir(); await apertar("Atualizar agora"); expect(botoes()).toEqual(["Atualizando…"]);
  srv.estado = "updating"; // o PATCH do POST pôs o item em coleta: o snapshot agora diz "updating"
  if (comBump) { await apertar("Padrão"); await apertar("Economizar"); await avancar(16_000, 16_000); }
  await act(async () => { g.soltar(); for (let k = 0; k < 4; k++) await drenar(); }); await act(drenar); await act(drenar);
  const g0 = gets(); await avancar(60_000, 5_000);
  return { botoes: botoes(), sozinho: !!screen.queryByText(SOZINHO), falha: !!screen.queryByText(FALHA), getsDepois: gets() - g0 };
};
it("M1 controle: sem bump, o POST 500 devolve 'Atualizar agora' com a falha, e nada lê", async () => {
  const r = await cenarioM1(false); expect(r).toEqual({ botoes: ["Atualizar agora"], sozinho: false, falha: true, getsDepois: 0 });
});
it("M1 ataque: com o bump, o 'atualizando sozinho' só aparece porque há uma rodada lendo de fato (a leitura adiada parte quando o POST acaba)", async () => {
  const r = await cenarioM1(true);
  expect(r.sozinho).toBe(true); expect(r.getsDepois).toBeGreaterThan(0); expect(r.botoes).toEqual([]); expect(haptico("warning")).toBe(1); // o POST que falhou vibrou; o acompanhamento que nasceu sozinho é calado
  srv.estado = "partial"; srv.detalhe = "Parte dos dados ainda não veio"; srv.sync = NOVO; await avancar(70_000, 5_000); // o acompanhamento que nasceu sozinho termina com problema: o texto fica, sem barulho
  await waitFor(() => expect(screen.getByText(PARCIAL_TEXTO)).toBeTruthy()); expect(haptico("warning")).toBe(1); expect(anuncios()).not.toContain(PARCIAL_TEXTO);
});
it("M1 + X: fechar o aviso com a leitura adiada e o POST ainda no ar: quando o POST acaba, nenhuma rodada nasce (nem lê, nem vibra)", async () => {
  perfilQueEstoura(); const g = segurar(); srv.post = () => g.promessa.then(() => resposta(500, {}));
  await abrir(); await apertar("Atualizar agora"); srv.estado = "updating"; await apertar("Padrão"); await apertar("Economizar"); await avancar(16_000, 16_000);
  await apertar("Fechar aviso"); const g0 = gets(), w0 = haptico("warning");
  await act(async () => { g.soltar(); for (let k = 0; k < 4; k++) await drenar(); }); await avancar(60_000, 5_000);
  expect(gets() - g0).toBe(0); expect(haptico("warning")).toBe(w0);
});

// ---- MGR2: o `resultado` visível manda; a leitura (a0) que chega NA MESMA vista não parte por cima dele (nem fica pendente) ----
const semPartir = async () => {
  const g0 = gets(), w0 = haptico("warning"); await avancar(60_000, 5_000);
  expect(screen.getByText(PARCIAL_TEXTO)).toBeTruthy(); expect(screen.queryByText(SOZINHO)).toBeNull(); expect(botoes()).toEqual(["Ver bancos conectados"]);
  expect(gets() - g0).toBe(0); expect(haptico("warning")).toBe(w0);
};
it("MGR2a: o POST termina em resultado e a releitura da PRÓPRIA recarga diz 'updating': o resultado fica, nada lê por cima", async () => {
  srv.post = () => { srv.estado = "updating"; return PARCIAL(); };
  await abrir(); await apertar("Atualizar agora"); await ate(PARCIAL_TEXTO); for (let k = 0; k < 4; k++) await act(drenar);
  await semPartir();
});
it("MGR2b: resultado visível, bump sem cancelar (PATCH estourado), snapshot novo diz 'updating': o resultado fica, nada lê por cima", async () => {
  perfilQueEstoura(); srv.post = PARCIAL;
  await abrir(); await apertar("Atualizar agora"); await ate(PARCIAL_TEXTO);
  srv.estado = "updating"; await apertar("Padrão"); await apertar("Economizar"); await avancar(16_000, 16_000); for (let k = 0; k < 4; k++) await act(drenar);
  await semPartir();
});
it("MGR3: resultado visível, puxar para atualizar, o banco agora está em coleta: a leitura (a0) da vista NOVA parte (o bloqueio só vale na mesma vista)", async () => {
  srv.post = PARCIAL; await abrir(); await apertar("Atualizar agora"); await ate(PARCIAL_TEXTO);
  srv.estado = "updating"; await refrescar(); const g0 = gets(); await avancar(20_000, 5_000);
  expect(screen.getByText(SOZINHO)).toBeTruthy(); expect(screen.queryByText(PARCIAL_TEXTO)).toBeNull(); expect(gets() - g0).toBeGreaterThan(0);
});
