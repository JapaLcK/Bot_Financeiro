// O painel da conta A (uid 7) com o marcador `pb_conta` (webapp/src/dashboard/lib/sessao.ts), comum a
// dashboard_v2_troca_de_conta.test.mjs e dashboard_v2_troca_de_conta_logout.test.mjs. O que cada campo mede: o cabeçalho do primeiro.
import { performance } from "node:perf_hooks";
import { setTimeout as dorme } from "node:timers/promises";
import { RESPOSTAS, abrirPainel } from "./_painel.mjs";

const A_TXT = ["Segredo da conta A", "7.777,77", "7.111,11", "7.333,33"];
export const CONTAS = '[data-widget-id="contas"]';
export const PIGGY = '[data-widget-id="piggy"]';

/** O painel da conta A. `s.uid` decide o que as rotas respondem (8 = a B; `null` = deslogado: 401 no perfil e na /api/v2). */
export async function abrir(browser, opts = {}) {
  const p = await abrirPainel(browser, { perfil: "padrao", marca: "7", ...opts });
  const s = { uid: 7, insights: null, eventos: null };
  const { ctx, page } = p;
  const pedidos = []; // [pathname, é a navegação do documento]
  let cargas = 0;
  page.on("load", () => { cargas += 1; });
  page.on("request", (q) => pedidos.push([new URL(q.url()).pathname, q.isNavigationRequest()]));
  const perfis = []; // status de cada resposta do perfil
  page.on("response", (r) => { if (new URL(r.url()).pathname === "/auth/dashboard-profile") perfis.push(r.status()); });
  await ctx.addInitScript((A) => {
    const ve = (n) => A.some((a) => (n.textContent || "").includes(a));
    new MutationObserver((ms) => {
      if (!sessionStorage.getItem("trocou")) return;
      for (const m of ms) if ((m.type === "characterData" && ve(m.target)) || [...m.addedNodes].some(ve)) sessionStorage.setItem("viuA", "1");
    }).observe(document, { childList: true, subtree: true, characterData: true });
  }, A_TXT);
  await ctx.route("**/auth/dashboard-profile", (r) => (s.uid === null
    ? r.fulfill({ status: 401, headers: { "WWW-Authenticate": "Bearer" }, json: { detail: "x" } })
    : r.fulfill({ json: { user_id: s.uid, display_name: null, email: null, whatsapp_linked: true } })));
  await ctx.route("**/api/v2/contas", (r) => r.fulfill({ json: { ...RESPOSTAS.contas.todos_os_estados, total: s.uid === 7 ? "7777.77" : "8888.88" } }));
  await ctx.route((u) => u.pathname === "/api/v2/resumo-do-mes", (r) => {
    const mes = new URL(r.request().url()).searchParams.get("mes") ?? "2026-10";
    const ant = s.uid === 7 ? (mes === "2026-10" ? "7111.11" : "7333.33") : "8111.11";
    return r.fulfill({ json: { ...RESPOSTAS.resumo_do_mes.exato, mes, ate: `${mes}-28`, anterior: { mes: "2026-08", entrou: ant, saiu: "1.00" } } });
  });
  await ctx.route("**/api/v2/insights", async (r) => {
    const uid = s.uid;
    if (s.insights) await s.insights(r);
    await r.fulfill({ json: { insights: [{ title: uid === 7 ? "Segredo da conta A" : "Aviso da conta B", message: "x" }] } }).catch(() => {});
  });
  // O SSE: pendente para sempre (como o harness), ou, com `s.eventos` (uma promise), um aviso quando ela resolver: a releitura sem gesto.
  await ctx.route("**/api/v2/eventos", async (r) => {
    if (!s.eventos) return;
    await s.eventos;
    await r.fulfill({ headers: { "content-type": "text/event-stream" }, body: 'data: {"recurso":"tudo"}\n\n' }).catch(() => {});
  });
  // Por último (vence): deslogado, toda a /api/v2 dá 401; senão segue para as rotas acima.
  await ctx.route("**/api/v2/**", (r) => (s.uid === null ? r.fulfill({ status: 401, headers: { "WWW-Authenticate": "Bearer" }, json: { detail: "x" } }) : r.fallback()));
  const contaA = async () => {
    await page.locator(CONTAS).getByText("7.777,77").waitFor({ timeout: 20000 });
    await page.locator(PIGGY).getByText("Segredo da conta A").waitFor({ timeout: 20000 });
  };
  const viuA = () => page.evaluate(() => sessionStorage.getItem("viuA"));
  /** Espera `n` cargas; devolve quanto levou (ms) ou `null` se não veio em `ms`. */
  const recarga = async (n, ms) => {
    const t0 = performance.now();
    while (cargas < n) { if (performance.now() - t0 > ms) return null; await dorme(20); }
    return performance.now() - t0;
  };
  return { ...p, s, pedidos, perfis, cargas: () => cargas, contaA, viuA, recarga };
}
