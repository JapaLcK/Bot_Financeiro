/** "Sair" das páginas públicas (nav-auth.js): limpa o aparelho antes de recarregar, dirigido no navegador (#852). */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer()); browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const json = (body) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

// Sai pelo menu da conta e devolve a ordem dos eventos e o aparelho depois do reload.
// O delete do Cache Storage é ATRASADO de propósito: um reload que não espera a limpeza
// derruba o documento antes de ela terminar, e o "apagou" nunca chega.
async function sair({ logout = (r) => r.fulfill(json({})), bloqueiaStorage = false } = {}) {
  const ctx = await browser.newContext();
  await ctx.route("**/auth/validate", (r) => r.fulfill(json({ ok: true })));
  await ctx.route("**/auth/dashboard-profile", (r) => r.fulfill(json({ email: "a@b.c", plan: "plus" })));
  await ctx.route("**/auth/logout", logout);
  const eventos = [];
  await ctx.exposeBinding("__apagou", (_s, k) => eventos.push(`apagou ${k}`));
  await ctx.addInitScript(() => {
    const orig = CacheStorage.prototype.delete;
    CacheStorage.prototype.delete = function (k) {
      return new Promise((r) => setTimeout(r, 300)).then(() => orig.call(this, k))
        .then((v) => { window.__apagou(k); return v; });
    };
  });
  const page = await ctx.newPage();
  await page.goto(`${ORIGIN}/contato.html`);
  await page.locator("#pb-acct-btn").click();
  await page.evaluate(async (bloqueia) => {
    await (await caches.open("pigbank-v9")).put("/privado", new Response("conta"));
    localStorage.setItem("pb_snap_1", "conta");
    localStorage.setItem("pigbank_theme", "dark");
    if (bloqueia) {
      for (const nome of ["localStorage", "sessionStorage"]) {
        Object.defineProperty(window, nome, { get() { throw new DOMException("bloqueado", "SecurityError"); } });
      }
    }
  }, bloqueiaStorage);
  page.on("framenavigated", (f) => { if (f === page.mainFrame()) eventos.push("reload"); });
  await Promise.all([
    page.waitForEvent("framenavigated", { timeout: 5000 }).catch(() => {}),
    page.getByRole("button", { name: "Sair" }).click(),
  ]);
  await page.waitForLoadState("load");
  const aparelho = await page.evaluate(async () => ({
    caches: await caches.keys(), conta: localStorage.getItem("pb_snap_1"), tema: localStorage.getItem("pigbank_theme"),
  }));
  await ctx.close();
  return { eventos, aparelho };
}

test("Sair: apaga o Cache Storage e o que é da conta ANTES de recarregar; o tema fica", async () => {
  const r = await sair();
  assert.deepEqual(r, { eventos: ["apagou pigbank-v9", "reload"], aparelho: { caches: [], conta: null, tema: "dark" } });
});

test("Sair offline: a limpeza e o reload acontecem mesmo sem resposta do servidor", async () => {
  const r = await sair({ logout: (rt) => rt.abort("internetdisconnected") });
  assert.deepEqual(r.eventos, ["apagou pigbank-v9", "reload"]);
  assert.deepEqual(r.aparelho.caches, []);
});

test("Sair com storage bloqueado: o cache sai e a página ainda recarrega", async () => {
  const r = await sair({ bloqueiaStorage: true });
  assert.deepEqual(r.eventos, ["apagou pigbank-v9", "reload"]);
  assert.deepEqual(r.aparelho.caches, []);
});
