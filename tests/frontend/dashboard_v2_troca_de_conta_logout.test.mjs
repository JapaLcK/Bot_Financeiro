/**
 * /painel, o logout com o marcador `pb_conta` (webapp/src/dashboard/lib/sessao.ts): a resposta do POST /auth/logout apaga o marcador,
 * e o reload da troca de conta não pode cancelar a saída para /?logout=1 (L1, L1b, L2); até o marcador mudar, o painel segue lendo e
 * respondendo a gestos (L3); se a limpeza do aparelho travar depois da resposta, um prazo leva a /?logout=1 (L4), e nunca antes dela
 * (L4b). L1–L2 e L4 falham se a proteção sair (a mutação está no nome); L3 falha se a trava passar a lançar desde o clique; L4b, se o
 * prazo armar no clique. O `abrir` (conta A, uid 7): _troca_de_conta.mjs; a troca de conta em si: dashboard_v2_troca_de_conta.test.mjs.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { setTimeout as dorme } from "node:timers/promises";
import { chromium } from "playwright";
import { exigeArtefatoEmDia } from "./_painel.mjs";
import { CONTAS, abrir as abrirConta } from "./_troca_de_conta.mjs";

let browser;
before(async () => { exigeArtefatoEmDia(); browser = await chromium.launch(); });
after(() => browser?.close());

const abrir = (opts) => abrirConta(browser, opts);

// O logout apaga o marcador (`_clear_session_cookies`): o reload da troca não pode cancelar a navegação de saída. O `/` demora 2,5 s.
async function comSaidaLenta() {
  const p = await abrir();
  await p.ctx.route((u) => u.pathname === "/", async (r) => { await dorme(2500); await r.fulfill({ contentType: "text/html", body: "<html><body>saiu</body></html>" }).catch(() => {}); });
  await p.ir();
  await p.contaA();
  return p;
}
const ondeTermina = (page) => page.waitForURL("**/?logout=1", { timeout: 6000 }).then(() => page.url(), () => page.url());

test("L1 (sem a trava no `sair` nem no `sairPara`): o 'Sair' daqui termina em /?logout=1, e não no /painel recarregado", async () => {
  const { ctx, page } = await comSaidaLenta();
  await ctx.route("**/auth/logout", (r) => r.fulfill({ headers: { "set-cookie": "pb_conta=; Max-Age=0; Path=/" }, json: {} }));
  await page.locator('button[title="Sair"]').click();
  const url = await ondeTermina(page);
  await ctx.close();
  assert.ok(url.endsWith("/?logout=1"), url);
});

test("L1b (trava só no `fim` do `sair`): a limpeza do aparelho (auth-refresh.js) demora 1,2 s depois da resposta do logout; o 'Sair' termina em /?logout=1", async () => {
  const { ctx, page, ir, contaA } = await abrir();
  await ctx.addInitScript(() => {
    const sw = navigator.serviceWorker;
    if (sw) { const real = sw.getRegistrations.bind(sw); sw.getRegistrations = () => new Promise((ok) => setTimeout(() => ok(real()), 1200)); }
  });
  await ctx.route((u) => u.pathname === "/", (r) => r.fulfill({ contentType: "text/html", body: "<html><body>saiu</body></html>" }));
  await ir();
  await contaA();
  await ctx.route("**/auth/logout", (r) => r.fulfill({ headers: { "set-cookie": "pb_conta=; Max-Age=0; Path=/" }, json: {} }));
  await page.locator('button[title="Sair"]').click();
  await dorme(3700); // sem a trava no começo, o intervalo (1 s) recarrega o /painel antes de a limpeza acabar
  const url = page.url();
  await ctx.close();
  assert.ok(url.endsWith("/?logout=1"), url);
});

test("L2 (sem a trava no `sairPara`): o 'Sair' de outra aba leva esta a /?logout=1, mesmo com o marcador sumindo no caminho", async () => {
  const { ctx, page } = await comSaidaLenta();
  await ctx.route("**/outra-aba", (r) => r.fulfill({ contentType: "text/html", body: "<html></html>" }));
  const outra = await ctx.newPage();
  await outra.goto(new URL("/outra-aba", page.url()).href);
  const saiu = page.waitForRequest((q) => q.isNavigationRequest() && new URL(q.url()).search === "?logout=1");
  await outra.evaluate(() => localStorage.setItem("finbot_logout_at", String(Date.now()))); // o `sair` da outra aba
  await saiu;
  await outra.evaluate(() => { document.cookie = "pb_conta=; max-age=0; path=/"; }); // a resposta do logout dela chega
  const url = await ondeTermina(page);
  await ctx.close();
  assert.ok(url.endsWith("/?logout=1"), url);
});
test("L3 (a trava lança desde o clique): o POST do logout fica preso; o clique no mês e o aviso do SSE leem a /api/v2, sem a tela de erro, e a resposta leva a /?logout=1", async () => {
  const { ctx, page, ir, s, contaA, pedidos } = await abrir();
  let avisar; s.eventos = new Promise((ok) => { avisar = ok; });
  let responder; const resposta = new Promise((ok) => { responder = ok; });
  await ctx.route((u) => u.pathname === "/", (r) => r.fulfill({ contentType: "text/html", body: "<html><body>saiu</body></html>" }));
  await ctx.route("**/auth/logout", async (r) => { await resposta; await r.fulfill({ headers: { "set-cookie": "pb_conta=; Max-Age=0; Path=/" }, json: {} }).catch(() => {}); });
  await ir();
  await contaA();
  await page.locator('button[title="Sair"]').click();
  await page.getByRole("button", { name: "Mês anterior", exact: true }).click({ timeout: 3000 }).catch(() => {});
  const gesto = await page.getByText("7.333,33").waitFor({ timeout: 3000 }).then(() => true, () => false);
  const desde = pedidos.length;
  avisar(); // "tudo" mudou: a releitura sem gesto, com o POST ainda no ar
  await dorme(1500);
  const r = {
    gesto,
    leu: pedidos.slice(desde).some(([p]) => p.startsWith("/api/v2/") && p !== "/api/v2/eventos"),
    erro: await page.locator("main.entrada").count(),
    contas: await page.locator(CONTAS).getByText("7.777,77").count(),
    vis: await page.evaluate(() => document.documentElement.style.visibility),
  };
  responder();
  const url = await ondeTermina(page);
  await ctx.close();
  assert.deepEqual(r, { gesto: true, leu: true, erro: 0, contas: 1, vis: "" });
  assert.ok(url.endsWith("/?logout=1"), url);
});

// A limpeza do aparelho (auth-refresh.js) que nunca termina: `getRegistrations` não resolve: o cookie da resposta do
// logout chega, mas o `fetch` do `sair` nunca devolve, e o `sairPara` do `fim` nunca roda.
const limpezaPresa = () => { const sw = navigator.serviceWorker; if (sw) sw.getRegistrations = () => new Promise(() => {}); };
const visivel = (page) => page.evaluate(() => document.documentElement.style.visibility);

test("L4 (sem o prazo do `trocou`): a resposta do logout chega e a limpeza trava para sempre; a página não fica escondida no /painel, termina em /?logout=1", async () => {
  const { ctx, page, ir, contaA } = await abrir();
  await ctx.addInitScript(limpezaPresa);
  await ctx.route((u) => u.pathname === "/", (r) => r.fulfill({ contentType: "text/html", body: "<html><body>saiu</body></html>" }));
  await ir();
  await contaA();
  await ctx.route("**/auth/logout", (r) => r.fulfill({ headers: { "set-cookie": "pb_conta=; Max-Age=0; Path=/" }, json: {} }));
  await page.locator('button[title="Sair"]').click();
  const url = await page.waitForURL("**/?logout=1", { timeout: 8000 }).then(() => page.url(), () => page.url()); // 1 s do intervalo + 3 s
  const vis = await visivel(page);
  await ctx.close();
  assert.deepEqual({ saiu: url.endsWith("/?logout=1"), vis }, { saiu: true, vis: "" }, url);
});

test("L4b (o prazo arma no clique): o POST do logout fica sem resposta; o painel segue no /painel, visível, sem navegar", async () => {
  const { ctx, page, ir, contaA, pedidos } = await abrir();
  await ctx.addInitScript(limpezaPresa);
  await ctx.route((u) => u.pathname === "/", (r) => r.fulfill({ contentType: "text/html", body: "<html><body>saiu</body></html>" }));
  await ir();
  await contaA();
  await ctx.route("**/auth/logout", () => {}); // sem resposta: o marcador não muda
  await page.locator('button[title="Sair"]').click();
  await dorme(5000); // mais que o prazo (3 s) e o intervalo (1 s)
  const r = { painel: new URL(page.url()).pathname, vis: await visivel(page), navegou: pedidos.some(([p, nav]) => nav && p === "/") };
  await ctx.close();
  assert.deepEqual(r, { painel: "/painel.html", vis: "", navegou: false });
});
