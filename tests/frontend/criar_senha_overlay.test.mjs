/**
 * tests/frontend/criar_senha_overlay.test.mjs — o "Crie sua senha" (PR 4 do funil v3).
 *
 * Conta paga sem senha nem Google/Apple: o `/auth/me` diz `precisa_criar_senha`
 * e as rotas de dados dão 403 `password_required`. O overlay de
 * `frontend/criar-senha.js` cobre a /home e o /app, não fecha, e é a única
 * coisa clicável na tela. Stubs no lugar do servidor; o bloqueio de verdade é
 * do servidor (tests/test_senha_obrigatoria.py).
 *
 * Controles (medidos no PR, uma mutação por vez): sem o `mostrar(me)` da home,
 * vermelhos o do botão (a home carrega /data) e o do ?upgrade=success; sem o
 * `_pbPrecisaSenha` no finally OU no `celebrar()`, vermelho o do ?upgrade=success
 * (as boas-vindas abrem); sem o interceptor, vermelho o do 403 avulso; sem o
 * ramo do `applyAccessVerdict`, vermelhos os dois do /dashboard. Positivos:
 * `precisa_criar_senha:false` carrega os dados sem overlay, e o 403
 * `pro_required` não sobe nada.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { startServer } from "./_server.mjs";
import { chromium } from "playwright";

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "frontend");
const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

let ORIGIN, server, browser;
before(async () => { ({ proc: server, origin: ORIGIN } = await startServer());
                     browser = await chromium.launch(); });
after(async () => { await browser?.close(); server?.kill(); });

const SEM_SENHA = { user_id: 1, email: "fulana@exemplo.com", plan: "pro", plan_expires_at: null,
                    app_access: true, needs_plan_selection: false, precisa_criar_senha: true };
const COM_SENHA = { ...SEM_SENHA, precisa_criar_senha: false };
const DESKTOP = { width: 1280, height: 900 };
const MOBILE = { width: 390, height: 844 };

/** Abre a página com os stubs. `estado.me` pode ser trocado no meio do teste. */
async function abrir(caminho, { me, viewport = DESKTOP, mfa = false } = {}) {
  const ctx = await browser.newContext({ viewport });
  await ctx.addCookies([{ name: "csrf_token", value: "csrf-teste", url: ORIGIN }]);
  const page = await ctx.newPage();
  const estado = { me, meStatus: 200, pedidos: [] };
  await page.route("**/*", (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.origin !== ORIGIN) return route.abort();
    if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
    estado.pedidos.push({ metodo: req.method(), path: url.pathname, csrf: req.headers()["x-csrf-token"] });
    if (url.pathname === "/auth/validate") return route.fulfill(json({ user_id: 1, show_mfa_onboarding: mfa }));
    if (url.pathname === "/auth/me") return route.fulfill(json(estado.meStatus === 200 ? estado.me : { detail: "x" }, estado.meStatus));
    if (url.pathname === "/ai/messages") return route.fulfill(json({ detail: { error: "pro_required", feature: "ai_chat" } }, 403));
    if (url.pathname === "/auth/refresh") return route.fulfill(json({ detail: "invalid_refresh_token" }, 401));
    if (url.pathname.startsWith("/data/")) {
      return route.fulfill(json({ detail: { error: "password_required" } }, estado.me.precisa_criar_senha ? 403 : 200));
    }
    return route.fulfill(json({}));
  });
  await page.route("**/static/auth-refresh.js", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript",
                    body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
  await page.goto(`${ORIGIN}${caminho}`);
  return { page, estado, ctx };
}

const overlayVisivel = (page) => page.waitForSelector("#pb-criar-senha", { state: "visible", timeout: 8000 });

/** O centro de um botão da tela de baixo devolve o overlay no hit-test. */
async function cobreOPainel(page) {
  return page.evaluate(() => {
    const ov = document.getElementById("pb-criar-senha");
    const alvo = [...document.querySelectorAll("button, a")].find((el) => {
      if (ov.contains(el)) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth;
    });
    if (!alvo) return "nenhum botão do painel visível";
    const r = alvo.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return ov.contains(hit) ? true : `hit em ${hit && (hit.id || hit.className || hit.tagName)}`;
  });
}

for (const [nome, viewport] of [["desktop", DESKTOP], ["mobile", MOBILE]]) {
  for (const pagina of ["/home.html", "/dashboard.html"]) {
    test(`${pagina} ${nome}: sem senha, o overlay cobre o painel e não fecha`, async () => {
      const { page, ctx } = await abrir(pagina, { me: SEM_SENHA, viewport });
      await overlayVisivel(page);
      assert.equal(await cobreOPainel(page), true);
      assert.equal(await page.textContent("#pb-cs-titulo"), "Crie sua senha para proteger sua conta");
      assert.equal(await page.textContent(".pb-cs-ok"), "✅ Assinatura confirmada!");
      assert.equal((await page.textContent(".pb-cs-primario")).trim(), "Enviar link para fulana@exemplo.com");
      const caixa = await page.evaluate(() => {
        const r = document.querySelector(".pb-cs-card").getBoundingClientRect();
        return { l: r.left, r: r.right, t: r.top, b: r.bottom, sw: document.documentElement.scrollWidth };
      });
      assert.ok(caixa.l >= 0 && caixa.r <= viewport.width && caixa.t >= 0 && caixa.b <= viewport.height, JSON.stringify(caixa));
      assert.ok(caixa.sw <= viewport.width, `overflow horizontal: ${caixa.sw}`);
      await page.keyboard.press("Escape");
      await page.mouse.click(5, 5);
      assert.ok(await page.isVisible("#pb-criar-senha"), "Esc ou clique fora fechou o overlay");
      for (let i = 0; i < 6; i++) await page.keyboard.press("Tab");
      assert.ok(await page.evaluate(() => document.getElementById("pb-criar-senha").contains(document.activeElement)),
                "o Tab escapou do overlay");
      await ctx.close();
    });
  }
}

test("com senha, nada sobe", async () => {
  const { page, estado, ctx } = await abrir("/home.html", { me: COM_SENHA });
  await page.waitForFunction(() => window._pbAccessDenied === undefined && document.readyState === "complete");
  await page.waitForTimeout(800);
  assert.equal(await page.$("#pb-criar-senha"), null);
  assert.ok(estado.pedidos.some((p) => p.path === "/data/1"), "a home devia carregar os dados");
  await ctx.close();
});

test("o botão manda o link com o CSRF do cookie, e o Reenviar volta", async () => {
  const { page, estado, ctx } = await abrir("/home.html", { me: SEM_SENHA });
  await overlayVisivel(page);
  await page.click(".pb-cs-primario");
  await page.waitForFunction(() => document.querySelector(".pb-cs-status").textContent === "Enviamos. Abra no seu e-mail.");
  const post = estado.pedidos.find((p) => p.path === "/settings/1/password-reset");
  assert.deepEqual([post.metodo, post.csrf], ["POST", "csrf-teste"]);
  assert.equal((await page.textContent(".pb-cs-primario")).trim(), "Reenviar");
  assert.ok(!estado.pedidos.some((p) => p.path === "/data/1"), "a home carregou dado com a senha pendente");
  await ctx.close();
});

test("?upgrade=success: o overlay só sobe depois do checkout fechar, e as boas-vindas não abrem", async () => {
  const { page, ctx } = await (async () => {
    const ctx = await browser.newContext({ viewport: DESKTOP });
    const page = await ctx.newPage();
    await page.addInitScript(() => {
      window.__ordem = [];
      new MutationObserver(() => {
        const cc = document.getElementById("checkout-confirm-overlay");
        if (cc && !cc.classList.contains("open") && window.__ordem.includes("checkout-aberto")
            && !window.__ordem.includes("checkout-fechado")) window.__ordem.push("checkout-fechado");
        if (cc && cc.classList.contains("open") && !window.__ordem.includes("checkout-aberto")) window.__ordem.push("checkout-aberto");
        if (document.getElementById("pb-criar-senha") && !window.__ordem.includes("senha")) window.__ordem.push("senha");
      }).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });
    });
    await page.route("**/*", (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== ORIGIN) return route.abort();
      if (/\.[a-z0-9]+$/i.test(url.pathname)) return route.continue();
      if (url.pathname === "/auth/validate") return route.fulfill(json({ user_id: 1 }));
      if (url.pathname === "/auth/me") return route.fulfill(json(SEM_SENHA));
      return route.fulfill(json({}));
    });
    await page.route("**/static/auth-refresh.js", (route) =>
      route.fulfill({ status: 200, contentType: "application/javascript",
                      body: readFileSync(join(FRONTEND, "static", "auth-refresh.js"), "utf8") }));
    await page.goto(`${ORIGIN}/home.html?upgrade=success&ev=purchase&pl=plus`);
    return { page, ctx };
  })();
  await overlayVisivel(page);
  await page.waitForTimeout(1500);  // o celebrar() das boas-vindas roda 450 ms depois
  const ordem = await page.evaluate(() => window.__ordem);
  assert.deepEqual(ordem, ["checkout-aberto", "checkout-fechado", "senha"]);
  assert.ok(!(await page.evaluate(() => document.getElementById("welcome-pro-overlay")?.classList.contains("open"))),
            "as boas-vindas abriram por cima/baixo do Crie sua senha");
  assert.match(await page.evaluate(() => location.pathname), /home/);
  await ctx.close();
});

test("convite do MFA que chegar aberto fica inacessível e não é marcado como visto", async () => {
  const { page, estado, ctx } = await abrir("/home.html", { me: SEM_SENHA, mfa: true });
  await overlayVisivel(page);
  await page.keyboard.press("Escape");
  await page.mouse.click(640, 450);
  await page.waitForTimeout(300);
  assert.ok(!estado.pedidos.some((p) => p.path === "/auth/mfa/onboarding-seen"), "o convite do MFA foi queimado");
  assert.equal(await page.evaluate(() => {
    const hit = document.elementFromPoint(640, 450);
    return !!hit.closest("#pb-criar-senha") || hit.id === "pb-criar-senha";
  }), true);
  await ctx.close();
});

test("403 password_required avulso sobe o overlay; 403 pro_required não", async () => {
  const { page, estado, ctx } = await abrir("/home.html", { me: COM_SENHA });
  await page.waitForTimeout(800);
  await page.evaluate(() => fetch("/ai/messages").then((r) => r.status));
  assert.equal(await page.$("#pb-criar-senha"), null);
  estado.me = SEM_SENHA;  // a senha "sumiu" noutra aba: o 403 da rota avisa
  await page.evaluate(() => fetch("/data/1").then((r) => r.json()));
  await overlayVisivel(page);
  await ctx.close();
});

test("aba volta ao foco: 401 vai para o login, senha criada recarrega sem overlay", async () => {
  const { page, estado, ctx } = await abrir("/home.html", { me: SEM_SENHA });
  await overlayVisivel(page);
  estado.me = COM_SENHA;
  await Promise.all([page.waitForEvent("load"), page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")))]);
  await page.waitForTimeout(600);
  assert.equal(await page.$("#pb-criar-senha"), null, "recarregou e o overlay continuou");

  estado.me = SEM_SENHA;
  await page.reload();
  await overlayVisivel(page);
  estado.meStatus = 401;
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await page.waitForURL(/\/login/, { timeout: 8000 });
  await ctx.close();
});
