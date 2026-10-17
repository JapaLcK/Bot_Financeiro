/**
 * Apoio dos testes da aba Piggy (conversa): o servidor falso e os gestos de tela.
 * O chat é um STREAM: `chat(n)` devolve uma `respostaFluxo()` (eventos empurrados pelo teste) ou um `resposta(status, ...)` de antes do stream.
 * Não termina em `.test.ts`, então o Jest não o roda como suíte.
 */
import { router } from "expo-router";
import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import { fetchFalso, resposta, segurar } from "./auth_apoio";
import { appVai, drenar } from "./open_finance_volta_rota_apoio";
import { guardarCredenciais } from "@/storage/secure";
import fixture from "./painel.fixture.json";

export type Segurar = ReturnType<typeof segurar>;
export type Fluxo = ReturnType<typeof respostaFluxo>;
type Rota = (n: number) => Promise<Response | Fluxo> | Response | Fluxo;

/** Resposta de stream falsa: o teste empurra eventos (ou bytes crus) e fecha/quebra; `read()` rejeita com AbortError quando o sinal aborta, como o `FetchResponse` real (o "servidor" segue de pé). */
export function respostaFluxo() {
  const fila: (Uint8Array | { erro: unknown } | null)[] = []; let espera: (() => void) | null = null, sinal: AbortSignal | undefined;
  const acordar = () => { espera?.(); espera = null; };
  const enc = new TextEncoder();
  const f = {
    ok: true, status: 200, headers: { get: () => null }, cancelado: false,
    body: { getReader: () => ({
      read: async (): Promise<{ done: boolean; value?: Uint8Array }> => {
        for (;;) {
          if (sinal?.aborted) throw Object.assign(new Error("Aborted"), { name: "AbortError" });
          const x = fila.shift();
          if (x === undefined) { await new Promise<void>((r) => (espera = r)); continue; }
          if (x === null) return { done: true };
          if (!(x instanceof Uint8Array)) throw x.erro;
          return { done: false, value: x };
        }
      },
      cancel: async () => { f.cancelado = true; },
    }) },
    empurrarBruto: (t: string | Uint8Array) => { fila.push(typeof t === "string" ? enc.encode(t) : t); acordar(); },
    empurrar: (e: object) => f.empurrarBruto(`data: ${JSON.stringify(e)}\n\n`),
    fechar: () => { fila.push(null); acordar(); },
    quebrar: (erro: unknown) => { fila.push({ erro }); acordar(); },
    ligar: (s: AbortSignal) => { sinal = s; s.addEventListener("abort", acordar); },
  };
  return f;
}

export const srv = { perfilDoServidor: "padrao", uso: 2, limite: 100, historico: [] as unknown[], statusPendencia: 200, cancelada: null as string | null, segurarDelete: null as Segurar | null, soUma: false, armada: false, segurarGet: null as Segurar | null, statusGet: 200, chat: (() => undefined) as unknown as Rota, nChat: 0, feedback: { status: 200 } as { status: number; seguro?: Segurar } };
export const caminho = (u: unknown) => new URL(String(u)).pathname;
export const delecoes = () => fetchFalso.mock.calls.filter(([u, r]) => caminho(u) === "/ai/pending" && r.method === "DELETE");
export const posts = () => fetchFalso.mock.calls.filter(([u]) => caminho(u) === "/ai/chat/stream");
export const feedbacks = () => fetchFalso.mock.calls.filter(([u]) => /^\/ai\/messages\/\d+\/feedback$/.test(caminho(u)));
export const bela = (req: RequestInit) => (req.headers as Record<string, string>).Authorization === "Bearer access-bela";
/** Um `fim` completo no formato do servidor. */
export const fim = (reply: string, extra: object = {}) => ({ tipo: "fim", reply, usage: { used: srv.uso, limit: srv.limite }, message_id: srv.uso, chips: [], ...extra });
/** Stream já completo (um `fim` e fechado), para os testes que só precisam de uma resposta. */
export const fluxoFim = (reply: string, used: number, limit = 100) => { const f = respostaFluxo(); f.empurrar({ tipo: "fim", reply, usage: { used, limit }, message_id: used, chips: [] }); f.fechar(); return f; };
export const ok = (reply: string, extra: object = {}) => () => { srv.uso++; const f = respostaFluxo(); f.empurrar(fim(reply, extra)); f.fechar(); return f; };

export function servidor() {
  fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
    const p = caminho(url);
    if (p === "/auth/me") return resposta(200, bela(req) ? { user_id: 2, display_name: "Bela", app_access: true } : { user_id: 1, display_name: "Ana", app_access: true });
    if (p === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
    if (/^\/open-finance\/\d\/limite$/.test(p)) return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
    if (p === "/api/app/perfil") return resposta(200, { perfil: srv.perfilDoServidor });
    if (/^\/(goals|cards|installments|analytics|insights)\/2\//.test(p)) return resposta(200, (fixture as Record<string, unknown>)[p.replace("/2/", "/1/")]);
    if (p === "/ai/pending") {
      if (srv.segurarDelete) { const g = srv.segurarDelete; if (srv.soUma) srv.segurarDelete = null; await g.promessa; } // soUma: segura só o 1º DELETE seguinte
      srv.armada = false; return srv.statusPendencia === 200 ? resposta(200, { cancelada: srv.cancelada }) : resposta(srv.statusPendencia, { detail: "indisponível" });
    }
    if (p === "/ai/chat/stream") { const r = await srv.chat(++srv.nChat); srv.armada = r.ok; if ("ligar" in r) r.ligar(req.signal!); return r as Response; } // armada: pendência viva no servidor; só o DELETE que o servidor resolve a desarma
    if (/^\/ai\/messages\/\d+\/feedback$/.test(p)) { if (srv.feedback.seguro) await srv.feedback.seguro.promessa; return srv.feedback.status === 200 ? resposta(200, { ok: true, nota: JSON.parse(String(req.body)).nota }) : resposta(srv.feedback.status, { detail: "x" }); }
    if (p === "/ai/messages") { const lido = { used: srv.uso, limit: srv.limite }; if (srv.segurarGet) await srv.segurarGet.promessa; if (srv.statusGet !== 200) return resposta(srv.statusGet, { detail: "x" }); return resposta(200, { messages: srv.historico, usage: lido }); }
    return resposta(200, (fixture as Record<string, unknown>)[p] ?? {});
  });
  const impl = fetchFalso.getMockImplementation()!; // fetch real: abortar rejeita no CLIENTE, mas o servidor segue processando o pedido já enviado
  fetchFalso.mockImplementation((u: string, r: RequestInit) => { const s = impl(u, r), sg = r?.signal; return !sg ? s : new Promise((res, rej) => { const err = () => rej(Object.assign(new Error("Aborted"), { name: "AbortError" })); if (sg.aborted) err(); sg.addEventListener("abort", err); s.then(res, rej); }); });
}
/** Estado de servidor de cada caso + a credencial da Ana. */
export async function reiniciar() {
  Object.assign(srv, { perfilDoServidor: "padrao", uso: 2, limite: 100, historico: [], statusPendencia: 200, cancelada: null, segurarDelete: null, soUma: false, armada: false, segurarGet: null, statusGet: 200, nChat: 0, chat: ok("Resposta do Piggy"), feedback: { status: 200 } });
  await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" }); servidor();
}

export const campo = () => screen.getByLabelText("Mensagem para o Piggy");
export const apertar = async (nome: string | RegExp) => { await act(async () => { fireEvent.press(screen.getByRole("button", { name: nome })); await drenar(); }); };
export const digitar = async (t: string) => { await act(async () => { fireEvent.changeText(campo(), t); await drenar(); }); };
export const enviar = async (t: string) => { await digitar(t); await apertar("Enviar mensagem"); };
export const ir = async (rota: "/piggy" | "/resumo" | "/gastos") => { await act(async () => { router.navigate(rota); await drenar(); }); };
/** Chat pronto = a entrada existe e o `bloqueio` saiu de "abrindo" (sem o "Preparando…") e de "falhou" (sem "Tentar novamente"). O contador de mensagens só aparece perto do limite, então não serve de marco. */
export const pronto = () => waitFor(() => { campo(); expect(screen.queryByLabelText("Preparando a conversa")).toBeNull(); expect(screen.queryByRole("button", { name: "Tentar novamente" })).toBeNull(); });
export const abrir = async () => { renderRouter("./app", { initialUrl: "/piggy" }); await pronto(); };
export const noResumo = async () => { await ir("/resumo"); await waitFor(() => expect(screen.getByRole("button", { name: "Organizar" })).toBeTruthy()); };
export const voltar = async () => { await ir("/piggy"); await pronto(); };
export const trocarParaBela = async () => { await appVai("background"); await guardarCredenciais({ access: "access-bela", refresh: "rt_bela" }); await appVai("active"); };
