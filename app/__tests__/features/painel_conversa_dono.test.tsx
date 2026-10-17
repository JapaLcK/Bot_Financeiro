import { act, fireEvent, renderRouter, screen, waitFor } from "expo-router/testing-library";
import * as estado from "@/features/painel/conversaEstado";
import { useEstadoPainel } from "@/features/painel/provider";
import { guardarCredenciais } from "@/storage/secure";
import { fetchFalso, prepararCaso, resposta, segurar } from "./auth_apoio";
import { appVai, desligarTrava, drenar } from "./open_finance_volta_rota_apoio";
import { abrir, bela as ehBela, delecoes, enviar, fim, fluxoFim, posts, pronto, reiniciar, respostaFluxo, srv, trocarParaBela, type Fluxo } from "./conversa_apoio";
import fixture from "./painel.fixture.json";
// Isolamento entre usuários: o estado da conversa de A nunca pode ser RENDERIZADO com o uid de B (nem por um quadro).
const caminho = (u: unknown) => new URL(String(u)).pathname;
const bela = (req: RequestInit) => (req.headers as Record<string, string>).Authorization === "Bearer access-bela";
beforeEach(async () => {
 prepararCaso(); desligarTrava(); await guardarCredenciais({ access: "access-ana", refresh: "rt_ana" });
});
afterEach(() => { jest.restoreAllMocks(); });
it("POST de A termina no segundo plano e B entra: nenhum render com o uid de B traz pergunta, resposta ou cota de A (o rascunho já sai no fechar)", async () => {
 const g = segurar();
 fetchFalso.mockImplementation(async (url: string, req: RequestInit) => {
  const p = caminho(url);
  if (p === "/auth/me") return resposta(200, { user_id: bela(req) ? 2 : 1, display_name: bela(req) ? "Bela" : "Ana", app_access: true });
  if (p === "/onboarding/open-finance") return resposta(200, { ok: true, completed: true, completed_at: "2026-10-05T12:00:00Z" });
  if (/^\/open-finance\/\d\/limite$/.test(p)) return resposta(200, { ok: true, of_banks_max: 3, em_uso: 0, pode_adicionar: true, code: null, message: null });
  if (p === "/api/app/perfil") return resposta(200, { perfil: "padrao" });
  if (p === "/ai/pending") return resposta(200, { cancelada: null });
  if (p === "/ai/chat/stream") { await g.promessa; return fluxoFim("Resposta da Ana", 90); }
  if (p === "/ai/messages") return resposta(200, { messages: [], usage: { used: bela(req) ? 5 : 2, limit: 100 } });
  return resposta(200, (fixture as Record<string, unknown>)[p.replace("/2/", "/1/")] ?? {});
 });
 const real = estado.useConversa, vistos: { uid: unknown; c: ReturnType<typeof real> }[] = [];
 jest.spyOn(estado, "useConversa").mockImplementation(() => { const c = real(); vistos.push({ uid: useEstadoPainel().usuario?.user_id, c }); return c; });
 renderRouter("./app", { initialUrl: "/piggy" });
 await pronto();
 const campo = () => screen.getByLabelText("Mensagem para o Piggy");
 await act(async () => { fireEvent.changeText(campo(), "Pergunta da Ana"); await drenar(); });
 await act(async () => { fireEvent.press(screen.getByRole("button", { name: "Enviar mensagem" })); await drenar(); });
 await act(async () => { fireEvent.changeText(campo(), "Rascunho da Ana"); await drenar(); });
 await appVai("background"); g.soltar(); await act(drenar); // o POST termina com a tela em segundo plano
 await guardarCredenciais({ access: "access-bela", refresh: "rt_bela" }); await appVai("active");
 await waitFor(() => expect(vistos.some((v) => v.uid === 2)).toBe(true)); await act(drenar);
 const deBela = vistos.filter((v) => v.uid === 2);
 expect(deBela.length).toBeGreaterThan(0);
 for (const { c } of deBela) { expect(c.turnos).toEqual([]); expect(c.rascunho).toBe(""); expect(c.uso?.used ?? 5).toBe(5); }
});
it("stream da Ana aberto, Bela entra: o signal da Ana aborta, o delta e o fim tardios não aparecem em render com o uid da Bela e nenhum DELETE sai com a credencial da Ana", async () => {
 await reiniciar(); let f: Fluxo | undefined; srv.chat = () => (f = respostaFluxo());
 const real = estado.useConversa, vistos: { uid: unknown; c: ReturnType<typeof real> }[] = [];
 jest.spyOn(estado, "useConversa").mockImplementation(() => { const c = real(); vistos.push({ uid: useEstadoPainel().usuario?.user_id, c }); return c; });
 await abrir(); await enviar("Pergunta da Ana"); await waitFor(() => expect(f).toBeDefined());
 await act(async () => { f!.empurrar({ tipo: "delta", texto: "Começo da Ana" }); await drenar(); });
 const post = posts()[0]!, antes = delecoes().length;
 await trocarParaBela(); await waitFor(() => expect(vistos.some((v) => v.uid === 2)).toBe(true));
 expect(post[1].signal.aborted).toBe(true);
 await act(async () => { f!.empurrar({ tipo: "delta", texto: " tardio" }); f!.empurrar(fim("Resposta da Ana", { usage: { used: 90, limit: 100 } })); await drenar(); await drenar(); });
 const deBela = vistos.filter((v) => v.uid === 2); expect(deBela.length).toBeGreaterThan(0);
 for (const { c } of deBela) { expect(c.turnos).toEqual([]); expect(c.uso?.used).not.toBe(90); }
 expect(screen.queryByText(/da Ana|tardio/)).toBeNull(); expect(screen.queryByText("90 de 100 mensagens utilizadas")).toBeNull();
 expect(delecoes().slice(antes).filter(([, r]) => !ehBela(r))).toHaveLength(0);
});
