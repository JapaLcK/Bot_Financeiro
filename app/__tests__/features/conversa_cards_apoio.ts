/**
 * Apoio dos testes dos cards do chat (E1): o bloco REAL de cada card (JSON de `painel.fixture.json`), o provider "vazio" (as rotas dos
 * widgets respondem 500: qualquer widget que ainda leia o provider em vez do bloco mostra "Tentar novamente") e o `/api/app/lancamentos` programável.
 * Não termina em `.test.ts`, então o Jest não o roda como suíte.
 */
import { screen, waitFor } from "expo-router/testing-library";
import { fetchFalso, resposta } from "./auth_apoio";
import { abrir, caminho, enviar, fim, respostaFluxo, srv } from "./conversa_apoio";
import fixture from "./painel.fixture.json";
import periodoJson from "../../../tests/fixtures/ai_chat_bloco_periodo.json";
import v2Json from "../../../tests/frontend/api_v2_respostas.json";

export const F = fixture as Record<string, any>;
export const PERIODO = periodoJson as Record<string, any>;
/** Respostas reais da `/api/v2` (o `metas` do chat vem neste formato). */
export const V2 = v2Json as unknown as { metas: Record<string, any> };
/** card -> recursos que o servidor embute (espelha `tests/_cards_apoio.py::CASOS`). */
export const RECURSOS: Record<string, Record<string, unknown>> = {
  contas: { contas: F["/api/app/contas"] }, hero: { previsao: F["/api/app/previsao"] }, compromissos: { previsao: F["/api/app/previsao"] },
  resumo: { resumo: F["/api/app/resumo-do-mes"], detalhes: F["/api/app/mes-detalhes"] },
  categorias: { detalhes: F["/api/app/mes-detalhes"], contas: F["/api/app/contas"] }, calendario: { detalhes: F["/api/app/mes-detalhes"] }, simulador: { detalhes: F["/api/app/mes-detalhes"], previsao: F["/api/app/previsao"] },
  patrimonio: { patrimonio: F["/api/app/patrimonio"] }, wealth: { patrimonio: F["/api/app/patrimonio"] }, rendimento: { rendimento: F["/api/app/rendimento"] },
  assinaturas: { assinaturas: F["/api/app/assinaturas"] }, metas: { metas: V2.metas.varios }, fatura: { cartoes: F["/cards/1/summary"] },
  parcelas: { parcelas: F["/installments/1/list"] }, renda: { renda: F["/analytics/1/evolution"] }, piggy: { insights: F["/insights/1/current"] },
  caixinhas: { caixinhas: F["/goals/1/status"] }, periodo: { periodo: PERIODO },
};
export const CARDS = Object.keys(RECURSOS);
export type BlocoCru = { card: string; abas: { mes: string | null; recursos: Record<string, unknown> }[] };
export const bloco = (card: string, recursos: Record<string, unknown> = RECURSOS[card]!, mes: string | null = null): BlocoCru => ({ card, abas: [{ mes, recursos }] });

/** O turno seguinte do chat manda estes blocos e fecha com "Resposta do Piggy". */
export const comBlocos = (...blocos: BlocoCru[]) => { srv.chat = () => { srv.uso++; const f = respostaFluxo(); for (const b of blocos) f.empurrar({ tipo: "bloco", bloco: b }); f.empurrar(fim("Resposta do Piggy")); f.fechar(); return f; }; };
export const perguntar = async () => { await abrir(); await enviar("oi"); await waitFor(() => expect(screen.getByText("Resposta do Piggy")).toBeTruthy()); };

export const lancamentos = { resp: (_u: URL, _r: RequestInit): Response | Promise<Response> => resposta(200, F["/api/app/lancamentos"]) };
export const chamadasLanc = () => fetchFalso.mock.calls.filter(([u]) => caminho(u) === "/api/app/lancamentos");
export const consultaLanc = (n = -1) => new URL(String(chamadasLanc().at(n)![0])).searchParams;
const WIDGETS = /^\/api\/app\/(contas|resumo-do-mes|mes-detalhes|patrimonio|rendimento|previsao|assinaturas|categorias|metas|me)$|^\/(goals|cards|installments|analytics|insights)\//;
/** Depois de `reiniciar()`: provider "vazio" (500 nas rotas dos widgets) e `lancamentos.resp` no lugar do `/api/app/lancamentos`. `provedor: true` deixa o provider normal (fixture). */
export function instalarCards(provedor = false) {
  const atual = fetchFalso.getMockImplementation()! as ((u: string, r: RequestInit) => unknown) & { base?: (u: string, r: RequestInit) => unknown }, impl = atual.base ?? atual; // idempotente: chamar de novo troca o modo, não empilha
  const nova = (u: string, r: RequestInit) => {
    const p = caminho(u);
    if (p === "/api/app/lancamentos") { const s = Promise.resolve(lancamentos.resp(new URL(u), r)), sg = r?.signal; return !sg ? s : new Promise((res, rej) => { const err = () => rej(Object.assign(new Error("Aborted"), { name: "AbortError" })); if (sg.aborted) err(); sg.addEventListener("abort", err); s.then(res, rej); }); }
    if (!provedor && WIDGETS.test(p)) return Promise.resolve(resposta(500, { detail: "provider vazio" }));
    return impl(u, r);
  };
  fetchFalso.mockImplementation(Object.assign(nova, { base: impl }) as never);
}
export const pagina = (itens: unknown[], proximo: string | null = null) => resposta(200, { ...F["/api/app/lancamentos"], itens, proximo });
let n = 0;
export const lanc = (extra: Record<string, unknown> = {}) => ({ ...F["/api/app/lancamentos"].itens[1], id: `x${++n}`, ...extra });
